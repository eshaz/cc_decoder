"""NABTS (EIA-516) packet slicer for 4fsc VBI captures (910 samples per line).

NABTS runs at 5.727 Mbit/s = 2.5 samples per bit at 4fsc. A packet is
    55 55 E7          clock run-in + framing code
    5 Hamming-8/4 bytes: 12-bit packet address, continuity index, packet structure
    28 data bytes (+ optional suffix)
Off-air recordings on VHS blur this badly, so each VBI line gets its own 13-tap linear equaliser,
trained first on the 24 known header bits and then decision-directed on lines whose header decodes.
Only the 4fsc ('tbc' / 'flac') layout is supported.
"""

import numpy as np

SPB = 2.5
START = 151.5
TAPS = np.arange(-6, 7)
HDR = np.array([(v >> i) & 1 for v in (0x55, 0x55, 0xE7) for i in range(8)])


def ham84(b):
    """Decode one Hamming 8/4 byte (bit order p1 d1 p2 d2 p3 d3 p4 d4). Returns (nibble, ok)."""
    p1, d1, p2, d2, p3, d3, p4, d4 = [(b >> i) & 1 for i in range(8)]
    A = p1 ^ d1 ^ d3 ^ d4
    B = p2 ^ d1 ^ d2 ^ d4
    C = p3 ^ d1 ^ d2 ^ d3
    D = p1 ^ d1 ^ p2 ^ d2 ^ p3 ^ d3 ^ p4 ^ d4
    if A and B and C:
        d1 ^= 1
    elif A and B:
        d4 ^= 1
    elif A and C:
        d3 ^= 1
    elif B and C:
        d2 ^= 1
    return d1 | d2 << 1 | d3 << 2 | d4 << 3, not ((A or B or C) and not D)


def ham84_encode(n):
    """Inverse of ham84 (used by the tests)."""
    d1, d2, d3, d4 = [(n >> i) & 1 for i in range(4)]
    p1 = d1 ^ d3 ^ d4
    p2 = d1 ^ d2 ^ d4
    p3 = d1 ^ d2 ^ d3
    p4 = p1 ^ d1 ^ p2 ^ d2 ^ p3 ^ d3 ^ d4
    return p1 | d1 << 1 | p2 << 2 | d2 << 3 | p3 << 4 | d3 << 5 | p4 << 6 | d4 << 7


def _norm(y):
    y = y.astype(float)
    return (y - y[150:880].mean()) / (y[150:880].std() + 1)


def _feats(y, st, nb):
    c = st + np.arange(nb) * SPB + SPB / 2 - 0.5
    return np.interp((c[:, None] + TAPS[None, :]).ravel(), np.arange(len(y)), y).reshape(nb, len(TAPS))


def _soft(y, w, st):
    nb = min(int((len(y) - 8 - st) / SPB), 288)
    return np.hstack([_feats(y, st, nb), np.ones((nb, 1))]) @ w


def _best_start(y, w):
    return max(np.arange(START - 1.5, START + 1.6, 0.25),
               key=lambda st: np.mean(np.sign(_soft(y, w, st)[:24]) == 2 * HDR - 1))


def _to_bytes(s):
    b = (s > 0).astype(int)
    return [int(sum(b[8 * k + i] << i for i in range(8))) for k in range(len(b) // 8)]


def _fit(X, T):
    return np.linalg.lstsq(np.hstack([X, np.ones((len(X), 1))]), T, rcond=None)[0]


def train(lines):
    """Equaliser weights for one VBI line, from an array [fields, 910]."""
    ys = [_norm(y) for y in lines[::3]]
    w = _fit(np.vstack([_feats(y, START, 24) for y in ys]), np.tile(2 * HDR - 1, len(ys)))
    for _ in range(3):
        X, T = [], []
        for y in ys[::max(1, len(ys) // 1500)]:
            st = _best_start(y, w)
            s = _soft(y, w, st)
            if _to_bytes(s)[:3] == [0x55, 0x55, 0xE7]:
                X.append(_feats(y, st, len(s)))
                T.append(np.sign(s))
        if not X:
            break
        w = _fit(np.vstack(X), np.concatenate(T))
    return w


def parse_packet(by):
    """Bytes starting at the 55 55 E7 run-in -> dict, or None if the Hamming header fails."""
    if len(by) < 36 or by[2] != 0xE7:
        return None
    h = [ham84(x) for x in by[3:8]]
    if not all(ok for _, ok in h):
        return None
    return dict(address=h[0][0] | h[1][0] << 4 | h[2][0] << 8, ci=h[3][0], ps=h[4][0], data=bytes(by[8:36]))


def slice_lines(lines, w=None):
    """Packets from one VBI line across fields. lines: [fields, 910]. Yields (field_index, packet dict)."""
    if w is None:
        w = train(lines)
    for fi in range(len(lines)):
        y = _norm(lines[fi])
        p = parse_packet(_to_bytes(_soft(y, w, _best_start(y, w))))
        if p:
            yield fi, p


def scan(path, line_indexes, fmt=None, max_fields=4000, ffmpeg='ffmpeg'):
    """NABTS packets from the given stored-line indexes of the first `max_fields` fields.
    Returns [dict(field, vbi_line, address, ci, ps, data_hex)]."""
    from lib.gemstar.capture import read_fields
    got = list(read_fields(path, fmt, max_fields=max_fields, ffmpeg=ffmpeg))
    if not got:
        return []
    geo = got[0][0]
    chunks = [a for _, a in got]
    if geo.samples != 910:
        raise ValueError('NABTS slicing needs a 4fsc (910 samples/line) capture')
    a = np.concatenate(chunks)
    out = []
    for li in line_indexes:
        for fi, p in slice_lines(a[:, li]):
            out.append(dict(field=fi, vbi_line=li + geo.first_line, address=f"0x{p['address']:03X}", ci=p['ci'],
                            ps=p['ps'], data_hex=p['data'].hex()))
    return out
