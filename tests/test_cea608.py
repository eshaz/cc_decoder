"""Construction tests for the CEA-608 byte layer: XDS packets, field 2 channels, XDS fields.

The KET and KCET captures show the XDS changes in bulk, but none of them carries CC3, CC4, T3
or T4, and each XDS case is clearer on its own, so these are built byte by byte to the spec
(CEA-608-E 8.4, 8.6 and 9.5) and fed through the same entry points the decoder uses.

    python3 -m pytest tests/test_cea608.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

import lib.cc_decode as cc_decode
from lib.cc_decode import (NO_PARITY_TO_ODD_PARITY, CaptionTrackFactory, SRTCaptionTrack,
                           TextCaptionTrack, decode_byte_pair, decode_xds_content_advisory,
                           decode_xds_packets, decode_xds_time_of_day, describe_xds_packet)


class Bad(int):
    """ A byte sent with its parity bit wrong """


def wire(byte):
    """ A seven bit value as it is on the wire, with its odd parity bit """
    return NO_PARITY_TO_ODD_PARITY[byte] ^ (0x80 if isinstance(byte, Bad) else 0)


def frames(lines):
    """ The raw rows the slicer hands on, one frame per pair, from {row: [(b1, b2), ...]} """
    count = len(next(iter(lines.values())))
    return [[(row, wire(pairs[at][0]), wire(pairs[at][1]), 0, ()) for row, pairs in lines.items()]
            for at in range(count)]


class Pipe:
    """ Stands in for the connection a format reads its frames from """

    def __init__(self, sent):
        self._sent = list(sent) + ["DONE"]

    def recv(self):
        return self._sent.pop(0)


def xds(capsys, monkeypatch, lines):
    monkeypatch.setattr(cc_decode, 'setproctitle', lambda name: None)
    decode_xds_packets(Pipe(frames(lines)), None, {})
    return [line.split(': ', 1)[1] for line in capsys.readouterr().out.splitlines()]


class Recorder:
    """ A caption track that keeps the codes it was given and nothing else """

    def __init__(self, cc_track, output_filename, options):
        self.codes = []
        self.mode = None

    def add_data(self, data, frame):
        if data[3] or data[5]:
            self.codes.append(data[1])

    def close(self):
        pass


def tracks(lines):
    factory = CaptionTrackFactory(Recorder, None, {})
    for frame, rows in enumerate(frames(lines)):
        factory.add_data(rows, frame)
    return {name: track.codes for name, track in factory._tracks.items()}


def written(capsys, track, lines):
    factory = CaptionTrackFactory(track, None, {'frame_rate': 30})
    for frame, rows in enumerate(frames(lines)):
        factory.add_data(rows, frame)
    factory.close_tracks()
    return capsys.readouterr().out


def chars(text):
    return [(ord(a), ord(b)) for a, b in zip(text[::2], text[1::2].ljust(len(text[::2]), '\0'))]


def packet(*pairs):
    """ An XDS packet with its End and a correct checksum """
    total = sum(a + b for a, b in pairs) + 0x0F
    return list(pairs) + [(0x0F, -total % 128)]


NULL = (0x00, 0x00)
PBS = [(0x05, 0x01), (0x20, 0x50), (0x42, 0x53), (0x0F, 0x66)]
RTD1 = (0x14, 0x2B)
RTD3 = (0x15, 0x2B)
RCL = (0x14, 0x20)
BS = (0x14, 0x21)
RU2 = (0x14, 0x25)
EDM = (0x14, 0x2C)
CR = (0x14, 0x2D)
ENM = (0x14, 0x2E)
EOC = (0x14, 0x2F)
ROW_15 = (0x14, 0x70)


def test_xds_packet_survives_xds_range_bytes_on_another_line(capsys, monkeypatch):
    # KET's field 1 text service sends bytes in 0x01-0x0F, which took the XDS reader off field 2
    text = [RTD1, (0x01, 0x00), (0x41, 0x42), (0x14, 0x2D)]
    assert xds(capsys, monkeypatch, {14: text, 15: PBS}) == ['XDS Channel Name:  PBS']


def test_xds_continue_resumes_packet_and_is_not_in_checksum(capsys, monkeypatch):
    call_sign = [(0x05, 0x02), (0x4B, 0x43), (0x15, 0x2C), (0x48, 0x49),
                 (0x06, 0x02), (0x45, 0x54), (0x0F, 0x43)]
    assert xds(capsys, monkeypatch, {15: call_sign}) == ['XDS Channel Station Call-Sign: KCET']


def test_xds_start_again_discards_the_partial_packet(capsys, monkeypatch):
    restarted = [(0x05, 0x01), (0x58, 0x58)] + PBS
    assert xds(capsys, monkeypatch, {15: restarted}) == ['XDS Channel Name:  PBS']


def test_xds_end_with_nothing_open_is_not_a_packet(capsys, monkeypatch):
    assert xds(capsys, monkeypatch, {15: [(0x0F, 0x66)]}) == []


def test_xds_checksum_byte_is_judged_by_the_checksum_not_its_parity(capsys, monkeypatch):
    # KCET 1994 sends its call sign's checksum byte with the parity bit wrong
    call_sign = [(0x05, 0x02), (0x4B, 0x43), (0x45, 0x54), (0x0F, Bad(0x43))]
    assert xds(capsys, monkeypatch, {15: call_sign}) == ['XDS Channel Station Call-Sign: KCET']

    # but a data bit gone wrong in it still fails
    damaged = call_sign[:-1] + [(0x0F, Bad(0x42))]
    assert xds(capsys, monkeypatch, {15: damaged}) == ['XDS Rejected Packet - Incorrect Checksum']


def test_field_two_is_cc3_and_leaves_xds_out():
    field_one = [RTD1, RTD1, (0x48, 0x49)] + [NULL] * 6
    field_two = [RTD3, RTD3, (0x4F, 0x4B)] + PBS + [RTD3, (0x59, 0x4F)]
    assert tracks({14: field_one, 15: field_two}) == {
        'CC1': ['CC1 Resume Text Display', 'CC1 Resume Text Display', 'HI'],
        'CC3': ['CC3 Resume Text Display', 'CC3 Resume Text Display', 'OK',
                'CC3 Resume Text Display', 'YO'],
    }


def test_codes_shared_by_both_fields_go_to_the_field_of_their_line():
    rcl = (0x15, 0x20)
    assert tracks({15: [rcl, rcl, (0x14, 0x70), (0x41, 0x42)]}) == {
        'CC3': ['CC3 Resume Caption Loading', 'CC3 Resume Caption Loading',
                'CC1 Pre: Indent 0 row 15', 'AB'],
    }


def test_one_stray_field_two_code_does_not_move_a_field_one_line():
    line = [RTD1, RTD1, (0x15, 0x20), (0x43, 0x44), (0x14, 0x2D)]
    assert tracks({14: line}) == {
        'CC1': ['CC1 Resume Text Display', 'CC1 Resume Text Display', 'CD', 'CC1 Carriage Return'],
    }


def test_xds_start_with_a_damaged_type_byte_is_still_xds():
    line = [RTD3, RTD3, (0x05, Bad(0x01)), (0x20, 0x50), (0x42, 0x53), (0x0F, 0x66),
            RTD3, (0x4F, 0x4B)]
    assert tracks({15: line}) == {
        'CC3': ['CC3 Resume Text Display'] * 3 + ['OK'],
    }


def test_content_advisory_systems():
    assert decode_xds_content_advisory([(0x43, 0x40)]) == 'XDS Rating: PG-13'
    assert decode_xds_content_advisory([(0x68, 0x7D)]) == \
        'XDS Rating: TV-14 Violence Sexual Situations Adult Language Sexually Suggestive Dialogue'
    assert decode_xds_content_advisory([(0x48, 0x62)]) == 'XDS Rating: TV-Y7 Fantasy Violence'
    assert decode_xds_content_advisory([(0x58, 0x44)]) == 'XDS Rating: PG'
    assert decode_xds_content_advisory([(0x78, 0x43)]) == 'XDS Rating: 13 ans +'


def test_audio_services():
    packet = [(0x01, 0x06), (0x4B, 0x51), (0x0F, 0x4E)]
    assert describe_xds_packet(packet) == 'XDS Audio Services: Main:English(Stereo) Sap:Spanish(Mono)'


def test_copy_protection():
    packet = [(0x01, 0x08), (0x5E, 0x40), (0x0F, 0x4A)]
    assert describe_xds_packet(packet) == \
        'XDS Copy protection: No copying is permitted Analogue protection: PSP On; 4 line Split Burst On'


def test_time_of_day_on_a_sunday():
    assert decode_xds_time_of_day([(0x5E, 0x74), (0x53, 0x44), (0x41, 0x48)]) == \
        'TM 20:30D _SA Apr 19 1998 Sun'


def test_xds_packet_fields_stop_at_the_end_pair():
    # CEA-608-E 8.6.1, 9.5.1.2
    assert describe_xds_packet(packet((0x01, 0x02), (0x5E, 0x41))) == 'XDS Current Length of Show: 01:30'
    # CEA-608-E 8.6.6
    assert describe_xds_packet(packet((0x01, 0x03), *[(0x41, 0x41)] * 17)) == 'XDS Rejected Packet - Too Long'


def test_xds_program_identification():
    # CEA-608-E 9.5.2
    assert describe_xds_packet(packet((0x03, 0x03), (0x41, 0x42))) == 'XDS Future Program Name: AB'
    # CEA-608-E 9.5.1.1
    assert describe_xds_packet(packet((0x01, 0x01), (0x7F, 0x7F), (0x7F, 0x7F))) == 'XDS Current End of Program'


def test_xds_channel_and_time_fields():
    # CEA-608-E 9.5.3.2
    assert describe_xds_packet(packet((0x05, 0x02), (0x4B, 0x43), (0x45, 0x54), (0x32, 0x38))) == \
        'XDS Channel Station Call-Sign: KCET Native Channel: 28'
    # CEA-608-E Table 15
    assert decode_xds_time_of_day([(0x5E, 0x40), (0x61, 0x43), (0x46, 0x46)]) == 'TM 00:30S _SL Mar 01 1996 Fri'
    # CEA-608-E Table 38
    assert describe_xds_packet(packet((0x07, 0x04), (0x57, 0x00))) == 'XDS Local Time Zone: 1 no DST'
    assert describe_xds_packet(packet((0x07, 0x04), (0x65, 0x00))) == 'XDS Local Time Zone: -5 observes DST'


def test_attribute_and_extended_character_codes():
    # CEA-608-E Table 3
    assert decode_byte_pair(True, 0x1F, 0x2D) == 'CC2 Background Transparent'
    assert decode_byte_pair(True, 0x10, 0x23) == 'CC1 Background Semi-Transparent Green'
    assert decode_byte_pair(True, 0x17, 0x2E) == 'CC1 Foreground Black'
    # CEA-608-E Table 5, Table 9
    assert decode_byte_pair(False, 0x12, 0x26) == '‘'
    assert decode_byte_pair(False, 0x13, 0x37) != decode_byte_pair(False, 0x13, 0x2E)


def test_redundant_control_codes_act_once(capsys):
    # CEA-608-E D.2, B.14
    line = [RTD1, RTD1] + chars('AB') + [CR, CR, CR] + chars('CD') + [CR, (0x11, 0x37), (0x11, 0x37), CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'AB\n\nCD\n\u266a\n'


def test_tab_offset_and_backspace_stay_on_the_row(capsys):
    # CEA-608-E C.13, B.12
    line = [RTD1] + chars('AB') + [(0x17, 0x21)] + chars('CD') + [CR, BS] + chars('EF') + [CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'AB CD\nEF\n'


def test_extended_character_replaces_the_one_before(capsys):
    # CEA-608-E 6.4.2
    line = [RTD1, (0x47, 0x72), (0x75, 0x00), (0x12, 0x25), (0x6E, 0x00), CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'Gr\u00fcn\n'


def test_text_mode_pac_keeps_the_row(capsys):
    # CEA-608-E 7.4
    line = [RTD1, (0x11, 0x50)] + chars('HELLO') + [(0x11, 0x70), CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'HELLO\n'


def test_end_of_caption_ends_text_mode(capsys):
    # CEA-608-E 7.7, C.10
    line = [RTD1] + chars('T1') + [CR, EOC, ROW_15] + chars('CAP') + [CR]
    assert written(capsys, TextCaptionTrack, {14: line}) == 'T1\n'


def test_xds_ends_field_two_text_mode(capsys):
    # CEA-608-E 7.7
    line = [RTD3, RTD3] + chars('AB') + [(0x15, 0x2D)] + PBS + [(0x15, 0x70)] + chars('XY') + [(0x15, 0x2D)]
    assert written(capsys, TextCaptionTrack, {15: line}) == 'AB\n'


def test_end_of_caption_ends_the_caption_it_replaces(capsys):
    # CEA-608-E B.8.3
    line = []
    for word in ('ONE ', 'TWO '):
        line += [RCL, ENM, ROW_15] + chars(word) + [EOC, NULL]
    cues = written(capsys, SRTCaptionTrack, {14: line + [EDM]}).split('\n\n')
    assert [cue.splitlines()[-1] for cue in cues if cue.strip()] == ['ONE ', 'TWO ']


def test_roll_up_keeps_its_window(capsys):
    # CEA-608-E 7.4, C.10
    line = [RU2, ROW_15] + chars('AA') + [CR] + chars('BB') + [CR] + chars('CC') + [CR, EDM]
    cues = written(capsys, SRTCaptionTrack, {14: line}).split('\n\n')
    assert ['|'.join(cue.splitlines()[2:]) for cue in cues if cue.strip()] == ['AA', 'AA|BB', 'BB|CC', 'CC']
