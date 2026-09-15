Silent Radio text service
=========================

A wire news feed carried in the CEA-608 Text service, found on the 1992 KCET capture
attached to [issue #3](https://github.com/eshaz/cc_decoder/issues/3). It needs no new
format: `--ccformat text` reads it, and writes it to `out.T1.txt` like any other text
channel. What it needed was one fix to the text-mode cursor, described below.

What it is
----------

Silent Radio fed scrolling LED signs from the VBI with news, sport and market copy. Its
decoder patent [US4620227A](https://patents.google.com/patent/US4620227A/en) (Cybernetic
Data Products, 1986) specifies the CEA-608 waveform with line-agnostic capture,
"regardless of the horizontal line" - the ordinary caption waveform and byte layer
carrying something that is not captions.

**A caveat on the name.** What is established is the bearer, the framing and the
content: a CEA-608 text service carrying Associated Press copy. EIA-608 reserves T1 and
T2 for exactly this kind of service and other operators used them too, so Silent Radio is
the service this matches rather than an identification proved from the bitstream. Nothing
in the data names its operator.

How it sits next to the other two services
-------------------------------------------

| | CEA-608 captions | Silent Radio text | [StarSight](starsight.md) |
|---|---|---|---|
| eighth bit | odd parity | odd parity | data |
| distinguished by | - | Resume Text Display | parity failure |
| payload | programme captions | wire copy | binary packets |

StarSight separates easily because it spends the eighth bit on data and so fails CEA-608
parity at chance. This service **passes parity**, because it *is* CEA-608. What marks it
out is the control code: a text service asserts `Resume Text Display` constantly to hold
the mode, while a caption line sends it rarely or never. In the 1992 capture it accounts
for 4,592 control codes against 1,462 Roll-Up.

It is also **multiplexed with real captions on the same channel** - the encoder alternates
text mode with roll-up, so one CEA-608 channel carries a captioned programme and a text
service at once. That part the caption path already handled.

The fix: the cursor reset has to wait
--------------------------------------

Lines are **retransmitted, not streamed**. A partial line is sent, then sent again from
the start with more text on it, so a receiver that tunes in mid-line or drops data still
ends up with the whole thing. Read as a stream the bytes give `researchresearch` and
`sitesite of the GOP conventisite osite of th`. A real decoder has a cursor, and an
indent preamble address code moves it back to the first column so the retransmission
overwrites what it already wrote. ANSI-CEA-608-E Annex D.3, Text-Mode Multiplexing,
describes this as TeleCaption I compatibility, and `TextCaptionTrack` already implemented
it by clearing the line buffer whenever an indent arrived.

The catch is where the indents actually fall. Every line is framed like this:

```
<RTD><RTD><PAC indent col 0>x2  lack of funding for AIDS  <RTD><RTD><PAC indent col 0>x2 <CR><CR>
```

There is an indent *before the text*, which is the one that means "overwrite", and
another **immediately before the carriage return that commits the line**. Clearing on
sight threw the line away a moment before it was written, which is why the service
produced a `T1.txt` of nothing but blank lines despite the line being full of readable
English.

So the reset waits for a character to actually arrive: an indent is remembered, and the
buffer is cleared when the next printable character follows it. An indent followed by a
carriage return now commits the line it was meant to commit. That is the whole change -
about eight lines in `TextCaptionTrack`, and `out.T1.txt` for the existing 1998 captures
is byte identical either way.

What comes out
--------------

From the 1992 KCET capture - 73,872 fields, 20.5 minutes, VBI line 21 field 1 only, 100%
odd parity - **849 lines of Associated Press copy in six stories**: MARKETS, PHONE TALKS,
RETIREE BENEFITS, REFUGEES KILLED, TROPICAL STORM and SPORTS, datelined TOKYO,
WASHINGTON, NEW YORK, MIAMI, CHICAGO and HOUSTON, with a byline of `Summary by FRANK
ELTMAN`. Lines wrap at 29 columns or fewer.

```
  MARKETS
  TOKYO (AP)  The dollar
opened lower against the
Japanese yen Tuesday, while
share prices on the Tokyo
Stock Exchange fell
```

The capture dates itself: the captions running alongside the feed cover the Republican
convention at the Houston Astrodome, which places it in August 1992.

Decoding a capture
------------------

The 1992 sample is a Bt8x8 frame grabber dump - **2048 samples per line, 8 bit** - not the
4fsc 16 bit TBC geometry the KET captures use, so the ffmpeg parameters differ:

```
ffmpeg -v error -i KCET_1992-xx-xx_bt8x8_2048x16.vbi.flac -f u8 - \
 | ffmpeg -hide_banner -f rawvideo -pixel_format gray -framerate 59.94 -video_size 2048x16 -i - \
     -vf "weave,crop=1530:16:30:16,scale=interl=1,scale=720:16" \
     -c:v ffv1 -f matroska pipe:1 \
 | cc_decoder.py --start_line 0 --end_line 15 --ccformat text,srt - -o out
```

`-f u8` rather than `-f u16le`, and `-pixel_format gray` rather than `y16`, because the
capture card is 8 bit.

**The crop width is the parameter that matters, and it is not arithmetic.** It sets
samples-per-bit once the line is scaled to 720, so getting it wrong puts the matched
filter off the bit clock and nothing decodes at all - which is what happens for every
width derived by scaling the 910-sample geometry. 1530 was found by sweeping the width
against the preamble correlation score, which peaks at 0.75, level with the 0.77 the KET
captures reach. The offset of 16 selects the weaved rows holding VBI line 21.

What is left
------------

**The first line of a capture.** Before the first indent arrives there is no cursor
reference, so text-mode and caption characters that interleave at the very start land in
one buffer and come out mixed. It costs one line and clears itself as soon as the service
sends its first indent.

**Which operator this was.** The stream carries no service identification - no header, no
call sign, nothing naming a provider.

**The other captures.** Only the 1992 KCET capture carries a text service; the 1994 KCET
capture is StarSight, and the three 1998 KET captures are StarSight plus ordinary
captions.
