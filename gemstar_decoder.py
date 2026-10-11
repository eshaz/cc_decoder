#!/usr/bin/env python3
# coding=utf-8
"""
gemstar_decoder.py: decode Gemstar EPG data (VCR Plus+ / Guide Plus+ / TV Guide On Screen) from
raw NTSC VBI captures.

Usage
-----
    gemstar_decoder.py capture.y16 -o out/                       # framing, packets, 1X clock only
    gemstar_decoder.py capture.y16 -o out/ --freqccd /path/to/FREQCCD_.EXE
                                                                 # + decryption, listings, ads, JSON
    gemstar_decoder.py capture.flac -o out/ --nabts 12,13        # + NABTS packets on NTSC lines 12, 13

Inputs: ld-decode 4fsc VBI exports (.tbc / .vbi / .y16, 910 samples x 16 lines per field, or the same
packed as .flac) and 8-bit 1440-sample capture-card VBI dumps (12 lines per field).

Decryption of Type B messages, descrambling of `|B` packets and decompression of listings text run
Gemstar's own routines from FREQCCD_.EXE under CPU emulation (`pip install unicorn`). That program and
its data files are not part of this repository; see docs/gemstar.md.

Public domain / Unlicense, as the rest of this repository.
"""

import argparse
import datetime
import json
import os
import sys

import numpy as np

from lib.gemstar import nabts as nabts_mod
from lib.gemstar.capture import read_fields, detect_format
from lib.gemstar.freqccd import Freqccd, FreqccdMissing
from lib.gemstar.guide import sip_listings, listings_text, build_guide
from lib.gemstar.slicer import slice_2x, slice_1x, frame_kind, scan_lines
from lib.gemstar.streams import (demux, find_key_date, assemble_messages, messages_text, descramble_packets,
                                 a11_lineups, decode_1x)

__license__ = 'Unlicense (public domain)'


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def slice_capture(path, fmt, line_idx, max_fields, ffmpeg):
    """2X and 1X bits for the chosen stored-line indexes over the whole capture."""
    b2 = {li: [] for li in line_idx}
    b1 = {li: [] for li in line_idx}
    geo, n = None, 0
    for geo, a in read_fields(path, fmt, max_fields=max_fields, ffmpeg=ffmpeg):
        for li in line_idx:
            b2[li].append(slice_2x(a[:, li, :], geo))
            b1[li].append(slice_1x(a[:, li, :], geo))
        n += len(a)
    return geo, {li: np.concatenate(v) for li, v in b2.items()}, {li: np.concatenate(v) for li, v in b1.items()}


def parse_date(s):
    return datetime.date.fromisoformat(s) if s else None


def decode_line(li, geo, bits2, bits1, fq, args, outdir):
    vbi_line = geo.first_line + li
    d = os.path.join(outdir, f'line{vbi_line}')
    os.makedirs(d, exist_ok=True)
    kind = frame_kind(bits2)
    dm = demux(bits2)
    # Reed-Solomon RS(76,68) over each Type B block (FREQCCD Syndrome4/ErrCorrect): fix up to 4 bad bytes
    from lib.gemstar.rs import correct
    fixed, bad, out = 0, 0, []
    for ph, fl, x in dm['blocks']:
        r, n = correct(x)
        if r is None:
            bad += 1; out.append((ph, fl, x))
        else:
            fixed += n > 0; out.append((ph, fl, r))
    dm['blocks'] = out
    summary = dict(vbi_line=vbi_line, stored_index=li, **dm['stats'])
    summary.update(rs_blocks_corrected=fixed, rs_blocks_uncorrectable=bad)

    # 1X clock packets (same line, fields not carrying 2X)
    pk1, clock = decode_1x(bits1, kind)
    summary['packets_1x'] = len(pk1)
    summary['clock'] = clock[:1] + clock[-1:] if clock else []
    if clock:
        with open(os.path.join(d, 'clock_1x.tsv'), 'w') as f:
            f.write('field\tutc\tweekday\tutc_offset\tdst\n')
            for c in clock:
                f.write(f"{c['field']}\t{c['utc']}\t{c['weekday']}\t{c['utc_offset']}\t{c['dst']}\n")

    # raw parity packets
    with open(os.path.join(d, 'packets_raw.tsv'), 'w') as f:
        f.write('field\tphase\txor_ok\tparity_ok\tbytes\n')
        for p in dm['packets']:
            f.write(f"{p['field']}\t{p['phase']}\t{int(p['xor_ok'])}\t{int(p['parity_ok'])}\t{p['body'].hex()}\n")

    if not dm['blocks'] and not dm['packets']:
        return summary
    if fq is None:
        summary['note'] = 'no --freqccd given: Type B blocks and |B packets left encrypted'
        with open(os.path.join(d, 'typeb_blocks_raw.tsv'), 'w') as f:
            f.write('field\tphase\tblock\n')
            for ph, fl, x in dm['blocks']:
                f.write(f'{fl}\t{ph}\t{x.hex()}\n')
        return summary

    # recording date: --date, else the 1X clock
    rec = parse_date(args.date)
    if rec is None and clock:
        c = clock[len(clock) // 2]
        t = datetime.datetime.strptime(c['utc'], '%Y-%m-%d %H:%MZ') + datetime.timedelta(hours=c['utc_offset'] + c['dst'])
        rec = t.date()                      # local date at the station
        summary['date_from_clock'] = str(rec)

    # ---- Type B messages
    messages = []
    ky = km = None
    if dm['blocks']:
        if args.key:
            ky, km = map(int, args.key.split('-'))
            hits = None
        else:
            years = range(args.year - 1, args.year + 2) if args.year else range(1985, 2040)
            ky, km, hits, tried = find_key_date(dm['blocks'], fq, years)
            summary['key_search'] = dict(hits=hits, block0s_tried=tried)
        if ky:
            messages, good, bad = assemble_messages(dm['blocks'], fq, ky, km)
            summary.update(key=f'{ky}-{km:02d}', messages_verified=good, messages_failed=bad,
                           unique_messages=len(messages))
            with open(os.path.join(d, 'typeb_messages.txt'), 'w') as f:
                f.write(messages_text(messages, f'Type B messages, key {ky}-{km:02d}'))
        else:
            summary['key'] = None
            summary['note'] = 'no key date made the block-0 checksums pass'

    # ---- |B packets
    a11 = {}
    if dm['packets']:
        try:
            table, scores, desc = descramble_packets(dm['packets'], fq, args.b_table)
        except (FreqccdMissing, ValueError) as e:
            table, scores, desc = None, {}, []
            log(f'  |B: {e}')
        summary['b_table'] = table
        summary['b_table_scores'] = dict(list(scores.items())[:3])
        if table:
            with open(os.path.join(d, 'b_packets.tsv'), 'w') as f:
                f.write('field\tok\theader\tcontent\n')
                for p in desc:
                    f.write(f"{p['field']}\t{int(p['ok'])}\t{p['header']}\t{p['content']}\n")
            a11 = a11_lineups(desc)
        elif fq.tables():
            summary['b_note'] = 'no supplied .INP table matches these |B packets'

    # ---- listings, graphics, JSON
    listings = []
    if any(m['type'] == 0 for m in messages):
        ref = rec or datetime.date(ky, km, 1)
        try:
            listings = sip_listings(messages, fq, ref.year, ref.month, ref.day)
            with open(os.path.join(d, 'listings.txt'), 'w') as f:
                f.write(listings_text(listings))
        except FreqccdMissing as e:
            log(f'  listings: {e}')
            summary['listings_note'] = str(e)
    summary['programs'] = len(listings)

    if any(m['type'] == 0x1E for m in messages):
        try:
            from lib.gemstar.graphics import export
            idx = export(messages, os.path.join(d, 'graphics'))
            summary['graphics_objects'] = len(idx)
        except ImportError:
            summary['graphics_note'] = 'install pillow to render graphics objects'

    if messages or a11:
        year = rec.year if rec else ky
        g = build_guide(messages, listings, a11, year, os.path.basename(args.input),
                        extra=dict(vbi_line=vbi_line, key=summary.get('key'), b_table=summary.get('b_table'),
                                   clock_1x=summary['clock']))
        with open(os.path.join(d, 'guide.json'), 'w') as f:
            json.dump(g, f, indent=1, ensure_ascii=False)
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description='Decode Gemstar EPG data from raw NTSC VBI captures.')
    ap.add_argument('input', help='VBI capture (.tbc/.vbi/.y16, .flac, or 1440-sample card dump)')
    ap.add_argument('-o', '--outdir', default='gemstar_out')
    ap.add_argument('--format', choices=['tbc', 'flac', 'cap1440'], help='capture layout (default: detect)')
    ap.add_argument('--lines', help='NTSC line numbers to decode, e.g. 16 or 15,16 (default: auto-detect)')
    ap.add_argument('--max-fields', type=int, help='stop after this many fields')
    ap.add_argument('--freqccd', help="path to Gemstar's FREQCCD_.EXE (enables decryption)")
    ap.add_argument('--inp-dir', help='folder with the monthly MMYY.INP tables (default: next to FREQCCD_.EXE)')
    ap.add_argument('--ddpacket', help='DDPACKET.BIN text dictionary (default: next to FREQCCD_.EXE)')
    ap.add_argument('--year', type=int, help='approximate recording year (narrows the Type B key search)')
    ap.add_argument('--key', help='Type B key date YYYY-MM (skips the key search)')
    ap.add_argument('--b-table', help='use this MMYY.INP table for |B packets, e.g. 1201 (skips the table search)')
    ap.add_argument('--date', help='recording date YYYY-MM-DD for listings (default: from the 1X clock)')
    ap.add_argument('--nabts', help='also slice NABTS on these NTSC line numbers (4fsc captures only)')
    ap.add_argument('--ffmpeg', default='ffmpeg')
    args = ap.parse_args(argv)

    fmt = args.format or detect_format(args.input)
    os.makedirs(args.outdir, exist_ok=True)
    log(f'{args.input}: format {fmt}')

    if args.lines:
        geo = next(read_fields(args.input, fmt, max_fields=1, ffmpeg=args.ffmpeg))[0]
        line_idx = [int(x) - geo.first_line for x in args.lines.split(',')]
    else:
        geo, counts = scan_lines(args.input, fmt, max_fields=min(args.max_fields or 4000, 4000), ffmpeg=args.ffmpeg)
        line_idx = [int(i) for i in np.nonzero(counts > max(20, counts.max() * 0.05))[0]] if counts.max() else []
        log('2X data frames per line: ' + ', '.join(f'{geo.first_line + i}:{c}' for i, c in enumerate(counts) if c))
    if not line_idx:
        log('no Gemstar 2X lines found')
    fq = None
    if args.freqccd:
        try:
            fq = Freqccd(args.freqccd, args.inp_dir, args.ddpacket)
            fq.decrypt(bytes(64), 0, 0, 2000, 1)            # fail early if unicorn is missing
        except FreqccdMissing as e:
            log(f'decryption disabled: {e}')
            fq = None

    summary = dict(input=os.path.abspath(args.input), format=fmt, lines=[])
    if line_idx:
        log(f'slicing lines {[geo.first_line + i for i in line_idx]}')
        geo, b2, b1 = slice_capture(args.input, fmt, line_idx, args.max_fields, args.ffmpeg)
        summary['fields'] = int(len(next(iter(b2.values()))))
        for li in line_idx:
            log(f'line {geo.first_line + li}: decoding')
            s = decode_line(li, geo, b2[li], b1[li], fq, args, args.outdir)
            log('  ' + ', '.join(f'{k}={v}' for k, v in s.items() if k in (
                'data_frames', 'blocks', 'packets', 'key', 'unique_messages', 'b_table', 'programs',
                'graphics_objects', 'note')))
            summary['lines'].append(s)

    if args.nabts:
        nl = [int(x) - geo.first_line for x in args.nabts.split(',')]
        pk = nabts_mod.scan(args.input, nl, fmt, max_fields=args.max_fields or 4000, ffmpeg=args.ffmpeg)
        with open(os.path.join(args.outdir, 'nabts_packets.tsv'), 'w') as f:
            f.write('field\tvbi_line\taddress\tci\tps\tdata_hex\n')
            for p in pk:
                f.write('\t'.join(str(p[k]) for k in ('field', 'vbi_line', 'address', 'ci', 'ps', 'data_hex')) + '\n')
        summary['nabts_packets'] = len(pk)
        log(f'NABTS: {len(pk)} packets')

    with open(os.path.join(args.outdir, 'summary.json'), 'w') as f:
        json.dump(summary, f, indent=1, default=str)
    log(f'-> {args.outdir}')


if __name__ == '__main__':
    main()
