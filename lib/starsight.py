#!/usr/local/bin/python
# coding: utf-8
""" StarSight program guide decoding for ccDecoder

The StarSight Data Transmission Network protocol, carried in the NTSC vertical
blanking interval. US6216265B1 (StarSight Telecast) reproduces it in full - packet
header, command table and the byte layout of each command - and this follows that.

The waveform underneath is the CEA-608 one moved to another VBI line, so
`lib.cc_decode` recovers the bytes and only the parity rule differs: CEA-608 spends
the eighth bit on odd parity, StarSight spends it on data. Decoder silicon calls that
bearer Gemstar 1X, and RFC 2728 describes the same guide riding PBS National Datacast
instead, so the protocol is bearer independent. These captures are not NABTS.

Verified against three 1998 captures: every packet's `size` chains to the next sync,
the header timestamps are monotonic and agree with the CEA-608 XDS clock on line 21 of
the same tape to the minute, and the commands tile each packet exactly.

Records are written as they arrive, the way `decode_xds_packets` writes XDS, so the
file is a log of the guide in broadcast order rather than a report at the end.

See docs/starsight.md. Public domain / Unlicense, as with the rest of ccDecoder.
"""

import datetime
import html
import json

from collections import Counter, defaultdict, namedtuple
from multiprocessing import current_process

from setproctitle import setproctitle

from lib.cc_decode import LineParityRate, get_output_function, scc_timecode
from lib.starsight_table import STARSIGHT_HUFFMAN, STARSIGHT_HUFFMAN_MAX_BITS


# The payload is plain ASCII where a string is not compressed.
STARSIGHT_TABLE = {i: chr(i) for i in range(0x20, 0x7F)}

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
    """ The loader's CRC-32 over `data` """
    crc = 0xFFFFFFFF
    for byte in data:
        crc = STARSIGHT_CRC_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc

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

# Where each thing this decoder knows actually came from. Three sources, in the order they
# are trusted: a capture settles it, the loader's own code settles the layout, a patent
# settles what something is for. `docs/starsight_tables.py` renders this against every row
# it publishes, so a reader can tell a measured fact from an inferred one.
MEASURED = 'measured'   # seen and confirmed in one of the five captures
LOADER = 'loader'       # read from SSLOAD.DLL; no capture contains it
PATENT = 'patent'       # described by the patents; neither measured nor in the loader

PROVENANCE_NOTES = {
    MEASURED: 'Confirmed against the captures, byte for byte.',
    LOADER: "Read from SSLOAD.DLL's own code. No capture contains this, so the layout is "
            'as the loader reads it and has not been seen on a wire.',
    PATENT: 'Described in the StarSight patents. Neither measured nor present in the loader.',
}

# Which command types a capture has actually carried.
COMMANDS_SEEN = frozenset((1, 2, 5, 6, 8, 20, 21))

# A type the loader has code for is a type that can appear on the wire, so all of them are
# framed. Anything else would desynchronise the reader rather than be skipped.
COMMAND_LENGTH_ONE_BYTE = frozenset(t for t, (w, _, h) in COMMAND_TABLE.items()
                                    if w == 1 and h != 'none')
COMMAND_LENGTH_TWO_BYTE = frozenset(t for t, (w, _, h) in COMMAND_TABLE.items()
                                    if w == 2 and h != 'none')

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

COMMAND_NAMES = {
    1: 'Time',              2: 'Daylight Saving Change', 3: 'Region',
    4: 'Channel Data',      5: 'Show List',              6: 'Show Title',
    8: 'Show Description', 11: 'Theme Category',        12: 'Theme Sub-Category',
    13: 'Subscriber Reset',14: 'Authorization',         17: 'Key Distribution',
    20: 'Sequence Number', 21: 'Station Node Status',   22: 'Long Assign IR Codes',
    24: 'Subscriber Unit',
}

# A string is sent compressed unless compressing it would make it longer, so most of
# them are. `lib.starsight_table` holds the Huffman table that decodes them.
COMPRESSED_FLAG = 0x80

# Bit 3 of a Show Description flag byte selects the extended form, three more bytes of
# rating, advisories and year before the text.
DESCRIPTION_EXTENDED = 0x08

# Byte 8 of the extended form. SSLOAD.DLL scatters these five bits through a record of
# its own and reads them back against the strings below; they are the vocabulary it uses
# when the rating system carries advisories as flags rather than as text. Bits 0, 5 and 6
# are set by nothing in any capture.
ADVISORY_NAMES = ((0x02, 'nudity'), (0x04, 'violence'), (0x08, 'adult situations'),
                  (0x10, 'adult themes'), (0x80, 'adult language'))

# Byte 7 of the extended form: a rating system above a rating within that system.
RATING_SYSTEM_SHIFT = 5
RATING_CODE_SHIFT = 1
RATING_CODE_MASK = 0x0F

ShowRating = namedtuple('ShowRating', 'system code advisories year')

def description_advisories(advisories):
    """ The content advisories named by the extended form's byte 8 """
    return [name for mask, name in ADVISORY_NAMES if advisories & mask]

# A Show List's channel id is 15 bits; SSLOAD.DLL masks the high byte before using it.
CHANNEL_ID_MASK = 0x7FFF

# A slot's flag byte selects the optional fields that follow it. The loader sizes a slot
# at 4 bytes, 6 with a description id, and 2 more again with a show group.
SLOT_HAS_DESCRIPTION = 0x80
SLOT_HAS_SHOW_GROUP = 0x20

# Bit 6 is pay per view. SSLOAD.DLL's slot walker copies it to bit 7 of its own slot
# record and the database writer passes that bit as CTimeSlot's `TS Pay Per View`. The
# captures agree: it is set on 581 of the October capture's 23,836 slots and they sit on
# 11 channels of 201, covering 89% to 100% of each of those channels' listings.
SLOT_PAY_PER_VIEW = 0x40

# Bit 0, on the first slot of a list, is a program already running when the list
# starts: the loader consumes that slot and takes its duration as elapsed time.
SLOT_JOINED_IN_PROGRESS = 0x01

# What is left once the four above are named. SSLOAD.DLL reads none of these, and the
# captures set them on two slots in 43,430 - both on commands with other damage, which is
# what they look like: the bits an error can flip without changing a slot's length and so
# without failing the packet's tiling check.
SLOT_UNNAMED = 0x1E


class StarSightLines:
    """ Which lines are carrying StarSight rather than captions

    The mirror of `Cea608Lines`, over the same measurement: a caption line passes odd
    parity on very nearly every field, while StarSight uses that eighth bit for data
    and so passes at chance. A line is taken only once its rate has actually been
    measured, so a caption line is never read as guide data while the counts build.

    This cannot be replaced by letting the packet parser sort the lines out. Measured
    over the April capture, where the guide is on rows 0 and 1 at parity rates 0.238
    and 0.232 and the captions are on rows 14 and 15 at 1.000: buffering both guide
    rows gives 58 packets, and **every other combination of rows gives none at all** -
    either row on its own, every other pair, and all four together. The stream is one
    byte sequence alternating between the two fields of a single VBI line, which
    `weave` puts on adjacent rows, so it has to be read from both in order, and one
    caption byte mixed in destroys the framing for good. Finding that pair by trying
    subsets would mean 2^n parses; the parity rate names it in about twenty fields.

    Parity alone is not enough, because a line of noise that merely correlated with the
    preamble also fails parity at chance and so looks exactly like guide data. What
    tells them apart is how often the line is there at all: the guide is transmitted on
    every field, while noise only occasionally clears the correlation threshold. So a
    line is taken only if it has also decoded on at least half the fields seen. Measured
    on the April capture with the correlation threshold lowered to 0.3, three noise rows
    joined the two real ones and the interleaved garbage cut the packets that passed
    both checksums from 101 to 15; the presence test rejects all three - they decode on
    1 to 15 per cent of fields against the guide's 100 - and restores the full 101.
    """

    MAX_PARITY_RATE = 0.75
    MIN_PRESENCE_RATE = 0.5

    def __init__(self):
        self._parity = LineParityRate()
        self._frames = 0
        self._accepted = set()

    def frame(self):
        """ One more frame has been sliced, whether or not any line decoded on it """
        self._frames += 1

    def update(self, row_num, byte1, byte2):
        self._parity.update(row_num, byte1, byte2)
        rate = self._parity.rate(row_num)
        if rate is None:
            return

        present = self._parity.seen(row_num) >= self.MIN_PRESENCE_RATE * self._frames
        if rate < self.MAX_PARITY_RATE and present:
            self._accepted.add(row_num)
        else:
            self._accepted.discard(row_num)

    def accepts(self, row_num):
        return row_num in self._accepted

    def accepted(self):
        """ The lines being read, in row order """
        return sorted(self._accepted)


def starsight_time(minutes):
    """ Turn an on-wire minute count into a date and time """
    return STARSIGHT_EPOCH + datetime.timedelta(minutes=minutes)

def _u16(data, offset):
    return (data[offset] << 8) | data[offset + 1]

def _u16le(data, offset):
    """ The one little endian number in the format: the header's own checksum """
    return data[offset] | (data[offset + 1] << 8)

def _u32(data, offset):
    return (data[offset] << 24) | (data[offset + 1] << 16) | (data[offset + 2] << 8) | data[offset + 3]

def decompress_starsight_string(payload):
    """ Huffman decode a compressed string, or None if the bits do not resolve

    A NUL is appended to the string before compression, so the codeword for it marks
    the end and what follows is padding out to the byte boundary. Anything else - a
    codeword that is not in the table, or a terminator in the wrong place - means the
    payload was damaged in the VBI, and the caller is told rather than shown guesswork.
    """
    bits = ''.join(format(byte, '08b') for byte in payload)

    text = []
    offset = 0
    while offset < len(bits):
        for length in range(1, STARSIGHT_HUFFMAN_MAX_BITS + 1):
            symbol = STARSIGHT_HUFFMAN.get(bits[offset:offset + length])
            if symbol is None:
                continue
            if symbol == '\x00':
                return ''.join(text) if len(bits) - (offset + length) < 8 else None
            text.append(symbol)
            offset += length
            break
        else:
            return None

    return None

def decode_starsight_string(flags, payload):
    """ The text of a string command, compressed or in the clear """
    if not payload:
        return None
    if flags & COMPRESSED_FLAG:
        return decompress_starsight_string(payload)
    if payload[-1] != 0 or any(c not in STARSIGHT_TABLE for c in payload[:-1]):
        return None
    return ''.join(STARSIGHT_TABLE[c] for c in payload[:-1])

def decode_starsight_commands(data, start, end):
    """ Split a packet's message into commands

    Returns None unless the commands tile the message exactly. That is no longer the
    integrity check - both of the packet's own checksums are verified before this is
    called - but it is still worth doing: a body that checksums correctly and does not
    tile would be a command layout this does not know, and `take_starsight_packets`
    counts those. Across every capture the count is zero.
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

# A packet spans about a hundred fields, so on a worn tape a single VBI line that does
# not slice takes a whole packet with it. Those two bytes are not wrong, though, they are
# *unknown*, and the body checksum is enough to work out what they were.
STARSIGHT_MAX_LOST_BYTES = 2

# How many bits may be treated as erasures - positions known, values not - when solving a
# packet against its body checksum. Each one spends a bit of the checksum's thirty-two, so
# the budget is what is left over to verify with, and it is worth spending as little of it
# as the packet actually needs. These are tried in order and the first that both checksums
# and tiles wins, which is the answer that assumes the fewest broken bits.
#
# Measured on the 1994 KCET capture, that costs nothing and is worth a great deal: going
# straight to twenty recovers the same packets but spends 15.1 bits on average, while
# escalating spends 5.3 and so keeps 26.7 bits of checksum in hand rather than 16.9 - a
# wrong answer goes from one in 4,000 to one in 100 million. Twenty remains the ceiling:
# twenty-four would leave eight bits and twenty-eight four, at which point one repair in
# sixteen would be fiction.
#
# A plain confidence threshold does not work in its place. Some 34 bits a packet sit below
# three quarters of the usual margin, on sound packets and broken ones alike, so the cutoff
# separates nothing; it is the *ordering* by confidence that puts the broken bits first.
STARSIGHT_ERASURE_STEPS = (2, 4, 6, 8, 12, 16, 20)
STARSIGHT_MAX_ERASURES = STARSIGHT_ERASURE_STEPS[-1]

_CRC_ERROR_VECTORS = []

def _crc_error_vectors():
    """ What flipping one bit does to a packet's checksum, by distance from the end

    The CRC is linear over GF(2) - `crc(a ^ b) == crc(a) ^ crc(b) ^ crc(zeros)` - so what
    a set of flipped bits does to it is the XOR of what each one does alone. That makes
    the effect of every bit position worth tabulating once: a candidate repair is then a
    few XORs rather than a fresh checksum over the whole packet.

    What a bit does depends only on how many bytes follow it, not on how long the packet
    is, so one table serves every packet length. It is built from the last byte backwards,
    each step advancing the register over one more trailing zero byte.
    """
    if not _CRC_ERROR_VECTORS:
        vectors = [0] * (STARSIGHT_MAX_PACKET * 8)
        for bit in range(8):
            register = STARSIGHT_CRC_TABLE[1 << bit]
            for trailing in range(STARSIGHT_MAX_PACKET):
                vectors[(trailing << 3) | bit] = register
                register = (register >> 8) ^ STARSIGHT_CRC_TABLE[register & 0xFF]
        _CRC_ERROR_VECTORS.extend(vectors)
    return _CRC_ERROR_VECTORS

def _solve_crc(vectors, target):
    """ Which of `vectors` XOR to `target`, as a bit mask over them, or None

    Gaussian elimination over GF(2) on 32 bit rows. Each kept row is reduced by the rows
    above it and indexed by its highest set bit, so reducing in descending order of that
    bit never disturbs a row already passed.
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

    `lost` gives the offsets of bytes whose VBI line never sliced. They are unknown
    rather than wrong, which is the difference that makes this work: sixteen unknown bits
    against a thirty-two bit checksum leave sixteen bits over, so a wrong answer would
    have to hit a one in 65,536 coincidence. Bits that are wrong but not known to be are
    dearer, because their position has to be searched as well: one costs about eleven
    bits of the budget and two about twenty. The two are therefore not combined - a lost
    line is repaired on its own, and a search for flipped bits is only run on a packet
    that lost none - which keeps at least eleven bits of checksum in hand on every repair
    this returns. Whatever comes back must still tile into commands, which the caller
    checks and which no arithmetic here can fake.

    Measured on the 1994 KCET capture: of 154 packets whose header checksummed, 19 passed
    the body checksum outright and 71 do once repaired. All 71 carry sequence numbers
    that rise in step across the capture, which nothing in this function constrains.
    """
    if len(lost) > STARSIGHT_MAX_LOST_BYTES:
        return None

    target = starsight_crc(packet)
    if target == 0:
        return bytes(packet)

    table = _crc_error_vectors()
    end = len(packet) - 1

    # Erasures first: the bits whose position is already known, so only their value has to
    # be solved for. A lost line contributes all sixteen of its bits, and the slicer's
    # confidence names the rest - the bits it decided on the smallest margin, which is
    # where a dropout leaves its mark.
    erasures = [(offset << 3) | bit for offset in lost for bit in range(8)]
    known = set(erasures)
    if confidence is not None:
        for position in sorted(range(len(confidence)), key=confidence.__getitem__):
            if len(erasures) >= STARSIGHT_MAX_ERASURES:
                break
            if position not in known:
                erasures.append(position)
                known.add(position)

    # A byte that was never received is unknown outright, so every one of its bits has to
    # be in the set for the answer to mean anything; there is no point trying fewer.
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
            # Tiling is checked here rather than by the caller, because a candidate that
            # does not tile is a reason to spend more of the budget, not to give up.
            if starsight_crc(repaired) == 0 and decode_starsight_commands(
                    repaired, STARSIGHT_HEADER, len(repaired) - STARSIGHT_TRAILER) is not None:
                return bytes(repaired)

        if count == len(erasures):
            break

    if lost:
        # Bytes that were never received are still wrong, so a search for flipped bits
        # would only be finding a second way to satisfy a checksum the first answer
        # already failed. Nothing further is safe here.
        return None

    # No erasure explains it, so look for a flipped bit the confidence did not flag. One bit is
    # a straight lookup of the checksum's difference; two is the same lookup for every
    # first bit. It stops at two: by three the candidate pairs outnumber the checksum and
    # a wrong answer is no longer a coincidence.
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
    """ Consume whole packets from the front of a growing buffer

    Returns `(packets, consumed)`. Only a packet that has not fully arrived is worth
    keeping, so `consumed` runs right up to the byte the next call has to look at again;
    everything before it has either been read or been ruled out for good, and the caller
    drops it.

    `lost` gives the offsets of bytes whose VBI line never sliced, which are passed on to
    `repair_starsight_packet` so a packet that lost one can still be recovered.

    Both of the format's own checksums are used, which is what SSLOAD.DLL does and what it
    has instead of any structural test. The header's own CRC settles whether a `0x2C` is a
    sync at all - far better than the size merely looking plausible - and once it passes,
    the size is trustworthy, so a packet whose body fails can be stepped over whole rather
    than rescanned a byte at a time. Command tiling is kept as well, but demoted to a
    cross-check: a body that checksums correctly and still does not tile is not damage, it
    is something in the format this does not know, and `report` counts it.
    """
    packets = []
    offset = 0
    count = report if report is not None else Counter()

    while offset + STARSIGHT_HEADER <= len(data):
        if data[offset] != STARSIGHT_SYNC:
            offset += 1
            continue

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
            offset += size
            continue

        count['accepted'] += 1
        packets.append((starsight_time(_u32(packet, 3)), _u16(packet, 7), commands))
        offset += size

    return packets, offset

# Byte 2 of a Show Title carries three attribute bits above the compression flag. All
# three are confirmed against SSLOAD.DLL, Microsoft's StarSight loader: its type 6
# handler tests exactly these masks and carries them to the database columns
# `TS Closed Caption`, `TS Stereo`, and the broadcast property abbreviated `B/W`.
ATTRIBUTE_BLACK_AND_WHITE = 0x10
ATTRIBUTE_STEREO = 0x20
ATTRIBUTE_CLOSED_CAPTIONED = 0x40

def show_attributes(flags):
    """ The attribute bits of a Show Title flag byte

    A Show Description's flag byte reuses the same three bit positions for something
    else, so this is not for it.
    """
    return {
        'closed_captioned': bool(flags & ATTRIBUTE_CLOSED_CAPTIONED),
        'stereo': bool(flags & ATTRIBUTE_STEREO),
        'black_and_white': bool(flags & ATTRIBUTE_BLACK_AND_WHITE),
    }

def decode_show_title(command):
    """ `(show id, theme id, title)` - title is None when it was sent compressed

    The id is 16 bits and the low nibble of the flag byte is no part of it: SSLOAD.DLL's
    type 6 handler reads bytes 3-4 and nothing else. The nibble is zero on every title in
    every capture, so this reads the same as it always did.
    """
    flags = command[2]
    return _u16(command, 3), _u16(command, 5), decode_starsight_string(flags, command[7:])

def decode_show_description(command):
    """ `(description id, rating, description)` - text is None when it was undecodable

    Bit 3 of the flags selects an extended form that puts three more bytes in front of
    the text: byte 7 is a rating system in its top three bits and a rating within that
    system below it, byte 8 is a set of content advisory bits, and byte 9 is the last two
    digits of the year. The text then begins at 10 rather than 7. Reading it from 7
    regardless garbled 55 of the April capture's 233 descriptions, turning 'TVG Three
    World War II veterans come home' into 'ennhG, caLento skrting aioaens  II veterans
    come home'. `rating` is None on the plain form.
    """
    flags = command[2]
    if not flags & DESCRIPTION_EXTENDED:
        return _u16(command, 3), None, decode_starsight_string(flags, command[7:])

    # byte 9 is the last two digits; zero is how the extended form says it has none
    rating = ShowRating(command[7] >> RATING_SYSTEM_SHIFT,
                        (command[7] >> RATING_CODE_SHIFT) & RATING_CODE_MASK,
                        command[8], 1900 + command[9] if command[9] else None)
    return _u16(command, 3), rating, decode_starsight_string(flags, command[10:])

def decode_show_list(command):
    """ A channel's schedule: `(channel, [(start, minutes, show id)])`

    Slots carry a duration rather than a start, so the channel's own start time is
    carried once in the command header and each duration advances it.
    """
    channel = _u16(command, 4) & CHANNEL_ID_MASK
    when = starsight_time(_u32(command, 6))

    # the header says how many slots follow; without honoring it a truncated command
    # keeps accumulating durations and walks the clock off into the far future
    remaining = command[10]

    slots = []
    offset = 11
    while remaining and offset + 4 <= len(command):
        remaining -= 1
        slot_flags = command[offset]
        minutes = command[offset + 1]
        show_id = _u16(command, offset + 2)

        # the flags select two optional fields after the slot: a description to show
        # with the program, and a show group. Descriptions are on 48.5% of slots;
        # a show group has turned up once in 43,430.
        # read them only where they are actually present: a command truncated by a VBI
        # error still advances past the fields it claimed, the way it always has, so
        # the slots already parsed out of it survive
        extra = offset + 4
        description_id = show_group = None
        if slot_flags & SLOT_HAS_DESCRIPTION:
            if extra + 2 <= len(command):
                description_id = _u16(command, extra)
            extra += 2
        if slot_flags & SLOT_HAS_SHOW_GROUP:
            if extra + 2 <= len(command):
                show_group = _u16(command, extra)
            extra += 2

        slots.append((when, minutes, show_id, description_id, show_group, slot_flags))
        when += datetime.timedelta(minutes=minutes)

        offset = extra

    return channel, slots

def decode_time(command):
    """ `(time, standard time zone offset in hours, daylight saving)` from a Time command

    The offset is the station's standard offset; when daylight saving is in force the
    wall clock is an hour ahead of it.
    """
    when = starsight_time(_u32(command, 2)) + datetime.timedelta(seconds=command[7])
    zone = command[6] & 0x0F
    return when, -zone if command[6] & 0x10 else zone, bool(command[6] & 0x80)

# A Station Node Status block is 733 fixed bytes of the station's own health, sent about
# every five minutes. 438 of those bytes are identical across all seven instances in the
# three 1998 captures - tapes seven months apart - so most of it is a template. The fields
# below are the ones the captures actually pin down, and they are all **Unix** seconds,
# which no other part of this format uses: everything else counts minutes from 1992-01-01.
STATION_ASSEMBLED = 12
STATION_CLOCK = 48
STATION_EPOCH = datetime.datetime(1970, 1, 1)

StationNode = namedtuple('StationNode', 'assembled clock')

def decode_station_node(command):
    """ `(assembled, clock)` from a Station Node Status command, or None if it is short

    `clock` is the station's own clock at the moment of sending: it falls inside each
    capture's packet header window and steps forward between instances. `assembled` is
    constant within a capture and an hour or two behind it, so it reads as the moment the
    block's contents were put together rather than sent. Everything else in the 733 bytes
    is left alone - a pair of monotonic counters and what looks like a node bitmap - since
    seven instances is not enough to name fields from and SSLOAD.DLL does not read this
    command at all.
    """
    if len(command) <= STATION_CLOCK + 4:
        return None
    return StationNode(STATION_EPOCH + datetime.timedelta(seconds=_u32(command, STATION_ASSEMBLED)),
                       STATION_EPOCH + datetime.timedelta(seconds=_u32(command, STATION_CLOCK)))

# Channel Data, type 4. The call sign is not stored as a string: byte 7 is a presence
# mask, taken from the top bit down, saying which of the eight bytes at 8-15 are really
# letters. The loader copies the selected ones and pads the result to four with spaces.
CHANNEL_NUMBER_HIGH_BIT = 0x80
CHANNEL_CALL_SIGN_AT = 8
CHANNEL_CALL_SIGN_BYTES = 8
CHANNEL_CALL_SIGN_WIDTH = 4
CHANNEL_SHOWS_CALL_SIGN = 0x02

ChannelData = namedtuple('ChannelData', 'channel number call_sign shows_call_sign')

def decode_channel_data(command):
    """ `(channel, number, call sign, shows call sign)` from a Channel Data command

    The command this format needs and no capture contains: it binds a Show List's bare
    channel id to a tuning position and a set of call letters. Everything here is read
    from SSLOAD.DLL's type 4 handler and **nothing confirms it against a broadcast**,
    because the command rides a carousel slow enough that no half hour window caught one.
    """
    if len(command) < CHANNEL_CALL_SIGN_AT + CHANNEL_CALL_SIGN_BYTES:
        return None
    channel = ((command[3] & 0x7F) << 8) | command[4]
    number = command[6] | (0x100 if command[3] & CHANNEL_NUMBER_HIGH_BIT else 0)

    present = command[7]
    letters = ''
    for index in range(CHANNEL_CALL_SIGN_BYTES):
        if present & (0x80 >> index):
            letters += chr(command[CHANNEL_CALL_SIGN_AT + index])
    call_sign = letters[:CHANNEL_CALL_SIGN_WIDTH].ljust(CHANNEL_CALL_SIGN_WIDTH).rstrip()

    return ChannelData(channel, number, call_sign,
                       bool(command[5] & CHANNEL_SHOWS_CALL_SIGN))

# Theme Category and Theme Sub-Category, types 11 and 12. Both carry a version byte that
# the loader compares against the one it stored, reloading everything when it changes, then
# a count, then that many variable length entries. A name is a plain NUL terminated string,
# not Huffman coded - these are the only strings in the format that are not compressed.
THEME_ENTRIES_AT = 5
THEME_ENTRY_HEADER = 3

def decode_theme_names(command):
    """ `(version, [(id, name)])` from a Theme Category or Sub-Category command

    Read from SSLOAD.DLL's type 11 and 12 handlers, which walk the entries identically.
    No capture contains either command, so this is unconfirmed against a broadcast; it is
    what would fill `themes[].name`.
    """
    if len(command) <= THEME_ENTRIES_AT:
        return None
    version = command[3]
    count = command[4] & 0x7F

    entries = []
    offset = THEME_ENTRIES_AT
    while len(entries) < count and offset + THEME_ENTRY_HEADER <= len(command):
        identifier, length = command[offset], command[offset + 2]
        body = command[offset + THEME_ENTRY_HEADER:offset + THEME_ENTRY_HEADER + length]
        name = body.split(b'\0')[0].decode('latin1') if body else ''
        entries.append((identifier, name))
        offset += THEME_ENTRY_HEADER + length
    return version, entries

RegionData = namedtuple('RegionData', 'region value')

def decode_region(command):
    """ `(region id, a 32 bit field)` from a Region command

    The patents describe this as the command that names every channel a receiver in one
    territory can see, sent once per region, and say a Subscriber Unit learns its region id
    from an Authorization command before it can use any Channel Data. SSLOAD.DLL's type 3
    handler reads the two fields below out of a ten byte header; what follows them is a
    list this cannot yet name. Unconfirmed against a broadcast.
    """
    if len(command) < 10:
        return None
    return RegionData(_u16(command, 3), _u32(command, 6))

SUBSCRIBER_RESET_ACTIONS = ((0x01, 'clear stored guide'), (0x02, 'clear stored settings'))

def decode_subscriber_reset(command):
    """ What a Subscriber Reset command asks a receiver to discard

    SSLOAD.DLL's type 13 handler tests two bits of byte 2 and calls a different routine for
    each; the names below are what those routines appear to do and are the least certain
    thing in this file. Unconfirmed against a broadcast.
    """
    if len(command) < 3:
        return None
    return [name for mask, name in SUBSCRIBER_RESET_ACTIONS if command[2] & mask]

def decode_sequence_number(command):
    return _u32(command, 2)

def decode_daylight_saving(command):
    """ `(daylight saving starts, daylight saving ends)` from a type 2 command

    Two 32 bit minute counts on the same epoch as every other timestamp. The April
    capture sends 1998-04-05 02:00 and 1998-10-25 01:00, the two US transitions of 1998.
    """
    return starsight_time(_u32(command, 2)), starsight_time(_u32(command, 6))

def _rating_note(rating):
    """ What the extended form of a Show Description adds to its log line """
    if rating is None:
        return ''
    advisories = description_advisories(rating.advisories)
    return '%s system %d rating %-2d%s  ' % (
        rating.year or '----', rating.system, rating.code,
        ' (%s)' % ', '.join(advisories) if advisories else '')

def _slot_line(label, channel, start, minutes, show_id, title):
    return '%-12s ch %-6d %s GMT %4d min  id %-7d %s' % (
        label, channel, start.strftime('%Y-%m-%d %H:%M'), minutes, show_id, title)

def new_guide():
    """ What the receiver knows so far: pending slots, plus everything it has learned

    `titles` keep the flag byte alongside the text so the JSON export can read the
    attribute bits off it; `descriptions` keep the year the extended form of the command
    carried, or None. Neither the log nor the listing has to care.
    """
    return {'pending': {}, 'slots': [], 'blocks': {}, 'titles': {}, 'descriptions': {},
            'clock': [], 'daylight': [], 'sequence': [], 'station': [],
            'channel_data': {}, 'theme_names': {}, 'regions': [], 'resets': [],
            'unnamed': Counter(), 'packets': [], 'undecodable': Counter()}

def describe_starsight_command(command_type, command, guide):
    """ The lines one command contributes to the log, in broadcast order

    A Show List names its programs by id and the matching Show Title is always sent
    later - measured over a whole capture, not one of 23,836 slots had its title
    already in hand - so the schedule goes out as it arrives and `guide['pending']`
    holds the slots still waiting for a name. A Show Title then completes every slot it
    names, which is the moment a receiver could first have shown that entry. Everything
    is also kept in `guide` so the HTML listing can be assembled at the end.
    """
    if command_type == COMMAND_SHOW_TITLE:
        show_id, theme, title = decode_show_title(command)
        lines = ['Show Title   id %-7d theme %-6d %s' % (
            show_id, theme, title if title is not None else '(undecodable)')]

        if title is not None:
            guide['titles'][show_id] = (theme, title, command[2])
            lines += [_slot_line('Guide', channel, start, minutes, show_id, title)
                      for channel, start, minutes in guide['pending'].pop(show_id, ())]
        else:
            guide['undecodable']['titles'] += 1
        return lines

    if command_type == COMMAND_SHOW_DESCRIPTION:
        description_id, rating, text = decode_show_description(command)
        if text is not None:
            guide['descriptions'][description_id] = (text, rating)
        else:
            guide['undecodable']['descriptions'] += 1
        return ['Show Desc    id %-7d %s%s' % (
            description_id, _rating_note(rating),
            text if text is not None else '(undecodable)')]

    if command_type == COMMAND_SHOW_LIST:
        channel, slots = decode_show_list(command)

        if slots:
            block = (channel, slots[0][0].date())
            stale = guide['blocks'].get(block)
            if stale:
                guide['slots'] = [slot for slot in guide['slots'] if slot not in stale]
                for show_id in {slot[3] for slot in stale}:
                    waiting = [entry for entry in guide['pending'].get(show_id, ())
                               if entry[0] != channel]
                    if waiting:
                        guide['pending'][show_id] = waiting
                    else:
                        guide['pending'].pop(show_id, None)
            guide['blocks'][block] = set()

        for start, minutes, show_id, description_id, show_group, slot_flags in slots:
            entry = (start, channel, minutes, show_id,
                     description_id, show_group, slot_flags)
            guide['pending'].setdefault(show_id, []).append((channel, start, minutes))
            guide['slots'].append(entry)
            guide['blocks'][(channel, slots[0][0].date())].add(entry)
        return [_slot_line('Show List', channel, start, minutes, show_id, '(title not sent yet)')
                for start, minutes, show_id, _, _, _ in slots]

    if command_type == COMMAND_CHANNEL_DATA:
        channel = decode_channel_data(command)
        if channel is None:
            return []
        guide['channel_data'][channel.channel] = channel
        return ['Channel Data ch %-6d number %-4d %s' % (
            channel.channel, channel.number, channel.call_sign or '(no call sign)')]

    if command_type in (COMMAND_THEME_CATEGORY, COMMAND_THEME_SUB_CATEGORY):
        named = decode_theme_names(command)
        if named is None:
            return []
        version, entries = named
        for identifier, name in entries:
            guide['theme_names'][identifier] = name
        return ['%-12s version %-4d %d name%s' % (
            COMMAND_NAMES[command_type], version, len(entries),
            '' if len(entries) == 1 else 's')]

    if command_type == COMMAND_REGION:
        region = decode_region(command)
        if region is None:
            return []
        guide['regions'].append(region)
        return ['Region       id %-6d %d' % (region.region, region.value)]

    if command_type == COMMAND_SUBSCRIBER_RESET:
        actions = decode_subscriber_reset(command)
        if actions is None:
            return []
        guide['resets'].append(tuple(actions))
        return ['Reset        %s' % (', '.join(actions) or 'nothing')]

    if command_type == COMMAND_STATION_NODE_STATUS:
        # 733 fixed bytes of the station's own health, about every five minutes. Roughly
        # 95% of it is identical from one to the next; what moves is a pair of 32 bit
        # counters and scattered single bits of what looks like a node bitmap. Three
        # instances in a capture is not enough to name fields from, and SSLOAD.DLL is no
        # help - its table sends type 21 to the trace function like type 20 - so the
        # command is framed, checksummed, counted and reported, and not invented.
        node = decode_station_node(command)
        guide['station'].append((node, bytes(command)))
        if node is None:
            return ['Station Node %d bytes' % len(command)]
        return ['Station Node %d bytes  assembled %s  station clock %s GMT' % (
            len(command), node.assembled.strftime('%Y-%m-%d %H:%M'),
            node.clock.strftime('%Y-%m-%d %H:%M:%S'))]

    if command_type == COMMAND_SEQUENCE_NUMBER:
        guide['sequence'].append(decode_sequence_number(command))
        return []

    if command_type == COMMAND_DAYLIGHT_SAVING:
        starts, ends = decode_daylight_saving(command)
        guide['daylight'].append((starts, ends))
        return ['Daylight     %s GMT to %s GMT' % (
            starts.strftime('%Y-%m-%d %H:%M'), ends.strftime('%Y-%m-%d %H:%M'))]

    if command_type == COMMAND_TIME:
        when, zone, daylight = decode_time(command)
        guide['clock'].append((when, zone, daylight))
        return ['Time         %s GMT  station offset %+d hours%s' % (
            when.strftime('%Y-%m-%d %H:%M:%S'), zone,
            ', daylight saving' if daylight else '')]

    if COMMAND_TABLE.get(command_type, (0, 0, 'none'))[2] == 'implemented':
        guide['unnamed'][command_type] += 1
        return ['Type %-8d %d bytes (no published meaning)' % (command_type, len(command))]

    return []

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

    box.ch.textContent=cell.closest('.row').querySelector('.badge').textContent;
    var text=window.DESC&&DESC[cell.getAttribute('d')];
    box.title.textContent=cell.firstChild.textContent+(text?'  -  '+text:'');
    box.show.textContent='show '+cell.getAttribute('n')
      +(cell.getAttribute('t')?'  theme '+cell.getAttribute('t'):'')
      +(cell.getAttribute('d')?'  description '+cell.getAttribute('d'):'')
      +(cell.getAttribute('g')?'  group '+cell.getAttribute('g'):'');
    box.time.textContent=start.toDateString().slice(0,10).toUpperCase()
      +'  '+clock(start)+'-'+clock(stop)+'  '+run+' min';
    box.flags.textContent=flags.join(', ');
  });
}

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

def _clock_label(when):
    """ 8:00P, the way the guide wrote a time """
    hour = when.hour % 12 or 12
    return '%d:%02d%s' % (hour, when.minute, 'A' if when.hour < 12 else 'P')

def _guide_grid(out_func, slots, titles, descriptions, offset):
    """ The schedule laid out the way the guide itself laid it out

    Channels down the side, half hours across the top, and a program drawn as one
    cell as wide as it is long - US6498895B2's "array of irregular cells, which vary in
    length, corresponding to different television program lengths". A cell is placed by
    arithmetic rather than by table columns, because a slot can be 5 minutes or 240 and
    nothing divides evenly; every cell carries the rest of its slot's fields, which the
    box underneath shows for whichever one is under the pointer.
    """
    if not slots:
        return

    esc = html.escape
    shown = lambda when: _local(when, offset) or when

    middle = sorted(shown(slot[0]) for slot in slots)[len(slots) // 2]
    reach = datetime.timedelta(days=GUIDE_MAX_DAYS)
    drawn = [slot for slot in slots if abs(shown(slot[0]) - middle) <= reach]
    adrift = len(slots) - len(drawn)

    window = min(shown(slot[0]) for slot in drawn).replace(minute=0, second=0, microsecond=0)
    last = max(shown(slot[0]) + datetime.timedelta(minutes=slot[2]) for slot in drawn)
    span = int((last - window).total_seconds() // 60 + 29) // 30 * 30
    minutes_of = lambda when: int((when - window).total_seconds() // 60)

    channels = defaultdict(list)
    for start, channel, length, show_id, description_id, show_group, slot_flags in drawn:
        channels[channel].append((shown(start), length, show_id, description_id,
                                  show_group, slot_flags))

    # the capture rarely starts at midnight, so the first day is a partial one and
    # still needs its own tab
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

    out_func("<div class='guide' id='guide' data-from='%s'>" % window.isoformat(' '))
    out_func("<div class='head'><div class='corner'></div>"
             "<div class='ticks' style='width:calc(%d * var(--px))'>" % span)
    for at in range(0, span, 30):
        when = window + datetime.timedelta(minutes=at)
        midnight = when.hour == 0 and when.minute == 0
        out_func("<b class='day' style='--a:%d'>%s</b>" % (at, when.strftime('%a %-d').upper())
                 if midnight else
                 "<b style='--a:%d'>%s</b>" % (at, _clock_label(when)))
    out_func("</div></div>")

    for channel in sorted(channels):
        out_func("<div class='row'><div class='badge'>%d</div>"
                 "<div class='lane' style='width:calc(%d * var(--px))'>" % (channel, span))
        for start, length, show_id, description_id, show_group, slot_flags in sorted(
                channels[channel]):
            named = titles.get(show_id)
            attributes = 'P' if slot_flags & SLOT_PAY_PER_VIEW else ''
            if named is not None:
                flags = show_attributes(named[2])
                attributes += ('C' if flags['closed_captioned'] else '') + \
                              ('S' if flags['stereo'] else '') + \
                              ('B' if flags['black_and_white'] else '')
            label = esc(named[1]) if named is not None else '#%d' % show_id
            extra = ''
            if named is not None:
                extra += " t=%d" % named[0]
            if description_id is not None:
                extra += " d=%d" % description_id
            if show_group is not None:
                extra += " g=%d" % show_group
            if attributes:
                extra += " f=%s" % attributes
            # bits 1-4 are the only ones no attribute above stands for; carry the raw
            # byte when any of them is set, so nothing the wire said is dropped
            if slot_flags & SLOT_UNNAMED:
                extra += " x=%d" % slot_flags
            classes = (' class="%s"' % ' '.join(
                (['ppv'] if slot_flags & SLOT_PAY_PER_VIEW else [])
                + (['joined'] if slot_flags & SLOT_JOINED_IN_PROGRESS else []))
                if slot_flags & (SLOT_PAY_PER_VIEW | SLOT_JOINED_IN_PROGRESS) else '')
            out_func("<i%s style='--a:%d;--b:%d' n=%d%s>%s%s</i>" % (
                classes, minutes_of(start), length, show_id, extra, label,
                "<u> %s</u>" % attributes if attributes else ''))
        out_func("</div></div>")
    out_func("</div>")

    out_func("<div class='key'>"
             + ("<span><b>%d</b> listing%s fall outside this window and are not drawn - "
                "a corrupt start time carries a whole command with it</span>"
                % (adrift, '' if adrift == 1 else 's') if adrift else '')
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

    # only the descriptions a slot actually points at, so a capture carrying both a Show
    # List and the descriptions it references can show the text in the box. No capture
    # here carries both, and then this is empty and costs nothing.
    wanted = {slot[4] for slot in slots if slot[4] is not None} & set(descriptions)
    out_func("<script>var DESC=%s</script>" % json.dumps(
        {str(k): descriptions[k][0] for k in sorted(wanted)}, separators=(',', ':')))

def _local(when, offset):
    """ Station wall clock for a GMT instant, or None if the clock was never sent """
    return None if offset is None else when + datetime.timedelta(hours=offset)

def _table(out_func, table_id, heading, note, columns, rows):
    """ One command type, on its own, with nothing joined onto it

    Column alignment goes in a rule per table rather than a class on every cell: at
    23,836 rows the repeated attributes would be most of the file.
    """
    if not rows:
        return
    out_func("<h2>%s</h2><p>%s rows. %s</p>" % (heading, format(len(rows), ',d'), note))
    rules = ''.join('#%s td:nth-child(%d){%s}'
                    % (table_id, i + 1, 'text-align:right' if css == 'num'
                       else 'white-space:normal;max-width:48em')
                    for i, (_, css) in enumerate(columns) if css)
    out_func("<style>%s</style><table id='%s'><thead><tr>" % (rules, table_id)
             + "".join("<th>%s<b></b>" % name for name, _ in columns)
             + "</thead><tbody>")
    # closing tags are left off: at this many rows the markup is most of the file, and
    # HTML closes a cell at the next one anyway
    for row in rows:
        out_func("<tr>" + "".join("<td>%s" % value for value in row))
    out_func("</tbody></table>")

def _iso(when):
    """ The wire carries GMT, so every instant in the export is written as GMT """
    return when.strftime('%Y-%m-%dT%H:%M:%SZ')

def _channel_entry(channel, data):
    """ One channel, with whatever a Channel Data command said about it

    A capture that carries no Channel Data leaves every channel as a bare id, which is
    what all five of them do; the fields only appear once one is caught.
    """
    entry = {'id': channel}
    if data is not None:
        if data.call_sign:
            entry['callSign'] = data.call_sign
        entry['channelNumber'] = data.number
        if data.shows_call_sign:
            entry['showsCallSign'] = True
    return entry

def _description_entry(description_id, text, rating):
    """ One Show Description, with whatever its extended form carried

    The rating fields are written as the broadcast numbered them. SSLOAD.DLL keeps the
    same two numbers and never maps them to a name from the wire - where a rating has a
    printable name it is already at the front of the text, which is where the loader
    reads it from too.
    """
    entry = {'id': description_id, 'text': text}
    if rating is not None:
        entry['ratingSystem'] = rating.system
        entry['rating'] = rating.code
        if rating.year:
            entry['year'] = rating.year
        advisories = description_advisories(rating.advisories)
        if advisories:
            entry['advisories'] = advisories
    return entry

def write_starsight_json(output_filename, guide):
    """ The guide as normalized JSON, for a page to query

    Only what the broadcast carried: five entity tables and a flat table of listings,
    which is the shape the query patterns want - a listing names its channel and
    program by the ids the broadcast used, and the page joins them. Nothing about the
    decode itself is written. The on-wire ids are kept as they were - renumbering
    them by how often they are referenced was measured and made the gzipped file
    larger, because gzip already models the repetition and shortening a five digit
    token leaves it less to match.

    Nothing derived is stored. Indexes cost more to ship than to rebuild: a page parses
    this file in about seven milliseconds and builds its own indexes in five.
    """
    slots, titles, descriptions = guide['slots'], guide['titles'], guide['descriptions']
    clock, daylight = guide['clock'], guide['daylight']
    channel_data, theme_names = guide['channel_data'], guide['theme_names']

    programs = {}
    for show_id in {slot[3] for slot in slots} | set(titles):
        named = titles.get(show_id)
        entry = {'id': show_id}
        if named is not None:
            theme, title, flags = named
            attributes = show_attributes(flags)
            entry['title'] = title
            entry['theme'] = theme
            # a flag is written only when it is set: a false costs bytes and says
            # nothing the reader could not assume from its absence
            for name, value in (('closedCaptioned', attributes['closed_captioned']),
                                ('stereo', attributes['stereo']),
                                ('blackAndWhite', attributes['black_and_white'])):
                if value:
                    entry[name] = True
        programs[show_id] = entry

    listings = []
    for start, channel, minutes, show_id, description_id, show_group, slot_flags in slots:
        listing = {'channel': channel, 'start': _iso(start),
                   'durationMinutes': minutes, 'program': show_id}
        if description_id is not None:
            listing['description'] = description_id
        if show_group is not None:
            listing['showGroup'] = show_group
        if slot_flags & SLOT_PAY_PER_VIEW:
            listing['payPerView'] = True
        if slot_flags & SLOT_JOINED_IN_PROGRESS:
            listing['joinedInProgress'] = True
        listing['slotFlags'] = slot_flags
        listings.append(listing)

    document = {
        'channels': [_channel_entry(channel, channel_data.get(channel))
                     for channel in sorted({slot[1] for slot in slots} | set(channel_data))],
        'themes': [dict({'id': theme}, **({'name': theme_names[theme]}
                                          if theme in theme_names else {}))
                   for theme in sorted({t[0] for t in titles.values()} | set(theme_names))],
        'programs': [programs[show_id] for show_id in sorted(programs)],
        'descriptions': [_description_entry(description_id, text, rating)
                         for description_id, (text, rating) in sorted(descriptions.items())],
        'listings': listings,
        'clock': [{'time': _iso(when), 'utcOffsetMinutes': zone * 60,
                   'daylightSaving': saving} for when, zone, saving in clock],
        'daylightSavingChanges': [{'starts': _iso(starts), 'ends': _iso(ends)}
                                  for starts, ends in daylight],
        'sequenceNumbers': guide['sequence'],
    }

    out_func, f = get_output_function("starsight.json", output_filename, end="")
    out_func(json.dumps(document, separators=(',', ':')))
    if f is not None:
        f.close()

def _from_iso(stamp):
    """ The GMT instant `_iso` wrote """
    return datetime.datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ')

def read_starsight_json(document):
    """ The guide a `write_starsight_json` document came from

    The inverse of the export, so a decoded capture can be read back and merged with
    another. The attribute bits are rebuilt into a flag byte because that is what the
    rest of the code reads them from; the compression bit is not among them, since it
    says how a string travelled rather than anything about the program, and the export
    does not carry it.
    """
    guide = new_guide()

    for program in document.get('programs', ()):
        if 'title' not in program:
            continue
        flags = ((ATTRIBUTE_CLOSED_CAPTIONED if program.get('closedCaptioned') else 0)
                 | (ATTRIBUTE_STEREO if program.get('stereo') else 0)
                 | (ATTRIBUTE_BLACK_AND_WHITE if program.get('blackAndWhite') else 0))
        guide['titles'][program['id']] = (program['theme'], program['title'], flags)

    for entry in document.get('channels', ()):
        if 'callSign' in entry or 'channelNumber' in entry:
            guide['channel_data'][entry['id']] = ChannelData(
                entry['id'], entry.get('channelNumber', 0), entry.get('callSign', ''),
                entry.get('showsCallSign', False))

    for entry in document.get('themes', ()):
        if 'name' in entry:
            guide['theme_names'][entry['id']] = entry['name']

    for entry in document.get('descriptions', ()):
        rating = None
        if 'ratingSystem' in entry:
            named = set(entry.get('advisories', ()))
            advisories = sum(mask for mask, name in ADVISORY_NAMES if name in named)
            rating = ShowRating(entry['ratingSystem'], entry['rating'], advisories,
                                entry.get('year'))
        guide['descriptions'][entry['id']] = (entry['text'], rating)

    for listing in document.get('listings', ()):
        guide['slots'].append((_from_iso(listing['start']), listing['channel'],
                               listing['durationMinutes'], listing['program'],
                               listing.get('description'), listing.get('showGroup'),
                               listing['slotFlags']))

    for entry in document.get('clock', ()):
        guide['clock'].append((_from_iso(entry['time']),
                               entry['utcOffsetMinutes'] // 60, entry['daylightSaving']))

    for entry in document.get('daylightSavingChanges', ()):
        guide['daylight'].append((_from_iso(entry['starts']), _from_iso(entry['ends'])))

    guide['sequence'] = list(document.get('sequenceNumbers', ()))

    return guide

# A gap wider than this is not packets missing, it is the counter itself being wrong: a
# single flipped bit in a 32 bit number moves it by up to 2^31, and one does exactly that
# in the 1994 capture, stepping 5,720 -> 1,054,298. Real gaps in these captures are 1 to 4.
SEQUENCE_MAX_GAP = 256

# How far either side of the middle of a schedule the guide grid will draw. Generous
# enough for any real guide, and for tapes months apart merged together; small enough that
# a corrupted timestamp cannot ask for a grid millions of columns wide.
GUIDE_MAX_DAYS = 400

def packet_loss(sequence):
    """ `(transmitted, received, lost, breaks)` from the sequence numbers a capture caught

    Counted from consecutive steps rather than from the span, because the span is only as
    trustworthy as the largest value in it and a bit error can put that anywhere. A step
    of one is a packet received, a small step is that many packets missing, and anything
    larger is a `break` - a corrupt counter, or a capture that stopped and started - which
    is reported separately and left out of the loss rather than swamping it.
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
    """ The loader's 'Delete Expired Time Slot': drop what had already finished

    A receiver has no use for a program that has ended, so the loader deletes time
    slots that finish before a date it passes in. For a decode that date is the tape's
    own clock, and then this removes nothing - the guide runs days ahead of the capture,
    so every slot in all five captures is still in the future. It is worth having anyway,
    and worth keeping out of a merge: run against the newest of several tapes it would
    delete every older tape's schedule, which is right for a receiver and wrong for an
    archive.
    """
    return [slot for slot in slots
            if slot[0] + datetime.timedelta(minutes=slot[2]) > when]

def merge_guides(guides, expire=False):
    """ `(guide, report)` from several guides of the same service

    Tapes of one channel overlap: the same program rides the carousel again and again,
    and two tapes a week apart share the days between them. So everything is keyed rather
    than concatenated, and keyed the way `SSLOAD.DLL` keys it.

    **A channel's day is the unit.** The loader replaces what it receives rather than
    adding to it, bounding a 'Delete Omitted Time Slot' by the window the load covered, so
    a later tape carrying a revised day supersedes the earlier one instead of both
    surviving as overlapping listings. Guides are put in the order they were received -
    from their own Time commands, which is broadcast data and not something this decoder
    adds - and the last one to carry a channel-day wins it.

    **A repeated key updates.** The loader writes every record with `UpdateRS` against an
    index in its own id space: seek, then update if present and add if not. So the newest
    value of a show number or a description wins here too.

    That last rule needs the warning `report` carries, because for show numbers a repeat
    is not always a revision - **StarSight reuses them**. Across the two 1998 captures a
    week apart, 2,209 show numbers are shared and exactly one names a different program;
    across captures six and seven months apart, 116 of 801 and 189 of 1,323 do. Numbers
    are stable within a season and recycled between them, so merging tapes that far apart
    mixes two meanings of one number and no choice of winner is right.

    `expire` applies the loader's other delete, against the newest capture instant. It is
    off by default because it is a receiver's rule, not an archive's: see `expire_slots`.
    """
    merged = new_guide()
    report = Counter()
    blocks = {}

    # oldest first, so the newest tape is the one that lands last and wins
    ordered = sorted(guides, key=lambda guide: (captured_at(guide) is not None,
                                                captured_at(guide) or datetime.datetime.min))

    for guide in ordered:
        day = defaultdict(list)
        for slot in guide['slots']:
            day[(slot[1], slot[0].date())].append(slot)
        report['channel days superseded'] += len(set(day) & set(blocks))
        blocks.update(day)

        for table in ('titles', 'descriptions'):
            for key, value in guide[table].items():
                if key in merged[table]:
                    report[table + ' shared'] += 1
                    if merged[table][key] != value:
                        report[table + ' reused'] += 1
                merged[table][key] = value
        merged['clock'] += guide['clock']
        merged['daylight'] += guide['daylight']
        merged['sequence'] += guide['sequence']
        merged['channel_data'].update(guide['channel_data'])
        merged['theme_names'].update(guide['theme_names'])

    merged['blocks'] = blocks
    slots = {slot for block in blocks.values() for slot in block}

    if expire:
        when = captured_at(merged) or max((slot[0] for slot in slots), default=None)
        if when is not None:
            kept = set(expire_slots(slots, when))
            report['slots expired'] = len(slots) - len(kept)
            slots = kept

    # a slot cannot be sorted on its optional fields, which are None when absent
    merged['slots'] = sorted(slots, key=lambda slot: slot[:4])
    merged['clock'] = sorted(set(merged['clock']))
    merged['daylight'] = sorted(set(merged['daylight']))
    # tapes months apart carry unrelated stretches of the counter; keeping them sorted and
    # unique is right, but a loss figure across them would be meaningless
    merged['sequence'] = sorted(set(merged['sequence']))

    at = Counter((slot[1], slot[0]) for slot in merged['slots'])
    report['slots overlapping'] = sum(n - 1 for n in at.values() if n > 1)
    return merged, report

def write_starsight_html(output_filename, guide, counts=None):
    """ The schedule as a guide grid, and every other command type as its own table

    The grid joins a Show List slot to its Show Title, which is the one join the
    broadcast supports and the one a reader wants. Everything else stays unjoined and
    under its own numbering, because those are separate records and relating them here
    would hide which of them the capture actually contained. Clicking a heading sorts a
    table, and earlier choices stay on as further keys.
    """
    out_func, f = get_output_function("starsight.html", output_filename, end="")

    counts = counts or Counter()
    slots, titles, descriptions = guide['slots'], guide['titles'], guide['descriptions']
    clock, daylight = guide['clock'], guide['daylight']
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

    _guide_grid(out_func, slots, titles, descriptions, offset)

    _table(out_func, 'showtitle', 'Show Title', 'One per program. The theme number groups programs by genre.',
           (('Show number', 'num'), ('Theme number', 'num'), ('Title', 'wrap')),
           [(show_id, theme, esc(title))
            for show_id, (theme, title, _) in sorted(titles.items())])

    _table(out_func, 'showdesc', 'Show Description',
           'Numbered separately from the show numbers above; a Show List slot points at '
           'one through the optional field its flags select. Year, rating and advisories '
           'come from the extended form of the command and are blank on the plain one.',
           (('Description number', 'num'), ('Year', 'num'), ('Rating system', 'num'),
            ('Rating', 'num'), ('Advisories', 'wrap'), ('Description', 'wrap')),
           [(description_id,
             rating.year if rating and rating.year else '',
             rating.system if rating else '', rating.code if rating else '',
             esc(', '.join(description_advisories(rating.advisories))) if rating else '',
             esc(text))
            for description_id, (text, rating) in sorted(descriptions.items())])

    _table(out_func, 'time', 'Time', 'The station clock, sent about twice a minute.',
           (('Time (GMT)', ''), ('Standard offset', 'num'), ('Daylight saving', '')),
           [(when.strftime('%Y-%m-%d %H:%M:%S'), '%+d' % zone, 'yes' if saving else 'no')
            for when, zone, saving in clock])

    _table(out_func, 'daylight', 'Daylight Saving Change',
           'The two instants the station changes its clock, sent alongside the time.',
           (('Daylight saving starts (GMT)', ''), ('Daylight saving ends (GMT)', '')),
           [(starts.strftime('%Y-%m-%d %H:%M'), ends.strftime('%Y-%m-%d %H:%M'))
            for starts, ends in daylight])

    transmitted, received, lost, breaks = packet_loss(guide['sequence'])
    if transmitted:
        out_func("<h2>Reception</h2>")
        out_func("<p><b>%s of %s packets received, %s lost (%.2f%%)</b>.%s</p>"
                 % (format(received, ',d'), format(transmitted, ',d'), format(lost, ',d'),
                    100.0 * lost / transmitted,
                    " %d break%s in the counter, too wide to be missing packets and read "
                    "as a corrupt value instead, are left out of that."
                    % (breaks, '' if breaks == 1 else 's') if breaks else ''))
        _table(out_func, 'gaps', 'Lost packets',
               'Where in the broadcast the missing packets fall.',
               (('Last packet before the gap', 'num'), ('Packets missing', 'num')),
               sequence_gaps(guide['sequence']))

    _table(out_func, 'commands', 'Commands',
           'Every command seen in the capture. All of these are decoded.',
           (('Type', 'num'), ('Command', ''), ('Count', 'num')),
           [(command_type, COMMAND_NAMES.get(command_type, 'unknown'), count)
            for command_type, count in sorted(counts.items())])

    out_func("</main><script>%s</script></body></html>" % STARSIGHT_HTML_SCRIPT)

    if f is not None:
        f.close()

def decode_starsight(rx, output_filename, options):
    """ Decode the StarSight program guide carried on its own VBI line

    Rows arrive a frame at a time and are appended to a rolling buffer; whole packets
    are taken off the front and written straight out, so the guide lands in the file
    as it is broadcast rather than being held back to the end.
    """
    setproctitle(current_process().name)

    lines = StarSightLines()
    buffer = bytearray()
    confidence = []
    lost = []
    guide = new_guide()
    counts = Counter()
    integrity = Counter()
    channels = set()
    frame = 0
    records = 0
    named = 0

    out_func = None
    f = None

    while True:
        try:
            rows = rx.recv()
            if rows == "DONE":
                break
        except:
            break

        frame += 1
        lines.frame()

        decoded = {}
        for row_num, byte1, byte2, _, certainty in rows:
            if byte1 is None:
                continue

            lines.update(row_num, byte1, byte2)
            decoded[row_num] = (byte1, byte2, certainty)

        # A line that did not slice leaves a hole rather than nothing at all. Dropping
        # its two bytes would shorten the packet they fall in and shift everything after
        # them, which is what used to cost the 1994 capture all but three of its packets;
        # held open, the hole is two unknown bytes the body checksum can solve for.
        for row_num in lines.accepted():
            if row_num in decoded:
                byte1, byte2, certainty = decoded[row_num]
                buffer += bytes((byte1, byte2))
                confidence += list(certainty)
            else:
                lost += [len(buffer), len(buffer) + 1]
                buffer += bytes(2)
                confidence += [0.0] * 16

        packets, consumed = take_starsight_packets(buffer, integrity, lost, confidence)
        if consumed:
            del buffer[:consumed]
            del confidence[:consumed * 8]
            lost = [position - consumed for position in lost if position >= consumed]

        for when, stream_id, commands in packets:
            guide['packets'].append((when, stream_id))

            for command_type, command in commands:
                counts[command_type] += 1

                try:
                    described = describe_starsight_command(command_type, command, guide)
                    if command_type == COMMAND_SHOW_LIST and described:
                        # a Show List can carry no slots at all, and a channel that
                        # never reaches the log should not reach the count either
                        channels.add(_u16(command, 4) & CHANNEL_ID_MASK)
                except IndexError:
                    continue

                for line in described:
                    if out_func is None:
                        out_func, f = get_output_function("starsight", output_filename)

                    out_func('%s  %s' % (scc_timecode(frame), line))
                    records += 1
                    named += line.startswith('Guide')

    if out_func is not None:
        waiting = sum(len(s) for s in guide['pending'].values())
        out_func('')
        out_func('%d records over %d channels, %d guide entries named, %d still waiting '
                 'on a title, %s' % (
                     records, len(channels), named, waiting,
                     ', '.join('%d %s' % (n, COMMAND_NAMES.get(t, 'type %d' % t))
                               for t, n in counts.most_common())))
        out_func('%d packets passed both checksums%s' % (
            integrity['accepted'],
            ''.join(', %d failed the %s' % (integrity[k], k)
                    for k in ('header crc', 'body crc', 'checksummed but did not tile')
                    if integrity[k])
            + (', %d recovered from a lost line or a flipped bit'
               % integrity['body crc repaired'] if integrity['body crc repaired'] else '')))
        transmitted, received, lost, breaks = packet_loss(guide['sequence'])
        if transmitted:
            out_func('%d packets by the sequence numbers, %d received, %d lost (%.2f%%)%s'
                     % (transmitted, received, lost, 100.0 * lost / transmitted,
                        ', %d break%s in the counter' % (breaks, '' if breaks == 1 else 's')
                        if breaks else ''))

    if f is not None:
        f.close()

    if guide['slots'] or guide['titles']:
        write_starsight_html(output_filename, guide, counts)
        write_starsight_json(output_filename, guide)
