"""Tests for lib.gemstar.

Everything here is built from synthetic signals and synthetic messages laid out to the formats in
docs/gemstar.md, so the tests need neither Gemstar's programs nor any broadcast recordings. The
routines that run inside FREQCCD_.EXE are replaced by small stand-ins with the same interface.

One end-to-end test runs only when you point it at your own files:
    GEMSTAR_FREQCCD=/path/FREQCCD_.EXE GEMSTAR_CAPTURE=/path/capture.vbi python3 -m pytest tests/test_gemstar.py

    python3 -m pytest tests/test_gemstar.py
"""

import importlib.util
import os
import struct
import sys
import tempfile
import zlib
from unittest import TestCase, skipUnless

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

from lib.gemstar.capture import TBC, CAP1440, detect_format, read_fields
from lib.gemstar.slicer import FRAME_2X, IDLE_2X, slice_2x, slice_1x, frame_kind, frame_bytes
from lib.gemstar.streams import (SYNC, demux, find_key_date, assemble_messages, decode_1x, a11_lineups,
                                 messages_text)
from lib.gemstar.guide import (parse_lineup, lineup_key, parse_genres, split_info, sip_listings, build_guide, listings_text,
                               object_header)
from lib.gemstar import graphics, nabts

HAVE_PIL = importlib.util.find_spec('PIL') is not None

__license__ = 'Unlicense (public domain)'


# ---- helpers: synthetic signals ----------------------------------------------------------------

def odd(b):
    """7-bit value with odd parity in bit 7."""
    b &= 0x7F
    return b | (0x80 if bin(b).count('1') % 2 == 0 else 0)


def frame_bits(four_bytes):
    """53 sliced bits of one 2X data frame."""
    bits = [int(c) for c in FRAME_2X]
    for v in four_bytes:
        bits += [(v >> i) & 1 for i in range(8)]
    return bits + [0, 0]


def idle_bits():
    return [int(c) for c in IDLE_2X] + [0] * 34


def wave_2x(bits, geo, start, lo=0.0, hi=100.0):
    """Waveform of one stored line carrying `bits` at the 2X rate (64 bits per line period)
    from sample `start`, softened by a 3-tap low-pass."""
    step = geo.line_period / 64.0
    y = np.full(geo.samples, lo)
    for k, b in enumerate(bits):
        a, e = int(round(start + k * step)), int(round(start + (k + 1) * step))
        y[a:e] = hi if b else lo
    return np.convolve(y, [0.25, 0.5, 0.25], 'same')


def wave_1x(byte1, byte2, geo, peak):
    """CEA-608-rate line: 7 run-in cycles (first peak at `peak`), start bits 001, two bytes LSB first."""
    p = geo.line_period / 32.0
    n = np.arange(geo.samples, dtype=float)
    y = np.zeros(geo.samples)
    run = (n >= peak - p / 2) & (n < peak + 6.5 * p)
    y[run] = 50 + 50 * np.cos(2 * np.pi * (n[run] - peak) / p)
    data = [0, 0, 1] + [(byte1 >> i) & 1 for i in range(8)] + [(byte2 >> i) & 1 for i in range(8)]
    for k, b in enumerate(data):
        c = peak + (7 + k) * p
        a, e = int(round(c - p / 2)), int(round(c + p / 2))
        y[a:e] = 100.0 if b else 0.0
    return y


def bits_array(rows):
    return np.array(rows, dtype=np.uint8)


# ---- capture readers -----------------------------------------------------------------------------

class TestCapture(TestCase):
    def test_tbc_reader_and_detection(self):
        a = (np.arange(TBC.samples * TBC.lines * 3) % 4000 + 32768).astype('<u2')
        with tempfile.NamedTemporaryFile(suffix='.y16', delete=False) as f:
            f.write(a.tobytes())
        try:
            self.assertEqual(detect_format(f.name), 'tbc')
            chunks = list(read_fields(f.name, chunk=2))
            self.assertEqual([c.shape for _, c in chunks], [(2, 16, 910), (1, 16, 910)])
            self.assertEqual(chunks[0][1][0, 0, 5], 5.0)          # unsigned 16-bit, offset removed
        finally:
            os.unlink(f.name)

    def test_cap1440_reader_and_detection(self):
        a = (np.arange(CAP1440.samples * CAP1440.lines * 5) % 200).astype(np.uint8)
        with tempfile.NamedTemporaryFile(suffix='.vbi', delete=False) as f:
            f.write(a.tobytes())
        try:
            self.assertEqual(detect_format(f.name), 'cap1440')
            (geo, c), = list(read_fields(f.name, max_fields=4))
            self.assertIs(geo, CAP1440)
            self.assertEqual(c.shape, (4, 12, 1440))
        finally:
            os.unlink(f.name)


# ---- slicers -----------------------------------------------------------------------------------

class TestSlicer(TestCase):
    def _roundtrip_2x(self, geo, start):
        payloads = [bytes([0x05, 0xFC, 0x4D, 0x61]), bytes([0x07, 0x7C, 0x42, 0xFF]), bytes([0, 1, 2, 128])]
        lines = np.array([wave_2x(frame_bits(p), geo, start) for p in payloads] + [wave_2x(idle_bits(), geo, start)])
        bits = slice_2x(lines, geo)
        self.assertEqual(list(frame_kind(bits)), [1, 1, 1, 0])
        self.assertEqual([bytes(r) for r in frame_bytes(bits)[:3]], payloads)

    def test_2x_tbc(self):
        self._roundtrip_2x(TBC, 134.0)

    def test_2x_tbc_other_phase(self):
        self._roundtrip_2x(TBC, 141.3)

    def test_2x_cap1440(self):
        self._roundtrip_2x(CAP1440, 9.5)

    def test_1x(self):
        lines = np.array([wave_1x(odd(0x07), odd(ord('z')), TBC, 140.0)])
        b = slice_1x(lines, TBC)[0]
        self.assertEqual(list(b[7:10]), [0, 0, 1])
        v1 = sum(int(b[10 + i]) << i for i in range(8))
        v2 = sum(int(b[18 + i]) << i for i in range(8))
        self.assertEqual((v1, v2), (odd(0x07), odd(ord('z'))))


# ---- 2X demultiplexing and Type B messages ------------------------------------------------------

def block(n, key_byte, flags, payload, chain=0):
    """80-byte Type B block whose check byte is right for an identity 'decryption'."""
    check = (sum(payload) & 0xFF) ^ chain
    return struct.pack('<I', SYNC) + bytes([n, key_byte, flags, check]) + bytes(payload) + bytes(8)


def message_blocks(payloads):
    out, chain = [], 0
    for n, p in enumerate(payloads):
        b = block(n, 0x11, 0, p, chain)
        chain ^= b[7]
        out.append(b)
    return out


def parity_packet(body7):
    """07 ... 0F <xor> with odd parity on every byte."""
    pk = [0x07] + list(body7) + [0x0F]
    x = 0
    for c in pk:
        x ^= c
    return bytes(odd(c) for c in pk + [x])


def interleave(phase0_frames, phase1_frames, n):
    """Fields alternate between two channels; missing frames are idle."""
    rows = []
    for i in range(n):
        src = phase0_frames if i % 2 == 0 else phase1_frames
        k = i // 2
        rows.append(frame_bits(src[k]) if k < len(src) else idle_bits())
    return bits_array(rows)


def frames_of(data):
    data = bytes(data) + bytes(-len(data) % 4)
    return [data[i:i + 4] for i in range(0, len(data), 4)]


class Identity:
    """Stand-in for Freqccd: 'decrypts' by XOR with a byte derived from the key date."""

    def __init__(self, year=None, month=None):
        self.key = (year, month)

    def decrypt(self, payload, block_no, key_byte, year, month):
        if self.key == (None, None):
            return bytes(payload)
        if (year, month) == self.key:
            return bytes(payload)
        return bytes([(payload[0] + 1) & 0xFF]) + bytes(payload[1:])     # wrong key: sum is off by one


class TestStreams(TestCase):
    def setUp(self):
        p1 = bytes([0x13, 2]) + bytes(range(2, 64))
        p2 = bytes(range(100, 164))
        self.blocks = message_blocks([p1, p2])
        self.packet = parity_packet(b'|B00Q01^W@' + b'ABCDEFGHIJKLMNOP')
        ph0 = [f for b in self.blocks for f in frames_of(b)]
        ph1 = frames_of(self.packet)
        self.bits = interleave(ph0, ph1, 2 * len(ph0) + 4)

    def test_demux(self):
        dm = demux(self.bits)
        self.assertEqual([b[2] for b in dm['blocks']], self.blocks)
        self.assertEqual(len(dm['packets']), 1)
        p = dm['packets'][0]
        self.assertTrue(p['xor_ok'])
        self.assertTrue(p['parity_ok'])
        self.assertEqual(p['body'], self.packet)
        self.assertEqual(dm['stats']['blocks'], 2)

    def test_assemble_messages(self):
        dm = demux(self.bits)
        msgs, good, bad = assemble_messages(dm['blocks'], Identity(), 2001, 12)
        self.assertEqual((good, bad), (1, 0))
        self.assertEqual(msgs[0]['type'], 0x13)
        self.assertEqual(len(msgs[0]['data']), 128)
        self.assertIn('=== type 13', messages_text(msgs))

    def test_bad_check_is_rejected(self):
        dm = demux(self.bits)
        broken = [(ph, f, x[:7] + bytes([x[7] ^ 1]) + x[8:]) if x[4] == 1 else (ph, f, x)
                  for ph, f, x in dm['blocks']]
        msgs, good, bad = assemble_messages(broken, Identity(), 2001, 12)
        self.assertEqual((good, bad, msgs), (0, 1, []))

    def test_find_key_date(self):
        dm = demux(self.bits)
        y, m, hits, tried = find_key_date(dm['blocks'], Identity(2003, 6), range(2000, 2005))
        self.assertEqual((y, m, hits, tried), (2003, 6, 1, 1))

    def test_single_block_is_not_enough_for_a_key(self):
        # one block-0 check is a 1-in-256 test, so a lone one-block message cannot settle the key date
        lone = [(0, 0, block(0, 0x11, 0, bytes([0x3A, 1]) + bytes(62)))]
        self.assertEqual(find_key_date(lone, Identity(2003, 6), range(2000, 2005))[:2], (None, None))

    def test_clock_packet(self):
        # 07 z MIN HOUR DAY MONTH WEEKDAY YEAR-1990 TZ CHK 0F XOR, values as 0x40 + v
        # (Tuesday May 20 2003 01:47 UTC, 5 hours west of UTC, daylight saving)
        vals = [0x40 + v for v in (47, 1, 20, 5, 3, 13, 37)]
        chk = (0x7F - sum(vals)) & 0x7F
        body = [0x07, ord('z')] + vals + [chk, 0x0F]
        x = 0
        for c in body:
            x ^= c
        body.append(x)
        rows = []
        for i in range(0, len(body), 2):
            b1, b2 = odd(body[i]), odd(body[i + 1] if i + 1 < len(body) else 0)
            rows.append([1] * 7 + [0, 0, 1] + [(b1 >> k) & 1 for k in range(8)] + [(b2 >> k) & 1 for k in range(8)] + [0])
        pk, clock = decode_1x(bits_array(rows))
        self.assertEqual(len(clock), 1)
        self.assertEqual(clock[0]['utc'], '2003-05-20 01:47Z')
        self.assertEqual(clock[0]['weekday'], 'Tue')
        self.assertEqual(clock[0]['utc_offset'], -5)
        self.assertTrue(clock[0]['dst'])

    def test_a11_lineups(self):
        # header 13 chars (A11 at 5..7, lineup tag at 8..9), then 7-char records: pos(2) flag call(4)
        plain = b'\x07|B00A11X}@@@' + bytes([0x44, 0x42]) + b'yWSOC' + bytes([0x44, 0x43]) + b'yWFMY' + b'\x00'
        got = a11_lineups([dict(ok=True, plain=plain)])
        self.assertEqual(got, {'X}': {2: 'WSOC', 3: 'WFMY'}})


# ---- guide messages ------------------------------------------------------------------------------

def lineup_message(home, tag, entries, digital=False):
    b = bytearray(13)
    b[0] = 0x13
    b[2:4] = tag
    b[5] = 1 if digital else 0
    b[12] = len(home)
    b += home.encode() + bytes([0x79, len(entries)])
    for pos, net, name in entries:
        b += bytes([pos, net, len(name)]) + name.encode()
    return bytes(b + b'\x00')


def genre_message(code, name, subs):
    b = bytearray(14)
    b[0] = 0x26
    b += bytes([code, len(name)]) + name.encode() + bytes([len(subs)])
    for c, n in subs:
        b += bytes([c, len(n)]) + n.encode()
    return bytes(b)


class FakeSip:
    def __init__(self, rows):
        self.rows = rows

    def sip(self, data, year, month, day):
        return 44, 11219, self.rows


class TestGuide(TestCase):
    def test_lineup(self):
        b = lineup_message('WLUK', b'\x1d\xff', [(2, 4, 'WLUK'), (5, 5, 'WMVS'), (44, 0x39, 'ESPN')])
        self.assertEqual(lineup_key(b), 'WLUK-1dff-analog')
        rows = parse_lineup(b)
        self.assertEqual([(r['position'], r['name'], r['network']) for r in rows],
                         [(2, 'WLUK', 'FOX'), (5, 'WMVS', 'PBS'), (44, 'ESPN', None)])

    def test_genres(self):
        g = parse_genres([genre_message(0x10, 'Movies', [(0x1E, 'Western'), (0x11, 'Action')]),
                          genre_message(0x70, 'Series', [(0x7A, 'Sitcom')])])
        self.assertEqual([x['name'] for x in g], ['Movies', 'Series'])
        self.assertEqual(g[0]['subgenres'][0], dict(code=0x1E, name='Western'))

    def test_split_info(self):
        d = split_info('Western 1967 *** John Wayne, Robert Mitchum, TVG')
        self.assertEqual((d['movie_genre_or_country'], d['year'], d['star_rating'], d['cast'], d['tv_rating']),
                         ('Western', 1967, 3, ['John Wayne', 'Robert Mitchum'], 'TVG'))
        self.assertEqual(split_info('&')['cast'], [])

    def test_sip_listings_marks_cut_messages(self):
        data = bytes([0, 0x40, 0x00, 0, 0x12, 0x34, 0x56, 0x78]) + bytes(8)     # declares 64 bytes, has 16
        rows = [(20 * 60, 21 * 60, 0x1E, 'Rio Bravo', 'Western 1959 ****', 'A sheriff & &'),
                (21 * 60, 22 * 60, 0, '& & &', '', '')]
        out = sip_listings([dict(type=0, data=data)], FakeSip(rows), 2001, 12, 12)
        self.assertEqual(len(out), 1)                       # the empty cut-off slot is dropped
        self.assertEqual(out[0]['description'], 'A sheriff [...]')
        self.assertFalse(out[0]['complete'])
        self.assertEqual(out[0]['vcr_plus_code'], '12345678')

    def test_misc_flags(self):
        from lib.gemstar.guide import misc_flags, flag_text
        f = misc_flags(0xD0)
        self.assertEqual((f['closed_captioned'], f['stereo'], f['sap'], f['live']), (True, True, False, True))
        self.assertEqual(flag_text(0xE0), 'CC Stereo SAP')
        self.assertEqual(misc_flags(None), {})
        rows = [(20 * 60, 21 * 60, 0x1E, 'Rio Bravo', 'Western 1959 ****', 'A sheriff', 0x80)]
        data = bytes([0, 0x10, 0x00, 0, 0x12, 0x34, 0x56, 0x78]) + bytes(8)
        out = sip_listings([dict(type=0, data=data)], FakeSip(rows), 2001, 12, 12)
        self.assertEqual(out[0]['misc'], 0x80)
        self.assertIn('[CC]', listings_text(out))

    def test_build_guide(self):
        msgs = [dict(type=0x26, data=genre_message(0x10, 'Movies', [(0x1E, 'Western')])),
                dict(type=0x13, data=lineup_message('FAM', b'\x15\xfd', [(2, 1, 'WTVD')]))]
        listing = dict(guide_channel=44, date=11219, start=1200, end=1290, category=0x1E, title='Rio Bravo',
                       info='Western 1959 ****, John Wayne', description='A sheriff', complete=True,
                       vcr_plus_code='12345678', message=0)
        g = build_guide(msgs, [listing], {}, 2001)
        p = g['programs'][0]
        self.assertEqual((p['air_date'], p['start'], p['duration_min'], p['genre'], p['subgenre'], p['year']),
                         ('2001-12-19', '20:00', 90, 'Movies', 'Western', 1959))
        self.assertEqual(g['lineups']['FAM-15fd-analog'][0]['network'], 'ABC')

    def test_object_header(self):
        b = bytes([0x1E]) + bytes(17) + bytes([0x3F, 0x0B, 0x00, 0x0A]) + b'\x01\x02'
        self.assertEqual(object_header(b), 22)
        targeted = bytes([0x1E]) + bytes(9) + b'\xc1\x01\xc1\x03\x4e' * 4 + bytes([0x3F, 0x2E, 0x00, 0x0A])
        self.assertEqual(object_header(targeted + bytes(8)), len(targeted))


# ---- graphics objects ----------------------------------------------------------------------------

def rle_literal(data):
    out = bytearray()
    for i in range(0, len(data), 64):
        chunk = data[i:i + 64]
        out += bytes([len(chunk) - 1]) + chunk
    return bytes(out)


def object_parts(oid, w, h, payload, part_size=200):
    """0x1E messages carrying `payload` as object `oid` (w/h as transmitted, i.e. transposed)."""
    parts = [payload[i:i + part_size] for i in range(0, len(payload), part_size)]
    out = []
    for n, d in enumerate(parts):
        b = bytearray(18)
        b[0] = 0x1E
        b[5], b[6] = n, len(parts)
        b += bytes([0x3F, 0x0B, 0x00, 0x0A])
        b += struct.pack('<HHH', oid, w, h) + b'\x80' + len(payload).to_bytes(3, 'little')
        b += struct.pack('<H', len(d) + 7) + b'\x0b' + struct.pack('<H', oid) + b'\x00'
        b += struct.pack('<H', n * part_size) + b'\x00' + d
        out.append(dict(type=0x1E, data=bytes(b)))
    return out


def tiny_png():
    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b'\x00\xff\x00\x00'
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


class TestReedSolomon(TestCase):
    @staticmethod
    def encode(block):
        # systematic RS(76,68) over the block's codeword order, via the generator polynomial
        from lib.gemstar import rs
        cw = rs.to_cw(block)[:68]
        g = [1]
        for j in range(1, 9):
            g = [a ^ rs.mul(b, rs.EXP[j]) for a, b in zip(g + [0], [0] + g)]
        msg = cw + [0] * 8
        for i in range(68):
            c = msg[i]
            if c:
                for k in range(1, 9):
                    msg[i + k] ^= rs.mul(g[k], c)
        return rs.from_cw(block, cw + msg[68:])

    def test_corrects_up_to_four_bytes(self):
        from lib.gemstar.rs import correct
        good = self.encode(bytes(range(80)))
        self.assertEqual(correct(good), (good, 0))
        bad = bytearray(good)
        for i in (9, 30, 50, 77):
            bad[i] ^= 0x5A
        fixed, n = correct(bytes(bad))
        self.assertEqual((fixed, n), (good, 4))
        for i in (11, 12, 13, 14, 15):
            bad[i] ^= 0x33
        self.assertEqual(correct(bytes(bad))[0], None)


class TestGraphics(TestCase):
    def test_rle(self):
        self.assertEqual(graphics.unpack(bytes([0x02, 1, 2, 3, 0x85, 9])), bytes([1, 2, 3]) + bytes([9]) * 8)
        self.assertEqual(graphics.unpack(bytes([0x41, 1, 2]) + bytes(64), lit_max=0x80)[:3], bytes([1, 2, 0]))
        self.assertEqual(graphics.unpack(bytes([0x50, 7]), lit_max=0x40), bytes([7]) * (0x50 + 3))

    def test_reassembly(self):
        payload = bytes(range(256)) * 3
        objs = graphics.load_objects(object_parts(0x19CA, 136, 168, payload))
        o = objs[0x19CA]
        self.assertTrue(o['complete'])
        self.assertEqual(bytes(o['buf']), payload)
        self.assertEqual((o['w'], o['h']), (136, 168))

    def test_missing_part(self):
        msgs = object_parts(0x10, 10, 10, bytes(500))
        objs = graphics.load_objects(msgs[:1] + msgs[2:])
        self.assertFalse(objs[0x10]['complete'])

    def test_png_extraction(self):
        png = tiny_png()
        buf = b'\x07\x80' + bytes(6) + png + b'tail'
        self.assertEqual(graphics.extract_pngs(buf), [png])
        bad = bytearray(buf)
        bad[40] ^= 0xFF
        self.assertEqual(graphics.extract_pngs(bytes(bad)), [])

    def test_1bpp_xor_bitmap(self):
        s, h = 4, 12                                        # 32 x 12 pixels
        img = np.zeros((h, s * 8), np.uint8)
        img[3:9, 8:24] = 1                                  # a solid block
        rows = np.packbits(img, axis=1)
        delta = rows.copy()
        delta[1:] ^= rows[:-1]                              # XOR-delta coded rows
        stream = rle_literal(bytes(64) + delta.tobytes())   # 64-byte preamble first
        pal = bytes([0x00, 0x0F, 0xFF])                     # black, white (12-bit RGB, 2 colours)
        d = bytes([0x00, 0x10]) + pal[:3] + bytes([s, h]) + stream
        rgb, note = graphics.decode_bitmap(d, 40)
        self.assertEqual(rgb.shape, (h, 32, 3))
        self.assertTrue((rgb[5, 10] == 255).all())
        self.assertTrue((rgb[0, 0] == 0).all())
        self.assertIn('preamble=64', note)

    def test_text(self):
        rest = b'\x02\x01' + struct.pack('<HHH', 30, 0x8000, 0xFFFF) + struct.pack('<H', 15) + b'\x1bVBose\x81\x1bW now!'
        els = graphics.text_elements(rest)
        self.assertEqual(len(els), 1)
        self.assertTrue(els[0]['centered'])
        self.assertEqual(graphics.clean_text(els[0]['raw']), 'Bose® now!')

    def test_schedule(self):
        b = bytearray(18)
        b[0] = 0x1B
        b += bytes([0x3F, 0x2E, 0x00, 0x0A])
        k = len(b)
        b += bytes(10) + bytes([0x67, 0x6B, 0x3C, 0x00]) + b'\x00' + struct.pack('<3H', 0x17C9, 0x17CA, 0x17CB)
        b += bytes(k + 27 - len(b)) + bytes([5]) + b'Awnings'[:5]
        b += bytes([0x7F, 0x01, 1, 12, 0x01, 0x10, 0x20, 0x30, 0x40, 0x00])
        sch = graphics.schedule([dict(type=0x1B, data=bytes(b))])
        self.assertEqual(sch[0x17C9]['name'], 'Awnin')
        self.assertEqual(sch[0x17C9]['objects'], ['0x17c9', '0x17ca', '0x17cb'])
        self.assertEqual(sch[0x17C9]['slots'], ['40302010'])

    @skipUnless(HAVE_PIL, 'pillow not installed')
    def test_render_fill(self):
        cmds = (b'\x07\x05' + bytes([0xF0, 0x00, 0x0F]) + bytes(3) +          # pair 5: fg red, bg blue
                b'\x06\x05\x09' + struct.pack('<4H', 2, 3, 6, 9))              # fill y 2-6, x 3-9
        o = dict(buf=bytearray(cmds), w=10, h=12)                             # drawn 12 wide x 10 tall
        im, texts, notes = graphics.render(o)
        self.assertEqual(im.size, (12, 10))
        self.assertEqual(im.getpixel((5, 4)), (0, 0, 255))
        self.assertEqual(im.getpixel((0, 0)), (0, 0, 0))


# ---- NABTS ----------------------------------------------------------------------------------------

def nabts_line(packet, start=nabts.START):
    bits = [(v >> i) & 1 for v in packet for i in range(8)]
    y = np.zeros(910)
    for k, b in enumerate(bits):
        a, e = int(round(start + k * nabts.SPB)), int(round(start + (k + 1) * nabts.SPB))
        y[a:e] = 100.0 if b else 0.0
    return np.convolve(y, [0.25, 0.5, 0.25], 'same')


class TestNabts(TestCase):
    def test_hamming(self):
        for n in range(16):
            c = nabts.ham84_encode(n)
            self.assertEqual(nabts.ham84(c), (n, True))
            for bit in range(8):
                self.assertEqual(nabts.ham84(c ^ (1 << bit))[0], n)

    def test_parse_packet(self):
        hdr = [nabts.ham84_encode(v) for v in (0x3, 0x5, 0x2, 7, 9)]
        p = nabts.parse_packet([0x55, 0x55, 0xE7] + hdr + list(range(28)))
        self.assertEqual((p['address'], p['ci'], p['ps'], p['data']), (0x253, 7, 9, bytes(range(28))))

    def test_slice_lines(self):
        rng = np.random.default_rng(1)
        pkts = []
        for ci in range(40):
            hdr = [nabts.ham84_encode(v) for v in (0x3, 0x5, 0x2, ci % 16, 9)]
            pkts.append(bytes([0x55, 0x55, 0xE7] + hdr) + bytes(rng.integers(0, 256, 28, dtype=np.uint8)))
        lines = np.array([nabts_line(p) + rng.normal(0, 2, 910) for p in pkts])
        got = list(nabts.slice_lines(lines))
        self.assertEqual(len(got), 40)
        self.assertEqual(got[5][1]['data'], pkts[5][8:])


# ---- optional end-to-end run with the user's own files -------------------------------------------

@skipUnless(os.environ.get('GEMSTAR_FREQCCD') and os.environ.get('GEMSTAR_CAPTURE'),
            'set GEMSTAR_FREQCCD and GEMSTAR_CAPTURE to run against your own files')
class TestEndToEnd(TestCase):
    def test_capture(self):
        import json
        import gemstar_decoder
        with tempfile.TemporaryDirectory() as out:
            gemstar_decoder.main([os.environ['GEMSTAR_CAPTURE'], '-o', out, '--freqccd', os.environ['GEMSTAR_FREQCCD']])
            s = json.load(open(os.path.join(out, 'summary.json')))
            self.assertTrue(s['lines'])
            for line in s['lines']:
                if line.get('blocks'):
                    self.assertIsNotNone(line.get('key'))
                    self.assertGreater(line['messages_verified'], 0)
