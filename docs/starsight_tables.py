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
                           CHANNEL_CALL_SIGN_WIDTH, CHANNEL_ID_MASK,
                           CHANNEL_SHOWS_CALL_SIGN, COMMANDS_SEEN, COMMAND_NAMES,
                           COMMAND_TABLE, COMPRESSED_FLAG, DESCRIPTION_EXTENDED, LOADER,
                           MEASURED, PATENT, PROVENANCE_NOTES, SLOT_HAS_DESCRIPTION,
                           SLOT_HAS_SHOW_GROUP, SLOT_JOINED_IN_PROGRESS, SLOT_PAY_PER_VIEW,
                           SLOT_UNNAMED, STARSIGHT_CRC_POLYNOMIAL, STARSIGHT_EPOCH,
                           STARSIGHT_HEADER, STARSIGHT_MAX_PACKET, STARSIGHT_SYNC,
                           STARSIGHT_TRAILER, STATION_ASSEMBLED, STATION_CLOCK,
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
        (0xFF, 'call sign presence',
         'Read from 0x80 down, one bit per candidate byte at 8-15; the selected bytes are '
         'concatenated and padded to %d.' % CHANNEL_CALL_SIGN_WIDTH, L),
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
                      ('6-9', 'a 32 bit field', L),
                      ('10..', 'the channels available in the region', P)]),
    ('Channel Data', 4, L, [
        ('0', 'type', L), ('1', 'length', L),
        ('3', 'bit 7: channel number bit 8; bits 0-6: channel id high 7', L),
        ('4', 'channel id low 8 (15 bits with byte 3)', L),
        ('5', 'flags', L), ('6', 'channel number low 8 (9 bits with byte 3)', L),
        ('7', 'which of bytes 8-15 are call letters', L),
        ('8-15', 'call letter candidates', L)]),
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
        ('%d-%d' % (STATION_ASSEMBLED, STATION_ASSEMBLED + 3),
         'when the block was assembled, Unix seconds', M),
        ('%d-%d' % (STATION_CLOCK, STATION_CLOCK + 3),
         "the station's own clock, Unix seconds", M),
        ('the rest', '438 of 733 bytes are identical across every instance; what moves is '
                     'two monotonic counters and what looks like a node bitmap', M)]),
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
        ('Types 9, 10, 31, 36-40, 42',
         'The loader runs code for them and neither the patents nor any capture says what '
         'they carry. Framed and counted here, not interpreted.'),
        ('Region and Channel Data payloads',
         'The fixed headers are read; what follows is a channel list the loader walks with '
         'a routine this has not yet followed.'),
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
