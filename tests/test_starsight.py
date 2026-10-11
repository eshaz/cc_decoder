"""Tests for the StarSight command decoders.

Where a capture has carried a command, the test decodes that command as it came off the
wire, as hex. The types no capture contains are tested the only way left: build a command
to the layout, decode it, and check the fields come back. That proves the code matches the
layout. It cannot prove the layout is right - only a capture carrying one could do that.
`docs/replay_commands.py` replays whole captures.

    python3 -m pytest tests/test_starsight.py
"""

import os
import datetime
import json
import struct
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

from lib.starsight import (COMMAND_TABLE, COMMANDS_SEEN, STARSIGHT_HEADER, STARSIGHT_SYNC,
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
                           STATION_TOTAL, STATION_DONE, STATION_REMAINING,
                           write_starsight_html, write_starsight_json, read_starsight_json,
                           decode_starsight, merge_guides, description_advisories,
                           OTHER_COMMANDS, listings, theme_labels, channel_name,
                           decode_news_service, decode_news_menu, decode_news_page,
                           news_text, decode_addressed_payload, region_tier)


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


def title(show_id=1654, theme=644, name=b'Test Show', flags=0x00):
    return bytes([6, 8 + len(name), flags]) + struct.pack('>HH', show_id, theme) + name + b'\x00'


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

def test_every_implemented_or_captured_type_is_framed():
    for command_type, (width, _, handler) in COMMAND_TABLE.items():
        if handler == 'none' and command_type not in COMMANDS_SEEN:
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


# --- the lineup: Channel Data, Region, Region Name and Postal Code ---------------------

# As the 1997 Ohio tape sent them.
KDKA = bytes.fromhex('04100100130002004b444b412d434253')
HEADLINE = bytes.fromhex('041001000a02009a484541444c494e45')
SCI_FI = bytes.fromhex('04100105ee0200e05343492d46490000')


def test_channel_data_splits_a_station_into_call_sign_and_network():
    data = decode_channel_data(KDKA)
    assert (data.channel, data.number, data.call_sign, data.network) == (19, 2, 'KDKA', 'CBS')
    assert (data.label, data.shows_call_sign) == ('', False)
    assert channel_name(data) == '2 KDKA'


def test_channel_data_keeps_a_cable_service_name_whole():
    data = decode_channel_data(HEADLINE)
    # no channel number, bit 1 of byte 5, and mask 0x9a picks H, D, L and N
    assert (data.number, data.call_sign, data.label, data.shows_call_sign) == \
        (0, 'HEADLINE', 'HDLN', True)
    assert channel_name(data) == 'HEADLINE' and channel_name(data, short=True) == 'HDLN'
    # a cable service's dash is part of its name, not a network
    data = decode_channel_data(SCI_FI)
    assert (data.call_sign, data.network, data.label) == ('SCI-FI', '', 'SCI')


def test_channel_data_puts_bit_8_of_the_number_in_byte_3():
    data = decode_channel_data(bytes([4, 16, 0, 0x91, 0xFF, 0x00, 0x04, 0x00]) + b'WKYT-CBS')
    assert data.channel == ((0x11 << 8) | 0xFF)
    assert data.number == 0x104
    assert (data.call_sign, data.network) == ('WKYT', 'CBS')


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
    assert names == [(5, 'Movies', 0), (9, 'Sport', 0)]


def test_theme_sub_category_lists_the_theme_ids_it_names():
    entry = bytes([3 + 4 + 5, 0, 2]) + struct.pack('>HH', 644, 97) + b'Drama'
    command = bytes([12]) + struct.pack('>H', 5 + len(entry)) + bytes([5, 0x81]) + entry
    assert decode_theme_sub_categories(command) == (5, [('Drama', [644, 97], 0)])


def test_a_theme_is_named_by_its_category_and_sub_category():
    guide = new_guide()
    category = bytes([5, 0, 7]) + b'Movies\x00'
    describe_starsight_command(11, bytes([11]) + struct.pack('>H', 5 + len(category))
                               + bytes([3, 1]) + category, guide)
    entry = bytes([3 + 2 + 5, 0, 1]) + struct.pack('>H', 644) + b'Drama'
    describe_starsight_command(12, bytes([12]) + struct.pack('>H', 5 + len(entry))
                               + bytes([5, 1]) + entry, guide)
    assert theme_labels(guide) == {644: 'Movies / Drama'}


# WHYY's of 7 December 1997: the categories, then the Comedy menu
CATEGORIES = bytes.fromhex(
    '0b00dbd00b0101074d6f76696573000200155370656369616c732f4d696e69205365726965730003010753'
    '706f727473000400174368696c6472656e27732050726f6772616d6d696e67000500194865616c74682f53'
    '6369656e63652f456475636174696f6e000600134e6577732f54616c6b2f4d6167617a696e650007011645'
    '6e7465727461696e6d656e742f5661726965747900080107436f6d65647900090016416374696f6e2f4d79'
    '73746572792f486f72726f72000a000e4472616d612f526f6d616e6365000b000e4d697363656c6c616e65'
    '6f757300')
COMEDY = bytes.fromhex(
    '0c00ae08053e0018005b006000730078008b009000a300b6012c012d013b01480149014c01600162016801'
    'c10240024e0259025d026b026c416c6c20436f6d65647900130105005b012c013b016202404d6f76696500'
    '1401050060007801480149026b536974636f6d001e01070073008b00a3014c01600168024e53686f772f53'
    '70656369616c00260009013b01480149014c0160016201680259026c436c6f7365642043617074696f6e65'
    '6400')


def test_the_theme_menus_label_a_theme_by_what_says_more_than_all():
    guide, _ = replayed([CATEGORIES, COMEDY])
    assert guide['theme_categories'][8] == 'Comedy' and len(guide['theme_categories']) == 11
    assert [(name, len(themes)) for name, themes, _ in guide['theme_lists'][8]] == [
        ('All Comedy', 24), ('Movie', 5), ('Sitcom', 5), ('Show/Special', 7),
        ('Closed Captioned', 9)]
    labels = theme_labels(guide)
    assert labels[0x5B] == 'Comedy / Movie'        # in All Comedy and Movie
    assert labels[0x60] == 'Comedy / Sitcom'
    assert labels[0x90] == 'Comedy'                # in All Comedy alone


def test_region_carries_the_whole_channel_lineup():
    entries = (bytes([0x91, 0xFF, 0x04, 0x00])    # id 0x11FF, number 0x104 via the top bit
               + bytes([0x00, 0x09, 0x2A, 0x00]))  # id 9, number 42
    command = bytes([3]) + struct.pack('>H', 11 + len(entries)) + struct.pack('>H', 300) \
        + bytes([REGION_APPLY_NOW]) + struct.pack('>I', 3_597_120) + bytes([2]) + entries
    region = decode_region(command)
    assert region.region == 300
    assert region.immediate is True
    assert region.effective == starsight_time(3_597_120)
    assert region.channels == [(0x11FF, 0x104, 0), (9, 42, 0)]
    assert region.cable is False


def test_region_stops_at_a_truncated_entry():
    command = bytes([3, 0, 15]) + struct.pack('>H', 1) + bytes([0]) \
        + struct.pack('>I', 0) + bytes([4]) + bytes([0, 7, 3, 0]) + bytes([0, 8])
    assert decode_region(command).channels == [(7, 3, 0)]


def test_a_captured_region_is_an_antenna_or_a_cable_lineup_with_tiers():
    columbus = decode_region(bytes.fromhex(
        '03007b1cd200000000001c003a0200006903000095040000c90500010a0600012d07000164080001c50a'
        '0001f60b0002520d00027b10000293120002ad140002c8160002d3170002e4180003031a0003d31b0003'
        '1c1c00089e220004122400039a2c0003a02d0003b5310003c033000404340004c33500211b4400'))
    assert (columbus.region, columbus.cable, columbus.effective) == (7378, False,
                                                                    starsight_time(0))
    assert columbus.channels[:3] == [(58, 2, 0), (105, 3, 0), (149, 4, 0)]
    assert len(columbus.channels) == 28
    cable = decode_region(bytes.fromhex('03000f000220000000000102e50200'))
    assert (cable.region, cable.cable, cable.channels) == (2, True, [(741, 2, 0)])
    assert [region_tier(tier) for tier in (0, 1, 2, 3, 0x82)] == [
        'basic', 'extended', 'premium', 'pay per view', 'premium (0x82)']


def test_type_9_names_a_region():
    command = bytes.fromhex('090d1cd2a773a115d2c274b380')
    assert decode_region_name(command) == (7378, 'columbus, oh')
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
    assert data.area == 0xAA and data.unread == b'\x00\xBB'
    # as Columbus, Ohio's 43235 went out: the Columbus antenna lineup, news area 0x58
    data = decode_postal_code(bytes.fromhex('1f00103433323335009501001cd25801'))
    assert (data.code, data.broadcast, data.cable, data.area, data.unread) == \
        ('43235', [7378], [], 0x58, b'\x95\x01')


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


def test_an_encrypted_command_is_not_decoded():
    guide = new_guide()
    command = bytes([0x86]) + title()[1:]
    assert describe_starsight_command(6, command, guide)[0].endswith('encrypted with key 0')
    assert guide['titles'] == {} and len(guide['encrypted']) == 1


def test_an_encrypted_show_list_still_reads_its_header():
    # WHYY, 27 December 1995: channel 40's day, its 24 slots enciphered with key 1
    command = bytes.fromhex(
        'c5007b01002800201fc0187adf89cc584b8c25afbb330bf4ed8a474f6d9b8ecb2d5e7fe8c6cb33488a5e'
        'f21a671810655e6ab7956e1239b1fd89239c7c2d6bf03c56c77ae29b53e14112236f474218713f234c5d'
        '2aeb320d092eb7a98a3e3662a380b9d0a4e4038616cffe442e30c0933aa0af6abdecfa4c67a68a')
    guide = new_guide()
    assert describe_starsight_command(5, command, guide) == [
        'Show List    ch 40     1996-01-02 00:00 GMT  24 slots, encrypted with key 1']
    assert not guide['blocks'] and guide['encrypted'][0].channel == 40


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


def test_station_node_layout_4_leaves_its_assembly_time_unset():
    # Nickelodeon's of 21 May 1998, as far as its clock
    head = bytes.fromhex('1502e704019500883561cdf612cea600000000003564187935641882000000001105'
                         '000000000005000000060000000635642cd8')
    node = decode_station_node(head + bytes(743 - len(head)))
    assert (node.version, node.software, node.stream) == (4, 405, 136)
    assert node.assembled is None
    assert node.clock == datetime.datetime(1998, 5, 21, 13, 32, 8)


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


# WHYY's of 3 January 1998 at 05:36:40, all zero but its first 64 bytes, the three times at
# 420 and its inventory from 432 to the end
WHYY_STATION_HEAD = ('1502dd030195000634a41ab934aae1700000000034a87d2d34adbcff0002000002190000'
                     '00000014000000060000000634adce6800000000059f000000000000')
WHYY_FULL_DAY = '0010' '02e5' '0305' '051a' '0551' '0684' '0686' '0687' '0688'
WHYY_INVENTORY = (
    '009b' '0185' '1dd1' '2539' + '0175' * 7 + '0084' '0175' '0000'   # what it holds
    + '0006' '0e'                                                   # stream id, 14 tables
    + '00' * 18 * 4                                                 # nothing missing
    + WHYY_FULL_DAY * 7
    + '0101' '0001' '0005' '0007' '0009' '000b' '000d' '000e' '000f'  # day 7, part loaded
    + WHYY_FULL_DAY
    + '0185' '0001' '0002' '0005' '0007' '0008' '0009' '000a' '000b'  # day 9, empty
    + '00000000' '21c9' '34adc775' '00000000' '00000000')


def whyy_station_node():
    command = bytearray(733)
    command[:64] = bytes.fromhex(WHYY_STATION_HEAD)
    command[420:432] = bytes.fromhex('349e9754' '34a41ec2' '12cea600')
    command[432:] = bytes.fromhex(WHYY_INVENTORY)
    return bytes(command)


def test_station_node_reads_its_inventory():
    node = decode_station_node(whyy_station_node())
    stock = node.inventory
    assert (stock.regions.held, stock.channels.held, stock.titles.held,
            stock.descriptions.held) == (155, 389, 7633, 9529)
    assert stock.titles.missing == 0 and stock.titles.first_missing == ()
    assert [day.held for day in stock.days] == [373] * 7 + [132, 373, 0]
    assert [day.missing for day in stock.days] == [16] * 7 + [257, 16, 389]
    assert stock.days[0].first_missing == (741, 773, 1306, 1361, 1668, 1670, 1671, 1672)
    assert stock.days[7].first_missing == (1, 5, 7, 9, 11, 13, 14, 15)
    # a day's channels, with a schedule or without, are all the channels
    assert all(day.held + day.missing == stock.channels.held for day in stock.days)
    assert node.daily == datetime.datetime(1998, 1, 3, 5, 7, 1)
    assert node.minutes == 1439 and node.minute_maps == ((), ())


def test_station_node_inventory_is_ten_bytes_later_in_layout_4():
    # Nickelodeon's of 21 May 1998 at 17:29:50, while a tenth day was loading
    head = ('1502e704019500883561cdf612cea600000000003564187935641882000000003dbe0000'
            '0000000500000000000000003564648e00000000059c000000000000')
    inventory = ('024e' '03e2' '2ade' '4397' + '03c8' * 9 + '03c1' + '0088' '0e'
                 + '00' * 36
                 + '0201' '007d' '0087' '0192' '01bc' '01f9' '0207' '021a' '0223'
                 + '065a' '0008' '0016' '0019' '001e' '002a' '002b' '003b' '0051'
                 + ('001a' '0143' '01ba' '02e5' '0305' '20c1' '2156' '29c0' '3027') * 9
                 + '0021' '0143' '01ba' '02e5' '0305' '20c1' '2156' '29c0' '3027'
                 + '00000000' '4d9f' '3563f316' '00000006' '00000005')
    command = bytearray(743)
    command[:64] = bytes.fromhex(head)
    command[420:424] = bytes.fromhex('12cea600')
    command[430:442] = bytes.fromhex('3561cdf6' '35510087' '355c617b')
    command[442:] = bytes.fromhex(inventory)
    node = decode_station_node(bytes(command))
    stock = node.inventory
    assert (stock.regions.held, stock.channels.held) == (590, 994)
    assert (stock.titles.held, stock.titles.missing) == (10974, 513)
    assert stock.titles.first_missing[:2] == (0x007D, 0x0087)
    assert (stock.descriptions.held, stock.descriptions.missing) == (17303, 1626)
    assert [day.held for day in stock.days] == [968] * 9 + [961]
    assert [day.missing for day in stock.days] == [26] * 9 + [33]
    assert node.daily == datetime.datetime(1998, 5, 21, 9, 25, 42)
    assert node.minutes == 1436


def test_station_node_inventory_needs_fourteen_tables_and_its_own_stream():
    command = bytearray(whyy_station_node())
    command[462] = 13
    assert decode_station_node(bytes(command)).inventory is None
    command = bytearray(whyy_station_node())
    command[461] = 7
    assert decode_station_node(bytes(command)).inventory is None


def test_station_node_minute_maps_count_back_from_the_bit_before_the_pointer():
    command = bytearray(whyy_station_node())
    command[56:58] = (1435).to_bytes(2, 'big')
    command[58 + 1434 // 8] |= 0x80 >> (1434 % 8)    # the bit before the pointer: now
    command[58 + 1424 // 8] |= 0x80 >> (1424 % 8)    # ten minutes before it
    command[58 + 1439 // 8] |= 0x80 >> (1439 % 8)    # past the pointer: a day ago, less 5
    command[238] = 0x80                              # the second map's first bit
    node = decode_station_node(bytes(command))
    assert node.minutes == 1435
    assert node.minute_maps == ((0, 10, 1435), (1434,))
    # a pointer outside 1,433-1,440 is not a minute map
    command[56:58] = (1432).to_bytes(2, 'big')
    assert decode_station_node(bytes(command)).minute_maps is None


def test_station_node_software_200_has_no_daily_time():
    # WVIZ's of 20 December 1995 is 721 bytes and ends before it
    command = bytearray(whyy_station_node()[:721])
    command[1:3] = (721).to_bytes(2, 'big')
    node = decode_station_node(bytes(command))
    assert node.daily is None and node.inventory is not None


def test_the_page_gives_a_minute_map_as_gmt_minutes(tmp_path):
    command = bytearray(whyy_station_node())
    command[56:58] = (1435).to_bytes(2, 'big')
    for index in (1434, 1433, 1432, 1420):   # now, the two minutes before, and 14 before
        command[58 + index // 8] |= 0x80 >> (index % 8)
    guide, _ = replayed([title(), bytes(command)])
    # the clock is 05:36:40, so the bit before the pointer is 05:36
    assert '<td>05:22, 05:34-05:36</tbody>' in rendered(tmp_path, guide)


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
        kept = {9: guide['region_names'], 31: guide['postal_codes'], 36: [None]}.get(
            command[0], guide['loader_commands'][command[0]])
        assert len(kept) == 1


def test_short_commands_return_none_rather_than_raising():
    assert decode_channel_data(bytes([4, 4, 0, 0])) is None
    assert decode_theme_names(bytes([11, 0, 5, 0, 0])) is None
    assert decode_region(bytes([3, 0, 4])) is None


# --- the page shows everything the guide holds ---------------------------------------

def show_list(channel, *slots):
    """ A Show List for `channel` from minute 3,400,000, its slots given as bytes """
    body = bytes([0]) + struct.pack('>HI', channel, 3_400_000) + bytes([len(slots)]) \
        + b''.join(slots)
    return bytes([5]) + struct.pack('>H', len(body) + 3) + body


def channel_data():
    """ A station: WKYT, a CBS affiliate, on channel 260 """
    return bytes([4, 16, 0, 0x91, 0xFF, 0x00, 0x04, 0x00]) + b'WKYT-CBS'


def description(advisories=0x0C, text=b'TV14 Bank robber and wife flee (R).'):
    """ The extended form: 3 stars and PG in byte 7, 1972 in byte 9 """
    return bytes([8, 11 + len(text), 0x08]) + struct.pack('>H', 62) + bytes([0, 0]) \
        + bytes([0x66, advisories, 72]) + text + b'\x00'


def station_node():
    """ KCET's 74 byte form, its clock at 58, 67 of 487 done, and some text to read """
    command = bytearray(74)
    command[0], command[3] = 21, 1
    command[1:3] = struct.pack('>H', 74)
    command[STATION_ASSEMBLED:STATION_ASSEMBLED + 4] = struct.pack('>I', 768_050_037)
    command[58:62] = struct.pack('>I', 768_051_237)
    command[STATION_TOTAL:STATION_TOTAL + 2] = struct.pack('>H', 487)
    command[STATION_DONE:STATION_DONE + 2] = struct.pack('>H', 67)
    command[STATION_REMAINING:STATION_REMAINING + 2] = struct.pack('>H', 420)
    command[64:69] = b'KCET*'
    return bytes(command)


def uncaptured():
    """ One of every command no capture carries, and a schedule and titles to hang them on """
    categories = bytes([5, 0, 7]) + b'Movies\x00' + bytes([9, 0, 6]) + b'Sport\x00'
    drama = bytes([3 + 4 + 5, 0, 2]) + struct.pack('>HH', 644, 97) + b'Drama'
    sub_category = bytes([12]) + struct.pack('>H', 5 + len(drama)) + bytes([5, 1]) + drama
    sub_category += struct.pack('<I', starsight_crc(sub_category))
    lineup = bytes([0x91, 0xFF, 0x04, 0x00]) + bytes([0x00, 0x09, 0x2A, 0x00])
    return [
        bytes([3]) + struct.pack('>H', 11 + len(lineup)) + struct.pack('>H', 300)
        + bytes([REGION_APPLY_NOW]) + struct.pack('>I', 3_597_120) + bytes([2]) + lineup,
        channel_data(),
        bytes([11]) + struct.pack('>H', 5 + len(categories)) + bytes([3, 2]) + categories,
        segment(0, 1, sub_category[:10]), segment(1, 1, sub_category[10:]),
        bytes([13, 8, 0x03, 0, 0, 0, 0, 0]),
        station_node(),
        bytes([9, 8]) + struct.pack('>H', 300) + bytes.fromhex('f923fda7'),
        bytes([10, 9, 0xA1, 0xB2, 0xC3, 0xD4, 0xE5, 0xF6, 0x07]),
        bytes([31, 0, 20]) + b'40506\x00' + bytes([0, 2, 1])
        + struct.pack('>HHH', 300, 301, 7) + b'\xAA\xBB',
        bytes([37, 0, 23, 0x05]) + struct.pack('>HHHH', 3, 11, 22, 33)
        + struct.pack('>I', 3_597_120) + bytes([2, 1, 0, 9, 2, 0, 10]),
        bytes([38, 0, 21]) + struct.pack('>H', 77) + bytes([0x11, 0x22, 0x03, 2, 4])
        + struct.pack('>H', 999) + b'AAABBCCCC',
        bytes([39, 0, 12]) + struct.pack('>HHI', 5, 600, 3_597_120) + bytes([0x2A]),
        bytes([40]) + struct.pack('>HH', 12, 8) + bytes([0x77]) + b'payloa',
        bytes([42, 0, 9, 0, 0, 0, 0, 0, 0x10]),
        # joined in progress, then pay per view with a description and a show group, then
        # a slot setting a bit nothing names
        show_list(0x11FF, bytes([0x01, 30]) + struct.pack('>H', 1654),
                  bytes([0xE0, 60]) + struct.pack('>HHH', 1654, 62, 5)),
        show_list(9, bytes([0x04, 30]) + struct.pack('>H', 77)),
        title(flags=0x60),
        bytes([6, 11, 0xC0]) + struct.pack('>HH', 77, 97) + bytes.fromhex('f923fda7'),
        bytes([6, 11, 0x80]) + struct.pack('>HH', 78, 97) + b'\xff\xff\xff\xff',
        description(),
    ]


def replayed(commands):
    """ The guide and command counts a stream of commands leaves behind """
    guide, counts = new_guide(), Counter()
    for command in commands:
        counts[command[0] & 0x3F] += 1
        describe_starsight_command(command[0] & 0x3F, command, guide)
    return guide, counts


def rendered(tmp_path, guide, counts=None):
    write_starsight_html(str(tmp_path / 'out'), guide, counts)
    return (tmp_path / 'out.starsight.html').read_text()


def test_the_page_shows_the_lineup_and_labels_the_grid_with_it(tmp_path):
    page = rendered(tmp_path, *replayed(uncaptured()))
    effective = starsight_time(3_597_120).strftime('%Y-%m-%d %H:%M')
    # the region's name comes from Region Name, and the lineup by tier, in number order
    assert ('<tr><td>300<td>News<td>antenna<td>%s<td>yes<td>2<td>42\u00a0#9, 260\u00a0WKYT'
            % effective) in page
    assert '<tr><td>4607<td>260<td>WKYT<td>CBS<td>1' in page
    # Channel Data names a channel; one it has not named keeps its id
    assert ("<div class='row' data-c='4607'><div class='badge' "
            "title='channel id 4607: WKYT-CBS on 260'>260 WKYT</div>") in page
    assert "<div class='row' data-c='9'><div class='badge'>9</div>" in page
    # and the lineup can be picked, to see the guide as a receiver there drew it
    assert "<option value='300'>News (antenna, 2 channels)</option>" in page
    assert 'var LINEUPS={"300":[[9,42],[4607,260]]};var SHORT={"4607":"WKYT"}' in page


def test_a_cable_service_is_named_by_its_name_and_listed_by_its_label(tmp_path):
    lineup = bytes([0x00, 0x0A, 0x24, 0x01])          # channel id 10 on 36, extended
    region = bytes([3]) + struct.pack('>H', 11 + len(lineup)) + struct.pack('>H', 1231) \
        + bytes([0x20]) + struct.pack('>I', 0) + bytes([1]) + lineup
    guide, counts = replayed([HEADLINE, region,
                              show_list(10, bytes([0, 30]) + struct.pack('>H', 51))])
    page = rendered(tmp_path, guide, counts)
    assert "title='channel id 10: HEADLINE, short HDLN'>HEADLINE</div>" in page
    # a column no lineup fills is left out, so the extended tier follows the count
    assert '<tr><td>1231<td>cable<td>1<td>36\u00a0HDLN' in page
    assert 'var LINEUPS={"1231":[[10,36]]};var SHORT={"10":"HDLN"}' in page


def test_the_page_names_themes_and_shows_every_title_and_description_field(tmp_path):
    page = rendered(tmp_path, *replayed(uncaptured()))
    # the sub-category came in two Segmented Command parts
    assert '<tr><td>5<td>Movies<td>0<td>Drama (2, flag 0)' in page
    assert '<tr><td>9<td>Sport<td>0<td>' in page
    assert '<tr><td>644<td>Movies / Drama<td>1' in page
    assert '<tr><td>1654<td>644<td>Movies / Drama<td>CC stereo<td><td>Test Show' in page
    assert '<tr><td>77<td>97<td>Movies / Drama<td>CC<td>yes<td>News' in page
    assert ('<tr><td>62<td>1972<td>***<td>PG<td>3<td>TV-14<td>violence, adult situations'
            '<td>yes<td>TV14 Bank robber and wife flee (R).') in page
    # the slot pointing at it keeps its number and group, and the box shows its text
    assert ' d=62 g=5 ' in page and '"62":"TV14 Bank robber' in page
    assert ' x=4>' in page


def test_the_page_shows_station_status_resets_and_what_did_not_decode(tmp_path):
    page = rendered(tmp_path, *replayed(uncaptured()))
    unix = lambda seconds: (STATION_EPOCH + datetime.timedelta(seconds=seconds)) \
        .strftime('%Y-%m-%d %H:%M:%S')
    assert '<tr><td>1<td>1<td>74<td>0<td>0<td>%s<td>%s<td>%s<td>67<td>420<td>487' % (
        unix(0), unix(768_050_037), unix(768_051_237)) in page
    assert '<tr><td>1<td>clear stored guide, clear stored guide and stop loading' in page
    # the title that did not decompress is kept whole, with why
    assert '<tr><td>6<td>Show Title<td>title did not decompress<td>11<td>06 0b 80 00 4e' in page


def test_the_page_has_a_table_for_each_loader_only_type(tmp_path):
    # a type the log can show has a table to show it in and a key in the export
    assert all(kind.columns and kind.key for kind in OTHER_COMMANDS.values())
    page = rendered(tmp_path, *replayed(uncaptured()))
    day = starsight_time(3_597_120).strftime('%Y-%m-%d %H:%M')
    for heading, row in (('Region', '<tr><td>300<td>News'),
                         ('Cycle Start', '<tr><td>0a 09 a1 b2 c3 d4 e5 f6 07'),
                         ('Postal Code', '<tr><td>40506<td>300\u00a0News, 301<td>7<td>0xaa<td>00 bb'),
                         ('Type 37', '<tr><td>5<td>%s<td>11 22 33<td>1:9 2:10' % day),
                         ('Type 38',
                          '<tr><td>77<td>11 22 03<td>41 41 41<td>42 42<td>43 43 43 43'),
                         ('Type 39', '<tr><td>5<td>600<td>3597120<td>42'),
                         ('Type 40', '<tr><td>8<td>70 61 79 6c 6f 61'),
                         ('Type 42', '<tr><td>on')):
        assert '<h2>%s</h2>' % heading in page and row in page, heading


def test_channel_data_survives_the_json_round_trip(tmp_path):
    guide, _ = replayed([channel_data(), title(),
                         show_list(0x11FF, bytes([0x01, 30]) + struct.pack('>H', 1654))])
    write_starsight_json(str(tmp_path / 'first'), guide)
    first = (tmp_path / 'first.starsight.json').read_text()
    again = read_starsight_json(json.loads(first))
    assert again['channel_data'] == guide['channel_data']
    write_starsight_json(str(tmp_path / 'second'), again)
    assert (tmp_path / 'second.starsight.json').read_text() == first

    # and a page drawn from it still names the channel, with nothing the JSON lacks
    page = rendered(tmp_path, again)
    assert "title='channel id 4607: WKYT-CBS on 260'>260 WKYT</div>" in page
    for heading in ('Region', 'Station Node Status', 'Subscriber Reset', 'Commands'):
        assert '<h2>%s</h2>' % heading not in page


def test_the_advisory_bits_nothing_names_are_shown_and_kept(tmp_path):
    assert description_advisories(0x65) == ['violence', 'unknown bit 0', 'unknown bit 5',
                                            'unknown bit 6']
    guide, _ = replayed([description(advisories=0x65)])
    cell = '<td>violence, unknown bit 0, unknown bit 5, unknown bit 6<td>'
    assert cell in rendered(tmp_path, guide)

    write_starsight_json(str(tmp_path / 'out'), guide)
    again = read_starsight_json(json.loads((tmp_path / 'out.starsight.json').read_text()))
    assert again['descriptions'][62][1].advisories == 0x65
    assert cell in rendered(tmp_path, merge_guides([again])[0])


def test_station_node_blocks_are_dumped_and_survive_a_merge(tmp_path):
    guide, _ = replayed([title(), station_node()])
    raw = station_node()
    rows = ['<tr><td>1<td>0x%04x<td>%s' % (at, raw[at:at + 16].hex(' '))
            for at in range(0, len(raw), 16)]
    # the last row reads the same in ASCII and in CEA-608 but for 0x2A, which 608 makes a
    # lower case a acute
    rows.append('<td>KCET*.....<td>KCET\u00e1.....')
    page = rendered(tmp_path, guide)
    assert all(row in page for row in rows)

    write_starsight_json(str(tmp_path / 'out'), guide)
    document = json.loads((tmp_path / 'out.starsight.json').read_text())
    assert [entry['data'] for entry in document['stationNodeStatus']] == [raw.hex()]
    assert document['stationNodeStatus'][0]['clock'] == '1994-05-04T11:33:57Z'
    again = read_starsight_json(document)
    assert again['station'] == guide['station']
    # the same tape twice is still one block
    page = rendered(tmp_path, merge_guides([again, read_starsight_json(document)])[0])
    assert all(row in page for row in rows) and '<tr><td>2<td>' not in page


class Pipe:
    """ What `decode_starsight` reads from: one sliced line a frame, then DONE """
    def __init__(self, data):
        data += bytes(len(data) % 2)
        self.frames = [[(0, data[at], data[at + 1], None, [1.0] * 16)]
                       for at in range(0, len(data), 2)] + ['DONE']

    def recv(self):
        return self.frames.pop(0)


def test_a_show_list_starting_past_9999_is_skipped(tmp_path):
    body = bytes([0]) + struct.pack('>HI', 1061, 0xFFFFFFFF) + bytes([1, 0, 30]) \
        + struct.pack('>H', 11)
    corrupt = bytes([5]) + struct.pack('>H', len(body) + 3) + body
    decode_starsight(Pipe(packet(sequence() + corrupt + title())), str(tmp_path / 'out'), None)
    # the title after it in the same packet is still read
    assert 'Show Title   id 1654' in (tmp_path / 'out.starsight').read_text()
    assert (tmp_path / 'out.starsight.html').exists()


def test_the_commands_note_names_the_types_only_counted(tmp_path):
    guide, _ = replayed([title()])
    # Sequence Number and Station Node Status are loader stubs this does read
    assert 'All of these are decoded.</p>' in rendered(tmp_path, guide, Counter({6: 1, 20: 3,
                                                                                  21: 1}))
    assert ('All of these are decoded except types 22, 35, which are kept whole under Not '
            'decoded.') in rendered(tmp_path, guide, Counter({6: 1, 22: 2, 35: 1}))


# --- the news service, and the commands addressed to one receiver --------------------

# The 1997 Ohio tape's: the service's name, its menu, a sports page sent in the clear and the
# two Segmented Command parts that carry Ashtabula's weather.
NEWS_SERVICE = bytes.fromhex('20001f0501000417537461725369676874204e455753205365727669636573')
NEWS_MENU = bytes.fromhex(
    '2100c8080c03000703000009f93d3792ff247fb4e004000701000008df9bc9dfe48ff69c050007010000'
    '09f923f8e6533bb7d3800a0007070000099901732fe589faf38006000702000005dfc7a4193807000706'
    '000009d7e0ddc7af7369169c08000704000008deb7afaf437169c00900070300000ad6ee73156daf4371'
    '69c00b0007010000070737db53bc45a70c00070200000b95d2cb45860f1e3e568b4e0d00070400000a95'
    'd2cb45873299ddbe9c0e00070100001062af74adaf70f5d2a2d6f846629eaf38')
NFL_PAGE = bytes.fromhex(
    '22007208170b05f9000a00002cf573a809f9379f37de2d26d38080566069544855525344415960650d4a75'
    '6c792033310d0d0d4e4550202020336069460d0d474250202020370d0d606953554e44415960650d4a756c'
    '792032370d0d0d506974202033306069460d0d436869202031370d0d')
ASHTABULA = (bytes.fromhex(
    '240082014d2200c606060d79c7000057002cd0390e076362dfcae99f4e00acf803ca435d717620c1cd34ce'
    'ef8c6a71353cda9b52958afbf58f6f89fe4997c6ea7d4d270577707d09f13bca5e6d833d7e2bfc89516ff1'
    '3fcfbfde52f36f07d6cc06f8bbc35d485d8830c5794b7c65c19dde52f296c1f7a1df3bad3006e067315ff8'
    '64'), bytes.fromhex(
    '240052114dc5d763f09486a2ec41862bca5b53b833bbca5fe3183ef4c4e9c7354cf743db15c19c7b4c3aad'
    '8816ee0671ff864c5d7949f20bb10618af295294e0ceef297d4f83ebf49b5a62c55a27e8104c82'))


def test_the_news_service_names_itself_and_its_menu():
    assert decode_news_service(NEWS_SERVICE).name == 'StarSight NEWS Services'
    unread, sections = decode_news_menu(NEWS_MENU)
    assert [(section.section, section.name) for section in sections[:4]] == [
        (3, 'National News'), (4, 'World News'), (5, 'News Extra'), (10, 'Entertainment')]
    assert sections[-1].name == 'About StarSight Telecast' and len(sections) == 12


def test_a_news_page_sent_in_the_clear_keeps_its_lines_and_attributes():
    page = decode_news_page(NFL_PAGE)
    assert (page.section, page.page, page.area, page.title) == (8, 0x0B05, 0, 'NFL Finals')
    assert page.when == datetime.datetime(1997, 8, 8, 2, 59)
    assert page.text.startswith('`iTHURSDAY`e\nJuly 31\n\n\nNEP   3`iF\n')
    assert news_text(page.text).startswith('THURSDAY\nJuly 31\n\n\nNEP   3F\n')


def test_a_compressed_news_page_breaks_its_lines_with_the_ur_leaf():
    guide = new_guide()
    describe_starsight_command(36, ASHTABULA[0], guide)
    lines = describe_starsight_command(36, ASHTABULA[1], guide)
    assert lines[1].startswith('News Page    section 6   page 3449  area 0x57  '
                               '1997-08-01 12:09  Ashtabula: THURSDAY July 31, 1997 /  Winds:')
    page = guide['news']['pages'][(6, 3449, 0x57)]
    assert page.text.split('\n')[:3] == ['THURSDAY July 31, 1997', ' Winds: NE,12mph   Hum:  60%',
                                         ' Night: Low 60. Clear ']
    # a title keeps the guide's reading of the same leaf
    assert decode_news_page(bytes([34, 0, 23, 6, 0, 0, 1, 0, 0, 0, 0x57, 0, 0, 0, 0, 0x0E, 0])
                            + bytes([0x80, 0x02]) + b'OK').text == 'OK'


def test_key_distribution_and_authorization_address_one_unit():
    key = bytes.fromhex('113a00000161555f4aa7c71c187bd39e6c24cfa3b8cff5f5c503219f2716457138f2'
                        '07acbea73fd08819bbc6db56ce74d81b5e2a7d56df2033b3')
    data = decode_addressed_payload(key)
    assert (data.address, len(data.payload)) == (0x161, 52)
    guide = new_guide()
    assert describe_starsight_command(17, key, guide) == ['Key Distribution unit 353      52 bytes']
    # both frame, with the one byte length the loader's table gives them
    found, _ = take_starsight_packets(packet(sequence() + key + bytes([14, 8, 0, 0, 5, 0xCF, 0xEF, 0])))
    assert [command_type for command_type, _ in found[0][2]] == [20, 17, 14]


def test_the_lineup_postal_codes_themes_and_news_survive_the_json_round_trip(tmp_path):
    region = bytes.fromhex('03000f000220000000000102e50200')
    guide, _ = replayed([KDKA, HEADLINE, region, bytes.fromhex('090d1cd2a773a115d2c274b380'),
                         bytes.fromhex('1f00103433323335009501001cd25801'),
                         CATEGORIES, COMEDY, NEWS_SERVICE, NEWS_MENU, NFL_PAGE] + list(ASHTABULA))
    write_starsight_json(str(tmp_path / 'first'), guide)
    first = (tmp_path / 'first.starsight.json').read_text()
    document = json.loads(first)
    assert document['regions'] == [
        {'id': 2, 'cable': True, 'lineup': [{'channel': 741, 'number': 2, 'tier': 0}]},
        {'id': 7378, 'name': 'columbus, oh'}]
    assert document['postalCodes'] == [{'code': '43235', 'broadcastRegions': [7378],
                                        'cableRegions': [], 'newsArea': 0x58,
                                        'unread': '9501'}]
    assert {'id': 19, 'callSign': 'KDKA', 'network': 'CBS', 'channelNumber': 2} \
        in document['channels']
    assert {'id': 10, 'callSign': 'HEADLINE', 'label': 'HDLN', 'showsCallSign': True} \
        in document['channels']
    assert document['news']['service'] == 'StarSight NEWS Services'
    assert len(document['news']['pages']) == 2 and len(document['news']['sections']) == 12

    again = read_starsight_json(document)
    assert theme_labels(again) == theme_labels(guide)
    write_starsight_json(str(tmp_path / 'second'), again)
    assert (tmp_path / 'second.starsight.json').read_text() == first


class Rows:
    """ What `decode_starsight` reads from: sliced rows a frame, then DONE """
    def __init__(self, frames):
        self.frames = list(frames) + ['DONE']

    def recv(self):
        return self.frames.pop(0)


def lines_of(data, row):
    """ `data` two bytes a frame on one row """
    data += bytes(len(data) % 2)
    return [[(row, data[at], data[at + 1], None, [1.0] * 16)]
            for at in range(0, len(data), 2)]


def test_a_line_carrying_a_damaged_copy_costs_nothing(tmp_path):
    data = b''.join(packet(sequence(number) + title()) for number in (1, 2, 3))
    good = lines_of(data, 8)
    # the line below carries the same bytes with every eighth one wrong, and sometimes none
    bad = [[(10, b1 ^ (0x40 if n % 4 == 0 else 0), b2, None, c)] if n % 3 else []
           for n, [(_, b1, b2, _, c)] in enumerate(good)]
    decode_starsight(Rows(g + b for g, b in zip(good, bad)), str(tmp_path / 'out'), None)
    log = (tmp_path / 'out.starsight').read_text()
    # three packets, each read once
    assert log.count('Show Title   id 1654') == 3
    assert '3 packets passed both checksums' in log


def test_a_tape_recorded_over_is_two_guides(tmp_path):
    def stamped(minutes, number):
        body = sequence(number) + title()
        size = STARSIGHT_HEADER + len(body) + STARSIGHT_TRAILER
        head = bytes([STARSIGHT_SYNC]) + struct.pack('>H', size) + struct.pack('>I', minutes) \
            + struct.pack('>H', 92)
        head += struct.pack('<H', starsight_crc(head) & 0xFFFF)
        return head + body + struct.pack('<I', starsight_crc(head + body))

    later, earlier = 4_092_480, 2_966_400        # 1999-10-13 and 1997-08-22
    data = b''.join(stamped(later + n, 10 + n) for n in range(3)) \
        + b''.join(stamped(earlier + n, 900 + n) for n in range(3))
    decode_starsight(Rows(lines_of(data, 8)), str(tmp_path / 'out'), None)
    log = (tmp_path / 'out.starsight').read_text()
    assert 'Recording    header clock 1997-08-22 00:00 GMT on stream 92' in log
    assert (tmp_path / 'out.1999-10-13.starsight.html').exists()
    assert (tmp_path / 'out.1997-08-22.starsight.json').exists()
    assert not (tmp_path / 'out.starsight.html').exists()

    # one packet that breaks off alone is a bad header, not another recording
    data = b''.join(stamped(later + n, 10 + n) for n in range(3)) + stamped(earlier, 900) \
        + b''.join(stamped(later + 3 + n, 13 + n) for n in range(2))
    decode_starsight(Rows(lines_of(data, 8)), str(tmp_path / 'one'), None)
    assert 'Recording' not in (tmp_path / 'one.starsight').read_text()
    assert (tmp_path / 'one.starsight.html').exists()


# --- every field the guide keeps has a place in the export and on the page -------------

def everything():
    """ Every kind of command the decoder reads, captured where a capture has carried one """
    commands = uncaptured() + [KDKA, HEADLINE, SCI_FI,
                               bytes.fromhex('03000f000220000000000102e50200'),
                               bytes.fromhex('090d1cd2a773a115d2c274b380'),
                               bytes.fromhex('1f00103433323335009501001cd25801'),
                               CATEGORIES, COMEDY, NEWS_SERVICE, NEWS_MENU, NFL_PAGE,
                               bytes.fromhex(
        'c5007b01002800201fc0187adf89cc584b8c25afbb330bf4ed8a474f6d9b8ecb2d5e7fe8c6cb33488a5e'
        'f21a671810655e6ab7956e1239b1fd89239c7c2d6bf03c56c77ae29b53e14112236f474218713f234c5d'
        '2aeb320d092eb7a98a3e3662a380b9d0a4e4038616cffe442e30c0933aa0af6abdecfa4c67a68a'),
                               bytes.fromhex('113a00000161555f4aa7c71c187bd39e6c24cfa3b8cff5f5c50321'
                                             '9f2716457138f207acbea73fd08819bbc6db56ce74d81b5e2a7d'
                                             '56df2033b3'),
                               bytes([14, 8, 0, 0, 5, 0xCF, 0xEF, 0]),
                               bytes.fromhex('2a001a0202803041100000510123456780012345675109876543'),
                               bytes([1, 8]) + struct.pack('>I', 3_400_000) + bytes([0xD8, 26]),
                               bytes([2, 10]) + struct.pack('>II', 3_291_960, 3_584_220),
                               sequence(77),
                               # a description of a black and white film with captions
                               bytes([8, 14, 0x50]) + struct.pack('>H', 5) + bytes([0, 0])
                               + b'Old film.\x00',
                               bytes([22, 8, 1, 2, 3, 4, 5, 6]),
                               whyy_station_node()]
    # the first part of a segmented message whose second never comes
    commands += list(ASHTABULA[:1]) + [segment(0, 1, b'abcd', message=9)]
    guide, counts = replayed(commands)
    guide['packets'] = [(starsight_time(3_400_000), 136, 77)]
    return guide, counts


KEPT_ELSEWHERE = {'pending', 'blocks', 'segmented'}


def test_every_field_the_guide_keeps_survives_the_json_round_trip(tmp_path):
    guide, _ = everything()
    for key, value in guide.items():
        assert value or key in KEPT_ELSEWHERE or key in ('theme_version',), key
    write_starsight_json(str(tmp_path / 'out'), guide)
    document = json.loads((tmp_path / 'out.starsight.json').read_text())
    again = read_starsight_json(document)
    for key in guide:
        if key not in KEPT_ELSEWHERE:
            assert again[key] == guide[key], key
    assert listings(again) == listings(guide)


def test_the_export_says_what_the_page_does(tmp_path):
    guide, _ = everything()
    write_starsight_json(str(tmp_path / 'out'), guide)
    document = json.loads((tmp_path / 'out.starsight.json').read_text())
    # a description carries a title's attribute bits; the clock, whether it is a provider
    assert {'id': 5, 'text': 'Old film.', 'closedCaptioned': True, 'blackAndWhite': True,
            'uncompressed': True} in document['descriptions']
    assert document['clock'][0]['notAProvider'] is True
    assert document['packets'] == [{'time': '1998-06-19T02:40:00Z', 'stream': 136,
                                    'sequence': 77}]
    assert {'channel': 9, 'start': '1998-06-19T02:40:00Z', 'version': 0} \
        in document['showLists']
    assert document['encrypted'][0]['channel'] == 40 \
        and document['encrypted'][0]['data'].startswith('c5007b')
    assert document['keyDistributions'][0]['unit'] == 0x161
    assert document['authorizations'] == [{'unit': 0x5CF, 'payload': 'ef00'}]
    assert document['type42'][0]['flag'] is True
    whys = {entry['why'] for entry in document['undecoded']}
    assert {'title did not decompress', 'the loader does not read this type',
            'message 77: the parts after these never came'} <= whys
    assert document['news']['pages'][0]['flags'] == 0xF9
    # and what a station's own database holds, as its Station Node Status says
    station = document['stationNodeStatus'][-1]
    assert station['inventory']['titles'] == {'held': 7633, 'missing': 0}
    assert station['inventory']['days'][7] == {'held': 132, 'missing': 257,
                                               'firstMissing': [1, 5, 7, 9, 11, 13, 14, 15]}
    assert station['daily'] == '1998-01-03T05:07:01Z' and station['minutesUsed'] == 1439
    assert station['minuteMaps'] == [[], []]


def test_the_page_folds_its_tables_and_shows_the_news(tmp_path):
    page = rendered(tmp_path, *everything())
    # every table is folded away until asked for
    assert "<details class='data' open" not in page
    assert page.count("<details class='data'>") > 20
    assert "<button type='button' data-open='1'>Open all</button>" in page
    # the news service gets a viewer of its own, with the menu, the pages and a postal code
    assert "<div class='set news' id='news'><div class='banner'>STARSIGHT NEWS SERVICES</div>" \
        in page
    news = json.loads(page.split('<script>var NEWS=')[1].split('</script>')[0])
    assert news['sections'][0] == [3, 'National News']
    assert news['pages'][0][:5] == [8, 0x0B05, 0, '1997-08-08 02:59', 'NFL Finals']
    assert news['postal'] == {'40506': 0xAA, '43235': 0x58}
    # and every new field has a column
    for row in ('<td>CC B/W<td>', '<tr><td>1998-06-19 02:40<td>136<td>77',
                '<td>message 77: the parts after these never came<td>',
                '<td>schedules, day 7<td>132<td>257<td>1 5 7 9 11 13 14 15',
                '<td>1998-01-03 05:07:01<td>1439</tbody>'):
        assert row in page, row


def test_the_export_meets_its_schema(tmp_path):
    jsonschema = __import__('pytest').importorskip('jsonschema')
    schema = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                                          'docs', 'starsight.schema.json')))
    guide, _ = everything()
    write_starsight_json(str(tmp_path / 'out'), guide)
    jsonschema.validate(json.loads((tmp_path / 'out.starsight.json').read_text()), schema)
