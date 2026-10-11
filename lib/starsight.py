#!/usr/local/bin/python
# coding: utf-8
""" StarSight program guide decoding for ccDecoder

The StarSight Data Transmission Network protocol, carried in the NTSC vertical blanking
interval. US6216265B1 (StarSight Telecast) reproduces it - packet header, command table and
the byte layout of each command - and SSLOAD.DLL, Microsoft's StarSight loader, settles much
of what the patent leaves open.

The waveform is the CEA-608 one on another VBI line, so `lib.cc_decode` recovers the bytes
and only the parity rule differs: StarSight spends the eighth bit on data. Decoder silicon
calls the bearer Gemstar 1X, and RFC 2728 carries the same guide over PBS National Datacast,
so the protocol is bearer independent. These captures are not NABTS.

A capture decodes to a log of the guide in broadcast order, written as it arrives the way
`decode_xds_packets` writes XDS; a page showing the guide and the news service as a receiver
drew them, with every command in a table of its own; and a JSON export of all of it.

See docs/starsight.md. Public domain / Unlicense, as with the rest of ccDecoder.
"""

import datetime
import html
import json
import os
import zlib

from collections import Counter, defaultdict, namedtuple
from functools import lru_cache
from multiprocessing import current_process

from setproctitle import setproctitle

from lib.cc_decode import CC_TABLE, get_output_function, scc_timecode
from lib.starsight_table import STARSIGHT_HUFFMAN


# Packet: 0x2C sync, 2 byte size, 4 byte timestamp, 2 byte VBI stream id, 2 byte CRC1,
# then the message - concatenated commands - and a 4 byte CRC-32.
STARSIGHT_SYNC = 0x2C
STARSIGHT_HEADER = 11
STARSIGHT_TRAILER = 4
STARSIGHT_MIN_PACKET = 32
STARSIGHT_MAX_PACKET = 0x7F8
STARSIGHT_CRC_POLYNOMIAL = 0xEDB88320

def _crc_table():
    table = []
    for index in range(256):
        value = index
        for _ in range(8):
            value = (value >> 1) ^ (STARSIGHT_CRC_POLYNOMIAL if value & 1 else 0)
        table.append(value)
    return tuple(table)

STARSIGHT_CRC_TABLE = _crc_table()

def starsight_crc(data):
    """ The loader's CRC-32 over `data`: zlib's, less its final inversion """
    return zlib.crc32(data) ^ 0xFFFFFFFF

# All times on the wire are minutes since midnight GMT on 1 January 1992.
STARSIGHT_EPOCH = datetime.datetime(1992, 1, 1)

COMMAND_TABLE = {
    0:  (1, 0,  'none'),         1:  (1, 2,  'implemented'), 2:  (1, 2,  'implemented'),
    3:  (2, 10, 'implemented'),  4:  (1, 5,  'implemented'), 5:  (2, 11, 'implemented'),
    6:  (1, 5,  'implemented'),  7:  (1, 2,  'none'),        8:  (1, 5,  'implemented'),
    9:  (1, 2,  'implemented'),  10: (1, 2,  'implemented'), 11: (2, 5,  'implemented'),
    12: (2, 5,  'implemented'),  13: (1, 8,  'implemented'), 14: (1, 2,  'none'),
    15: (1, 2,  'none'),         16: (1, 2,  'none'),        17: (1, 2,  'none'),
    18: (1, 2,  'none'),         19: (1, 2,  'none'),        20: (1, 2,  'stub'),
    21: (2, 3,  'stub'),         22: (2, 12, 'stub'),        23: (2, 3,  'none'),
    24: (2, 9,  'stub'),         25: (1, 2,  'none'),        26: (1, 2,  'none'),
    27: (1, 2,  'none'),         28: (1, 2,  'none'),        29: (2, 3,  'none'),
    30: (2, 3,  'none'),         31: (2, 3,  'implemented'), 32: (2, 3,  'stub'),
    33: (2, 3,  'stub'),         34: (2, 3,  'stub'),        35: (2, 3,  'stub'),
    36: (2, 3,  'implemented'),  37: (2, 3,  'implemented'), 38: (2, 3,  'implemented'),
    39: (2, 3,  'implemented'),  40: (2, 3,  'implemented'), 41: (2, 3,  'none'),
    42: (2, 3,  'implemented'),
}
COMMAND_TABLE.update({t: (2, 3, 'none') for t in range(43, 64)})

# Which command types a capture has actually carried.
COMMANDS_SEEN = frozenset((1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 14, 17, 20, 21, 31, 32, 33, 34,
                           36, 42))

# A type the loader has code for is a type that can appear on the wire, and so is one a
# capture has carried, so all of them are framed - with the widths the loader's descriptor
# table gives every type. Anything else would desynchronise the reader rather than be skipped.
COMMAND_LENGTH_ONE_BYTE = frozenset(t for t, (w, _, h) in COMMAND_TABLE.items()
                                    if w == 1 and (h != 'none' or t in COMMANDS_SEEN))
COMMAND_LENGTH_TWO_BYTE = frozenset(t for t, (w, _, h) in COMMAND_TABLE.items()
                                    if w == 2 and (h != 'none' or t in COMMANDS_SEEN))

COMMAND_TIME = 1
COMMAND_DAYLIGHT_SAVING = 2
COMMAND_SHOW_LIST = 5
COMMAND_SHOW_TITLE = 6
COMMAND_SHOW_DESCRIPTION = 8
COMMAND_REGION = 3
COMMAND_CHANNEL_DATA = 4
COMMAND_THEME_CATEGORY = 11
COMMAND_THEME_SUB_CATEGORY = 12
COMMAND_SUBSCRIBER_RESET = 13
COMMAND_SEQUENCE_NUMBER = 20
COMMAND_STATION_NODE_STATUS = 21

# The nine the loader runs code for beyond the guide itself.
COMMAND_REGION_NAME = 9
COMMAND_CYCLE_START = 10
COMMAND_POSTAL_CODE = 31
COMMAND_SEGMENTED = 36
COMMAND_ADDRESSED = 37
COMMAND_KEYED_RECORD = 38
COMMAND_TIMED_RECORD = 39
COMMAND_SIZED_PAYLOAD = 40
COMMAND_GATE = 42

# Addressed to one receiver, which the loader has no code for.
COMMAND_AUTHORIZATION = 14
COMMAND_KEY_DISTRIBUTION = 17

# The news service, which the loader stubs.
COMMAND_NEWS_SERVICE = 32
COMMAND_NEWS_MENU = 33
COMMAND_NEWS_PAGE = 34

COMMAND_NAMES = {
    1: 'Time',              2: 'Daylight Saving Change', 3: 'Region',
    4: 'Channel Data',      5: 'Show List',              6: 'Show Title',
    8: 'Show Description', 9: 'Region Name',            10: 'Cycle Start',
    11: 'Theme Category',  12: 'Theme Sub-Category',    13: 'Subscriber Reset',
    14: 'Authorization',   17: 'Key Distribution',      20: 'Sequence Number',
    21: 'Station Node Status', 22: 'Long Assign IR Codes', 24: 'Subscriber Unit',
    31: 'Postal Code',     32: 'News Service',          33: 'News Menu',
    34: 'News Page',       36: 'Segmented Command',
}

# Bit 7 of byte 0 is the patents' encryption flag and bit 6 the key it was made with. The loader
# runs no handler for such a command. An encrypted Show List keeps its header in the clear -
# version, channel, start and slot count - and enciphers only the slots.
COMMAND_ENCRYPTED = 0x80
COMMAND_KEY_SHIFT = 6

EncryptedCommand = namedtuple('EncryptedCommand', 'type key channel start slots data')

# A string is sent compressed unless compressing it would make it longer, so most of them
# are. `lib.starsight_table` holds the Huffman table that decodes them.
COMPRESSED_FLAG = 0x80

# Bit 3 of a Show Description's flag byte selects the extended form: three more bytes of
# rating, advisories and year before the text.
DESCRIPTION_EXTENDED = 0x08

# Byte 8 of the extended form. SSLOAD.DLL reads these five bits back against the names below;
# it carries bit 0 without showing it and reads neither 5 nor 6. No capture sets those three.
ADVISORY_NAMES = ((0x02, 'nudity'), (0x04, 'violence'), (0x08, 'adult situations'),
                  (0x10, 'adult themes'), (0x80, 'adult language'))
ADVISORY_UNKNOWN = ((0x01, 'unknown bit 0'), (0x20, 'unknown bit 5'), (0x40, 'unknown bit 6'))

# Byte 7 of the extended form: a critic's star rating above a film rating. SSLOAD.DLL writes
# the stars as that many '*': The Best Years of Our Lives, Lifeboat and The King and I have
# 4, Sheena 1.
RATING_STARS_SHIFT = 5
RATING_CODE_SHIFT = 1
RATING_CODE_MASK = 0x0F

# The film rating. The captures agree on 0, 1 and 3-5 - Rapa Nui and Thinner are 5, The
# Evening Star 4, Silent Movie 3, The Neptune Factor 1 - and every title carrying 2 is an
# adult film.
RATING_NAMES = ('NR', 'G', 'NC-17', 'PG', 'PG-13', 'R', 'NC-17', 'NC-17')

ShowRating = namedtuple('ShowRating', 'stars code advisories year')

def rating_name(rating):
    return RATING_NAMES[rating.code] if rating.code < len(RATING_NAMES) else None

# A TV Parental Guidelines rating opens the description text itself, as on 96 of the April
# capture's 233. SSLOAD.DLL looks for the hyphenated names; TV-M became TV-MA in 1997.
TV_RATINGS = {'TVY': 'TV-Y', 'TVY7': 'TV-Y7', 'TVG': 'TV-G', 'TVPG': 'TV-PG',
              'TV14': 'TV-14', 'TVM': 'TV-MA', 'TVMA': 'TV-MA'}

def tv_rating(text):
    """ The TV rating a description opens with, or None """
    word = text.split(' ', 1)[0] if text else ''
    return TV_RATINGS.get(word.replace('-', ''))

def description_advisories(advisories):
    """ The content advisories named by the extended form's byte 8 """
    return [name for mask, name in ADVISORY_NAMES + ADVISORY_UNKNOWN if advisories & mask]

# A Show List's channel id is 15 bits; SSLOAD.DLL masks the high byte before using it.
CHANNEL_ID_MASK = 0x7FFF

# A slot's flag byte. Bits 7 and 5 add a description id and a show group after the slot's
# four bytes. Bit 6 is pay per view, the loader's `TS Pay Per View`: 581 of the October
# capture's 23,836 slots set it, all on 11 channels. Bit 0, on a list's first slot, is a
# program already running when the list starts, whose duration the loader takes as elapsed.
# The loader reads none of the rest, which two slots in 43,430 set, both on damaged commands.
SLOT_HAS_DESCRIPTION = 0x80
SLOT_PAY_PER_VIEW = 0x40
SLOT_HAS_SHOW_GROUP = 0x20
SLOT_JOINED_IN_PROGRESS = 0x01
SLOT_UNNAMED = 0x1E


def starsight_time(minutes):
    """ Turn an on-wire minute count into a date and time """
    return STARSIGHT_EPOCH + datetime.timedelta(minutes=minutes)

def _u16(data, offset):
    return (data[offset] << 8) | data[offset + 1]

def _u16le(data, offset):
    """ The one little endian number in the format: the header's own checksum """
    return data[offset] | (data[offset + 1] << 8)

def _u32(data, offset):
    return ((data[offset] << 24) | (data[offset + 1] << 16) | (data[offset + 2] << 8)
            | data[offset + 3])

# A news page's text breaks its lines with the leaf the guide expands to 'ur' (0x1F); it
# spells every other 'ur' out, and an uncompressed page breaks its lines with CR.
STARSIGHT_HUFFMAN_NEWS = {code: '\n' if text == 'ur' else text
                          for code, text in STARSIGHT_HUFFMAN.items()}

@lru_cache(maxsize=None)
def _huffman_steps(news):
    """ The Huffman code as a machine that takes a byte at a time

    A state is an internal node of the code's tree, the root being 0. For each state and
    byte: the text the byte completes, the state after it, and whether it held the NUL that
    ends the string - or None where its bits leave the tree.
    """
    root = [None, None]
    for code, text in (STARSIGHT_HUFFMAN_NEWS if news else STARSIGHT_HUFFMAN).items():
        node = root
        for bit in map(int, code[:-1]):
            if node[bit] is None:
                node[bit] = [None, None]
            node = node[bit]
        node[int(code[-1])] = text

    nodes = [root]
    for node in nodes:
        nodes += [child for child in node if isinstance(child, list)]
    state = {id(node): number for number, node in enumerate(nodes)}

    steps = []
    for node in nodes:
        row = []
        for byte in range(256):
            at, text, ended = node, [], False
            for shift in range(7, -1, -1):
                at = at[byte >> shift & 1]
                if at is None or at == '\0':
                    ended = at is not None
                    break
                if isinstance(at, str):
                    text.append(at)
                    at = root
            row.append(None if at is None else
                       (''.join(text), 0 if ended else state[id(at)], ended))
        steps.append(row)
    return steps

def decompress_starsight_string(payload, news=False):
    """ Huffman decode a compressed string, or None if the bits do not resolve

    A NUL is appended to the string before compression, so its codeword marks the end and the
    rest of that byte is padding. A codeword not in the table, or a terminator before the last
    byte, means the payload was damaged in the VBI, and the caller is told rather than shown
    guesswork. `news` reads 0x1F as a news page's line break.
    """
    steps = _huffman_steps(news)
    text, state, last = [], 0, len(payload) - 1
    for at, byte in enumerate(payload):
        step = steps[state][byte]
        if step is None:
            return None
        piece, state, ended = step
        text.append(piece)
        if ended:
            return ''.join(text) if at == last else None
    return None

def decode_starsight_string(flags, payload):
    """ The text of a string command, compressed or in the clear, or None """
    if not payload:
        return None
    if flags & COMPRESSED_FLAG:
        return decompress_starsight_string(payload)
    text = bytes(payload[:-1])
    if payload[-1] or not all(0x20 <= c < 0x7F for c in text):
        return None
    return text.decode('ascii')

def decode_starsight_commands(data, start, end):
    """ Split a packet's message into `[(type, command)]`, or None unless they tile it

    Both checksums are verified before this is called, so a message that does not tile is a
    layout this does not know rather than damage; `take_starsight_packets` counts those, and
    across every capture the count is zero.
    """
    commands = []
    offset = start
    while offset < end:
        command_type = data[offset] & 0x3F
        if command_type in COMMAND_LENGTH_ONE_BYTE:
            length = data[offset + 1]
        elif command_type in COMMAND_LENGTH_TWO_BYTE:
            length = _u16(data, offset + 1)
        else:
            return None
        if length < 3 or offset + length > end:
            return None
        commands.append((command_type, data[offset:offset + length]))
        offset += length
    return commands if offset == end else None

# A packet spans about a hundred fields, so on a worn tape one VBI line that does not slice
# takes a whole packet with it. Its bytes are unknown rather than wrong, and up to this many
# are solved for against the body checksum.
STARSIGHT_MAX_LOST_BYTES = 2

# How many bits may be treated as erasures - positions known, values not - when solving a
# packet against its body checksum, tried in turn so that the first answer that checksums and
# tiles assumes the fewest broken bits. Each erasure spends one of the checksum's 32 bits. On
# the 1994 KCET capture, escalating spends 5.3 bits a packet where going straight to twenty
# spends 15.1 for the same packets, which takes a wrong answer from one in 4,000 to one in 100
# million. Twenty is the ceiling: at 28, one repair in sixteen would be fiction. It is ordering
# the bits by the slicer's confidence that finds the broken ones; no threshold on it does.
STARSIGHT_ERASURE_STEPS = (2, 4, 6, 8, 12, 16, 20)
STARSIGHT_MAX_ERASURES = STARSIGHT_ERASURE_STEPS[-1]

@lru_cache(maxsize=None)
def _crc_error_vectors():
    """ What flipping one bit does to a packet's checksum, by distance from the end

    The CRC is linear over GF(2), so what a set of flipped bits does to it is the XOR of what
    each does alone, and that depends only on how many bytes follow the bit. One table, built
    from the last byte backwards, serves every packet length.
    """
    vectors = [0] * (STARSIGHT_MAX_PACKET * 8)
    for bit in range(8):
        register = STARSIGHT_CRC_TABLE[1 << bit]
        for trailing in range(STARSIGHT_MAX_PACKET):
            vectors[(trailing << 3) | bit] = register
            register = (register >> 8) ^ STARSIGHT_CRC_TABLE[register & 0xFF]
    return vectors

def _solve_crc(vectors, target):
    """ Which of `vectors` XOR to `target`, as a bit mask over them, or None

    Gaussian elimination over GF(2) on 32 bit rows, each kept row indexed by its highest set
    bit, so reducing in descending order of that bit never disturbs a row already passed.
    """
    basis = []
    for index, vector in enumerate(vectors):
        mask = 1 << index
        for pivot, value, combination in basis:
            if vector >> pivot & 1:
                vector ^= value
                mask ^= combination
        if vector:
            basis.append((vector.bit_length() - 1, vector, mask))
            basis.sort(reverse=True)

    mask = 0
    for pivot, value, combination in basis:
        if target >> pivot & 1:
            target ^= value
            mask ^= combination
    return None if target else mask

def repair_starsight_packet(packet, lost, confidence=None):
    """ The packet the checksum says was sent, or None if it cannot be pinned down

    `lost` gives the offsets of bytes whose VBI line never sliced: unknown rather than wrong,
    so solving for them spends only their own bits of the checksum. Bits wrong but not known
    to be cost more, since their position has to be searched as well - about eleven bits for
    one and twenty for two - so the two are never combined: a packet that lost a line is
    repaired on its own, and flipped bits are searched for only where none was lost. Whatever
    comes back still has to tile into commands.

    On the 1994 KCET capture, of 154 packets whose header checksummed, 19 pass the body
    checksum outright and 71 more once repaired, all with sequence numbers rising in step.
    """
    if len(lost) > STARSIGHT_MAX_LOST_BYTES:
        return None

    target = starsight_crc(packet)
    if target == 0:
        return bytes(packet)

    table = _crc_error_vectors()
    end = len(packet) - 1

    # Erasures first: every bit of a lost byte, then the bits the slicer decided on the
    # smallest margin, which is where a dropout leaves its mark.
    erasures = [(offset << 3) | bit for offset in lost for bit in range(8)]
    known = set(erasures)
    if confidence is not None:
        for position in sorted(range(len(confidence)), key=confidence.__getitem__):
            if len(erasures) >= STARSIGHT_MAX_ERASURES:
                break
            if position not in known:
                erasures.append(position)
                known.add(position)

    # A lost byte is unknown outright, so all its bits have to be in every attempt.
    floor = len(lost) * 8
    tried = 0
    for count in STARSIGHT_ERASURE_STEPS:
        count = min(max(count, floor), len(erasures))
        if count == 0 or count == tried:
            continue
        tried = count

        chosen = erasures[:count]
        mask = _solve_crc([table[((end - (p >> 3)) << 3) | (p & 7)] for p in chosen], target)
        if mask is not None:
            repaired = bytearray(packet)
            for index, position in enumerate(chosen):
                if mask >> index & 1:
                    repaired[position >> 3] ^= 1 << (position & 7)
            # a candidate that does not tile is a reason to spend more of the budget
            if starsight_crc(repaired) == 0 and decode_starsight_commands(
                    repaired, STARSIGHT_HEADER, len(repaired) - STARSIGHT_TRAILER) is not None:
                return bytes(repaired)

        if count == len(erasures):
            break

    if lost:
        return None

    # No erasure explains it, so look for one or two flipped bits the confidence did not
    # flag. By three the candidates outnumber the checksum.
    where = {}
    for offset in range(len(packet)):
        for bit in range(8):
            where.setdefault(table[((end - offset) << 3) | bit], (offset, bit))

    for flips in _bit_error_candidates(where, target):
        repaired = bytearray(packet)
        for offset, bit in flips:
            repaired[offset] ^= 1 << bit
        if starsight_crc(repaired) == 0:
            return bytes(repaired)
    return None

def _bit_error_candidates(where, target):
    """ Sets of one and then two flipped bits that would account for `target` """
    single = where.get(target)
    if single is not None:
        yield (single,)
    for vector, first in where.items():
        second = where.get(target ^ vector)
        if second is not None and second != first:
            yield (first, second)

def take_starsight_packets(data, report=None, lost=(), confidence=None):
    """ Consume whole packets from the front of a growing buffer: `(packets, consumed)`

    Only a packet that has not fully arrived is worth keeping, so `consumed` runs up to the
    byte the next call has to look at again. `lost` and `confidence` are passed on to
    `repair_starsight_packet` for the bytes each packet spans.

    Both of the format's checksums are used, as SSLOAD.DLL uses them. The header's settles
    whether a 0x2C is a sync at all, and once it passes the size can be trusted, so a packet
    whose body fails is stepped over whole. `report` counts what failed, what was repaired,
    and any packet that checksums and still does not tile.
    """
    packets = []
    count = report if report is not None else Counter()
    last_sync = len(data) - STARSIGHT_HEADER
    offset = 0

    while offset <= last_sync:
        offset = data.find(STARSIGHT_SYNC, offset, last_sync + 1)
        if offset < 0:
            offset = last_sync + 1
            break

        size = _u16(data, offset + 1)
        if not STARSIGHT_MIN_PACKET <= size <= STARSIGHT_MAX_PACKET:
            offset += 1
            continue
        if starsight_crc(data[offset:offset + 9]) & 0xFFFF != _u16le(data, offset + 9):
            count['header crc'] += 1
            offset += 1
            continue
        if offset + size > len(data):
            # the rest of this packet is still on the wire
            break

        packet = data[offset:offset + size]
        if starsight_crc(packet) != 0:
            packet = repair_starsight_packet(
                packet,
                [position - offset for position in lost if offset <= position < offset + size],
                None if confidence is None else confidence[offset * 8:(offset + size) * 8])
            if packet is None:
                count['body crc'] += 1
                offset += size
                continue
            count['body crc repaired'] += 1

        commands = decode_starsight_commands(packet, STARSIGHT_HEADER, size - STARSIGHT_TRAILER)
        if commands is None:
            count['checksummed but did not tile'] += 1
        else:
            count['accepted'] += 1
            packets.append((starsight_time(_u32(packet, 3)), _u16(packet, 7), commands))
        offset += size

    return packets, offset


# Byte 2 of a Show Title carries three attribute bits above the compression flag. SSLOAD.DLL's
# type 6 handler tests exactly these masks and writes them to `TS Closed Caption`, `TS Stereo`
# and the broadcast property abbreviated `B/W`. A Show Description carries the same three bits
# as the title of the program it describes: on all 13,701 distinct title and description pairs
# the captured slots name, the two agree.
ATTRIBUTE_BLACK_AND_WHITE = 0x10
ATTRIBUTE_STEREO = 0x20
ATTRIBUTE_CLOSED_CAPTIONED = 0x40

def decode_show_title(command):
    """ `(show id, theme id, title)` - title is None when it did not decode

    The id is bytes 3-4, all SSLOAD.DLL reads; the flag byte's low nibble is no part of it.
    """
    return _u16(command, 3), _u16(command, 5), decode_starsight_string(command[2], command[7:])

def decode_show_description(command):
    """ `(description id, rating, text)` - text is None when it did not decode

    The extended form puts three bytes before the text, which then begins at 10 rather than
    7: a star and film rating, content advisory bits, and the last two digits of the year,
    zero for none. `rating` is None on the plain form.
    """
    flags = command[2]
    if not flags & DESCRIPTION_EXTENDED:
        return _u16(command, 3), None, decode_starsight_string(flags, command[7:])
    rating = ShowRating(command[7] >> RATING_STARS_SHIFT,
                        (command[7] >> RATING_CODE_SHIFT) & RATING_CODE_MASK,
                        command[8], 1900 + command[9] if command[9] else None)
    return _u16(command, 3), rating, decode_starsight_string(flags, command[10:])

def decode_show_list(command):
    """ A channel's schedule: `(channel, [(start, minutes, show id, description id, show
    group, flags)])`

    The channel's start is sent once and each slot carries a duration. The header's slot
    count is honoured, so a truncated command cannot walk the clock into the future, and an
    optional field is read only where it is present, so the slots before a truncation survive.
    """
    channel = _u16(command, 4) & CHANNEL_ID_MASK
    when = starsight_time(_u32(command, 6))
    slots = []
    offset = 11
    for _ in range(command[10]):
        if offset + 4 > len(command):
            break
        slot_flags, minutes, show_id = command[offset], command[offset + 1], \
            _u16(command, offset + 2)
        offset += 4
        description_id = show_group = None
        if slot_flags & SLOT_HAS_DESCRIPTION:
            if offset + 2 <= len(command):
                description_id = _u16(command, offset)
            offset += 2
        if slot_flags & SLOT_HAS_SHOW_GROUP:
            if offset + 2 <= len(command):
                show_group = _u16(command, offset)
            offset += 2
        slots.append((when, minutes, show_id, description_id, show_group, slot_flags))
        when += datetime.timedelta(minutes=minutes)
    return channel, slots

# Bit 6 of a Time command's byte 6. SSSCAN.EXE, scanning for the channel that carries
# StarSight, rejects one whose Time sets it: that channel is not a StarSight provider. The PBS
# stations clear it; the Nickelodeon feed, the guide for many markets at once on Pacific time,
# sets it on every Time it sends.
TIME_NOT_A_PROVIDER = 0x40

def decode_time(command):
    """ `(time, standard time zone offset in hours, daylight saving)` from a Time command

    The offset is the station's standard one; with daylight saving in force the wall clock
    is an hour ahead of it.
    """
    when = starsight_time(_u32(command, 2)) + datetime.timedelta(seconds=command[7])
    zone = command[6] & 0x0F
    return when, -zone if command[6] & 0x10 else zone, bool(command[6] & 0x80)

# Station Node Status, the station's own health, every one to ten minutes. Its times are
# Unix seconds, which nothing else in the format uses, and a time nothing has set holds
# 1980-01-01 00:00, the zero of a DOS clock. Byte 3 is the layout: 1 on the 1994 KCET
# capture, 3 on the 1995-1999 PBS tapes, 4 on the 1998 Nickelodeon one. Bytes 4-5 rise with
# the years in layouts 3 and 4 - 200, 403, 405 - and bytes 6-7 are the stream id the packet
# headers carry.
STATION_EPOCH = datetime.datetime(1970, 1, 1)
STATION_UNSET = 0x12CEA600
STATION_VERSION = 3
STATION_SOFTWARE = 4
STATION_STREAM = 6
STATION_BUILT = 8
STATION_ASSEMBLED = 12
STATION_TOTAL = 38
STATION_REMAINING = 40
STATION_DONE = 50

# Where each layout keeps the station clock. In any other it is found as the one field past
# the assembly time that reads as a time within a week after it.
STATION_CLOCK_AT = {1: 58, 3: 48, 4: 48}
STATION_CLOCK_WINDOW = 7 * 24 * 60 * 60

# Layouts 3 and 4 hold two maps of the last day at 58 and 238, a bit a minute, the first bit
# the most significant. The u16 at 56 counts the bits in use: it gains one a minute, and when
# it would pass 1,440 both maps turn a byte to the left and it drops by eight, so the bit
# before it is the current minute. On all 265 pairs of consecutive blocks the maps turned by
# exactly the minutes between their clocks. What a set bit means is not known.
STATION_MINUTES_USED = 56
STATION_MINUTE_MAPS = (58, 238)
STATION_DAY_MINUTES = 1440

# Then the station's inventory of its own guide database: a u16 count of what it holds of
# each of fourteen tables at 432, the stream id again at 460, the number of tables at 462, and
# a record per table of what it lacks - a u16 count and the lowest eight missing ids,
# ascending. The tables are the regions, channels, Show Titles and Show Descriptions it holds,
# then the schedules of ten days from the clock's own GMT day, each counting the channels with
# one. The channels it holds are exactly the Channel Data ids the capture carries, and a
# day's channels exactly its Show List ones. Layout 4 inserts an unset time and six zero bytes
# at 420, so all of this sits ten bytes later there.
STATION_LATER_BY = {3: 0, 4: 10}
STATION_HELD = 432
STATION_INVENTORY_STREAM = 460
STATION_TABLE_COUNT = 462
STATION_MISSING = 463
STATION_MISSING_BYTES = 18
STATION_MISSING_SHOWN = 8
STATION_TABLES = ('regions', 'channels', 'titles', 'descriptions')
STATION_DAYS = 10

# From software 403 on, a time at 721 that falls at the same local time every day at each
# station - 00:07 at WHYY, 00:56 at KET, 01:38 at the Ohio station, 00:30 at WVIZ - through a
# change of daylight saving, and is always the latest before the clock: a daily event at the
# station, of a kind nothing names.
STATION_DAILY = 721

StationNode = namedtuple('StationNode', 'version built assembled clock total done remaining '
                                        'software stream minutes minute_maps inventory daily')
StationTable = namedtuple('StationTable', 'held missing first_missing')
StationInventory = namedtuple('StationInventory', STATION_TABLES + ('days',))

def _station_time(seconds):
    return None if seconds == STATION_UNSET else STATION_EPOCH + datetime.timedelta(
        seconds=seconds)

def decode_station_minutes(command):
    """ `(bits in use, (map, map))` - each map its set minutes, as minutes before the clock -
    or None where the block carries no minute maps """
    if command[STATION_VERSION] not in STATION_LATER_BY \
            or len(command) < STATION_MINUTE_MAPS[-1] + STATION_DAY_MINUTES // 8:
        return None
    used = _u16(command, STATION_MINUTES_USED)
    if not STATION_DAY_MINUTES - 7 <= used <= STATION_DAY_MINUTES:
        return None
    return used, tuple(tuple(sorted((used - 1 - index) % STATION_DAY_MINUTES
                                    for index in range(STATION_DAY_MINUTES)
                                    if command[start + index // 8] & 0x80 >> index % 8))
                       for start in STATION_MINUTE_MAPS)

def decode_station_inventory(command):
    """ What the station holds and lacks of its own guide, or None where the block does not
    carry it: read only when there are fourteen tables and the stream id among them is the
    block's own, as on all 275 blocks of layouts 3 and 4 """
    later = STATION_LATER_BY.get(command[STATION_VERSION])
    if later is None:
        return None
    tables = len(STATION_TABLES) + STATION_DAYS
    missing_at = STATION_MISSING + later
    if len(command) < missing_at + tables * STATION_MISSING_BYTES \
            or command[STATION_TABLE_COUNT + later] != tables \
            or _u16(command, STATION_INVENTORY_STREAM + later) != _u16(command, STATION_STREAM):
        return None
    read = []
    for table in range(tables):
        record = missing_at + table * STATION_MISSING_BYTES
        missing = _u16(command, record)
        read.append(StationTable(
            _u16(command, STATION_HELD + later + table * 2), missing,
            tuple(_u16(command, record + 2 + i * 2)
                  for i in range(min(missing, STATION_MISSING_SHOWN)))))
    return StationInventory(*read[:len(STATION_TABLES)], tuple(read[len(STATION_TABLES):]))

def decode_station_node(command):
    """ What a Station Node Status command says, or None if it is short

    `clock` is the station's clock at sending: inside each capture's packet header window,
    stepping forward between instances. `assembled` is constant within a capture and an hour
    or two behind it, so it is when the block's contents were put together; layout 4 leaves
    it unset. Layout 1 counts through a cycle - total, done and remaining - which is read only
    where the three agree, as they do to the byte on all twelve the 1994 capture carries. The
    minute maps, the inventory and the daily time are layouts 3 and 4 only.
    """
    if len(command) <= STATION_DONE + 2:
        return None
    version = command[STATION_VERSION]
    assembled = _u32(command, STATION_ASSEMBLED)

    at = STATION_CLOCK_AT.get(version)
    if at is not None:
        clock = _station_time(_u32(command, at))
    else:
        found = [_u32(command, offset)
                 for offset in range(STATION_ASSEMBLED + 4, len(command) - 3)
                 if assembled <= _u32(command, offset) <= assembled + STATION_CLOCK_WINDOW]
        clock = _station_time(found[0]) if len(found) == 1 else None

    total, done, remaining = (_u16(command, STATION_TOTAL), _u16(command, STATION_DONE),
                              _u16(command, STATION_REMAINING))
    if version != 1 or done + remaining != total or not total:
        total = done = remaining = None

    minutes, maps = decode_station_minutes(command) or (None, None)
    later = STATION_LATER_BY.get(version)
    daily = None
    if later is not None and len(command) >= STATION_DAILY + later + 4:
        daily = _station_time(_u32(command, STATION_DAILY + later))

    return StationNode(version, _station_time(_u32(command, STATION_BUILT)),
                       _station_time(assembled), clock, total, done, remaining,
                       _u16(command, STATION_SOFTWARE), _u16(command, STATION_STREAM),
                       minutes, maps, decode_station_inventory(command), daily)

# Channel Data, type 4. The name is not stored as a string: byte 7 is a mask, from the top
# bit down, picking up to four of the eight bytes at 8-15 as a short label for a display too
# narrow for the rest. A cable service sets bit 1 of byte 5.
CHANNEL_NUMBER_HIGH_BIT = 0x80
CHANNEL_CALL_SIGN_AT = 8
CHANNEL_CALL_SIGN_BYTES = 8
CHANNEL_LABEL_WIDTH = 4
CHANNEL_SHOWS_CALL_SIGN = 0x02
# The loader splits the eight characters on the first '-' only from the fourth on, so a
# call sign is never shorter than three.
CHANNEL_NETWORK_MIN_CALL = 3

ChannelData = namedtuple('ChannelData', 'channel number call_sign network label shows_call_sign')

def decode_channel_data(command):
    """ `(channel, number, call sign, network, label, shows call sign)`, or None if short

    What names a channel; it does not create one, since SSLOAD.DLL ignores an id no Region
    has established. A station's eight characters are `CALL-NET` - `WKYC-NBC` on 3 - which
    the loader splits at the dash, looking the right up among the networks. A cable service
    has no number and its eight characters are its name, which can carry a dash of its own -
    `SCI-FI`, `CNN-HLN` - so only a station's is split. Byte 7 picks a cable service's label:
    `HEADLINE` gives `HDLN`. Stations send none.
    """
    if len(command) < CHANNEL_CALL_SIGN_AT + CHANNEL_CALL_SIGN_BYTES:
        return None
    channel = ((command[3] & 0x7F) << 8) | command[4]
    number = command[6] | (0x100 if command[3] & CHANNEL_NUMBER_HIGH_BIT else 0)
    shows_call_sign = bool(command[5] & CHANNEL_SHOWS_CALL_SIGN)

    characters = command[CHANNEL_CALL_SIGN_AT:CHANNEL_CALL_SIGN_AT + CHANNEL_CALL_SIGN_BYTES]
    text = bytes(characters).split(b'\x00')[0].decode('ascii', 'replace').rstrip()
    dash = text.find('-')
    if dash >= CHANNEL_NETWORK_MIN_CALL and not shows_call_sign:
        call_sign, network = text[:dash], text[dash + 1:]
    else:
        call_sign, network = text, ''

    label = ''.join(chr(characters[index]) for index in range(CHANNEL_CALL_SIGN_BYTES)
                    if command[7] & (0x80 >> index))[:CHANNEL_LABEL_WIDTH].rstrip()
    return ChannelData(channel, number, call_sign, network, label, shows_call_sign)

def channel_name(data, short=False):
    """ How a channel reads in a guide: `3 WKYC` for a station, a cable service's name, or
    with `short` its label """
    if data.shows_call_sign or not data.number:
        return (data.label if short and data.label else data.call_sign) or data.label
    return ('%d %s' % (data.number, data.call_sign)).strip()

# Theme Category and Theme Sub-Category, types 11 and 12, the only strings in the format that
# are never compressed. A category is an id and a NUL terminated name, under a version the
# loader reloads on. A sub-category belongs to the category its byte 3 names and lists the
# theme ids a Show Title carries, so it is what names a theme.
THEME_ENTRIES_AT = 5
THEME_ENTRY_HEADER = 3

def decode_theme_names(command):
    """ `(version, [(id, name, flag byte)])` from a Theme Category command, or None

    The flag byte is set on Movies, Sports, Entertainment/Variety and Comedy and clear on
    the other seven, the same on both captures that carry one; nothing says what it means.
    """
    if len(command) <= THEME_ENTRIES_AT:
        return None
    entries = []
    offset = THEME_ENTRIES_AT
    while len(entries) < command[4] and offset + THEME_ENTRY_HEADER <= len(command):
        identifier, length = command[offset], command[offset + 2]
        body = command[offset + THEME_ENTRY_HEADER:offset + THEME_ENTRY_HEADER + length]
        name = body.split(b'\0')[0].decode('latin1') if body else ''
        entries.append((identifier, name, command[offset + 1]))
        offset += THEME_ENTRY_HEADER + length
    return command[3], entries

def decode_theme_sub_categories(command):
    """ `(category id, [(name, [theme id], flag byte)])` from a Theme Sub-Category command

    An entry is its own length, a flag byte, a count of theme ids, the ids, and the name in
    what is left. The flag byte is 1 on 93 of the 125 captured, for reasons nothing names.
    """
    if len(command) <= THEME_ENTRIES_AT:
        return None
    entries = []
    offset = THEME_ENTRIES_AT
    while len(entries) < command[4] & 0x7F and offset + THEME_ENTRY_HEADER <= len(command):
        length, count = command[offset], command[offset + 2]
        if length < THEME_ENTRY_HEADER + count * 2 or offset + length > len(command):
            break
        themes = [_u16(command, offset + THEME_ENTRY_HEADER + i * 2) for i in range(count)]
        name = bytes(command[offset + THEME_ENTRY_HEADER + count * 2:offset + length])
        entries.append((name.split(b'\0')[0].decode('latin1'), themes, command[offset + 1]))
        offset += length
    return command[3], entries

# A Region command's header, then four bytes per channel in its lineup. Byte 5 bit 5 is set
# on a cable system's lineup and clear on an antenna's: the postal codes list every region
# that sets it as a cable region and every other as a broadcast one.
REGION_APPLY_NOW = 0x01
REGION_CABLE = 0x20
REGION_COUNT_AT = 10
REGION_ENTRIES_AT = 11
REGION_ENTRY_BYTES = 4

# The fourth byte of a lineup entry, which the loader never reads: the low two bits are the
# tier a cable system sells the channel in - basic, extended, then premium where HBO and
# Showtime sit, then pay per view - and bits 6 and 7 are set on a few dozen entries in
# thousands, for reasons nothing names.
REGION_TIER_MASK = 0x03
REGION_TIERS = ('basic', 'extended', 'premium', 'pay per view')

RegionData = namedtuple('RegionData', 'region immediate effective channels cable')

def decode_region(command):
    """ `(region id, apply now, effective from, [(channel id, number, tier byte)], cable)`

    The lineup: which channels a receiver in one territory - a cable system, or one market's
    stations for an antenna - can see and the number each sits on there. It is what binds a
    Show List's bare channel id to anything a viewer would recognise. A receiver keeps its
    own region id and ignores the rest. Unless byte 5 says to apply it now, the loader holds a
    lineup until the stream's clock reaches `effective`, which every captured one sets to the
    epoch. The count is one byte, so a lineup has at most 255 channels.
    """
    if len(command) < REGION_ENTRIES_AT:
        return None
    channels = []
    offset = REGION_ENTRIES_AT
    for _ in range(command[REGION_COUNT_AT]):
        if offset + REGION_ENTRY_BYTES > len(command):
            break
        # Channel Data's fields one byte tighter: a fifteen bit id, with the channel
        # number's ninth bit at the top of the first byte
        channels.append((((command[offset] & 0x7F) << 8) | command[offset + 1],
                         command[offset + 2]
                         | (0x100 if command[offset] & CHANNEL_NUMBER_HIGH_BIT else 0),
                         command[offset + 3]))
        offset += REGION_ENTRY_BYTES
    return RegionData(_u16(command, 3), bool(command[5] & REGION_APPLY_NOW),
                      starsight_time(_u32(command, 6)), channels,
                      bool(command[5] & REGION_CABLE))

def region_tier(tier):
    """ The tier a lineup entry's fourth byte names, with any of the other bits it sets """
    name = REGION_TIERS[tier & REGION_TIER_MASK]
    return name if not tier & ~REGION_TIER_MASK else '%s (0x%02x)' % (name, tier)


# --- the types the loader runs code for beyond the guide ---------------------------------
#
# The loader keeps a state byte (image offset 0x1AB00) and a flag byte (0x1AB34). The region
# setup in BPCCTL.DLL starts in state 1 with the viewer's postal code, a type 31 for that
# code moves it to 2, and only then are type 9 names kept. SSLOAD.DLL starts in state 3 with
# the region id the setup saved, and there Region, Channel Data, Show List, both theme
# commands and types 36 and 37 act only while type 42's flag is on.

RegionName = namedtuple('RegionName', 'region name')
PostalCode = namedtuple('PostalCode', 'code broadcast cable area unread')
Segment = namedtuple('Segment', 'message part last data')
TypedPayload = namedtuple('TypedPayload', 'identifier payload')
AddressedCommand = namedtuple('AddressedCommand', 'version regions day entries')
KeyedRecord = namedtuple('KeyedRecord', 'identifier flags parts')
TimedRecord = namedtuple('TimedRecord', 'identifier value when trailer')
Type42 = namedtuple('Type42', 'flag data')
AddressedPayload = namedtuple('AddressedPayload', 'address payload')

def decode_region_name(command):
    """ `(region id, name)` from a Region Name command, the name None when it does not decode

    Always compressed; BPCCTL.DLL decodes up to 34 characters of it for its list of regions.
    """
    if len(command) < 4 or command[1] < 4:
        return None
    return RegionName(_u16(command, 2), decompress_starsight_string(command[4:command[1]]))

def decode_cycle_start(command):
    """ The nine bytes of a Cycle Start command, which SSSCAN.EXE counts to know it has
    watched a whole carousel; nothing reads them """
    return bytes(command[:9]) if len(command) >= 9 else None

def decode_postal_code(command):
    """ `(postal code, broadcast regions, cable regions, news area, unread bytes)`

    Bytes 3-8 are the code as text, sent in its sort order, which is how SSSCAN.EXE knows it
    has passed the viewer's. Bytes 10 and 11 count the region ids BPCCTL.DLL offers for an
    antenna and for cable. The byte after them is the area a News Page is filed under for the
    code - weather for Akron, Ashtabula and Rochester is area 0x57 - and the last, like byte 9,
    holds one value on every code sent.
    """
    if len(command) < 12:
        return None
    first_count, second_count = command[10], command[11]
    at = 12
    first = [_u16(command, at + i * 2) for i in range(first_count)
             if at + i * 2 + 2 <= len(command)]
    at += first_count * 2
    second = [_u16(command, at + i * 2) for i in range(second_count)
              if at + i * 2 + 2 <= len(command)]
    at += second_count * 2
    trailer = bytes(command[at:at + 2])
    return PostalCode(bytes(command[3:9]).split(b'\0')[0].decode('latin1'), first, second,
                      trailer[0] if trailer else None, bytes(command[9:10]) + trailer[1:])

def decode_segmented(command):
    """ `(message, part, last part, data)` from a Segmented Command: byte 4 numbers the
    message, the high and low nibbles of byte 3 the part and the last part """
    if len(command) < 5:
        return None
    return Segment(command[4], command[3] >> 4, command[3] & 0x0F, bytes(command[5:]))

# The loader will not assemble a segmented command longer than this.
STARSIGHT_MAX_SEGMENTED = 3000

def join_segments(guide, segment):
    """ The command a Segmented Command completes, or None

    The parts have to arrive in order from 0, and a part of another message abandons the one
    held. The first begins with the command's own header and the last ends with a CRC over the
    whole, which has to check as a packet body's does. What never comes together is kept in
    `guide['undecoded']`.
    """
    held = guide['segmented']
    if held is not None and held.message != segment.message:
        _abandon(guide, held, 'the parts after these never came')
        held = None
    if held is None:
        if segment.part or len(segment.data) < 3 \
                or _u16(segment.data, 1) + STARSIGHT_TRAILER > STARSIGHT_MAX_SEGMENTED:
            guide['segmented'] = None
            _abandon(guide, segment, 'the parts before this one never came' if segment.part
                     else 'too long to put together')
            return None
        held = Segment(segment.message, 0, segment.last, b'')
    elif (segment.part, segment.last) != (held.part, held.last):
        guide['segmented'] = None
        _abandon(guide, held, 'a part came out of order')
        return None

    data = held.data + segment.data
    if segment.part < segment.last:
        guide['segmented'] = Segment(held.message, segment.part + 1, held.last, data)
        return None
    guide['segmented'] = None
    if starsight_crc(data) != 0:
        _abandon(guide, Segment(segment.message, segment.part, segment.last, data),
                 'its CRC did not check')
        return None
    return data[:-STARSIGHT_TRAILER]

def _abandon(guide, held, why):
    """ Keep the parts of a segmented message that will not be put together """
    if held.data:
        guide['undecoded'].append((COMMAND_SEGMENTED, 'message %d: %s' % (held.message, why),
                                   bytes(held.data)))

def decode_addressed(command):
    """ `(version, regions, day, [(byte, value)])` from a type 37 command

    Bytes 4-5 count a table of region ids from byte 6, and the loader reads no further unless
    its own region is among them. Then come the midnight the command is for, one of the next
    seven, and a count of three byte entries. The low nibble of byte 3 is a version; a day
    already held at that version is skipped.
    """
    if len(command) < 6:
        return None
    count = _u16(command, 4)
    at = 6 + count * 2
    if at + 5 > len(command):
        return None
    entries = [(command[i], _u16(command, i + 1))
               for i in range(at + 5, at + 5 + command[at + 4] * 3, 3) if i + 3 <= len(command)]
    return AddressedCommand(command[3] & 0x0F,
                            [_u16(command, 6 + i * 2) for i in range(count)],
                            starsight_time(_u32(command, at)), entries)

def decode_keyed_record(command):
    """ `(id, flags, three payloads)` from a type 38 command

    Bytes 3-4 are an id. Three lengths - the low three bits of byte 7, byte 8 and byte 9 -
    measure three payloads that run on from byte 12. Bytes 5-7 are kept as flags, as sent;
    the loader repacks them into one byte.
    """
    if len(command) < 12:
        return None
    parts = []
    at = 12
    for length in (command[7] & 0x07, command[8], command[9]):
        parts.append(bytes(command[at:at + length]))
        at += length
    return KeyedRecord(_u16(command, 3), (command[5], command[6], command[7]), parts)

def decode_timed_record(command):
    """ `(id, value, field, byte 11)` from a type 39 command

    Bytes 3-4 are an id, 5-6 a sixteen bit value and 7-10 a thirty-two bit one, which is the
    width and byte order of a time but is not known to be one, so it is left a number.
    """
    if len(command) < 12:
        return None
    return TimedRecord(_u16(command, 3), _u16(command, 5), _u32(command, 7), command[11])

def decode_sized_payload(command):
    """ `(id, payload)` from a type 40 command: bytes 3-4 an id, and the rest from byte 6 up
    to the length in bytes 1-2, stored whole """
    if len(command) < 6:
        return None
    return TypedPayload(_u16(command, 3), bytes(command[6:_u16(command, 1)]))

# Bits 4-5 of byte 8 of a type 42 command. The loader clears its flag for 0, sets it for 1
# and ignores the command for 2 and 3.
TYPE_42_SETTING = 0x30
TYPE_42_SETTING_SHIFT = 4

def decode_gate(command):
    """ Whether a type 42 command turns its flag on (True), off (False), or neither (None) """
    if len(command) < 9:
        return None
    return {0: False, 1: True}.get((command[8] & TYPE_42_SETTING) >> TYPE_42_SETTING_SHIFT)

def decode_type_42(command):
    """ `(flag, bytes)` from a type 42 command: the flag as `decode_gate` reads it, and the
    command whole, since nothing else in it is read. Both captured are identical, and bytes
    12-25 read as the digits 0123456780 01234567 5109876543. """
    if len(command) < 9:
        return None
    return Type42(decode_gate(command), bytes(command))

def decode_addressed_payload(command):
    """ `(address, payload)` from an Authorization or Key Distribution command: bytes 2-5
    address one receiver - a Key Distribution carousel runs through them in rising order -
    and the rest is opaque """
    if len(command) < 6:
        return None
    return AddressedPayload(_u32(command, 2), bytes(command[6:]))

SUBSCRIBER_RESET_ACTIONS = ((0x01, 'clear stored guide'),
                            (0x02, 'clear stored guide and stop loading'))

def decode_subscriber_reset(command):
    """ What a Subscriber Reset command asks a receiver to discard

    SSLOAD.DLL's type 13 handler clears its lineup, schedules and themes for either bit of
    byte 2, and for bit 1 then ignores guide commands until restarted. It acts only when bytes
    3-7 are zero or its own address. Unconfirmed against a broadcast.
    """
    if len(command) < 3:
        return None
    return [name for mask, name in SUBSCRIBER_RESET_ACTIONS if command[2] & mask]

def decode_sequence_number(command):
    return _u32(command, 2)

def decode_daylight_saving(command):
    """ `(daylight saving starts, daylight saving ends)` from a type 2 command: the April
    capture sends 1998-04-05 02:00 and 1998-10-25 01:00, the two US transitions of 1998 """
    return starsight_time(_u32(command, 2)), starsight_time(_u32(command, 6))

# The news service, types 32 to 34: news, sports, weather and horoscope pages, which the
# loader stubs and the captures carry inside Segmented Commands. Their texts are length
# prefixed and Huffman coded, a page's text in the clear when the top bit of its sixteen bit
# length is set. A text breaks lines and sets `X attributes - `i, `a, `d - that run to `e or
# to the end of the line.
NEWS_PLAIN = 0x8000
NEWS_ATTRIBUTE = '`'

NewsService = namedtuple('NewsService', 'fields name')
NewsSection = namedtuple('NewsSection', 'section fields name')
NewsPage = namedtuple('NewsPage', 'section page flags area when title text unread')

def _news_string(raw):
    """ A name or title: Huffman coded as the guide's are, or in the clear where it is all
    printable and does not decode """
    text = decompress_starsight_string(raw)
    if text is None and raw and all(0x20 <= b < 0x7F for b in raw):
        return bytes(raw).decode('latin1')
    return text

def news_text(text):
    """ A news text without its attributes, for a log line """
    if text is None:
        return None
    out, at = [], 0
    while at < len(text):
        if text[at] == NEWS_ATTRIBUTE:
            at += 2
            continue
        out.append(text[at])
        at += 1
    return ''.join(out)

def decode_news_service(command):
    """ `(bytes 3-6, name)` from a News Service command; byte 7 is the name's length. The one
    captured is in the clear: 'StarSight NEWS Services'. """
    if len(command) < 8:
        return None
    return NewsService(bytes(command[3:7]), _news_string(bytes(command[8:8 + command[7]])))

def decode_news_menu(command):
    """ `(byte 3, [(section, bytes 1-5, name)])` from a News Menu command

    Byte 4 counts the sections, each a number, five bytes nothing reads and a length prefixed
    name. A News Page names its section by the number.
    """
    if len(command) < 5:
        return None
    sections = []
    at = 5
    for _ in range(command[4]):
        if at + 7 > len(command):
            break
        end = at + 7 + command[at + 6]
        sections.append(NewsSection(command[at], bytes(command[at + 1:at + 6]),
                                    _news_string(bytes(command[at + 7:end]))))
        at = end
    return command[3], sections

def decode_news_page(command):
    """ One page of the news service, or None if it is short

    Byte 3 is its menu section, bytes 5-6 a page number - the weather pages are numbered in
    the alphabetical order of their towns - byte 7 flags, byte 10 the area a Postal Code files
    a viewer under, zero for everyone, and bytes 11-14 when it was written. Then the title,
    its length in byte 16, and the text, its length in the two bytes after the title. Bytes
    4, 8-9 and 15 are kept as sent.
    """
    if len(command) < 17:
        return None
    at = 17 + command[16]
    if at + 2 > len(command):
        return None
    length = _u16(command, at)
    raw = bytes(command[at + 2:at + 2 + (length & ~NEWS_PLAIN)])
    text = (raw.decode('latin1').replace('\r', '\n') if length & NEWS_PLAIN
            else decompress_starsight_string(raw, news=True))
    return NewsPage(command[3], _u16(command, 5), command[7], command[10],
                    starsight_time(_u32(command, 11)), _news_string(bytes(command[17:at])), text,
                    bytes((command[4], command[8], command[9], command[15])))

# The commands with no more to them than a log line, a table on the page and a list in the
# export, by type: how each is decoded, logged, drawn - its note, columns and a row from what
# was decoded, bytes nothing names shown as hex - and written to and read from the export.
OtherCommand = namedtuple('OtherCommand', 'decode log note columns row key write read')

_LOADER_NOTE = 'Every field as SSLOAD.DLL reads it, which is where this layout comes from.'

def _addressed_payload(key):
    return OtherCommand(
        decode_addressed_payload, lambda d: 'unit %-8d %d bytes' % (d.address, len(d.payload)),
        'Addressed to one receiver, by bytes 2-5; the rest is opaque.',
        (('Unit', 'num'), ('Bytes', 'num'), ('Payload', 'wrap')),
        lambda d: (d.address, len(d.payload), d.payload.hex(' ')), key,
        lambda d: {'unit': d.address, 'payload': d.payload.hex()},
        lambda e: AddressedPayload(e['unit'], bytes.fromhex(e['payload'])))

def _plural(count, word, plural=None):
    return '%d %s' % (count, word if count == 1 else plural or word + 's')

OTHER_COMMANDS = {
    COMMAND_CYCLE_START: OtherCommand(
        decode_cycle_start, bytes.hex, _LOADER_NOTE, (('Bytes', ''),),
        lambda d: (d.hex(' '),), 'cycleStarts',
        lambda d: {'data': d.hex()}, lambda e: bytes.fromhex(e['data'])),
    COMMAND_AUTHORIZATION: _addressed_payload('authorizations'),
    COMMAND_KEY_DISTRIBUTION: _addressed_payload('keyDistributions'),
    COMMAND_ADDRESSED: OtherCommand(
        decode_addressed,
        lambda d: '%s, %s, %s' % (_plural(len(d.regions), 'region'), _date(d.day),
                                  _plural(len(d.entries), 'entry', 'entries')),
        _LOADER_NOTE,
        (('Version', 'num'), ('Day (GMT)', ''), ('Regions', 'wrap'), ('Entries', 'wrap')),
        lambda d: (d.version, _minute(d.day), ' '.join(map(str, d.regions)),
                   ' '.join('%d:%d' % entry for entry in d.entries)), 'type37',
        lambda d: {'version': d.version, 'regions': d.regions, 'day': _iso(d.day),
                   'entries': [{'byte': b, 'value': v} for b, v in d.entries]},
        lambda e: AddressedCommand(e['version'], e['regions'], _from_iso(e['day']),
                                   [(x['byte'], x['value']) for x in e['entries']])),
    COMMAND_KEYED_RECORD: OtherCommand(
        decode_keyed_record,
        lambda d: 'id %-6d flags %s parts %s' % (
            d.identifier, ' '.join('%02x' % f for f in d.flags),
            ' '.join(str(len(part)) for part in d.parts)),
        _LOADER_NOTE,
        (('Id', 'num'), ('Flags', ''), ('First part', 'wrap'), ('Second part', 'wrap'),
         ('Third part', 'wrap')),
        lambda d: (d.identifier, bytes(d.flags).hex(' ')) + tuple(p.hex(' ') for p in d.parts),
        'type38',
        lambda d: {'id': d.identifier, 'flags': list(d.flags),
                   'parts': [part.hex() for part in d.parts]},
        lambda e: KeyedRecord(e['id'], tuple(e['flags']),
                              [bytes.fromhex(part) for part in e['parts']])),
    COMMAND_TIMED_RECORD: OtherCommand(
        decode_timed_record,
        lambda d: 'id %-6d value %-6d field %d' % (d.identifier, d.value, d.when),
        _LOADER_NOTE, (('Id', 'num'), ('Value', 'num'), ('Field', 'num'), ('Byte 11', 'num')),
        tuple, 'type39',
        lambda d: {'id': d.identifier, 'value': d.value, 'field': d.when, 'byte11': d.trailer},
        lambda e: TimedRecord(e['id'], e['value'], e['field'], e['byte11'])),
    COMMAND_SIZED_PAYLOAD: OtherCommand(
        decode_sized_payload,
        lambda d: 'id %-6d %s' % (d.identifier, _plural(len(d.payload), 'byte')),
        _LOADER_NOTE, (('Id', 'num'), ('Payload', 'wrap')),
        lambda d: (d.identifier, d.payload.hex(' ')), 'type40',
        lambda d: {'id': d.identifier, 'payload': d.payload.hex()},
        lambda e: TypedPayload(e['id'], bytes.fromhex(e['payload']))),
    COMMAND_GATE: OtherCommand(
        decode_type_42, lambda d: 'flag %s' % _gate_name(d.flag), _LOADER_NOTE,
        (('Flag', ''), ('Bytes', 'wrap')), lambda d: (_gate_name(d.flag), d.data.hex(' ')),
        'type42',
        lambda d: dict({'data': d.data.hex()}, **({'flag': d.flag} if d.flag is not None else {})),
        lambda e: Type42(e.get('flag'), bytes.fromhex(e['data']))),
}

def _gate_name(flag):
    return 'ignored' if flag is None else 'on' if flag else 'off'


def new_guide():
    """ What the receiver knows so far

    A Show List's slots are kept as `blocks`, the latest list for each channel and day, and
    `listings` reads them out. `pending` holds the slots still waiting for a title and
    `segmented` a Segmented Command being put together; neither is exported.
    """
    return {'blocks': {}, 'pending': {}, 'titles': {}, 'descriptions': {},
            # description id -> its flag byte, which carries a title's attribute bits
            'description_flags': {},
            # (channel, start) -> byte 3 of the Show List that sent that day, its version
            'list_versions': {},
            'clock': [], 'daylight': [], 'sequence': [], 'station': [],
            # the instants of the Time commands that said the channel is not a provider
            'not_provider': set(),
            'channel_data': {},
            # category id -> name and flag byte, and the version the categories came under
            'theme_categories': {}, 'theme_category_flags': {}, 'theme_version': None,
            # category id -> [(sub-category name, [theme id], flag byte)], as last sent
            'theme_lists': {},
            # region id -> its latest lineup, and what Region Name and Postal Code say of them
            'regions': {}, 'region_names': {}, 'postal_codes': {},
            'resets': [], 'encrypted': [],
            # the news service: its name, its menu's sections and the latest of each page,
            # and the bytes of each that nothing names
            'news': {'service': None, 'service_bytes': None, 'menu_byte': None,
                     'sections': {}, 'section_bytes': {}, 'pages': {}},
            # (time, stream id, sequence number or None) for every packet received
            'packets': [],
            # whatever arrived and could not be decoded, as `(type, why, bytes)`
            'undecoded': [],
            # the types in OTHER_COMMANDS, as decoded
            'loader_commands': defaultdict(list), 'segmented': None}

def listings(guide):
    """ Every slot the guide holds, as `(start, channel, minutes, show id, description id,
    show group, flags)`, in order of start, channel, length and show id """
    return sorted((slot for block in guide['blocks'].values() for slot in block),
                  key=lambda slot: slot[:4])

def theme_labels(guide):
    """ `{theme id: label}` from the Theme Category and Sub-Category commands received

    The sub-categories are the receiver's search menus rather than a naming: a theme sits in
    as many as fit it, and every category opens with an 'All ...' list of all its themes and
    closes with a 'Closed Captioned' one. So a theme is labelled with each category that lists
    it and the sub-categories of it that say something more - 'Movies / Action/Adventure;
    Action/Mystery/Horror / Action/Adventure/Thriller' - or the category alone.
    """
    found = defaultdict(dict)
    for category, entries in sorted(guide['theme_lists'].items()):
        parent = guide['theme_categories'].get(category, 'category %d' % category)
        for name, themes, _ in entries:
            for theme in themes:
                subs = found[theme].setdefault(parent, [])
                if not name.startswith('All ') and name != 'Closed Captioned':
                    subs.append(name)
    return {theme: '; '.join('%s / %s' % (parent, ', '.join(subs)) if subs else parent
                             for parent, subs in categories.items())
            for theme, categories in found.items()}

def _minute(when):
    return when.isoformat(' ', 'minutes')

def _second(when):
    return when.isoformat(' ', 'seconds')

def _date(when):
    return when.isoformat()[:10]

def _rating_note(rating):
    """ What the extended form of a Show Description adds to its log line """
    if rating is None:
        return ''
    advisories = description_advisories(rating.advisories)
    return '%s %-5s %-4s%s  ' % (
        rating.year or '----', rating_name(rating) or rating.code, '*' * rating.stars,
        ' (%s)' % ', '.join(advisories) if advisories else '')

def _slot_line(label, channel, start, minutes, show_id, title):
    return '%-12s ch %-6d %s GMT %4d min  id %-7d %s' % (
        label, channel, _minute(start), minutes, show_id, title)

# The handlers `describe_starsight_command` dispatches to, one per type: each keeps what its
# command says in the guide and returns the command's lines for the log.

def _undecoded(command_type, command, guide, why):
    guide['undecoded'].append((command_type, why, bytes(command)))
    return []

def _short(command_type, command, guide):
    """ Keep a command too short for its own layout, which the log does not show """
    return _undecoded(command_type, command, guide, 'shorter than its layout')

def _encrypted(command_type, command, guide):
    # A Show List keeps its header in the clear, so what it schedules and when can be read
    # even though the slots cannot.
    key = command[0] >> COMMAND_KEY_SHIFT & 1
    if command_type == COMMAND_SHOW_LIST and len(command) > 10:
        item = EncryptedCommand(command_type, key, _u16(command, 4) & CHANNEL_ID_MASK,
                                starsight_time(_u32(command, 6)), command[10], bytes(command))
        guide['encrypted'].append(item)
        return ['Show List    ch %-6d %s GMT  %d slots, encrypted with key %d' % (
            item.channel, _minute(item.start), item.slots, key)]
    guide['encrypted'].append(EncryptedCommand(command_type, key, None, None, None,
                                               bytes(command)))
    return ['Type %-8d %d bytes, encrypted with key %d' % (command_type, len(command), key)]

def _show_title(command_type, command, guide):
    show_id, theme, title = decode_show_title(command)
    if title is None:
        _undecoded(command_type, command, guide, 'title did not decompress')
        return ['Show Title   id %-7d theme %-6d (undecodable)' % (show_id, theme)]
    guide['titles'][show_id] = (theme, title, command[2])
    # the moment a receiver could first have shown each slot waiting for this title
    return ['Show Title   id %-7d theme %-6d %s' % (show_id, theme, title)] + [
        _slot_line('Guide', channel, start, minutes, show_id, title)
        for channel, start, minutes in guide['pending'].pop(show_id, ())]

def _show_description(command_type, command, guide):
    description_id, rating, text = decode_show_description(command)
    if text is None:
        _undecoded(command_type, command, guide, 'text did not decompress')
    else:
        guide['descriptions'][description_id] = (text, rating)
        guide['description_flags'][description_id] = command[2]
    return ['Show Desc    id %-7d %s%s' % (description_id, _rating_note(rating),
                                           '(undecodable)' if text is None else text)]

def _show_list(command_type, command, guide):
    # A Show List names its programs by id and the Show Title always comes later - not one of
    # 23,836 slots had its title in hand - so the schedule is logged as it arrives and the
    # slots wait in `pending`. A list replaces the one before it for the same channel and day.
    channel, slots = decode_show_list(command)
    guide['list_versions'][(channel, starsight_time(_u32(command, 6)))] = command[3]
    if slots:
        block = (channel, slots[0][0].date())
        pending = guide['pending']
        for show_id in {slot[3] for slot in guide['blocks'].pop(block, ())}:
            waiting = [entry for entry in pending.get(show_id, ()) if entry[0] != channel]
            if waiting:
                pending[show_id] = waiting
            else:
                pending.pop(show_id, None)
        guide['blocks'][block] = [(slot[0], channel) + slot[1:] for slot in slots]
        for start, minutes, show_id, _, _, _ in slots:
            pending.setdefault(show_id, []).append((channel, start, minutes))
    return [_slot_line('Show List', channel, start, minutes, show_id, '(title not sent yet)')
            for start, minutes, show_id, _, _, _ in slots]

def _channel_data(command_type, command, guide):
    data = decode_channel_data(command)
    if data is None:
        return _short(command_type, command, guide)
    guide['channel_data'][data.channel] = data
    return ['Channel Data ch %-6d number %-4d %s%s%s' % (
        data.channel, data.number, data.call_sign or '(no call sign)',
        ' (%s)' % data.network if data.network else '',
        ', short %s' % data.label if data.label and data.label != data.call_sign else '')]

def _theme_category(command_type, command, guide):
    named = decode_theme_names(command)
    if named is None:
        return _short(command_type, command, guide)
    version, entries = named
    guide['theme_version'] = version
    for identifier, name, flags in entries:
        guide['theme_categories'][identifier] = name
        guide['theme_category_flags'][identifier] = flags
    return ['Theme Category version %-4d %s' % (version, _plural(len(entries), 'name'))]

def _theme_sub_category(command_type, command, guide):
    named = decode_theme_sub_categories(command)
    if named is None:
        return _short(command_type, command, guide)
    category, entries = named
    guide['theme_lists'][category] = entries
    return ['Theme Sub-Category category %-4d %s' % (
        category, ', '.join('%s (%d)' % (name, len(themes)) for name, themes, _ in entries))]

def _region(command_type, command, guide):
    region = decode_region(command)
    if region is None:
        return _short(command_type, command, guide)
    guide['regions'][region.region] = region
    return ['Region       id %-6d %s lineup, %s%s%s' % (
        region.region, 'cable' if region.cable else 'antenna',
        _plural(len(region.channels), 'channel'),
        ', from %s' % region.effective if region.effective != STARSIGHT_EPOCH else '',
        ' (now)' if region.immediate else '')]

def _region_name(command_type, command, guide):
    named = decode_region_name(command)
    if named is None:
        return _short(command_type, command, guide)
    guide['region_names'][named.region] = named.name
    if named.name is None:
        _undecoded(command_type, command, guide, 'name did not decompress')
    return ['Region Name  region %-6d %s' % (
        named.region, '(undecodable)' if named.name is None else named.name)]

def _postal_code(command_type, command, guide):
    code = decode_postal_code(command)
    if code is None:
        return _short(command_type, command, guide)
    guide['postal_codes'][code.code] = code
    return ['Postal Code  code %-6s %d broadcast and %s' % (
        code.code, len(code.broadcast), _plural(len(code.cable), 'cable region'))]

def _news(command_type, command, guide):
    news = guide['news']
    if command_type == COMMAND_NEWS_SERVICE:
        service = decode_news_service(command)
        if service is None:
            return _short(command_type, command, guide)
        news['service'], news['service_bytes'] = service.name, service.fields
        if service.name is None:
            _undecoded(command_type, command, guide, 'name did not decompress')
        return ['News Service %s' % (service.name or '(undecodable)')]

    if command_type == COMMAND_NEWS_MENU:
        menu = decode_news_menu(command)
        if menu is None:
            return _short(command_type, command, guide)
        news['menu_byte'], sections = menu
        for section in sections:
            news['sections'][section.section] = section.name
            news['section_bytes'][section.section] = section.fields
        return ['News Menu    %s' % ', '.join('%d %s' % (section.section, section.name)
                                             for section in sections)]

    page = decode_news_page(command)
    if page is None:
        return _short(command_type, command, guide)
    news['pages'][(page.section, page.page, page.area)] = page
    if page.text is None or page.title is None:
        _undecoded(command_type, command, guide, 'text did not decompress')
    return ['News Page    section %-3d page %-5d area 0x%02x  %s  %s: %s' % (
        page.section, page.page, page.area, _minute(page.when), page.title,
        (news_text(page.text) or '(undecodable)').replace('\n', ' / '))]

def _reset(command_type, command, guide):
    actions = decode_subscriber_reset(command)
    if actions is None:
        return _short(command_type, command, guide)
    guide['resets'].append((tuple(actions), bytes(command)))
    return ['Reset        %s' % (', '.join(actions) or 'nothing')]

def _segmented(command_type, command, guide):
    segment = decode_segmented(command)
    if segment is None:
        return _short(command_type, command, guide)
    joined = join_segments(guide, segment)
    lines = ['Segmented Command message %-4d part %d of %d' % (
        segment.message, segment.part + 1, segment.last + 1)]
    if joined is not None:
        lines += describe_starsight_command(joined[0] & 0x3F, joined, guide)
    return lines

def _station_node(command_type, command, guide):
    # SSLOAD.DLL sends type 21 to its trace function like type 20, so beyond the fields read
    # here the block is kept whole
    node = decode_station_node(command)
    guide['station'].append((node, bytes(command)))
    if node is None:
        return ['Station Node %d bytes' % len(command)]
    stock = node.inventory
    return ['Station Node v%d %d bytes  stream %d  assembled %s  clock %s%s%s' % (
        node.version, len(command), node.stream,
        _minute(node.assembled) if node.assembled else '(unset)',
        '(not found)' if node.clock is None else _second(node.clock) + ' GMT',
        '  %d of %d done, %d to go' % (node.done, node.total, node.remaining)
        if node.total is not None else '',
        '  holds %d regions, %d channels, %d titles, %d descriptions' % (
            stock.regions.held, stock.channels.held, stock.titles.held,
            stock.descriptions.held) if stock else '')]

def _sequence(command_type, command, guide):
    guide['sequence'].append(decode_sequence_number(command))
    return []

def _daylight(command_type, command, guide):
    starts, ends = decode_daylight_saving(command)
    if (starts, ends) not in guide['daylight']:
        guide['daylight'].append((starts, ends))
    return ['Daylight     %s GMT to %s GMT' % (_minute(starts), _minute(ends))]

def _time(command_type, command, guide):
    when, zone, daylight = decode_time(command)
    guide['clock'].append((when, zone, daylight))
    not_provider = bool(command[6] & TIME_NOT_A_PROVIDER)
    if not_provider:
        guide['not_provider'].add(when)
    return ['Time         %s GMT  station offset %+d hours%s%s' % (
        _second(when), zone, ', daylight saving' if daylight else '',
        ', not a provider' if not_provider else '')]

def _other(command_type, command, guide):
    kind = OTHER_COMMANDS[command_type]
    decoded = kind.decode(command)
    if decoded is None:
        return _short(command_type, command, guide)
    guide['loader_commands'][command_type].append(decoded)
    return ['%-12s %s' % (COMMAND_NAMES.get(command_type, 'type %d' % command_type),
                          kind.log(decoded))]

# Why a command of a type the loader has no code for is kept whole.
NOT_READ = 'the loader does not read this type'

def _not_read(command_type, command, guide):
    return _undecoded(command_type, command, guide, NOT_READ)

_DESCRIBE = {
    COMMAND_TIME: _time, COMMAND_DAYLIGHT_SAVING: _daylight, COMMAND_REGION: _region,
    COMMAND_CHANNEL_DATA: _channel_data, COMMAND_SHOW_LIST: _show_list,
    COMMAND_SHOW_TITLE: _show_title, COMMAND_SHOW_DESCRIPTION: _show_description,
    COMMAND_REGION_NAME: _region_name, COMMAND_THEME_CATEGORY: _theme_category,
    COMMAND_THEME_SUB_CATEGORY: _theme_sub_category, COMMAND_SUBSCRIBER_RESET: _reset,
    COMMAND_SEQUENCE_NUMBER: _sequence, COMMAND_STATION_NODE_STATUS: _station_node,
    COMMAND_POSTAL_CODE: _postal_code, COMMAND_NEWS_SERVICE: _news, COMMAND_NEWS_MENU: _news,
    COMMAND_NEWS_PAGE: _news, COMMAND_SEGMENTED: _segmented,
}
_DESCRIBE.update({command_type: _other for command_type in OTHER_COMMANDS})

def describe_starsight_command(command_type, command, guide):
    """ The lines one command contributes to the log, in broadcast order

    Everything is kept in `guide` as well, for the page and the export. The loader runs no
    handler for an encrypted command, so one is kept whole.
    """
    if command[0] & COMMAND_ENCRYPTED:
        return _encrypted(command_type, command, guide)
    return _DESCRIBE.get(command_type, _not_read)(command_type, command, guide)


STARSIGHT_HTML_STYLE = """
:root{
 --px:2.6px;--row:26px;--chan:78px;
 --set:#0b0b0c;--screen:#121214;--ink:#e6e6df;--dim:#9a9a90;--rule:#34353b;
 --banner:#f7f0a4;--bar:#8d8d7b;--barink:#14140d;
 --badge:#aec0e8;--badgeink:#16255f;--badgeedge:#5c6d9e;
 --cell:#a9dda1;--cellink:#0d1f0a;--celledge:#5f8f58;--ppv:#e8d49a;
 --hi:#f2e500;--shadow:#00000066;
}
*{box-sizing:border-box}
body{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px;margin:0;
 padding:18px;background:var(--set);color:var(--ink)}
main{max-width:1600px;margin:0 auto}
h1{font-size:1.15em;margin:0 0 .3em;letter-spacing:.04em}
h2{font-size:1em;margin:2.2em 0 .4em;color:var(--banner)}
p{margin:0 0 .5em;color:var(--dim);max-width:62em}
a{color:var(--badge)}

/* the set: a guide screen inside its surround */
.set{background:#000;border:2px solid #2a2a2c;border-radius:10px;padding:10px;
 margin:0 0 1.5em;box-shadow:0 2px 18px #0008}
.banner{background:var(--banner);color:#111;text-align:center;font-weight:700;
 letter-spacing:.7em;padding:5px 0 5px .7em;font-size:1.05em}
.daybar{display:flex;align-items:stretch;background:var(--bar);color:var(--barink)}
.daybar .date{background:#111;color:var(--banner);padding:3px 10px;font-weight:700;
 min-width:var(--chan);text-align:center;white-space:nowrap}
.daybar button{font:inherit;border:0;background:transparent;color:var(--barink);
 padding:3px 12px;cursor:pointer;letter-spacing:.12em}
.daybar button[aria-current=true]{background:var(--banner);color:#111;font-weight:700}

.guide{overflow:auto;max-height:72vh;background:var(--screen);
 scrollbar-color:var(--bar) #000}
.head{display:flex;position:sticky;top:0;z-index:3}
.corner{position:sticky;left:0;z-index:4;width:var(--chan);flex:0 0 var(--chan);
 background:#111;border-right:2px solid #000}
.ticks{position:relative;height:22px;background:var(--bar);flex:0 0 auto}
.ticks b{position:absolute;left:calc(var(--a) * var(--px));top:0;height:22px;
 padding:3px 0 0 4px;color:var(--barink);font-weight:400;white-space:nowrap;
 border-left:1px solid #0006}
.ticks b.day{background:#111;color:var(--banner);font-weight:700;
 border-left:2px solid var(--banner);padding-right:8px}

.row{display:flex;height:var(--row)}
.row[hidden]{display:none}
.pick{display:flex;gap:8px;align-items:center;background:var(--bar);color:var(--barink);
 padding:3px 10px;border-top:1px solid #000}
.pick select{font:inherit;max-width:60em}
.badge{position:sticky;left:0;z-index:2;width:var(--chan);flex:0 0 var(--chan);
 background:var(--badge);color:var(--badgeink);border:1px solid var(--badgeedge);
 border-radius:4px;text-align:center;line-height:calc(var(--row) - 4px);
 font-weight:700;margin:1px 2px 1px 0}
.lane{position:relative;flex:0 0 auto;height:var(--row);
 background:repeating-linear-gradient(to right,transparent 0,
  transparent calc(30 * var(--px) - 1px),#ffffff14 calc(30 * var(--px) - 1px),
  #ffffff14 calc(30 * var(--px)))}
.lane i{position:absolute;top:1px;height:calc(var(--row) - 3px);
 left:calc(var(--a) * var(--px));width:calc(var(--b) * var(--px) - 2px);
 background:var(--cell);color:var(--cellink);border:1px solid var(--celledge);
 font-style:normal;line-height:calc(var(--row) - 5px);padding:0 5px;
 overflow:hidden;white-space:nowrap;cursor:default}
.lane i.ppv{background:var(--ppv);border-color:#8a6a2e}
.lane i.joined{border-left:4px solid #6a7b3a}
.lane i.on{background:var(--hi);border-color:#111;
 box-shadow:3px 3px 0 var(--shadow);z-index:1}
.lane i u{text-decoration:none;color:#2c5a26;font-size:.85em}

.info{display:flex;gap:2px;margin-top:6px;font-size:.95em}
.info span{background:var(--badge);color:var(--badgeink);padding:3px 8px;
 border:1px solid var(--badgeedge);white-space:nowrap}
.info span:empty{display:none}
.key{display:flex;gap:14px;flex-wrap:wrap;margin:6px 2px 0;color:var(--dim);
 font-size:.9em;align-items:center}
.key b{font-weight:400;color:var(--ink)}
.key s{text-decoration:none;display:inline-block;width:14px;height:12px;
 vertical-align:-1px;margin-right:4px;border:1px solid var(--celledge);
 background:var(--cell)}
.key s.ppv{background:var(--ppv);border-color:#8a6a2e}
.key s.joined{background:var(--cell);border-left:4px solid #6a7b3a}
.info .wide{background:var(--cell);color:var(--cellink);border-color:var(--celledge);
 flex:1 1 auto;overflow:hidden;text-overflow:ellipsis}
.info .lit{background:var(--hi);color:#111;border-color:#111;font-weight:700}

table{border-collapse:collapse;margin:0 0 .5em}
th,td{border:1px solid var(--rule);padding:2px 8px;text-align:left;vertical-align:top}
th{cursor:pointer;white-space:nowrap;background:#1d1e22;color:var(--banner)}
td{white-space:nowrap}
tbody tr:nth-child(even) td{background:#17181b}
.wrap{white-space:normal;max-width:48em}
.num{text-align:right}
td b{color:var(--banner)}
td em{font-style:normal;color:var(--badge)}
.lede{margin:0 0 1em}

/* the data tables, each folded away under its heading */
.tools{display:flex;gap:8px;align-items:center;margin:2.2em 0 .4em}
.tools h2{margin:0}
.tools button{font:inherit;background:#1d1e22;color:var(--ink);border:1px solid var(--rule);
 border-radius:3px;padding:2px 10px;cursor:pointer}
details.data{border-top:1px solid var(--rule)}
details.data>summary{cursor:pointer;list-style:none;display:flex;gap:1em;align-items:baseline;
 padding:.45em 0}
details.data>summary::-webkit-details-marker{display:none}
details.data>summary::before{content:'\\25b8';color:var(--banner);width:1em}
details.data[open]>summary::before{content:'\\25be'}
details.data>summary h2{display:inline;margin:0}
details.data>summary span{color:var(--dim)}
details.data[open]{padding-bottom:1em}

/* the news service, as a receiver showed it */
.news .bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;background:var(--bar);
 color:var(--barink);padding:3px 10px}
.news .bar input,.news .bar select{font:inherit}
.news .bar input{width:7ch}
.newsbody{display:flex;gap:2px;min-height:24em;background:var(--screen);flex-wrap:wrap}
.newsbody nav{display:flex;flex-direction:column;gap:2px;padding:4px;background:#000;
 flex:0 0 auto;max-height:36em;overflow:auto}
.newsbody nav button{font:inherit;text-align:left;border:1px solid var(--badgeedge);
 background:var(--badge);color:var(--badgeink);border-radius:3px;padding:3px 8px;
 cursor:pointer;white-space:nowrap}
.newsbody nav button[aria-pressed=true]{background:var(--hi);color:#111;border-color:#111;
 font-weight:700}
.newsbody nav button:disabled{opacity:.35;cursor:default}
.newsbody nav button small{color:#3a4a78;margin-left:.6em}
.newsbody .pages button{background:var(--cell);color:var(--cellink);
 border-color:var(--celledge)}
.newsbody article{flex:1 1 30em;padding:10px 16px;min-width:0}
.newsbody article h3{margin:0 0 .2em;color:var(--banner);font-size:1.05em;letter-spacing:.04em}
.newsbody article p{margin:0 0 .8em}
.newsbody article pre{margin:0;font:inherit;white-space:pre-wrap;color:var(--ink);
 line-height:1.45}
.newsbody article pre b{color:var(--hi)}
.newsbody article pre em{font-style:normal;color:var(--badge)}
"""

STARSIGHT_HTML_SCRIPT = """
var guide=document.getElementById('guide');
if(guide){
  var perMinute=parseFloat(getComputedStyle(document.documentElement)
                           .getPropertyValue('--px'));
  var from=new Date(guide.dataset.from.replace(' ','T'));
  var days=[].slice.call(document.querySelectorAll('.daybar button'));

  days.forEach(function(button){
    button.addEventListener('click',function(){
      guide.scrollLeft=button.dataset.at*perMinute;
    });
  });
  function markDay(){
    var at=guide.scrollLeft/perMinute, current=days[0];
    days.forEach(function(b){ if(+b.dataset.at<=at+1) current=b; });
    days.forEach(function(b){
      b.setAttribute('aria-current', b===current ? 'true' : 'false');
    });
  }
  guide.addEventListener('scroll',markDay); markDay();

  var box={ch:document.getElementById('i-ch'),title:document.getElementById('i-title'),
           show:document.getElementById('i-show'),time:document.getElementById('i-time'),
           flags:document.getElementById('i-flags')};
  var NAMES={P:'pay per view',C:'closed captioned',S:'stereo',B:'black and white'};
  var lit=null;
  function clock(date){
    var h=date.getHours()%12||12;
    return h+':'+('0'+date.getMinutes()).slice(-2)+(date.getHours()<12?'A':'P');
  }
  guide.addEventListener('pointerover',function(event){
    var cell=event.target.closest('.lane i');
    if(!cell||cell===lit) return;
    if(lit) lit.classList.remove('on');
    lit=cell; cell.classList.add('on');

    var style=cell.getAttribute('style')||'';
    var at=+(/--a:(-?\\d+)/.exec(style)||[])[1];
    var run=+(/--b:(\\d+)/.exec(style)||[])[1];
    var start=new Date(from.getTime()+at*60000);
    var stop=new Date(start.getTime()+run*60000);
    var flags=(cell.getAttribute('f')||'').split('').map(function(f){return NAMES[f]});
    if(cell.classList.contains('joined')) flags.push('already in progress');

    box.ch.textContent=cell.closest('.row').querySelector('.badge').textContent;
    var text=window.DESC&&DESC[cell.getAttribute('d')];
    box.title.textContent=cell.firstChild.textContent+(text?'  -  '+text:'');
    box.show.textContent='show '+cell.getAttribute('n')
      +(cell.getAttribute('t')?'  theme '+cell.getAttribute('t'):'')
      +(cell.getAttribute('d')?'  description '+cell.getAttribute('d'):'')
      +(cell.getAttribute('g')?'  group '+cell.getAttribute('g'):'')
      +(cell.getAttribute('x')?'  flag byte 0x'+(+cell.getAttribute('x')).toString(16):'');
    box.time.textContent=start.toDateString().slice(0,10).toUpperCase()
      +'  '+clock(start)+'-'+clock(stop)+'  '+run+' min';
    box.flags.textContent=flags.join(', ');
  });
}

var pick=document.getElementById('lineup');
if(pick&&guide){
  var rows=[].slice.call(guide.querySelectorAll('.row'));
  rows.forEach(function(r){r.dataset.t=r.querySelector('.badge').textContent});
  var count=document.getElementById('lineup-count');
  pick.addEventListener('change',function(){
    var lineup=LINEUPS[pick.value], at={};
    if(lineup) lineup.forEach(function(e){at[e[0]]=e[1]});
    var shown=rows.filter(function(r){
      var n=at[r.dataset.c], badge=r.querySelector('.badge');
      r.hidden=!!lineup&&n===undefined;
      badge.textContent=lineup&&n!==undefined?n+' '+(SHORT[r.dataset.c]||r.dataset.c):r.dataset.t;
      return !r.hidden;
    });
    if(lineup) shown.sort(function(a,b){return at[a.dataset.c]-at[b.dataset.c]});
    var frag=document.createDocumentFragment();
    (lineup?shown:rows).forEach(function(r){frag.appendChild(r)});
    guide.appendChild(frag);
    count.textContent=lineup?shown.length+' of its '+lineup.length+' channels have listings':'';
  });
}

var news=document.getElementById('news');
if(news){
  var sectionsBox=news.querySelector('.sections'), pagesBox=news.querySelector('.pages'),
      screen=news.querySelector('article'), area=document.getElementById('news-area'),
      zip=document.getElementById('news-zip'), zipNote=document.getElementById('news-zip-note'),
      current=null, shown=null;
  function fits(p){ return !area.value||!p[2]||String(p[2])===area.value; }
  function button(box, label, count, pressed, act){
    var b=document.createElement('button');
    b.type='button'; b.textContent=label; b.setAttribute('aria-pressed', pressed);
    if(count!==null){ var n=document.createElement('small'); n.textContent=count; b.appendChild(n); }
    b.disabled=count===0; b.addEventListener('click', act); box.appendChild(b);
  }
  function draw(){
    sectionsBox.textContent=''; pagesBox.textContent='';
    var pages=NEWS.pages.filter(fits);
    if(current===null||!pages.some(function(p){return p[0]===current}))
      current=(pages[0]||[null])[0];
    NEWS.sections.forEach(function(s){
      var n=pages.filter(function(p){return p[0]===s[0]}).length;
      button(sectionsBox, s[1], n, s[0]===current, function(){ current=s[0]; shown=null; draw(); });
    });
    var here=pages.filter(function(p){return p[0]===current});
    if(shown===null||here.indexOf(shown)<0) shown=here[0]||null;
    here.forEach(function(p){
      button(pagesBox, p[4], null, p===shown, function(){ shown=p; draw(); });
    });
    if(!shown){ screen.innerHTML='<p>No page in this section for this area.</p>'; return; }
    screen.innerHTML='<h3></h3><p></p><pre></pre>';
    screen.querySelector('h3').textContent=shown[4];
    screen.querySelector('p').textContent='written '+shown[3]+' GMT  -  page '+shown[1]
      +(shown[2]?'  -  area 0x'+('0'+shown[2].toString(16)).slice(-2):'  -  every area');
    screen.querySelector('pre').innerHTML=shown[5];
  }
  area.addEventListener('change', draw);
  if(zip) zip.addEventListener('input', function(){
    var hit=NEWS.postal[zip.value.trim()];
    zipNote.textContent=zip.value.trim()?(hit===undefined?'no such code':''):'';
    if(hit!==undefined){ area.value=hit?String(hit):''; draw(); }
  });
  draw();
}

document.querySelectorAll('.tools button').forEach(function(b){
  b.addEventListener('click', function(){
    document.querySelectorAll('details.data').forEach(function(d){
      d.open=b.dataset.open==='1';
    });
  });
});

document.querySelectorAll('table').forEach(function(table){
  var body=table.tBodies[0], head=table.tHead.rows[0].cells, keys=[];
  function apply(){
    var rows=[].slice.call(body.rows);
    rows.sort(function(a,b){
      for(var i=0;i<keys.length;i++){
        var k=keys[i].k, x=a.cells[k].textContent, y=b.cells[k].textContent;
        if(x===y) continue;
        var n=Number(x), m=Number(y);
        var c=(x!==''&&y!==''&&!isNaN(n)&&!isNaN(m))?n-m:x.localeCompare(y);
        if(c) return keys[i].down?-c:c;
      }
      return 0;
    });
    var frag=document.createDocumentFragment();
    rows.forEach(function(r){frag.appendChild(r)});
    body.appendChild(frag);
    [].forEach.call(head,function(h){h.querySelector('b').textContent=''});
    keys.forEach(function(e,i){
      head[e.k].querySelector('b').textContent=' '+(e.down?'desc':'asc')+(i+1);
    });
  }
  [].forEach.call(head,function(h,k){
    h.addEventListener('click',function(){
      if(keys[0]&&keys[0].k===k) keys[0].down=!keys[0].down;
      else keys=[{k:k,down:false}].concat(keys.filter(function(e){return e.k!==k}));
      apply();
    });
  });
});
"""

# How far either side of the middle of a schedule the guide grid will draw: enough for any
# real guide, and for tapes months apart merged together, but not for a corrupted timestamp
# to ask for a grid millions of columns wide.
GUIDE_MAX_DAYS = 400

def _clock_label(when):
    """ 8:00P, the way the guide wrote a time """
    hour = when.hour % 12 or 12
    return '%d:%02d%s' % (hour, when.minute, 'A' if when.hour < 12 else 'P')

def _letters(flags):
    """ A title's attribute bits as the grid writes them """
    return ''.join(letter for mask, letter in ((ATTRIBUTE_CLOSED_CAPTIONED, 'C'),
                                               (ATTRIBUTE_STEREO, 'S'),
                                               (ATTRIBUTE_BLACK_AND_WHITE, 'B'))
                   if flags & mask)

def _guide_grid(out_func, slots, titles, descriptions, offset, labels, lineups=()):
    """ The schedule laid out the way the guide itself laid it out

    Channels down the side, half hours across the top, and a program drawn as one cell as
    wide as it is long - US6498895B2's "array of irregular cells, which vary in length,
    corresponding to different television program lengths". A cell is placed by arithmetic,
    since a slot can be 5 minutes or 240, and carries the rest of its slot's fields for the
    box underneath to show.

    `labels` names a channel id `(badge, tooltip, short)` where Channel Data has named it.
    `lineups` are the Region commands' `(region, option text, [(channel, number)])`: picking
    one shows only its channels, in its order and under its numbers, as a receiver set up
    for that cable system or antenna drew the guide.
    """
    if not slots:
        return

    esc = html.escape
    shift = datetime.timedelta(hours=offset or 0)
    placed = [(slot[0] + shift, slot) for slot in slots]
    middle = sorted(start for start, _ in placed)[len(placed) // 2]
    reach = datetime.timedelta(days=GUIDE_MAX_DAYS)
    drawn = [(start, slot) for start, slot in placed if abs(start - middle) <= reach]
    adrift = [slot for start, slot in placed if abs(start - middle) > reach]

    window = min(start for start, _ in drawn).replace(minute=0, second=0, microsecond=0)
    last = max(start + datetime.timedelta(minutes=slot[2]) for start, slot in drawn)
    span = int((last - window).total_seconds() // 60 + 29) // 30 * 30
    minutes_of = lambda when: int((when - window).total_seconds() // 60)

    channels = defaultdict(list)
    for start, slot in drawn:
        channels[slot[1]].append((start, slot[2]) + slot[3:])

    # the capture rarely starts at midnight, so the first day is a partial one with a tab
    days = [window]
    day = (window + datetime.timedelta(days=1)).replace(hour=0)
    while day < last:
        days.append(day)
        day += datetime.timedelta(days=1)

    out_func("<div class='set'><div class='banner'>STARSIGHT</div>")
    out_func("<div class='daybar'><span class='date'>%s</span>"
             % window.strftime('%b %-d').upper())
    for day in days:
        out_func("<button type='button' data-at='%d'>%s</button>"
                 % (minutes_of(day), day.strftime('%a').upper()))
    out_func("</div>")

    if lineups:
        out_func("<div class='pick'><label for='lineup'>Lineup</label><select id='lineup'>"
                 "<option value=''>every channel with listings, by channel id</option>%s"
                 "</select><span id='lineup-count'></span></div>" % ''.join(
                     "<option value='%d'>%s</option>" % (region, esc(text))
                     for region, text, _ in lineups))
    out_func("<div class='guide' id='guide' data-from='%s'>" % window.isoformat(' '))
    out_func("<div class='head'><div class='corner'></div>"
             "<div class='ticks' style='width:calc(%d * var(--px))'>" % span)
    for at in range(0, span, 30):
        when = window + datetime.timedelta(minutes=at)
        out_func("<b class='day' style='--a:%d'>%s</b>" % (at, when.strftime('%a %-d').upper())
                 if when.hour == 0 and when.minute == 0 else
                 "<b style='--a:%d'>%s</b>" % (at, _clock_label(when)))
    out_func("</div></div>")

    for channel in sorted(channels):
        badge, tooltip, _ = labels.get(channel, (None, None, None))
        out_func("<div class='row' data-c='%d'><div class='badge'%s>%s</div>"
                 "<div class='lane' style='width:calc(%d * var(--px))'>" % (
                     channel, " title='%s'" % esc(tooltip, True) if badge else '',
                     esc(badge) if badge else channel, span))
        for start, length, show_id, description_id, show_group, slot_flags in sorted(
                channels[channel], key=lambda cell: cell[:3]):
            named = titles.get(show_id)
            letters = ('P' if slot_flags & SLOT_PAY_PER_VIEW else '') + (
                _letters(named[2]) if named else '')
            classes = ' '.join(name for mask, name in ((SLOT_PAY_PER_VIEW, 'ppv'),
                                                       (SLOT_JOINED_IN_PROGRESS, 'joined'))
                               if slot_flags & mask)
            out_func("<i%s style='--a:%d;--b:%d' n=%d%s%s%s%s%s>%s%s</i>" % (
                ' class="%s"' % classes if classes else '', minutes_of(start), length, show_id,
                " t=%d" % named[0] if named else '',
                " d=%d" % description_id if description_id is not None else '',
                " g=%d" % show_group if show_group is not None else '',
                " f=%s" % letters if letters else '',
                # the raw byte, when it sets a bit no attribute stands for
                " x=%d" % slot_flags if slot_flags & SLOT_UNNAMED else '',
                esc(named[1]) if named else '#%d' % show_id,
                "<u> %s</u>" % letters if letters else ''))
        out_func("</div></div>")
    out_func("</div>")

    out_func("<div class='key'>"
             + ("<span><b>%d</b> listing%s fall outside this window and are not drawn - "
                "a corrupt start time carries a whole command with it</span>"
                % (len(adrift), '' if len(adrift) == 1 else 's') if adrift else '')
             + "<span><s></s>program</span>"
             "<span><s class='ppv'></s>pay per view</span>"
             "<span><s class='joined'></s>already in progress when the list starts</span>"
             "<span><b>P</b> pay per view &nbsp; <b>C</b> closed captioned &nbsp; "
             "<b>S</b> stereo &nbsp; <b>B</b> black and white</span>"
             "<span><b>#1234</b> no Show Title for that number yet</span></div>")
    out_func("<div class='info'><span class='lit' id='i-ch'>-</span>"
             "<span class='wide' id='i-title'>Point at a program</span>"
             "<span id='i-show'></span><span id='i-time'></span>"
             "<span id='i-flags'></span></div>")
    out_func("</div>")

    # only the descriptions a slot points at, for the box to show
    wanted = {slot[4] for slot in slots if slot[4] is not None} & set(descriptions)
    out_func("<script>var DESC=%s</script>" % json.dumps(
        {str(k): descriptions[k][0] for k in sorted(wanted)}, separators=(',', ':')))
    if lineups:
        out_func("<script>var LINEUPS=%s;var SHORT=%s</script>" % (
            json.dumps({str(region): entries for region, _, entries in lineups},
                       separators=(',', ':')),
            json.dumps({str(channel): short for channel, (_, _, short) in labels.items()
                        if channel in channels}, separators=(',', ':'))))

    _table(out_func, 'adrift', 'Listings not drawn',
           'The ones the key above counts, as the broadcast sent them.',
           (('Start (GMT)', ''), ('Channel', 'num'), ('Minutes', 'num'), ('Show number', 'num'),
            ('Title', 'wrap'), ('Description number', 'num'), ('Show group', 'num'),
            ('Flag byte', '')),
           [(_minute(start), channel, length, show_id,
             esc(titles[show_id][1]) if show_id in titles else '', _blank(description_id),
             _blank(show_group), '0x%02x' % slot_flags)
            for start, channel, length, show_id, description_id, show_group, slot_flags
            in sorted(adrift, key=lambda slot: slot[:4])])

def _table(out_func, table_id, heading, note, columns, rows):
    """ One command type, on its own, with nothing joined onto it, folded away under its
    heading until asked for

    A column no row fills is left out. Alignment goes in a rule per table, and closing tags
    are left off: at 23,836 rows, either would be most of the file.
    """
    if not rows:
        return
    kept = [i for i in range(len(columns)) if any(row[i] != '' for row in rows)]
    columns = [columns[i] for i in kept]
    rows = [[row[i] for i in kept] for row in rows]
    out_func("<details class='data'><summary><h2>%s</h2><span>%s row%s</span></summary>"
             "<p>%s</p>" % (heading, format(len(rows), ',d'), '' if len(rows) == 1 else 's',
                            note))
    rules = ''.join('#%s td:nth-child(%d){%s}'
                    % (table_id, i + 1, {'num': 'text-align:right', 'pre': 'white-space:pre',
                                         'page': 'white-space:pre-wrap;max-width:48em'}
                       .get(css, 'white-space:normal;max-width:48em'))
                    for i, (_, css) in enumerate(columns) if css)
    out_func("<style>%s</style><table id='%s'><thead><tr>" % (rules, table_id)
             + "".join("<th>%s<b></b>" % name for name, _ in columns)
             + "</thead><tbody>")
    for row in rows:
        out_func("<tr>" + "".join("<td>%s" % value for value in row))
    out_func("</tbody></table></details>")

def _blank(value):
    """ An optional field's cell: empty when the command did not carry it """
    return '' if value is None else value

def _stamp(when):
    return '' if when is None else _second(when)

def _minute_runs(clock, minutes):
    """ Minutes before `clock` as the runs of GMT minutes they make: 22:03-22:23, 23:04 """
    times = sorted({(clock - datetime.timedelta(minutes=ago)).replace(second=0)
                    for ago in minutes})
    runs = []
    for when in times:
        if runs and when - runs[-1][1] == datetime.timedelta(minutes=1):
            runs[-1][1] = when
        else:
            runs.append([when, when])
    return ', '.join(first.strftime('%H:%M') + ('' if first == last else
                                                last.strftime('-%H:%M'))
                     for first, last in runs)

def _title_attributes(flags):
    """ A Show Title's attribute bits, named the way the loader's database columns are """
    return ' '.join(name for mask, name in ((ATTRIBUTE_CLOSED_CAPTIONED, 'CC'),
                                            (ATTRIBUTE_STEREO, 'stereo'),
                                            (ATTRIBUTE_BLACK_AND_WHITE, 'B/W'))
                    if flags & mask)

def _channel_labels(guide):
    """ `{channel id: (badge, tooltip, short)}` for every channel Channel Data has named """
    labels = {}
    for channel, data in guide['channel_data'].items():
        whole = data.call_sign + ('-%s' % data.network if data.network else '')
        labels[channel] = (channel_name(data),
                           'channel id %d: %s%s%s' % (
                               channel, whole or '(no name)',
                               ' on %d' % data.number if data.number else '',
                               ', short %s' % data.label
                               if data.label and data.label != data.call_sign else ''),
                           channel_name(data, short=True) if data.shows_call_sign
                           or not data.number else data.call_sign)
    return labels

def _lineups(guide):
    """ `[(region, option text, [(channel, number)])]`, the named regions first, by name """
    names = guide['region_names']
    return [(region.region,
             '%s (%s, %d channels)' % (names.get(region.region) or 'region %d' % region.region,
                                       'cable' if region.cable else 'antenna',
                                       len(region.channels)),
             [(channel, number) for channel, number, _ in sorted(region.channels,
                                                                 key=lambda e: e[1])])
            for region in sorted(guide['regions'].values(),
                                 key=lambda r: (names.get(r.region) is None,
                                                names.get(r.region) or '', r.region))]

def _news_html(text):
    """ A news page's text as markup: its `X attributes run to `e or the end of a line """
    if text is None:
        return '(undecodable)'
    out, open_tag = [], None
    at = 0
    while at < len(text):
        character = text[at]
        if character == NEWS_ATTRIBUTE and at + 1 < len(text):
            if open_tag:
                out.append('</%s>' % open_tag)
            code = text[at + 1]
            open_tag = None if code == 'e' else ('b' if code in 'ia' else 'em')
            if open_tag:
                out.append('<%s>' % open_tag)
            at += 2
            continue
        if character == '\n' and open_tag:
            out.append('</%s>' % open_tag)
            open_tag = None
        out.append(html.escape(character))
        at += 1
    if open_tag:
        out.append('</%s>' % open_tag)
    return ''.join(out)

def _news_viewer(out_func, guide):
    """ The news service the way a receiver showed it: its menu down the side, the pages
    under the chosen section beside it, and the page itself

    A page filed under an area is for the postal codes filed under it, so the viewer picks
    an area - or a postal code, which picks its area - and shows those pages and the ones for
    everyone.
    """
    news = guide['news']
    if not news['pages']:
        return
    esc = html.escape
    sections = dict(news['sections'])
    for section, _, _ in news['pages']:
        sections.setdefault(section, 'section %d' % section)
    areas = Counter(page.area for page in news['pages'].values() if page.area)
    filed = Counter(code.area for code in guide['postal_codes'].values() if code.area)

    out_func("<div class='set news' id='news'><div class='banner'>%s</div><div class='bar'>"
             "<label>Area <select id='news-area'><option value=''>every area</option>%s"
             "</select></label>%s</div>" % (
                 esc((news['service'] or 'News Service').upper()),
                 ''.join("<option value='%d'>0x%02x - %d page%s%s</option>" % (
                     number, number, count, '' if count == 1 else 's',
                     ', %d postal codes' % filed[number] if filed[number] else '')
                     for number, count in sorted(areas.items())),
                 "<label>Postal code <input id='news-zip' inputmode='numeric' maxlength='6'>"
                 "</label><span id='news-zip-note'></span>" if guide['postal_codes'] else ''))
    out_func("<div class='newsbody'><nav class='sections' aria-label='Sections'></nav>"
             "<nav class='pages' aria-label='Pages'></nav><article aria-live='polite'>"
             "</article></div></div>")
    out_func("<script>var NEWS=%s</script>" % json.dumps({
        'sections': [[section, name] for section, name in sections.items()],
        'pages': [[page.section, page.page, page.area, _minute(page.when),
                   page.title or '(undecodable)', _news_html(page.text)]
                  for _, page in sorted(news['pages'].items())],
        'postal': {code.code: code.area or 0 for code in guide['postal_codes'].values()},
    }, separators=(',', ':')).replace('</', '<\\/'))

def write_starsight_html(output_filename, guide, counts=None):
    """ The schedule and the news service as a receiver showed them, and every command the
    capture carried in a table of its own

    The grid joins a Show List slot to its Show Title, the one join the broadcast supports
    and the one a reader wants. Everything else stays under its own numbering, since relating
    separate records here would hide which of them the capture contained. Clicking a column
    heading sorts a table, earlier choices staying on as further keys.
    """
    out_func, f = get_output_function("starsight.html", output_filename, end="")

    counts = counts or Counter()
    slots, titles, descriptions = listings(guide), guide['titles'], guide['descriptions']
    clock = guide['clock']
    offset = None
    if clock:
        _, zone, saving = clock[-1]
        offset = zone + (1 if saving else 0)

    esc = html.escape
    out_func("<!DOCTYPE html><html><head><meta charset='UTF-8'>"
             "<meta name='description' content='Decoded by https://github.com/eshaz/cc_decoder'>"
             "<title>StarSight Guide</title><style>%s</style></head><body><main>"
             % STARSIGHT_HTML_STYLE)
    out_func("<h1>StarSight program guide</h1>")

    # what the capture is, in a line: when its packets went out, and what came through
    packets = guide['packets']
    transmitted, received, lost, breaks = packet_loss(guide['sequence'])
    lede = []
    if packets:
        first, last = min(p[0] for p in packets), max(p[0] for p in packets)
        lede.append('Packets of %s to %s GMT on stream %s.' % (
            _minute(first), last.strftime('%H:%M') if last.date() == first.date()
            else _minute(last), ', '.join(str(n) for n in sorted({p[1] for p in packets}))))
    if transmitted:
        lede.append('%s of %s received, %s lost (%.2f%%)%s.' % (
            format(received, ',d'), format(transmitted, ',d'), format(lost, ',d'),
            100.0 * lost / transmitted,
            ', and %d break%s in the counter left out of that' % (
                breaks, '' if breaks == 1 else 's') if breaks else ''))
    if offset is not None and slots:
        lede.append("Times on the guide are the station's clock, GMT%+d." % offset)
    if lede:
        out_func("<p class='lede'>%s</p>" % ' '.join(lede))

    labels = _channel_labels(guide)
    _guide_grid(out_func, slots, titles, descriptions, offset, labels, _lineups(guide))
    _news_viewer(out_func, guide)

    out_func("<div class='tools'><h2>Everything the capture carried</h2>"
             "<button type='button' data-open='1'>Open all</button>"
             "<button type='button' data-open='0'>Close all</button></div>")

    theme_names = theme_labels(guide)
    _table(out_func, 'showtitle', 'Show Title',
           'One per program. The theme number groups programs by genre. The attributes are '
           "the closed captioned (CC), stereo and black and white (B/W) bits of the title's "
           'flag byte, and compressed marks one sent Huffman coded.',
           (('Show number', 'num'), ('Theme number', 'num'), ('Theme', ''),
            ('Attributes', ''), ('Compressed', ''), ('Title', 'wrap')),
           [(show_id, theme, esc(theme_names.get(theme, '')), _title_attributes(flags),
             'yes' if flags & COMPRESSED_FLAG else '', esc(title))
            for show_id, (theme, title, flags) in sorted(titles.items())])

    description_flags = guide['description_flags']
    _table(out_func, 'showdesc', 'Show Description',
           'Numbered separately from the show numbers above; a Show List slot points at '
           'one through the optional field its flags select. Year, stars, rating and '
           'advisories come from the extended form of the command and are blank on the '
           'plain one. The attributes are the same bits a Show Title carries, for the program '
           'described. The TV rating is the one the text opens with, and a description '
           "ending '(R).' is a rerun.",
           (('Description number', 'num'), ('Year', 'num'), ('Stars', ''),
            ('Rating', ''), ('Rating code', 'num'), ('TV rating', ''),
            ('Advisories', 'wrap'), ('Rerun', ''), ('Attributes', ''), ('Compressed', ''),
            ('Description', 'wrap')),
           [(description_id,
             rating.year if rating and rating.year else '',
             '*' * rating.stars if rating else '',
             (rating_name(rating) or rating.code) if rating else '',
             rating.code if rating else '',
             tv_rating(text) or '',
             esc(', '.join(description_advisories(rating.advisories))) if rating else '',
             'yes' if text.endswith(' (R).') else '',
             _title_attributes(description_flags.get(description_id, 0)),
             'yes' if description_flags.get(description_id, COMPRESSED_FLAG) & COMPRESSED_FLAG
             else '',
             esc(text))
            for description_id, (text, rating) in sorted(descriptions.items())])

    _table(out_func, 'showlist', 'Show List',
           "Each channel's day as its Show List last sent it: when it starts, how many slots "
           'it holds and its version, byte 3, which the loader compares to skip a day it '
           'already holds. The slots themselves are the guide above.',
           (('Channel id', 'num'), ('Channel', ''), ('Start (GMT)', ''), ('Version', 'num'),
            ('Slots', 'num')),
           [(channel, esc(labels[channel][0]) if channel in labels else '', _minute(start),
             version, len(guide['blocks'].get((channel, start.date()), ())) or '')
            for (channel, start), version in sorted(guide['list_versions'].items())])

    _table(out_func, 'themecategory', 'Theme Category',
           "The genres the receiver's search menus offered, each with the sub-categories under "
           'it, how many theme numbers each lists and the flag byte each entry carries%s.' % (
               ', under version %d' % guide['theme_version']
               if guide['theme_version'] is not None else ''),
           (('Category number', 'num'), ('Name', ''), ('Flag byte', 'num'),
            ('Sub-categories', 'wrap')),
           [(category, esc(guide['theme_categories'].get(category, '')),
             _blank(guide['theme_category_flags'].get(category)), esc(', '.join(
                 '%s (%d, flag %d)' % (sub, len(themes), flags)
                 for sub, themes, flags in guide['theme_lists'].get(category, ()))))
            for category in sorted(set(guide['theme_categories']) | set(guide['theme_lists']))])

    _table(out_func, 'theme', 'Theme',
           "Every theme number the Theme Sub-Category commands list, by the menus that list "
           "it: each category, and the sub-categories of it that say more than 'All' and "
           "'Closed Captioned'.",
           (('Theme number', 'num'), ('Categories', 'wrap'), ('Titles', 'num')),
           [(theme, esc(name), sum(1 for t, _, _ in titles.values() if t == theme) or '')
            for theme, name in sorted(theme_names.items())])

    regions, region_names = guide['regions'], guide['region_names']
    carried = Counter(channel for region in regions.values() for channel, _, _ in region.channels)
    _table(out_func, 'channeldata', 'Channel Data',
           'What names a channel id: the channel a station broadcasts on with its call letters '
           "and network, or a cable service's name, and the characters picked for a display "
           'too narrow for the rest. Lineups counts the regions that carry it.',
           (('Channel id', 'num'), ('Channel number', 'num'), ('Call sign or name', ''),
            ('Network', ''), ('Label', ''), ('Shows call sign', ''), ('Lineups', 'num')),
           [(data.channel, data.number or '', esc(data.call_sign), esc(data.network),
             esc(data.label), 'yes' if data.shows_call_sign else '', carried[channel] or '')
            for channel, data in sorted(guide['channel_data'].items())])

    # a no-break space keeps an entry's number with its name when a cell wraps
    def lineup(region, tier):
        return esc(', '.join('%d\u00a0%s' % (number, labels[channel][2] if channel in labels
                                             else '#%d' % channel)
                             for channel, number, flags in sorted(region.channels,
                                                                  key=lambda e: e[1])
                             if flags & REGION_TIER_MASK == tier))

    _table(out_func, 'region', 'Region',
           'A lineup: the channels a receiver on one cable system, or with an antenna in one '
           'market, can see and the numbers they sit on there, split by the tier the fourth '
           "byte of each entry gives. Odd bits lists the entries setting bits 6 or 7 of it. "
           "The name is Region Name's; a lineup applies from the time given, or always.",
           (('Region', 'num'), ('Name', 'wrap'), ('Kind', ''), ('Effective (GMT)', ''),
            ('Apply now', ''), ('Channels', 'num'))
           + tuple((name.capitalize(), 'wrap') for name in REGION_TIERS)
           + (('Odd bits', 'wrap'),),
           [(number, esc(region_names.get(number) or ''),
             ('cable' if region.cable else 'antenna') if region else '',
             _minute(region.effective) if region and region.effective != STARSIGHT_EPOCH
             else '',
             'yes' if region and region.immediate else '',
             len(region.channels) if region else '')
            + tuple(lineup(region, tier) if region else '' for tier in range(len(REGION_TIERS)))
            + (esc(', '.join('%d 0x%02x' % (n, flags) for _, n, flags in region.channels
                             if flags & ~REGION_TIER_MASK)) if region else '',)
            for number, region in sorted((number, regions.get(number))
                                         for number in set(regions) | set(region_names))])

    def region_list(numbers):
        return esc(', '.join(('%d\u00a0%s' % (number, region_names[number])
                              if region_names.get(number) else str(number))
                             for number in numbers))

    _table(out_func, 'postal', 'Postal Code',
           'The regions a receiver set up with a postal code is offered, for an antenna and '
           'for cable, and the area its News Pages are filed under. The other bytes hold one '
           'value on every code sent.',
           (('Postal code', ''), ('Broadcast regions', 'wrap'), ('Cable regions', 'wrap'),
            ('News area', ''), ('Byte 9 and the last byte', '')),
           [(esc(code.code), region_list(code.broadcast), region_list(code.cable),
             '' if code.area is None else '0x%02x' % code.area, code.unread.hex(' '))
            for _, code in sorted(guide['postal_codes'].items())])

    news = guide['news']
    _table(out_func, 'newsmenu', 'News Menu',
           'The sections of %s, in the order the menu lists them, with the five bytes of each '
           'entry nothing reads%s%s.' % (
               esc(news['service'] or 'the news service'),
               "; the menu's byte 3 is %d" % news['menu_byte']
               if news['menu_byte'] is not None else '',
               " and the service's bytes 3-6 are %s" % news['service_bytes'].hex(' ')
               if news['service_bytes'] is not None else ''),
           (('Section', 'num'), ('Name', ''), ('Pages', 'num'), ('Bytes 1-5', '')),
           [(section, esc(name or ''),
             sum(1 for page in news['pages'].values() if page.section == section) or '',
             news['section_bytes'][section].hex(' ') if section in news['section_bytes']
             else '')
            for section, name in news['sections'].items()])
    _table(out_func, 'newspage', 'News Page',
           "Each page as it last went out, as the viewer above shows it. Its text keeps the "
           "service's own line breaks, and the parts it set apart are bold. The flag byte is "
           "byte 7; the unread bytes are 4, 8, 9 and 15.",
           (('Section', ''), ('Page', 'num'), ('Area', ''), ('Written (GMT)', ''),
            ('Title', ''), ('Flag byte', ''), ('Unread bytes', ''), ('Text', 'page')),
           [(esc(news['sections'].get(page.section) or str(page.section)), page.page,
             '0x%02x' % page.area if page.area else '', _minute(page.when),
             esc(page.title or '(undecodable)'),
             '' if page.flags is None else '0x%02x' % page.flags,
             page.unread.hex(' ') if page.unread else '', _news_html(page.text))
            for _, page in sorted(news['pages'].items())])

    _table(out_func, 'encrypted', 'Encrypted',
           'Commands sent encrypted, which no receiver without the key could read, whole as '
           "hex. A Show List keeps its header in the clear, so its channel, the day it starts "
           'and how many slots it holds are known even though the slots are not.',
           (('Command', ''), ('Key', 'num'), ('Channel', ''), ('Start (GMT)', ''),
            ('Slots', 'num'), ('Length', 'num'), ('Bytes', 'wrap')),
           [(COMMAND_NAMES.get(item.type, 'type %d' % item.type), item.key,
             '' if item.channel is None else esc(labels[item.channel][0])
             if item.channel in labels else item.channel,
             _minute(item.start) if item.start else '',
             _blank(item.slots), len(item.data), item.data.hex(' '))
            for item in guide['encrypted']])

    _table(out_func, 'time', 'Time',
           'The station clock, sent about twice a minute. Not a provider is bit 6 of byte 6, '
           'which SSSCAN.EXE reads as a channel that is not a StarSight provider.',
           (('Time (GMT)', ''), ('Standard offset', 'num'), ('Daylight saving', ''),
            ('Not a provider', '')),
           [(_second(when), '%+d' % zone, 'yes' if saving else 'no',
             'yes' if when in guide['not_provider'] else '')
            for when, zone, saving in clock])

    _table(out_func, 'daylight', 'Daylight Saving Change',
           'The two instants the station changes its clock, sent alongside the time.',
           (('Daylight saving starts (GMT)', ''), ('Daylight saving ends (GMT)', '')),
           [(_minute(starts), _minute(ends)) for starts, ends in guide['daylight']])

    _table(out_func, 'station', 'Station Node Status',
           "The station's own health, every one to ten minutes. Its times are Unix seconds, "
           'which nothing else in the format uses, the stream is the one the packet headers '
           'carry, and the three counts are read only where they add up. The daily time falls '
           'at the same local time every day; the two minute maps cover the last day, and '
           'list the minutes whose bit is set, GMT. What either means is not known.',
           (('Block', 'num'), ('Version', 'num'), ('Bytes', 'num'), ('Stream', 'num'),
            ('Bytes 4-5', 'num'), ('Built (GMT)', ''), ('Assembled (GMT)', ''),
            ('Clock (GMT)', ''), ('Done', 'num'), ('Remaining', 'num'), ('Total', 'num'),
            ('Daily (GMT)', ''), ('Minutes used', 'num'), ('First minute map', 'wrap'),
            ('Second minute map', 'wrap')),
           [(number, node.version, len(raw), node.stream, node.software, _stamp(node.built),
             _stamp(node.assembled), _stamp(node.clock), _blank(node.done),
             _blank(node.remaining), _blank(node.total), _stamp(node.daily),
             _blank(node.minutes))
            + (tuple(_minute_runs(node.clock, minutes) for minutes in node.minute_maps)
               if node.minute_maps and node.clock else ('', ''))
            if node else (number, '', len(raw)) + ('',) * 12
            for number, (node, raw) in enumerate(guide['station'], 1)])

    _table(out_func, 'stationinventory', 'Station Node inventory',
           "What the station's own guide database holds and lacks, table by table, in each "
           'block: its regions, channels, Show Titles and Show Descriptions, then the '
           "schedules of ten days from the clock's own GMT day, each counting channels. A "
           'block names the lowest eight ids missing from each.',
           (('Block', 'num'), ('Clock (GMT)', ''), ('Table', ''), ('Held', 'num'),
            ('Missing', 'num'), ('Lowest missing ids', 'wrap')),
           [(number, _stamp(node.clock), name, table.held, table.missing,
             ' '.join(map(str, table.first_missing)))
            for number, (node, _) in enumerate(guide['station'], 1)
            if node and node.inventory
            for name, table in zip(STATION_TABLES + tuple(
                'schedules, day %d' % day for day in range(STATION_DAYS)),
                node.inventory[:len(STATION_TABLES)] + node.inventory.days)])

    _table(out_func, 'stationbytes', 'Station Node Status bytes',
           'Each block above as it was sent, sixteen bytes a row, read as ASCII and as '
           'CEA-608 characters.',
           (('Block', 'num'), ('Offset', ''), ('Bytes', 'pre'), ('ASCII', 'pre'),
            ('CEA-608', 'pre')),
           [(number, '0x%04x' % at, raw[at:at + 16].hex(' '),
             esc(''.join(chr(b) if 0x20 <= b < 0x7F else '.' for b in raw[at:at + 16])),
             esc(''.join(CC_TABLE.get(b & 0x7F) or '.' for b in raw[at:at + 16])))
            for number, (_, raw) in enumerate(guide['station'], 1)
            for at in range(0, len(raw), 16)])

    _table(out_func, 'reset', 'Subscriber Reset',
           'What each reset asked a receiver to discard, and the command whole.',
           (('Reset', 'num'), ('Action', ''), ('Bytes', 'wrap')),
           [(number, ', '.join(actions) or 'nothing', data.hex(' '))
            for number, (actions, data) in enumerate(guide['resets'], 1)])

    for command_type, decoded in sorted(guide['loader_commands'].items()):
        kind = OTHER_COMMANDS[command_type]
        _table(out_func, 'type%d' % command_type,
               COMMAND_NAMES.get(command_type, 'Type %d' % command_type), kind.note,
               kind.columns, [kind.row(value) for value in decoded])

    _table(out_func, 'gaps', 'Lost packets',
           'Where in the broadcast the packets the sequence numbers say were sent and never '
           'arrived fall.',
           (('Last packet before the gap', 'num'), ('Packets missing', 'num')),
           sequence_gaps(guide['sequence']))

    _table(out_func, 'packets', 'Packets',
           'Every packet that passed both checksums, in the order received: the time and VBI '
           'stream id in its header, and the number its Sequence Number command carried.',
           (('Time (GMT)', ''), ('Stream id', 'num'), ('Sequence number', 'num')),
           [(_minute(when), stream_id, _blank(number)) for when, stream_id, number in packets])

    kept_whole = sorted(command_type for command_type in counts
                        if command_type not in _DESCRIBE)
    _table(out_func, 'commands', 'Commands',
           'Every command seen in the capture. All of these are decoded%s.' % (
               ' except type%s %s, which %s kept whole under Not decoded' % (
                   's' if len(kept_whole) > 1 else '', ', '.join(map(str, kept_whole)),
                   'are' if len(kept_whole) > 1 else 'is') if kept_whole else ''),
           (('Type', 'num'), ('Command', ''), ('Count', 'num')),
           [(command_type, COMMAND_NAMES.get(command_type, 'unknown'), count)
            for command_type, count in sorted(counts.items())])

    _table(out_func, 'undecoded', 'Not decoded',
           'Everything that arrived and could not be decoded, whole as hex, and why.',
           (('Type', 'num'), ('Command', ''), ('Why', 'wrap'), ('Length', 'num'),
            ('Bytes', 'wrap')),
           [(command_type, COMMAND_NAMES.get(command_type, 'unknown'), esc(why), len(data),
             data.hex(' '))
            for command_type, why, data in guide['undecoded']])

    out_func("</main><script>%s</script></body></html>" % STARSIGHT_HTML_SCRIPT)
    if f is not None:
        f.close()


def _iso(when):
    """ The wire carries GMT, so every instant in the export is written as GMT """
    return when.isoformat('T', 'seconds') + 'Z'

def _from_iso(stamp):
    """ The GMT instant `_iso` wrote """
    return datetime.datetime.fromisoformat(stamp.rstrip('Z'))

def _channel_entry(channel, data):
    """ One channel, with whatever a Channel Data command said about it. The number is the
    one a station broadcasts on; where a channel sits on a cable system is in its lineup. """
    entry = {'id': channel}
    if data is not None:
        for name, value in (('callSign', data.call_sign), ('network', data.network),
                            ('label', data.label), ('channelNumber', data.number),
                            ('showsCallSign', data.shows_call_sign)):
            if value:
                entry[name] = value
    return entry

def _attribute_entry(entry, flags):
    """ Write the closed captioned, stereo and black and white bits a flag byte sets, and
    that the text was sent in the clear: each only when it is so """
    for name, value in (('closedCaptioned', flags & ATTRIBUTE_CLOSED_CAPTIONED),
                        ('stereo', flags & ATTRIBUTE_STEREO),
                        ('blackAndWhite', flags & ATTRIBUTE_BLACK_AND_WHITE),
                        ('uncompressed', not flags & COMPRESSED_FLAG)):
        if value:
            entry[name] = True
    return entry

def _attribute_flags(entry):
    """ The flag byte `_attribute_entry` wrote """
    return ((ATTRIBUTE_CLOSED_CAPTIONED if entry.get('closedCaptioned') else 0)
            | (ATTRIBUTE_STEREO if entry.get('stereo') else 0)
            | (ATTRIBUTE_BLACK_AND_WHITE if entry.get('blackAndWhite') else 0)
            | (0 if entry.get('uncompressed') else COMPRESSED_FLAG))

def _description_entry(description_id, text, rating, flags=None):
    """ One Show Description, with whatever its extended form carried

    `rating` is the code as broadcast and `ratingName` what Microsoft's guide showed for it.
    A description ending ' (R).' is a rerun, which is how SSLOAD.DLL sets `TS Rerun`.
    """
    entry = {'id': description_id, 'text': text}
    if text is not None and text.endswith(' (R).'):
        entry['rerun'] = True
    if tv_rating(text):
        entry['tvRating'] = tv_rating(text)
    if rating is not None:
        entry['stars'] = rating.stars
        entry['rating'] = rating.code
        if rating_name(rating):
            entry['ratingName'] = rating_name(rating)
        if rating.year:
            entry['year'] = rating.year
        advisories = description_advisories(rating.advisories)
        if advisories:
            entry['advisories'] = advisories
    if flags is not None:
        _attribute_entry(entry, flags)
    return entry

def _station_table(table):
    return _optional({'held': table.held, 'missing': table.missing},
                     firstMissing=list(table.first_missing) or None)

def _station_entry(node, raw):
    """ One Station Node Status block: whole, as hex, and the fields read from it """
    entry = {'data': raw.hex()}
    if node is not None:
        entry.update({'version': node.version, 'stream': node.stream,
                      'software': node.software})
        for name, value in (('built', node.built), ('assembled', node.assembled),
                            ('clock', node.clock), ('daily', node.daily)):
            if value is not None:
                entry[name] = _iso(value)
        if node.total is not None:
            entry.update({'total': node.total, 'done': node.done,
                          'remaining': node.remaining})
        if node.minutes is not None:
            entry.update({'minutesUsed': node.minutes,
                          'minuteMaps': [list(minutes) for minutes in node.minute_maps]})
        if node.inventory is not None:
            entry['inventory'] = dict(
                {name: _station_table(getattr(node.inventory, name)) for name in STATION_TABLES},
                days=[_station_table(day) for day in node.inventory.days])
    return entry

def _listing_entry(slot):
    start, channel, minutes, show_id, description_id, show_group, slot_flags = slot
    entry = {'channel': channel, 'start': _iso(start), 'durationMinutes': minutes,
             'program': show_id}
    if description_id is not None:
        entry['description'] = description_id
    if show_group is not None:
        entry['showGroup'] = show_group
    if slot_flags & SLOT_PAY_PER_VIEW:
        entry['payPerView'] = True
    if slot_flags & SLOT_JOINED_IN_PROGRESS:
        entry['joinedInProgress'] = True
    entry['slotFlags'] = slot_flags
    return entry

def _optional(entry, **fields):
    """ `entry` with each of `fields` that is not None """
    entry.update((name, value) for name, value in fields.items() if value is not None)
    return entry

def write_starsight_json(output_filename, guide):
    """ The guide as normalized JSON, for a page to query

    What the broadcast carried, all of it: entity tables and a flat table of listings that
    names its channel and program by the ids the broadcast used, and beside them the lineups,
    the news service, the packets, the commands addressed to single receivers and whatever
    arrived and could not be decoded, as sent. Nothing about the decode itself is written,
    and nothing derived: a page parses this in about seven milliseconds and builds its own
    indexes in five. The on-wire ids are kept: renumbering them by use made the gzipped file
    larger, since gzip already models the repetition.
    """
    slots, titles = listings(guide), guide['titles']
    channel_data, regions = guide['channel_data'], guide['regions']
    listed = {theme for entries in guide['theme_lists'].values()
              for _, themes, _ in entries for theme in themes}

    programs = []
    for show_id in sorted({slot[3] for slot in slots} | set(titles)):
        entry = {'id': show_id}
        if show_id in titles:
            theme, title, flags = titles[show_id]
            entry.update(title=title, theme=theme)
            _attribute_entry(entry, flags)
        programs.append(entry)

    document = {
        'channels': [_channel_entry(channel, channel_data.get(channel))
                     for channel in sorted({slot[1] for slot in slots} | set(channel_data)
                                           | {c for r in regions.values()
                                              for c, _, _ in r.channels})],
        'themes': [{'id': theme}
                   for theme in sorted({t[0] for t in titles.values()} | listed)],
        'programs': programs,
        'descriptions': [_description_entry(description_id, text, rating,
                                            guide['description_flags'].get(description_id))
                         for description_id, (text, rating)
                         in sorted(guide['descriptions'].items())],
        'listings': [_listing_entry(slot) for slot in slots],
        'showLists': [{'channel': channel, 'start': _iso(start), 'version': version}
                      for (channel, start), version in sorted(guide['list_versions'].items())],
        'clock': [_optional({'time': _iso(when), 'utcOffsetMinutes': zone * 60,
                             'daylightSaving': saving},
                            notAProvider=True if when in guide['not_provider'] else None)
                  for when, zone, saving in guide['clock']],
        'daylightSavingChanges': [{'starts': _iso(starts), 'ends': _iso(ends)}
                                  for starts, ends in guide['daylight']],
        'sequenceNumbers': guide['sequence'],
        'packets': [_optional({'time': _iso(when), 'stream': stream_id}, sequence=number)
                    for when, stream_id, number in guide['packets']],
    }

    # the receiver's search menus, as sent: a theme is in every one that fits it
    categories = sorted(set(guide['theme_categories']) | set(guide['theme_lists']))
    if categories:
        if guide['theme_version'] is not None:
            document['themeCategoryVersion'] = guide['theme_version']
        document['themeCategories'] = [
            dict(_optional({'id': category}, name=guide['theme_categories'].get(category),
                           flags=guide['theme_category_flags'].get(category)),
                 subCategories=[{'name': name, 'themes': themes, 'flags': flags}
                                for name, themes, flags in
                                guide['theme_lists'].get(category, ())])
            for category in categories]

    names = guide['region_names']
    if regions or names:
        document['regions'] = []
        for number in sorted(set(regions) | set(names)):
            entry = _optional({'id': number}, name=names.get(number))
            region = regions.get(number)
            if region is not None:
                entry['cable'] = region.cable
                if region.effective != STARSIGHT_EPOCH:
                    entry['effective'] = _iso(region.effective)
                if region.immediate:
                    entry['applyNow'] = True
                entry['lineup'] = [{'channel': channel, 'number': channel_number, 'tier': tier}
                                   for channel, channel_number, tier in region.channels]
            document['regions'].append(entry)

    if guide['postal_codes']:
        document['postalCodes'] = [
            _optional({'code': code.code, 'broadcastRegions': code.broadcast,
                       'cableRegions': code.cable},
                      newsArea=code.area, unread=code.unread.hex() or None)
            for _, code in sorted(guide['postal_codes'].items())]

    news = guide['news']
    if news['service'] or news['sections'] or news['pages']:
        document['news'] = _optional(
            {}, service=news['service'],
            serviceBytes=None if news['service_bytes'] is None else news['service_bytes'].hex(),
            menuByte=news['menu_byte'])
        document['news']['sections'] = [
            _optional({'id': section, 'name': name},
                      bytes=news['section_bytes'][section].hex()
                      if section in news['section_bytes'] else None)
            for section, name in news['sections'].items()]
        document['news']['pages'] = [
            _optional({'section': page.section, 'page': page.page, 'area': page.area,
                       'written': _iso(page.when), 'title': page.title, 'text': page.text},
                      flags=page.flags, unread=page.unread.hex() if page.unread else None)
            for _, page in sorted(news['pages'].items())]

    if guide['encrypted']:
        document['encrypted'] = [
            dict({'type': item.type, 'key': item.key, 'data': item.data.hex()},
                 **({'channel': item.channel, 'start': _iso(item.start), 'slots': item.slots}
                    if item.channel is not None else {}))
            for item in guide['encrypted']]

    if guide['resets']:
        document['subscriberResets'] = [{'actions': list(actions), 'data': data.hex()}
                                        for actions, data in guide['resets']]

    for command_type, kind in sorted(OTHER_COMMANDS.items()):
        if guide['loader_commands'].get(command_type):
            document[kind.key] = [kind.write(value)
                                  for value in guide['loader_commands'][command_type]]

    if guide['station']:
        document['stationNodeStatus'] = [_station_entry(node, raw)
                                         for node, raw in guide['station']]

    if guide['undecoded']:
        document['undecoded'] = [{'type': command_type, 'why': why, 'data': data.hex()}
                                 for command_type, why, data in guide['undecoded']]

    out_func, f = get_output_function("starsight.json", output_filename, end="")
    out_func(json.dumps(document, separators=(',', ':')))
    if f is not None:
        f.close()

def read_starsight_json(document):
    """ The guide a `write_starsight_json` document came from

    The inverse of the export, so a decoded capture can be read back and merged with another.
    The attribute bits are rebuilt into a flag byte, which is what the rest of the code reads
    them from. A document written before a field was exported simply lacks it.
    """
    guide = new_guide()

    for program in document.get('programs', ()):
        if 'title' in program:
            guide['titles'][program['id']] = (program['theme'], program['title'],
                                              _attribute_flags(program))

    for entry in document.get('channels', ()):
        if 'callSign' in entry or 'channelNumber' in entry:
            guide['channel_data'][entry['id']] = ChannelData(
                entry['id'], entry.get('channelNumber', 0), entry.get('callSign', ''),
                entry.get('network', ''), entry.get('label', ''),
                entry.get('showsCallSign', False))

    guide['theme_version'] = document.get('themeCategoryVersion')
    for entry in document.get('themeCategories', ()):
        if 'name' in entry:
            guide['theme_categories'][entry['id']] = entry['name']
        if 'flags' in entry:
            guide['theme_category_flags'][entry['id']] = entry['flags']
        if entry.get('subCategories'):
            guide['theme_lists'][entry['id']] = [(sub['name'], sub['themes'],
                                                  sub.get('flags', 0))
                                                 for sub in entry['subCategories']]

    for entry in document.get('regions', ()):
        if 'name' in entry:
            guide['region_names'][entry['id']] = entry['name']
        if 'lineup' in entry:
            guide['regions'][entry['id']] = RegionData(
                entry['id'], entry.get('applyNow', False),
                _from_iso(entry['effective']) if 'effective' in entry else STARSIGHT_EPOCH,
                [(item['channel'], item['number'], item['tier']) for item in entry['lineup']],
                entry.get('cable', False))

    for entry in document.get('postalCodes', ()):
        guide['postal_codes'][entry['code']] = PostalCode(
            entry['code'], entry['broadcastRegions'], entry['cableRegions'],
            entry.get('newsArea'), bytes.fromhex(entry.get('unread', '')))

    news, entry = guide['news'], document.get('news')
    if entry:
        news['service'] = entry.get('service')
        if 'serviceBytes' in entry:
            news['service_bytes'] = bytes.fromhex(entry['serviceBytes'])
        news['menu_byte'] = entry.get('menuByte')
        for section in entry.get('sections', ()):
            news['sections'][section['id']] = section['name']
            if 'bytes' in section:
                news['section_bytes'][section['id']] = bytes.fromhex(section['bytes'])
        for page in entry.get('pages', ()):
            news['pages'][(page['section'], page['page'], page['area'])] = NewsPage(
                page['section'], page['page'], page.get('flags'), page['area'],
                _from_iso(page['written']), page['title'], page['text'],
                bytes.fromhex(page['unread']) if 'unread' in page else None)

    for entry in document.get('descriptions', ()):
        rating = None
        if 'rating' in entry:
            named = set(entry.get('advisories', ()))
            advisories = sum(mask for mask, name in ADVISORY_NAMES + ADVISORY_UNKNOWN
                             if name in named)
            # documents written before the stars were identified call them ratingSystem
            rating = ShowRating(entry.get('stars', entry.get('ratingSystem')), entry['rating'],
                                advisories, entry.get('year'))
        guide['descriptions'][entry['id']] = (entry['text'], rating)
        guide['description_flags'][entry['id']] = (
            _attribute_flags(entry) | (DESCRIPTION_EXTENDED if rating else 0))

    # a block a day per channel, which is how `merge_guides` keys them too
    for listing in document.get('listings', ()):
        start = _from_iso(listing['start'])
        guide['blocks'].setdefault((listing['channel'], start.date()), []).append(
            (start, listing['channel'], listing['durationMinutes'], listing['program'],
             listing.get('description'), listing.get('showGroup'), listing['slotFlags']))

    for entry in document.get('showLists', ()):
        guide['list_versions'][(entry['channel'], _from_iso(entry['start']))] = entry['version']

    for entry in document.get('clock', ()):
        when = _from_iso(entry['time'])
        guide['clock'].append((when, entry['utcOffsetMinutes'] // 60, entry['daylightSaving']))
        if entry.get('notAProvider'):
            guide['not_provider'].add(when)

    guide['daylight'] = [(_from_iso(entry['starts']), _from_iso(entry['ends']))
                         for entry in document.get('daylightSavingChanges', ())]
    guide['packets'] = [(_from_iso(entry['time']), entry['stream'], entry.get('sequence'))
                        for entry in document.get('packets', ())]
    guide['sequence'] = list(document.get('sequenceNumbers', ()))

    guide['encrypted'] = [EncryptedCommand(
        entry['type'], entry['key'], entry.get('channel'),
        _from_iso(entry['start']) if 'start' in entry else None, entry.get('slots'),
        bytes.fromhex(entry['data'])) for entry in document.get('encrypted', ())]
    guide['resets'] = [(tuple(entry['actions']), bytes.fromhex(entry['data']))
                       for entry in document.get('subscriberResets', ())]

    for command_type, kind in OTHER_COMMANDS.items():
        for entry in document.get(kind.key, ()):
            guide['loader_commands'][command_type].append(kind.read(entry))

    # a block is kept whole, as hex, in both forms the export has had
    guide['station'] = [(decode_station_node(raw), raw)
                        for raw in (bytes.fromhex(entry if isinstance(entry, str)
                                                  else entry['data'])
                                    for entry in document.get('stationNodeStatus', ()))]
    guide['undecoded'] = [(entry['type'], entry['why'], bytes.fromhex(entry['data']))
                          for entry in document.get('undecoded', ())]
    return guide

# A gap wider than this is not packets missing but the counter itself being wrong: a single
# flipped bit in a 32 bit number moves it by up to 2^31, and one does exactly that in the
# 1994 capture, stepping 5,720 -> 1,054,298. Real gaps in these captures are 1 to 4.
SEQUENCE_MAX_GAP = 256

def packet_loss(sequence):
    """ `(transmitted, received, lost, breaks)` from the sequence numbers a capture caught

    Counted from consecutive steps rather than the span, which is only as trustworthy as the
    largest value in it. A small step is that many packets missing, and a larger one a
    `break` - a corrupt counter, or a capture that stopped and started - left out of the loss.
    """
    ordered = sorted(set(sequence))
    if not ordered:
        return 0, 0, 0, 0
    lost = breaks = 0
    for earlier, later in zip(ordered, ordered[1:]):
        step = later - earlier
        if step <= SEQUENCE_MAX_GAP:
            lost += step - 1
        else:
            breaks += 1
    return len(ordered) + lost, len(ordered), lost, breaks

def sequence_gaps(sequence):
    """ `[(last received before the gap, how many are missing)]` """
    ordered = sorted(set(sequence))
    return [(earlier, later - earlier - 1)
            for earlier, later in zip(ordered, ordered[1:])
            if 1 < later - earlier <= SEQUENCE_MAX_GAP]

def captured_at(guide):
    """ When a guide was received, from its own Time commands, or None """
    return max((when for when, _, _ in guide['clock']), default=None)

def expire_slots(slots, when):
    """ The loader's 'Delete Expired Time Slot': drop what had already finished by `when`

    Against a tape's own clock this removes nothing, since the guide runs days ahead of the
    capture. It is kept out of a merge by default: run against the newest of several tapes
    it would delete every older tape's schedule, right for a receiver and wrong for an
    archive.
    """
    return [slot for slot in slots if slot[0] + datetime.timedelta(minutes=slot[2]) > when]

def merge_guides(guides, expire=False):
    """ `(guide, report)` from several guides of the same service

    Tapes of one channel overlap - the same program rides the carousel again and again, and
    two tapes a week apart share the days between them - so everything is keyed rather than
    concatenated, the way SSLOAD.DLL keys it.

    **A channel's day is the unit.** The loader replaces what it receives rather than adding
    to it, so a later tape carrying a revised day supersedes the earlier one. Guides are put
    in the order they were received, by their own Time commands, and the last to carry a
    channel-day wins it.

    **A repeated key updates**, as the loader's `UpdateRS` does, so the newest value of a show
    number or a description wins. `report` counts how often that changed one, because
    StarSight reuses show numbers: across the two 1998 captures a week apart one of 2,209
    shared numbers names a different program, across captures six and seven months apart 116
    of 801 and 189 of 1,323 do, and for those no choice of winner is right.

    `expire` applies the loader's other delete, against the newest capture instant: see
    `expire_slots`.
    """
    merged = new_guide()
    report = Counter()
    blocks = {}

    # oldest first, so the newest tape is the one that lands last and wins
    ordered = sorted(guides, key=lambda guide: (captured_at(guide) is not None,
                                                captured_at(guide) or datetime.datetime.min))
    for guide in ordered:
        days = defaultdict(list)
        for slot in listings(guide):
            days[(slot[1], slot[0].date())].append(slot)
        report['channel days superseded'] += len(days.keys() & blocks.keys())
        blocks.update(days)

        for table in ('titles', 'descriptions'):
            for key, value in guide[table].items():
                if key in merged[table]:
                    report[table + ' shared'] += 1
                    if merged[table][key] != value:
                        report[table + ' reused'] += 1
                merged[table][key] = value
        for table in ('description_flags', 'list_versions', 'channel_data', 'theme_categories',
                      'theme_category_flags', 'theme_lists', 'regions', 'region_names',
                      'postal_codes'):
            merged[table].update(guide[table])
        if guide['theme_version'] is not None:
            merged['theme_version'] = guide['theme_version']
        merged['not_provider'] |= guide['not_provider']
        for table in ('clock', 'daylight', 'sequence', 'packets'):
            merged[table] += guide[table]
        for table in ('encrypted', 'resets', 'station', 'undecoded'):
            merged[table] += [item for item in guide[table] if item not in merged[table]]
        for command_type, values in guide['loader_commands'].items():
            kept = merged['loader_commands'][command_type]
            kept += [value for value in values if value not in kept]
        for key in ('service', 'service_bytes', 'menu_byte'):
            if guide['news'][key] is not None:
                merged['news'][key] = guide['news'][key]
        for key in ('sections', 'section_bytes', 'pages'):
            merged['news'][key].update(guide['news'][key])

    slots = list(dict.fromkeys(slot for block in blocks.values() for slot in block))
    if expire:
        when = captured_at(merged) or max((slot[0] for slot in slots), default=None)
        if when is not None:
            kept = expire_slots(slots, when)
            report['slots expired'] = len(slots) - len(kept)
            slots = kept
    for slot in slots:
        merged['blocks'].setdefault((slot[1], slot[0].date()), []).append(slot)

    merged['clock'] = sorted(set(merged['clock']))
    merged['daylight'] = list(dict.fromkeys(merged['daylight']))
    # tapes months apart carry unrelated stretches of the counter; keeping them sorted and
    # unique is right, but a loss figure across them would be meaningless
    merged['sequence'] = sorted(set(merged['sequence']))
    merged['packets'] = sorted(dict.fromkeys(merged['packets']), key=lambda packet: packet[0])

    at = Counter((slot[1], slot[0]) for slot in slots)
    report['slots overlapping'] = sum(n - 1 for n in at.values() if n > 1)
    return merged, report


# Packets whose header clock jumps back more than an hour or ahead more than a day, or that
# change stream id, belong to another recording: a tape recorded over keeps what was under it,
# and guides a season apart reuse show numbers, so the two are not joined. Two packets in a
# row have to agree before a recording starts, so one packet a repair got wrong cannot.
RECORDING_BACK = datetime.timedelta(hours=1)
RECORDING_AHEAD = datetime.timedelta(days=1)

# How many recent packets are remembered to drop the copy a second line delivers.
STARSIGHT_RECENT_PACKETS = 256

class _LineStream:
    """ One VBI line's bytes, both fields of it, waiting to be framed into packets """

    def __init__(self):
        self.rows, self.order = set(), []
        self.buffer = bytearray()
        self.confidence = []
        self.lost = []
        self.integrity = Counter()

    def frame(self, decoded):
        """ Append this frame's bytes, and return the packets they complete """
        if not decoded.keys() <= self.rows:
            self.rows |= decoded.keys()
            self.order = sorted(self.rows)
        # A row that did not slice leaves a hole of two unknown bytes, which the body
        # checksum can solve for, rather than shifting everything after it.
        for row_num in self.order:
            if row_num in decoded:
                byte1, byte2, certainty = decoded[row_num]
                self.buffer += bytes((byte1, byte2))
                self.confidence += certainty
            else:
                self.lost += [len(self.buffer), len(self.buffer) + 1]
                self.buffer += bytes(2)
                self.confidence += [0.0] * 16

        packets, consumed = take_starsight_packets(self.buffer, self.integrity, self.lost,
                                                   self.confidence)
        if consumed:
            del self.buffer[:consumed]
            del self.confidence[:consumed * 8]
            self.lost = [position - consumed for position in self.lost if position >= consumed]
        return packets

class _Recording:
    """ One recording's guide, and what the log says about it """

    def __init__(self, when, stream_id):
        self.guide = new_guide()
        self.counts = Counter()
        self.channels = set()
        self.records = 0
        self.named = 0
        self.stream_id = stream_id
        self.first = self.last = when

    def continues(self, when, stream_id):
        return _continues(self.last, self.stream_id, when, stream_id)

def _continues(last, last_stream, when, stream_id):
    """ Whether a packet's header can follow one sent at `last` on `last_stream` """
    return stream_id == last_stream and last - RECORDING_BACK <= when <= last + RECORDING_AHEAD

def _has_guide(guide):
    """ Whether a guide holds anything the page and the export have to show """
    return any(guide[key] for key in ('blocks', 'titles', 'descriptions', 'channel_data',
                                      'regions', 'encrypted')) or any(guide['news'].values())

def _recording_name(output_filename, recordings, recording):
    """ Where a recording's page and export go: the output name when there is one recording,
    and that with the recording's date when a capture holds several """
    if len(recordings) == 1:
        return output_filename
    dated = [r for r in recordings if r.first.date() == recording.first.date()]
    suffix = '' if len(dated) == 1 else '-%d' % (dated.index(recording) + 1)
    return '%s.%s%s' % (output_filename, _date(recording.first), suffix)

def decode_starsight(rx, output_filename, options):
    """ Decode the StarSight program guide carried on its own VBI line

    Rows arrive a frame at a time and are appended to a rolling buffer; whole packets are
    taken off the front and written straight out, so the guide lands in the log as it is
    broadcast. The two fields of a VBI line sit on adjacent rows of a woven frame, and each
    line is read as a stream of its own: a line carrying a degraded copy of another, or noise
    that passed for one, then costs nothing but its own packets, and a packet two lines both
    deliver is kept once.
    """
    setproctitle(current_process().name)

    streams = {}
    recent = {}
    recordings = []
    waiting = []
    frame = 0
    out_func = f = None

    def log(line):
        nonlocal out_func, f
        if out_func is None:
            out_func, f = get_output_function("starsight", output_filename)
        out_func('%s  %s' % (scc_timecode(frame), line))

    def take(recording, when, stream_id, commands, outlier=False):
        guide = recording.guide
        guide['packets'].append((when, stream_id, next(
            (decode_sequence_number(command) for command_type, command in commands
             if command_type == COMMAND_SEQUENCE_NUMBER and len(command) >= 6), None)))
        if not outlier:
            recording.last = max(recording.last, when)
        for command_type, command in commands:
            recording.counts[command_type] += 1
            try:
                described = describe_starsight_command(command_type, command, guide)
            except (IndexError, OverflowError):
                # a short command, or a Show List start a bit error put past 9999
                guide['undecoded'].append((command_type, 'shorter than its layout, or a time '
                                           'past 9999', bytes(command)))
                continue
            if command_type == COMMAND_SHOW_LIST and described:
                # a Show List can carry no slots, and then its channel is not counted
                recording.channels.add(_u16(command, 4) & CHANNEL_ID_MASK)
            for line in described:
                log(line)
                recording.records += 1
                recording.named += line.startswith('Guide')

    while True:
        try:
            rows = rx.recv()
            if rows == "DONE":
                break
        except:
            break

        frame += 1
        decoded = defaultdict(dict)
        for row_num, byte1, byte2, _, certainty in rows:
            if byte1 is not None:
                decoded[row_num >> 1][row_num] = (byte1, byte2, certainty)

        if not decoded.keys() <= streams.keys():
            streams.update((line, _LineStream()) for line in decoded.keys() - streams.keys())
            streams = dict(sorted(streams.items()))
        packets = []
        for line, stream in streams.items():
            packets += stream.frame(decoded.get(line, {}))

        for when, stream_id, commands in packets:
            key = (when, stream_id, tuple(bytes(command) for _, command in commands))
            if key in recent:
                continue
            recent[key] = None
            if len(recent) > STARSIGHT_RECENT_PACKETS:
                del recent[next(iter(recent))]

            current = recordings[-1] if recordings else None
            if current is None:
                recordings.append(_Recording(when, stream_id))
                take(recordings[-1], when, stream_id, commands)
            elif current.continues(when, stream_id):
                # a packet that broke off alone was a bad one, not a new recording
                for held in waiting:
                    take(current, *held, outlier=True)
                waiting = []
                take(current, when, stream_id, commands)
            elif waiting and _continues(waiting[-1][0], waiting[-1][1], when, stream_id):
                log('Recording    header clock %s GMT on stream %d, after %s GMT on stream %d:'
                    ' a guide of its own' % (_minute(waiting[0][0]), waiting[0][1],
                                             _minute(current.last), current.stream_id))
                recordings.append(_Recording(*waiting[0][:2]))
                for held in waiting:
                    take(recordings[-1], *held)
                waiting = []
                take(recordings[-1], when, stream_id, commands)
            else:
                for held in waiting:
                    take(current, *held, outlier=True)
                waiting = [(when, stream_id, commands)]

    for held in waiting:
        take(recordings[-1], *held, outlier=True)
    for recording in recordings:
        if recording.guide['segmented'] is not None:
            _abandon(recording.guide, recording.guide['segmented'], 'the capture ended first')

    # the lines that delivered packets; the others only ever carried what passed for one
    integrity = Counter()
    for stream in streams.values():
        if stream.integrity['accepted']:
            integrity += stream.integrity

    if out_func is not None:
        checksums = '%d packets passed both checksums%s' % (
            integrity['accepted'],
            ''.join(', %d failed the %s' % (integrity[k], k)
                    for k in ('header crc', 'body crc', 'checksummed but did not tile')
                    if integrity[k])
            + (', %d recovered from a lost line or a flipped bit'
               % integrity['body crc repaired'] if integrity['body crc repaired'] else ''))
        out_func('')
        for recording in recordings:
            guide = recording.guide
            if len(recordings) > 1:
                out_func('%s to %s GMT, stream %d, written to %s.starsight.*' % (
                    _minute(recording.first), _minute(recording.last), recording.stream_id,
                    os.path.basename(_recording_name(output_filename, recordings, recording))))
            out_func('%d records over %d channels, %d guide entries named, %d still waiting '
                     'on a title, %s' % (
                         recording.records, len(recording.channels), recording.named,
                         sum(len(s) for s in guide['pending'].values()),
                         ', '.join('%d %s' % (n, COMMAND_NAMES.get(t, 'type %d' % t))
                                   for t, n in recording.counts.most_common())))
            if len(recordings) == 1:
                out_func(checksums)
            transmitted, received, lost, breaks = packet_loss(guide['sequence'])
            if transmitted:
                out_func('%d packets by the sequence numbers, %d received, %d lost (%.2f%%)%s'
                         % (transmitted, received, lost, 100.0 * lost / transmitted,
                            ', %d break%s in the counter' % (breaks, '' if breaks == 1 else 's')
                            if breaks else ''))
        if len(recordings) > 1:
            out_func(checksums)

    if f is not None:
        f.close()

    for recording in recordings:
        if _has_guide(recording.guide):
            name = _recording_name(output_filename, recordings, recording)
            write_starsight_html(name, recording.guide, recording.counts)
            write_starsight_json(name, recording.guide)
