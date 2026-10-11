"""Bit slicers for the two Gemstar line rates.

Gemstar 2X (US5543852A): run-in 10101010 + frame code 10011101101, then 32 payload bits (4 bytes,
LSB first) at 64 bits per line period (about 1.007 Mbit/s). An idle line carries 1010101010101000011.
Gemstar 1X: the CEA-608 waveform (32 bits per line period) on another VBI line, two 7-bit+odd parity bytes.

Both slicers lock to the run-in by measuring its phase with a single-bin DFT, then sample at bit centres
with linear interpolation and compare against the mean level of the run-in.
"""

import numpy as np

FRAME_2X = '1010101010011101101'   # run-in + frame code of a data line
IDLE_2X = '1010101010101000011'
NB_2X = 53                          # 19 framing bits + 32 payload bits + 2 spare
NB_1X = 27


def _slice(y, geo, period, nbits, run_in, first_bit, thr_win, bit_step):
    n = np.arange(*run_in)
    c = np.exp(-2j * np.pi * n / period)
    z = (y[:, run_in[0]:run_in[1]] - y[:, run_in[0]:run_in[1]].mean(1, keepdims=True)) @ c
    amp = np.abs(z) / len(n) * 2
    p0 = (-np.angle(z) / (2 * np.pi)) * period
    p0 = np.where(p0 < 0, p0 + period, p0)
    p0 = p0 + np.ceil((first_bit - p0) / period) * period
    thr = y[:, thr_win[0]:thr_win[1]].mean(1)
    pos = p0[:, None] + np.arange(nbits)[None, :] * bit_step
    i0 = np.clip(np.floor(pos).astype(int), 0, y.shape[1] - 2)
    fr = pos - i0
    v = np.take_along_axis(y, i0, 1) * (1 - fr) + np.take_along_axis(y, i0 + 1, 1) * fr
    d = v - thr[:, None]
    return (d > 0).astype(np.uint8), amp.astype(np.float32)


def slice_2x(lines, geo):
    """lines: [fields, samples] for one VBI line. Returns bits [fields, 53]."""
    period = geo.line_period / 32.0
    bits, _ = _slice(lines, geo, period, NB_2X, geo.run_in_2x, geo.first_bit_2x, geo.thr_2x, geo.line_period / 64.0)
    return bits


def slice_1x(lines, geo):
    """CEA-608-rate slicer. Returns bits [fields, 27] (bits 7-9 = start bits 0 0 1, then two bytes)."""
    period = geo.line_period / 32.0
    bits, _ = _slice(lines, geo, period, NB_1X, geo.run_in_1x, geo.first_bit_1x, geo.thr_1x, period)
    return bits


def frame_kind(bits):
    """1 = data frame, 0 = idle frame, -1 = unreadable (per field)."""
    s19 = np.array([''.join(map(str, r[:19])) for r in bits])
    return np.where(s19 == FRAME_2X, 1, np.where(s19 == IDLE_2X, 0, -1))


def frame_bytes(bits):
    """The 4 payload bytes of every 2X frame (LSB first)."""
    return (bits[:, 19:51].reshape(-1, 4, 8) * (1 << np.arange(8))).sum(2).astype(np.uint8)


def scan_lines(path, fmt=None, max_fields=4000, ffmpeg='ffmpeg'):
    """Count 2X data frames on every stored line of the first `max_fields` fields."""
    from lib.gemstar.capture import read_fields
    counts = None
    geo = None
    for geo, a in read_fields(path, fmt, max_fields=max_fields, ffmpeg=ffmpeg):
        c = np.array([(frame_kind(slice_2x(a[:, li, :], geo)) == 1).sum() for li in range(geo.lines)])
        counts = c if counts is None else counts + c
    return geo, counts
