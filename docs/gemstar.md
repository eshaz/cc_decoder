# Gemstar EPG data in the NTSC VBI

Gemstar's interactive program guide (VCR Plus+ / **Guide Plus+**, later **TV Guide On Screen**) was
broadcast in the vertical blanking interval of host stations from the mid-1990s until about 2011. TVs,
VCRs and DVD recorders with a Guide Plus+ receiver built their on-screen guide from it. This document
describes the signal and the data formats as far as they are understood, and how `gemstar_decoder.py`
decodes them.

Gemstar bought StarSight in 1997. StarSight rides a similar 1X waveform but uses different message
formats, so it is decoded separately.

## Contents

- [Quick start](#quick-start)
- [What you need](#what-you-need)
- [Physical layer](#physical-layer)
- [Two logical channels on a 2X line](#two-logical-channels-on-a-2x-line)
- [Type B messages](#type-b-messages)
- [`|B` packets](#b-packets)
- [1X lines and the clock packet](#1x-lines-and-the-clock-packet)
- [Message types](#message-types)
- [Graphics objects (0x1E)](#graphics-objects-0x1e)
- [NABTS](#nabts)
- [Nova2 and `.dbm` databases](#nova2-and-dbm-databases)
- [Captures tested](#captures-tested)
- [Open questions](#open-questions)

## Quick start

```
pip install numpy                     # framing, packets, clock, NABTS
pip install unicorn pillow            # decryption, listings, graphics (with FREQCCD_.EXE)

python3 gemstar_decoder.py capture.vbi -o out/
python3 gemstar_decoder.py capture.vbi -o out/ --freqccd /path/to/FREQCCD_.EXE
python3 gemstar_decoder.py capture.flac -o out/ --freqccd /path/to/FREQCCD_.EXE --nabts 12,13
```

The decoder finds the Gemstar lines by itself (`--lines 16` to choose them by hand). For each line it
writes `out/line<N>/`:

| file | contents |
|---|---|
| `packets_raw.tsv` | every 7-bit packet on the 2X line, with checksum and parity results |
| `clock_1x.tsv` | Gemstar clock packets (UTC) |
| `typeb_blocks_raw.tsv` | encrypted Type B blocks (only when no `--freqccd` is given) |
| `typeb_messages.txt` | decrypted, verified Type B messages as hex dumps |
| `b_packets.tsv` | descrambled `\|B` packets |
| `listings.txt` | program listings (SIP messages) |
| `graphics/` | ad screens, detail pages and PNG screens (0x1E objects) with `index.json` |
| `guide.json` | everything understood from the line, in one JSON file |

`out/summary.json` lists the key date and `.INP` table that were found, message counts and the clock
range for each line.

Options:

| option | |
|---|---|
| `--format tbc\|flac\|cap1440` | capture layout (detected from the extension and size otherwise) |
| `--lines 15,16` | NTSC line numbers to decode |
| `--max-fields N` | stop after N fields |
| `--freqccd PATH` | FREQCCD_.EXE; enables decryption, descrambling and listings |
| `--inp-dir DIR` | folder of `MMYY.INP` tables (default: next to FREQCCD_.EXE) |
| `--ddpacket PATH` | DDPACKET.BIN (default: next to FREQCCD_.EXE) |
| `--year YYYY` | narrows the Type B key search to the years around YYYY |
| `--key YYYY-MM` | Type B key date; skips the search |
| `--b-table MMYY` | `.INP` table for `\|B` packets; skips the table search |
| `--date YYYY-MM-DD` | recording date for listings (default: from the clock packets) |
| `--nabts 12,13` | also slice NABTS packets on these lines (4fsc captures only) |

### Capture formats

| `--format` | layout |
|---|---|
| `tbc` | ld-decode / vhs-decode VBI exports: unsigned 16-bit, 910 samples per line (4 x fsc), 16 lines per field from NTSC line 9 (`.tbc`, `.vbi`, `.y16`) |
| `flac` | the same samples packed as FLAC (decoded through ffmpeg) |
| `cap1440` | 8-bit capture-card VBI dumps, 1440 samples per line at 27 MHz, 12 lines per field from NTSC line 10 (bttv / saa713x `.vbi`) |

## What you need

Framing, the 7-bit packet layer, the clock and NABTS need nothing but numpy.

The guide content is encrypted (Type B messages) or scrambled with monthly tables (`|B` packets).
Instead of re-implementing Gemstar's routines, the decoder runs them from Gemstar's own DOS diagnostic
program **FREQCCD_.EXE** (the 1996 "VBI Data Monitor", 476,362 bytes) inside a CPU emulator
([Unicorn](https://www.unicorn-engine.org/)). Two copies of its install disk image are on the Internet
Archive; the program is identical in both, but the `.INP` tables differ.

| file | used for |
|---|---|
| `FREQCCD_.EXE` | Type B decryption (`Decrypt`), `\|B` descrambling (`Descramble`), SIP parsing and text decompression |
| `MMYY.INP` | monthly descramble tables for `\|B` packets (e.g. `1201.INP` = December 2001) |
| `DDPACKET.BIN` | the text-decompression dictionary (a saved type 0x40 message) |

None of these files are in this repository. Point the decoder at your copy. The routine addresses are
for that build of FREQCCD_.EXE; the decoder refuses a file of a different size.

The `.INP` tables that are known to survive cover Jan 2000 – Mar 2001 (1996 disk) and Nov 2001 –
Mar 2003 (2002 disk). Recordings from months without a table still decode everything except the long
`|B` packets.

## Physical layer

Gemstar 2X ([US5543852A](https://patents.google.com/patent/US5543852A)):

- **Bit rate** 2 x the CEA-608 rate: 64 bits per line period, about 1.007 Mbit/s
  (910/64 = 14.22 samples per bit at 4fsc; 1716/64 at 27 MHz).
- **Data line**: run-in `10101010` + frame code `10011101101` (19 bits: `1010101010011101101`), then
  32 payload bits = 4 bytes, each LSB first.
- Lines that carry no 2X frame are **1X** lines: the CEA-608 waveform with frame code `01000011` and
  two 7-bit + odd parity bytes.

The slicer measures the run-in phase with a single-bin DFT at the 2X clock's half rate, samples at bit
centers with linear interpolation, and thresholds against the run-in mean. Off-air and VHS captures
typically have a very clean eye.

Lines used in the captures tested: 15, 16, 17 and 18 (two lines at once in some cases, sometimes for
two different stations), and line 12 in 2007.

## Two logical channels on a 2X line

Each field phase (even / odd field index) is an independent channel. Which kind of data is on which
phase changes over time; it is not tied to field 1 / field 2.

1. **Type B blocks** (8-bit): 20 consecutive frames of one phase = 80 bytes starting with sync
   `05 FC 4D 61`.
2. **7-bit packets**: `07 | type … 0F chk`, each byte 7 bits + odd parity, XOR of all 7-bit values = 0.
   Type `00` (`07 | 00 } 0F 09`) is a keep-alive; type `B` packets carry the `|B` layer.

## Type B messages

Block (from FREQCCD's `TypeBBuffGroup` / `MsgBuffGroup`):

| bytes | |
|---|---|
| 0–3 | `05 FC 4D 61` sync (bit 0 of byte 3: 1 = 80-byte block, 0 = 48-byte block) |
| 4 | block number within the message |
| 5 | key byte (bit 7 selects current / next month inside `Make_Key`) |
| 6 | bit 7 month-parity flag; bits 0–6 checksum mode (0 = chained) |
| 7 | check: (sum of the decrypted payload) XOR chain == byte 7; in mode 0, chain ^= byte 7 after each block |
| 8–71 | 64-byte encrypted payload |
| 72–79 | 8 Reed–Solomon parity bytes |

Decryption is FREQCCD's `Decrypt` (13CD:0561): `Make_Key(year − 1980, month, block number, key byte)`
followed by four rounds of a hash and S-box. The key date is not sent, so the decoder tries every
(year, month) on up to 12 block-0s and keeps the one whose checks pass; the checks make a wrong
answer practically impossible. Blocks are then chained into messages and every block's check is
verified. Decrypted message: byte 0 = type, byte 1 = block count.

Before decryption each block goes through **Reed–Solomon RS(76,68)** (FREQCCD's `Syndrome4` / `ErrCorrect`):
GF(256) with field polynomial 0x11D and roots α¹–α⁸, over bytes 4–79. Those 76 bytes arrive as 19 four-byte
2X frames, and within each frame the bytes are taken last to first. Up to 4 bad bytes per block are corrected;
`summary.json` reports `rs_blocks_corrected` and `rs_blocks_uncorrectable`.

Which key date is in use:

- Usually the **current month** (Dec 2001, Feb 2003, Nov 2007).
- Near the end of a month the head-end moves to the **next month's** key: on 31 Dec 2001 it was already on
  January 2002, and 19 May 2003 uses June 2003 (`Decrypt(2003, 6)`).
- Around the change both keys can be in use at once. Byte 6 bit 7 of each message's first block gives the
  parity of its key month (1 = odd), so each message is decrypted with the found month or the adjacent month
  of the right parity.

## `|B` packets

`07 | B` + 2-character sequence + section letter + 2-character subtype + … + payload + `0F chk`.
The content after byte 13 is scrambled with the monthly `.INP` table: FREQCCD's `Descramble`
(0BAC:0168) picks the current or next month's table by a parity bit. Its checksum, (7-bit sum of the
content + byte 11) & 0x7F == 0, tells whether a table is right, so the decoder tries every table it is
given and keeps the clear winner: it must pass at least two long packets and a majority of them, and
score four times the runner-up. A capture with a single long packet cannot settle the table that way;
give it with `--b-table` if you know the month. Packets of 9 content bytes or fewer are sent in the clear.

Record types seen: `A11` channel lineups (2-character channel number, flag, 4-character call sign;
position = ((c0 & 0x3F) − 4) × 64 + (c1 & 0x3F)), `B03`/`B05` compact 3-byte records, `C01` small
control records, `D04` sparse tables, `E01`. The lineup tag after `A11` identifies the lineup.
Mapping the other record types to FREQCCD's packet classes (GHSPkt, TZPkt, ZCPkt, CMPkt…) is open.

## 1X lines and the clock packet

The 1X lines carry the same `07 … 0F <xor>` packets, plus the **clock**:

```
07 'z' MIN HOUR DAY MONTH WEEKDAY YEAR TZ CHK 0F XOR
```

Each field is `char & 0x3F` (characters are 0x40 + a 6-bit value); HOUR and MONTH use `& 0x1F`.
WEEKDAY is 1 = Sunday … 7 = Saturday, YEAR is the year − 1990, and TZ is the station's offset in hours west
of UTC, plus 32 while daylight saving is in effect (37 = Eastern daylight time, 6 = Central standard time).
CHK makes bytes 2–9 sum to 127 (mod 128). The time is UTC, one tick per minute, and matches program changes
on the tape. The decoder uses the clock's local date as the recording date when `--date` is not given.

## Message types

Byte 0 of a decrypted Type B message. FREQCCD (1996) knows types 0–12; later types were worked out from
the captures.

| type | meaning | status |
|---|---|---|
| 0x00 | **SIP** (Show Information Packet): one channel's programs | decoded with FREQCCD `SIP::Process` + `decomp` |
| 0x07 | DPPGS table | not parsed |
| 0x09 | sort / genre menu (old form of 0x26) | parsed |
| 0x10 | receiver UI and resource data; byte-identical 1999 – 2007 | not parsed |
| 0x11, 0x14 | per-lineup tables | not parsed |
| 0x12 | numbered per-lineup records (byte 10 = record number, byte 15 = lineup tag) | raw |
| 0x13 | **channel lineup** | parsed |
| 0x16 | object display trigger (2007) | raw |
| 0x1A | ad targeting / display rules | raw |
| 0x1B | **ad schedule** | parsed (time base unknown) |
| 0x1E | **graphics objects**: ad screens, detail pages, PNG screens | rendered |
| 0x26 | **genre tree** | parsed |
| 0x27 | service promo panel ("NewsGuide…") | strings |
| 0x28 | channel sort order per lineup | parsed |
| 0x3A | small control message | raw |
| 0x3D | file announcement (2007: `/system/patch.bin`, a receiver firmware patch) | strings |
| 0x40 | dynamic dictionary (DDPACKET.BIN is a saved copy; unchanged 1996 – 2001) | – |

In 2007 the messages carry a receiver-targeting expression (`C1 … 3F`) before the object header, so
`object_header()` finds the header by its closing `3F xx 00 0A` rather than by a fixed offset.

### SIP listings (0x00)

Bytes 1–2 (little-endian) = declared message length; bytes 4–7 = VCR Plus+ code; byte 8 = source id.
FREQCCD's SIP class yields, per program, the guide channel, date, start and end (minutes), category, and
three compressed strings (title; genre/year/stars/cast/rating line; description). Text compression is a
digraph table plus case/space control codes and the dynamic dictionary.

- **Category** = genre tree code: high nibble = group (0x10 Movies, 0x20 Sports, 0x30 Children,
  0x40 Educational, 0x50 News, 0x60 Variety, 0x70 Series), full byte = subgenre. 0 = none.
- **Guide channel** is the VCR Plus+ guide channel, not a lineup position, and the feed carries several
  markets, so one number can mean different stations. Mapping it to call signs is open.
- A message shorter than its declared length lost blocks on tape; `decomp` then runs off the end and pads
  with `&`. Such rows are marked `complete: false` and their text cut to ` [...]`. A lone `&` is the
  feed's normal "no text" marker.
- The date is a day code completed from the recording date (`--date`, or the clock packets).
- Each program record starts with 4 bytes: start (low 6 bits × 5 minutes from the message's base time;
  bit 7 is set on every program after the first), duration (low 7 bits × 5 minutes), category, and a
  **flag byte** that FREQCCD reads with `SIP::GetMisc` (152F:11A9) and prints only as `Misc=%02Xh`.
  The bit meanings below were worked out from 2,030 listings in the 2001-12-12 capture; the decoder writes
  them as `closed_captioned`, `stereo`, `sap`, `live` and `misc_raw`, and `listings.txt` shows them as
  `[CC Stereo SAP Live]`.

  | bit | meaning | evidence |
  |---|---|---|
  | 7 (0x80) | closed captioned | off for ~90% of Paid Programming; on for I Love Lucy, M\*A\*S\*H, Seinfeld |
  | 6 (0x40) | stereo | set on 3 of ~70 pre-1980 films; on for current sitcoms and recent films |
  | 5 (0x20) | SAP (probable) | 23 listings, mostly PBS (Nova, Arthur, Between the Lions) and some network prime time |
  | 4 (0x10) | live | sports events, SportsCenter, live religious shows; taped *Inside the NFL* is off |
  | 0–3 | always 0 so far | |

### Lineups (0x13)

```
… b[2:4] lineup id, b[5] 0 analog / 1 digital, b[12] = n, b[13:13+n] home station, tag, count,
then entries [position][network id][len][name]
```

Network id 1 ABC, 2 CBS, 3 NBC, 4 FOX, 5 PBS; cable networks keep one id across markets.

### Genre tree (0x26 / 0x09)

From byte 14 (0x26) or 6 (0x09): `code len name count` then `count` × `code len name`.

## Graphics objects (0x1E)

Each 0x1E message is one part of an object. After the header (`… 3F xx 00 0A`):

```
id(2) width(2) height(2) 80 total_length(3) part_length+7(2) 0B id(2) 00 offset(2) 00  data…
```

Byte 5 of the message = part number, byte 6 = part count. Width, height and all coordinates are
transposed as sent (y before x). In 2003, ads come in threes: **N** = picture (168 wide), **N+1** = stub,
**N+2** = the "Click for More" detail page (352 × 390).

Command stream:

| command | |
|---|---|
| `07 nn c1 c2 c3 00 00 00` | color pair nn: two 12-bit RGB colors (foreground, background) |
| `06 nn 09 y0 x0 y1 x1` | fill with pair nn's background |
| `06 nn 05 flag y x len data` | bitmap |
| `02 style w1 w2 [y0 x0 y1 x1 \| FFFF] len text` | text element (no box: y = w1, w2 & 0x8000 = centered) |

Text: `ESC V`…`ESC W` bold, `ESC C`…`ESC D` italic; 0x80 ℠, 0x81 ®, 0x83 ™, 0x85 remote-button glyph,
0x86 bullet, 0x88 ►, 0xA0 no-break space, 0xA3 ¢, 0xAE em dash. The renderer substitutes a system font,
so typography is approximate but the text is exact.

Bitmap data: byte 0 bits 0–1 = depth (0 = 1 bpp, 2 = 4 bpp); byte 1 high nibble = colors − 1, low
nibble = palette base (pixel v uses entry v − base); then the 12-bit palette; 1 bpp adds
`[bytes per row][rows]`. Pixels are run-length coded: c < L copies c + 1 literal bytes, otherwise the
next byte repeats (c & 0x7F) + 3 times. Usually L = 0x40, the first 64 decoded bytes are a preamble,
and 1 bpp rows are XOR-delta coded (each row XORed with the one above).

**Heuristics**: the flags that select L (0x40 / 0x80), the preamble (64 / 0) and XOR / raw rows are
not known. The renderer picks per bitmap whichever reproduces the declared size, fits the layout, and
gives continuous images, and records each choice in `index.json`. About 18 of 25 ad screens in the
May 2003 capture render fully clean.

From 2007 a bitmap command can carry a **PNG** file instead; it is extracted byte for byte (chunk
CRCs checked).

0x1B ad schedule: after the header, a time (4 bytes) at +10, the three object ids at +15, name length at
+27 and the name, then `7F 01 count 12` and count × `01 time32 00` slots. The time base is unknown.

## NABTS

Some captures carry NABTS (EIA-516) on other lines, e.g. lines 12–13 of the May 2003 recording:
5.727 Mbit/s = 2.5 samples per bit at 4fsc, packet `55 55 E7` + 5 Hamming-8/4 header bytes (12-bit
address, continuity index, packet structure) + 28 data bytes. VHS blur is removed with a 13-tap linear
equalizer per line, trained on the 24 known header bits and then decision-directed. The datacast seen
(address 0x253) is continuous, high-entropy and unidentified; `--nabts` dumps the packets.

## Nova2 and `.dbm` databases

`tools/nova2/nova2_dump.py` dumps a Guide Plus+ database (`.dbm`) with **Nova2** (`nova2.0.13.0_pc.exe`),
Gemstar's Win32 receiver simulator, under emulation. Nova2 still has its COFF symbol table, which the
script reads from the user's copy at runtime to find the routines by name.

```
pip install unicorn pefile pillow
python3 tools/nova2/nova2_dump.py listings latest.dbm out/ --exe nova2.0.13.0_pc.exe
python3 tools/nova2/nova2_dump.py logos    latest.dbm out/logos --exe nova2.0.13.0_pc.exe
```

DBM (big-endian): +0 file size, +8 build time, +16 schedule start, +20 span (seconds). Per station a
24-bit offset to a start time followed by slot records (flags: bits 0–4 duration code, 0x1F = explicit
minutes; 0x80 show id follows; 0x40 second id; 0x20 extra fields; varint values). Text is Huffman
coded. Channel logos are **TransMaps**: byte 0 = type (bits 6–7) | color count, byte 1 = width / 2,
byte 2 = height / 2, 15-bit RGB palette, then compressed pixels.

## Captures tested

| recording | format, line | key / table | decoded |
|---|---|---|---|
| 1997-10-31 | cap1440, 16 | no Type B; `\|B` table not held | clock, raw `\|B` packets |
| 1999-12-31, FOX Family | cap1440, 17 + 18 | 2000-01 / `100.INP` | nearly idle feed; 0x10 |
| 2001-12-12 | cap1440, 17 | 2001-12 / `1201.INP` | 1,999 programs, 2 lineups, genres |
| 2001-12-31, ABC Family | cap1440, 17 | 2002-01 / `102.INP` | 57 programs |
| 2003-02-09, WLUK | cap1440, 16 | 2003-02 / `203.INP` | lineups (analog + digital), 0x12 records |
| 2003-05-19, WHAS | flac, 16 | 2003-06 / none held | lineups, 25 ad screens + detail pages, ad schedule, NABTS |
| 2001-11-07, WPVI + WTXF | tbc, 15 + 16 | 2001-11 / `1101.INP` | lineups |
| 2007-11-13, WQED | tbc, 12 | 2007-11 / none held | firmware-update PNG screen, file announcement |

Type B blocks first appear on a September 1998 capture; earlier feeds (1997 to mid-1998) carry only `|B`
packets, which need the `.INP` table for their month.

`tests/test_gemstar.py` builds synthetic signals and messages for every layer. To run the end-to-end
test on your own files, set `GEMSTAR_FREQCCD` and `GEMSTAR_CAPTURE`.

## Open questions

- The bitmap flags (run-length limit, preamble, XOR rows).
- The 0x1B time base, and message types 0x10, 0x11, 0x12, 0x14, 0x1A, 0x3A.
- `|B` record types other than A11, and the FREQCCD classes they belong to.
- Mapping SIP guide channels to stations.
- Confirming the SIP flag byte's bit 5 (SAP) against a capture with known second-audio programs.
- `.INP` tables for 1999, mid-2001 and from April 2003 on.
