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

from collections import Counter, defaultdict
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
STARSIGHT_MAX_PACKET = 600

# All times on the wire are minutes since midnight GMT on 1 January 1992.
STARSIGHT_EPOCH = datetime.datetime(1992, 1, 1)

# Command: flags and type in byte 0, then a length that includes those bytes. Which
# types carry a one byte length and which a two byte one is fixed by the protocol.
COMMAND_LENGTH_ONE_BYTE = frozenset((1, 2, 4, 6, 8, 13, 14, 17, 20))
COMMAND_LENGTH_TWO_BYTE = frozenset((3, 5, 11, 12, 15, 21, 22, 24))

COMMAND_TIME = 1
COMMAND_SHOW_LIST = 5
COMMAND_SHOW_TITLE = 6
COMMAND_SHOW_DESCRIPTION = 8

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
    """

    MAX_PARITY_RATE = 0.75

    def __init__(self):
        self._parity = LineParityRate()
        self._accepted = set()

    def update(self, row_num, byte1, byte2):
        self._parity.update(row_num, byte1, byte2)
        rate = self._parity.rate(row_num)
        if rate is None:
            return

        if rate < self.MAX_PARITY_RATE:
            self._accepted.add(row_num)
        else:
            self._accepted.discard(row_num)

    def accepts(self, row_num):
        return row_num in self._accepted


def starsight_time(minutes):
    """ Turn an on-wire minute count into a date and time """
    return STARSIGHT_EPOCH + datetime.timedelta(minutes=minutes)

def _u16(data, offset):
    return (data[offset] << 8) | data[offset + 1]

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

    Returns None unless the commands tile the message exactly, which is the check that
    the packet was received cleanly - there is no usable checksum, but a whole packet
    of self-consistent lengths is a strong test on its own.
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

def take_starsight_packets(data):
    """ Consume whole packets from the front of a growing buffer

    Returns `(packets, consumed)`. Only a packet that has not fully arrived is worth
    keeping, so `consumed` runs right up to the byte the next call has to look at
    again; everything before it has either been read or been ruled out for good, and
    the caller drops it. A sync whose commands do not tile is treated as a false one
    and skipped a byte at a time, so a stray 0x2C in the payload cannot walk the
    reader out of step.
    """
    packets = []
    offset = 0

    while offset + STARSIGHT_HEADER <= len(data):
        if data[offset] != STARSIGHT_SYNC:
            offset += 1
            continue

        size = _u16(data, offset + 1)
        if not STARSIGHT_MIN_PACKET <= size <= STARSIGHT_MAX_PACKET:
            offset += 1
            continue

        if offset + size > len(data):
            # the rest of this packet is still on the wire
            break

        commands = decode_starsight_commands(
            data, offset + STARSIGHT_HEADER, offset + size - STARSIGHT_TRAILER)

        if commands is None:
            offset += 1
            continue

        packets.append((starsight_time(_u32(data, offset + 3)), _u16(data, offset + 7), commands))
        offset += size

    return packets, offset

def decode_show_title(command):
    """ `(show id, theme id, title)` - title is None when it was sent compressed """
    flags = command[2]
    show_id = ((flags & 0x0F) << 16) | _u16(command, 3)
    return show_id, _u16(command, 5), decode_starsight_string(flags, command[7:])

def decode_show_description(command):
    """ `(show id, description)` - description is None when it was sent compressed """
    flags = command[2]
    show_id = ((flags & 0x0F) << 16) | _u16(command, 3)
    return show_id, decode_starsight_string(flags, command[7:])

def decode_show_list(command):
    """ A channel's schedule: `(channel, [(start, minutes, show id)])`

    Slots carry a duration rather than a start, so the channel's own start time is
    carried once in the command header and each duration advances it.
    """
    channel = _u16(command, 4)
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
        show_id = (((slot_flags >> 1) & 0x0F) << 16) | _u16(command, offset + 2)

        slots.append((when, minutes, show_id))
        when += datetime.timedelta(minutes=minutes)

        offset += 4 + (2 if slot_flags & 0x80 else 0) + (2 if slot_flags & 0x20 else 0)

    return channel, slots

def decode_time(command):
    """ `(time, standard time zone offset in hours, daylight saving)` from a Time command

    The offset is the station's standard offset; when daylight saving is in force the
    wall clock is an hour ahead of it.
    """
    when = starsight_time(_u32(command, 2)) + datetime.timedelta(seconds=command[7])
    zone = command[6] & 0x0F
    return when, -zone if command[6] & 0x10 else zone, bool(command[6] & 0x80)

def _slot_line(label, channel, start, minutes, show_id, title):
    return '%-12s ch %-6d %s GMT %4d min  id %-7d %s' % (
        label, channel, start.strftime('%Y-%m-%d %H:%M'), minutes, show_id, title)

def new_guide():
    """ What the receiver knows so far: pending slots, plus everything it has learned """
    return {'pending': {}, 'slots': [], 'titles': {}, 'descriptions': {}, 'clock': []}

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
            guide['titles'][show_id] = (theme, title)
            lines += [_slot_line('Guide', channel, start, minutes, show_id, title)
                      for channel, start, minutes in guide['pending'].pop(show_id, ())]
        return lines

    if command_type == COMMAND_SHOW_DESCRIPTION:
        show_id, text = decode_show_description(command)
        if text is not None:
            guide['descriptions'][show_id] = text
        return ['Show Desc    id %-7d %s' % (
            show_id, text if text is not None else '(undecodable)')]

    if command_type == COMMAND_SHOW_LIST:
        channel, slots = decode_show_list(command)
        for start, minutes, show_id in slots:
            guide['pending'].setdefault(show_id, []).append((channel, start, minutes))
            guide['slots'].append((start, channel, minutes, show_id))
        return [_slot_line('Show List', channel, start, minutes, show_id, '(title not sent yet)')
                for start, minutes, show_id in slots]

    if command_type == COMMAND_TIME:
        when, zone, daylight = decode_time(command)
        guide['clock'].append((when, zone, daylight))
        return ['Time         %s GMT  station offset %+d hours%s' % (
            when.strftime('%Y-%m-%d %H:%M:%S'), zone,
            ', daylight saving' if daylight else '')]

    return []

STARSIGHT_HTML_STYLE = """
:root{color-scheme:light dark}
body{font-family:monospace;font-size:13px;margin:1em}
h1{font-size:1.2em;margin:0 0 .3em}
h2{font-size:1em;margin:2em 0 .4em}
p{margin:0 0 .3em}
table{border-collapse:collapse}
th,td{border:1px solid;padding:2px 8px;text-align:left;vertical-align:top}
th{cursor:pointer;white-space:nowrap}
td{white-space:nowrap}
.wrap{white-space:normal;max-width:48em}
.num{text-align:right}
"""

STARSIGHT_HTML_SCRIPT = """
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

def write_starsight_html(output_filename, guide):
    """ Each command type as its own table, exactly as it came off the wire

    Nothing is joined: a Show List row keeps the show number it carried rather than
    borrowing a title, and descriptions stay under their own numbering, because those
    are separate records in the broadcast and relating them here would hide which of
    them the capture actually contained. Clicking a heading sorts that table, and
    earlier choices stay on as further keys.
    """
    out_func, f = get_output_function("starsight.html", output_filename, end="")

    slots, titles, descriptions, clock, counts = guide
    offset = None
    if clock:
        _, zone, daylight = clock[-1]
        offset = zone + (1 if daylight else 0)
    stamp = 'station time' if offset is not None else 'Greenwich Mean Time'

    esc = html.escape
    out_func("<!DOCTYPE html><html><head><meta charset='UTF-8'>"
             "<meta name='description' content='Decoded by https://github.com/eshaz/cc_decoder'>"
             "<title>StarSight Guide</title><style>%s</style></head><body>"
             % STARSIGHT_HTML_STYLE)
    out_func("<h1>StarSight program guide</h1>")
    out_func("<p>Decoded from the NTSC vertical blanking interval by "
             "<a href='https://github.com/eshaz/cc_decoder'>cc_decoder</a>. Each table is "
             "one command type, kept separate. Click a heading to sort by it, again to "
             "reverse; earlier choices stay on as further keys.</p>")

    _table(out_func, 'showlist', 'Show List', 'The schedule. Times are %s.' % stamp,
           (('Channel', 'num'), ('Start', ''), ('Minutes', 'num'), ('Show number', 'num')),
           [(channel, (_local(start, offset) or start).strftime('%Y-%m-%d %H:%M'),
             minutes, show_id)
            for start, channel, minutes, show_id in sorted(slots)])

    _table(out_func, 'showtitle', 'Show Title', 'One per program. The theme number groups programs by genre.',
           (('Show number', 'num'), ('Theme number', 'num'), ('Title', 'wrap')),
           [(show_id, theme, esc(title))
            for show_id, (theme, title) in sorted(titles.items())])

    _table(out_func, 'showdesc', 'Show Description',
           'Numbered separately from the show numbers above; a Show List slot points at '
           'one through the optional field its flags select.',
           (('Description number', 'num'), ('Description', 'wrap')),
           [(description_id, esc(text))
            for description_id, text in sorted(descriptions.items())])

    _table(out_func, 'time', 'Time', 'The station clock, sent about twice a minute.',
           (('Time (GMT)', ''), ('Standard offset', 'num'), ('Daylight saving', '')),
           [(when.strftime('%Y-%m-%d %H:%M:%S'), '%+d' % zone, 'yes' if daylight else 'no')
            for when, zone, daylight in clock])

    _table(out_func, 'commands', 'Commands', 'Every command seen in the capture.',
           (('Type', 'num'), ('Command', ''), ('Count', 'num')),
           [(command_type, COMMAND_NAMES.get(command_type, 'unknown'), count)
            for command_type, count in sorted(counts.items())])

    out_func("<script>%s</script></body></html>" % STARSIGHT_HTML_SCRIPT)

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
    guide = new_guide()
    counts = Counter()
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

        for row_num, byte1, byte2, _ in rows:
            if byte1 is None:
                continue

            lines.update(row_num, byte1, byte2)
            if lines.accepts(row_num):
                buffer += bytes((byte1, byte2))

        packets, consumed = take_starsight_packets(buffer)
        if consumed:
            del buffer[:consumed]

        for _, _, commands in packets:
            for command_type, command in commands:
                counts[command_type] += 1

                try:
                    described = describe_starsight_command(command_type, command, guide)
                    if command_type == COMMAND_SHOW_LIST and described:
                        # a Show List can carry no slots at all, and a channel that
                        # never reaches the log should not reach the count either
                        channels.add(_u16(command, 4))
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

    if f is not None:
        f.close()

    if guide['slots'] or guide['titles']:
        write_starsight_html(output_filename, (guide['slots'], guide['titles'],
                                               guide['descriptions'], guide['clock'], counts))
