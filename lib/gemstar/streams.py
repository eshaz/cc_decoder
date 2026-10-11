"""The two logical channels inside a Gemstar 2X line, the 1X clock packet, and Type B message assembly.

Every field phase (even / odd field index) of a 2X line is an independent channel carrying either

* Type B blocks: 20 frames = 80 bytes
      0-3   05 FC 4D 61   sync (bit 0 of byte 3: 1 = 80-byte block)
      4     block number within the message
      5     key byte
      6     bit 7 month-parity flag, bits 0-6 checksum mode (0 = chained)
      7     check: (sum of decrypted payload) XOR chain == byte 7
      8-71  64 encrypted payload bytes
      72-79 8 Reed-Solomon parity bytes
* `|B` packets: 7-bit + odd parity bytes, `07 '|' 'B' ... 0F <xor>` (XOR of the 7-bit values = 0),
  scrambled with a monthly .INP table.
"""

import collections

import numpy as np

from lib.gemstar.slicer import frame_kind, frame_bytes

SYNC = 0x614DFC05
PAR = np.array([bin(x).count('1') % 2 for x in range(256)])


def s7(b):
    return ''.join(chr(c & 0x7F) if 32 <= (c & 0x7F) < 127 else f'<{c & 0x7F:02X}>' for c in b)


def demux(bits):
    """Split one line's 2X frames into Type B blocks and parity packets.
    Returns dict(blocks=[(phase, field, 80 bytes)], packets=[dict], stats=dict)."""
    kind = frame_kind(bits)
    by = frame_bytes(bits)
    w = by.copy().view('<u4').ravel()
    blocks, packets, stray = [], [], collections.Counter()
    for ph in (0, 1):
        idx = [i for i in range(ph, len(kind), 2) if kind[i] == 1]
        j = 0
        while j < len(idx):
            i = idx[j]
            if w[i] == SYNC:
                fr = idx[j:j + 20]
                if len(fr) == 20 and all(y - x == 2 for x, y in zip(fr, fr[1:])):
                    blocks.append((ph, i, bytes(by[fr].ravel())))
                j += 20
                continue
            if by[i, 0] == 0x07 and by[i, 1] == 0x7C:
                k, data = j, []
                while k < len(idx):
                    data += list(by[idx[k]])
                    k += 1
                    b7 = [x & 0x7F for x in data]
                    if 0x0F in b7[2:] and b7.index(0x0F, 2) + 1 < len(b7):
                        break
                    if len(data) > 400:
                        break
                b7 = [x & 0x7F for x in data]
                e = b7.index(0x0F, 2) if 0x0F in b7[2:] else None
                body = bytes(data[:e + 2]) if e is not None else bytes(data)
                x = 0
                for c in body:
                    x ^= c & 0x7F
                packets.append(dict(phase=ph, field=i, body=body, xor_ok=e is not None and x == 0,
                                    parity_ok=all(PAR[c] for c in body)))
                j = k
                continue
            stray[ph] += 1
            j += 1
    stats = dict(fields=int(len(kind)), data_frames=int((kind == 1).sum()), idle_frames=int((kind == 0).sum()),
                 unreadable=int((kind == -1).sum()), blocks=len(blocks), packets=len(packets),
                 packets_ok=sum(p['xor_ok'] for p in packets))
    return dict(blocks=blocks, packets=packets, stats=stats)


# ---- Type B messages ---------------------------------------------------------------------------

def find_key_date(blocks, fq, years=range(1985, 2040)):
    """Find the (year, month) whose Decrypt() makes the Type B checks pass. Returns (year, month, hits, tried).

    Each block-0 check is one byte, so over ~660 candidate dates a wrong one passes by chance now and
    then. Candidates are therefore found on up to 12 block-0s and then confirmed on whole messages: the
    date that verifies the most blocks wins, and it must verify at least two."""
    b0 = list({x for _, _, x in blocks if x[4] == 0})[:12]
    hits = collections.Counter()
    for x in b0:
        for y in years:
            for m in range(1, 13):
                if sum(fq.decrypt(x[8:72], x[4], x[5], y, m)) & 0xFF == x[7]:
                    hits[(y, m)] += 1
    best, best_blocks = None, 0
    for (y, m), n in hits.most_common(5):
        msgs, _, _ = assemble_messages(blocks, fq, y, m)
        nblocks = sum(len(x['data']) // 64 for x in msgs)
        if nblocks > best_blocks:
            best, best_blocks = (y, m, n), nblocks
    if best is None or best_blocks < 2:
        return None, None, 0, len(b0)
    return best[0], best[1], best[2], len(b0)


def assemble_messages(blocks, fq, year, month):
    """Chain consecutive blocks into messages, decrypt and verify them.
    Returns (unique verified messages [dict(type, first_field, data)], verified, failed)."""
    cache = {}

    def dec(x):
        if x not in cache:
            cache[x] = fq.decrypt(x[8:72], x[4], x[5], year, month)
        return cache[x]

    msgs = []
    for ph in (0, 1):
        cur = None
        for p, f, x in [t for t in blocks if t[0] == ph]:
            if x[4] == 0:
                if cur:
                    msgs.append(cur)
                cur = dict(field=f, blocks=[x])
            elif cur and x[4] == len(cur['blocks']):
                cur['blocks'].append(x)
            else:
                if cur:
                    msgs.append(cur)
                cur = None
        if cur:
            msgs.append(cur)
    # Byte 6 bit 7 is the month-parity flag (1 = odd month). Around a month change the head-end sends messages under
    # both the current and the next month's key, so a message whose flag does not match the key month is decrypted
    # with the adjacent month of the right parity.
    def months_for(flag):
        cands = [(year, month)]
        for dm in (1, -1):
            mm, yy = month + dm, year
            if mm == 13: mm, yy = 1, year + 1
            if mm == 0: mm, yy = 12, year - 1
            cands.append((yy, mm))
        return sorted(cands, key=lambda c: (c[1] & 1) != flag)

    def run(m, y, mo):
        chain, pt, ok = 0, b'', True
        for x in m['blocks']:
            d = fq.decrypt(x[8:72], x[4], x[5], y, mo) if (y, mo) != (year, month) else dec(x)
            ok &= ((sum(d) & 0xFF) ^ chain) == x[7]
            chain = chain ^ x[7] if (x[6] & 0x7F) == 0 else 0
            pt += d
        return ok, pt

    good = bad = 0
    uniq = collections.OrderedDict()
    for m in msgs:
        for y, mo in months_for(m['blocks'][0][6] >> 7):
            ok, pt = run(m, y, mo)
            if ok:
                break
        good += ok
        bad += (not ok)
        if ok:
            uniq.setdefault(pt, m['field'])
    out = [dict(type=pt[0], first_field=f, data=pt) for pt, f in uniq.items()]
    return out, good, bad


def messages_text(messages, header=''):
    lines = [header] if header else []
    for m in messages:
        pt = m['data']
        lines.append(f"\n=== type {pt[0]:02X}  {len(pt)} bytes  first field {m['first_field']}")
        for i in range(0, len(pt), 16):
            c = pt[i:i + 16]
            lines.append(f'  {i:04X} ' + ' '.join(f'{v:02X}' for v in c).ljust(48) + ' ' +
                         ''.join(chr(v) if 32 <= v < 127 else '.' for v in c))
    return '\n'.join(lines) + '\n'


# ---- |B packets ----------------------------------------------------------------------------------

def descramble_packets(packets, fq, force=None):
    """Try every .INP table, keep the one whose built-in checksum passes most often
    (or use table `force`, e.g. '100', without scoring).
    Returns (table_name or None, scores, [dict(field, ok, header, content, plain)])."""
    usable = [(p['field'], bytes(c & 0x7F for c in p['body'])) for p in packets
              if p['xor_ok'] and len(p['body']) >= 16]
    tables = fq.tables()
    if force is not None:
        if force not in tables:
            raise ValueError(f'no {force}.INP in {fq.inp_dir}')
        tables = {force: tables[force]}
    if not usable or not tables:
        return None, {}, []
    scores = collections.Counter()
    longs = [p for _, p in usable if len(p) > 30]
    for name, t in tables.items():
        fq.load_table(name, t)
        scores[name] = sum(fq.descramble(p)[1] for p in longs)
    ranked = scores.most_common(2)
    best = ranked[0][0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    # The right table passes nearly every long packet (48/48, 926 vs 21, 92 vs 4 on real captures);
    # a wrong one passes about 1 in 128 by chance. Demand a majority, at least two, and a clear lead.
    if force is None and (ranked[0][1] < max(2, (len(longs) + 1) // 2) or ranked[0][1] < 4 * second):
        return None, dict(ranked), []
    fq.load_table(best, tables[best])
    out = []
    for f, p in usable:
        plain, ok = fq.descramble(p)
        out.append(dict(field=f, ok=ok, header=s7(plain[:13]), content=s7(plain[13:]), plain=plain))
    return best, dict(ranked), out


def a11_lineups(descrambled):
    """Channel position -> 4-character call sign from descrambled A11 records, per lineup tag.
    A11 content is a run of 7-character records ended by 00: two position characters
    (position = (c0 & 3) * 64 + (c1 & 0x3F)), one flag character, and the call sign."""
    fam = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    for p in descrambled:
        h = p['plain'][:13]
        if not p['ok'] or h[5:8] != b'A11':
            continue
        tag = s7(h[8:10])                      # lineup tag, e.g. 'X}' / 'X<7F>' (2001) or 'AF' (Nov 2001, WPVI)
        body = p['plain'][13:]
        for k in range(0, len(body) - 6, 7):
            rec = body[k:k + 7]
            if rec[0] == 0:
                break
            cs = rec[3:7]
            if not all(32 <= c < 127 for c in cs):
                break
            fam[tag][(rec[0] & 3) * 64 + (rec[1] & 0x3F)][cs.decode('latin1').strip()] += 1
    return {t: {pos: c.most_common(1)[0][0] for pos, c in sorted(v.items())} for t, v in fam.items()}


# ---- 1X lines: clock packet -----------------------------------------------------------------------

def decode_1x(bits_1x, kind_2x=None):
    """Packets carried on the 1X (CEA-608 rate) lines of the Gemstar line.
    The clock packet is  07 'z' MIN HOUR DAY MONTH WEEKDAY YEAR TZ CHK 0F XOR  with every field = char & 0x3F
    (HOUR & 0x1F, MONTH & 0x1F): WEEKDAY 1 = Sunday, YEAR = year - 1990, TZ = hours west of UTC (+32 while
    daylight saving is in effect). CHK makes bytes 2..9 sum to 127 (mod 128). The time is UTC.
    Returns (packets, clock_rows)."""
    b = bits_1x
    W = 1 << np.arange(8)
    c1 = (b[:, 10:18] * W).sum(1)
    c2 = (b[:, 18:26] * W).sum(1)
    ok = (b[:, 7] == 0) & (b[:, 8] == 0) & (b[:, 9] == 1) & (PAR[c1] == 1) & (PAR[c2] == 1)
    if kind_2x is not None:
        ok &= kind_2x != 1
    c1 &= 0x7F
    c2 &= 0x7F
    stream, fields = [], []
    for i in np.nonzero(ok & ((c1 | c2) != 0))[0]:
        stream += [int(c1[i]), int(c2[i])]
        fields += [int(i), int(i)]
    pk, j = [], 0
    while j < len(stream) - 1:
        if stream[j] == 7:
            k = j + 1
            while k < len(stream) - 1 and stream[k] != 0x0F and k - j < 200:
                k += 1
            body = stream[j:k + 2]
            x = 0
            for c in body:
                x ^= c
            pk.append((fields[j], body, x == 0))
            j = k + 2
        else:
            j += 1
    clock = []
    for fl, body, good in pk:
        if not good or len(body) < 12 or body[1] != ord('z'):
            continue
        v = [c & 0x3F for c in body[2:9]]
        if (sum(body[2:10]) & 0x7F) != 0x7F:
            continue
        # fields: MIN HOUR DAY MONTH WEEKDAY(1 = Sunday) YEAR-1990 TZ(hours west of UTC, +32 = daylight saving)
        wd = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][(v[4] - 1) % 7]
        tz = v[6] & 0x1F
        clock.append(dict(field=fl, utc=f"{1990 + v[5]:04d}-{v[3] & 0x1F:02d}-{v[2]:02d} {v[1] & 0x1F:02d}:{v[0]:02d}Z",
                          weekday=wd, utc_offset=-tz, dst=bool(v[6] & 0x20)))
    return pk, clock
