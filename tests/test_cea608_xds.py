"""XDS construction tests, CEA-608-E 8.6 and 9

    python3 -m pytest tests/test_cea608_xds.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

import lib.cc_decode as cc_decode
from lib.cc_decode import (compute_xds_packet_checksum, decode_xds_packets, decode_xds_string,
                           describe_xds_packet, get_output_function)
from tests.test_cea608 import NULL, PBS, RTD3, Bad, Pipe, chars, frames, packet, xds


# CEA-608-E Table 17
KEYWORDS = [
    'Education', 'Entertainment', 'Movie', 'News', 'Religious', 'Sports', 'Other', 'Action',
    'Advertisement', 'Animated', 'Anthology', 'Automobile', 'Awards', 'Baseball', 'Basketball', 'Bulletin',
    'Business', 'Classical', 'College', 'Combat', 'Comedy', 'Commentary', 'Concert', 'Consumer',
    'Contemporary', 'Crime', 'Dance', 'Documentary', 'Drama', 'Elementary', 'Erotica', 'Exercise',
    'Fantasy', 'Farm', 'Fashion', 'Fiction', 'Food', 'Football', 'Foreign', 'Fund Raiser',
    'Game/Quiz', 'Garden', 'Golf', 'Government', 'Health', 'High School', 'History', 'Hobby',
    'Hockey', 'Home', 'Horror', 'Information', 'Instruction', 'International', 'Interview', 'Language',
    'Legal', 'Live', 'Local', 'Math', 'Medical', 'Meeting', 'Military', 'Miniseries',
    'Music', 'Mystery', 'National', 'Nature', 'Police', 'Politics', 'Premier', 'Prerecorded',
    'Product', 'Professional', 'Public', 'Racing', 'Reading', 'Repair', 'Repeat', 'Review',
    'Romance', 'Science', 'Series', 'Service', 'Shopping', 'Soap Opera', 'Special', 'Suspense',
    'Talk', 'Technical', 'Tennis', 'Travel', 'Variety', 'Video', 'Weather', 'Western',
]

# CEA-608-E Table 20
MPA = ['N/A', 'G', 'PG', 'PG-13', 'R', 'NC-17', 'X', 'Not Rated']

# CEA-608-E Table 21
TV = ['Not rated', 'TV-Y', 'TV-Y7', 'TV-G', 'TV-PG', 'TV-14', 'TV-MA', 'Not rated']
TV_FLAGS = {
    2: ['Fantasy Violence'],
    4: ['Violence', 'Sexual Situations', 'Adult Language', 'Sexually Suggestive Dialogue'],
    5: ['Violence', 'Sexual Situations', 'Adult Language', 'Sexually Suggestive Dialogue'],
    6: ['Violence', 'Sexual Situations', 'Adult Language'],
}

# CEA-608-E Table 22
ENGLISH = ['E', 'C', 'C8+', 'G', 'PG', '14+', '18+', 'Invalid']

# CEA-608-E Table 23
FRENCH = ['E', 'G', '8 ans +', '13 ans +', '16 ans +', '18 ans +', 'Invalid', 'Invalid']

# CEA-608-E Table 25
LANGUAGES = ['Unknown', 'English', 'Spanish', 'French', 'German', 'Italian', 'Other', 'None']

# CEA-608-E Table 26
MAIN_AUDIO = ['Unknown', 'Mono', 'Simulated Stereo', 'Stereo', 'Stereo Surround', 'Data Service', 'Other', 'None']
SECOND_AUDIO = ['Unknown', 'Mono', 'Video Descriptions', 'Non-program Audio', 'Special Effects', 'Data Service',
                'Other', 'None']

# CEA-608-E Table 30
CGMS = ['Copying is permitted without restriction', 'No more copies (one generation copy has been made)',
        'One generation of copies may be made', 'No copying is permitted']

# CEA-608-E Table 31
APS = ['No Analogue protection', 'Analogue protection: PSP On; Split Burst Off',
       'Analogue protection: PSP On; 2 line Split Burst On', 'Analogue protection: PSP On; 4 line Split Burst On']

# CEA-608-E Table 15
MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

# CEA-608-E Table 36
DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

# CEA-608-E Table 13
STAR_TREK = [(0x01, 0x03), (0x53, 0x74), (0x61, 0x72), (0x20, 0x54), (0x72, 0x65), (0x6B, 0x00), (0x0F, 0x1D)]

# CEA-608-E Table 32
COMPOSITE_1 = [(0x25, 0x2E), NULL, (0x00, 0x48), (0x40, 0x42), NULL, (0x46, 0x69), (0x6E, 0x61), (0x6C, 0x73)]

# CEA-608-E Table 33
COMPOSITE_2 = [(0x5E, 0x54), (0x53, 0x44), (0x4B, 0x51), (0x48, 0x51), (0x4B, 0x43), (0x45, 0x54), (0x32, 0x38),
               (0x50, 0x42), (0x53, 0x00)]

# CEA-608-E 9.5.3.2
CALL_SIGN =[(0x05, 0x02), (0x4B, 0x43), (0x45, 0x54), (0x0F, 0x43)]


def describe(*pairs):
    return describe_xds_packet(packet(*pairs))


def run(capsys, monkeypatch, lines):
    monkeypatch.setattr(cc_decode, 'setproctitle', lambda name: None)
    decode_xds_packets(Pipe(frames(lines)), None, {})
    captured = capsys.readouterr()
    return captured.out.splitlines(), captured.err.splitlines()


def resumed(whole, at, *between):
    start, kind = whole[0]
    return whole[:at] + list(between) + [(start + 1, kind)] + whole[at:]


def time_of_day(minute, hour, date, month, day, year):
    return describe((0x07, 0x01), (0x40 | minute, 0x40 | hour), (0x40 | date, 0x40 | month),
                    (0x40 | day, 0x40 | year))


class Closed(Pipe):
    def recv(self):
        sent = Pipe.recv(self)
        if sent == "DONE":
            raise EOFError
        return sent


def test_xds_checksum():
    # CEA-608-E Table 13
    assert compute_xds_packet_checksum(STAR_TREK)
    # CEA-608-E 8.6.3
    assert not compute_xds_packet_checksum(STAR_TREK[:-1] + [(0x0F, 0x1E)])
    assert not compute_xds_packet_checksum(STAR_TREK[:-1] + [(0x0F, 0x1C)])
    assert not compute_xds_packet_checksum(STAR_TREK[:-1] + [(0x0F, 0x5D)])
    assert compute_xds_packet_checksum([(0x01, 0x03), (0x6D, 0x00), (0x0F, 0x00)])
    assert compute_xds_packet_checksum([(0x01, 0x10), (0x0F, 0x60)])
    assert not compute_xds_packet_checksum([])
    assert describe_xds_packet(STAR_TREK[:-1] + [(0x0F, 0x1E)]) == 'XDS Rejected Packet - Incorrect Checksum'
    assert describe_xds_packet([]) == 'XDS - Empty Packet'


def test_xds_interleaved_with_captions_as_in_table_13(capsys, monkeypatch):
    # CEA-608-E Table 13
    ru3, caption = (0x14, 0x26), (0x48, 0x49)
    line = [NULL, NULL, caption] + STAR_TREK[:5] + [ru3] + [caption] * 7 + [(0x02, 0x03), ru3]
    line += [caption] * 6 + [ru3] + [caption] * 5 + [(0x02, 0x03)] + STAR_TREK[5:] + [ru3, NULL, NULL]
    line += [caption] * 2
    assert xds(capsys, monkeypatch, {15: line}) == ['XDS Current Program Name: Star Trek']


def test_xds_two_lines_at_once(capsys, monkeypatch):
    # CEA-608-E 8.6
    fourteen = packet((0x01, 0x03), *chars('ABCD')) + PBS
    fifteen = packet((0x01, 0x03), *chars('WX')) + [NULL] + CALL_SIGN
    assert run(capsys, monkeypatch, {14: fourteen, 15: fifteen}) == ([
        '3: XDS Current Program Name: WX', '4: XDS Current Program Name: ABCD',
        '8: XDS Channel Name:  PBS', '8: XDS Channel Station Call-Sign: KCET'], [])


def test_xds_packet_resumes_after_a_packet_of_another_class(capsys, monkeypatch):
    # CEA-608-E 8.6.7
    future = packet((0x03, 0x03), *chars('XY'))
    line = resumed(packet((0x01, 0x03), *chars('ABCD')), 2, *future)
    assert xds(capsys, monkeypatch, {15: line}) == ['XDS Future Program Name: XY', 'XDS Current Program Name: ABCD']


def test_xds_two_packets_open_at_once(capsys, monkeypatch):
    # CEA-608-E 8.6.5
    name = packet((0x01, 0x03), *chars('ABCDEF'))
    row = packet((0x01, 0x10), *chars('XYZW'))
    line = name[:2] + row[:2] + [(0x02, 0x03), name[2], (0x02, 0x10)] + row[2:] + [(0x02, 0x03)] + name[3:]
    assert xds(capsys, monkeypatch, {15: line}) == ['XDS Program description line: 1 :XYZW ',
                                                    'XDS Current Program Name: ABCDEF']


def test_xds_start_again_after_an_interruption_aborts_the_first(capsys, monkeypatch):
    # CEA-608-E 8.6.8
    line = [(0x01, 0x03), (0x58, 0x58)] + PBS + packet((0x01, 0x03), *chars('AB'))
    line += [(0x01, 0x03), (0x59, 0x59), RTD3] + packet((0x01, 0x03), *chars('CD'))
    assert xds(capsys, monkeypatch, {15: line}) == ['XDS Channel Name:  PBS', 'XDS Current Program Name: AB',
                                                    'XDS Current Program Name: CD']


def test_xds_damaged_end_leaves_the_packet_to_be_started_again(capsys, monkeypatch):
    # CEA-608-E 8.6.2, 8.6.8
    damaged = packet((0x01, 0x03), *chars('CD'))
    damaged[-1] = (Bad(0x0F), damaged[-1][1])
    line = damaged + packet((0x01, 0x03), *chars('EF'))
    assert xds(capsys, monkeypatch, {15: line}) == ['XDS Current Program Name: EF']


def test_xds_continue_of_a_packet_never_started(capsys, monkeypatch):
    # CEA-608-E 9.2
    name = packet((0x01, 0x03), *chars('ABCD'))
    line = name[:2] + [(0x04, 0x03), (0x58, 0x58), (0x0F, 0x00), (0x02, 0x03)] + name[2:]
    assert run(capsys, monkeypatch, {15: line}) == (['8: XDS Current Program Name: ABCD'], [])


def test_xds_caption_codes_suspend_the_packet(capsys, monkeypatch):
    # CEA-608-E 8.6.2, 8.6.7
    name = packet((0x01, 0x03), *chars('ABCDEF'))
    line = name[:2] + [(0x15, 0x20), (0x48, 0x49), (0x0F, 0x00), (0x02, 0x03), name[2], (0x1D, 0x2D),
                       (0x4A, 0x4B), (0x02, 0x03), name[3], (0x10, 0x40), (0x02, 0x03), name[4]]
    assert run(capsys, monkeypatch, {15: line}) == (['14: XDS Current Program Name: ABCDEF'], [])


def test_xds_continue_resumes_the_start_of_its_own_class(capsys, monkeypatch):
    # CEA-608-E Table 14
    sent = [
        ((0x01, 0x03), chars('ABCD'), 'XDS Current Program Name: ABCD'),
        ((0x03, 0x03), chars('ABCD'), 'XDS Future Program Name: ABCD'),
        ((0x05, 0x01), chars(' PBS'), 'XDS Channel Name:  PBS'),
        ((0x07, 0x01), [(0x60, 0x60), (0x4C, 0x44), (0x43, 0x44)],
         'XDS Time of day (UTC): TM 00:32D _SA Apr 12 1994 Tue'),
        ((0x09, 0x02), chars('ABCD'), 'XDS Public Service - Weather: ABCD'),
        ((0x0B, 0x01), chars('ABCD'), 'Could not decode ---> XDS describes: 0b 01'),
        ((0x0D, 0x01), chars('ABCD'), 'Could not decode ---> XDS describes: 0d 01'),
    ]
    line = []
    for start, pairs, _ in sent:
        line += resumed(packet(start, *pairs), 2, RTD3)
    assert xds(capsys, monkeypatch, {15: line}) == [described for _, _, described in sent]


def test_xds_pairs_that_fail_parity_inside_a_packet(capsys, monkeypatch):
    # CEA-608-E 8.6.2, 8.6.3
    name = packet((0x01, 0x03), *chars('AB'))
    first = [name[0], (Bad(0x41), 0x42), name[2]]
    second = [name[0], (0x41, Bad(0x42)), name[2]]
    kind = [(0x05, Bad(0x01))] + PBS[1:]
    start = [(Bad(0x05), 0x01)] + PBS[1:]
    assert run(capsys, monkeypatch, {15: first + second + kind + start}) == ([
        '3: XDS Rejected Packet - Incorrect Checksum', '6: XDS Rejected Packet - Incorrect Checksum',
        '10: XDS Rejected Packet - Incorrect Checksum'], [])


def test_xds_checksum_parity_is_judged_per_line(capsys, monkeypatch):
    # CEA-608-E 8.6.3
    current = packet((0x01, 0x03), *chars('AB'))
    future = packet((0x03, 0x03), *chars('CD'))
    damaged = list(future)
    for sent in (current, future):
        sent[-1] = (0x0F, Bad(sent[-1][1]))
    damaged[-1] = (0x0F, Bad(damaged[-1][1] ^ 0x10))
    assert xds(capsys, monkeypatch, {14: current + current, 15: future + damaged}) == [
        'XDS Current Program Name: AB', 'XDS Future Program Name: CD',
        'XDS Current Program Name: AB', 'XDS Rejected Packet - Incorrect Checksum']


def test_xds_informational_nulls_are_kept(capsys, monkeypatch):
    # CEA-608-E 8.6.1, 9.2, 9.5.3.2
    call = packet((0x05, 0x02), *chars('WGBH'), (0x00, 0x32))
    name = packet((0x05, 0x01), *chars('NBC'))
    show = packet((0x01, 0x02), (0x5E, 0x41), (0x6D, 0x40), (0x4C, 0x00))
    assert xds(capsys, monkeypatch, {15: call + name + show}) == [
        'XDS Channel Station Call-Sign: WGBH Native Channel: 2', 'XDS Channel Name: NBC',
        'XDS Current Length of Show: 01:30 XDS Current Elapsed time: 00:45:12']


def test_xds_stuffing_is_not_part_of_a_packet(capsys, monkeypatch):
    # CEA-608-E 8.6.4, 8.6.6
    title = packet((0x01, 0x03), *chars('ABCDEFGHIJKLMNOPQRSTUVWXYZ012345'))
    show = packet((0x01, 0x02), (0x5E, 0x41), (0x6D, 0x40))
    line = [NULL] + resumed(title, 9, NULL, NULL) + [NULL] + resumed(show, 2, NULL) + [NULL]
    assert xds(capsys, monkeypatch, {15: line}) == [
        'XDS Current Program Name: ABCDEFGHIJKLMNOPQRSTUVWXYZ012345',
        'XDS Current Length of Show: 01:30 XDS Current Elapsed time: 00:45:00']


def test_xds_packet_of_more_than_32_characters_is_rejected(capsys, monkeypatch):
    # CEA-608-E 8.6.6
    longest = packet((0x01, 0x03), *chars('ABCDEFGHIJKLMNOPQRSTUVWXYZ012345'))
    longer = packet((0x01, 0x03), *chars('ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567'))
    line = resumed(longest, 9, RTD3) + resumed(longer, 9, RTD3)
    assert xds(capsys, monkeypatch, {15: line}) == ['XDS Current Program Name: ABCDEFGHIJKLMNOPQRSTUVWXYZ012345',
                                                    'XDS Rejected Packet - Too Long']


def test_xds_null_fields_of_a_composite_packet_are_kept(capsys, monkeypatch):
    # CEA-608-E 8.6.1, 9.5.1.10
    assert xds(capsys, monkeypatch, {15: packet((0x01, 0x0C), *COMPOSITE_1)}) == ['Composite packet 1 8']


def test_xds_short_packets_warn_and_print_nothing(capsys, monkeypatch):
    # CEA-608-E 9.5.1.1, 9.5.1.2, 9.5.1.5, 9.5.1.6, 9.5.1.8, 9.5.1.9, 9.5.3.3, 9.5.4.1, 9.5.4.4
    short = [packet((0x07, 0x01), (0x5E, 0x54)), packet((0x07, 0x01), (0x5E, 0x54), (0x53, 0x44)),
             packet((0x01, 0x01), (0x5E, 0x54)), packet((0x03, 0x01), (0x5E, 0x54)), packet((0x01, 0x02)),
             packet((0x01, 0x05)), packet((0x01, 0x06)), packet((0x01, 0x08)), packet((0x01, 0x09)),
             packet((0x05, 0x03)), packet((0x07, 0x04))]
    out, err = run(capsys, monkeypatch, {15: [pair for sent in short for pair in sent] + PBS})
    assert [line.split(': ', 1)[1] for line in out] == ['XDS Channel Name:  PBS']
    assert len(err) == len(short) and all(line.startswith('WARN: ') for line in err)


def test_xds_file_is_written_once_a_packet_ends(capsys, monkeypatch):
    opened = []

    def output(extension, output_filename):
        out_func, written = get_output_function(extension, output_filename)
        opened.append(written)
        return out_func, written

    monkeypatch.setattr(cc_decode, 'setproctitle', lambda name: None)
    monkeypatch.setattr(cc_decode, 'get_output_function', output)
    with tempfile.TemporaryDirectory() as folder:
        name = os.path.join(folder, 'capture')
        decode_xds_packets(Closed(frames({15: [NULL, (0x0F, 0x00)]})), name, {})
        assert opened == [] and not os.path.exists(name + '.xds')
        decode_xds_packets(Closed(frames({15: PBS + PBS})), name, {})
        assert len(opened) == 1 and opened[0].closed
        with open(name + '.xds') as written:
            assert written.read() == '4: XDS Channel Name:  PBS\n8: XDS Channel Name:  PBS\n'
    assert capsys.readouterr().out == ''


def test_xds_program_identification_number():
    # CEA-608-E 9.5.1.1, Table 15
    assert describe((0x01, 0x01), (0x5E, 0x54), (0x53, 0x44)) == \
        'XDS Current Scheduled Start Time: 20:30 on Day 19 of Month 04 '
    assert describe((0x01, 0x01), (0x5E, 0x74), (0x73, 0x64)) == \
        'XDS Current Scheduled Start Time: 20:30 on Day 19 of Month 04 '
    assert describe((0x03, 0x01), (0x5E, 0x54), (0x53, 0x54)) == \
        'XDS Future Scheduled Start Time: 20:30 on Day 19 of Month 04 (Tape Delayed)'
    assert describe((0x01, 0x01), (0x40, 0x40), (0x41, 0x41)) == \
        'XDS Current Scheduled Start Time: 00:00 on Day 01 of Month 01 '
    assert describe((0x03, 0x01), (0x7B, 0x57), (0x5F, 0x4C)) == \
        'XDS Future Scheduled Start Time: 23:59 on Day 31 of Month 12 '
    assert describe((0x03, 0x01), (0x7F, 0x7F), (0x7F, 0x7F)) == 'XDS Future End of Program'
    assert describe((0x01, 0x01), (0x7F, 0x7F), (0x7F, 0x7E)) == \
        'XDS Current Scheduled Start Time: 31:63 on Day 31 of Month 14 (Tape Delayed)'


def test_xds_length_and_time_in_show():
    # CEA-608-E 9.5.1.2, Table 16
    assert describe((0x03, 0x02), (0x5E, 0x40)) == 'XDS Future Length of Show: 00:30'
    assert describe((0x01, 0x02), (0x5E, 0x41), (0x6D, 0x40)) == \
        'XDS Current Length of Show: 01:30 XDS Current Elapsed time: 00:45:00'
    assert describe((0x03, 0x02), (0x40, 0x42), (0x4F, 0x41), (0x5E, 0x00)) == \
        'XDS Future Length of Show: 02:00 XDS Future Elapsed time: 01:15:30'
    assert describe((0x01, 0x02), (0x7B, 0x57), (0x7B, 0x57), (0x7B, 0x00)) == \
        'XDS Current Length of Show: 23:59 XDS Current Elapsed time: 23:59:59'


def test_xds_program_name():
    # CEA-608-E 9.5.1.3, Table 50
    assert describe((0x01, 0x03), (0x45, 0x6C), (0x20, 0x4E), (0x69, 0x7E), (0x6F, 0x00)) == \
        'XDS Current Program Name: El Ni\u00f1o'


def test_xds_program_type_keywords():
    # CEA-608-E Table 17
    for first in range(0x20, 0x80, 32):
        codes = list(range(first, first + 32))
        assert describe((0x01, 0x04), *zip(codes[::2], codes[1::2])) == \
            'XDS Program Genre: ' + ''.join(KEYWORDS[code - 0x20] + ' ' for code in codes)
    # CEA-608-E 9.2
    assert describe((0x01, 0x04), (0x25, 0x2D), (0x2E, 0x00)) == 'XDS Program Genre: Sports Baseball Basketball  '


def test_xds_fields_stop_at_an_end_pair():
    # CEA-608-E 8.6.2
    pairs = [(0x41, 0x42), (0x0F, 0x00), (0x43, 0x44)]
    assert decode_xds_string(pairs) == 'AB' and pairs == [(0x43, 0x44)]
    assert describe((0x01, 0x04), (0x20, 0x22), (0x0F, 0x00), (0x23, 0x24)) == 'XDS Program Genre: Education Movie '


def test_xds_content_advisory_systems_and_ratings():
    # CEA-608-E Table 19, Table 20
    for system in (0x00, 0x10):
        for rating in range(8):
            assert describe((0x01, 0x05), (0x40 | system | rating, 0x40)) == 'XDS Rating: ' + MPA[rating]
    for rating in range(8):
        # CEA-608-E Table 21
        assert describe((0x01, 0x05), (0x48, 0x40 | rating)) == 'XDS Rating: ' + TV[rating]
        # CEA-608-E Table 22
        assert describe((0x01, 0x05), (0x58, 0x40 | rating)) == 'XDS Rating: ' + ENGLISH[rating]
        # CEA-608-E Table 23
        assert describe((0x01, 0x05), (0x78, 0x40 | rating)) == 'XDS Rating: ' + FRENCH[rating]
    # CEA-608-E Table 19
    assert describe((0x01, 0x05), (0x58, 0x48)) == 'XDS Rating: International reserved code (88, 72)'
    assert describe((0x01, 0x05), (0x78, 0x48)) == 'XDS Rating: International reserved code (120, 72)'


def test_xds_us_tv_content_flags():
    # CEA-608-E Table 18, Table 21
    for rating in range(8):
        assert describe((0x01, 0x05), (0x68, 0x78 | rating)) == \
            ' '.join(['XDS Rating:', TV[rating]] + TV_FLAGS.get(rating, []))
    for rating in (4, 5, 6):
        assert describe((0x01, 0x05), (0x48, 0x60 | rating)) == 'XDS Rating: %s Violence' % TV[rating]
        assert describe((0x01, 0x05), (0x48, 0x50 | rating)) == 'XDS Rating: %s Sexual Situations' % TV[rating]
        assert describe((0x01, 0x05), (0x48, 0x48 | rating)) == 'XDS Rating: %s Adult Language' % TV[rating]
    assert describe((0x01, 0x05), (0x68, 0x44)) == 'XDS Rating: TV-PG Sexually Suggestive Dialogue'
    assert describe((0x01, 0x05), (0x68, 0x45)) == 'XDS Rating: TV-14 Sexually Suggestive Dialogue'
    assert describe((0x01, 0x05), (0x68, 0x46)) == 'XDS Rating: TV-MA'
    assert describe((0x01, 0x05), (0x48, 0x62)) == 'XDS Rating: TV-Y7 Fantasy Violence'


def test_xds_audio_services_every_language_and_type():
    # CEA-608-E Table 24, Table 25, Table 26
    for main in range(64):
        second = 63 - main
        assert describe((0x01, 0x06), (0x40 | main, 0x40 | second)) == \
            'XDS Audio Services: Main:%s(%s) Sap:%s(%s)' % (LANGUAGES[main >> 3], MAIN_AUDIO[main & 7],
                                                           LANGUAGES[second >> 3], SECOND_AUDIO[second & 7])


def test_xds_caption_services():
    # CEA-608-E Table 27
    assert describe((0x01, 0x07), (0x48, 0x51)) == 'XDS Caption Services'


def test_xds_copy_and_redistribution_control():
    # CEA-608-E Table 29, Table 30, Table 31
    for copying in range(4):
        for protection in range(4):
            first = 0x40 | (copying << 3) | (protection << 1) | ((copying ^ protection) & 1)
            assert describe((0x01, 0x08), (first, 0x40 | (protection & 1))) == \
                'XDS Copy protection: %s %s' % (CGMS[copying], APS[protection])


def test_xds_aspect_ratio():
    # CEA-608-E 9.5.1.9
    assert describe((0x01, 0x09), (0x40, 0x40)) == 'XDS Aspect Ratio: start line: 22 end line: 262 '
    assert describe((0x01, 0x09), (0x7F, 0x7F)) == 'XDS Aspect Ratio: start line: 85 end line: 199 '
    assert describe((0x01, 0x09), (0x43, 0x44), (0x41, 0x00)) == \
        'XDS Aspect Ratio: start line: 25 end line: 258 Anamorphic'
    assert describe((0x01, 0x09), (0x43, 0x44), (0x40, 0x00)) == 'XDS Aspect Ratio: start line: 25 end line: 258 '


def test_xds_composite_packets():
    # CEA-608-E 9.5.1.10
    assert describe((0x01, 0x0C), *COMPOSITE_1) == 'Composite packet 1 8'
    # CEA-608-E 9.5.1.11
    assert describe((0x01, 0x0D), *COMPOSITE_2) == 'Composite packet 2 9'


def test_xds_program_description_rows():
    # CEA-608-E 9.5.1.12
    for row in range(1, 9):
        assert describe((0x01, 0x0F + row), *chars('ROW %d' % row)) == \
            'XDS Program description line: %d :ROW %d ' % (row, row)
    assert describe((0x01, 0x10)) == 'XDS Program description line: 1 : '


def test_xds_channel_information():
    # CEA-608-E 9.5.3.1
    assert describe((0x05, 0x01), *chars('NBC')) == 'XDS Channel Name: NBC'
    # CEA-608-E 9.5.3.2
    assert describe((0x05, 0x02), *chars('WNET')) == 'XDS Channel Station Call-Sign: WNET'
    assert describe((0x05, 0x02), *chars('KET 38')) == 'XDS Channel Station Call-Sign: KET Native Channel: 38'
    assert describe((0x05, 0x02), *chars('WGBH02')) == 'XDS Channel Station Call-Sign: WGBH Native Channel: 02'
    assert describe((0x05, 0x02), *chars('WGBH'), (0x00, 0x32)) == \
        'XDS Channel Station Call-Sign: WGBH Native Channel: 2'
    assert describe((0x05, 0x02), *chars('KCETTV')) == 'XDS Channel Station Call-Sign: KCETTV'
    # CEA-608-E Table 34
    assert describe((0x05, 0x03), (0x5E, 0x43)) == 'XDS Channel Tape Delay: 03:30'
    assert describe((0x05, 0x03), (0x7B, 0x77)) == 'XDS Channel Tape Delay: 23:59'
    # CEA-608-E Table 35
    assert describe((0x05, 0x04), (0x4F, 0x40), (0x41, 0x40)) == 'XDS Transmission Signal Identifier (TSID)'


def test_xds_time_of_day():
    # CEA-608-E 9.5.4.4
    assert time_of_day(32, 0x20, 12, 4, 3, 4) == 'XDS Time of day (UTC): TM 00:32D _SA Apr 12 1994 Tue'
    # CEA-608-E Table 15, 9.5.4.1
    assert time_of_day(30, 0x20 | 20, 19, 4, 1, 8) == 'XDS Time of day (UTC): TM 20:30D _SA Apr 19 1998 Sun'
    assert time_of_day(0, 0, 0x20 | 1, 3, 6, 6) == 'XDS Time of day (UTC): TM 00:00S _SL Mar 01 1996 Fri'
    assert time_of_day(0, 0, 1, 0x20 | 1, 1, 0) == 'XDS Time of day (UTC): TM 00:00S ZSA Jan 01 1990 Sun'
    assert time_of_day(0, 0, 1, 0x10 | 1, 1, 0) == 'XDS Time of day (UTC): TM 00:00S _TA Jan 01 1990 Sun'
    assert time_of_day(59, 0x20 | 23, 0x20 | 31, 0x30 | 12, 7, 63) == \
        'XDS Time of day (UTC): TM 23:59D ZTL Dec 31 2053 Sat'


def test_xds_time_of_day_days_and_months():
    # CEA-608-E Table 36
    for day in range(1, 8):
        assert time_of_day(0, 0, 1, 1, day, 0) == 'XDS Time of day (UTC): TM 00:00S _SA Jan 01 1990 ' + DAYS[day - 1]
    assert time_of_day(0, 0, 1, 1, 0, 0) == 'XDS Time of day (UTC): TM 00:00S _SA Jan 01 1990 --'
    # CEA-608-E Table 15
    for month in range(1, 13):
        assert time_of_day(0, 0, 1, month, 1, 0) == \
            'XDS Time of day (UTC): TM 00:00S _SA %s 01 1990 Sun' % MONTHS[month - 1]
    assert time_of_day(0, 0, 1, 0, 1, 0) == 'XDS Time of day (UTC): TM 00:00S _SA -- 01 1990 Sun'
    assert time_of_day(0, 0, 1, 13, 1, 0) == 'XDS Time of day (UTC): TM 00:00S _SA -- 01 1990 Sun'


def test_xds_local_time_zone():
    # CEA-608-E Table 38
    for hour in range(24):
        offset = -hour if hour < 12 else 24 - hour
        assert describe((0x07, 0x04), (0x40 | hour, 0x00)) == 'XDS Local Time Zone: %d no DST' % offset
        assert describe((0x07, 0x04), (0x60 | hour, 0x00)) == 'XDS Local Time Zone: %d observes DST' % offset


def test_xds_miscellaneous_packets():
    # CEA-608-E 9.5.4.2
    assert describe((0x07, 0x02), (0x5E, 0x54), (0x53, 0x44), (0x5E, 0x41)) == 'XDS Impulse Capture ID'
    # CEA-608-E Table 37
    assert describe((0x07, 0x03), (0x75, 0x00)) == 'XDS Supplemental Data Location'
    # CEA-608-E Table 39
    assert describe((0x07, 0x40), (0x5F, 0x40)) == 'XDS Out-of-Band Channel Number'
    # CEA-608-E Table 40
    assert describe((0x07, 0x41), (0x42, 0x40)) == 'XDS Channel Map Pointer'
    # CEA-608-E Table 41
    assert describe((0x07, 0x42), (0x4A, 0x40), (0x41, 0x00)) == 'XDS Channel Map Header Packet'
    # CEA-608-E Table 42
    assert describe((0x07, 0x43), (0x42, 0x40), *chars('KCET')) == 'XDS Channel Map Packet'


def test_xds_public_service():
    # CEA-608-E Table 43, Table 44
    wrsame = [(0x54, 0x4F), (0x52, 0x2D), (0x31, 0x30), (0x36, 0x30), (0x33, 0x37), (0x2D, 0x00), (0x2B, 0x30),
              (0x31, 0x2D)]
    assert describe((0x09, 0x01), *wrsame) == 'XDS Public Service - WRSAME message: %s' % wrsame
    # CEA-608-E 9.5.5.2
    assert describe((0x09, 0x02), *chars('SEVERE STORM')) == 'XDS Public Service - Weather: SEVERE STORM'


def test_xds_packets_this_decoder_does_not_describe():
    # CEA-608-E Table 14
    assert describe((0x0B, 0x01), (0x41, 0x42)) == 'Could not decode ---> XDS describes: 0b 01'
    assert describe((0x0D, 0x7F), (0x41, 0x42)) == 'Could not decode ---> XDS describes: 0d 7f'
    # CEA-608-E 9.4
    assert describe((0x01, 0x41), (0x41, 0x42)) == 'Could not decode ---> XDS describes: 01 41'
    # CEA-608-E 9.5
    assert describe((0x01, 0x0A), (0x41, 0x42)) == 'Could not decode ---> XDS describes: 01 0a'
    assert describe((0x05, 0x05), (0x41, 0x42)) == 'Could not decode ---> XDS describes: 05 05'
    assert describe((0x07, 0x05), (0x41, 0x42)) == 'Could not decode ---> XDS describes: 07 05'
    assert describe((0x09, 0x03), (0x41, 0x42)) == 'Could not decode ---> XDS describes: 09 03'
