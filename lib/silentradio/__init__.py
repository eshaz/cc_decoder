#!/usr/local/bin/python
# coding: utf-8
""" Silent Radio, the LED sign feed on VBI line 18 of the 1992 KCET capture

Silent Radio Inc's decoder patent, US4620227A, finds its data by a pre-data code of
`101010101000011` - nine digits of run-in, four of blanking, a two digit start - each
digit half of one of the sixteen 32x line rate data bits after it. The KCET line sends
the same fifteen digits as bits, and its data bits after them, at 80 times the line rate,
most significant bit first. Two bytes a field make one stream of records, sent round a
carousel so a sign that missed one picks it up on the next pass.

The package: `line` slices the line, `records` takes records from the stream and makes
stories of them, `content` reads what they carry, `sign` plays a message on a model of the
sign's firmware from the fonts and templates in `table`, `scene` draws what it plays as an
SVG, `polar` turns the keyed swings of the messages that have one into it, and `page` writes
the page of every story with, beside it, each story's and channel's SVG and player. Here is the
format's loop. See docs/silentradio.md.

Public domain / Unlicense, as with the rest of ccDecoder.
"""

from collections import Counter, defaultdict
from multiprocessing import Pool, current_process

from setproctitle import setproctitle

from lib.cc_decode import get_output_function, scc_timecode
from lib.silentradio.content import LEVELS_KIND, MESSAGE_KIND, describe_record, displayed, parse_body, render, sign_checksum
from lib.silentradio.line import LINE_SILENT_RADIO, decode_silent_radio_row
from lib.silentradio.page import write_silent_radio_html
from lib.silentradio.polar import polar_scene
from lib.silentradio.records import (LOOP_START_BYTES, PART_KIND, _bigrams, _checked, _compose, _derived, _mended,
                                     _patterned, _realigned, _repaired, _sending, _stories, _voted,
                                     take_silent_radio_records, vote)
from lib.silentradio.scene import scene_of
from lib.silentradio.sign import _played, _received, _shown, sign_frames

def _drawn(play):
    """ `(program, by, scene, received)`: a message's program, in colour, and who played it,
    as `_played` gives them, the model's run of it as the layers its SVG draws with its keyed
    swing where it has one, and the payload the sign received """
    payload, ops = _received(*play), []
    frames, end = sign_frames(payload, ops)
    shown = _shown(frames, end, hues=True)
    return _played(payload, model=(shown, end), hues=True) + (
        polar_scene(scene_of(payload, (frames, end, ops, shown)), payload, (frames, end)), payload)

def _programs(plays):
    """ Each message's `(program, by, scene, received)` as `_drawn` gives them, in parallel,
    `plays` its `(payload, lost)` or None for one with nothing to draw """
    if not any(plays):
        return [([], None, None, b'') for _ in plays]
    with Pool() as pool:
        drawn = iter(pool.map(_drawn, [play for play in plays if play]))
    return [next(drawn) if play else ([], None, None, b'') for play in plays]

def decode_silent_radio(rx, output_filename, options):
    """ Decode the Silent Radio sign feed carried on its own VBI line

    Records are written to the log as they complete, each repeat on a line of its own;
    the page at the end holds each message once, voted across every copy received.
    """
    setproctitle(current_process().name)

    hits = Counter()
    buffer = bytearray()
    lost = set()
    copies = defaultdict(list)
    trailers = defaultdict(list)
    first = {}
    frame = 0

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
        decoded = {row_num: (byte1, byte2) for row_num, byte1, byte2, _, _ in rows
                   if byte1 is not None}
        hits.update(decoded.keys())

        # the feed is one line on every tape, so a stray match on another row is left out;
        # a field it did not slice on leaves two unknown bytes, so the record they fall in
        # keeps its length and a later copy can fill them in
        line = max(hits, key=hits.get, default=None)
        if line is not None:
            if line not in decoded:
                lost.update((len(buffer), len(buffer) + 1))
            buffer += bytes(decoded.get(line, (0, 0)))

        records, consumed = take_silent_radio_records(bytes(buffer), lost)
        if consumed:
            del buffer[:consumed]
            lost = {position - consumed for position in lost if position >= consumed}

        for number, body, damaged, trailer in records:
            key = (number, len(body))
            copies[key].append(body)
            trailers[key].append(trailer)
            if out_func is None:
                out_func, f = get_output_function('silentradio', output_filename)
            if key in first:
                out_func('%s  Repeat %04x  copy %d%s' % (
                    scc_timecode(frame), number, len(copies[key]), ', damaged' if damaged else ''))
                continue
            first[key] = scc_timecode(frame)
            for entry in describe_record(number, vote([_derived(number, body)]), vote([trailer])):
                out_func('%s  %s' % (scc_timecode(frame), entry))

    for key in copies:
        copies[key], trailers[key] = _realigned(copies[key], trailers[key])
    # the messages in the order they first went out, and each channel's loop marks: a record of
    # FF alone where the carousel starts it again, and 00 0A n where its part n begins; and the
    # first 01 02 byte each channel's signs were sent, by its groups
    sent, marks, levels = [], [], {}
    for key, when in first.items():
        body = vote([_derived(key[0], _voted(copies[key]))])
        parsed = parse_body(body, vote(trailers[key]))
        if parsed is None:
            if len(body) == LOOP_START_BYTES and body[-1] == 0xff:
                marks.append((len(sent), bytes(body[4:9]), 0, when))
        elif displayed(parsed[2]) is not None:
            sent.append((when, key, parsed))
        elif parsed[2][:2] == PART_KIND and len(parsed[2]) == 3:
            marks.append((len(sent), parsed[1], parsed[2][2], when))
        elif parsed[2][:2] == LEVELS_KIND and len(parsed[2]) == 3 and len(parsed[1]) > 2:
            levels.setdefault(parsed[1][1:3], parsed[2][2])
    kept, plays = [], []
    stories = _stories(sent, copies, marks)
    composed = [_compose([_sending(copies, *sent[index][1:]) for index, _ in story]) for story in stories]
    bigrams = _bigrams(composed)
    for story, voted, repaired in zip(stories, composed, _repaired(composed)):
        sendings = [sent[index] for index, _ in story]
        mended, restored = _mended(repaired)
        mended, patterned = _patterned(mended)
        # where the story fails its trailer, what the checksum makes of the values its copies
        # received and its repairs replaced, and of the bytes none received; the latest sending's
        # trailer first
        each = [_sending(copies, key, parsed) for _, key, parsed in sendings]
        others = {}
        for n, value in enumerate(mended):
            seen = {copy[n] for sending in each for copy in sending if copy[n] is not None}
            if value is not None and seen - {value}:
                others[n] = sorted(seen - {value})
        checked = list(dict.fromkeys(trailer[0] | trailer[1] << 8 for trailer in (
            _voted(trailers[key]) for _, key, _ in sendings[::-1]) if None not in trailer))
        filled = [n for n, value in enumerate(voted) if value is None and repaired[n] is not None]
        before = mended
        mended, settled = _checked(mended, checked, others, bigrams, filled)
        filled = sum(1 for n in filled if mended[n] == before[n])
        payload = bytes(value or 0 for value in mended)
        # whether a sending's trailer is the checksum of the story as it stands
        inner = sign_checksum(bytes([len(payload) & 0xff, len(payload) >> 8]) + payload)
        checks = None not in mended and inner in checked
        left_out = {n for n, value in enumerate(mended) if value is None}
        unknown = voted.count(None)
        when, key, parsed = sendings[0]
        # WTTW's clock pages have nothing to draw but a colon: the sign drew its own clock
        nothing = payload[:2] == MESSAGE_KIND and not render(payload[4:])
        kept.append((when, key[0], parsed[1], sum(len(copies[key]) for _, key, _ in sendings),
                     displayed(payload), payload, [(index, sent[index][0], sent[index][1][0], n) for index, n in story],
                     (unknown, filled, restored, patterned, checks, settled)))
        plays.append(None if nothing else (payload, left_out))
    # each story with the payload the sign plays of it, who played it, its sendings, how many of
    # its bytes no copy received, how many of those the capture's context filled in, how many of its
    # graphic columns got their top bit back and bytes their pattern put back, whether its
    # checksum checks and how many bytes the checksum set, and its layers
    messages = [(when, number, address, count, items, program, payload, received, by, sendings, counts, scene)
                for (when, number, address, count, items, payload, sendings, counts), (program, by, scene, received)
                in zip(kept, _programs(plays))]

    if out_func is not None:
        out_func('')
        out_func('%d records, %d messages, %d copies received; %d messages check their trailer' % (
            len(first), len(messages), sum(len(c) for c in copies.values()),
            sum(1 for message in messages if message[10][4])))
    if f is not None:
        f.close()

    if messages:
        write_silent_radio_html(output_filename, messages, marks, levels)
