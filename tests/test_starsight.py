"""Construction tests for the StarSight command decoders.

The five commands that carry guide data are verified against real captures, and
`docs/replay_commands.py` is the regression for those. The fourteen types the loader
implements but no capture contains cannot be tested that way, so they are tested the only
way left: build a command to the layout, decode it, and check the
fields come back. That proves the code matches the layout. It cannot prove the layout is
right - only a capture carrying one of these commands could do that.

    python3 -m pytest tests/test_starsight.py
"""

import os
import datetime
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

from lib.starsight import (COMMAND_TABLE, STARSIGHT_HEADER, STARSIGHT_SYNC,
                           STARSIGHT_TRAILER, decode_channel_data,
                           decode_daylight_saving, decode_region, decode_sequence_number,
                           decode_show_description, decode_show_list, decode_show_title,
                           decode_starsight_commands, decode_subscriber_reset,
                           decode_theme_names, decode_theme_sub_categories, decode_time,
                           starsight_crc, take_starsight_packets, repair_starsight_packet,
                           REGION_APPLY_NOW, starsight_time,
                           decode_region_name, decode_cycle_start,
                           decode_postal_code, decode_addressed, decode_keyed_record,
                           decode_timed_record, decode_sized_payload, decode_gate,
                           describe_starsight_command, new_guide, COMMAND_NAMES,
                           rating_name, tv_rating, ShowRating, _description_entry,
                           decode_station_node, STATION_ASSEMBLED, STATION_EPOCH,
                           STATION_TOTAL, STATION_DONE, STATION_REMAINING)


def packet(*commands):
    """ A whole packet around `commands`, with both checksums made correct """
    body = b''.join(commands)
    size = STARSIGHT_HEADER + len(body) + STARSIGHT_TRAILER
    head = bytes([STARSIGHT_SYNC]) + struct.pack('>H', size) \
        + struct.pack('>I', 3_400_000) + struct.pack('>H', 52)
    head += struct.pack('<H', starsight_crc(head) & 0xFFFF)
    packet = head + body
    return packet + struct.pack('<I', starsight_crc(packet))


# --- the checksums, which gate everything else -------------------------------------

def sequence(number=481_563):
    return bytes([20, 6]) + struct.pack('>I', number)


def title(show_id=1654, theme=644, name=b'Test Show'):
    return bytes([6, 8 + len(name), 0x00]) + struct.pack('>HH', show_id, theme) + name + b'\x00'


def test_both_checksums_verify():
    # a packet has to clear STARSIGHT_MIN_PACKET, so carry something real as well
    one = sequence() + title()
    built = packet(one)
    assert starsight_crc(built[0:9]) & 0xFFFF == built[9] | (built[10] << 8)
    assert starsight_crc(built) == 0

    found, consumed = take_starsight_packets(built)
    assert consumed == len(built)
    assert len(found) == 1
    assert [command_type for command_type, _ in found[0][2]] == [20, 6]


def test_a_flipped_bit_is_repaired():
    built = bytearray(packet(sequence() + title()))
    built[STARSIGHT_HEADER + 5] ^= 0x01
    found, _ = take_starsight_packets(bytes(built))
    assert len(found) == 1
    assert [command_type for command_type, _ in found[0][2]] == [20, 6]
    assert decode_sequence_number(found[0][2][0][1]) == 481_563


def test_a_lost_line_is_solved_for():
    original = packet(sequence() + title())
    built = bytearray(original)
    hole = STARSIGHT_HEADER + 4
    built[hole] = built[hole + 1] = 0
    found, _ = take_starsight_packets(bytes(built), lost=[hole, hole + 1])
    assert len(found) == 1
    assert decode_sequence_number(found[0][2][0][1]) == 481_563
    # the repair has to put back what was sent, not merely something that checksums
    assert repair_starsight_packet(bytes(built), [hole, hole + 1]) == original


def test_damage_beyond_the_budget_is_rejected():
    # three flipped bits is past what two bits of search can account for
    built = bytearray(packet(sequence() + title()))
    for offset in (STARSIGHT_HEADER + 5, STARSIGHT_HEADER + 9, STARSIGHT_HEADER + 14):
        built[offset] ^= 0x01
    assert take_starsight_packets(bytes(built))[0] == []

    # and so is a second lost line, which would leave the checksum nothing to verify with
    built = bytearray(packet(sequence() + title()))
    holes = [STARSIGHT_HEADER + 4, STARSIGHT_HEADER + 5,
             STARSIGHT_HEADER + 12, STARSIGHT_HEADER + 13]
    for offset in holes:
        built[offset] = 0
    assert repair_starsight_packet(bytes(built), holes) is None


# --- every type the loader implements is framed -------------------------------------

def test_every_implemented_type_is_framed():
    for command_type, (width, _, handler) in COMMAND_TABLE.items():
        if handler == 'none':
            continue
        length = 8
        body = bytes([command_type]) + (struct.pack('>H', length) if width == 2
                                        else bytes([length]))
        body += bytes(length - len(body))
        assert decode_starsight_commands(body, 0, len(body)) == [(command_type, body)], \
            'type %d does not frame' % command_type


# --- the five that captures confirm --------------------------------------------------

def test_show_title():
    command = bytes([6, 17, 0x00]) + struct.pack('>HH', 1654, 644) + b'Test Show\x00'
    assert decode_show_title(command) == (1654, 644, 'Test Show')


def test_show_description_extended_carries_rating_and_year():
    command = bytes([8, 20, 0x08]) + struct.pack('>H', 413) + bytes([0, 0]) \
        + bytes([0x4A, 0x0E, 94]) + b'Synopsis\x00'
    identifier, rating, text = decode_show_description(command)
    assert identifier == 413
    assert (rating.stars, rating.code, rating.advisories, rating.year) == (2, 5, 0x0E, 1994)
    assert text == 'Synopsis'
    assert rating_name(rating) == 'R'


def test_a_description_carries_its_ratings_and_rerun_into_the_json():
    rating = ShowRating(3, 3, 0x0C, 1972)
    entry = _description_entry(62, 'TV14 Bank robber and wife flee (R).', rating)
    assert entry == {'id': 62, 'text': 'TV14 Bank robber and wife flee (R).', 'rerun': True,
                     'tvRating': 'TV-14', 'stars': 3, 'rating': 3, 'ratingName': 'PG',
                     'year': 1972, 'advisories': ['violence', 'adult situations']}
    # TVM is the 1997 name for TV-MA, and a word that only starts like a rating is not one
    assert tv_rating('TVM A father and a son date the same woman.') == 'TV-MA'
    assert tv_rating('TV-PG Nashville: robbery suspect turns violent.') == 'TV-PG'
    assert tv_rating('TVs on sale.') is None
    assert rating_name(ShowRating(0, 9, 0, None)) is None


def test_show_list_slots_accumulate_duration():
    body = bytes([0]) + struct.pack('>H', 1061) + struct.pack('>I', 3_400_000) + bytes([2])
    body += bytes([0, 30]) + struct.pack('>H', 11)
    body += bytes([0, 60]) + struct.pack('>H', 22)
    command = bytes([5]) + struct.pack('>H', len(body) + 3) + body
    channel, slots = decode_show_list(command)
    assert channel == 1061 and len(slots) == 2
    assert slots[0][1] == 30 and slots[1][1] == 60
    assert (slots[1][0] - slots[0][0]).total_seconds() == 30 * 60


def test_time_and_daylight_saving():
    when, zone, saving = decode_time(bytes([1, 8]) + struct.pack('>I', 3_312_168)
                                    + bytes([0x95, 27]))
    assert (zone, saving) == (-5, True) and when.year == 1998
    starts, ends = decode_daylight_saving(bytes([2, 10]) + struct.pack('>I', 3_291_960)
                                         + struct.pack('>I', 3_584_220))
    assert (starts.month, ends.month) == (4, 10)


def test_sequence_number():
    assert decode_sequence_number(bytes([20, 6]) + struct.pack('>I', 481_563)) == 481_563


# --- the fourteen no capture contains ------------------------------------------------

def test_channel_data_splits_the_call_sign_from_the_network():
    command = bytes([4, 16, 0, 0x91, 0xFF, 0x02, 0x04, 0xF0]) + b'WKYT-CBS'
    data = decode_channel_data(command)
    assert data.channel == ((0x11 << 8) | 0xFF)
    assert data.number == 0x104          # byte 3 bit 7 supplies bit 8
    assert data.call_sign == 'WKYT'      # the loader splits on the first dash
    assert data.network == 'CBS'
    assert data.label == 'WKYT'          # mask 0xF0 takes the first four characters
    assert data.shows_call_sign is True


def test_channel_data_keeps_a_call_sign_with_no_network():
    command = bytes([4, 16, 0, 0x00, 0x05, 0x00, 0x09, 0xC0]) + b'KYTV\x00\x00\x00\x00'
    data = decode_channel_data(command)
    assert data.call_sign == 'KYTV' and data.network == ''
    # a dash before the fourth character is part of the name, not a separator
    early = bytes([4, 16, 0, 0, 5, 0, 9, 0xC0]) + b'K-9\x00\x00\x00\x00\x00'
    assert decode_channel_data(early).call_sign == 'K-9'


def test_theme_names_walk_variable_length_entries():
    entries = bytes([5, 0, 7]) + b'Movies\x00' + bytes([9, 0, 6]) + b'Sport\x00'
    command = bytes([11]) + struct.pack('>H', 5 + len(entries)) + bytes([3, 2]) + entries
    version, names = decode_theme_names(command)
    assert version == 3
    assert names == [(5, 'Movies'), (9, 'Sport')]


def test_theme_sub_category_lists_the_theme_ids_it_names():
    entry = bytes([3 + 4 + 5, 0, 2]) + struct.pack('>HH', 644, 97) + b'Drama'
    command = bytes([12]) + struct.pack('>H', 5 + len(entry)) + bytes([5, 0x81]) + entry
    assert decode_theme_sub_categories(command) == (5, [('Drama', [644, 97])])


def test_a_theme_is_named_by_its_category_and_sub_category():
    guide = new_guide()
    category = bytes([5, 0, 7]) + b'Movies\x00'
    describe_starsight_command(11, bytes([11]) + struct.pack('>H', 5 + len(category))
                               + bytes([3, 1]) + category, guide)
    entry = bytes([3 + 2 + 5, 0, 1]) + struct.pack('>H', 644) + b'Drama'
    describe_starsight_command(12, bytes([12]) + struct.pack('>H', 5 + len(entry))
                               + bytes([5, 1]) + entry, guide)
    assert guide['theme_names'] == {644: 'Movies / Drama'}


def test_region_carries_the_whole_channel_lineup():
    entries = (bytes([0x91, 0xFF, 0x04, 0x00])    # id 0x11FF, number 0x104 via the top bit
               + bytes([0x00, 0x09, 0x2A, 0x00]))  # id 9, number 42
    command = bytes([3]) + struct.pack('>H', 11 + len(entries)) + struct.pack('>H', 300) \
        + bytes([REGION_APPLY_NOW]) + struct.pack('>I', 3_597_120) + bytes([2]) + entries
    region = decode_region(command)
    assert region.region == 300
    assert region.immediate is True
    assert region.effective == starsight_time(3_597_120)
    assert region.channels == [(0x11FF, 0x104), (9, 42)]


def test_region_stops_at_a_truncated_entry():
    command = bytes([3, 0, 15]) + struct.pack('>H', 1) + bytes([0]) \
        + struct.pack('>I', 0) + bytes([4]) + bytes([0, 7, 3, 0]) + bytes([0, 8])
    assert decode_region(command).channels == [(7, 3)]


def test_type_9_names_a_region():
    # f9 23 fd a7 is 'News', the four byte title KET sent for its local news
    command = bytes([9, 8]) + struct.pack('>H', 300) + bytes.fromhex('f923fda7')
    assert decode_region_name(command) == (300, 'News')
    # byte 1 is the length, so bytes past it are not part of the name
    assert decode_region_name(bytes([9, 6]) + command[2:]).name is None


def test_type_10_is_nine_bytes():
    assert decode_cycle_start(bytes(range(9))) == bytes(range(9))
    assert decode_cycle_start(bytes(range(8))) is None


def test_type_31_lists_the_regions_for_a_postal_code():
    command = (bytes([31, 0, 20]) + b'40506\x00' + bytes([0])
               + bytes([2, 1]) + struct.pack('>HH', 300, 301) + struct.pack('>H', 7)
               + b'\xAA\xBB')
    data = decode_postal_code(command)
    assert data.code == '40506'
    assert data.broadcast == [300, 301] and data.cable == [7]
    assert data.trailer == b'\xAA\xBB'


def test_type_37_reads_a_day_of_entries_after_its_regions():
    body = (bytes([0x05]) + struct.pack('>H', 3) + struct.pack('>HHH', 11, 22, 33)
            + struct.pack('>I', 3_597_120) + bytes([2, 1]) + struct.pack('>H', 9)
            + bytes([2]) + struct.pack('>H', 10))
    data = decode_addressed(bytes([37]) + struct.pack('>H', 3 + len(body)) + body)
    assert data.version == 5
    assert data.regions == [11, 22, 33]
    assert data.day == starsight_time(3_597_120)
    assert data.entries == [(1, 9), (2, 10)]
    # a table that runs off the end is refused rather than half read
    assert decode_addressed(bytes([37, 0, 9, 0]) + struct.pack('>H', 9) + b'xx') is None


def test_type_38_walks_its_three_lengths():
    command = (bytes([38, 0, 20]) + struct.pack('>H', 77) + bytes([0x11, 0x22, 0x03, 2, 4])
               + struct.pack('>H', 999) + b'AAA' + b'BB' + b'CCCC')
    data = decode_keyed_record(command)
    assert data.identifier == 77
    assert data.flags == (0x11, 0x22, 0x03)      # bytes 5, 6, 7 as sent
    assert data.parts == [b'AAA', b'BB', b'CCCC']  # lengths from byte 7 low bits, 8 and 9


def test_type_39_reads_its_four_fields():
    command = (bytes([39, 0, 12]) + struct.pack('>H', 5) + struct.pack('>H', 600)
               + struct.pack('>I', 3_597_120) + bytes([0x2A]))
    data = decode_timed_record(command)
    assert (data.identifier, data.value, data.when, data.trailer) == (5, 600, 3_597_120, 0x2A)


def test_type_40_takes_its_length_from_the_header():
    command = bytes([40]) + struct.pack('>H', 12) + struct.pack('>H', 8) + bytes([0x77]) \
        + b'payload!'
    data = decode_sized_payload(command)
    assert data.identifier == 8
    # the length counts the whole command, so the payload is bytes 6 to 11 and the two
    # bytes after it are the next command's, not this one's
    assert data.payload == b'payloa'

def test_type_42_acts_on_only_two_of_four_values():
    def gate(bits):
        return decode_gate(bytes([42, 0, 9, 0, 0, 0, 0, 0, bits << 4]))
    assert gate(0) is False
    assert gate(1) is True
    assert gate(2) is None and gate(3) is None


def segment(part, last, data, message=7):
    return bytes([36]) + struct.pack('>H', 5 + len(data)) + bytes([part << 4 | last, message]) \
        + data


def test_a_segmented_command_is_joined_and_decoded():
    entries = bytes([5, 0, 7]) + b'Movies\x00' + bytes([9, 0, 6]) + b'Sport\x00'
    inner = bytes([11]) + struct.pack('>H', 5 + len(entries)) + bytes([3, 2]) + entries
    whole = inner + struct.pack('<I', starsight_crc(inner))

    guide = new_guide()
    assert describe_starsight_command(36, segment(0, 1, whole[:10]), guide) \
        == ['Segmented Command message 7    part 1 of 2']
    lines = describe_starsight_command(36, segment(1, 1, whole[10:]), guide)
    assert lines[1].startswith('Theme Category')
    assert guide['theme_categories'] == {5: 'Movies', 9: 'Sport'}

    # a part out of order, or a last part that does not check, gives nothing
    for parts in ([segment(1, 1, whole[10:]), segment(0, 1, whole[:10])],
                  [segment(0, 1, whole[:10]), segment(1, 1, whole[10:-1] + bytes([whole[-1] ^ 1]))]):
        guide = new_guide()
        for command in parts:
            describe_starsight_command(36, command, guide)
        assert guide['theme_categories'] == {}


def test_a_command_with_bit_7_set_is_not_decoded():
    guide = new_guide()
    command = bytes([0x86]) + title()[1:]
    assert 'ignores' in describe_starsight_command(6, command, guide)[0]
    assert guide['titles'] == {}


def test_station_node_finds_its_clock_wherever_it_sits():
    """ the two stations put it at different offsets, so it is found and not indexed """
    def build(length, clock_at, assembled, clock):
        command = bytearray(length)
        command[0] = 21
        command[1:3] = struct.pack('>H', length)
        command[STATION_ASSEMBLED:STATION_ASSEMBLED + 4] = struct.pack('>I', assembled)
        command[clock_at:clock_at + 4] = struct.pack('>I', clock)
        return bytes(command)

    assembled, clock = 768_050_037, 768_051_237     # KCET's own values
    for length, clock_at in ((733, 48), (74, 58)):  # KET's form, then KCET's
        node = decode_station_node(build(length, clock_at, assembled, clock))
        assert node.assembled == STATION_EPOCH + datetime.timedelta(seconds=assembled)
        assert node.clock == STATION_EPOCH + datetime.timedelta(seconds=clock)

    # two fields that both read as a time leave it ambiguous, and it says so rather
    # than picking one
    command = bytearray(build(80, 58, assembled, clock))
    command[40:44] = struct.pack('>I', clock + 60)
    assert decode_station_node(bytes(command)).clock is None

    # and a command with no such field at all
    assert decode_station_node(build(80, 58, assembled, assembled - 1)).clock is None


def test_station_node_reads_the_cycle_counts_only_when_they_add_up():
    def build(total, done, remaining):
        command = bytearray(74)
        command[0], command[3] = 21, 1
        command[1:3] = struct.pack('>H', 74)
        command[STATION_ASSEMBLED:STATION_ASSEMBLED + 4] = struct.pack('>I', 768_050_037)
        command[STATION_TOTAL:STATION_TOTAL + 2] = struct.pack('>H', total)
        command[STATION_DONE:STATION_DONE + 2] = struct.pack('>H', done)
        command[STATION_REMAINING:STATION_REMAINING + 2] = struct.pack('>H', remaining)
        return bytes(command)

    node = decode_station_node(build(487, 67, 420))
    assert node.version == 1
    assert (node.total, node.done, node.remaining) == (487, 67, 420)

    # the three are only believed because they agree; one that does not is not reported
    assert decode_station_node(build(487, 67, 419)).total is None
    assert decode_station_node(build(0, 0, 0)).total is None


def test_subscriber_reset_names_each_bit():
    assert decode_subscriber_reset(bytes([13, 8, 0x00])) == []
    assert len(decode_subscriber_reset(bytes([13, 8, 0x03]))) == 2


def test_every_loader_only_type_reaches_the_log_through_a_real_packet():
    """ the nine fire on no capture, so the dispatch has to be exercised deliberately """
    built = [
        bytes([9, 8]) + struct.pack('>H', 300) + bytes.fromhex('f923fda7'),
        bytes([10, 9, 0, 0, 0, 0, 0, 0, 0]),
        bytes([31, 0, 20]) + b'40506\x00' + bytes([0, 2, 1])
            + struct.pack('>HHH', 300, 301, 7) + b'\xAA\xBB',
        segment(0, 1, b'part'),
        bytes([37, 0, 17, 0x05]) + struct.pack('>HHHH', 3, 11, 22, 33)
            + struct.pack('>I', 3_597_120) + bytes([0]),
        bytes([38, 0, 21]) + struct.pack('>H', 77) + bytes([0x11, 0x22, 0x03, 2, 4])
            + struct.pack('>H', 999) + b'AAABBCCCC',
        bytes([39, 0, 12]) + struct.pack('>H', 5) + struct.pack('>H', 600)
            + struct.pack('>I', 3_597_120) + bytes([0x2A]),
        bytes([40]) + struct.pack('>HH', 12, 8) + bytes([0x77]) + b'payloa',
        bytes([42, 0, 9, 0, 0, 0, 0, 0, 0x00]),
    ]
    for command in built:
        # a title as well, so the packet clears STARSIGHT_MIN_PACKET on its own merits
        found, _ = take_starsight_packets(packet(sequence() + title() + command))
        assert len(found) == 1, 'type %d did not frame' % command[0]
        types = [command_type for command_type, _ in found[0][2]]
        assert types == [20, 6, command[0]]

        guide = new_guide()
        lines = describe_starsight_command(command[0], command, guide)
        assert lines and lines[0].startswith(COMMAND_NAMES.get(command[0], 'type %d' % command[0]))
        if command[0] != 36:
            assert len(guide['loader_commands'][command[0]]) == 1


def test_short_commands_return_none_rather_than_raising():
    assert decode_channel_data(bytes([4, 4, 0, 0])) is None
    assert decode_theme_names(bytes([11, 0, 5, 0, 0])) is None
    assert decode_region(bytes([3, 0, 4])) is None
