#!/usr/local/bin/python
# coding: utf-8
""" Silent Radio's line: a row of samples sliced into the two bytes it carries, found by
its pre-data code wherever it lands

Public domain / Unlicense, as with the rest of ccDecoder.
"""

import numpy as np

LINE_SILENT_RADIO = 'silentradio'

SILENT_RADIO_HEADER = (1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 0, 0, 0, 1, 1)
SILENT_RADIO_BITS = len(SILENT_RADIO_HEADER) + 16

# The level the line has to swing through before it is looked at; the KCET line swings 85
# of 255, blanking to peak.
SILENT_RADIO_MIN_SWING = 30

# The rising edges the pre-data code puts at known bits: the run-in's, two bits apart, and
# the start bit's, five bits after the last of them. Two run-in edges are enough to take the
# clock from, which matters on KCET, where the line's first cycle falls before the crop.
RUN_IN_EDGES = (0, 2, 4, 6, 8)
START_EDGE = 13
MIN_RUN_IN_EDGES = 2
MIN_HEADER_BITS = 11

def _clock(bits, times):
    """ `(bit width, start)` by least squares through edges at known bit positions """
    slope = np.cov(bits, times, bias=True)[0, 1] / bits.var()
    return slope, times.mean() - slope * bits.mean()

def _edges(x, threshold, margin):
    """ `(crossings, rising)`: where the line crosses `threshold`, to a fraction of a pixel, and
    the rising ones; with a `margin`, only the crossings the line carries on past `margin` from
    the threshold before it comes back """
    above = x > threshold
    at = np.flatnonzero(above[1:] != above[:-1])
    crossings = at + (threshold - x[at]) / (x[at + 1] - x[at])
    if not margin:
        return crossings, crossings[above[at + 1]]
    high, low = x > threshold + margin, x < threshold - margin
    decided = np.flatnonzero(high | low)
    state = high[decided]
    turned = np.flatnonzero(state[1:] != state[:-1]) + 1
    # each turn past the margin at the threshold's last crossing before it
    k = np.searchsorted(crossings, decided[turned], side='right') - 1
    kept = k >= 0
    return crossings[k[kept]], crossings[k[kept]][state[turned][kept]]

# A line the slicer can't read as it is gets a second look: its edges taken through a margin
# either side of the threshold, so that a notch in a run-in peak - KQED 1987 has them - isn't
# an edge; its pre-data code held against the threshold its own ones and zeros set; and a bit
# the crop leaves less than a bit of line before not held against the code. What it reads is
# taken only where every data bit is SILENT_RADIO_SURE of the swing from the threshold: on
# KQED, of the bytes a majority of their record's copies settles, 57% of those it read nearer
# are against it, 1.5% of the rest
SILENT_RADIO_EDGE_MARGIN = 0.05
SILENT_RADIO_SURE = 0.1
CEA608 = 'CEA-608'

def _sliced(x, base, top, refined):
    """ `(byte 1, byte 2, confidence)`, None if the line isn't Silent Radio, or CEA608 if it is
    a CEA-608 line """
    threshold = (base + top) / 2

    # edges to a fraction of a pixel; whole pixels leave WTTW with 2.2% of its bytes off the
    # carousel's vote rather than 1.3%
    crossings, rising = _edges(x, threshold, SILENT_RADIO_EDGE_MARGIN * (top - base) if refined else 0)
    gaps = np.diff(rising)
    for m in range(MIN_RUN_IN_EDGES - 1, min(len(gaps), len(RUN_IN_EDGES) + 2)):
        # the start edge is five bits on where the run-in edges before it are two apart
        run = gaps[max(0, m - len(RUN_IN_EDGES) + 1):m]
        period = run.mean() / 2 if len(run) else 0
        if not period or abs(gaps[m] - 5 * period) > period or np.abs(run - 2 * period).max() > period / 2:
            continue
        # a sixth run-in edge is a CEA-608 line, whose seven cycles read at half their
        # width otherwise pass for this code; on KCET that is line 21
        before = m - len(run) - 1
        if before >= 0 and abs(gaps[before] - 2 * period) < period / 2:
            return CEA608
        slope, start = _clock(np.array(RUN_IN_EDGES[-len(run) - 1:] + (START_EDGE,), dtype=np.float64),
                              rising[m - len(run):m + 2])
        break
    else:
        return None

    # the data's own edges fall on bit boundaries too, and fitting the clock to them as well
    # takes WTTW from 1.3% to 0.4%; KCET 1992 is the same either way
    bit = np.round((crossings - start) / slope)
    near = (bit >= 0) & (bit <= SILENT_RADIO_BITS) & (np.abs(crossings - start - bit * slope) < slope / 4)
    if np.unique(bit[near]).size > 1:
        slope, start = _clock(bit[near], crossings[near])

    centers = start + (np.arange(SILENT_RADIO_BITS) + 0.5) * slope
    if centers[-1] >= len(x) - 1:
        return None
    visible = centers >= (slope if refined else 0)
    values = np.interp(centers, np.arange(len(x)), x)
    header = [i for i in range(len(SILENT_RADIO_HEADER)) if visible[i]]
    if len(header) < MIN_HEADER_BITS:
        return None

    # the data is sliced midway between the code's own ones and zeros, not midway to the
    # line's peak, which overshoots them: that leaves WTTW at 1.4% rather than 0.4%
    known = np.array(SILENT_RADIO_HEADER, dtype=bool)[header]
    own = (values[header][known].mean() + values[header][~known].mean()) / 2
    bits = values > (own if refined else threshold)
    if any(bits[i] != SILENT_RADIO_HEADER[i] for i in header):
        return None
    bits = values > own
    data = bits[len(SILENT_RADIO_HEADER):]
    byte1, byte2 = np.packbits(data).tolist()
    confidence = tuple(float(v) for v in np.abs(values[len(SILENT_RADIO_HEADER):] - own) / (top - base))
    return byte1, byte2, confidence

def decode_silent_radio_row(line):
    """ `(byte 1, byte 2, confidence)` from one line, or None if it is not Silent Radio

    The long gap before the start bit anchors the pre-data code and the run-in edges give
    the bit clock, so nothing about the capture's geometry has to be known. Only a line
    whose visible pre-data code matches is accepted.
    """
    x = np.asarray(line, dtype=np.float32)
    # the data fills under half the line, so a low quantile is blanking
    k = len(x) // 4
    base, top = np.partition(x, k)[k], x.max()
    if top - base < SILENT_RADIO_MIN_SWING:
        return None
    got = _sliced(x, base, top, False)
    if got is None:
        got = _sliced(x, base, top, True)
        if got is not None and got is not CEA608 and min(got[2]) < SILENT_RADIO_SURE:
            return None
    return None if got is CEA608 else got
