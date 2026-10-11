"""Construction tests for the CEA-608 line 21 waveform, its slicer and the byte layer above it.

    python3 -m pytest tests/test_cea608_signal.py
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

import lib.cc_decode as cc_decode
from lib.cc_decode import (LINE_CAPTIONS, LINE_NOTHING, LINE_STARSIGHT, Cea608Lines,
                           LineParityRate, LineService, _level_line, band_limit, cea608_byte,
                           cea608_parity_ok, decode_bytes, decode_cea608_row, decode_cea608_rows,
                           decode_row, precompute_sine_templates, sync_to_preamble)
from tests.test_cea608 import Bad, wire


WIDTH = 720
D_US = 1e6 / (32 * 15734.26)                       # CEA-608-E Table 2 note 1
RISE = 0.240 / D_US                                # CEA-608-E Table 2
SLOW_RISE = 0.480 / D_US                           # CEA-608-E Table 2

MIN_PERIOD = cc_decode.MIN_CLOCK_FRACTION * WIDTH
BT601 = 858 / 32
FOUR_FSC = 910 / 32 * WIDTH / 760
ACTIVE_LINE = WIDTH / 52.6 * D_US

BT601_MHZ = 13.5
BT601_0H = 122

BLANK = 40.0
LOGIC_ONE = 200.0


def run_in_start(a_us, period=BT601):
    # CEA-608-E Table 2, Figure 2 note 1
    return a_us * BT601_MHZ - BT601_0H - 0.25 * period


def ire(level):
    return BLANK + (LOGIC_ONE - BLANK) * level / 50.0


def bits(byte1, byte2):
    return [byte1 >> i & 1 for i in range(8)] + [byte2 >> i & 1 for i in range(8)]


def line21(byte1, byte2, period=BT601, start=None, low=BLANK, high=LOGIC_ONE, rise=RISE,
           cycles=7, cells=None):
    if start is None:
        start = run_in_start(10.5, period)
    if cells is None:
        cells = [0, 0, 1] + bits(byte1, byte2)                          # CEA-608-E 5.2
    tau = (np.arange(WIDTH) - start) / period
    level = np.zeros(WIDTH)

    run_in = (tau >= 7 - cycles) & (tau < 7)                              # CEA-608-E Table 2 note 2
    level[run_in] = 0.5 - 0.5 * np.cos(2 * np.pi * tau[run_in])

    span = rise * math.pi / (math.acos(-0.8) - math.acos(0.8))           # CEA-608-E Table 2 note 6
    after = tau >= 7
    previous = 0.0
    for index, value in enumerate(list(cells) + [0.0]):
        middle = 6.75 + index                                             # CEA-608-E Table 2 note 3
        ramp = np.clip((tau[after] - middle) / span + 0.5, 0.0, 1.0)
        level[after] += (value - previous) * (0.5 - 0.5 * np.cos(np.pi * ramp))
        previous = value

    return np.clip(np.round(low + (high - low) * level), 0, 255).astype(np.uint8)


def with_noise(line, sigma, rng):
    return np.clip(np.round(line + rng.normal(0.0, sigma, line.size)), 0, 255).astype(np.uint8)


def with_tilt(line, rise):
    return np.clip(np.round(line + rise * np.arange(line.size) / line.size), 0, 255).astype(np.uint8)


def slicer(monkeypatch, run_in=cc_decode.PREAMBLE_RUN_IN_COUNT, width=WIDTH):
    monkeypatch.setattr(cc_decode, 'PRE_COMPUTED_PREAMBLE_TEMPLATES',
                        precompute_sine_templates(width, run_in))


def sliced(line, min_correlation=0.5):
    return decode_row(np.asarray(line, dtype=np.uint8).reshape(1, -1), 0, min_correlation, False)


def read(line, min_correlation=0.5):
    row = sliced(line, min_correlation)
    return None if row is None else row[1:3]


def random_pairs(count, seed):
    rng = np.random.default_rng(seed)
    return [(int(a), int(b)) for a, b in rng.integers(0, 256, size=(count, 2))]


PAIRS = random_pairs(16, 608)
START_BIT_FAILURE = (0, None, None, 0, ())


def cell_line(cells, unsettled=None, width=24, run_in=168):
    line = [np.full(run_in, 0.5)]
    for index, value in enumerate(cells):
        cell = np.full(width, float(value))
        if index == unsettled:
            cell[0::2], cell[1::2] = 0.0, 1.0
        line.append(cell)
    line.append(np.zeros(width))
    return np.concatenate(line)


def prefix_sums(line):
    return np.concatenate(([0.0], np.cumsum(line))), np.concatenate(([0.0], np.cumsum(line * line)))


def sliced_cells(line):
    return decode_bytes(line, 0, 168, 24.0, 1.0, False, *prefix_sums(line))


def test_every_first_byte_value_decodes(monkeypatch):
    # CEA-608-E 5.2, Table 2
    slicer(monkeypatch)
    assert [read(line21(value, 0x80)) for value in range(256)] == [(value, 0x80) for value in range(256)]


def test_every_second_byte_value_decodes(monkeypatch):
    # CEA-608-E 5.2, Table 2
    slicer(monkeypatch)
    assert [read(line21(0x80, value)) for value in range(256)] == [(0x80, value) for value in range(256)]


def test_bit_periods_across_the_template_search(monkeypatch):
    # CEA-608-E Table 2 note 1
    slicer(monkeypatch)
    for period, start in ((MIN_PERIOD, None), (BT601, None), (FOUR_FSC, None),
                          (ACTIVE_LINE, None), (28.0, -3.0)):
        assert [read(line21(a, b, period=period, start=start)) for a, b in PAIRS] == PAIRS


def test_run_in_start_across_interval_a(monkeypatch):
    # CEA-608-E Table 2
    slicer(monkeypatch)
    for a_us in (10.0, 10.25, 10.5, 10.75, 11.0):
        assert [read(line21(a, b, start=run_in_start(a_us))) for a, b in PAIRS] == PAIRS
    for start in (-6.0, 0.0, 36.0):
        assert [read(line21(a, b, start=start)) for a, b in PAIRS] == PAIRS


def test_levels_within_the_decoder_bounds(monkeypatch):
    # CEA-608-E Table 2 note 8
    slicer(monkeypatch)
    for low, high in ((0, 50), (-2, 38), (12, 52), (-2, 58), (12, 62)):
        assert [read(line21(a, b, low=ire(low), high=ire(high))) for a, b in PAIRS] == PAIRS


def test_dc_offset_and_amplitude(monkeypatch):
    slicer(monkeypatch)
    for low, high in ((16.0, 126.0), (100.0, 250.0), (120.0, 160.0), (200.0, 230.0)):
        assert [read(line21(a, b, low=low, high=high)) for a, b in PAIRS] == PAIRS


def test_rise_times_up_to_the_decoder_bound(monkeypatch):
    # CEA-608-E Table 2 note 6
    slicer(monkeypatch)
    for rise in (RISE, 0.288 / D_US, SLOW_RISE):
        assert [read(line21(a, b, rise=rise)) for a, b in PAIRS] == PAIRS


def test_mild_gaussian_noise(monkeypatch):
    slicer(monkeypatch)
    rng = np.random.default_rng(608)
    assert [read(with_noise(line21(a, b), 8.0, rng)) for a, b in PAIRS] == PAIRS


def test_level_fit_follows_levels_that_drift_across_the_line(monkeypatch):
    slicer(monkeypatch)
    lines = [with_tilt(line21(a, b, low=20.0, high=120.0), 80.0) for a, b in PAIRS]
    assert [read(line) for line in lines] == PAIRS

    monkeypatch.setattr(cc_decode, 'LEVEL_FIT_PASSES', 0)
    assert [read(line) for line in lines] != PAIRS


def test_lines_with_too_few_cells_on_one_level(monkeypatch):
    slicer(monkeypatch)
    for pair in ((0x00, 0x00), (0x01, 0x00), (0x00, 0x01), (0xFF, 0xFF), (0xFE, 0xFF)):
        assert read(line21(*pair)) == pair


def test_null_pair(monkeypatch):
    # CEA-608-E 8.2, D.7
    slicer(monkeypatch)
    row = sliced(line21(0x80, 0x80))
    assert row[:4] == (0, 0x80, 0x80, 0)
    assert decode_cea608_row(row) == (0, '', False, 0x00, True, 0x00, True)


def test_blank_line_is_nothing(monkeypatch):
    # CEA-608-E Figure 2 note 2, 8.2
    slicer(monkeypatch)
    assert sync_to_preamble(np.zeros((1, WIDTH), dtype=np.uint8), 0) is None
    assert [sliced(np.full(WIDTH, level)) for level in range(256)] == [None] * 256


def test_pure_noise_is_nothing(monkeypatch):
    slicer(monkeypatch)
    rng = np.random.default_rng(608)
    uniform = [rng.integers(0, 256, WIDTH) for _ in range(8)]
    gaussian = [np.clip(np.round(rng.normal(BLANK, 20.0, WIDTH)), 0, 255) for _ in range(8)]
    assert [sliced(line) for line in uniform + gaussian] == [None] * 16


def test_min_correlation_gates_the_match(monkeypatch):
    slicer(monkeypatch)
    line = line21(0x94, 0x2C)
    score = sync_to_preamble(line.reshape(1, -1), 0)['score']
    assert read(line, min_correlation=score - 1e-6) == (0x94, 0x2C)
    assert sliced(line, min_correlation=score) is None


def test_run_in_too_far_right_for_its_data_is_nothing(monkeypatch):
    slicer(monkeypatch)
    for start in (300.0, 500.0, 650.0):
        line = line21(0x94, 0x2C, start=start)
        assert sync_to_preamble(line.reshape(1, -1), 0) is None
        assert sliced(line) is None


def test_decode_row_keeps_the_row_it_was_asked_for(monkeypatch):
    slicer(monkeypatch)
    image = np.stack((np.full(WIDTH, BLANK, dtype=np.uint8), line21(0x94, 0x2C),
                      line21(0x94, 0x2C, low=LOGIC_ONE, high=BLANK)))
    assert decode_row(image, 0, 0.5, False) is None
    assert decode_row(image, 1, 0.5, False)[:4] == (1, 0x94, 0x2C, 0)
    assert decode_row(image, 2, 0.5, False) == (2, None, None, 0, ())


def test_wrong_start_bits_fail_the_slicing(monkeypatch):
    # CEA-608-E 5.2
    slicer(monkeypatch)
    for a, b in PAIRS:
        data = bits(a, b)
        assert sliced(line21(a, b, low=LOGIC_ONE, high=BLANK)) == START_BIT_FAILURE
        assert sliced(line21(a, b, cells=[1, 1, 0] + data)) == START_BIT_FAILURE
        assert sliced(line21(a, b, cells=[0, 0.65, 1] + data)) == START_BIT_FAILURE
        assert sliced(line21(a, b, cells=[0, 0, 0.35] + data)) == START_BIT_FAILURE


def test_missing_third_start_bit_is_not_read_as_data(monkeypatch):
    # CEA-608-E 5.2, Table 2
    slicer(monkeypatch)
    row = sliced(line21(0xC1, 0xC2, cells=[0, 0, 0] + bits(0xC1, 0xC2)))
    assert row is None or row[1:] == (None, None, 0, ())


def test_first_start_bit_overshoot_still_decodes(monkeypatch):
    # CEA-608-E Figure 2 note 4
    slicer(monkeypatch)
    assert [read(line21(a, b, cells=[0.7, 0, 1] + bits(a, b))) for a, b in PAIRS] == PAIRS


def test_truncated_run_in(monkeypatch):
    # CEA-608-E Table 2
    slicer(monkeypatch)
    assert [read(line21(a, b, cycles=6)) for a, b in PAIRS] == PAIRS
    assert [sliced(line21(a, b, cycles=3)) for a, b in PAIRS] == [None] * len(PAIRS)

    slicer(monkeypatch, 3.0)
    assert [read(line21(a, b, cycles=3)) for a, b in PAIRS] == PAIRS


def test_run_in_count_matches_the_visible_cycles(monkeypatch):
    # CEA-608-E Table 2 notes 2, 3
    for count in (3.0, 7.0):
        slicer(monkeypatch, count)
        assert [read(line21(a, b, cycles=count)) for a, b in PAIRS] == PAIRS


def test_confidence_on_a_clean_line(monkeypatch):
    slicer(monkeypatch)
    for a, b in PAIRS + [(0x80, 0x80), (0x00, 0x00), (0xFF, 0xFF)]:
        _, _, _, noisy, confidence = sliced(line21(a, b))
        assert noisy == 0
        assert len(confidence) == 16
        assert all(0.75 < value < 1.25 for value in confidence)


def test_bit_at_the_slicing_level_has_low_confidence(monkeypatch):
    slicer(monkeypatch)
    for index in (0, 5, 8, 12):
        cells = [0, 0, 1] + bits(0x94, 0x2C)
        cells[3 + index] = 0.5
        _, byte1, byte2, noisy, confidence = sliced(line21(0x94, 0x2C, cells=cells))
        assert (byte1 | byte2 << 8) & ~(1 << index) == (0x94 | 0x2C << 8) & ~(1 << index)
        assert noisy == 0
        assert confidence[index] < 0.3
        assert all(value > 0.75 for at, value in enumerate(confidence) if at != index)


def test_unsettled_bit_is_flagged_and_its_parity_corrects_it():
    # CEA-608-E 5.3, Table 52
    byte1, byte2, noisy, confidence = sliced_cells(cell_line([0, 0, 1] + bits(0x94, 0x2C), unsettled=3 + 2))
    assert (byte1, byte2, noisy) == (0x90, 0x2C, 1 << 2)
    assert confidence[2] < 0.1
    assert decode_cea608_row((14, byte1, byte2, noisy, confidence)) == \
        (14, 'CC1 Erase Displayed Memory', True, 0x14, True, 0x2C, True)


def test_line_with_no_eye_reports_no_confidence():
    assert sliced_cells(cell_line([0, 0, 1] + [0.5] * 16)) == (0, 0, 0, (0.0,) * 16)


def test_cells_past_the_end_of_the_line_are_not_sliced():
    line = cell_line([0, 0, 1] + bits(0x94, 0x2C))[:168 + 24 * 10]
    assert sliced_cells(line) == (None, None, 0, ())


def test_level_line():
    assert _level_line([0.0, 1.0, 0.25, 1.0, 0.5], [False, True, False, True, False], False) == \
        [0.0, 0.125, 0.25, 0.375, 0.5]
    assert _level_line([0.25, 0.75, 0.25], [False, True, False], True) == [0.75, 0.75, 0.75]


def test_debug_plot_is_given_the_bits_it_sliced(monkeypatch):
    slicer(monkeypatch)
    shown = []
    monkeypatch.setattr(cc_decode, 'show_debug_plot', lambda *args: shown.append(args))
    assert decode_row(line21(0x94, 0x2C).reshape(1, -1), 0, 0.5, True)[1:3] == (0x94, 0x2C)
    (args,) = shown
    assert args[5] == [0, 0, 1] + bits(0x94, 0x2C)
    assert args[8:] == ([0x94, 0x2C], [True, True])


def test_template_search_stops_where_a_line_no_longer_fits(monkeypatch):
    search = precompute_sine_templates(WIDTH, cc_decode.PREAMBLE_RUN_IN_COUNT)
    assert all(max_width < WIDTH for _, _, _, _, max_width, _, _ in search.spans)

    slicer(monkeypatch, width=100)
    assert cc_decode.PRE_COMPUTED_PREAMBLE_TEMPLATES.spans == ()
    assert sliced(np.arange(100) % 50) is None


def test_band_limit_keeps_the_run_in_and_drops_what_is_above_the_data():
    # CEA-608-E Table 2 note 2
    x = np.arange(WIDTH)
    run_in = np.sin(2 * np.pi * 27 * x / WIDTH)
    above = np.sin(2 * np.pi * 54 * x / WIDTH)
    assert np.allclose(band_limit(run_in), run_in, atol=0.01)
    assert np.allclose(band_limit(above), 0.0, atol=1e-9)


def test_cea608_byte_and_parity_spot_checks():
    # CEA-608-E 5.3
    assert cea608_parity_ok(0x80) and cea608_parity_ok(0xC1) and cea608_parity_ok(0x7F)
    assert not cea608_parity_ok(0x00) and not cea608_parity_ok(0x41) and not cea608_parity_ok(0xFF)
    assert cea608_byte(0xC1, 0, 0) == (0x41, True)
    assert cea608_byte(0x41, 0, 0) == (0x41, False)
    assert cea608_byte(0xC5, 1 << 2, 0) == (0x41, True)
    assert cea608_byte(0xC5, 1 << 10, 8) == (0x41, True)
    assert cea608_byte(0xC5, 1 << 2, 8) == (0x45, False)
    assert cea608_byte(0xC5, 1 << 1 | 1 << 2, 0) == (0x45, False)
    assert cea608_byte(0xC5, 1 << 2 | 1 << 7, 0) == (0x45, False)
    assert cea608_byte(0xC1, 1 << 2, 0) == (0x41, True)


def test_text_pair():
    # CEA-608-E 5.3
    assert decode_cea608_row((14, wire(0x48), wire(0x49), 0, ())) == (14, 'HI', False, 0x48, True, 0x49, True)


def test_control_pair():
    # CEA-608-E Table 52
    assert decode_cea608_row((14, wire(0x14), wire(0x2C), 0, ())) == \
        (14, 'CC1 Erase Displayed Memory', True, 0x14, True, 0x2C, True)


def test_control_pair_whose_second_byte_fails_parity_is_dropped():
    assert decode_cea608_row((14, wire(0x14), wire(Bad(0x2C)), 0, ())) is None


def test_text_pair_whose_second_byte_fails_parity():
    # CEA-608-E Table 50
    assert decode_cea608_row((14, wire(0x41), wire(Bad(0x42)), 0, ())) == \
        (14, 'A■', False, 0x41, True, 0x7F, False)


def test_first_byte_failing_parity():
    # CEA-608-E Table 50
    assert decode_cea608_row((14, wire(Bad(0x41)), wire(0x42), 0, ())) == \
        (14, '■B', False, 0x7F, False, 0x42, True)
    assert decode_cea608_row((14, wire(Bad(0x14)), wire(0x2C), 0, ())) is None


def test_start_bit_failure_row():
    assert decode_cea608_row((14, None, None, 0, ())) == \
        (14, '■■', False, 0x7F, False, 0x7F, False)


def test_decode_cea608_rows_with_and_without_a_filter():
    text = (14, wire(0x48), wire(0x49), 0, ())
    dropped = (15, wire(0x14), wire(Bad(0x2C)), 0, ())
    failed = (16, None, None, 0, ())
    expected = [(14, 'HI', False, 0x48, True, 0x49, True),
                (16, '■■', False, 0x7F, False, 0x7F, False)]
    assert decode_cea608_rows([text, dropped, failed]) == expected

    lines = Cea608Lines()
    noise = (14, wire(Bad(0x48)), wire(0x49), 0, ())
    assert decode_cea608_rows([noise], lines) == [(14, '■I', False, 0x7F, False, 0x49, True)]
    for _ in range(LineParityRate.MIN_FIELDS - 1):
        decode_cea608_rows([noise], lines)
    assert decode_cea608_rows([text, dropped, failed], lines) == expected[1:]


def test_line_parity_rate():
    # CEA-608-E 5.3
    rate = LineParityRate()
    for field in range(LineParityRate.MIN_FIELDS - 1):
        rate.update(14, wire(0x41), wire(Bad(0x42)) if field % 4 == 0 else wire(0x42))
    assert rate.rate(14) is None
    assert rate.seen(14) == LineParityRate.MIN_FIELDS - 1

    rate.update(14, wire(0x41), wire(0x42))
    assert rate.rate(14) == 15 / 20
    assert rate.rate(15) is None and rate.seen(15) == 0


def test_cea608_lines_drop_a_line_while_its_parity_fails_and_take_it_back():
    lines = Cea608Lines()
    bad = (14, wire(Bad(0x41)), wire(0x42), 0, ())
    good = (14, wire(0x41), wire(0x42), 0, ())
    for _ in range(LineParityRate.MIN_FIELDS - 1):
        lines.update(bad)
        assert lines.accepts(14)
    lines.update(bad)
    assert not lines.accepts(14)

    for _ in range(59):
        lines.update(good)
    assert not lines.accepts(14)
    lines.update(good)
    assert lines.accepts(14)

    for _ in range(3 * LineParityRate.MIN_FIELDS):
        lines.update((15, None, None, 0, ()))
    assert lines.accepts(15)


def test_line_service():
    captions = LineService()
    assert captions.service() == LINE_CAPTIONS
    for _ in range(40):
        captions.frame()
        captions.update(wire(0x41), wire(0x42))
    assert captions.service() == LINE_CAPTIONS

    rng = np.random.default_rng(608)
    guide = LineService()
    for _ in range(100):
        guide.frame()
        guide.update(*(int(value) for value in rng.integers(0, 256, 2)))
    assert guide.service() == LINE_STARSIGHT

    stray = LineService()
    for frame in range(200):
        stray.frame()
        if frame % 5 == 0:
            stray.update(*(int(value) for value in rng.integers(0, 256, 2)))
    assert stray.service() == LINE_NOTHING
