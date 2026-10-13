import os
import tempfile
from multiprocessing import Pipe
from unittest import TestCase

import numpy as np

from lib.amol_decode import (
    AMOL_BIT_COUNT, AmolSlicer, AmolStreamDecoder, amol_data, AmolTimestamp, amol_frame_address, amol_parity_ok, build_amol_word,
    build_text_data, build_timestamp_data, decode_amol, decode_text_chunk, extract_amol_words, slice_amol_line,
)

__license__ = "Public domain / Unlicense, see LICENSE"

# 720 px over a 53.3 us active line (Rec. 601) -> 13.41 px per AMOL bit; a full 63.5 us line -> 11.33 px
ACTIVE_LINE_BIT_WIDTH = 720 / 53.33 / (64 * 15734.264 / 1e6)
FULL_LINE_BIT_WIDTH = 720 / 64.0


def render_word(word, origin=40.0, bit_width=ACTIVE_LINE_BIT_WIDTH, width=720, black=16, white=150,
                noise=0.0, blur=3, seed=0):
    """ Draws a 48 bit NRZ word onto one 8 bit luma row """
    x = np.arange(width) + 0.5
    k = np.floor((x - origin) / bit_width).astype(int)
    row = np.full(width, float(black))
    inside = (k >= 0) & (k < AMOL_BIT_COUNT)
    bits = np.array([(word >> (AMOL_BIT_COUNT - 1 - i)) & 1 for i in range(AMOL_BIT_COUNT)])
    row[inside] = np.where(bits[k[inside]] == 1, white, black)
    if blur > 1:
        row = np.convolve(row, np.ones(blur) / blur, mode="same")
    if noise:
        row += np.random.default_rng(seed).normal(0, noise, width)
    return np.clip(row, 0, 255).astype(np.uint8)


def render_cc_row(width=720, black=16, white=110):
    """ A line 21 caption row: 7 cycle clock run-in, start bits 001, two bytes """
    bit = 26.8
    x = np.arange(width, dtype=float)
    row = np.full(width, float(black))
    start = 70
    run_in = (x >= start) & (x < start + 7 * bit)
    row[run_in] = black + (white - black) * (0.5 + 0.5 * np.sin(2 * np.pi * (x[run_in] - start) / bit))
    bits = [0, 0, 1] + [1, 0, 1, 0, 0, 1, 0, 1] + [0, 1, 1, 0, 1, 0, 0, 1]
    data_start = start + 7 * bit
    for i, b in enumerate(bits):
        seg = (x >= data_start + i * bit) & (x < data_start + (i + 1) * bit)
        row[seg] = white if b else black
    return row.astype(np.uint8)


def stamp_word(fa, second, invert=False):
    return build_amol_word(fa, build_timestamp_data(37, 4, 30, 3, 28, second, True), invert_data=invert)


class TestAmolWord(TestCase):
    def test_parity(self):
        for fa in range(30):
            for data in (0, 1, 0x123456789, (1 << 35) - 1):
                word = build_amol_word(fa, data)
                self.assertTrue(amol_parity_ok(word))
                self.assertFalse(amol_parity_ok(word ^ (1 << 20)))
                self.assertEqual(amol_frame_address(word), fa)

    def test_timestamp_fields(self):
        t = AmolTimestamp(stamp_word(2, 12), 2)
        self.assertEqual(t.key(), (37, 4, 30, 3, 28, 12, True))
        self.assertTrue(t.plausible())
        self.assertTrue(t.parity_ok)
        self.assertEqual(str(t), "SID 37 (NBC)  4/30  3:28:12 PM")

    def test_inverted_timestamp(self):
        t = AmolTimestamp(stamp_word(27, 5, invert=True), 27, inverted=True)
        self.assertEqual(t.key(), (37, 4, 30, 3, 28, 5, True))

    def test_text_chunk(self):
        self.assertEqual(decode_text_chunk(build_amol_word(20, build_text_data("NBC N"))), "NBC N")
        self.assertIsNone(decode_text_chunk(build_amol_word(20, build_text_data("\x01\x02\x03ab"))))
        self.assertIsNone(decode_text_chunk(build_amol_word(20, build_text_data("  .  "))))


class TestAmolSlicer(TestCase):
    def test_round_trip(self):
        rng = np.random.default_rng(1)
        # the 48 bit word has to fit in the row: up to ~76 px in on an active-line capture
        for bit_width, origins in ((ACTIVE_LINE_BIT_WIDTH, (8.0, 20.0, 41.3, 60.7, 70.2)),
                                   (FULL_LINE_BIT_WIDTH, (20.0, 61.3, 90.7, 150.2))):
            for origin in origins:
                for _ in range(10):
                    word = build_amol_word(int(rng.integers(30)), int(rng.integers(1 << 35)))
                    row = render_word(word, origin=origin, bit_width=bit_width, noise=4, seed=int(rng.integers(1000)))
                    result = slice_amol_line(row)
                    self.assertIsNotNone(result, (bit_width, origin))
                    got, quality, width, start = result
                    self.assertAlmostEqual(start, origin, delta=1.0)
                    self.assertEqual(got, word, (bit_width, origin))
                    self.assertAlmostEqual(width, bit_width, delta=0.3)
                    self.assertGreater(quality, 0.1)

    def test_low_level_and_soft_edges(self):
        word = build_amol_word(3, build_timestamp_data(18, 12, 25, 11, 59, 59, False))
        row = render_word(word, black=30, white=75, blur=5, noise=3)
        result = slice_amol_line(row)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], word)

    def test_word_cut_off_at_line_end(self):
        word = build_amol_word(3, 0x5555)
        self.assertIsNone(slice_amol_line(render_word(word, origin=200)))

    def test_rejects_other_lines(self):
        self.assertIsNone(slice_amol_line(np.full(720, 16, dtype=np.uint8)))
        self.assertIsNone(slice_amol_line(render_cc_row()))
        rng = np.random.default_rng(2)
        noise_hits = sum(slice_amol_line(rng.integers(0, 256, 720).astype(np.uint8)) is not None for _ in range(50))
        self.assertEqual(noise_hits, 0)

    def test_extract_rows(self):
        word = stamp_word(2, 1)
        img = np.vstack([np.full(720, 16, dtype=np.uint8), render_cc_row(), render_word(word),
                         np.full(720, 16, dtype=np.uint8)])
        self.assertEqual([(r, w) for r, w, _ in extract_amol_words(img, 0, 4)], [(2, word)])


def render_slow_rise(word, origin, lag=2.5, seed=0, noise=3.0):
    """ Like render_word, but rising edges are slow and falling edges fast, as on the tapes: the preamble
        edges then give a biased bit width """
    row = render_word(word, origin=origin, blur=1).astype(float)
    out = np.empty_like(row)
    y = row[0]
    for i, x in enumerate(row):
        y = y + (x - y) / lag if x > y else x
        out[i] = y
    out += np.random.default_rng(seed).normal(0, noise, len(out))
    return np.clip(out, 0, 255).astype(np.uint8)


class TestAmolSlicerLearning(TestCase):
    def test_learned_width_fixes_idle_words(self):
        rng = np.random.default_rng(5)
        rows, truth = [], []
        for i in range(300):
            fa = i % 30
            data = int(rng.integers(1 << 35)) if fa in (2, 3, 20, 21, 22, 23, 24, 25) else 0
            truth.append(build_amol_word(fa, data))
            rows.append(render_slow_rise(truth[-1], 30 + rng.uniform(0, 15), seed=i))

        def idle_exact(results):
            return sum(1 for r, t in zip(results, truth) if amol_data(t) == 0 and r and r[0] == t)

        slicer = AmolSlicer()
        learned = [slicer.slice(r) for r in rows]
        per_line = [slice_amol_line(r) for r in rows]
        self.assertAlmostEqual(slicer.bit_width, ACTIVE_LINE_BIT_WIDTH, delta=0.05)
        self.assertGreater(idle_exact(learned), idle_exact(per_line) + 50)

    def test_known_width(self):
        word = stamp_word(2, 30)
        result = slice_amol_line(render_word(word, noise=3), bit_width=ACTIVE_LINE_BIT_WIDTH)
        self.assertEqual(result[0], word)
        self.assertEqual(result[2], ACTIVE_LINE_BIT_WIDTH)


def frames_for_seconds(seconds, first_second=10, text=None):
    """ One word per frame: timestamps at frame addresses 2 and 3, optional text at 20..25, idle elsewhere """
    words = []
    for s in range(seconds):
        for fa in range(30):
            if fa in (2, 3):
                words.append(stamp_word(fa, first_second + s))
            elif text and 20 <= fa <= 25:
                words.append(build_amol_word(fa, build_text_data(text[(fa - 20) // 2 * 5:][:5])))
            else:
                words.append(build_amol_word(fa, 0))
    return words


class TestAmolStream(TestCase):
    def decode(self, words, start_frame=1):
        out = []
        decoder = AmolStreamDecoder(out.append)
        for i, word in enumerate(words):
            if word is not None:
                decoder.add_frame(start_frame + i, word)
        decoder.flush()
        return out, decoder

    def test_timestamps_and_text(self):
        out, decoder = self.decode(frames_for_seconds(3, text="NBC NIGHTLY NEWS"))
        stamps = [l for l in out if "SID" in l]
        self.assertEqual(len(stamps), 3)
        self.assertTrue(stamps[0].endswith("3:28:10 PM"))
        self.assertTrue(stamps[2].endswith("3:28:12 PM"))
        self.assertEqual([l.split(": ", 1)[1] for l in out if "TEXT" in l], ["TEXT NBC NIGHTLY NEW"])
        self.assertEqual(decoder.parity_errors, 0)

    def test_repairs_frame_address(self):
        words = frames_for_seconds(2)
        # corrupt the frame address of one idle word in the first second
        words[10] = (words[10] & ~(0x1f << 36)) | (25 << 36)
        out, decoder = self.decode(words)
        self.assertEqual(decoder.repaired, 1)
        self.assertEqual(len([l for l in out if "SID" in l]), 2)

    def test_dropped_frames(self):
        words = frames_for_seconds(3)
        for i in (5, 6, 40, 70):
            words[i] = None
        out, _ = self.decode(words)
        self.assertEqual(len([l for l in out if "SID" in l]), 3)

    def test_drop_frame_minute(self):
        # like drop-frame timecode, frame addresses 0 and 1 are skipped at the start of most minutes
        words = []
        for second in (58, 59, 0, 1):
            for fa in range(30):
                if second == 0 and fa < 2:
                    continue
                if fa in (2, 3):
                    words.append(build_amol_word(fa, build_timestamp_data(37, 4, 30, 3, 28 if second >= 58 else 29,
                                                                          second, True)))
                else:
                    words.append(build_amol_word(fa, 0))
        out, decoder = self.decode(words)
        stamps = [l.split("  ")[-1] for l in out if "SID" in l]
        self.assertEqual(stamps, ["3:28:58 PM", "3:28:59 PM", "3:29:00 PM", "3:29:01 PM"])
        self.assertEqual(decoder.repaired, 0)

    def test_timestamp_at_26_27(self):
        words = []
        for fa in range(30):
            if fa == 27:
                words.append(stamp_word(27, 44, invert=True))
            else:
                words.append(build_amol_word(fa, 0))
        out, _ = self.decode(words)
        self.assertTrue(any(l.endswith("3:28:44 PM") for l in out))

    def test_parity_error_flagged(self):
        words = frames_for_seconds(1)
        words[2] ^= 1 << 5
        words[3] ^= 1 << 6
        out, decoder = self.decode(words)
        self.assertEqual(decoder.parity_errors, 2)


class TestAmolOutput(TestCase):
    def test_decode_amol_process(self):
        rx, tx = Pipe(False)
        for word in frames_for_seconds(2):
            tx.send([(5, word, 0.9)])
        tx.send([])  # a frame without AMOL
        tx.send("DONE")
        with tempfile.TemporaryDirectory() as d:
            name = os.path.join(d, "out")
            decode_amol(rx, name, {"frame_rate": 29.97})
            with open(name + ".amol") as f:
                lines = f.read().splitlines()
        self.assertEqual(len([l for l in lines if "SID 37 (NBC)" in l]), 2)
        self.assertTrue(lines[-1].startswith("# 60 words, 0 parity errors"))
