#!/usr/bin/env python3
""" Write the StarSight protocol reference as JSON and as a page

Everything the format is known to say, in one place: the packet, the checksums, the
command table, the byte layout of each command, the meaning of every flag bit, and the 127
symbol compression table. It is read back out of `lib.starsight` so the reference cannot
drift from the decoder, and the command table is checked against the tables extracted from
the loader (`docs/research/ssload-tables.json`) before anything is written. Where each part
comes from is in `docs/research/starsight-sources.md`.

    python3 docs/starsight_tables.py            # writes docs/starsight-protocol.{json,html}

Public domain / Unlicense, as with the rest of ccDecoder.
"""

import html
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

from lib.starsight import (ADVISORY_NAMES, ADVISORY_UNKNOWN, ATTRIBUTE_BLACK_AND_WHITE,
                           ATTRIBUTE_CLOSED_CAPTIONED, ATTRIBUTE_STEREO, CHANNEL_ID_MASK,
                           CHANNEL_LABEL_WIDTH, CHANNEL_SHOWS_CALL_SIGN, COMMAND_ENCRYPTED,
                           COMMAND_NAMES, COMMAND_TABLE, COMPRESSED_FLAG, DESCRIPTION_EXTENDED,
                           NEWS_PLAIN, RATING_NAMES, REGION_APPLY_NOW, REGION_CABLE,
                           REGION_TIER_MASK, REGION_TIERS, SLOT_HAS_DESCRIPTION,
                           SLOT_HAS_SHOW_GROUP, SLOT_JOINED_IN_PROGRESS, SLOT_PAY_PER_VIEW,
                           SLOT_UNNAMED, STARSIGHT_CRC_POLYNOMIAL, STARSIGHT_EPOCH,
                           STARSIGHT_HEADER, STARSIGHT_MAX_PACKET, STARSIGHT_MAX_SEGMENTED,
                           STARSIGHT_SYNC, STARSIGHT_TRAILER, STATION_ASSEMBLED, STATION_BUILT,
                           STATION_CLOCK_AT, STATION_DAILY, STATION_DAYS, STATION_DAY_MINUTES,
                           STATION_DONE, STATION_HELD, STATION_INVENTORY_STREAM,
                           STATION_LATER_BY, STATION_MINUTE_MAPS, STATION_MINUTES_USED,
                           STATION_MISSING, STATION_MISSING_BYTES, STATION_REMAINING,
                           STATION_SOFTWARE, STATION_STREAM, STATION_TABLE_COUNT,
                           STATION_TABLES, STATION_TOTAL, STATION_UNSET,
                           SUBSCRIBER_RESET_ACTIONS, TIME_NOT_A_PROVIDER)
from lib.starsight_table import STARSIGHT_HUFFMAN, STARSIGHT_HUFFMAN_MAX_BITS

HERE = os.path.dirname(os.path.abspath(__file__))
LOADER_TABLES = os.path.join(HERE, 'research', 'ssload-tables.json')

# Which command types this decoder turns into records, as against merely framing them.
DECODED_COMMANDS = frozenset((1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14, 17, 20, 21, 31, 32,
                              33, 34, 36, 37, 38, 39, 40, 42))

STATION_TABLE_TOTAL = len(STATION_TABLES) + STATION_DAYS

PACKET = [
    (0, 1, 'sync', 'Always 0x%02X.' % STARSIGHT_SYNC),
    (1, 2, 'size', 'The whole packet in bytes, this header and the trailer included, MSB '
                   'first. At most 0x%X.' % STARSIGHT_MAX_PACKET),
    (3, 4, 'time', 'The minute the packet was sent: minutes since %s GMT, MSB first.'
     % STARSIGHT_EPOCH.date()),
    (7, 2, 'stream', 'Stream id, naming the feed.'),
    (9, 2, 'header check', 'The low 16 bits of the CRC of bytes 0-8, least significant byte '
                           'first.'),
    (STARSIGHT_HEADER, None, 'commands', 'Concatenated commands, which fill it exactly.'),
    (None, STARSIGHT_TRAILER, 'body check',
     'The CRC of everything before it, least significant byte first, so that the CRC of the '
     'whole packet is zero.'),
]

CRC = [
    ('algorithm', 'Table driven reflected CRC-32'),
    ('polynomial', '0x%08X, the standard IEEE one' % STARSIGHT_CRC_POLYNOMIAL),
    ('initial value', '0xFFFFFFFF'),
    ('final inversion', 'none - the register is returned as it stands'),
    ('header check', 'crc(bytes 0..8) & 0xFFFF == bytes 9-10 read little endian'),
    ('body check', 'crc(the whole packet, its own trailer included) == 0'),
]

FLAGS = [
    ('Command byte 0', [
        (0x3F, 'type', 'The command type.'),
        (COMMAND_ENCRYPTED, 'encrypted',
         'The command is encrypted. An encrypted Show List keeps bytes 0-10 - version, '
         'channel, start and slot count - in the clear and enciphers its slots.'),
        (0x40, 'key', 'Which of two keys enciphered it.'),
    ]),
    ('Show Title byte 2', [
        (COMPRESSED_FLAG, 'compressed', 'The title is compressed.'),
        (ATTRIBUTE_CLOSED_CAPTIONED, 'closedCaptioned', 'The program is closed captioned.'),
        (ATTRIBUTE_STEREO, 'stereo', 'The program is in stereo.'),
        (ATTRIBUTE_BLACK_AND_WHITE, 'blackAndWhite', 'The program is in black and white.'),
        (0x0F, 'unused', 'Unused; no part of the show id.'),
    ]),
    ('Show Description byte 2', [
        (COMPRESSED_FLAG, 'compressed', 'The text is compressed.'),
        (ATTRIBUTE_CLOSED_CAPTIONED, 'closedCaptioned',
         'The program described is closed captioned, as its Show Title says.'),
        (ATTRIBUTE_STEREO, 'stereo', 'The program described is in stereo.'),
        (ATTRIBUTE_BLACK_AND_WHITE, 'blackAndWhite',
         'The program described is in black and white.'),
        (DESCRIPTION_EXTENDED, 'extended',
         'Rating, advisories and year precede the text, which then starts at byte 10.'),
    ]),
    ('Show List slot byte 0', [
        (SLOT_HAS_DESCRIPTION, 'hasDescription', 'Two more bytes follow: a description id.'),
        (SLOT_PAY_PER_VIEW, 'payPerView', 'Pay per view.'),
        (SLOT_HAS_SHOW_GROUP, 'hasShowGroup', 'Two more bytes follow: a show group.'),
        (SLOT_UNNAMED, 'unused', 'Unused.'),
        (SLOT_JOINED_IN_PROGRESS, 'joinedInProgress',
         "On a list's first slot: the program was already running when the list starts."),
    ]),
    ('Show Description byte 8, content advisories',
     [(mask, name, 'The program carries this advisory.') for mask, name in ADVISORY_NAMES]
     + [(mask, name, 'Unknown.') for mask, name in ADVISORY_UNKNOWN]),
    ('Time byte 6', [
        (0x0F, 'utcOffsetHours', "The station's standard offset from GMT, whole hours."),
        (0x10, 'offsetIsNegative', 'The offset is west of Greenwich.'),
        (TIME_NOT_A_PROVIDER, 'notAProvider',
         'This channel is not a StarSight provider: a receiver scanning for the guide passes '
         'it over.'),
        (0x80, 'daylightSaving',
         'Daylight saving is in force: the wall clock is an hour ahead of the offset.'),
    ]),
    ('Channel Data byte 5', [
        (CHANNEL_SHOWS_CALL_SIGN, 'showsCallSign',
         'A cable service, shown by its name rather than a channel number.'),
    ]),
    ('Channel Data byte 7', [
        (0xFF, 'short label',
         'Read from 0x80 down, one bit per character of bytes 8-15: the characters selected '
         'make a %d character label for a display too narrow for the whole name. HEADLINE '
         'with 0x9A gives HDLN, CINEMAX with 0x0E gives MAX. Zero for a station.'
         % CHANNEL_LABEL_WIDTH),
    ]),
    ('Region byte 5', [
        (REGION_CABLE, 'cable', "A cable system's lineup rather than an antenna's."),
        (REGION_APPLY_NOW, 'applyNow',
         "Use this lineup at once. Otherwise it waits until the stream's clock reaches its "
         'effective time.'),
    ]),
    ('Region lineup entry byte 3', [
        (REGION_TIER_MASK, 'tier', 'The tier a cable system sells the channel in: %s.'
         % ', '.join('%d %s' % pair for pair in enumerate(REGION_TIERS))),
        (0xC0, 'unknown', 'Unknown.'),
    ]),
    ('Subscriber Reset byte 2', [
        (SUBSCRIBER_RESET_ACTIONS[0][0], SUBSCRIBER_RESET_ACTIONS[0][1],
         'Clear the stored lineup, schedules and themes.'),
        (SUBSCRIBER_RESET_ACTIONS[1][0], SUBSCRIBER_RESET_ACTIONS[1][1],
         'Clear them, and stop loading the guide until restarted.'),
    ]),
]

STATION_LAYOUT = [
    ('0', 'type'),
    ('1-2', 'length: growing in layout 1; 721 to 743 bytes in layouts 3 and 4'),
    ('3', 'layout: 1, 3 or 4. Layout 1 places its fields after byte 20 differently'),
    ('%d-%d' % (STATION_SOFTWARE, STATION_SOFTWARE + 1),
     'in layouts 3 and 4, a software release: 200, 403 and 405 are known'),
    ('%d-%d' % (STATION_STREAM, STATION_STREAM + 1), 'the stream id the packet headers carry'),
    ('%d-%d' % (STATION_BUILT, STATION_BUILT + 3), 'a Unix time, constant over a session'),
    ('%d-%d' % (STATION_ASSEMBLED, STATION_ASSEMBLED + 3),
     "when the block's contents were assembled, Unix seconds; unset in layout 4"),
    ('20-23', 'a Unix time, or 0x%08X - 1980-01-01, the zero of a DOS clock - where nothing '
              'set it, as every unset time in the block is' % STATION_UNSET),
    (', '.join('%d (layout %d)' % (at, layout) for layout, at in sorted(STATION_CLOCK_AT.items())),
     'the station clock when the block was sent, Unix seconds'),
    ('%d-%d, %d-%d, %d-%d (layout 1)' % (STATION_TOTAL, STATION_TOTAL + 1,
                                         STATION_DONE, STATION_DONE + 1,
                                         STATION_REMAINING, STATION_REMAINING + 1),
     'how far through its cycle the station is: a total, how many are done and how many '
     'remain, the last two adding up to the first'),
    ('66-67 (layout 1)',
     'minutes since the assembly time. The block grows by two bytes for every eight'),
    ('the rest of layout 1', 'zero'),
    ('%d-%d (layouts 3 and 4)' % (STATION_MINUTES_USED, STATION_MINUTES_USED + 1),
     'how many bits of each minute map are in use, %d to %d: it gains one a minute, and when '
     'it would pass %d both maps turn a byte to the left and it drops by eight, so the bit '
     'before it is the current minute'
     % (STATION_DAY_MINUTES - 7, STATION_DAY_MINUTES, STATION_DAY_MINUTES)),
    (', '.join('%d-%d' % (at, at + STATION_DAY_MINUTES // 8 - 1) for at in STATION_MINUTE_MAPS),
     'two maps of the last day, a bit a minute, the first bit the most significant. What a '
     'set bit means is unknown'),
    ('420-429 (layout 4)',
     'an unset time and six zero bytes, which layout 4 inserts, so everything after them sits '
     '%d bytes later than in layout 3' % STATION_LATER_BY[4]),
    ('%d-%d' % (STATION_HELD, STATION_HELD + STATION_TABLE_TOTAL * 2 - 1),
     "the station's inventory of its own guide: how many it holds of each of %d tables - "
     '%s, then the schedules of %d days from the GMT day of its clock, each counting '
     'channels' % (STATION_TABLE_TOTAL, ', '.join(STATION_TABLES), STATION_DAYS)),
    ('%d-%d' % (STATION_INVENTORY_STREAM, STATION_INVENTORY_STREAM + 1), 'the stream id again'),
    ('%d' % STATION_TABLE_COUNT, 'the number of tables, %d' % STATION_TABLE_TOTAL),
    ('%d-%d' % (STATION_MISSING, STATION_MISSING
                + STATION_TABLE_TOTAL * STATION_MISSING_BYTES - 1),
     'a %d byte record a table of what the station lacks: a count, then the lowest eight '
     'missing ids, ascending, zero where there are fewer' % STATION_MISSING_BYTES),
    ('%d-%d' % (STATION_DAILY, STATION_DAILY + 3),
     'from software 403 on, a time at the same local time every day, always the latest one '
     'before the clock'),
    ('the rest of layouts 3 and 4',
     'unknown: times at 20-27 and 420-431, a running counter at 30-33, constants at 28-29 and '
     '38-39, values at 40-47, and short counts at 715-732'),
]

LAYOUTS = [
    ('Time', 1, [('0', 'type'), ('1', 'length'), ('2-5', 'the time, to the minute'),
                 ('6', 'zone and daylight saving flags'), ('7', 'seconds')]),
    ('Daylight Saving Change', 2, [('0', 'type'), ('1', 'length'),
                                   ('2-5', 'daylight saving starts'),
                                   ('6-9', 'daylight saving ends')]),
    ('Region', 3, [('0', 'type'), ('1-2', 'length'), ('3-4', 'region id'), ('5', 'flags'),
                   ('6-9', 'the time the lineup takes effect'),
                   ('10', 'channel count, so a lineup holds at most 255'),
                   ('11.. +0', 'bit 7: channel number bit 8; bits 0-6: channel id high 7'),
                   ('11.. +1', 'channel id low 8 (15 bits with the byte before)'),
                   ('11.. +2', 'channel number low 8 (9 bits with the byte before): the '
                               'number the channel sits on in this region'),
                   ('11.. +3', 'the tier, and two bits of unknown meaning')]),
    ('Channel Data', 4, [
        ('0', 'type'), ('1', 'length, 16'), ('2', 'unknown'),
        ('3', 'bit 7: channel number bit 8; bits 0-6: channel id high 7'),
        ('4', 'channel id low 8 (15 bits with byte 3)'),
        ('5', 'flags'),
        ('6', 'channel number low 8 (9 bits with byte 3): the channel a station broadcasts on, '
              'zero for a cable service'),
        ('7', 'which of bytes 8-15 make the short label'),
        ('8-15', "a station's CALL-NET - call letters, a dash, the network - or a cable "
                 "service's name, which can carry a dash of its own: SCI-FI, CNN-HLN, "
                 'DSC-KIDS')]),
    ('Show List', 5, [
        ('0', 'type and the encryption bits; an encrypted list keeps bytes 0-10 in the clear'),
        ('1-2', 'length'),
        ('3', 'low nibble: a version. A receiver skips a list for a channel and day it already '
              'holds at it, and a revised day comes with a higher one'),
        ('4-5', 'channel id, 15 bits (0x%04X)' % CHANNEL_ID_MASK),
        ('6-9', 'start of the first slot, a GMT midnight'),
        ('10', 'slot count'),
        ('11..', 'slots: flags, minutes, show id, then the optional fields')]),
    ('Show Title', 6, [('0', 'type'), ('1', 'length'), ('2', 'flags'), ('3-4', 'show id'),
                       ('5-6', 'theme id'), ('7..', 'title')]),
    ('Show Description', 8, [
        ('0', 'type'), ('1', 'length'), ('2', 'flags'), ('3-4', 'description id'),
        ('7', "critic's stars (bits 7-5), film rating code (bits 4-1), extended form only. "
              'The codes are %s' % ', '.join('%d %s' % (code, name)
                                             for code, name in enumerate(RATING_NAMES))),
        ('8', 'content advisories, extended form only'),
        ('9', 'year, last two digits, zero for none; extended form only'),
        ('7.. or 10..', 'text, after the extended bytes when present')]),
    ('Region Name', 9, [('0', 'type'), ('1', 'length'), ('2-3', 'region id'),
                        ('4..', "the name, always compressed: 'columbus, oh', 'akron - time "
                                "warner cable'")]),
    ('Cycle Start', 10, [('0', 'type'), ('1', 'length, 9'),
                         ('2-8', 'unexplained. The command marks the start of a carousel '
                                 'cycle; a receiver scanning for the guide counts them')]),
    ('Theme Category', 11, [
        ('0', 'type'), ('1-2', 'length'),
        ('3', 'version; a change means every name is to be reloaded'),
        ('4', 'entry count'),
        ('5..', 'entries of: id, flags, length, then a NUL terminated name: 1 Movies to 11 '
                "Miscellaneous. The flag byte's meaning is unknown")]),
    ('Theme Sub-Category', 12, [
        ('0', 'type'), ('1-2', 'length'), ('3', 'the Theme Category id these belong to'),
        ('4', 'entry count, low 7 bits'),
        ('5..', 'entries of: their own length, flags, a count of theme ids, the ids, then the '
                "name. They are the receiver's search menus: each category opens with an 'All' "
                "list and closes with a 'Closed Captioned' one, and one theme sits in as many "
                "as fit it. The flag byte's meaning is unknown")]),
    ('Subscriber Reset', 13, [('0', 'type'), ('1', 'length'), ('2', 'what to discard'),
                              ('3-7', 'the receiver it is for; zero for every receiver')]),
    ('Authorization', 14, [('0', 'type'), ('1', 'length'), ('2-5', 'the receiver it is for'),
                           ('6..', 'opaque')]),
    ('Key Distribution', 17, [('0', 'type'), ('1', 'length'),
                              ('2-5', 'the receiver it is for; sent in rising order'),
                              ('6..', 'opaque')]),
    ('Sequence Number', 20, [('0', 'type'), ('1', 'length'),
                             ('2-5', 'a 32 bit counter, one per packet')]),
    ('Station Node Status', 21, STATION_LAYOUT),
    ('Postal Code', 31, [('0', 'type'), ('1-2', 'length'),
                         ('3-8', 'the postal code as text; codes are sent in sort order'),
                         ('9', 'unknown'), ('10', 'antenna region count'),
                         ('11', 'cable region count'),
                         ('12..', 'that many two byte region ids, then the cable ones'),
                         ('then', 'the news area its pages are filed under, then a byte of '
                                  'unknown meaning')]),
    ('News Service', 32, [('0', 'type'), ('1-2', 'length'), ('3-6', 'unknown'),
                          ('7', 'name length'),
                          ('8..', "the name, usually in the clear: 'StarSight NEWS Services'")]),
    ('News Menu', 33, [('0', 'type'), ('1-2', 'length'), ('3', 'unknown'),
                       ('4', 'section count'),
                       ('5..', 'sections of: number, five unknown bytes, name length, the name '
                               'compressed')]),
    ('News Page', 34, [('0', 'type'), ('1-2', 'length'),
                       ('3', 'the menu section it is under'), ('4', 'unknown'),
                       ('5-6', 'page number'), ('7', 'flags'), ('8-9', 'unknown'),
                       ('10', 'the news area of the postal codes it is for; zero for every area'),
                       ('11-14', 'when it was written'),
                       ('15', 'unknown'), ('16', 'title length'),
                       ('17..', 'the title, compressed'),
                       ('then', 'text length, sixteen bits; 0x%04X set when the text is in '
                                'the clear' % NEWS_PLAIN),
                       ('then', "the text: compressed, it breaks lines with the 0x1F phrase, "
                                "'ur' everywhere else; in the clear, with CR. `i, `a and `d "
                                'set text apart up to `e or the end of the line')]),
    ('Segmented Command', 36, [('0', 'type'), ('1-2', 'length'),
                               ('3', 'high nibble: this part; low nibble: the last part'),
                               ('4', 'which message the parts belong to'),
                               ('5..', 'that part of a command. Joined in order, the parts '
                                       'end with a CRC-32 over the whole, at most %d bytes'
                                       % STARSIGHT_MAX_SEGMENTED)]),
    ('type 37', 37, [('0', 'type'), ('1-2', 'length'),
                     ('3', 'low nibble: a version; a day already held at it is skipped'),
                     ('4-5', 'region count'),
                     ('6..', 'that many region ids: the regions it is for'),
                     ('after them', 'a midnight, one of the next seven'),
                     ('then', 'an entry count, and that many entries of a byte and a 16 bit '
                              'value')]),
    ('type 38', 38, [('0', 'type'), ('1-2', 'length'), ('3-4', 'an id'), ('5', 'a byte'),
                     ('6', 'flags'), ('7', 'low 3 bits: first length; upper bits: more flags'),
                     ('8', 'second length'), ('9', 'third length'),
                     ('10-11', 'a 16 bit value'),
                     ('12..', 'the three payloads, one after another')]),
    ('type 39', 39, [('0', 'type'), ('1-2', 'length'), ('3-4', 'an id'),
                     ('5-6', 'a 16 bit value'), ('7-10', 'a 32 bit value'), ('11', 'a byte')]),
    ('type 40', 40, [('0', 'type'), ('1-2', 'length'), ('3-4', 'an id'), ('5', 'a byte'),
                     ('6..', 'payload, to the length in bytes 1-2')]),
    ('type 42', 42, [('0', 'type'), ('1-2', 'length'),
                     ('8', 'bits 4-5: 0 stops a receiver acting on Region, Channel Data, Show '
                           'List, the theme commands and types 36 and 37; 1 lets it act on '
                           'them again; 2 and 3 do nothing'),
                     ('3-25', 'otherwise unexplained')]),
]


def loader_table():
    """ The loader's command descriptor table, read back from the extracted JSON, or {} """
    try:
        with open(LOADER_TABLES) as handle:
            return {entry['type']: entry for entry in json.load(handle)['ssload']['commandTable']}
    except (IOError, KeyError, ValueError):
        return {}


def tables():
    """ Every table, as one document """
    return {
        'packet': [{'offset': offset, 'bytes': size, 'field': field, 'note': note}
                   for offset, size, field, note in PACKET],
        'checksums': [{'property': name, 'value': value} for name, value in CRC],
        'commands': [{'type': command_type, 'name': COMMAND_NAMES.get(command_type),
                      'lengthBytes': width, 'headerBytes': header,
                      'decoded': command_type in DECODED_COMMANDS}
                     for command_type, (width, header, _) in sorted(COMMAND_TABLE.items())],
        'commandLayouts': [{'name': name, 'type': command_type,
                            'bytes': [{'at': at, 'field': field} for at, field in rows]}
                           for name, command_type, rows in sorted(LAYOUTS,
                                                                  key=lambda l: l[1])],
        'flags': [{'byte': name, 'bits': [{'mask': mask, 'field': field, 'note': note}
                                          for mask, field, note in bits]}
                  for name, bits in FLAGS],
        'compression': {
            'symbols': len(STARSIGHT_HUFFMAN),
            'maxCodeBits': STARSIGHT_HUFFMAN_MAX_BITS,
            'table': [{'code': code, 'symbol': STARSIGHT_HUFFMAN[code]}
                      for code in sorted(STARSIGHT_HUFFMAN, key=lambda c: (len(c), c))],
        },
    }


STYLE = """
:root{color-scheme:light dark;
 --ink:#17181c;--ground:#fbfbf9;--rule:#d6d5cf;--zebra:#f5f4f0;--head:#efeee8;--dim:#5b5f68}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){
 --ink:#e6e6e3;--ground:#16171a;--rule:#35383f;--zebra:#1d1f23;--head:#24262b;--dim:#a8abb3}}
:root[data-theme="dark"]{
 --ink:#e6e6e3;--ground:#16171a;--rule:#35383f;--zebra:#1d1f23;--head:#24262b;--dim:#a8abb3}
*{box-sizing:border-box}
body{font:14px/1.5 ui-monospace,Menlo,Consolas,monospace;margin:0;padding-block:2rem;
 padding-inline:16px;color:var(--ink);background:var(--ground)}
main{max-width:64rem;margin:0 auto}
h1{font-size:1.5rem;margin:0 0 .25rem;text-wrap:balance}
h2{font-size:1.1rem;margin:2.5rem 0 .25rem;text-wrap:balance}
h3{font-size:.95rem;margin:1.5rem 0 .25rem;font-weight:600}
p{margin:.25rem 0 1rem;max-width:44rem;color:var(--dim)}
nav p{margin:0}
table{border-collapse:collapse;margin:0 0 1rem;width:100%;display:block;overflow-x:auto}
th,td{border:1px solid var(--rule);padding:.25rem .5rem;text-align:left;
 vertical-align:top;white-space:normal}
th{background:var(--head);font-weight:600;white-space:nowrap}
td.num{text-align:right;font-variant-numeric:tabular-nums}
tr:nth-child(even) td{background:var(--zebra)}
code{background:var(--head);padding:0 .2em}
a{color:inherit}
"""


def _rows(out, columns, rows):
    """ One table """
    out.append('<table><thead><tr>%s</tr></thead><tbody>'
               % ''.join('<th>%s</th>' % html.escape(c) for c in columns))
    for row in rows:
        out.append('<tr>%s</tr>' % ''.join(
            '<td%s>%s</td>' % (' class="num"' if isinstance(value, int)
                               and not isinstance(value, bool) else '',
                               html.escape('' if value is None else str(value)))
            for value in row))
    out.append('</tbody></table>')


def _anchor(name):
    return name.lower().replace(' ', '-')


def as_html(doc):
    out = ["<!doctype html><html lang='en'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width,initial-scale=1'>",
           "<title>StarSight protocol</title>",
           "<style>%s</style></head><body><main>" % STYLE,
           "<h1>The StarSight protocol</h1>",
           "<p>The byte-level reference for the StarSight Data Transmission Network, carried "
           "on a line of the NTSC vertical blanking interval, generated from "
           "<code>lib/starsight.py</code>. <code>docs/starsight.md</code> explains it, and "
           "<code>docs/research/starsight-sources.md</code> gives its sources.</p>",
           "<nav><p><a href='#packet'>Packet</a> &middot; <a href='#checksums'>Checksums</a> "
           "&middot; <a href='#commands'>Commands</a> &middot; "
           "<a href='#command-layouts'>Command layouts</a> &middot; "
           "<a href='#flag-bits'>Flag bits</a> &middot; "
           "<a href='#compression'>Compression</a></p></nav>"]

    out.append("<h2 id='packet'>Packet</h2>")
    out.append("<p>Byte offsets from the sync. Every number is big endian except the two "
               "checks. Times are minutes since 1992-01-01 00:00 GMT.</p>")
    _rows(out, ('Offset', 'Bytes', 'Field', 'Note'),
          [(p['offset'], p['bytes'], p['field'], p['note']) for p in doc['packet']])

    out.append("<h2 id='checksums'>Checksums</h2>")
    out.append("<p>Both checks use one CRC: zlib's <code>crc32</code> with its result "
               "inverted. The missing final inversion is what makes the body check come out "
               "zero over the whole packet.</p>")
    _rows(out, ('Property', 'Value'),
          [(c['property'], c['value']) for c in doc['checksums']])

    out.append("<h2 id='commands'>Commands</h2>")
    out.append("<p>Indexed by <code>byte 0 &amp; 0x3F</code>. <b>Length</b> is how many bytes "
               "the length field after the type takes, and <b>header</b> how many bytes come "
               "before a command's variable part. <b>Decoded</b> says whether "
               "<code>cc_decoder</code> turns the type into records.</p>")
    _rows(out, ('Type', 'Name', 'Length', 'Header', 'Decoded'),
          [(c['type'], c['name'] or '', c['lengthBytes'], c['headerBytes'],
            'yes' if c['decoded'] else 'no') for c in doc['commands']])

    out.append("<h2 id='command-layouts'>Command layouts</h2>")
    out.append("<p>Every type whose layout is known, by type. Offsets count from byte 0, the "
               "type.</p>")
    for layout in doc['commandLayouts']:
        out.append("<h3 id='%s'>%s &mdash; type %d</h3>" % (
            _anchor(layout['name']), html.escape(layout['name']), layout['type']))
        _rows(out, ('Byte', 'Field'), [(b['at'], b['field']) for b in layout['bytes']])

    out.append("<h2 id='flag-bits'>Flag bits</h2>")
    for group in doc['flags']:
        out.append("<h3>%s</h3>" % html.escape(group['byte']))
        _rows(out, ('Mask', 'Field', 'Note'),
              [('0x%02X' % b['mask'], b['field'], b['note']) for b in group['bits']])

    out.append("<h2 id='compression'>Compression</h2>")
    out.append("<p>A Huffman code of %d symbols, codes up to %d bits, Kraft sum exactly 1: "
               "0x00 ends a string, 0x01-0x1F expand to whole words and fragments, and "
               "0x20-0x7E are the printable characters. Theme names are never compressed and "
               "region names always are. A News Page's text breaks its lines with 0x1F, which "
               "everywhere else expands to 'ur'.</p>"
               % (doc['compression']['symbols'], doc['compression']['maxCodeBits']))
    _rows(out, ('Code', 'Bits', 'Symbol'),
          [(e['code'], len(e['code']), repr(e['symbol'])[1:-1])
           for e in doc['compression']['table']])

    out.append("</main></body></html>")
    return '\n'.join(out)


def main():
    # the published command table must still be what the loader's own table says, where the
    # extracted tables are at hand
    loader = loader_table()
    if not loader:
        print('%s not found: the command table is not checked against the loader'
              % LOADER_TABLES, file=sys.stderr)
    disagreements = [command_type for command_type, (_, _, handler) in COMMAND_TABLE.items()
                     if command_type in loader and loader[command_type].get('loader') != handler]
    if disagreements:
        raise SystemExit('command table disagrees with SSLOAD.DLL on types %s'
                         % ', '.join(str(t) for t in sorted(disagreements)))

    doc = tables()
    with open(os.path.join(HERE, 'starsight-protocol.json'), 'w') as f:
        json.dump(doc, f, indent=1)
        f.write('\n')
    with open(os.path.join(HERE, 'starsight-protocol.html'), 'w') as f:
        f.write(as_html(doc))
    print('wrote docs/starsight-protocol.json and docs/starsight-protocol.html')


if __name__ == '__main__':
    main()
