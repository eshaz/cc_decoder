StarSight program guide
=======================

What `cc_decoder` decodes on a StarSight VBI line, and what is left. Started as
[issue #3](https://github.com/eshaz/cc_decoder/issues/3), a request for Silent Radio
support - see "How Silent Radio fits in" for why that led here.

The protocol
------------

**The StarSight Data Transmission Network protocol** is what this decodes.
[US6216265B1](https://patents.google.com/patent/US6216265B1/en) (StarSight Telecast)
reproduces it in full - packet header, command table, and the byte layout of each
command - and `lib/starsight.py` follows that document.

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

```
0      0x2C sync
1-2    packet size in bytes, MSB first
3-6    timestamp, minutes since midnight GMT 1 January 1992, MSB first
7-8    VBI stream id
9-10   CRC1
11..   message: concatenated commands
last 4 CRC-32
```

Verified on all three captures: the size field chains to the next sync, the timestamps
are monotonic, and the commands **tile the message exactly** once the four byte trailer
is reserved - 2,437 packets tile against 4 syncs rejected, 99.7% / 99.7% / 100% by
capture. The April capture's header timestamps read `1998-04-19 02:48 GMT`, which is
what the CEA-608 XDS clock on line 21 of the same tape reports to the minute - two
independent services agreeing on the instant.

Neither CRC has been reproduced. Sweeping polynomials, spans and bit orders found no
variant that matches, so `decode_starsight_commands` uses exact tiling as its
integrity check instead; a whole packet of self-consistent command lengths is a strong
test on its own.

Commands
--------

Byte 0 is `enc_flg | key_id | type`, then a length that includes those bytes and is one
or two bytes wide depending on the type. Seen in the captures:

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
List channels stay bare 16 bit ids here rather than resolving to call signs. A longer
capture would fix that and nothing else needs to change to read it.

**Show Title** - `cmp_flg` in bit 7 of byte 2 with CC, stereo and B&W in bits 6-4, a
20 bit show id split across the low nibble of byte 2 and bytes 3-4, a 16 bit theme id
in bytes 5-6, then the title. The theme id groups programs by genre, which is why
IROC, NASCAR 2Day and RPM 2Day share one and Grow It!, How 2 and Fix It Up! share
another.

**Show List** - a version byte, a 16 bit channel id, a 32 bit start time and a slot
count, then slots of `flags | duration in minutes | show id`, with two more bytes when
the flags carry a description id and two more again for a show group. Slots carry only
a duration, so the channel's start time is given once and each duration advances it.
The slot count must be honored; without it a truncated command keeps accumulating and
walks the clock into the far future.

**Show Description** - laid out like Show Title, but its id is **not** a show id. Across
the April capture its 227 ids and the 3,640 title ids overlap in exactly zero places, and
they run to 529,489 where show ids stop at 65,510. A description is pointed at from a Show
List slot through the optional field its flags select, so the two are joined through the
schedule rather than by sharing a key. No single capture here carries both, so the join is
described but not demonstrated.

**Time** - a 32 bit minute count plus seconds, and the station's standard zone offset
with a daylight saving flag. The April capture reads `-5 hours, daylight saving`,
which is EST plus DST, matching the `D` in XDS's `TM 02:48D` on line 21.

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

Alongside the log, `out.starsight.html` puts each command type in **its own table, with
nothing joined between them**: Show List keeps the show number it carried rather than
borrowing a title, and Show Description stays under its own numbering. Relating them in
the page would hide which records the capture actually contained, and the two numberings
are not the same key anyway.

| table | what it holds |
|---|---|
| Show List | channel, start, duration, show number - the schedule |
| Show Title | show number, theme number, title |
| Show Description | description number, description |
| Time | the station clock, about twice a minute |
| Commands | every command type seen, with counts |

Times are the station's wall clock where a Time command supplied the offset. Clicking a
column heading sorts that table and pushes earlier choices down as further keys, so any
grouping can be built up without writing the data out once per arrangement.

Each capture carries a guide window a few days ahead of its own clock, each with an
absolute GMT start, a duration and a show id:

| capture | slots | channels | window | named in capture |
|---|---|---|---|---|
| 31 Oct | 23,836 | 201 | 1998-11-03 .. 11-07 | 16,294 |
| 7 Nov | 19,594 | 201 | 1998-11-11 .. 11-14 | - |

A slot goes unnamed only when its Show Title never came round on the carousel inside
the captured half hour; the title table itself decodes completely.

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

| | compressed | decode cleanly |
|---|---|---|
| titles | 11,031 | **11,031 (100%)** |
| descriptions | 231 | 225 (97.4%) |

Every compressed title in all three captures decodes. The six descriptions that fail
are all in the noisiest capture and are VBI bit errors, which the decoder reports
rather than papering over.

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

What is left
------------

**Six damaged descriptions**, which are VBI bit errors in the April capture rather than
anything unknown about the format. Every title decodes.

**Neither CRC.** Sweeping polynomials, spans and bit orders found no variant that
matches either the two byte CRC1 or the four byte trailer, so
`decode_starsight_commands` uses exact command tiling as its integrity check instead.

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
