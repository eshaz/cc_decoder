StarSight program guide
=======================

**The byte-level reference is [`starsight-protocol.html`](starsight-protocol.html)** -
every command layout, both checksums, every flag bit, the compression table and the schema
the data lands in, each row marked with whether a capture measured it, the loader supplied
it, or a patent described it. This document is the account of how those facts were arrived
at, and what is still missing.

What `cc_decoder` decodes on a StarSight VBI line, and what is left. Started as
[issue #3](https://github.com/eshaz/cc_decoder/issues/3), a request for Silent Radio
support - see "How Silent Radio fits in" for why that led here.

The protocol
------------

**The StarSight Data Transmission Network protocol** is what this decodes.
[US6216265B1](https://patents.google.com/patent/US6216265B1/en) (StarSight Telecast)
reproduces it in full - packet header, command table, and the byte layout of each
command - and `lib/starsight.py` follows that document.

**The shipped implementation has now been read.** `SSLOAD.DLL` on the Windows 98 SE CD -
the same binary the compression table came out of, md5 `6a85a39dd536cfe582d19090fd48e68e` -
is Microsoft's StarSight Guide Data Loader, and it contains the original command parser
and the database it loads into. It settles every field below that had been inferred. See
"What SSLOAD.DLL settles".

The waveform underneath is the CEA-608 one - 32x line rate bit clock, clock run in,
2+1 start bits, 16 data bits per field, about 960 bit/s - moved to another VBI line.
Decoder silicon calls that bearer Gemstar 1x (TI TVP514x and Analog Devices ADV718x
both list "Gemstar 1x, NTSC, 2 bytes") and
[US5543852A](https://patents.google.com/patent/US5543852A/en) defines its 1X and 2X
rates; Gemstar acquired StarSight in 1997, so its bearer carrying StarSight's data
format is what you would expect. The protocol is what matters here, and it is bearer
independent.

**It is not NABTS.** [RFC 2728](https://www.rfc-editor.org/rfc/rfc2728) describes the
same StarSight guide going over PBS National Datacast at the 5.727 Mbit/s teletext
rate, 9600 baud per VBI line per field - the same guide, ten times faster. Measured
across 4,000 fields of the sample, the 5.73 MHz band sits four to six orders of
magnitude below the 0.5 MHz band on every one of the 16 captured lines, so there is no
NABTS - and so no WaveTop - in these captures.

Packet layout
-------------

The layout is in [the reference](starsight-protocol.html#packet): a sync, a size, a
timestamp, a stream id, a header checksum, the commands, and a body checksum.

Verified on all three captures: the size field chains to the next sync, the timestamps
are monotonic, and the commands **tile the message exactly** once the four byte trailer
is reserved - 2,437 packets tile against 4 syncs rejected, 99.7% / 99.7% / 100% by
capture. The April capture's header timestamps read `1998-04-19 02:48 GMT`, which is
what the CEA-608 XDS clock on line 21 of the same tape reports to the minute - two
independent services agreeing on the instant.

**Both checksums are reproduced**, from `SSLOAD.DLL`. One routine computes them, at image
offset `0xF1F0` and again verbatim at `0xF240`: a table driven reflected CRC-32 over the
standard IEEE polynomial `0xEDB88320`, table at `0x149F8`, started at `0xFFFFFFFF` and
returned **without a final inversion**.

```
header   crc32(bytes 0..8) & 0xFFFF  ==  bytes 9-10 read LITTLE endian
body     crc32(the whole packet, its own trailer included)  ==  0
```

Sweeping had failed here for two reasons, and both are in that pair of lines. The header
checksum is the one number in the format stored little endian, so every big-endian reading
of bytes 9-10 missed it - across 243 packets the little endian reading matches all of them
and the big endian reading none. And the body checksum has no final inversion, which is
what makes running it over the packet *including* its own trailer come out zero; a sweep
looking for the stored value to equal the computed one over the payload would never land.

They are used as the loader uses them. The header's own checksum settles whether a `0x2C`
is a sync at all, which is a far better test than the size merely looking plausible, and
once it passes the size is trustworthy, so a packet whose body fails is stepped over whole
instead of rescanned a byte at a time. Exact command tiling is kept, demoted to a
cross-check: a packet that checksums correctly and still does not tile would be structure
this decoder does not know, and that count is **zero** across every capture.

Commands
--------

Byte layouts for all nineteen command types the loader implements are in
[the reference](starsight-protocol.html#command-layouts); what follows is what the captures
show about the five that carry guide data, and how the rest were arrived at.

Byte 0 is `enc_flg | key_id | type`; the type is the low 6 bits, and `SSLOAD.DLL`
dispatches on exactly `byte0 & 0x3F`. Then a length that includes those bytes and is one
or two bytes wide depending on the type - the loader carries a 64 entry descriptor table
for that, one entry per type, so the widths are not guesswork. Seen in the captures:

| type | command | KET 18 Apr 98 | KET 31 Oct 98 | KET 7 Nov 98 | KCET 3 May 94 |
|---|---|---|---|---|---|
| 6 | Show Title | 3,640 | 3,034 | 4,487 | - |
| 20 | Sequence Number | 377 | 1,027 | 1,033 | 78 |
| 5 | Show List | - | 941 | 757 | 82 |
| 8 | Show Description | 233 | - | - | - |
| 1 | Time | 12 | 29 | 30 | - |
| 2 | Daylight Saving Change | 12 | 29 | 30 | - |

The fourth capture is from a different station and four years earlier - **KCET, Los
Angeles, 3 May 1994**, a `vhs-decode` TBC rather than the KET tapes - and it parses with
the same code: 78 packets, header timestamps `1994-05-03 17:25 .. 17:30 GMT` matching its
own filename. The protocol was stable across at least those four years and two markets.

**No capture contains a Channel Data command (type 4).** Only types 1, 2, 5, 6, 8 and 20
appear anywhere across all four. Type 4 is what carries station call letters as plain
ASCII, on a carousel slow enough that no half hour window caught one, which is why Show
List channels stay bare 15 bit ids here rather than resolving to call signs. A longer
capture would fix that and nothing else needs to change to read it.

**Show Title** - `cmp_flg` in bit 7 of byte 2 with CC, stereo and B&W in bits 6-4, a
**16 bit** show id in bytes 3-4, a 16 bit theme id in bytes 5-6, then the title, running
to the end of the command. All four flag bits are confirmed: the loader's type 6 handler
tests exactly `0x80`, `0x40`, `0x20`, `0x10` of byte 2 and carries the last three through
to the Access columns `TS Closed Caption`, `TS Stereo` and the broadcast property
abbreviated `B/W`.

The id is 16 bits and the low nibble of byte 2 is not part of it - the handler reads
bytes 3-4 and nothing else. The nibble is zero on all 692 titles in the April capture
anyway, so nothing decoded differently for having assumed otherwise.

The theme id groups programs by genre, which is why IROC, NASCAR 2Day and RPM 2Day share
one and Grow It!, How 2 and Fix It Up! share another. In the loader's database a theme is
literally a pair: the `Theme` table is `T Theme ID`, `T Genre ID`, `T SubGenre ID`, so one
theme id names a genre and a subgenre together.

**Show List** - a version byte, a **15 bit** channel id in bytes 4-5 (the loader masks
byte 4 with `0x7F`; no capture has used the spare top bit), a 32 bit start time and a slot
count, then slots of `flags | duration in minutes | show id`, with two more bytes when
the flags carry a description id and two more again for a show group. Slots carry only
a duration, so the channel's start time is given once and each duration advances it.
The slot count must be honored; without it a truncated command keeps accumulating and
walks the clock into the far future.

A slot is four bytes, six with a description id and two more again with a show group -
which is exactly what the loader's slot walker computes, confirming that bit 7 and bit 5
of the slot flags are the two optional field selectors. **Bit 6 is pay per view**: the
walker copies it into bit 7 of its own slot record and the database writer hands that bit
to `CTimeSlot` as `TS Pay Per View`. The captures agree without being asked to - 581 of
October's 23,836 slots set it, and they sit on 11 channels out of 201, covering 89% to
100% of each of those channels' listings, which is the shape of a pay-per-view channel and
not of a bit error. The show id inside a slot is
**16 bits**, bytes 2-3; the loader takes no nibble from the flags. Seven slots out of
43,430 across the October and November captures had had a non-zero nibble read into their
id, putting them above 65,535 where no title could ever match. Bits 1-4 and 6 of the slot
flags are the only ones a VBI bit error can corrupt without changing the slot's length and
so failing the tiling check, which is what those seven look like.

**Show Description** - laid out like Show Title, with a 16 bit id in bytes 3-4 that is
**not** a show id: a description is pointed at from a Show List slot through the optional
field its flags select, so the two are joined through the schedule rather than by sharing
a key. No single capture here carries both, so the join is described but not demonstrated.

Bit 3 of the flag byte selects an **extended form** that puts three more bytes in front of
the text: a rating system and rating code in byte 7, five content advisory bits in byte 8,
and the last two digits of the year in byte 9. The text then starts at byte 10. Byte 7
splits as a three bit rating system above a four bit rating - anything above 7 the loader
clamps - and byte 8's bits are `nudity 0x02`, `violence 0x04`, `adult situations 0x08`,
`adult themes 0x10`, `adult language 0x80`, which is the vocabulary the loader pairs them
with. Every one of those is now parsed and exported. This is also the one place reading
the loader changed what `cc_decoder` already produced. 55 of the April
capture's 233 descriptions use the extended form, and all 55 were being decompressed from
byte 7, which fed three bytes of rating data into the Huffman decoder before the text:

```
was:  ennhG, caLento skrting aioaens  II veterans come home. Fredric March, Myrna Loy...
now:  TVG Three World War II veterans come home. Fredric March, Myrna Loy, Dana Andrews.
```

The same bit explains an anomaly that had been recorded here as a property of the format.
Description ids appeared to need 20 bits and to run to 529,489 while a slot could only
point at 16 bits' worth - but 529,489 is 524,288 + 5,201, and 524,288 is bit 3 shifted
into an id that never had a high nibble. **Description ids are 16 bits**; across the April
capture they now run 4 to 6,330, and the contradiction with the two byte slot reference
goes away. The years are the strongest check available that the rest of the extended
layout is right, because they are externally verifiable: 1946 against Fredric March and
Myrna Loy is The Best Years of Our Lives, 1972 against Steve McQueen and Ali MacGraw is
The Getaway, 1926 to 1998 across 51 descriptions with no value out of range.

**Time** - a 32 bit minute count in bytes 2-5, seconds in byte 7, and byte 6 carrying
the station's standard zone offset as whole hours in bits 0-3, a sign in bit 4 and a
daylight saving flag in bit 7. Byte for byte what the loader does. The April capture reads
`-5 hours, daylight saving`, which is EST plus DST, matching the `D` in XDS's `TM 02:48D`
on line 21.

**Sequence Number** - a 32 bit counter in bytes 2-5, one command per packet, stepping by
one. Its gaps are therefore exactly the packets that never arrived, which is the only
direct measure of loss this format offers - a checksum tells you a packet was damaged,
only the counter tells you one never came at all. It reconciles
with what the decoder saw: over the October capture the counter spans **1,043 packets**,
1,027 were accepted, 3 were rejected at the tiling check and the remaining **13 never
formed at all** - 1.53% lost. Per day-block the losses are 6, 2, 3, 2 and 3, and that
third figure is worth keeping: the 5 November block is short exactly three channels and
lost exactly three packets. The 3 November block is short sixty-two channels and lost six,
so loss does not explain that one - the recording start does.

Count it from consecutive steps and not from the span: a single flipped bit moves a 32 bit
value by up to 2^31, and one does exactly that in the 1994 capture, stepping 5,720 to
1,054,298. Counted properly the four captures lose:

| capture | packets | lost |
|---|---|---|
| KET 18 Apr 1998 | 378 | 1 (0.26%) |
| KET 31 Oct 1998 | 1,043 | 16 (1.53%) |
| KET 7 Nov 1998 | 1,040 | 7 (0.67%) |
| KCET 3 May 1994 | 177 | **99 (55.9%)** |

That last row explains a capture that had never been explained, and checksumming it made
the picture worse still. Of the packets the 1994 tape does deliver, **three pass both
checksums; forty have a corrupt header and eighty-three a corrupt body**. The 2,231 slots
this used to report from that tape were read out of packets with bit errors in them - which
is exactly why 123 of those slots landed in the year 9312.

It is not the crop: sweeping the crop offset across 106, 116, 126, 136 and 146 gives two
good packets at best and the current 126 is already there. It is a `vhs-decode` TBC off VHS
rather than one of the clean off-air KET tapes, and what it has to say about StarSight is
that the protocol and the decoder both work on it - three clean packets parse perfectly -
and that almost nothing else on the tape survived.

`SSLOAD.DLL` is no help here and does not need to be: its command table sends type 20 to
the trace function and reads nothing, because the loader has two CRCs and a database to
accumulate into. This is read off the wire instead.

**Station Node Status** - 733 fixed bytes of the station's own health, sent about every
ten minutes. It had never been seen in any capture, and the reason was this decoder: the
maximum packet size here was a made up 600 when the loader's is `0x7F8`, so every packet
carrying one was thrown away. There are seven across the three 1998 tapes now, and **438 of
the 733 bytes are identical in all of them** - tapes seven months apart - so most of it is a
template.

Two fields are pinned down, and both are **Unix seconds**, which nothing else in this
format uses; everywhere else counts minutes from 1992-01-01. Bytes 48-51 are the station's
own clock: it lands inside each capture's packet header window and steps forward about ten
minutes from one block to the next. Bytes 12-15 are constant within a capture and an hour
or two behind it, which reads as when the block's contents were assembled rather than sent.

```
Station Node 733 bytes  assembled 1998-11-01 01:39  station clock 1998-11-01 03:36:09 GMT
Station Node 733 bytes  assembled 1998-11-01 01:39  station clock 1998-11-01 03:46:15 GMT
```

That clock is a third independent witness to when a tape was made, after the packet headers
and the Time commands: on the April tape it reads `1998-04-19 02:51:47` against packet
headers of `02:48` and a Time command of `02:59:27`.

The rest is left alone. A pair of 32 bit counters advance monotonically, scattered single
bits look like a node bitmap, and there is a table of nine eighteen-byte records - but seven
instances is not enough to name fields from, and `SSLOAD.DLL` is no help, sending type 21 to
its trace function as it does type 20.

**Daylight Saving Change** - two 32 bit minute counts, bytes 2-5 and 6-9, on the same
epoch as every other timestamp: the instants the station moves its clock forward and back.
The April capture sends `1998-04-05 02:00` and `1998-10-25 01:00`, which are the two US
daylight saving transitions of 1998 - a date nobody had to be told. The pair is repeated
unchanged on every send.

What SSLOAD.DLL settles
-----------------------

The patent describes the protocol; the loader *is* an implementation of it, and the two
agree. `SSLOAD.DLL` (122,880 bytes, 23 April 1999) exports one real entry point,
`EPG_DBLoad`, and imports the rest of its data model from `dbsets.dll` next to it. Both
were read the same way the compression table was: parse the PE, disassemble, and follow
the string and import tables.

Three things came out of it.

**A command descriptor table.** At image offset `0x14610` there are 64 eight byte entries
indexed by `byte0 & 0x3F`: a handler pointer, and a flags byte whose bit 7 selects a two
byte length and whose low bits give the fixed header size. It is the authoritative version
of the two length sets in `lib/starsight.py`, and it disagrees with them in one place -
**type 15 takes a one byte length, not two**. No capture contains a type 15, so nothing
decoded here has ever depended on it. The table also settles the 47 types the two sets say
nothing about, and its header sizes match what the decoder already used: 11 bytes for Show
List, which is exactly where `decode_show_list` starts reading slots.

Nineteen types have a real handler. Ten of them are the ones already decoded here; the
loader stubs types 20, 21, 22, 24 and 32-35 to its trace function and leaves the rest
unimplemented. So **nine command types exist that this has never seen**: 9, 10, 31, 36,
37, 38, 39, 40 and 42. Given that the Access schema underneath has tables for stations,
networks, genres, subgenres, ratings and enhancements that nothing in these captures
fills, those nine are the obvious place the missing names live.

**The attribute vocabulary.** The loader writes into an Access database whose schema is
spelled out in `dbsets.dll` as literal column names. A broadcast carries exactly five
hard flags - `TS Pay Per View`, `TS Closed Caption`, `TS Stereo`, `TS Rerun`,
`TS Tape Inhibited` - of which StarSight populates the first four and leaves the rest
zero, plus three more (`TS Other Properties Exist`, `TS Alternate Data Exists`,
`TS Alternate Audio Exists`) it never sets. Anything beyond that is an extensible
*broadcast property* attached to a program, and the resource string table names the four
StarSight ships as abbreviation, name and pictogram triples: `CC`/Closed Caption,
`STER`/Stereo, `PPV`/Pay Per View, `RRUN`/Rerun - and `B/W`.

That is what promotes closed captioning and stereo from probable to confirmed here. The
bits had been read off the wire as 6 and 5 of the Show Title flag byte, adjacent to black
and white at bit 4; the loader tests the same three bits in the same order and hands them
to Closed Caption, Stereo and B/W.

**Fields the captures did carry after all.** The same tables carry a content rating system
("Extended MPAA Based Rating System for StarSight", with TV-Y, TV-Y7, TV-G, TV-PG, TV-14
and TV-MA), five content advisories (nudity, violence, adult situations, adult themes,
adult language) and a two digit year formatted `19%d`. They ride the Show Description
command's extended form. The wider model behind it is three levels deep - a `Channel` is a
(tuning space, channel number) binding to a `Station` over a time range, and a station
belongs to a `Network` - which is why a Show List channel id is an internal number and not
a call sign, and why resolving it needs the type 4 command none of these captures caught.

What actually changed in the decoder
------------------------------------

| finding | effect on output |
|---|---|
| Show Description bit 3 selects an extended form; text starts at byte 10 | 55 of the April capture's 233 descriptions were garbled and now are not |
| Description ids are 16 bits | ids ran to 529,489, now 4 to 6,330 |
| A slot's show id is 16 bits, bytes 2-3 | 7 slots of 43,430 stop being corrupted by a bit error in the flags |
| A Show Title's show id is 16 bits | none - the nibble was zero on all 11,161 titles |
| A Show List channel id is 15 bits | none - the spare bit is unused in all five captures |
| Type 15 takes a one byte length | none - no capture contains a type 15 |

The loader accepts all 64 command types. `cc_decoder` still rejects a packet containing a
type it does not know, which is deliberate: the loader can afford to be permissive because
it trusts two CRCs, and exact command tiling is the only integrity check here. Accepting
33 more types would give noise 33 more ways to tile.

What comes out
--------------

Records are written as they arrive, the way `decode_xds_packets` writes XDS, so
`out.starsight` is a log of the guide in broadcast order with a timecode on every line
rather than a report assembled at the end.

The schedule is always broadcast ahead of the names: measured over the whole October
capture, not one of its 23,836 slots had its title already in hand, and 16,294 got one
later. So a Show List slot goes out as it arrives with its show id, and a Show Title
then completes every slot it names - a `Guide` line at the moment a receiver could
first have displayed that entry.

```
00:12:05;11  Show List    ch 449    1998-11-04 18:30 GMT   30 min  id 13     (title not sent yet)
00:21:00;23  Show Title   id 13      theme 644    Amazing Animals
00:21:00;23  Guide        ch 449    1998-11-04 18:30 GMT   30 min  id 13     Amazing Animals
```

Alongside the log, `out.starsight.html` draws the schedule **as the guide itself drew
it**: channels down the side, half hours across the top, and each program one cell as
wide as it is long. US6498895B2 calls that "an array of irregular cells, which vary in
length, corresponding to different television program lengths", and the reason it has to
be arithmetic rather than table columns is that a slot runs 5 minutes or 240 and nothing
divides evenly - a cell is placed at `start x pixels-per-minute` and sized by its
duration. The strip scrolls sideways across the whole captured window; the day tabs jump
to a day; the box underneath is the patent's "channel information box", showing the rest
of whichever slot is under the pointer.

The grid is the one place the page joins anything, and only in the direction the broadcast
supports: a cell shows its program's title when a Show Title has named it and `#12345`
when none has, which is exactly the distinction the log's `Guide` lines draw. A cell also
carries its theme, description number, show group and attribute bits, so nothing the Show
List held is lost by drawing it this way rather than as rows - a slot whose flag byte sets
one of the four bits nothing names keeps the raw byte too, which happens twice in 43,430.
Pay per view cells are tinted and a program already running when its list starts takes a
marked edge, so both read without hovering; a key under the grid says which is which.

A grid drawn at a fixed width per minute has to be bounded, because a bit error in a Show
List's 32 bit start time carries every slot of that command with it. One in the 1994
capture lands 123 slots in the year 9312, which asks for 128 million half-hour headings and
gigabytes of markup. The window is therefore measured out from the middle of the schedule
rather than its edges, so no single slot can move it, and anything beyond is counted in the
key instead of drawn.

Then **Reception**, which is what the Sequence Number command is for: how many packets the
counter says were sent, how many arrived, and where the gaps fall. The remaining command
types stay in tables of their own, unjoined:

| table | what it holds |
|---|---|
| Show Title | show number, theme number, title |
| Show Description | description number, year, rating system, rating, advisories, description |
| Time | the station clock, about twice a minute |
| Daylight Saving Change | the two instants the station moves its clock |
| Lost packets | where the gaps in the sequence numbers fall |
| Commands | every command type seen, with counts |

Times are the station's wall clock where a Time command supplied the offset. Clicking a
column heading sorts that table and pushes earlier choices down as further keys, so any
grouping can be built up without writing the data out once per arrangement.

The October capture draws 23,836 cells over 201 channels and five days in a 1.7 MB page,
in about a tenth of a second. A capture with no Show List - April is one - has no grid, and
says so.

`out.starsight.json` is the same data normalized for a query engine, against
`docs/starsight.schema.json`: entity tables for channels, themes, programs and
descriptions, one flat table of listings that names its channel and program by the ids
the broadcast used, and the station clock. Every instant is GMT.

**It contains only what the broadcast carried.** No source file, no decoder name or
version, no decode timestamp, no packet or command counts, no record of what failed to
decode - none of that is in the document, because none of it is guide data. What a
particular run of the decoder did is in `out.starsight` and on the console; the JSON is
the guide. Programs carry the Show Title attribute bits, all three of which - closed
captioning, stereo and black and white - are now confirmed against `SSLOAD.DLL`.
Descriptions carry everything the extended form of their command held: the year, the
rating system and the rating within it, and the content advisories by name.

```json
{"id":62,"text":"TV14 Bank robber and wife flee to Mexican border. Steve McQueen, ...",
 "ratingSystem":3,"rating":3,"year":1972,"advisories":["violence","adult situations"]}
```

The rating system and rating stay as the two numbers the broadcast sent. They are not
translated, because the same code means different things in different systems and the
loader does not translate them either - where a rating has a printable name it is already
at the front of the text, which is where `SSLOAD.DLL` reads it from too. What the numbers
clearly do is separate: system 0 is the one every adult title in the April capture uses.

Three things there were measured rather than assumed. Renumbering entities densely by
how often they are referenced **makes the gzipped file larger**: gzip already models the
repetition, and shortening a five digit token leaves it less to match. Nesting listings
under their program is worse again and ruins the two queries that matter, what is on a
channel at a time and what is on everywhere at an instant. Shipping prebuilt indexes
costs 56% more to save five milliseconds a browser can spend itself. So the file stays
flat, on-wire ids, no indexes - about 2.8 MB raw and 226 KB gzipped for a 23,836 listing
capture, parsing in roughly 7 ms.

Each capture carries a guide window a few days ahead of its own clock, each with an
absolute GMT start, a duration and a show id:

| capture | slots | channels | window | named in capture |
|---|---|---|---|---|
| 31 Oct | 23,836 | 201 | 1998-11-03 .. 11-07 | 16,294 |
| 7 Nov | 19,594 | 201 | 1998-11-11 .. 11-14 | 17,879 |

A slot goes unnamed only when its Show Title never came round on the carousel inside
the captured half hour; the title table itself decodes completely. November names more of
its own slots than October does from fewer of them - 17,879 of 19,594 against 16,294 of
23,836 - because its half hour caught more of the title carousel, not because it carries
a better schedule.

The April capture carries titles and descriptions but no Show List at all.

```
1998-11-03 00:00 GMT  ch 10601    30 min  M*A*S*H
1998-11-03 02:00 GMT  ch 571      60 min  WWF RAW
1998-11-03 04:30 GMT  ch 717     240 min  SIGN OFF
```

The text compression table
--------------------------

Strings are Huffman compressed unless compressing one would make it longer, so all but
a few dozen titles per capture are compressed. US6216265B1 defers the table to
"Appendix A" - a composite document of gate array schematics that no patent office
ever printed, and which is absent from every member of the family, including the 215
page Canadian one.

**The table was recovered from a shipped implementation instead.** Microsoft licensed
the StarSight data format in 1996 - the obligation to hand over "decryption of the
StarSight data stream, StarSight data formats" is recorded in StarSight's FY1996 10-K -
and put the resulting decoder on the Windows 98 CD as `SSLOAD.DLL`, the "Microsoft
StarSight Guide Data Loader". It carries the table as a 256 byte binary tree, with a
31 entry phrase dictionary beside it. The same bytes appear in `SSSCAN.EXE` and
`BPCCTL.DLL` on that disc, and the same code independently in Gemstar's own DOS tool
`FREQCCD_.EXE`, which still has its Borland debug symbols attached and names the
modules `DECOMP.CPP` and `DYNADICT.CPP`.

It is Huffman over **127 symbols**, Kraft sum exactly 1. The reason reading it as a
character code failed is that symbols `0x01`-`0x1F` are not control characters: they
expand to whole words and fragments - `' the '`, `'ing '`, `'and '`, `"'s "`, `'tion'`,
`'when '`. The code averages about 6.3 bits per token and a token is often several
characters, so per-character statistics never lined up.

That two layer design is StarSight's own, and they patented it:
[US5548338A](https://patents.google.com/patent/US5548338A/en), "Compression of an
electronic programming guide", claims Huffman coding over characters *and* replacement
strings chosen for their savings. It prints only illustrative tables rather than the
real one, but it describes the structure exactly.

`lib/starsight_table.py` holds it, and `docs/huffman-recovery.md` is the account of how
it was found. Decoding the three captures:

| | commands | decode cleanly |
|---|---|---|
| titles | 11,161 | **11,161 (100%)** |
| descriptions | 233 | **233 (100%)** |

Every title and every description in all three captures decodes, and the decoder reports
nothing undecodable. Not all of them are compressed - a string goes out in the clear when
compressing it would make it longer, which is 36 of the April capture's 3,640 titles. Six
descriptions used to fail here and were written up as VBI bit errors; they were not, they
were the extended form being read from the wrong offset.

```
Show Title   id 9555    theme 97     Wanted: Dead or Alive
Show Title   id 35386   theme 367    Amityville II: The Possession
Show Title   id 7885    theme 651    Las Vegas Senior Classic, Second Round
Show Desc    id 64      TV14 Gun battles; routine stops lead to gunfire.
```

Independently, a ciphertext-only attack run before the table was found had recovered
46 codewords and 607 readable titles by bootstrapping from crib-solved anchors, and
every codeword it produced agrees with the table - including the word symbols `The `,
`of `, `to `, `& `, `'s `, `and `, `er`, `an`, `in`, `th`, `ar`, `un`, `ur`, `al`,
`st`. What that attack could not have found alone is the framing: it was converging on
the right answer for the wrong structural reason.

Repeated channels
-----------------

A channel id is **a position in a line-up, not a station**, so the same station appears
under several of them and their schedules come out identical. In the October capture 201
channel ids carry only **173 distinct schedules**: 45 of the ids fall into 17 groups whose
listings match exactly, the largest being eight ids - 4602, 4603, 4606, 4608, 4609, 8360,
14830 and 18925 - carrying one station between them.

That is the broadcast, not the decoder. Each of those ids arrives in its own Show List
command with its own id in bytes 4-5, and **all 17 groups reappear as the same 17 groups
in the November capture**, a separate tape a week later. Bit errors do not reproduce
across tapes. It is also what `SSLOAD.DLL`'s schema says should happen: its `Channel`
table is a (tuning space, channel number) binding with a `C Station ID` pointing at a
separate `Station`, so many channels to one station is the design. One stream fed cable
systems across the country and a receiver kept the ids its own line-up used.

Channels whose listings are *nearly* identical are a different thing, and worth knowing
when reading one capture. The schedule is broadcast a day at a time, all 201 channels in a
block, and the first block in the October capture is the only incomplete one:

| day | channels carried | |
|---|---|---|
| 3 Nov | 139 of 201 | the first block in the recording |
| 4 Nov | 201 | |
| 5 Nov | 198 | three lost to bit errors |
| 6 Nov | 201 | |
| 7 Nov | 201 | |

The obvious reading is that recording began part way through that block, and it is the
likely one, but the capture does not quite prove it: the channel order differs from day to
day, so the 62 absences cannot be mapped onto positions in another day's block to show
they are a clipped prefix. What the capture does show is that they are concentrated at the
front - 15 of the first 20 channels of the next day's order are among them - and that no
later block is short this way.

Either way the effect on reading the data is the same: 62 channels have four days where
their twins have five, which makes a pair look like a partial copy of each other when it
is only a partial recording.

Of 1,005 possible channel-days, three are genuinely lost. Measured over the whole capture
the tiling check
accepts **1,027 packets and rejects 3** - 619 bytes of 194,467, 0.3% - and each of those
three is about the size of one Show List command, so the two counts plausibly describe the
same three losses, though nothing here proves they are.

Nothing is truncated inside what did arrive: across all 941 Show List commands the slot
counts declare 23,836 slots and the decoder reads 23,836, with 939 of the 941 consuming
their bytes exactly. The two that do not are bit errors, and neither invents a channel -
one declares 0 slots with a year-6145 timestamp and the top bit set on its channel, so it
contributes nothing at all, and the other overruns its last slot's optional fields by four
bytes, costing one slot in 23,836.

### What the loader does about both

`SSLOAD.DLL` was written against the same two problems, and its answers say the decoder's
are right.

**It never merges two channel ids.** Every table it writes is opened on an index -
`?OpenIndexed@CDatabaseRecordset` is called fourteen times in one function, once per
recordset - and for most tables that index is named `TSSLoadBy`, a key in the loader's own
id space rather than the database's. Records are then written with `UpdateRS`, having been
built with `0x4A4D4120` where their real id goes: seek by the loader's key, update the row
if it is there and add it if it is not. So a channel id that arrives again on the carousel
lands on its own row and nothing is duplicated - and two *different* ids stay two rows,
because nothing in a Show List says they are one station. Only a Channel Data command
carries the station binding, and none of these captures has one. The loader keeps them
apart for exactly the reason this does.

It does normalise in the one direction the broadcast supports: the episode is written
first and `?EpisodeID@CEpisodeT` is called to fetch its key when the time slot is built, so
a program carried on eight channels is stored once and referenced eight times - which is
the shape of `out.starsight.json`, programs deduplicated and listings per channel.

**Partial data is the expected case, not an error.** The loader keeps two `COleDateTime`
fields - at `0xF4` and `0x100` in its state - and extends them against every time slot it
writes, so by the end of a load they are the earliest and latest instant it actually
received. It then runs two parameterised deletes: `Delete Expired Time Slot`, bounded by a
date, and `Delete Omitted Time Slot`, bounded by **that pair** and its own loader id. It
prunes only inside the window it can see, so a channel-day that never arrived is not taken
as a channel-day that was cancelled - whatever a previous load put there survives.

That is a receiver designed to accumulate across broadcasts rather than treat any one
reception as complete, which is what a half hour off a carousel forces, and it is the same
model `starsight_merge.py` uses: absence is not deletion.

Merging tapes
-------------

One capture is a half hour of a carousel: part of a schedule, and part of the names for
it. `starsight_merge.py` reads the `.starsight.json` of several decodes and writes one
merged export and one merged page, through the same code that renders a single decode.

```
python3 starsight_merge.py 'KET_1998-1*.starsight.json' -o KET-november
```

It is worth doing. The October tape carries 23,836 listings and names 16,294 of them
inside its own half hour; merged with the November tape a week later, **20,959 of those
same October listings have a title** - 4,665 more - because November's carousel came round
on shows October's did not reach. Across both, 38,843 of 43,430 listings are named.

Everything is keyed rather than concatenated, and keyed the way the loader keys it:

- **A channel's day is the unit.** The loader replaces what it receives rather than adding
  to it, bounding its `Delete Omitted Time Slot` by the window the load covered. So when
  two tapes both carry a channel-day the later one wins it outright, instead of both
  surviving as overlapping listings. The decoder does the same within a capture, for a
  Show List that comes round again.
- **A repeated key updates.** The loader writes every record with `UpdateRS` against an
  index in its own id space - seek, update if present, add if not - so the newest value of
  a show number or description wins.
- **Recency comes from the tapes themselves**, from their Time commands, so the order the
  files are passed in does not matter.

Passing the same file twice changes nothing.

**Show numbers are reused, so tapes must come from one season.** This is the thing to
know before merging, and the captures measure it:

| tapes | apart | show numbers in both | naming different programs |
|---|---|---|---|
| 31 Oct, 7 Nov | one week | 2,209 | **1** |
| 18 Apr, 31 Oct | six months | 801 | 116 |
| 18 Apr, 7 Nov | seven months | 1,323 | 189 |

A number is stable within a season and recycled between them - show 4903 is
"Countdown to Signing Day" in April and "Pee-Wee's Playhouse" by October. Merging across
that boundary mixes two meanings of one number and no choice of winner is right, so the
tool takes the newer, which is what the loader would do, and says how often it had to:

```
306 of 3588 show numbers shared between inputs name something different (9%)
** these captures look like different seasons.
```

Under a couple of percent is one season and the merge is sound. Nine percent is not.
Channel numbers are internal to a StarSight line-up too, so tapes from different markets
should never be merged for the same reason.

`--expire` adds the loader's other delete, dropping listings that had already finished by
the newest capture's clock. It is off by default because it is a receiver's rule and not
an archive's: run against the October and November tapes together it drops all 23,836 of
October's listings, which is exactly right for a box in November 1998 and exactly wrong
for a record of what was broadcast. Against a single capture it removes nothing at all -
the guide runs days ahead of the tape that carried it.

The research in this repository
------------------------------

| file | what it is |
|---|---|
| `docs/starsight.md` | this: the protocol, and what is left of it |
| `docs/ssload-analysis.md` | how Microsoft's loader was read, and what each finding rests on |
| `docs/huffman-recovery.md` | how the compression table was recovered, including the wrong turns |
| `docs/starsight-protocol.json` / `.html` | every mapping as a table - the reference for the format |
| `docs/starsight.schema.json` | the separate question of what a decoded capture looks like |
| `docs/ssload-tables.json` | the raw tables read out of `SSLOAD.DLL` and `dbsets.dll` |
| `docs/samples/*.commands.txt` | real commands off the wire, as hex - the evidence |
| `docs/replay_commands.py` | runs those back through the decoder, without ffmpeg |
| `docs/capture_commands.py` | what wrote them; takes `cc_decoder.py`'s own arguments |

Three scripts regenerate all of it, and none of them needs a video:

```
python3 docs/starsight_tables.py                         # the protocol tables
python3 docs/ssload_extract.py SSLOAD.DLL dbsets.dll \
        -o docs/ssload-tables.json                       # the loader's own tables
python3 docs/replay_commands.py docs/samples/*.commands.txt
```

`starsight_tables.py` reads its constants back out of `lib/starsight.py`, so the published
tables cannot drift from the decoder that uses them, and the command widths in them can be
diffed against `ssload_extract.py`'s output to check the decoder against the loader.
`replay_commands.py` runs a saved capture's commands through the decoder without ffmpeg,
which is the cheap way to see what a change does to real data. Two captures are saved
whole rather than sampled, because the schedule is broadcast well ahead of the names and a
slice from the front of the stream is all Show List: it would never exercise the join. The
replay reproduces the video decode exactly - 43,222 records and 16,294 slots named for
31 October, the same numbers the 557 MB capture produces - in about a second.

| sample | commands | what it covers |
|---|---|---|
| `KET_1998-04-18` | 4,274 | Show Title, Show Description including all 55 extended, Time, Daylight Saving Change |
| `KET_1998-10-31` | 5,060 | Show List and the slot walker, 23,836 slots over 201 channels, and the title join |

The binaries themselves are not here - they are Microsoft's - but
`docs/ssload-analysis.md` records what they are, where they came from and their checksums,
and `docs/ssload-tables.json` holds everything read out of them.

What is left
------------

**Nothing in the strings.** Every title and every description in every capture now
decodes - 11,161 and 233 of them. The six descriptions previously listed here as VBI bit
errors were not: they were the extended form being read from the wrong offset.

**Nothing in the packet.** Both checksums are reproduced and both are checked; the
maximum packet size the loader accepts, `0x7F8`, is honoured.

**The dynamic dictionary.** `FREQCCD_.EXE` loads a `DDPACKET.BIN` through a
`DYNADICT.CPP` module, a word level layer above the character code. Nothing in these
captures appears to need it, but it is the one documented part of the codec that has
not been read.

How Silent Radio fits in
------------------------

Silent Radio fed scrolling LED signs from the VBI. Its decoder patent,
[US4620227A](https://patents.google.com/patent/US4620227A/en) (Cybernetic Data
Products, 1986), specifies 32x line rate, a clock run in, a 2 bit dwell, a 1 bit start
and sixteen data bits - the CEA-608 waveform - and its actual invention is
line-agnostic capture, "regardless of the horizontal line". That is the same bearer
Gemstar 1X uses, which is why the issue's request led here. The captures are named for
Silent Radio but carry StarSight. The second Cybernetic patent,
[US4551720A](https://patents.google.com/patent/US4551720A/en), is often cited as the
VBI one and is not - it describes the twisted pair bus inside the sign.

Decoding a capture
------------------

```
ffmpeg -hide_banner -f rawvideo -pixel_format y16 -framerate 59.94 -video_size 910x16 \
       -i capture-tbc-crop.vbi \
       -vf "weave,crop=760:16:126:10,scale=interl=1,scale=720:16" \
       -c:v ffv1 -f matroska pipe:1 \
  | cc_decoder.py --start_line 0 --end_line 15 --ccformat srt,xds,starsight - -o out
```

`weave` pairs the two fields into a frame, which is what the caption field logic
expects, and halves the frame rate to 29.97 so the default `--frame_rate` is correct.
Without it the two caption channels interleave and the text comes out shredded.

The StarSight line is one continuous byte stream that alternates fields - taking either
field alone yields zero valid packets, while both in order yield a full parse. `weave`
puts the two fields of a VBI line on adjacent rows, so the decoder reads them in order.

`out.starsight` carries the guide log and a closing summary. `out.T1.srt` has the line
21 captions and `out.xds` the XDS.
