import os
import re
import tempfile
from html import unescape

import lib.cc_decode as cc_decode
from lib.cc_decode import (Cea608Lines, CaptionTrackFactory, HTMLCaptionTrack, SCCCaptionTrack, SRTCaptionTrack,
                           TextCaptionTrack, decode_captions_debug, decode_captions_raw, decode_cea608_row,
                           decode_cea608_rows, decode_to_html, decode_to_scc, decode_to_srt, decode_to_text,
                           scc_timecode)
from tests.test_cea608 import (wire, frames, Pipe, tracks, written, chars, packet, NULL, PBS, RTD1, RTD3, RCL, BS,
                               RU2, EDM, CR, ENM, EOC, ROW_15, Bad)

AOF = (0x14, 0x22)
AON = (0x14, 0x23)
DER = (0x14, 0x24)
RU3 = (0x14, 0x26)
RU4 = (0x14, 0x27)
FON = (0x14, 0x28)
RDC = (0x14, 0x29)
TR = (0x14, 0x2A)
TO1 = (0x17, 0x21)
TO2 = (0x17, 0x22)
TO3 = (0x17, 0x23)
ROW_14 = (0x14, 0x50)
ROW_15_INDENT_4 = (0x14, 0x72)
ROW_15_INDENT_8 = (0x14, 0x74)
ROW_15_GREEN = (0x14, 0x62)
ROW_14_ITALICS = (0x14, 0x4E)
ROW_15_INDENT_4_UNDERLINE = (0x14, 0x73)
MID_WHITE = (0x11, 0x20)
MID_RED = (0x11, 0x28)
MID_RED_UNDERLINE = (0x11, 0x29)
MID_ITALICS = (0x11, 0x2E)
MID_ITALICS_UNDERLINE = (0x11, 0x2F)
BACKGROUND_GREEN = (0x10, 0x22)
BACKGROUND_GREEN_SEMI = (0x10, 0x23)
BACKGROUND_BLACK = (0x10, 0x2E)
BACKGROUND_TRANSPARENT = (0x17, 0x2D)
FOREGROUND_BLACK = (0x17, 0x2E)
FOREGROUND_BLACK_UNDERLINE = (0x17, 0x2F)
SMALL_U_UMLAUT = (0x12, 0x25)
CAPITAL_U_UMLAUT = (0x12, 0x24)
MUSIC_NOTE = (0x11, 0x37)
RCL2 = (0x1C, 0x20)
RTD2 = (0x1C, 0x2B)
EDM2 = (0x1C, 0x2C)
CR2 = (0x1C, 0x2D)
EOC2 = (0x1C, 0x2F)
ROW_15_CHANNEL_2 = (0x1C, 0x70)
RCL3 = (0x15, 0x20)
EDM3 = (0x15, 0x2C)
CR3 = (0x15, 0x2D)
EOC3 = (0x15, 0x2F)
RCL4 = (0x1D, 0x20)
RTD4 = (0x1D, 0x2B)
CR4 = (0x1D, 0x2D)
EOC4 = (0x1D, 0x2F)
EDM4 = (0x1D, 0x2C)
NO_START_BITS = (14, None, None, 0, ())


class Hangup:
    def recv(self):
        raise EOFError


def files(track, lines):
    with tempfile.TemporaryDirectory() as folder:
        factory = CaptionTrackFactory(track, os.path.join(folder, 'out'), {'frame_rate': 30})
        for frame, rows in enumerate(frames(lines)):
            factory.add_data(rows, frame)
        factory.close_tracks()
        found = {}
        for name in os.listdir(folder):
            with open(os.path.join(folder, name)) as f:
                found[name[len('out.'):]] = f.read()
        return found


def cues(srt):
    return [cue[1:] for cue in re.findall(r'(\d+)\n(\S+) --> (\S+)\n(.*?)\n\n(?=\d+\n|\Z)', srt, re.S)]


def styled(html):
    body = html.split("<div id='captions' >", 1)[1]
    found = [(classes, text.replace('<br>', ''))
             for classes, text in re.findall(r"<pre class='([^']*)'>(.*?)</pre>", body, re.S)]
    return [(classes, unescape(text)) for classes, text in found if text]


def shown(html):
    body = html.split("<div id='captions' >", 1)[1]
    return unescape(re.sub(r'<[^>]*>', '', body.replace('<br>', '\n')))


def backgrounds(html):
    found = []
    for classes, text in styled(html):
        background = classes.split()[1]
        if found and found[-1][0] == background:
            found[-1] = (background, found[-1][1] + text)
        else:
            found.append((background, text))
    return found


def style_sheet(html):
    return html.split('<style>', 1)[1].split('</style>', 1)[0]


def raw(capsys, monkeypatch, sent, decoder=decode_captions_raw):
    monkeypatch.setattr(cc_decode, 'setproctitle', lambda name: None)
    returned = decoder(Pipe(sent), None, {})
    return capsys.readouterr().out.splitlines(), returned


def test_line_without_start_bits_is_two_solid_blocks():
    # CEA-608-E Table 50
    assert decode_cea608_row(NO_START_BITS) == (14, '■■', False, 0x7F, False, 0x7F, False)
    assert decode_cea608_rows([NO_START_BITS]) == [(14, '■■', False, 0x7F, False, 0x7F, False)]


def test_parity_failures(capsys):
    # CEA-608-E 5.3, Table 50
    assert decode_cea608_row((14, wire(0x14), wire(Bad(0x2D)), 0, ())) is None
    assert decode_cea608_row((14, wire(Bad(0x14)), wire(0x2D), 0, ())) is None
    assert decode_cea608_row((14, wire(0x41), wire(Bad(0x42)), 0, ())) == (14, 'A■', False, 0x41, True, 0x7F, False)
    line = [RTD1] + chars('AB') + [(0x14, Bad(0x2D))] + chars('CD') + [(Bad(0x14), 0x2D), CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'ABCD\n'


def test_one_flagged_bit_is_put_right_by_parity():
    # CEA-608-E 5.3
    assert decode_cea608_row((14, wire(0x41) ^ 0x02, wire(0x42), 0x0002, ()))[1] == 'AB'
    assert decode_cea608_row((14, wire(0x41), wire(0x42) ^ 0x10, 0x1000, ()))[1] == 'AB'
    assert decode_cea608_row((14, wire(0x14), wire(0x2D) ^ 0x01, 0x0100, ()))[1] == 'CC1 Carriage Return'
    assert decode_cea608_row((14, wire(0x41) ^ 0x02, wire(0x42), 0x0006, ()))[1] == '■B'
    assert decode_cea608_row((14, wire(0x41) ^ 0x02, wire(0x42), 0x0082, ()))[1] == '■B'


def test_a_damaged_copy_of_a_control_code_is_covered_by_the_other(capsys):
    # CEA-608-E D.2
    for damaged in ((0x14, Bad(0x2D)), (Bad(0x14), 0x2D)):
        line = [RTD1] + chars('AB') + [damaged, CR] + chars('CD') + [CR, damaged] + chars('EF') + [CR]
        assert written(capsys, TextCaptionTrack, {14: line}) == 'AB\nCD\nEF\n'


def test_line_failing_parity_is_dropped_until_it_recovers():
    lines = Cea608Lines()
    bad = (5, wire(Bad(0x41)), wire(0x42), 0, ())
    good = (5, wire(0x41), wire(0x42), 0, ())
    assert [len(decode_cea608_rows([bad], lines)) for _ in range(20)] == [1] * 19 + [0]
    assert decode_cea608_rows([(5, None, None, 0, ())], lines) == []
    assert [len(decode_cea608_rows([good], lines)) for _ in range(60)] == [0] * 59 + [1]
    assert decode_cea608_rows([bad], lines) == []
    assert decode_cea608_rows([bad]) == [(5, '■B', False, 0x7F, False, 0x42, True)]


def test_raw_output_groups_text_until_the_next_code(capsys, monkeypatch):
    line = [RCL, ROW_15] + chars('HELLO') + [EOC, NULL] + chars('HI') + [NULL]
    assert raw(capsys, monkeypatch, frames({14: line})) == ([
        '0 14 - [14, 20] - CC1 Resume Caption Loading',
        '1 14 - [14, 70] - CC1 Pre: Indent 0 row 15',
        '5 14 - [14, 2f] - Text:HELLO',
        '5 14 - [14, 2f] - CC1 End of Caption (flip memory)',
        '8 14 - [00, 00] - Text:HI',
    ], None)


def test_raw_output_after_parity_failures(capsys, monkeypatch):
    # CEA-608-E 5.3
    sent = frames({14: [RTD1, (Bad(0x41), 0x42)]}) + [[NO_START_BITS]] + \
        frames({14: [(0x14, Bad(0x2D)), (0x43, Bad(0x44)), CR]})
    assert raw(capsys, monkeypatch, sent) == ([
        '0 14 - [14, 2b] - CC1 Resume Text Display',
        '5 14 - [14, 2d] - Text:■B■■C■',
        '5 14 - [14, 2d] - CC1 Carriage Return',
    ], None)


def test_raw_output_reads_caption_lines_and_debug_shows_every_line(capsys, monkeypatch):
    sent = frames({5: [(Bad(0x41), 0x42)] * 18 + [EDM, (Bad(0x41), 0x42), CR]})
    assert raw(capsys, monkeypatch, sent) == ([
        '18 5 - [14, 2c] - Text:' + '■B' * 18,
        '18 5 - [14, 2c] - CC1 Erase Displayed Memory',
    ], None)
    shown_lines, codes = raw(capsys, monkeypatch, sent, decode_captions_debug)
    assert shown_lines[-1] == '20 5 - bytes: 0x14 0x2d - parity: T T: CC1 Carriage Return'
    assert len(shown_lines) == len(codes) == 21


def test_debug_output_shows_bytes_and_parity(capsys, monkeypatch):
    # CEA-608-E 5.3
    sent = frames({14: [RTD1, (Bad(0x41), 0x42), (0x14, Bad(0x2D)), NULL]}) + [[NO_START_BITS]]
    assert raw(capsys, monkeypatch, sent, decode_captions_debug) == ([
        '0 14 - bytes: 0x14 0x2b - parity: T T: CC1 Resume Text Display',
        '1 14 - bytes: 0x7f 0x42 - parity: F T: ■B',
        '3 14 - bytes: 0x00 0x00 - parity: T T: ',
        '4 14 - bytes: 0x7f 0x7f - parity: F F: ■■',
    ], [[0x14, 0x2B], [0x7F, 0x42], [0x00, 0x00], [0x7F, 0x7F]])


def test_raw_and_debug_write_their_files(capsys, monkeypatch):
    monkeypatch.setattr(cc_decode, 'setproctitle', lambda name: None)
    with tempfile.TemporaryDirectory() as folder:
        base = os.path.join(folder, 'out')
        decode_captions_raw(Pipe(frames({14: [RTD1]})), base, {})
        assert decode_captions_debug(Pipe(frames({14: [RTD1]})), base, {}) == [[0x14, 0x2B]]
        with open(base + '.captions.raw') as f:
            assert f.read() == '0 14 - [14, 2b] - CC1 Resume Text Display\n'
        with open(base + '.captions.debug') as f:
            assert f.read() == '0 14 - bytes: 0x14 0x2b - parity: T T: CC1 Resume Text Display\n'
    decode_captions_raw(Hangup(), None, {})
    assert decode_captions_debug(Hangup(), None, {}) == []
    assert capsys.readouterr().out == ''


def test_scc_timecode_is_drop_frame():
    assert scc_timecode(0) == '00:00:00;00'
    assert scc_timecode(1799) == '00:00:59;29'
    assert scc_timecode(1800) == '00:01:00;02'
    assert scc_timecode(17981) == '00:09:59;29'
    assert scc_timecode(17982) == '00:10:00;00'
    assert scc_timecode(17983) == '00:10:00;01'
    assert scc_timecode(19782) == '00:11:00;02'
    assert scc_timecode(107892) == '01:00:00;00'
    assert scc_timecode(107892 * 24) == '00:00:00;00'


def test_scc_pop_on_caption_is_written_when_it_is_displayed():
    # CEA-608-E B.8.3, D.2
    line = [RCL, RCL, ROW_15, ROW_15] + chars('u') + [SMALL_U_UMLAUT, SMALL_U_UMLAUT, NULL] + \
        [EOC, EOC, EDM, EDM, ENM, ENM]
    assert files(SCCCaptionTrack, {14: line}) == {
        'CC1.scc': 'Scenarist_SCC V1.0\n\n'
                   '00:00:00;08\t9420 9470 9470 7580 9225 9225 942f \n'
                   '00:00:00;10\t942c \n',
    }


def test_scc_header_and_words_in_caption_and_text_files():
    line = [RCL, ROW_15] + chars('AB') + [EOC, RTD1, (0x01, 0x00)] + chars('T') + [CR]
    written_files = files(SCCCaptionTrack, {14: line})
    assert written_files == {
        'CC1.scc': 'Scenarist_SCC V1.0\n\n00:00:00;03\t9420 9470 c1c2 942f \n',
        'T1.scc': 'Scenarist_SCC V1.0\n\n00:00:00;07\t94ab 0180 5480 94ad \n',
    }
    for scc in written_files.values():
        for entry in scc.splitlines()[2:]:
            timecode, words = entry.split('\t')
            assert re.fullmatch(r'\d\d:\d\d:\d\d;\d\d', timecode)
            assert re.fullmatch(r'([0-9a-f]{4} )+', words)


def test_scc_roll_up_writes_each_pair():
    # CEA-608-E C.10
    line = [RU2, RU2, ROW_15] + chars('AB') + [CR, CR, RU3] + chars('C') + [EDM]
    assert files(SCCCaptionTrack, {14: line})['CC1.scc'] == (
        'Scenarist_SCC V1.0\n\n'
        '00:00:00;00\t9425 \n'
        '00:00:00;00\t9425 \n'
        '00:00:00;02\t9470 \n'
        '00:00:00;03\tc1c2 \n'
        '00:00:00;04\t94ad \n'
        '00:00:00;05\t94ad \n'
        '00:00:00;06\t9426 \n'
        '00:00:00;07\t4380 \n'
        '00:00:00;08\t942c \n'
    )


def test_scc_paint_on_rewrites_displayed_memory():
    # CEA-608-E B.8.2
    line = [RDC, RDC, ROW_15] + chars('AB') + [EDM]
    assert files(SCCCaptionTrack, {14: line})['CC1.scc'] == (
        'Scenarist_SCC V1.0\n\n'
        '00:00:00;00\t9429 \n'
        '00:00:00;02\t9429 9470 \n'
        '00:00:00;03\t9429 9470 c1c2 \n'
        '00:00:00;04\t942c \n'
    )


def test_scc_text_restart_drops_the_unfinished_row():
    # CEA-608-E 7.4
    line = [RTD1, RTD1] + chars('AB') + [TR, TR] + chars('CD') + [CR, CR]
    assert files(SCCCaptionTrack, {14: line}) == {
        'T1.scc': 'Scenarist_SCC V1.0\n\n00:00:00;06\t43c4 94ad \n00:00:00;07\t94ad \n',
    }


def test_reserved_codes_and_delete_to_end_of_row_change_nothing(capsys):
    # CEA-608-E Table 52, B.3
    line = [RTD1] + chars('AB') + [AOF, AON, DER] + chars('CD') + [CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'ABCD\n'


def test_flash_on_and_mid_row_codes_take_a_column(capsys):
    # CEA-608-E 7.4, C.8
    line = [RTD1] + chars('AB') + [FON] + chars('CD') + [MID_ITALICS] + chars('EF') + [CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'AB CD EF\n'


def test_backspace_erases_a_character_or_a_mid_row_code(capsys):
    # CEA-608-E 7.4
    line = [RTD1] + chars('ABC') + [BS] + chars('D') + [MID_RED, BS] + chars('E') + [CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'ABDE\n'


def test_attribute_codes_use_the_space_sent_before_them(capsys):
    # CEA-608-E 6.2
    line = [RTD1] + chars('A ') + [BACKGROUND_GREEN] + chars('B ') + [FOREGROUND_BLACK] + chars('C ') + \
        [BACKGROUND_TRANSPARENT] + chars('D') + [CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'A B C D\n'


def test_extended_character_first_on_a_row_replaces_nothing(capsys):
    # CEA-608-E 6.4.2
    line = [RTD1, CAPITAL_U_UMLAUT] + chars('BER') + [CR, CAPITAL_U_UMLAUT, CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == '\u00dcBER\n\u00dc\n'


def test_doubled_extended_character_replaces_one_character(capsys):
    # CEA-608-E 6.4.2, D.2
    line = [RTD1] + chars('Gru') + [SMALL_U_UMLAUT, SMALL_U_UMLAUT] + chars('n') + [CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'Gr\u00fcn\n'


def test_tab_offset_stops_at_column_32(capsys):
    # CEA-608-E C.13
    line = [RTD1] + chars('A' * 29) + [TO3] + chars('X') + [CR] + \
        chars('A' * 30) + [TO3] + chars('X') + [CR] + \
        chars('A' * 31) + [TO1] + chars('X') + [CR] + \
        chars('A' * 32) + [TO2] + chars('X') + [CR] + \
        chars('A') + [TO1] + chars('B') + [TO2] + chars('C') + [TO3] + chars('D') + [CR]
    assert written(capsys, TextCaptionTrack, {14: line}).splitlines() == [
        'A' * 29 + '  X',
        'A' * 30 + ' X',
        'A' * 31 + 'X',
        'A' * 32 + 'X',
        'A B  C   D',
    ]


def test_characters_past_column_32_are_kept(capsys):
    line = [RTD1] + chars('0123456789' * 3 + 'ABCD') + [CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == '0123456789' * 3 + 'ABCD\n'


def test_text_restart_erases_the_unfinished_row(capsys):
    # CEA-608-E 7.4
    line = [RTD1] + chars('AB') + [CR] + chars('CD') + [TR] + chars('EF') + [CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'AB\nEF\n'


def test_text_restart_starts_text_mode_on_its_own(capsys):
    # CEA-608-E 7.4, B.11.4
    line = [RCL, ROW_15] + chars('CAP') + [TR] + chars('TX') + [CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'TX\n'


def test_text_restart_forgets_the_resent_row(capsys):
    # CEA-608-E 7.4
    line = [RTD1] + chars('ABCDEF') + [ROW_15, TR, TO2] + chars('X') + [CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == '  X\n'


def test_resume_text_display_continues_the_row(capsys):
    # CEA-608-E 7.4, C.15
    line = [RTD1] + chars('AB') + [RCL, ROW_15] + chars('CAP') + [RTD1] + chars('CD') + [CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'ABCD\n'


def test_erase_commands_do_not_end_text_mode(capsys):
    # CEA-608-E 7.7, C.16
    line = [RTD1] + chars('AB') + [EDM, MID_ITALICS] + chars('CD') + [ENM, BS] + chars('E') + [CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'AB CE\n'


def test_caption_commands_end_text_mode(capsys):
    # CEA-608-E 7.7
    line = []
    for text, command in zip('ABCDEF', (RCL, RDC, RU2, RU3, RU4, EOC)):
        line += [RTD1] + chars(text) + [command] + chars('X')
    assert written(capsys, TextCaptionTrack, {14: line + [RTD1, CR]}) == 'ABCDEF\n'


def test_other_data_channel_does_not_end_text_mode(capsys):
    # CEA-608-E 7.7
    line = [RTD1] + chars('AB') + [RCL2, ROW_15_CHANNEL_2] + chars('XY') + [RTD1] + chars('CD') + [CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'ABCD\n'


def test_text_row_sent_again_after_a_pac(capsys):
    # CEA-608-E 7.4, D.3
    line = [RTD1, ROW_15] + chars('HEL') + [ROW_15] + chars('HELLO') + [CR] + \
        [ROW_15_INDENT_4] + chars('AB') + [ROW_15_INDENT_4] + chars('ABCD') + [CR] + \
        [ROW_15] + chars('AB') + [ROW_15, TO2] + chars('AB') + [CR] + \
        [ROW_15] + chars('AB') + [ROW_15, MID_ITALICS] + chars('CD') + [CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'HELLO\n    ABCD\n  AB\n CD\n'


def test_text_mode_ignores_pac_rows_after_a_carriage_return(capsys):
    # CEA-608-E 7.4
    line = [RTD1, (0x11, 0x50)] + chars('AB') + [CR, (0x11, 0x70)] + chars('CD') + [CR, ROW_14] + chars('EF') + [CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'AB\nCD\nEF\n'


def test_control_codes_separated_by_a_null_both_act(capsys):
    # CEA-608-E D.2
    line = [RTD1] + chars('AB') + [CR, NULL, CR] + chars('C') + [MUSIC_NOTE, NULL, MUSIC_NOTE, CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'AB\n\nC\u266a\u266a\n'


def test_repeated_characters_are_not_redundant(capsys):
    # CEA-608-E D.2
    line = [RTD1] + chars('ABABAB') + [CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'ABABAB\n'


def test_solid_block_runs_collapse(capsys):
    line = [RTD1, (Bad(0x41), 0x42), (0x43, Bad(0x44)), (Bad(0x41), Bad(0x42)), (0x45, 0x46), CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == '■BC■EF\n'
    caption = [RCL, ROW_15, (Bad(0x41), Bad(0x42)), (Bad(0x41), Bad(0x42)), (0x41, Bad(0x42)), EOC, EDM]
    assert cues(files(SRTCaptionTrack, {14: caption})['CC1.srt']) == [('00:00:00,167', '00:00:00,200', '■A■')]
    assert shown(files(HTMLCaptionTrack, {14: caption})['CC1.html']) == '■A■\n'


def test_unfinished_text_row_is_written_at_the_end(capsys):
    line = [RTD1] + chars('AB') + [CR] + chars('CD')
    assert written(capsys, TextCaptionTrack, {14: line}) == 'AB\nCD'


def test_text_file_leaves_captions_out(capsys):
    line = [RCL, ROW_15] + chars('POP') + [EOC, RU2, ROW_15] + chars('ROLL') + [CR, RDC] + chars('PAINT') + [EDM]
    assert written(capsys, TextCaptionTrack, {14: line}) == ''


def test_field_one_bytes_below_0x10_are_not_text(capsys):
    # CEA-608-E 7.11, B.11.5
    line = [RTD1, (0x01, 0x41), (0x0F, 0x00)] + chars('BC') + [CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'ABC\n'


def test_srt_pop_on_cues_are_numbered_and_timed():
    # CEA-608-E B.8.3
    line = [RCL, ROW_15] + chars('AB') + [EOC, NULL, NULL, EDM, RCL, ROW_15] + chars('CD') + [EOC, EDM]
    assert files(SRTCaptionTrack, {14: line}) == {
        'CC1.srt': '1\n00:00:00,100 --> 00:00:00,200\nAB\n\n'
                   '2\n00:00:00,333 --> 00:00:00,367\nCD\n\n',
    }


def test_doubled_pop_on_commands_act_once():
    # CEA-608-E D.2, C.12
    line = [RCL, RCL, ROW_15, ROW_15] + chars('AB') + [EOC, EOC, NULL, EDM, EDM]
    assert cues(files(SRTCaptionTrack, {14: line})['CC1.srt']) == [('00:00:00,167', '00:00:00,267', 'AB')]
    # CEA-608-E B.14
    line = [RCL, ROW_15] + chars('AB') + [EOC, EOC, EOC, EDM]
    assert cues(files(SRTCaptionTrack, {14: line})['CC1.srt']) == [('00:00:00,100', '00:00:00,167', 'AB')]


def test_end_of_caption_with_and_without_erase_displayed_memory():
    # CEA-608-E B.8.3
    replaced = [RCL, ROW_15] + chars('ONE') + [EOC, RCL, ROW_15] + chars('TWO') + [EOC, NULL, EDM]
    assert cues(files(SRTCaptionTrack, {14: replaced})['CC1.srt']) == [
        ('00:00:00,133', '00:00:00,300', 'ONE'),
        ('00:00:00,300', '00:00:00,367', 'TWO'),
    ]
    blinked = [RCL, ROW_15] + chars('ONE') + [EOC, RCL, ROW_15] + chars('TWO') + [EDM, EOC, NULL, EDM]
    assert cues(files(SRTCaptionTrack, {14: blinked})['CC1.srt']) == [
        ('00:00:00,133', '00:00:00,300', 'ONE'),
        ('00:00:00,333', '00:00:00,400', 'TWO'),
    ]


def test_end_of_caption_flips_the_memories_back():
    # CEA-608-E C.10, B.8.3
    line = [RCL, ROW_15] + chars('ONE') + [EOC, RCL, ROW_15] + chars('TWO') + [EOC, NULL, EOC, NULL, EDM]
    assert cues(files(SRTCaptionTrack, {14: line})['CC1.srt']) == [
        ('00:00:00,133', '00:00:00,300', 'ONE'),
        ('00:00:00,300', '00:00:00,367', 'TWO'),
        ('00:00:00,367', '00:00:00,433', 'ONE'),
    ]


def test_erase_non_displayed_memory_clears_the_caption_being_loaded():
    # CEA-608-E C.16, B.8.3
    line = [RCL, ROW_15] + chars('AA') + [ENM, ROW_15] + chars('BB') + [EOC, EDM]
    assert cues(files(SRTCaptionTrack, {14: line})['CC1.srt']) == [('00:00:00,200', '00:00:00,233', 'BB')]


def test_pop_on_rows_indents_and_backspace():
    # CEA-608-E C.7, B.12, 6.4.2
    line = [RCL, ROW_14] + chars('AB') + [ROW_15, BS, CAPITAL_U_UMLAUT] + chars('C') + [EOC, EDM] + \
        [RCL, ROW_15_INDENT_8] + chars('AB') + [EOC, EDM] + \
        [RCL, ROW_15] + chars('AB') + [ROW_15_INDENT_8] + chars('CDE') + [BS, EOC, EDM]
    assert [cue[2] for cue in cues(files(SRTCaptionTrack, {14: line})['CC1.srt'])] == [
        'AB\n\u00dcC',
        '        AB',
        'AB      CD',
    ]


def test_pop_on_mid_row_and_misc_codes():
    # CEA-608-E D.2, C.8, Table 52
    line = [RCL, ROW_15] + chars('AB') + [MID_ITALICS, MID_ITALICS] + chars('CD') + [FON, AOF, AON, DER] + \
        chars('EF') + [EOC, EDM]
    assert [cue[2] for cue in cues(files(SRTCaptionTrack, {14: line})['CC1.srt'])] == ['AB CD EF']


def test_carriage_return_in_pop_on_and_paint_on_breaks_the_line():
    line = [RCL, ROW_15] + chars('AB') + [CR] + chars('CD') + [EOC, EDM, RDC, ROW_15] + chars('EF') + [CR] + \
        chars('GH') + [EDM]
    assert [cue[2] for cue in cues(files(SRTCaptionTrack, {14: line})['CC1.srt'])] == [
        'AB\nCD', '', 'EF', 'EF', 'EF\nGH', 'EF\nGH',
    ]


def test_roll_up_rows_in_the_recommended_order():
    # CEA-608-E B.8.1, D.2
    line = []
    for text in ('AA', 'BB', 'CC'):
        line += [RU2, RU2, CR, CR, ROW_15, ROW_15] + chars(text)
    assert cues(files(SRTCaptionTrack, {14: line + [EDM, EDM]})['CC1.srt']) == [
        ('00:00:00,067', '00:00:00,300', 'AA'),
        ('00:00:00,300', '00:00:00,533', 'AA\nBB'),
        ('00:00:00,533', '00:00:00,700', 'BB\nCC'),
    ]


def test_roll_up_three_and_four_rows():
    # CEA-608-E B.8.1
    line = [RU3, EDM, ROW_15]
    for text in ('AA', 'BB', 'CC', 'DD'):
        line += chars(text) + [CR]
    assert [cue[2] for cue in cues(files(SRTCaptionTrack, {14: line + [EDM]})['CC1.srt'])] == [
        'AA', 'AA\nBB', 'AA\nBB\nCC', 'BB\nCC\nDD', 'CC\nDD',
    ]
    line = [RU4, ROW_15]
    for text in ('AA', 'BB', 'CC', 'DD', 'EE'):
        line += chars(text) + [CR]
    assert [cue[2] for cue in cues(files(SRTCaptionTrack, {14: line + [EDM]})['CC1.srt'])] == [
        'AA', 'AA\nBB', 'AA\nBB\nCC', 'AA\nBB\nCC\nDD', 'BB\nCC\nDD\nEE', 'CC\nDD\nEE',
    ]


def test_roll_up_depth_change_keeps_the_rows():
    # CEA-608-E C.10
    line = [RU2, ROW_15] + chars('AA') + [CR] + chars('BB') + [RU3, CR] + chars('CC') + [CR, EDM]
    assert cues(files(SRTCaptionTrack, {14: line})['CC1.srt']) == [
        ('00:00:00,000', '00:00:00,100', 'AA'),
        ('00:00:00,100', '00:00:00,200', 'AA\nBB'),
        ('00:00:00,200', '00:00:00,267', 'AA\nBB\nCC'),
        ('00:00:00,267', '00:00:00,300', 'BB\nCC'),
    ]


def test_roll_up_erases_pop_on_and_paint_on_captions():
    # CEA-608-E C.10
    shown_then_rolled = [RCL, ROW_15] + chars('AA') + [EOC, RU2, ROW_15] + chars('BB') + [CR, EDM]
    assert cues(files(SRTCaptionTrack, {14: shown_then_rolled})['CC1.srt']) == [
        ('00:00:00,100', '00:00:00,133', 'AA'),
        ('00:00:00,133', '00:00:00,233', 'BB'),
        ('00:00:00,233', '00:00:00,267', 'BB'),
    ]
    loaded_then_rolled = [RCL, ROW_15] + chars('AA') + [RU2, RCL, EOC, EDM]
    assert files(SRTCaptionTrack, {14: loaded_then_rolled}) == {}
    painted_then_rolled = [RDC, ROW_15] + chars('AB') + [RU2, ROW_15] + chars('CD') + [CR, EDM]
    assert cues(files(SRTCaptionTrack, {14: painted_then_rolled})['CC1.srt'])[2:] == [
        ('00:00:00,000', '00:00:00,100', 'AB'),
        ('00:00:00,100', '00:00:00,200', 'CD'),
        ('00:00:00,200', '00:00:00,233', 'CD'),
    ]


def test_roll_up_caption_stays_up_after_pop_on_or_paint_on_is_selected():
    # CEA-608-E C.10, B.7
    line = [RU2, ROW_15] + chars('AA') + [CR] + chars('BB') + [RCL, ROW_15] + chars('CC') + [EOC, EDM]
    assert cues(files(SRTCaptionTrack, {14: line})['CC1.srt']) == [
        ('00:00:00,000', '00:00:00,100', 'AA'),
        ('00:00:00,100', '00:00:00,267', 'AA\nBB'),
        ('00:00:00,267', '00:00:00,300', 'CC'),
    ]
    line = [RU2, ROW_15] + chars('AA') + [RDC] + chars('BB') + [EDM]
    assert cues(files(SRTCaptionTrack, {14: line})['CC1.srt']) == [
        ('00:00:00,000', '00:00:00,133', 'AABB'),
        ('00:00:00,000', '00:00:00,167', 'AABB'),
    ]


def test_end_of_caption_swaps_a_roll_up_caption():
    # CEA-608-E C.11
    line = [RU2, ROW_15] + chars('AA') + [EOC, NULL, EOC, EDM]
    assert cues(files(SRTCaptionTrack, {14: line})['CC1.srt']) == [
        ('00:00:00,000', '00:00:00,100', 'AA'),
        ('00:00:00,167', '00:00:00,200', 'AA'),
    ]


def test_roll_up_misc_codes():
    # CEA-608-E 6.4.2, 7.4, C.16, Table 52
    line = [RU2, ROW_15] + chars('ABCX') + [BS] + chars('u') + [SMALL_U_UMLAUT, DER, ENM, AOF, AON, FON] + \
        chars('D') + [CR, EDM]
    assert [cue[2] for cue in cues(files(SRTCaptionTrack, {14: line})['CC1.srt'])] == ['ABC\u00fc D', 'ABC\u00fc D']


def test_paint_on_writes_a_cue_per_pair():
    # CEA-608-E B.8.2
    line = [RDC, ROW_15] + chars('AB') + [BS, ENM, DER] + chars('C') + [EOC, NULL, EDM]
    assert cues(files(SRTCaptionTrack, {14: line})['CC1.srt']) == [
        ('00:00:00,000', '00:00:00,033', ''),
        ('00:00:00,000', '00:00:00,067', 'AB'),
        ('00:00:00,000', '00:00:00,100', 'A'),
        ('00:00:00,000', '00:00:00,167', 'A'),
        ('00:00:00,000', '00:00:00,200', 'AC'),
        ('00:00:00,000', '00:00:00,233', 'AC'),
    ]


def test_paint_on_stays_up_after_pop_on_is_selected():
    # CEA-608-E C.10
    line = [RDC, ROW_15] + chars('AB') + [RCL, ROW_15] + chars('CD') + [EOC, EDM]
    assert cues(files(SRTCaptionTrack, {14: line})['CC1.srt'])[2:] == [
        ('00:00:00,000', '00:00:00,200', 'AB'),
        ('00:00:00,200', '00:00:00,233', 'CD'),
    ]


def test_doubled_extended_character_paints_once():
    # CEA-608-E D.2, 6.4.2
    line = [RDC, ROW_15] + chars('u') + [SMALL_U_UMLAUT, SMALL_U_UMLAUT, EDM]
    assert [cue[2] for cue in cues(files(SRTCaptionTrack, {14: line})['CC1.srt'])] == ['', 'u', '\u00fc', '\u00fc']


def test_text_cues_run_from_row_to_row():
    # CEA-608-E 7.4
    line = [RTD1] + chars('AB') + [CR] + chars('  ') + [CR] + chars('CD') + [CR] + chars('EF') + [NULL, NULL]
    assert files(SRTCaptionTrack, {14: line}) == {
        'T1.srt': '1\n00:00:00,000 --> 00:00:00,067\nAB\n\n'
                  '2\n00:00:00,067 --> 00:00:00,200\nCD\n\n'
                  '3\n00:00:00,200 --> 00:00:00,233\nEF\n\n',
    }


def test_text_restart_cue():
    # CEA-608-E 7.4
    line = [RTD1] + chars('AB') + [CR] + chars('CD') + [TR] + chars('EF') + [CR]
    assert cues(files(SRTCaptionTrack, {14: line})['T1.srt']) == [
        ('00:00:00,000', '00:00:00,067', 'AB'),
        ('00:00:00,067', '00:00:00,200', 'EF'),
    ]


def test_erase_displayed_memory_during_text_ends_the_caption():
    # CEA-608-E C.16, 7.7
    line = [RCL, ROW_15] + chars('AB') + [EOC, RTD1] + chars('TX') + [EDM] + chars('T') + [CR]
    assert {name: cues(srt) for name, srt in files(SRTCaptionTrack, {14: line}).items()} == {
        'CC1.srt': [('00:00:00,100', '00:00:00,200', 'AB')],
        'T1.srt': [('00:00:00,133', '00:00:00,267', 'TXT')],
    }


def test_end_of_caption_during_text_shows_the_loaded_caption():
    # CEA-608-E 7.7, B.11.4
    line = [RCL, ROW_15] + chars('CAP') + [RTD1] + chars('TX') + [CR, EOC, NULL, EDM, RCL, ROW_15] + chars('AB') + \
        [TR] + chars('T') + [CR, EOC, EDM]
    assert {name: cues(srt) for name, srt in files(SRTCaptionTrack, {14: line}).items()} == {
        'CC1.srt': [('00:00:00,233', '00:00:00,300', 'CAP'), ('00:00:00,533', '00:00:00,567', 'AB')],
        'T1.srt': [('00:00:00,133', '00:00:00,200', 'TX'), ('00:00:00,200', '00:00:00,500', 'T')],
    }


def test_srt_timecodes():
    track = SRTCaptionTrack('CC1', None, {'frame_rate': 30})
    assert track._get_timecode(1) == '00:00:00,033'
    assert track._get_timecode(45) == '00:00:01,500'
    assert track._get_timecode(1830) == '00:01:01,000'
    assert track._get_timecode(108001) == '01:00:00,033'


def test_html_page_around_captions_and_text():
    line = [RCL, ROW_15] + chars('AB') + [EOC, RTD1] + chars('T') + [CR, EDM]
    pages = files(HTMLCaptionTrack, {14: line})
    assert sorted(pages) == ['CC1.html', 'T1.html']
    assert pages['CC1.html'].startswith("<!DOCTYPE html><html><head><meta charset='UTF-8'>")
    assert '<title>CC1 Closed Captions</title>' in pages['CC1.html']
    assert 'Text Mode</title>' in pages['T1.html']
    for page in pages.values():
        assert page.endswith("</pre><!--\n--></div></body></html>")
    assert shown(pages['CC1.html']) == 'AB\n'
    assert shown(pages['T1.html']) == 'T\n'


def test_html_colours_underline_italics_and_flash():
    # CEA-608-E 6.2, C.7
    line = [RCL, ROW_15_GREEN] + chars('G') + [MID_RED_UNDERLINE] + chars('R') + [MID_ITALICS] + chars('I') + \
        [FON] + chars('F') + [MID_ITALICS_UNDERLINE] + chars('U ') + [FOREGROUND_BLACK] + chars('K ') + \
        [FOREGROUND_BLACK_UNDERLINE] + chars('L') + [MID_WHITE] + chars('W') + [EOC, EDM] + \
        [RCL, ROW_14_ITALICS] + chars('O') + [ROW_15_INDENT_4_UNDERLINE, TO1] + chars('N') + [EOC, EDM]
    assert styled(files(HTMLCaptionTrack, {14: line})['CC1.html']) == [
        ('caption-font-normal background-black text-green', 'G'),
        ('caption-font-normal background-black', ' '),
        ('caption-font-normal background-black text-red underline', 'R'),
        ('caption-font-normal background-black', ' '),
        ('caption-font-normal background-black text-red italics', 'I'),
        ('caption-font-normal background-black', ' '),
        ('caption-font-normal background-black text-red italics flashing', 'F'),
        ('caption-font-normal background-black', ' '),
        ('caption-font-normal background-black text-red italics underline', 'U'),
        ('caption-font-normal background-black text-black', ' K'),
        ('caption-font-normal background-black text-black underline', ' L'),
        ('caption-font-normal background-black', ' '),
        ('caption-font-normal background-black text-white', 'W'),
        ('caption-font-normal background-black text-white italics', 'O'),
        ('caption-font-normal background-black text-white underline', '    '),
        ('caption-font-normal background-black text-white underline', ' N'),
    ]


def test_html_background_classes_are_in_the_style_sheet():
    # CEA-608-E 6.2, Table 3
    line = [RCL, ROW_15]
    for second in range(0x20, 0x30):
        line += chars('A ') + [(0x10, second)]
    line += chars('A ') + [BACKGROUND_TRANSPARENT] + chars('A') + [EOC, EDM]
    page = files(HTMLCaptionTrack, {14: line})['CC1.html']
    used = [background for background, text in backgrounds(page)]
    assert used == ['background-black'] + ['background-%s%s' % (colour, opacity)
                                           for colour in ('white', 'green', 'blue', 'cyan', 'red', 'yellow',
                                                          'magenta', 'black')
                                           for opacity in ('', '-semi-transparent')] + ['background-transparent']
    for classes, _ in styled(page):
        for name in classes.split():
            assert '.%s {' % name in style_sheet(page)


def test_html_background_colours_the_characters_after_it():
    # CEA-608-E 6.2
    line = [RCL, ROW_15] + chars('A ') + [BACKGROUND_GREEN_SEMI] + chars('B ') + [BACKGROUND_TRANSPARENT] + \
        chars('C ') + [BACKGROUND_BLACK] + chars('D') + [EOC, EDM]
    page = files(HTMLCaptionTrack, {14: line})['CC1.html']
    assert [(background, text.strip()) for background, text in backgrounds(page)] == [
        ('background-black', 'A'),
        ('background-green-semi-transparent', 'B'),
        ('background-transparent', 'C'),
        ('background-black', 'D'),
    ]
    assert shown(page) == 'A B C D\n'


def test_html_backspace_steps_over_markup():
    # CEA-608-E 7.4, B.12
    line = [RTD1] + chars('A&') + [BS] + chars('<') + [MID_RED, BS] + chars('B') + [CR, BS] + chars('"C') + [CR]
    page = files(HTMLCaptionTrack, {14: line})['T1.html']
    assert shown(page) == 'A<B\n"C\n'
    assert '&lt;' in page and '&quot;' in page


def test_html_roll_up_is_written_as_a_transcript():
    # CEA-608-E 7.4
    line = [RU2, EDM, ROW_15] + chars('AA') + [CR] + chars('BB') + [CR] + chars('CC') + [EDM, RU2, ROW_15] + \
        chars('DD') + [EOC, ENM, RCL, ROW_15] + chars('EE') + [EOC, EDM]
    assert shown(files(HTMLCaptionTrack, {14: line})['CC1.html']) == 'AA\nBB\nCC\nDD\nEE\n'


def test_html_paint_on_writes_the_display_per_pair():
    # CEA-608-E B.8.2
    line = [EDM, RDC, ROW_15] + chars('AB') + [EDM]
    assert shown(files(HTMLCaptionTrack, {14: line})['CC1.html']) == '\nAB\nAB\n'


def test_channels_are_data_channel_and_field():
    # CEA-608-E 8.4, C.18
    field_one = [RCL2, ROW_15_CHANNEL_2] + chars('C2') + [EOC2, RTD2] + chars('T2') + [CR2, EDM2]
    field_two = [RCL3, ROW_15] + chars('C3') + [EOC3, RTD3] + chars('T3') + [CR3, EDM3]
    field_two_channel_two = [RCL4, ROW_15_CHANNEL_2] + chars('C4') + [EOC4, RTD4] + chars('T4') + [CR4, EDM4]
    assert {name: cues(srt) for name, srt in files(SRTCaptionTrack, {
        14: field_one + [NULL] * len(field_two_channel_two),
        15: field_two + field_two_channel_two,
    }).items()} == {
        'CC2.srt': [('00:00:00,100', '00:00:00,233', 'C2')],
        'T2.srt': [('00:00:00,133', '00:00:00,200', 'T2')],
        'CC3.srt': [('00:00:00,100', '00:00:00,233', 'C3')],
        'T3.srt': [('00:00:00,133', '00:00:00,200', 'T3')],
        'CC4.srt': [('00:00:00,367', '00:00:00,500', 'C4')],
        'T4.srt': [('00:00:00,400', '00:00:00,467', 'T4')],
    }


def test_characters_before_any_control_code_have_no_channel():
    assert tracks({14: chars('AB') + [RTD1] + chars('CD') + [CR]}) == {
        'CC1': ['CC1 Resume Text Display', 'CD', 'CC1 Carriage Return'],
    }


def test_field_follows_the_miscellaneous_codes_a_line_sends():
    # CEA-608-E 8.4
    line = [RCL, RCL3, RCL3, (0x15, 0x70)] + chars('AB') + [RCL, CR, CR3]
    assert tracks({14: line}) == {
        'CC1': ['CC1 Resume Caption Loading', 'CC1 Resume Caption Loading', 'CC1 Carriage Return'],
        'CC3': ['CC3 Resume Caption Loading', 'CC1 Pre: Indent 0 row 6', 'AB'],
    }


def test_one_stray_field_one_code_does_not_move_a_field_two_line():
    # CEA-608-E 8.4
    line = [RTD3, RTD3] + chars('AB') + [CR, CR3] + chars('CD') + [CR3]
    assert tracks({15: line}) == {
        'CC3': ['CC3 Resume Text Display', 'CC3 Resume Text Display', 'AB', 'CC3 Carriage Return', 'CD',
                'CC3 Carriage Return'],
    }


def test_data_channels_share_a_field():
    # CEA-608-E C.15
    line = [RCL, ROW_15] + chars('AB') + [RCL2, ROW_15_CHANNEL_2] + chars('XY') + [RCL] + chars('CD') + \
        [EOC, EOC2, NULL, EDM, EDM2]
    assert {name: cues(srt) for name, srt in files(SRTCaptionTrack, {14: line}).items()} == {
        'CC1.srt': [('00:00:00,267', '00:00:00,367', 'ABCD')],
        'CC2.srt': [('00:00:00,300', '00:00:00,400', 'XY')],
    }


def test_xds_holds_the_line_until_a_caption_control_code():
    # CEA-608-E 8.6.2
    call_sign = packet((0x05, 0x02), (0x4B, 0x43), (0x45, 0x54))
    line = [RTD3] + chars('AB') + call_sign + chars('XY') + [RTD3] + chars('CD') + [CR3]
    assert tracks({15: line}) == {
        'CC3': ['CC3 Resume Text Display', 'AB', 'CC3 Resume Text Display', 'CD', 'CC3 Carriage Return'],
    }


def test_resume_text_display_after_xds_continues_the_row(capsys):
    # CEA-608-E 7.4, 7.7
    line = [RTD3] + chars('AB') + PBS + chars('XY') + [RTD3] + chars('CD') + [CR3]
    assert written(capsys, TextCaptionTrack, {15: line}) == 'ABCD\n'


def test_xds_leaves_field_two_captions_and_field_one_text_alone():
    # CEA-608-E 7.7, 8.4
    field_one = [RTD1] + chars('ABCDEF') + [CR]
    field_two = [RCL3, ROW_15] + PBS + [RCL3] + chars('CAP') + [EOC3, EDM3]
    assert {name: cues(srt) for name, srt in files(SRTCaptionTrack, {
        14: field_one + [NULL] * (len(field_two) - len(field_one)),
        15: field_two,
    }).items()} == {
        'T1.srt': [('00:00:00,000', '00:00:00,133', 'ABCDEF')],
        'CC3.srt': [('00:00:00,300', '00:00:00,333', 'CAP')],
    }


def test_decoders_read_frames_until_done(monkeypatch):
    monkeypatch.setattr(cc_decode, 'setproctitle', lambda name: None)
    line = {14: [RCL, ROW_15] + chars('AB') + [EOC, RTD1] + chars('T') + [CR, EDM]}
    with tempfile.TemporaryDirectory() as folder:
        base = os.path.join(folder, 'out')
        for decoder in (decode_to_text, decode_to_srt, decode_to_scc, decode_to_html):
            decoder(Pipe(frames(line)), base, {'frame_rate': 30})
            decoder(Hangup(), base + '.hangup', {'frame_rate': 30})
        found = {}
        for name in os.listdir(folder):
            with open(os.path.join(folder, name)) as f:
                found[name[len('out.'):]] = f.read()
    assert sorted(found) == ['CC1.html', 'CC1.scc', 'CC1.srt', 'T1.html', 'T1.scc', 'T1.srt', 'T1.txt']
    assert found == {**files(TextCaptionTrack, line), **files(SRTCaptionTrack, line),
                     **files(SCCCaptionTrack, line), **files(HTMLCaptionTrack, line)}


def test_pac_to_an_earlier_row_starts_a_new_line():
    # CEA-608-E C.7, B.8.3
    line = [RCL, ROW_15] + chars('BB') + [ROW_14] + chars('AA') + [EOC, EDM]
    assert cues(files(SRTCaptionTrack, {14: line})['CC1.srt']) == [('00:00:00,167', '00:00:00,200', 'AA\nBB')]


def test_background_colour_ends_with_the_row():
    # CEA-608-E 6.2
    line = [RCL, ROW_14] + chars(' ') + [BACKGROUND_GREEN] + chars('A') + [ROW_15] + chars('B') + [EOC, EDM]
    assert [classes for classes, text in styled(files(HTMLCaptionTrack, {14: line})['CC1.html']) if text == 'B'] == [
        'caption-font-normal background-black text-white',
    ]


def test_background_attribute_code_colours_the_space_it_stands_for():
    # CEA-608-E 6.2
    line = [RCL, ROW_15] + chars('A ') + [BACKGROUND_GREEN] + chars('B') + [EOC, EDM]
    assert backgrounds(files(HTMLCaptionTrack, {14: line})['CC1.html']) == [
        ('background-black', 'A'),
        ('background-green', ' B'),
    ]


def test_roll_up_row_after_a_carriage_return_is_white():
    # CEA-608-E C.14
    line = [RU2, ROW_15] + chars('A') + [MID_RED] + chars('B') + [CR] + chars('C') + [EDM]
    assert [classes for classes, text in styled(files(HTMLCaptionTrack, {14: line})['CC1.html']) if text == 'C'] == [
        'caption-font-normal background-black text-white',
    ]


def test_erase_displayed_memory_during_text_ends_a_roll_up_caption():
    # CEA-608-E C.16, B.11.4
    line = [RU2, ROW_15] + chars('AA') + [RTD1] + chars('T') + [CR, EDM]
    assert cues(files(SRTCaptionTrack, {14: line}).get('CC1.srt', '')) == [('00:00:00,000', '00:00:00,200', 'AA')]


def test_text_interruption_does_not_restart_a_roll_up_caption():
    # CEA-608-E C.10, 7.7
    line = [RU2, ROW_15] + chars('AA') + [RTD1] + chars('T') + [CR, RU2, CR, ROW_15] + chars('BB') + [EDM]
    assert cues(files(SRTCaptionTrack, {14: line})['CC1.srt']) == [
        ('00:00:00,000', '00:00:00,233', 'AA'),
        ('00:00:00,233', '00:00:00,333', 'AA\nBB'),
    ]


def test_single_resume_text_display_after_xds_resumes_text(capsys):
    # CEA-608-E D.2, 8.6.2
    line = [RTD3] + PBS + [RTD3] + chars('OK') + [CR3]
    assert written(capsys, TextCaptionTrack, {15: line}) == 'OK\n'
