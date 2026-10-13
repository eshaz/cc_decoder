# coding: utf-8
"""
Nielsen AMOL I (Automated Measurement Of Lineups) decoder.

AMOL I is a 48 bit NRZ word sent once per frame on one line of the vertical
blanking interval (usually line 20, sometimes line 22 field 2) at 64 x fH,
about 1.007 Mbit/s, which is twice the line 21 caption bit rate.

Word layout, first bit sent first, every field MSB first:

    bit  0..6    preamble          1010110 (phase reference 101, start of message 0110)
    bit  7..11   frame address     0..29, one cycle per second
    bit 12..46   data              35 bits
    bit 47       parity            odd parity over bits 7..47

Frame addresses 2 and 3 carry a timestamp:

    source ID (6) month (4) day (5) hour (4, 12 hour clock) minute (6) second (6) PM (1) spare (3)

Some feeds send the timestamp in frame addresses 26 and 27 instead, with 27 bit-inverted.
Other frame addresses may carry text, 5 characters of 7 bit ASCII each (frame addresses
20..25 on most feeds, each chunk repeated in the following odd frame address).

Seen in off-air recordings (cx23885 captures, 1997-1999):
  * the word starts about 12 us after 0H and ends about 60 us, inside the 720 sample Rec. 601 line;
  * frame addresses skip 0 and 1 at the start of most minutes (29 -> 2), like drop-frame timecode;
  * networks fill other frame addresses with their own data (CBS: text, e.g. "CBS  NET04NY019");
  * local breaks and some programmes carry no AMOL or a different source ID.

The word layout and the observed source IDs were documented by the Teletext Rescue project
(https://github.com/AssunaYuuki/teletext_rescue). This module is an independent
implementation written from that description.

Public domain / Unlicense, same as the rest of cc_decoder.
"""

import numpy as np

from setproctitle import setproctitle
from multiprocessing import current_process

AMOL_BIT_COUNT = 48
AMOL_PREAMBLE = (1, 0, 1, 0, 1, 1, 0)
AMOL_PREAMBLE_VALUE = 0b1010110
AMOL_FRAMES_PER_CYCLE = 30

# bit boundaries inside the preamble where the level changes (1|0|1|0|1 1|0)
_PREAMBLE_EDGES = (1, 2, 3, 4, 6)
_PREAMBLE_ONES = (0, 2, 4, 5)
_PREAMBLE_ZEROS = (1, 3, 6)

# Plausible bit widths as a fraction of the image width. About 0.0186 when the image is
# the 720 sample active line (Rec. 601), about 0.0157 when it is a full 63.5 us line.
MIN_BIT_WIDTH_FRACTION = 0.0140
MAX_BIT_WIDTH_FRACTION = 0.0210

# minimum contrast between the preamble one and zero levels, in 8 bit luma
MIN_CONTRAST = 20

# Fields of the timestamp word: (first bit, length)
TIMESTAMP_FIELDS = {
    "sid": (12, 6),
    "month": (18, 4),
    "day": (22, 5),
    "hour": (27, 4),
    "minute": (31, 6),
    "second": (37, 6),
    "pm": (43, 1),
    "spare": (44, 3),
}

TIMESTAMP_FRAME_ADDRESSES = (2, 3, 26, 27)
INVERTED_TIMESTAMP_FRAME_ADDRESSES = (27,)

# Source IDs seen in off-air recordings so far
KNOWN_SOURCE_IDS = {
    18: "CBS",
    37: "NBC",
}

_TEXT_PUNCTUATION = " -./:&'"
_DATA_MASK = ((1 << 35) - 1) << 1  # the 35 data bits, in place


def amol_field(word, first, length):
    """ Returns `length` bits of the 48 bit `word` starting at bit `first` (0 = first bit sent) """
    return (word >> (AMOL_BIT_COUNT - first - length)) & ((1 << length) - 1)


def amol_frame_address(word):
    return amol_field(word, 7, 5)


def amol_data(word):
    """ The 35 data bits """
    return amol_field(word, 12, 35)


def amol_parity_ok(word):
    """ Odd parity over the frame address, data and parity bit (the last 41 bits) """
    return bin(word & ((1 << 41) - 1)).count("1") % 2 == 1


def build_amol_word(frame_address, data, invert_data=False):
    """ Builds a 48 bit word with correct odd parity. Mainly useful for testing. """
    if invert_data:
        data ^= (1 << 35) - 1
    word = (AMOL_PREAMBLE_VALUE << 41) | ((frame_address & 0x1f) << 36) | ((data & ((1 << 35) - 1)) << 1)
    if not amol_parity_ok(word):
        word |= 1
    return word


def build_timestamp_data(sid, month, day, hour, minute, second, pm):
    """ Packs a timestamp into the 35 data bits """
    word = 0
    for name, value in (("sid", sid), ("month", month), ("day", day), ("hour", hour),
                        ("minute", minute), ("second", second), ("pm", int(pm))):
        first, length = TIMESTAMP_FIELDS[name]
        word |= (value & ((1 << length) - 1)) << (AMOL_BIT_COUNT - first - length)
    return amol_data(word)


def build_text_data(text):
    """ Packs 5 characters of 7 bit ASCII into the 35 data bits """
    data = 0
    for c in text.ljust(5)[:5]:
        data = (data << 7) | (ord(c) & 0x7f)
    return data


def _crossing(line, threshold, start, end, rising):
    """ Sub-pixel position of the first threshold crossing in [start, end), or None """
    start = max(1, int(start))
    end = min(len(line), int(end))
    if end <= start:
        return None
    seg_prev = line[start - 1:end - 1]
    seg = line[start:end]
    if rising:
        hits = np.nonzero((seg_prev <= threshold) & (seg > threshold))[0]
    else:
        hits = np.nonzero((seg_prev > threshold) & (seg <= threshold))[0]
    if len(hits) == 0:
        return None
    i = start + hits[0]
    a, b = line[i - 1], line[i]
    return (i - 1) + (threshold - a) / (b - a)


def _cell_mean(line, start, width):
    """ Mean of the centre 60% of a bit cell """
    s = int(round(start + 0.2 * width))
    e = int(round(start + 0.8 * width))
    if e <= s:
        e = s + 1
    return float(line[s:e].mean())


def _try_sync(line, threshold, rise, min_width, max_width):
    """ Locks the bit clock to the preamble starting at the rising edge `rise`.
        Returns (origin, bit_width) or None. """
    n = len(line)
    # the first bit is a one; the fall at its end gives a first estimate of the bit width
    fall = _crossing(line, threshold, rise + 0.5 * min_width, rise + 1.5 * max_width, rising=False)
    if fall is None:
        return None
    width = fall - rise
    if not (min_width * 0.8 <= width <= max_width * 1.2):
        return None

    # walk the other preamble edges, refining the clock as we go
    edges_idx = [0, 1]
    edges_pos = [rise, fall]
    for k in _PREAMBLE_EDGES[1:]:
        expected = rise + k * width
        is_rising = AMOL_PREAMBLE[k] == 1
        pos = _crossing(line, threshold, expected - width / 3, expected + width / 3, rising=is_rising)
        if pos is None:
            return None
        edges_idx.append(k)
        edges_pos.append(pos)
        width, origin = np.polyfit(edges_idx, edges_pos, 1)

    width, origin = np.polyfit(edges_idx, edges_pos, 1)
    if not (min_width <= width <= max_width):
        return None
    if origin < 0 or origin + AMOL_BIT_COUNT * width > n:
        return None
    return origin, width


def slice_amol_line(row, bit_width=None, max_candidates=4):
    """ Looks for an AMOL I word in one image row.
        Returns (word, quality, bit_width, origin) or None. `quality` is the smallest decision margin of
        all 48 bits relative to the preamble contrast, 0..1; `origin` is where the first bit starts, in pixels.
        Pass `bit_width` (pixels) when it is known, see AmolSlicer; only the phase is then fitted. """
    result = _slice(np.asarray(row, dtype=np.float32), bit_width, max_candidates)
    return None if result is None else result[:4]


def _slice(line, bit_width, max_candidates):
    """ As slice_amol_line, plus a flag telling whether the bit width was measured over the whole word """
    n = len(line)
    min_width = MIN_BIT_WIDTH_FRACTION * n
    max_width = MAX_BIT_WIDTH_FRACTION * n
    if n < AMOL_BIT_COUNT * min_width:
        return None

    lo = np.percentile(line, 20)
    hi = np.percentile(line, 98)
    if hi - lo < MIN_CONTRAST:
        return None
    threshold = (lo + hi) / 2

    above = line > threshold
    rises = np.nonzero(~above[:-1] & above[1:])[0] + 1
    for r in rises[:max_candidates]:
        a, b = line[r - 1], line[r]
        rise = (r - 1) + (threshold - a) / (b - a)
        if bit_width is None:
            sync = _try_sync(line, threshold, rise, min_width, max_width)
        else:
            sync = _phase_sync(line, threshold, rise, bit_width)
        if sync is None:
            continue
        origin, width = sync

        high = np.mean([_cell_mean(line, origin + k * width, width) for k in _PREAMBLE_ONES])
        low = np.mean([_cell_mean(line, origin + k * width, width) for k in _PREAMBLE_ZEROS])
        if high - low < MIN_CONTRAST:
            continue
        slice_level = (high + low) / 2

        word, quality = _read_bits(line, origin, width, slice_level, high - low)
        if (word >> 41) != AMOL_PREAMBLE_VALUE:
            continue
        if bit_width is not None:
            return word, quality, width, origin, False
        # The preamble alone pins the clock only roughly, and a small error grows to a good part of a bit
        # by the end of the word. Re-fit it on every level change in the word and read the bits again.
        measured = False
        refit = _refit_clock(line, origin, width, word, slice_level)
        if refit is not None:
            word2, quality2 = _read_bits(line, refit[0], refit[1], slice_level, high - low)
            if (word2 >> 41) == AMOL_PREAMBLE_VALUE and quality2 >= quality:
                word, quality = word2, quality2
                origin, width = refit
                measured = True
        return word, quality, width, origin, measured
    return None


def _phase_sync(line, threshold, rise, width):
    """ Bit width known: fit only the start of the word, on the preamble edges """
    if origin_out_of_range(rise, width, len(line)):
        return None
    idx, pos = [0], [rise]
    for k in _PREAMBLE_EDGES:
        expected = rise + k * width
        p = _crossing(line, threshold, expected - width / 3, expected + width / 3, rising=AMOL_PREAMBLE[k] == 1)
        if p is None:
            return None
        idx.append(k)
        pos.append(p)
    origin = float(np.mean(np.asarray(pos) - np.asarray(idx) * width))
    if origin_out_of_range(origin, width, len(line)):
        return None
    return origin, width


def origin_out_of_range(origin, width, n):
    return origin < 0 or origin + AMOL_BIT_COUNT * width > n


class AmolSlicer(object):
    """ Slices rows using a bit width learned from the stream.

        The bit width is 1/64 of a line, so it is the same on every line of a recording. Measured on one
        line it is only as good as that word's edges: on idle words (data all zero) only the 7 preamble
        bits give a clock, and extrapolated over 40 bits that lands the parity bit half a bit off. So the
        width is learned from words whose clock could be fitted over the whole word, and then only the
        phase is fitted on each line. """

    LEARN_WORDS = 16      # clean words needed before the learned width is used
    WINDOW = 256          # recent widths kept, so the estimate can follow a change of source

    def __init__(self):
        self.widths = []
        self.bit_width = None

    def slice(self, row):
        line = np.asarray(row, dtype=np.float32)
        result = None
        if self.bit_width is not None:
            result = _slice(line, self.bit_width, 4)
            if result is not None and result[1] < 0.3:
                # a poor read with the learned width: see whether this line has a different clock
                own = _slice(line, None, 4)
                if own is not None and own[4] and own[1] > result[1]:
                    result = own
        if result is None:
            result = _slice(line, None, 4)
        if result is None:
            return None
        word, quality, width, origin, measured = result
        if measured and quality >= 0.3 and amol_parity_ok(word):
            self.widths.append(width)
            del self.widths[:-self.WINDOW]
            if len(self.widths) >= self.LEARN_WORDS:
                self.bit_width = float(np.median(self.widths))
        return word, quality, width, origin


def _read_bits(line, origin, width, slice_level, contrast):
    word = 0
    quality = 1.0
    for k in range(AMOL_BIT_COUNT):
        v = _cell_mean(line, origin + k * width, width)
        word = (word << 1) | (1 if v > slice_level else 0)
        quality = min(quality, abs(v - slice_level) / contrast * 2)
    return word, quality


def _refit_clock(line, origin, width, word, slice_level):
    """ Least-squares fit of (origin, width) to every bit boundary where the level changes """
    bits = [(word >> (AMOL_BIT_COUNT - 1 - k)) & 1 for k in range(AMOL_BIT_COUNT)]
    idx, pos = [], []
    for k in range(1, AMOL_BIT_COUNT):
        if bits[k] == bits[k - 1]:
            continue
        expected = origin + k * width
        p = _crossing(line, slice_level, expected - width / 3, expected + width / 3, rising=bits[k] == 1)
        if p is not None:
            idx.append(k)
            pos.append(p)
    if len(idx) < 8 or idx[-1] - idx[0] < 20:
        return None
    new_width, new_origin = np.polyfit(idx, pos, 1)
    if abs(new_width - width) > 0.05 * width:
        return None
    return new_origin, new_width


_DEFAULT_SLICER = None


def extract_amol_words(img, start_line, search_lines, slicer=None):
    """ Returns [(row_num, word, quality)] for every row in the search range carrying AMOL.
        Frames of one video should share a slicer; by default one per process is kept. """
    global _DEFAULT_SLICER
    if slicer is None:
        if _DEFAULT_SLICER is None:
            _DEFAULT_SLICER = AmolSlicer()
        slicer = _DEFAULT_SLICER
    found = []
    for row_num in range(start_line, min(start_line + search_lines, len(img))):
        result = slicer.slice(img[row_num])
        if result is not None:
            word, quality = result[0], result[1]
            found.append((row_num, word, quality))
    return found


class AmolTimestamp(object):
    def __init__(self, word, frame_address, inverted=False):
        data_word = word ^ _DATA_MASK if inverted else word
        for name, (first, length) in TIMESTAMP_FIELDS.items():
            setattr(self, name, amol_field(data_word, first, length))
        self.pm = bool(self.pm)
        self.frame_address = frame_address
        self.inverted = inverted
        self.parity_ok = amol_parity_ok(word)

    def plausible(self):
        return (1 <= self.month <= 12 and 1 <= self.day <= 31 and 1 <= self.hour <= 12
                and self.minute < 60 and self.second < 60)

    def key(self):
        return (self.sid, self.month, self.day, self.hour, self.minute, self.second, self.pm)

    def score(self):
        return 4 * self.parity_ok + 2 * (self.spare == 0) + (self.frame_address in (2, 3))

    def __str__(self):
        source = KNOWN_SOURCE_IDS.get(self.sid)
        return "SID %d%s  %d/%d  %d:%02d:%02d %s" % (
            self.sid, " (%s)" % source if source else "", self.month, self.day,
            self.hour, self.minute, self.second, "PM" if self.pm else "AM")


def decode_text_chunk(word):
    """ 5 characters of 7 bit ASCII, or None if it doesn't look like plain text """
    chars = [amol_field(word, 12 + 7 * i, 7) for i in range(5)]
    text = "".join(chr(c) for c in chars)
    if not all(c.isalnum() or c in _TEXT_PUNCTUATION for c in text):
        return None
    if sum(c.isalnum() for c in text) < 3:
        return None
    return text


class AmolStreamDecoder(object):
    """ Turns a stream of per-frame AMOL words into timestamps and text.
        Call `add_frame(frame, word)` for every frame that carried a word, then `flush()`. """

    def __init__(self, out_func):
        self.out = out_func
        self.last_frame = None
        self.last_fa = None
        self.cycle_key = None
        self.cycle = []
        self.last_text = None
        self.words = 0
        self.parity_errors = 0
        self.repaired = 0
        self.timestamps = 0

    def _frame_address(self, frame, word):
        """ The frame address counts with the frames, 0..29 each second, except that like NTSC drop-frame
            timecode it skips 0 and 1 at the start of most minutes (29 -> 2) to stay with the clock. So a
            word that passes parity is trusted as read; only a word that fails parity has its frame
            address taken from the count. """
        fa = amol_frame_address(word)
        if amol_parity_ok(word) and fa < AMOL_FRAMES_PER_CYCLE:
            return fa
        if self.last_frame is None or frame - self.last_frame >= AMOL_FRAMES_PER_CYCLE:
            return fa
        expected = (self.last_fa + (frame - self.last_frame)) % AMOL_FRAMES_PER_CYCLE
        if fa != expected:
            self.repaired += 1
        return expected

    def add_frame(self, frame, word):
        self.words += 1
        if not amol_parity_ok(word):
            self.parity_errors += 1
        fa = self._frame_address(frame, word)
        self.last_frame, self.last_fa = frame, fa
        if fa >= AMOL_FRAMES_PER_CYCLE:
            return
        key = frame - fa
        if key != self.cycle_key:
            self._flush_cycle()
            self.cycle_key = key
        self.cycle.append((frame, fa, word))

    def _flush_cycle(self):
        if not self.cycle:
            return
        best = None
        texts = {}
        for frame, fa, word in self.cycle:
            if amol_data(word) == 0:
                continue  # idle
            if fa in TIMESTAMP_FRAME_ADDRESSES:
                inverted = fa in INVERTED_TIMESTAMP_FRAME_ADDRESSES
                for inv in (inverted, not inverted):
                    t = AmolTimestamp(word, fa, inverted=inv)
                    if t.plausible() and (best is None or t.score() > best.score()):
                        best = t
            elif amol_parity_ok(word):
                text = decode_text_chunk(word)
                if text is not None and fa not in texts:
                    texts[fa] = text

        first_frame = self.cycle[0][0]
        if best is not None:
            self.timestamps += 1
            self.out("%d: %s%s" % (first_frame, best, "" if best.parity_ok else "  (parity error)"))

        # each text chunk is usually sent twice, in an even and the following odd frame address
        chunks = []
        for fa in sorted(texts):
            if fa % 2 == 1 and texts.get(fa - 1) == texts[fa]:
                continue
            chunks.append(texts[fa])
        line = "".join(chunks).strip()  # chunks are consecutive 5 character pieces of one string
        if line and line != self.last_text:
            self.out("%d: TEXT %s" % (first_frame, line))
            self.last_text = line
        self.cycle = []

    def flush(self):
        self._flush_cycle()
        self.out("# %d words, %d parity errors, %d frame addresses repaired, %d timestamps"
                 % (self.words, self.parity_errors, self.repaired, self.timestamps))


def decode_amol(rx, output_filename, options):
    """ Output process for `--ccformat amol`. Receives one list of (row_num, word, quality)
        per frame and writes timestamps and text to <output>.amol """
    setproctitle(current_process().name)
    from lib.cc_decode import get_output_function

    out_func, f = get_output_function("amol", output_filename)
    decoder = AmolStreamDecoder(out_func)
    frame = 0
    amol_row = None
    try:
        while True:
            try:
                words = rx.recv()
                if words == "DONE":
                    break
            except EOFError:
                break
            frame += 1
            if not words:
                continue
            # lock to the first row that carries AMOL, and stay there
            if amol_row is None or all(r != amol_row for r, _, _ in words):
                amol_row = words[0][0]
            for row_num, word, _ in words:
                if row_num == amol_row:
                    decoder.add_frame(frame, word)
                    break
        decoder.flush()
    finally:
        if f is not None:
            f.close()
