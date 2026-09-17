#!/usr/bin/env python3
""" Write the StarSight protocol tables as JSON and as a page

Everything the format is known to say, in one place: the command table, the meaning of
every flag bit, the byte layout of each command this decodes, and the 127 symbol
compression table. The command widths and handler column come from SSLOAD.DLL, the
Microsoft StarSight Guide Data Loader on the Windows 98 SE CD; everything else is read
back out of `lib.starsight` so the tables cannot drift from the decoder.

    python3 docs/starsight_tables.py            # writes docs/starsight-protocol.{json,html}

Public domain / Unlicense, as with the rest of ccDecoder.
"""

import html
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

from lib.starsight import (ADVISORY_NAMES, ATTRIBUTE_BLACK_AND_WHITE,
                           ATTRIBUTE_CLOSED_CAPTIONED, ATTRIBUTE_STEREO,
                           CHANNEL_LABEL_WIDTH, REGION_APPLY_NOW, CHANNEL_ID_MASK,
                           CHANNEL_SHOWS_CALL_SIGN, COMMANDS_SEEN, COMMAND_NAMES,
                           COMMAND_TABLE, COMPRESSED_FLAG, DESCRIPTION_EXTENDED, LOADER,
                           MEASURED, PATENT, PROVENANCE_NOTES, SLOT_HAS_DESCRIPTION,
                           SLOT_HAS_SHOW_GROUP, SLOT_JOINED_IN_PROGRESS, SLOT_PAY_PER_VIEW,
                           SLOT_UNNAMED, STARSIGHT_CRC_POLYNOMIAL, STARSIGHT_EPOCH,
                           STARSIGHT_HEADER, STARSIGHT_MAX_PACKET, STARSIGHT_SYNC,
                           STARSIGHT_TRAILER, STATION_ASSEMBLED, STATION_BUILT,
                           STATION_TOTAL, STATION_DONE, STATION_REMAINING,
                           SUBSCRIBER_RESET_ACTIONS)
from lib.starsight_table import STARSIGHT_HUFFMAN, STARSIGHT_HUFFMAN_MAX_BITS

HERE = os.path.dirname(os.path.abspath(__file__))
LOADER_TABLES = os.path.join(HERE, 'ssload-tables.json')

# Which command types this decoder turns into records, as against merely framing them.
DECODED_COMMANDS = frozenset((1, 2, 3, 4, 5, 6, 8, 11, 12, 13, 20, 21))

M, L, P = MEASURED, LOADER, PATENT

PACKET = [
    (0, 1, 'sync', 'Always 0x%02X.' % STARSIGHT_SYNC, M),
    (1, 2, 'size', 'Whole packet in bytes, this header and the trailer included, MSB '
                   'first. The loader rejects anything above 0x%X.' % STARSIGHT_MAX_PACKET, M),
    (3, 4, 'time', 'Minutes since %s GMT, MSB first.' % STARSIGHT_EPOCH.date(), M),
    (7, 2, 'stream', 'VBI stream id.', M),
    (9, 2, 'header checksum',
     'CRC-32 of bytes 0-8, low 16 bits, stored LITTLE endian - the only number in the '
     'format that way round.', M),
    (STARSIGHT_HEADER, None, 'message', 'Concatenated commands, which tile it exactly.', M),
    (None, STARSIGHT_TRAILER, 'body checksum',
     'CRC-32 of everything before it. Running the CRC over the whole packet, this field '
     'included, gives zero.', M),
]

# Both checksums come from one routine in SSLOAD.DLL at image offset 0xF1F0, duplicated
# verbatim at 0xF240, with its table at 0x149F8.
CRC = [
    ('algorithm', 'Table driven reflected CRC-32', M),
    ('polynomial', '0x%08X, the standard IEEE one' % STARSIGHT_CRC_POLYNOMIAL, M),
    ('initial value', '0xFFFFFFFF', M),
    ('final inversion', 'none - the register is returned as it stands', M),
    ('header check', 'crc32(bytes 0..8) & 0xFFFF == bytes 9-10 read little endian', M),
    ('body check', 'crc32(the whole packet, its own trailer included) == 0', M),
]

# What the body checksum can be made to do beyond saying yes or no. A CRC is linear over
# GF(2), so the thirty-two bits of it are thirty-two equations in the packet's bits: where
# the damage is in a known place the missing bits are solved for, and where it is not the
# position has to be searched too, which is what costs the budget. Each row is the number
# of bits of checksum still unspent once the damage is accounted for, which is the odds
# against a wrong answer being accepted.
RECOVERY = [
    ('nothing', '0', '1', '32', 'passes as it stands'),
    ('bits the slicer doubted, 2 rising to 20', '2 - 20', '1', '30 - 12', 'solved as erasures'),
    ('one lost VBI line', '16', '1', '16', 'solved as erasures'),
    ('one flipped bit the slicer was sure of', '1', '2^10.6', '21', 'searched'),
    ('two flipped bits', '2', '2^20.3', '12', 'searched'),
    ('two lost VBI lines', '32', '1', '0', 'refused'),
    ('damage the confidence did not point at', '-', '-', '-', 'refused'),
]

FLAGS = [
    ('Command byte 0', [
        (0x3F, 'type', 'The command type; the loader dispatches on exactly these six bits.', M),
        (0xC0, 'enc_flg | key_id', 'Encryption flag and key id. Zero in every capture.', P),
    ]),
    ('Show Title byte 2', [
        (COMPRESSED_FLAG, 'compressed', 'The title is Huffman coded.', M),
        (ATTRIBUTE_CLOSED_CAPTIONED, 'closedCaptioned', "Loader column 'TS Closed Caption'.", M),
        (ATTRIBUTE_STEREO, 'stereo', "Loader column 'TS Stereo'.", M),
        (ATTRIBUTE_BLACK_AND_WHITE, 'blackAndWhite', "Loader broadcast property 'B/W'.", M),
        (0x0F, 'unused', 'Zero on all 11,161 titles; no part of the show id.', M),
    ]),
    ('Show Description byte 2', [
        (COMPRESSED_FLAG, 'compressed', 'The text is Huffman coded.', M),
        (DESCRIPTION_EXTENDED, 'extended',
         'Rating, advisories and year precede the text, which then starts at byte 10.', M),
    ]),
    ('Show List slot byte 0', [
        (SLOT_HAS_DESCRIPTION, 'hasDescription', 'Two more bytes: a description id.', M),
        (SLOT_PAY_PER_VIEW, 'payPerView', "Reaches the database as 'TS Pay Per View'.", M),
        (SLOT_HAS_SHOW_GROUP, 'hasShowGroup', 'Two more bytes: a show group.', M),
        (SLOT_UNNAMED, 'unnamed',
         'Bits 1-4. The loader reads none of them and they are set on two slots in 43,430, '
         'both on commands with other damage.', M),
        (SLOT_JOINED_IN_PROGRESS, 'joinedInProgress',
         'On a list\'s first slot, a programme already running.', M),
    ]),
    ('Show Description byte 8, content advisories',
     [(mask, name, 'Set when the programme carries this advisory.', M)
      for mask, name in ADVISORY_NAMES]),
    ('Time byte 6', [
        (0x0F, 'utcOffsetHours', "The station's standard offset from GMT, whole hours.", M),
        (0x10, 'offsetIsNegative', 'Makes the offset west of Greenwich.', M),
        (0x80, 'daylightSaving', 'The wall clock is an hour ahead of the offset.', M),
    ]),
    ('Channel Data byte 5', [
        (CHANNEL_SHOWS_CALL_SIGN, 'showsCallSign',
         'Display the call sign rather than the channel number.', L),
    ]),
    ('Channel Data byte 7', [
        (0xFF, 'short label',
         'Read from 0x80 down, one bit per character at 8-15; the selected characters make '
         'a %d character label for a display too narrow for the whole call sign.'
         % CHANNEL_LABEL_WIDTH, L),
    ]),
    ('Region byte 5', [
        (REGION_APPLY_NOW, 'applyNow',
         'Use this lineup at once. Otherwise the loader holds the command and replays it '
         'when the stream clock reaches the effective time.', L),
    ]),
    ('Subscriber Reset byte 2',
     [(mask, name, 'The loader calls a different routine for each bit.', L)
      for mask, name in SUBSCRIBER_RESET_ACTIONS]),
]

LAYOUTS = [
    ('Time', 1, M, [('0', 'type', M), ('1', 'length', M),
                    ('2-5', 'minute count, MSB first', M),
                    ('6', 'zone and daylight saving flags', M), ('7', 'seconds', M)]),
    ('Daylight Saving Change', 2, M, [('0', 'type', M), ('1', 'length', M),
                                      ('2-5', 'daylight saving starts', M),
                                      ('6-9', 'daylight saving ends', M)]),
    ('Region', 3, L, [('0', 'type', L), ('1-2', 'length', L), ('3-4', 'region id', L),
                      ('5', 'flags', L),
                      ('6-9', 'effective from, same epoch as Time', L),
                      ('10', 'channel count, so a lineup is at most 255', L),
                      ('11.. +0', 'bit 7: channel number bit 8; bits 0-6: channel id high 7', L),
                      ('11.. +1', 'channel id low 8 (15 bits with the byte before)', L),
                      ('11.. +2', 'channel number low 8 (9 bits with the byte before)', L),
                      ('11.. +3', 'never read by the loader', L)]),
    ('Channel Data', 4, L, [
        ('0', 'type', L), ('1', 'length', L),
        ('3', 'bit 7: channel number bit 8; bits 0-6: channel id high 7', L),
        ('4', 'channel id low 8 (15 bits with byte 3)', L),
        ('5', 'flags', L), ('6', 'channel number low 8 (9 bits with byte 3)', L),
        ('7', 'which of bytes 8-15 make the short label', L),
        ('8-15', 'one string, CALL-NET: call letters, a dash, the network', L)]),
    ('type 9', 9, L, [('0', 'type', L), ('1', 'length', L), ('2-3', 'an id', L),
                      ('4..', 'payload, to the length in byte 1', L)]),
    ('type 10', 10, L, [('0-8', 'nine bytes the loader passes to the host application '
                                'without reading them', L)]),
    ('type 31', 31, L, [('0', 'type', L), ('1-2', 'length', L),
                        ('3-8', 'a six byte key; the loader ignores the command unless it '
                                'matches the one it holds', L),
                        ('10', 'first table entry count', L),
                        ('11', 'second table entry count', L),
                        ('12..', 'that many two byte entries, then the second table, then '
                                 'two more bytes', L)]),
    ('type 36', 36, L, [('0', 'type', L), ('1-2', 'length', L),
                        ('3', 'high nibble: which part this is', L),
                        ('4', 'which message the parts belong to', L),
                        ('5..', 'that part of it', L)]),
    ('type 37', 37, L, [('0', 'type', L), ('1-2', 'length', L), ('3', 'low nibble, a field', L),
                        ('4-5', 'address count', L),
                        ('6..', 'that many two byte addresses; the loader reads no further '
                                'unless its own is among them', L),
                        ('after them', 'the payload for the receivers named', L)]),
    ('type 38', 38, L, [('0', 'type', L), ('1-2', 'length', L), ('3-4', 'an id', L),
                        ('5', 'a byte the loader keeps', L), ('6', 'flags', L),
                        ('7', 'low 3 bits: first length; upper bits: more flags', L),
                        ('8', 'second length', L), ('9', 'third length', L),
                        ('10-11', 'a second 16 bit value', L),
                        ('12..', 'the three payloads, one after another', L)]),
    ('type 39', 39, L, [('0', 'type', L), ('1-2', 'length', L), ('3-4', 'an id', L),
                        ('5-6', 'a 16 bit value', L), ('7-10', 'a 32 bit value', L),
                        ('11', 'a byte', L)]),
    ('type 40', 40, L, [('0', 'type', L), ('1-2', 'length', L), ('3-4', 'an id', L),
                        ('5', 'a byte the loader keeps', L),
                        ('6..', 'payload, to the length in bytes 1-2', L)]),
    ('type 42', 42, L, [('0', 'type', L), ('1-2', 'length', L),
                        ('8', 'bits 4-5: set a flag, clear it, or neither', L)]),
    ('Show List', 5, M, [
        ('0', 'type', M), ('1-2', 'length', M), ('3', 'low nibble, meaning unknown', M),
        ('4-5', 'channel id, 15 bits (0x%04X)' % CHANNEL_ID_MASK, M),
        ('6-9', 'start of the first slot', M), ('10', 'slot count', M),
        ('11..', 'slots: flags, minutes, show id, then the optional fields', M)]),
    ('Show Title', 6, M, [('0', 'type', M), ('1', 'length', M), ('2', 'flags', M),
                          ('3-4', 'show id', M), ('5-6', 'theme id', M),
                          ('7..', 'title', M)]),
    ('Show Description', 8, M, [
        ('0', 'type', M), ('1', 'length', M), ('2', 'flags', M), ('3-4', 'description id', M),
        ('7', 'rating system (bits 7-5), rating (bits 4-1), extended form only', M),
        ('8', 'content advisories, extended form only', M),
        ('9', 'year, last two digits, extended form only', M),
        ('7.. or 10..', 'text, after the extended bytes when present', M)]),
    ('type 9', 9, L, [('0', 'type', L), ('1', 'length', L), ('2-3', 'a 16 bit id', L),
                      ('4..', 'payload of length - 4 bytes', L)]),
    ('type 10', 10, L, [('0', 'type', L), ('1', 'length', L),
                        ('2..', 'nine bytes handed to a registered callback', L)]),
    ('Theme Category', 11, L, [
        ('0', 'type', L), ('1-2', 'length', L),
        ('3', 'version; a change makes the loader reload every name', L),
        ('4', 'entry count', L),
        ('5..', 'entries of: id, flags, length, then a NUL terminated name', L)]),
    ('Theme Sub-Category', 12, L, [
        ('0', 'type', L), ('1-2', 'length', L), ('3', 'version', L),
        ('4', 'entry count, low 7 bits', L), ('5..', 'entries, as Theme Category', L)]),
    ('Subscriber Reset', 13, L, [('0', 'type', L), ('1', 'length', L),
                                 ('2', 'which state to discard', L), ('3..', 'payload', L)]),
    ('Sequence Number', 20, M, [('0', 'type', M), ('1', 'length', M),
                                ('2-5', 'a 32 bit counter, one per packet', M)]),
    ('Station Node Status', 21, M, [
        ('0', 'type', M), ('1-2', 'length, always 733', M),
        ('3', 'which layout this is. The 1994 KCET capture sends 1 and the 1998 KET '
              'tapes send 3, and the fields after byte 20 sit in different places in each', M),
        ('4-5', 'a constant, 500 on KCET and 405 on KET', M),
        ('6-7', 'a constant, 5 on KCET and 52 on KET', M),
        ('%d-%d' % (STATION_BUILT, STATION_BUILT + 3),
         'a Unix time, constant within a capture and earlier than the one after it', M),
        ('%d-%d' % (STATION_ASSEMBLED, STATION_ASSEMBLED + 3),
         'when the block was assembled, Unix seconds; constant within a capture', M),
        ('20-23', '0x12CEA600 in every instance of both stations, four years and a '
                  'continent apart. Unexplained, and the only value in the block that is', M),
        ('varies',
         'the station clock at the moment of sending, Unix seconds. Not at a fixed offset '
         '- 48 in layout 3 and 58 in layout 1 - so it is found as the one field past the '
         'assembly time that reads as a time shortly after it', M),
        ('%d-%d, %d-%d, %d-%d (layout 1)' % (STATION_TOTAL, STATION_TOTAL + 1,
                                             STATION_DONE, STATION_DONE + 1,
                                             STATION_REMAINING, STATION_REMAINING + 1),
         'how far through its cycle the station is: a total, how many are done and how '
         'many are left. They hold to the byte across all twelve instances of the 1994 '
         'capture - 487 in all, 33 more done every ten minutes - and no three fields '
         'anywhere in layout 3 hold the same identity, so they are read only when they '
         'agree', M),
        ('66-67 (layout 1)',
         'the minutes between the assembly time and the clock. Exact on all twelve, and '
         'so carries nothing the other two fields do not', M),
        ('the rest of layout 1',
         'zero. The block grows two bytes every instance and the added bytes are padding, '
         'so something is counted that this capture never fills in', M),
        ('the rest of layout 3',
         '669 bytes, two thirds of them zero and 93 to 95 per cent of them identical from '
         'one instance to the next ten minutes later, in place - aligning the two at any '
         'other offset fits worse, so nothing slides. What does change is 7 to 9 per cent '
         'of it, in short runs which are most often pairs two bytes apart, so what is '
         'being updated is 16 bit fields scattered through a table that is otherwise '
         'static. Six instances over two captures is not enough to say what they count', M),
    ]),
    ('type 31', 31, L, [('0', 'type', L), ('1-2', 'length', L), ('10', 'a count', L),
                        ('11', 'a second count', L),
                        ('12..', 'two arrays of 16 bit values, sized by those counts', L)]),
    ('type 36', 36, L, [('0', 'type', L), ('1-2', 'length', L), ('3', 'a byte', L),
                        ('4', 'entry count', L), ('5..', 'entries', L)]),
    ('type 37', 37, L, [('0', 'type', L), ('1-2', 'length', L), ('3', 'a byte', L),
                        ('4-5', 'entry count', L), ('6..', 'entries of 16 bit fields', L)]),
    ('type 38', 38, L, [('0', 'type', L), ('1-2', 'length', L),
                        ('7', 'a byte', L), ('8', 'a byte', L), ('9', 'a byte', L)]),
    ('type 39', 39, L, [('0', 'type', L), ('1-2', 'length', L),
                        ('5-10', 'a multi byte value assembled MSB first', L)]),
    ('type 40', 40, L, [('0', 'type', L), ('1-2', 'length', L), ('3-4', 'a 16 bit field', L),
                        ('5..', 'records of six bytes', L)]),
    ('type 42', 42, L, [('0', 'type', L), ('1-2', 'length', L), ('8', 'a byte', L)]),
]

def loader_table():
    """ SSLOAD.DLL's command descriptor table, read back from the extracted JSON

    `docs/ssload_extract.py` writes it; reading it here rather than retyping it means the
    published table cannot drift from the binary, and `main` asserts the two agree.
    """
    try:
        with open(LOADER_TABLES) as handle:
            return {entry['type']: entry for entry in json.load(handle)['ssload']['commandTable']}
    except (IOError, KeyError, ValueError):
        return {}


def tables():
    """ Every table, as one document """
    loader = loader_table()

    commands = []
    for command_type in sorted(COMMAND_TABLE):
        width, header, handler = COMMAND_TABLE[command_type]
        commands.append({
            'type': command_type,
            'name': COMMAND_NAMES.get(command_type),
            'lengthBytes': width,
            'headerBytes': header,
            'loader': handler,
            'decoded': command_type in DECODED_COMMANDS,
            'seen': command_type in COMMANDS_SEEN,
            'provenance': MEASURED if command_type in COMMANDS_SEEN else LOADER,
            'binary': loader.get(command_type, {}).get('loader'),
        })

    return {
        'provenance': PROVENANCE_NOTES,
        'packet': [{'offset': offset, 'bytes': size, 'field': field,
                    'note': note, 'provenance': source}
                   for offset, size, field, note, source in PACKET],
        'checksums': [{'property': name, 'value': value, 'provenance': source}
                      for name, value, source in CRC],
        'recovery': [{'damage': damage, 'unknownBits': unknown, 'candidates': candidates,
                      'checksumLeft': left, 'outcome': outcome}
                     for damage, unknown, candidates, left, outcome in RECOVERY],
        'commands': commands,
        'commandLayouts': [{'name': name, 'type': command_type, 'provenance': source,
                            'bytes': [{'at': at, 'field': field, 'provenance': field_source}
                                      for at, field, field_source in rows]}
                           for name, command_type, source, rows in LAYOUTS],
        'flags': [{'byte': name,
                   'bits': [{'mask': mask, 'field': field, 'note': note,
                             'provenance': source}
                            for mask, field, note, source in bits]}
                  for name, bits in FLAGS],
        'compression': {
            'symbols': len(STARSIGHT_HUFFMAN),
            'maxCodeBits': STARSIGHT_HUFFMAN_MAX_BITS,
            'table': [{'code': code, 'symbol': STARSIGHT_HUFFMAN[code]}
                      for code in sorted(STARSIGHT_HUFFMAN, key=lambda c: (len(c), c))],
        },
        'database': database_schema(),
    }


def database_schema():
    """ The Access schema the loader writes into, from the extracted JSON """
    try:
        with open(LOADER_TABLES) as handle:
            return json.load(handle).get('dbsets', {}).get('schema', [])
    except (IOError, ValueError):
        return []

STYLE = """
:root{color-scheme:light dark;
 --ink:#17181c;--ground:#fbfbf9;--rule:#d6d5cf;--zebra:#f5f4f0;--head:#efeee8;--dim:#5b5f68;
 --measured-bg:#d8ead6;--measured-ink:#1d4a19;
 --loader-bg:#e4e0f2;--loader-ink:#332a63;
 --patent-bg:#f2e6cf;--patent-ink:#5c4413}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){
 --ink:#e6e6e3;--ground:#16171a;--rule:#35383f;--zebra:#1d1f23;--head:#24262b;--dim:#a8abb3;
 --measured-bg:#1f3a1c;--measured-ink:#b7ddb2;
 --loader-bg:#2a2450;--loader-ink:#c6bef0;
 --patent-bg:#43331a;--patent-ink:#e6cf9f}}
:root[data-theme="dark"]{
 --ink:#e6e6e3;--ground:#16171a;--rule:#35383f;--zebra:#1d1f23;--head:#24262b;--dim:#a8abb3;
 --measured-bg:#1f3a1c;--measured-ink:#b7ddb2;
 --loader-bg:#2a2450;--loader-ink:#c6bef0;
 --patent-bg:#43331a;--patent-ink:#e6cf9f}
*{box-sizing:border-box}
body{font:14px/1.5 ui-monospace,Menlo,Consolas,monospace;margin:0;padding-block:2rem;
 padding-inline:16px;color:var(--ink);background:var(--ground)}
main{max-width:64rem;margin:0 auto}
h1{font-size:1.5rem;margin:0 0 .25rem;text-wrap:balance}
h2{font-size:1.1rem;margin:2.5rem 0 .25rem;text-wrap:balance}
h3{font-size:.95rem;margin:1.5rem 0 .25rem;font-weight:600}
p{margin:.25rem 0 1rem;max-width:44rem;color:var(--dim)}
table{border-collapse:collapse;margin:0 0 1rem;width:100%;display:block;overflow-x:auto}
th,td{border:1px solid var(--rule);padding:.25rem .5rem;text-align:left;
 vertical-align:top;white-space:normal}
th{background:var(--head);font-weight:600;white-space:nowrap}
td.num{text-align:right;font-variant-numeric:tabular-nums}
tr:nth-child(even) td{background:var(--zebra)}
code{background:var(--head);padding:0 .2em}
s{text-decoration:none;display:inline-block;padding:0 .45em;border-radius:3px;
 font-size:.85em;white-space:nowrap}
s.measured{background:var(--measured-bg);color:var(--measured-ink)}
s.loader{background:var(--loader-bg);color:var(--loader-ink)}
s.patent{background:var(--patent-bg);color:var(--patent-ink)}
"""


def _tag(source):
    """ A provenance chip """
    return '<s class="%s">%s</s>' % (source, html.escape(source))


def _rows(out, columns, rows):
    """ One table. A cell that is already markup is passed through; anything else escapes """
    out.append('<table><thead><tr>%s</tr></thead><tbody>'
               % ''.join('<th>%s</th>' % html.escape(c) for c in columns))
    for row in rows:
        cells = []
        for value in row:
            if isinstance(value, Markup):
                cells.append('<td>%s</td>' % value)
            else:
                numeric = isinstance(value, int) and not isinstance(value, bool)
                cells.append('<td%s>%s</td>' % (' class="num"' if numeric else '',
                                                html.escape('' if value is None else str(value))))
        out.append('<tr>%s</tr>' % ''.join(cells))
    out.append('</tbody></table>')


class Markup(str):
    """ A cell that is already HTML """


def as_html(doc):
    out = ["<!doctype html><html lang='en'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width,initial-scale=1'>",
           "<title>StarSight protocol</title>",
           "<style>%s</style></head><body><main>" % STYLE,
           "<h1>The StarSight protocol</h1>",
           "<p>Everything <code>cc_decoder</code> knows about the StarSight Data "
           "Transmission Network, carried on a line of the NTSC vertical blanking "
           "interval. This is the reference; <code>docs/starsight.md</code> is the account "
           "of how it was worked out.</p>"]

    out.append("<h2>How to read this</h2>")
    out.append("<p>Every row says where it came from. Three sources, in the order they are "
               "trusted.</p>")
    _rows(out, ('Source', 'What it means'),
          [(Markup(_tag(key)), doc['provenance'][key])
           for key in (MEASURED, LOADER, PATENT) if key in doc['provenance']])

    out.append("<h2 id='packet'>Packet</h2><p>Byte offsets from the sync.</p>")
    _rows(out, ('Offset', 'Bytes', 'Field', 'Note', 'Source'),
          [(p['offset'], p['bytes'], p['field'], p['note'], Markup(_tag(p['provenance'])))
           for p in doc['packet']])

    out.append("<h2 id='checksums'>Checksums</h2>")
    out.append("<p>Both are the same routine, at image offset <code>0xF1F0</code> in "
               "<code>SSLOAD.DLL</code> and again verbatim at <code>0xF240</code>, with its "
               "table at <code>0x149F8</code>. The header field is the only little endian "
               "number in the format, and the absent final inversion is what makes the body "
               "check come out zero over the whole packet.</p>")
    _rows(out, ('Property', 'Value', 'Source'),
          [(c['property'], c['value'], Markup(_tag(c['provenance'])))
           for c in doc['checksums']])

    out.append("<h2 id='recovery'>Recovering a damaged packet</h2>")
    out.append("<p>The body checksum is not only a test. A CRC is linear over GF(2), so its "
               "thirty-two bits are thirty-two equations in the bits of the packet, and where "
               "the damage sits in a known place the lost bits can be solved for rather than "
               "guessed. What makes that worth doing is the shape of the damage. Measured over "
               "the 1994 KCET capture, the slicer decides a bit with about 6.8 sigma of margin, "
               "which on ordinary noise would be one error in 10^15; the real rate is nearer one "
               "in a thousand. The errors are not noise at all. They are rare dropouts that "
               "destroy a few bits outright - 0.2 to 0.9 per cent of bits land inside half the "
               "eye, where noise alone would put 0.005 per cent - and a packet spans about a "
               "hundred fields, so most packets meet one.</p>")
    out.append("<p>That is what makes them recoverable. A dropout leaves its mark on the bits "
               "the slicer decided by the narrowest margin, so those positions are already known "
               "and only their values are in doubt. A bit like that is an <i>erasure</i>, and an "
               "erasure costs one bit of the checksum where a flipped bit of unknown position "
               "costs about eleven, because its position has to be searched as well and every "
               "candidate tried is a chance to checksum by coincidence.</p>")
    out.append("<p>How many erasures a packet needs is a property of that packet, so the budget "
               "is not fixed: the two least confident bits are tried first and the set widens "
               "only while the checksum refuses it. <b>Left over</b> below is the bits of "
               "checksum still unspent, which is what the answer is verified with. On the 1994 "
               "KCET capture half of all repairs are done with four erased bits or fewer and the "
               "mean is 6.1, so 25.9 bits are typically held back rather than the 12 that going "
               "straight to the ceiling would leave - the odds of a coincidence fall from one in "
               "4,000 to one in 60 million. Whatever comes back must also tile into commands, which no "
               "arithmetic here can fake, and a candidate that does not tile is a reason to "
               "spend more of the budget rather than to give up.</p>")
    _rows(out, ('Damage', 'Unknown bits', 'Candidates', 'Left over', 'Outcome'),
          [(r['damage'], r['unknownBits'], r['candidates'], r['checksumLeft'], r['outcome'])
           for r in doc['recovery']])
    out.append("<p>Measured on that capture: of 173 packets the sequence numbers say were "
               "sent, 154 were found, 19 passed the body checksum outright and 140 do once "
               "repaired. Three things corroborate them, none of which the repair constrains. "
               "The recovered sequence numbers rise in step across the whole capture. The "
               "recovered Time command puts the station eight hours behind GMT, which is Los "
               "Angeles. And solving the same packets with the budget fixed at the ceiling "
               "instead of escalating returns byte for byte the same answers - two different "
               "routes to the same packet.</p>")

    out.append("<h2 id='commands'>Commands</h2>")
    out.append("<p>Indexed by <code>byte 0 &amp; 0x3F</code>. <b>Length</b> is how wide the "
               "length field after the type is and <b>header</b> where the variable part "
               "starts, both from the loader's own descriptor table. <b>Loader</b> is what "
               "<code>SSLOAD.DLL</code> does with the type: run its own code, accept it "
               "without acting, or nothing. <b>Seen</b> means a capture has carried one.</p>")
    _rows(out, ('Type', 'Name', 'Length', 'Header', 'Loader', 'Decoded', 'Seen', 'Source'),
          [(c['type'], c['name'] or '', c['lengthBytes'], c['headerBytes'], c['loader'],
            'yes' if c['decoded'] else 'no', 'yes' if c['seen'] else 'no',
            Markup(_tag(c['provenance'])))
           for c in doc['commands']])

    out.append("<h2 id='command-layouts'>Command layouts</h2>")
    out.append("<p>Every type the loader implements. The five a capture carries are byte "
               "exact; the rest are as <code>SSLOAD.DLL</code> reads them and have never "
               "been seen on a wire.</p>")
    for layout in doc['commandLayouts']:
        out.append("<h3>%s &mdash; type %d %s</h3>"
                   % (html.escape(layout['name']), layout['type'], _tag(layout['provenance'])))
        _rows(out, ('Byte', 'Field', 'Source'),
              [(b['at'], b['field'], Markup(_tag(b['provenance']))) for b in layout['bytes']])

    out.append("<h2>Flag bits</h2>")
    for group in doc['flags']:
        out.append("<h3>%s</h3>" % html.escape(group['byte']))
        _rows(out, ('Mask', 'Field', 'Note', 'Source'),
              [('0x%02X' % b['mask'], b['field'], b['note'], Markup(_tag(b['provenance'])))
               for b in group['bits']])

    out.append("<h2>Compression</h2>")
    out.append("<p>%d symbols, longest code %d bits, Kraft sum exactly 1. Symbols "
               "0x01-0x1F expand to whole words and fragments, which is why reading it as a "
               "character code never worked. The only strings in the format that are "
               "<em>not</em> compressed are the theme names.</p>"
               % (doc['compression']['symbols'], doc['compression']['maxCodeBits']))
    _rows(out, ('Code', 'Bits', 'Symbol'),
          [(e['code'], len(e['code']), repr(e['symbol'])[1:-1])
           for e in doc['compression']['table']])

    if doc['database']:
        out.append("<h2>What the data becomes</h2>")
        out.append("<p>The schema <code>SSLOAD.DLL</code> loads a decoded guide into, read "
                   "from <code>dbsets.dll</code>. It is the clearest statement of what the "
                   "protocol's fields are <em>for</em>: a Theme is a (genre, subgenre) pair, "
                   "a Channel binds a tuning position to a Station over a time range, and "
                   "the eight booleans on a Time Slot are the attribute set a broadcast "
                   "can carry.</p>")
        _rows(out, ('Table', 'Columns'),
              [(t['table'], ', '.join(t['columns'])) for t in doc['database']])

    out.append("<h2>Still unknown</h2>")
    _rows(out, ('What', 'Where it stands'), [
        ('Show List byte 3, low nibble',
         'Ten distinct values, one per channel-day. The loader stores it at header offset '
         '0xC and nothing reads it back; it correlates with neither slot count, description '
         'count nor command length.'),
        ('Station Node Status, 295 of 733 bytes',
         'Two monotonic counters and what looks like a node bitmap. Seven instances across '
         'three tapes is not enough to name fields from.'),
        ('What types 9, 10, 31, 36-40 and 42 are for',
         'Their layouts are read off the loader and are in the table above; what they mean '
         'is not. They gate each other through two bytes the loader keeps - 31 advances a '
         'state, 9 waits for it, 42 sets a flag, 36 and 37 wait for that - so they are one '
         'sequence a receiver is walked through rather than nine messages. The first step '
         'is addressed by a six byte key the receiver must already hold, which is reason '
         'enough for no capture to carry any of them.'),
        ('The fourth byte of a Region lineup entry',
         'Three of the four carry the channel id and its number. The loader never reads '
         'the fourth.'),
        ('Which network names the loader knows',
         'Channel Data gives the network as text after a dash, and the loader looks that '
         'text up in a table to store an id. The table itself is in dbsets.dll, not read '
         'here, so the name is carried through as it was sent.'),
    ])

    out.append("</main></body></html>")
    return '\n'.join(out)


def main():
    doc = tables()

    # the published table must still be what the binary says; ssload-tables.json is written
    # straight out of SSLOAD.DLL by docs/ssload_extract.py
    disagreements = [c['type'] for c in doc['commands']
                     if c['binary'] is not None and c['binary'] != c['loader']]
    if disagreements:
        raise SystemExit('command table disagrees with SSLOAD.DLL on types %s'
                         % ', '.join(str(t) for t in disagreements))

    for entry in doc['commands']:
        entry.pop('binary', None)

    with open(os.path.join(HERE, 'starsight-protocol.json'), 'w') as f:
        json.dump(doc, f, indent=1)
        f.write('\n')
    with open(os.path.join(HERE, 'starsight-protocol.html'), 'w') as f:
        f.write(as_html(doc))
    print('wrote docs/starsight-protocol.json and docs/starsight-protocol.html')


if __name__ == '__main__':
    main()
