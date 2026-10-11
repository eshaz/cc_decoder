StarSight
=========

StarSight was an interactive television program guide of the 1990s. Its data - schedules,
program titles and descriptions, channel lineups, news pages - was broadcast continuously
on a line of the NTSC vertical blanking interval, and a StarSight receiver built its guide
from what it heard. This document describes that data stream, the StarSight Data
Transmission Network protocol, and how `cc_decoder` decodes it.

- [`starsight-protocol.html`](starsight-protocol.html) (and `.json`) is the byte-level
  reference: every command type, layout, flag bit and the compression table, generated from
  `lib/starsight.py` by `docs/starsight_tables.py`.
- [`starsight.schema.json`](starsight.schema.json) describes the decoder's JSON export.
- [`research/starsight-sources.md`](research/starsight-sources.md) gives the sources and
  the evidence behind everything here.

Contents

1. [The signal](#the-signal)
2. [Packets](#packets)
3. [Commands](#commands)
4. [Text](#text)
5. [The clock](#the-clock)
6. [The schedule](#the-schedule)
7. [Themes](#themes)
8. [Channels and lineups](#channels-and-lineups)
9. [The news service](#the-news-service)
10. [The station](#the-station)
11. [Receiver control](#receiver-control)
12. [How a receiver builds the guide](#how-a-receiver-builds-the-guide)
13. [Decoding with cc_decoder](#decoding-with-cc_decoder)
14. [Open questions](#open-questions)


The signal
----------

StarSight uses the line 21 caption waveform of CEA-608 on another line of the vertical
blanking interval: a clock run-in, start bits and sixteen data bits a field at 32 times the
line rate. Each field carries two bytes, about 960 bits a second, and unlike captions all
eight bits of each byte are data - there is no parity bit.

The two fields of the line carry **one byte stream**: field 1's two bytes, then field 2's,
and so on. Neither field alone holds a whole packet.


Packets
-------

The stream is a sequence of packets. All numbers are big endian except the two checksums.

| Offset | Bytes | Field |
|---|---|---|
| 0 | 1 | sync, `0x2C` |
| 1 | 2 | size of the whole packet, header and trailer included; at most `0x7F8` |
| 3 | 4 | the minute the packet was sent (see [Times](#times)) |
| 7 | 2 | stream id, which identifies the feed |
| 9 | 2 | header check, little endian |
| 11 | size - 15 | commands, which fill the space exactly |
| size - 4 | 4 | body check, little endian |

Both checks use one CRC: the reflected CRC-32 with polynomial `0xEDB88320`, started at
`0xFFFFFFFF` and returned **without a final inversion** - zlib's `crc32` with its result
inverted.

```
header check   crc(bytes 0-8) & 0xFFFF  ==  bytes 9-10, least significant byte first
body check     crc(the whole packet, trailer included)  ==  0
```

The body check holds because the trailer is the CRC of everything before it, stored least
significant byte first. The header check is what marks a `0x2C` as a real sync: once it
passes, the size can be trusted.

Every packet carries a [Sequence Number](#the-clock) command, so a receiver can tell when
it has missed one.

### Times

Unless a field says otherwise, a time is a 32 bit count of minutes since **1 January 1992,
00:00 GMT**. Times are in GMT; a station's [Time](#the-clock) command gives its offset.
Station Node Status is the exception, counting Unix seconds.


Commands
--------

Every command starts the same way.

| Byte | Field |
|---|---|
| 0 | bit 7: encrypted; bit 6: which key; bits 0-5: the command type |
| 1, or 1-2 | the command's length, counting from byte 0 |

Whether the length takes one byte or two depends on the type. The full table of 64 types
is in [the reference](starsight-protocol.html#commands); these are the ones with a known
meaning:

| Type | Command | Length bytes | |
|---|---|---|---|
| 1 | Time | 1 | [the clock](#the-clock) |
| 2 | Daylight Saving Change | 1 | [the clock](#the-clock) |
| 3 | Region | 2 | [channels and lineups](#channels-and-lineups) |
| 4 | Channel Data | 1 | [channels and lineups](#channels-and-lineups) |
| 5 | Show List | 2 | [the schedule](#the-schedule) |
| 6 | Show Title | 1 | [the schedule](#the-schedule) |
| 8 | Show Description | 1 | [the schedule](#the-schedule) |
| 9 | Region Name | 1 | [channels and lineups](#channels-and-lineups) |
| 10 | Cycle Start | 1 | [the station](#the-station) |
| 11 | Theme Category | 2 | [themes](#themes) |
| 12 | Theme Sub-Category | 2 | [themes](#themes) |
| 13 | Subscriber Reset | 1 | [receiver control](#receiver-control) |
| 14 | Authorization | 1 | [receiver control](#receiver-control) |
| 17 | Key Distribution | 1 | [receiver control](#receiver-control) |
| 20 | Sequence Number | 1 | [the clock](#the-clock) |
| 21 | Station Node Status | 2 | [the station](#the-station) |
| 22 | Long Assign IR Codes | 2 | no layout known |
| 24 | Subscriber Unit | 2 | no layout known |
| 31 | Postal Code | 2 | [channels and lineups](#channels-and-lineups) |
| 32 | News Service | 2 | [the news service](#the-news-service) |
| 33 | News Menu | 2 | [the news service](#the-news-service) |
| 34 | News Page | 2 | [the news service](#the-news-service) |
| 36 | Segmented Command | 2 | [below](#segmented-commands) |
| 37-40 | (unnamed) | 2 | [receiver control](#receiver-control) |
| 42 | (unnamed) | 2 | [receiver control](#receiver-control) |

### Encryption

Bit 7 of byte 0 marks an encrypted command and bit 6 says which of two keys enciphered it.
A receiver without the key cannot read one. An encrypted Show List keeps bytes 0-10 in
the clear - its version, channel, start and slot count - and enciphers only its slots. The
cipher is unknown.

### Segmented commands

A command too long for one packet travels in parts, each a Segmented Command (type 36).

| Byte | Field |
|---|---|
| 3 | high nibble: this part, from 0; low nibble: the last part |
| 4 | which message the part belongs to |
| 5.. | the part |

Joined in order, the parts form one command, beginning with its own type and length and
followed by a CRC-32 that has to check as a packet's body check does. The whole is at most
3,000 bytes. A part of another message abandons the message in progress, and so does a
part out of order. The news service is sent this way.


Text
----

A string is either in the clear - printable ASCII ended by a NUL - or **compressed**. In a
Show Title or Show Description, bit 7 of the flag byte says which. A string is sent
compressed unless compressing it would make it longer, so most are.

Compression is a fixed Huffman code of 127 symbols, with codewords of 4 to 20 bits:

- `0x00`, the NUL that ends the string. What follows it in the last byte is padding.
- `0x01`-`0x1F`, 31 phrases: `' the '`, `'s '`, `'ing '`, `'and '`, `'tion'`, `"'s "`,
  `'when '`, `'with '`, `'The '` and other common fragments.
- `0x20`-`0x7E`, the printable characters.

The whole table is in [the reference](starsight-protocol.html#compression) and in
`lib/starsight_table.py`. A codeword outside the table, or a terminator anywhere but the
last byte, means the string was damaged.

Some strings are never compressed: theme category and sub-category names. Region names are
always compressed. The news service has its own rules (see
[the news service](#the-news-service)).


The clock
---------

**Time** (type 1) is the station's clock, sent about twice a minute.

| Byte | Field |
|---|---|
| 2-5 | the time, to the minute |
| 6 | bits 0-3: the station's standard offset from GMT in whole hours; bit 4: the offset is west (negative); bit 6: this channel is **not a StarSight provider**; bit 7: daylight saving is in force, so the wall clock is an hour ahead of the offset |
| 7 | seconds |

A receiver scanning for the channel that carries its guide passes over one whose Time sets
bit 6. A feed that carries the guide for many markets at once sets it.

**Daylight Saving Change** (type 2) gives the two instants the station changes its clock:
when daylight saving starts (bytes 2-5) and ends (bytes 6-9). It repeats unchanged with the
Time commands.

**Sequence Number** (type 20) is a 32 bit counter in bytes 2-5, one per packet, rising by
one. A gap is that many packets lost. A checksum only says a packet arrived damaged; the
counter says how many never arrived at all.


The schedule
------------

Three commands make the schedule. A **Show List** lays out one channel's day as a run of
slots, each naming a program by its show id; a **Show Title** names a show id; a **Show
Description** carries a longer text that a slot can point at.

### Show List (type 5)

| Byte | Field |
|---|---|
| 3 | low nibble: a version |
| 4-5 | channel id, 15 bits; the top bit is unused |
| 6-9 | start of the first slot: a GMT midnight |
| 10 | slot count |
| 11.. | the slots |

A slot is four bytes, with optional fields after them:

| Byte | Field |
|---|---|
| 0 | flags |
| 1 | duration in minutes |
| 2-3 | show id |
| +2 | description id, when flag `0x80` is set |
| +2 | show group, when flag `0x20` is set |

| Flag | Meaning |
|---|---|
| `0x80` | a description id follows |
| `0x40` | pay per view |
| `0x20` | a show group follows |
| `0x1E` | unused |
| `0x01` | on the first slot: the program was already running when the list starts |

Slots carry durations, not start times: each starts where the one before it ended. The
slot count bounds the list, so a damaged command cannot run its clock on into the future.

A Show List covers one channel for one day. A later list for the same channel and day
replaces it, and a receiver skips a list whose version it already holds.

### Show Title (type 6)

| Byte | Field |
|---|---|
| 2 | flags |
| 3-4 | show id |
| 5-6 | theme id (see [themes](#themes)) |
| 7.. | the title |

| Flag | Meaning |
|---|---|
| `0x80` | the title is compressed |
| `0x40` | closed captioned |
| `0x20` | stereo |
| `0x10` | black and white |
| `0x0F` | unused |

### Show Description (type 8)

| Byte | Field |
|---|---|
| 2 | flags |
| 3-4 | description id |
| 7 | extended form only: bits 7-5 a critic's rating in stars, bits 4-1 a film rating |
| 8 | extended form only: content advisories |
| 9 | extended form only: the year, last two digits, zero for none |
| 7.. or 10.. | the text |

| Flag | Meaning |
|---|---|
| `0x80` | the text is compressed |
| `0x40`, `0x20`, `0x10` | closed captioned, stereo, black and white: the bits of the Show Title of the program described |
| `0x08` | the extended form: bytes 7-9 are present and the text starts at byte 10 |

Description ids are a numbering of their own, not show ids: a slot points at a description
through its optional field.

The film rating codes are:

| Code | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| Rating | NR | G | NC-17 | PG | PG-13 | R | NC-17 | NC-17 |

Code 2 also appears on some unrated films, so it may mean any rating outside the MPAA's.

| Advisory bit | Meaning |
|---|---|
| `0x02` | nudity |
| `0x04` | violence |
| `0x08` | adult situations |
| `0x10` | adult themes |
| `0x80` | adult language |
| `0x01`, `0x20`, `0x40` | unknown |

Two more things are carried in the text itself. A text that opens with a TV Parental
Guidelines rating - `TVY`, `TVY7`, `TVG`, `TVPG`, `TV14`, `TVM` or `TVMA` - gives the
program's TV rating (`TVM` is the original name for TV-MA). A text ending ` (R).` marks a
rerun.


Themes
------

A Show Title's theme id says what kind of program it is. The theme commands are the
receiver's search menus, and they are the only thing that names a theme.

**Theme Category** (type 11) names the top-level categories.

| Byte | Field |
|---|---|
| 3 | version; a change means every name is to be reloaded |
| 4 | entry count |
| 5.. | entries: id (1 byte), flags (1), length (1), then that many bytes holding a NUL-terminated name |

The categories run from 1 Movies to 11 Miscellaneous. What the flag byte means is unknown.

**Theme Sub-Category** (type 12) gives one category's menu.

| Byte | Field |
|---|---|
| 3 | the category it belongs to |
| 4 | entry count, low 7 bits |
| 5.. | entries: the entry's length (1 byte), flags (1), a count of theme ids (1), the theme ids (2 each), then the name, NUL-terminated |

A theme is listed in every sub-category that fits it. Each category's menu opens with an
`All ...` entry listing all its themes and closes with a `Closed Captioned` one. So theme
640 is in Movies under Action/Adventure, in Children's under Cable in the Classroom, and
in Action/Mystery/Horror under Action/Adventure/Thriller. What the flag byte means is
unknown.


Channels and lineups
--------------------

A Show List names its channel by a **channel id**: a position in StarSight's own numbering,
not a station. A **Region** - one cable system's lineup, or one market's stations for an
antenna - says which channel ids a receiver there can see and the number each sits on.
**Channel Data** names a channel id: a station's call letters and network, or a cable
service's name. Several channel ids can carry the same station's schedule.

### Region (type 3)

| Byte | Field |
|---|---|
| 3-4 | region id |
| 5 | flags: bit 0, apply now; bit 5, a cable lineup rather than an antenna's |
| 6-9 | the time the lineup takes effect |
| 10 | channel count, so a lineup holds at most 255 |
| 11.. | four bytes a channel |

| Entry byte | Field |
|---|---|
| 0 | bit 7: bit 8 of the channel number; bits 0-6: the channel id's high 7 bits |
| 1 | the channel id's low 8 bits |
| 2 | the channel number's low 8 bits: the number the channel sits on in this region |
| 3 | bits 0-1: the tier - 0 basic, 1 extended, 2 premium, 3 pay per view; bits 6-7 unknown |

A receiver keeps the lineup for its own region and ignores the rest. Unless bit 0 of byte 5
says to apply it now, a lineup waits until the stream's clock reaches its effective time.
A new lineup is merged into the one held: a channel in both keeps the name it already had,
and a channel missing from the new one is dropped.

### Channel Data (type 4)

| Byte | Field |
|---|---|
| 1 | length, 16 |
| 2 | unknown |
| 3 | bit 7: bit 8 of the channel number; bits 0-6: the channel id's high 7 bits |
| 4 | the channel id's low 8 bits |
| 5 | bit 1: a cable service, shown by name rather than number |
| 6 | the channel number's low 8 bits: the channel a station broadcasts on, zero for a cable service |
| 7 | short label: one bit a character, `0x80` for byte 8 down to `0x01` for byte 15 |
| 8-15 | the name, padded |

A station's name is `CALL-NET`: its call letters, a dash and its network - `WKYC-NBC` on
channel 3. A receiver splits it at the first dash from the fourth character on, and looks
the network up by name. A cable service's eight characters are its name, which can hold a
dash of its own - `SCI-FI`, `CNN-HLN` - and is not split. Byte 7 picks up to four of the
eight characters as a short label for a narrow display: `HEADLINE` with `0x9A` gives `HDLN`.
Stations send no label.

Channel Data names a channel; it does not create one. A receiver ignores it for a channel
id no Region has given it.

### Region Name (type 9)

Bytes 2-3 are a region id and the rest of the command is its name, compressed: `columbus,
oh`, `akron - time warner cable`.

### Postal Code (type 31)

| Byte | Field |
|---|---|
| 3-8 | the postal code, as text |
| 9 | unknown |
| 10 | count of antenna regions |
| 11 | count of cable regions |
| 12.. | the region ids, two bytes each: the antenna regions, then the cable ones |
| then | the news area the code is filed under (see [news](#the-news-service)) |
| then | unknown |

### Setting up a receiver

A receiver is set up with a postal code. It waits for the Postal Code command for that
code and keeps the two tables of region ids it lists - one for an antenna, one for cable.
It then keeps the Region Names of those regions and offers them to the viewer, who picks
one. From then on the receiver keeps that region's lineup. Postal Codes are sent in sort
order, so a receiver that has passed its own code without finding it knows the code is not
served.


The news service
----------------

The news service is a menu of sections - national and world news, weather, sports stories
and scores, horoscopes, business - with pages under them. The commands are usually sent
inside [Segmented Commands](#segmented-commands).

**News Service** (type 32): bytes 3-6 unknown, byte 7 the length of the name that follows -
`StarSight NEWS Services`.

**News Menu** (type 33): byte 3 unknown, byte 4 a section count, then for each section its
number (1 byte), five unknown bytes, a name length (1 byte) and the name.

**News Page** (type 34):

| Byte | Field |
|---|---|
| 3 | the section the page is under |
| 4 | unknown |
| 5-6 | page number |
| 7 | flags |
| 8-9 | unknown |
| 10 | area: the news area of the postal codes the page is for, or zero for every area |
| 11-14 | when the page was written |
| 15 | unknown |
| 16 | title length |
| 17.. | the title |
| then | text length, 16 bits; bit 15 set when the text is in the clear |
| then | the text |

Names and titles are compressed as other strings are, or occasionally in the clear. A
page's text breaks its lines differently: compressed, it uses the `0x1F` phrase - `'ur'`
everywhere else - as the line break, and spells `ur` out a letter at a time; in the clear,
it breaks lines with CR. Within a line, `` `i ``, `` `a `` and `` `d `` set the words after
them apart, up to `` `e `` or the end of the line.

```
Weather  page 3449  area 0x57  1997-08-01 12:09  Ashtabula
THURSDAY July 31, 1997
 Winds: NE,12mph   Hum:  60%
 Night: Low 60. Clear
FRIDAY
 84 / 68. Partly sunny &
 warm
```

A receiver shows the pages filed under its own area and those for everyone.


The station
-----------

### Station Node Status (type 21)

The station's report on itself, every one to ten minutes. Its times are **Unix seconds**,
and a time nothing has set holds `0x12CEA600` - 1 January 1980, the zero of a DOS clock.
Byte 3 gives the layout: 1, 3 or 4.

| Byte | Field |
|---|---|
| 3 | layout |
| 4-5 | in layouts 3 and 4, a software release: 200, 403 and 405 are known |
| 6-7 | the stream id, as in the packet header |
| 8-11 | a time, constant over a session |
| 12-15 | when the block's contents were assembled; unset in layout 4 |
| 48-51 | layouts 3 and 4: the station's clock when the block was sent |
| 58-61 | layout 1: the station's clock |

Layout 1 is short. Bytes 38-39, 50-51 and 40-41 count how far the station is through a
cycle - the total, how many are done and how many remain - and bytes 66-67 count minutes
since the assembly time. The block grows by two bytes, all zero, for every eight minutes.

Layouts 3 and 4 are 721 to 743 bytes long. Layout 4 is layout 3 with ten bytes inserted at
byte 420 - an unset time and six zero bytes - so from there on its fields sit ten bytes
later.

| Byte (layout 3) | Field |
|---|---|
| 56-57 | how many bits of each minute map are in use, 1,433 to 1,440 |
| 58-237 | the first minute map |
| 238-417 | the second minute map |
| 432-459 | what the station holds, fourteen 16 bit counts |
| 460-461 | the stream id again |
| 462 | the number of tables, 14 |
| 463-714 | what the station lacks: an 18 byte record a table |
| 721-724 | a daily time; from software 403 on |

**The minute maps** each cover the last day at a bit a minute, the first bit the most
significant. The count at 56 gains one a minute; when it would pass 1,440, both maps turn a
byte to the left and the count drops by eight. The bit just before the count is the
current minute. What a set bit means is unknown.

**The inventory** describes the station's own copy of the guide, in fourteen tables: the
regions, channels, Show Titles and Show Descriptions it holds, then the schedules of ten
days starting with the GMT day of its clock, each counted in channels. Bytes 432-459 give
how many it holds of each. Each 18 byte record gives how many it lacks, then the lowest
eight missing ids in ascending order, zero where there are fewer. For a day, the ids are
channel ids, and what it holds plus what it lacks is every channel.

**The daily time** falls at the same local time every day at a station, in the early
hours, and is always the latest one before the clock. What happens then is unknown.

### Cycle Start (type 10)

Nine bytes marking the start of a cycle of the carousel. A receiver scanning for the guide
counts them, to know it has seen a whole cycle. What its bytes say is unknown.


Receiver control
----------------

**Subscriber Reset** (type 13) asks receivers to discard what they hold.

| Byte | Field |
|---|---|
| 2 | bit 0: clear the stored guide; bit 1: clear it and stop loading until restarted |
| 3-7 | the receiver it is for; zero for every receiver |

Either bit clears the lineup, the schedules and the themes.

**Authorization** (type 14) and **Key Distribution** (type 17) each address one receiver,
by bytes 2-5. Key Distribution runs through receivers in rising order. The rest of both is
opaque.

**Type 42** switches the guide on and off. Bits 4-5 of byte 8 are 0 to stop a receiver
acting on Region, Channel Data, Show List, the theme commands and types 36 and 37, and 1 to
let it act on them again; 2 and 3 change nothing. The rest of the command is unexplained.

**Types 37 to 40** have known layouts but no known purpose.

| Type | Layout |
|---|---|
| 37 | byte 3, low nibble: a version, under which a day already held is skipped; bytes 4-5: a count of region ids, which follow from byte 6 and name the regions it is for; then a midnight within the next seven days, a count of entries, and that many entries of a byte and a 16 bit value |
| 38 | bytes 3-4: an id; bytes 5-7: flags, with the low three bits of byte 7 the first of three lengths and bytes 8 and 9 the others; bytes 10-11: a 16 bit value; from byte 12: three payloads of those lengths, one after another |
| 39 | bytes 3-4: an id; bytes 5-6: a 16 bit value; bytes 7-10: a 32 bit value; byte 11: a byte |
| 40 | bytes 3-4: an id; byte 5: a byte; from byte 6: a payload, up to the length |


How a receiver builds the guide
-------------------------------

**The guide is a carousel.** The commands go round and round, so a receiver that tunes in
at any moment eventually hears all of them. A receiver accumulates: what it hears adds to
or revises what it holds, and nothing it fails to hear is taken as deleted.

**The schedule comes before the names.** Show Lists run days ahead, and the Show Titles
that name their slots come round later on their own cycle. Until its title arrives, a slot
has only a show id.

**Keys update in place.** A show id, description id, channel id or region heard again
replaces what was held under it. A Show List replaces the channel's day it covers. A
receiver drops slots that have finished.

**Show ids are reused.** A number is stable within a season and reassigned between
seasons, so the same show id can name different programs a few months apart.

**A guide is local.** A receiver keeps one region's lineup and the news pages for its own
area. Channel ids, and so schedules, mean something only within StarSight's numbering, and
a cable system's channel numbers only within its region.


Decoding with cc_decoder
------------------------

### Running it

The decoder reads the VBI rows of a video through `ffmpeg`. For a `vhs-decode` TBC VBI
capture, 910 samples a line and 16 lines a field:

```
ffmpeg -hide_banner -f rawvideo -pixel_format y16 -framerate 59.94 -video_size 910x16 \
       -i capture-tbc-crop.vbi \
       -vf "weave,crop=760:16:126:10,scale=interl=1,scale=720:16" \
       -c:v ffv1 -f matroska pipe:1 \
  | cc_decoder.py --start_line 0 --end_line 15 --ccformat srt,xds,starsight - -o out
```

For a cx18 VBI dump, 1440 samples a line and lines 10-21 of each field, with StarSight on
line 14 (rows 8 and 9 once woven):

```
ffmpeg -hide_banner -f rawvideo -pixel_format gray -framerate 59.94 -video_size 1440x12 \
       -i capture.vbi -vf "weave,scale=720:24" -c:v ffv1 -f matroska pipe:1 \
  | cc_decoder.py - --ccformat starsight --start_line 8 --end_line 9 -o out
```

`weave` puts the two fields of a frame on alternate rows, which is what both decoders need:
the caption decoder pairs fields that way, and the StarSight decoder reads a line's two
fields in order as the one stream they are. It also halves the frame rate to 29.97, so the
default `--frame_rate` is right.

Some tapes also carry a Gemstar data line at twice the rate on lines 16-18. It is not
StarSight and is not read.

### How the line is read

- **Each VBI line is a stream of its own.** A row range can be wide: a line that carries
  something else, or a weaker copy of the StarSight line, costs only its own packets, and a
  packet two lines both deliver is kept once.
- **The header check finds packets.** A `0x2C` whose header checks starts a packet; once it
  does, the size is trusted, so a packet whose body fails is skipped whole.
- **A damaged packet is repaired where the checksum allows.** A field that did not slice
  leaves two unknown bytes in place rather than shifting the rest of the packet. Because the
  CRC is linear, its 32 bits are 32 equations in the packet's bits, and bits whose position
  is known can be solved for: the bytes of a lost field, and the bits the slicer decided by
  the narrowest margins. Those are tried a few at a time, widening only while the checksum
  refuses, so the answer assumes as few broken bits as it can and keeps most of the
  checksum to verify with. With no lost field, one or two flipped bits anywhere are also
  searched for. Anything repaired has to split exactly into commands as well.
- **The commands have to fill the packet exactly.** A packet holding a command type with
  no known length is rejected.
- **A tape can hold several recordings.** When the packet clock jumps back more than an
  hour or ahead more than a day, or the stream id changes, and the next packet agrees, a
  new recording starts. Each gets its own page and export, named with its date:
  `out.1999-09-19.starsight.html`, `out.1997-08-01.starsight.html`.

| Damage | Unknown bits | Checksum bits left | Outcome |
|---|---|---|---|
| none | 0 | 32 | passes as it stands |
| bits the slicer doubted, 2 rising to 20 | 2 - 20 | 30 - 12 | solved |
| one lost field | 16 | 16 | solved |
| one flipped bit the slicer was sure of | 1 | about 21 | searched |
| two flipped bits | 2 | about 12 | searched |
| two lost fields | 32 | 0 | refused |

### The log

`out.starsight` is the guide in broadcast order, a timecode on every line, written as the
commands arrive. Because the schedule comes before the names, a Show List's slots are
logged with their show ids, and when a Show Title arrives a `Guide` line completes each
slot it names - the moment a receiver could first have shown it:

```
00:12:05;11  Show List    ch 449    1998-11-04 18:30 GMT   30 min  id 13     (title not sent yet)
00:21:00;23  Show Title   id 13      theme 644    Amazing Animals
00:21:00;23  Guide        ch 449    1998-11-04 18:30 GMT   30 min  id 13     Amazing Animals
```

It closes with a summary: the commands of each type, how many packets passed both checks
or were repaired, and the packets the sequence numbers say were lost.

### The page

`out.starsight.html` shows the guide as a receiver drew it: channels down the side, half
hours across the top, and each program a cell as wide as it is long. Pay per view programs
are tinted and one already running when its list starts has a marked edge. Pointing at a
cell shows the rest of its slot - times, theme, description, show group and attributes.

- A channel shows as Channel Data named it: `3 WKYC` for a station, its name for a cable
  service.
- Where Regions were received, a **lineup** picker shows the guide as a receiver in that
  region drew it: only its channels, in its order and under its numbers.
- Where the news service was received, a **news viewer** shows its menu, the pages of the
  chosen section and the page itself. An area, or a postal code, narrows it to the pages
  for that area and for everyone.
- A line above the viewers gives the packets' time span, stream and the loss the sequence
  numbers show.

The grid's window is measured out from the middle of the schedule, so a slot whose start
a bit error has moved years away is listed under the grid rather than stretching it.

Under the viewers every command type the capture carried has its own table, folded until
opened. Clicking a column heading sorts a table, and earlier choices stay on as further
keys. A table nothing filled is left out, and so is a column no row fills.

| Table | What it holds |
|---|---|
| Show Title | show number, theme number and name, the CC, stereo and B/W bits, whether it was compressed, title |
| Show Description | description number, year, stars, rating and its code, TV rating, advisories, rerun, the CC, stereo and B/W bits, whether it was compressed, description |
| Show List | each channel's day: its start, version and slot count |
| Theme Category, Theme | the search menus, and every theme number by the menus that list it |
| Channel Data | a channel id's number, call letters and network or cable service name, short label, and how many lineups carry it |
| Region | each region's name, cable or antenna, and its lineup by tier under its own numbers |
| Postal Code | the antenna and cable regions offered for a postal code, and its news area |
| News Menu, News Page | the sections, and the latest copy of every page with its line breaks, flags and unexplained bytes |
| Encrypted | each encrypted command whole, and for a Show List the channel, day and slot count its clear header gives |
| Time | the station clock, and whether it says the channel is not a provider |
| Daylight Saving Change | the two instants the station moves its clock |
| Station Node Status | layout, release, stream, times, cycle counts, daily time and the minute maps |
| Station Node inventory | what the station holds and lacks of each table, block by block |
| Station Node Status bytes | each block as a hex dump beside its ASCII and CEA-608 reading |
| Subscriber Reset | what each reset asked for, and the command whole |
| Authorization, Key Distribution, Cycle Start, types 37-40 and 42 | a table each, every field |
| Lost packets | where the gaps in the sequence numbers fall |
| Packets | every packet: the time and stream id in its header and its sequence number |
| Commands | every command type seen, with counts |
| Not decoded | everything that arrived and could not be decoded, whole, and why |

Times on the page are the station's wall clock where a Time command gave its offset.

### The JSON export

`out.starsight.json` is the same guide normalized for a query engine, as
[`starsight.schema.json`](starsight.schema.json) describes: entity tables for channels,
themes, programs and descriptions, and one flat table of listings that names its channel
and program by the ids the broadcast used, in time order. Beside them are the Show Lists'
versions, the lineups, postal codes and theme menus, the news service, the clock, the
packets, the encrypted and addressed commands, Station Node Status whole and as read, and
whatever could not be decoded, whole, with the reason. Every instant is GMT. A channel's
number is the one Channel Data gives; the number it sits on in a cable system is in that
region's lineup.

```json
{"id":62,"text":"TV14 Bank robber and wife flee to Mexican border. Steve McQueen, ...",
 "stars":3,"rating":3,"ratingName":"PG","year":1972,"advisories":["violence","adult situations"]}
```

The page and the export are two views of one model: every field a command decodes into is
in both, a byte nothing explains is kept as sent, and a command that cannot be decoded is
kept whole. The export holds only what the broadcast carried - no decoder name, version or
timestamp, and no count of failed or repaired packets, which are in the log. It is flat
and keeps the on-wire ids, without indexes, which a page rebuilds in milliseconds.

### Merging captures

One capture is part of a carousel: part of a schedule and part of the names for it.
`starsight_merge.py` reads the exports of several decodes and writes one merged export and
page, keyed the way a receiver keys them:

- **A channel's day is the unit.** When two captures both carry a channel-day, the later
  one wins it outright.
- **A repeated id updates.** The newest title or description for an id wins.
- **Recency comes from the captures' own Time commands**, so the order the files are
  given in does not matter, and passing the same file twice changes nothing.

```
python3 starsight_merge.py 'KET_1998-1*.starsight.json' -o KET-november
```

Because show ids are reused between seasons, merge only captures from one season. The tool
reports how many show ids the inputs share that name different programs. Within a season
that is a fraction of a percent; several percent means the captures are from different
seasons and the merged titles cannot all be right:

```
306 of 3588 show numbers shared between inputs name something different (9%)
** these captures look like different seasons.
```

Channel ids are StarSight's own and lineups local, so captures from different markets
should not be merged either.

`--expire` drops listings that had finished by the newest capture's clock, as a receiver
would. It is off by default, because across captures a week apart it deletes the older
one's whole schedule: right for a receiver, wrong for a record of what was broadcast.


Open questions
--------------

- **The encryption.** The cipher and keys behind encrypted Show Lists, Authorization and
  Key Distribution.
- **Station Node Status.** What a set bit in the minute maps means, the counters and times
  at bytes 16-47, 52-55 and 420-431 of layout 3, the short counts at 715-732, and what the
  daily time marks.
- **Types 37 to 40**, whose layouts are known and whose purpose is not, and the rest of
  type 42.
- **Types 22, 24 and 35**, which have no known layout.
- **Unexplained bytes**: bits 6-7 of a lineup entry's tier byte, Channel Data byte 2,
  Postal Code byte 9 and its last byte, the news commands' unread bytes, the theme flag
  bytes, Cycle Start's bytes, and advisory bits `0x01`, `0x20` and `0x40`.
- **Film rating code 2**, nominally NC-17.
