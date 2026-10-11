#!/usr/local/bin/python
# coding: utf-8
""" The carousel's records: taken from the byte stream, voted across their copies, gathered
into stories across a loop's passes, the bytes no copy received filled from the rest of the
capture, and sorted into channels and their loops

Public domain / Unlicense, as with the rest of ccDecoder.
"""

import difflib
import itertools
from collections import Counter, defaultdict

import numpy as np

from lib.silentradio.content import GRAPHIC_MIN_COLUMNS, elements, sign_checksum
from lib.silentradio.table import SIGN_PRESETS

# A record is six zero bytes and 0xFF, a 16 bit record number, a 16 bit length, that many
# bytes of body and two more after it, the trailer. Nothing on the tape runs longer than
# 1,075. A sync that lost some of its zeros is still one where three and FF stand before a
# header whose word checks: KCET's 9F40 comes with four.
SILENT_RADIO_SYNC = b'\x00' * 6 + b'\xff'
SILENT_RADIO_SHORT_SYNC = b'\x00' * 3 + b'\xff'
SILENT_RADIO_MAX_RECORD = 4096

def _next_sync(data, offset):
    """ `(start, head)` of the next sync from `offset`, a whole one or a short one before a
    header that checks; head None where the bytes to tell have yet to come; None if none """
    whole = data.find(SILENT_RADIO_SYNC, offset)
    stop = len(data) if whole < 0 else whole
    at = data.find(SILENT_RADIO_SHORT_SYNC, offset, stop)
    while at >= 0:
        head = at + len(SILENT_RADIO_SHORT_SYNC)
        if head + 8 > len(data):
            return at, None
        if data[head + 4] == 0 and sign_checksum(data[head:head + 6]) == data[head + 6] | data[head + 7] << 8:
            return at, head
        at = data.find(SILENT_RADIO_SHORT_SYNC, at + 1, stop)
    return None if whole < 0 else (whole, whole + len(SILENT_RADIO_SYNC))

def take_silent_radio_records(data, lost=()):
    """ `([(record number, body, damaged, trailer)], bytes consumed)` from the front of `data`

    `lost` holds the offsets of bytes whose line never sliced; a record that covers one is
    returned `damaged`, with those bytes as None, so another copy can stand in for them.
    """
    records = []
    offset = 0
    while True:
        found = _next_sync(data, offset)
        if found is None:
            # keep a tail that could still be the start of a sync
            return records, max(offset, len(data) - len(SILENT_RADIO_SYNC) + 1)
        at, head = found
        if head is None or head + 4 > len(data):
            return records, at
        number = data[head] << 8 | data[head + 1]
        length = data[head + 2] << 8 | data[head + 3]
        if length > SILENT_RADIO_MAX_RECORD:
            offset = at + 1
            continue
        end = head + 4 + length + 2
        if end > len(data):
            return records, at
        # a sync inside the record means bytes were lost ahead of it; start again there
        inside = data.find(SILENT_RADIO_SYNC, head, end)
        if inside >= 0:
            offset = inside
            continue
        body = [None if position in lost else data[position]
                for position in range(head + 4, head + 4 + length)]
        trailer = [None if position in lost else data[position] for position in (end - 2, end - 1)]
        records.append((number, body, None in body, trailer))
        offset = end

def _voted(copies):
    """ The bytes most copies agree on, ignoring those a copy never received, and None where
    no copy received one """
    return [Counter(known).most_common(1)[0][0] if known else None
            for known in ([value for value in column if value is not None] for column in zip(*copies))]

def vote(copies):
    """ As `_voted`, 0 where no copy received a byte """
    return bytes(0 if value is None else value for value in _voted(copies))

# A record's own checks fix some of its bytes: the word is the checksum of the number, length,
# 00 and class; the address's check follows its first class bytes, and the two bytes of the
# field a record numbered with bit 14 carries after them; and the inner length counts the
# bytes after it, where the record isn't the ten-byte FF
RECORD_FIELD_FLAG = 0x4000

def _derived(number, body):
    """ `body`, None where no copy received a byte, with the bytes its other bytes fix put back """
    body = list(body)
    head = [number >> 8, number & 0xff, len(body) >> 8, len(body) & 0xff]
    if len(body) >= 4 and None not in body[:2] and None in body[2:4]:
        word = sign_checksum(bytes(head + body[:2]))
        body[2:4] = [word & 0xff, word >> 8]
    if len(body) < 2 or body[1] is None:
        return body
    count = body[1] + (2 if number & RECORD_FIELD_FLAG else 0)
    at = 4 + count
    if at + 2 <= len(body) and None not in body[4:at] and None in body[at:at + 2]:
        check = sign_checksum(bytes(body[4:at]))
        body[at:at + 2] = [check & 0xff, check >> 8]
    inner = len(body) - at - 4
    if inner >= 0 and at + 4 <= len(body) and None in body[at + 2:at + 4]:
        body[at + 2:at + 4] = [inner & 0xff, inner >> 8]
    return body

# A field the capture dropped whole, rather than one it received and couldn't slice, puts every
# byte of a copy after it that many places early, and the copy reads the next sync's zeros as its
# trailer: KCET's 9F41. A copy whose bytes from where it first differs from another are the
# other's SLIP_BYTES on is put back in step, the bytes it dropped unknown.
SLIP_BYTES = (2, 4)
SLIP_LEAST = 8

def _realigned(copies, trailers):
    """ `(copies, trailers)` of one record, each copy that dropped a field or two put back in step
    with another, its trailer the two bytes it read as its body's last """
    copies, trailers = [list(copy) for copy in copies], [list(trailer) for trailer in trailers]
    for i, copy in enumerate(copies):
        for other in copies[:i] + copies[i + 1:]:
            differ = [n for n, (a, b) in enumerate(zip(copy, other)) if None not in (a, b) and a != b]
            if len(differ) < SLIP_LEAST:
                continue
            start = differ[0]
            slip = next((slip for slip in SLIP_BYTES if _same_story(
                copy[start:len(copy) - slip], other[start + slip:])), None)
            if slip:
                trailers[i] = copy[len(copy) - slip:len(copy) - slip + 2]
                copies[i] = copy[:start] + [None] * slip + copy[start:len(copy) - slip]
                break
    return copies, trailers

def _sending(copies, key, parsed):
    """ Each copy of the record `key`, the message `parsed`, from its payload on """
    return [copy[len(copy) - len(parsed[2]):] for copy in copies[key]]

# A channel's loop sends each story again on every pass, under a new record number: the same
# length, and the bytes both sendings received the same but for a slicing error or two. The
# sendings of one story voted together repair what any of them lost.
SAME_STORY = 0.95

def _same_story(a, b):
    """ Whether two sendings' payloads, None where unreceived, are one story """
    both = [x == y for x, y in zip(a, b) if x is not None and y is not None]
    return len(a) == len(b) and bool(both) and sum(both) >= SAME_STORY * len(both)

def _compose(sendings):
    """ One story's payload from each of its sendings' copies, oldest first: the latest
    sending's bytes, its copies voted, and where they tie or none received a byte the latest
    sending whose copies settle it; None where none received it """
    out = []
    for n in range(len(sendings[-1][0])):
        votes = [Counter(copy[n] for copy in copies if copy[n] is not None).most_common(2) for copies in sendings]
        clear = [vote[0][0] for vote in votes if vote and (len(vote) == 1 or vote[0][1] > vote[1][1])]
        tied = [vote[0][0] for vote in votes if vote]
        out.append(clear[-1] if clear else tied[-1] if tied else None)
    return out

# Bytes no copy of any sending received are filled from where the same bytes stand either side
# of them elsewhere in the capture, or in the message itself: the filling matched furthest,
# then most often in the message, then in the capture, and none if another is as good. In
# text, where a word's ending alone would pick one, it takes REPAIR_TEXT_SIDE bytes matched
# each side or REPAIR_CONTEXT in all.
REPAIR_CONTEXT = 12
REPAIR_LEAST = 3
REPAIR_TEXT_SIDE = 3

def _fillings(payloads, k, start, length, pairs):
    """ `[(filling, left matched, right matched, in payload k, in all)]` for the `length` bytes
    payload k lost at `start`, best first """
    payload = payloads[k]
    side = lambda at, step: [payload[n] for n in itertools.takewhile(
        lambda n: 0 <= n < len(payload) and payload[n] is not None, range(at, at + step * REPAIR_CONTEXT, step))]
    left, right = side(start - 1, -1), side(start + length, 1)
    places = set()
    if len(right) > 1:
        places |= {(other, at - length) for other, at in pairs.get((right[0], right[1]), ())}
    if len(left) > 1:
        places |= {(other, at + 2) for other, at in pairs.get((left[1], left[0]), ())}
    found = {}
    for other, at in places:
        source = payloads[other]
        filling = tuple(source[at:at + length]) if 0 <= at and at + length <= len(source) else (None,)
        if None in filling or (other, at) == (k, start):
            continue
        a = sum(1 for _ in itertools.takewhile(lambda n: at - 1 - n >= 0 and source[at - 1 - n] == left[n],
                                               range(len(left))))
        b = sum(1 for _ in itertools.takewhile(lambda n: at + length + n < len(source) and
                                               source[at + length + n] == right[n], range(len(right))))
        if a and b:
            best = found.setdefault(filling, [0, 0, 0, 0])
            if a + b > best[0] + best[1]:
                best[:2] = a, b
            best[2] += other == k
            best[3] += 1
    return sorted(((filling,) + tuple(got) for filling, got in found.items()),
                  key=lambda got: (-got[1] - got[2], -got[3], -got[4]))

def _repaired(payloads):
    """ Each payload, None where lost, with what the capture shows of its lost bytes filled in """
    payloads = [list(payload) for payload in payloads]
    for _ in range(2):
        pairs = defaultdict(list)
        for k, payload in enumerate(payloads):
            for at in range(len(payload) - 1):
                if payload[at] is not None and payload[at + 1] is not None:
                    pairs[payload[at], payload[at + 1]].append((k, at))
        for k, payload in enumerate(payloads):
            lost = [n for n, value in enumerate(payload) if value is None]
            runs = [[n for _, n in run] for _, run in itertools.groupby(enumerate(lost), lambda item: item[1] - item[0])]
            for start, length in ((run[0], len(run)) for run in runs):
                got = _fillings(payloads, k, start, length, pairs)
                if not got:
                    continue
                filling, a, b, mine, count = got[0]
                text = all(0 <= n < len(payload) and payload[n] is not None and 0x20 <= payload[n] < 0x7f
                           for n in (start - 1, start + length))
                if (a + b < REPAIR_LEAST or any(other[1] + other[2] == a + b and other[3:] == (mine, count)
                                                for other in got[1:]) or
                        text and min(a, b) < REPAIR_TEXT_SIDE and a + b < REPAIR_CONTEXT):
                    continue
                payload[start:start + length] = filling
    return payloads

# A graphic column's first byte has its top bit set, and a slicing error that clears it has the
# router read it as a command or a character, and each column after it a byte out of step:
# WTTW's SPORTS REPORT loses 9E so, and its pictures after it come out garbled. Where reading
# the byte so comes back to columns an odd number of bytes on, and reading it as a column keeps
# GRAPHIC_MIN_COLUMNS more in step, it gets its top bit back - as does 00 and a byte between
# two columns, which is how the text finds WTTW's gavel. A command read in step, as KCET's
# 07 00 between two graphics is, stays one.
ROUTER_PARAMETERS = {0x00: 1, 0x04: 3, 0x05: 6, 0x06: 1, 0x07: 1, 0x0a: 1, 0x1c: 2, 0x1d: 1, 0x1f: 1}

def _in_step(payload, at, end):
    """ How many columns follow from `at` in step, their first bytes set and none lost """
    count = 0
    while at + 1 < end and None not in payload[at:at + 2] and payload[at] >= 0x80:
        count, at = count + 1, at + 2
    return count

def _mended(payload):
    """ `(payload, count)`: `payload`, None where lost, with each graphic column that lost its
    top bit given it back, and how many did """
    payload, count, i = list(payload), 0, 0
    while i + 3 < len(payload) and payload[i] == 0 and payload[i + 1] == 0x0c and None not in payload[i + 2:i + 4]:
        end = min(len(payload), i + 4 + (payload[i + 2] | payload[i + 3] << 8))
        pairs, line, columns, at = [0, 0], 0, 0, i + 4
        while at < end:
            byte = payload[at]
            if byte is None:
                pairs, columns, at = [0, 0], 0, at + 1
                continue
            if pairs[line] == 2:
                pairs[line], at = 1, at + 1
                continue
            if pairs[line] and byte < 0x80 and columns and at + 1 < end and payload[at + 1] is not None:
                # read as commands and text, where does the stream come back to columns
                back = at
                while back < end and payload[back] is not None and payload[back] < 0x80 and \
                        payload[back] not in (0x01, 0x02, 0x08, 0x09) and payload[back] not in SIGN_PRESETS:
                    back += 1 + ROUTER_PARAMETERS.get(payload[back], 0)
                out_of_step = back < end and payload[back] is not None and payload[back] >= 0x80 and (back - at) % 2
                if (out_of_step or byte == 0 and back == at + 2) and _in_step(payload, at + 2, end) >= (
                        GRAPHIC_MIN_COLUMNS if out_of_step else 1):
                    payload[at] |= 0x80
                    count += 1
                    byte = payload[at]
            if pairs[line] and byte >= 0x80:
                pairs[line], columns, at = 2, columns + 1, at + 1
                continue
            columns = 0
            if byte in (0x01, 0x02):
                line = byte - 1
            elif byte in (0x08, 0x09):
                pairs[line] = byte == 0x08
            elif byte in SIGN_PRESETS:
                pairs, line = [0, 0], 0
            at += 1 + ROUTER_PARAMETERS.get(byte, 0)
        i = end
    return payload, count

# Presentation codes this ROM faults on. One in a run of elements of one code, opening on the
# same line, is that code received wrong: WTTW's 89E8 has its W-Sox row come as 00 among the 10s
# of the rest of the standings. And a 1F 3E or 1F 3D where the message's other steps put 1F 3F
# before the same bytes is that 3F: a step of BEIJING's typing.
FAULTING_CODES = (0x00, 0x01, 0x02, 0x03, 0x0a, 0x0b, 0x0d, 0x0e)

def _patterned(payload):
    """ `(payload, count)`: `payload`, None where lost, with the bytes its own pattern shows
    were received wrong put back, and how many """
    payload, count, i, heads = list(payload), 0, 0, []
    while i + 3 < len(payload) and payload[i] == 0 and payload[i + 1] is not None:
        code = payload[i + 1]
        if code == 0x0c:
            if None in payload[i + 2:i + 4]:
                break
            end = min(len(payload), i + 4 + (payload[i + 2] | payload[i + 3] << 8))
            stream = payload[i + 4:end]
            for at in range(len(stream) - 3):
                if stream[at] == 0x1f and stream[at + 1] in (0x3d, 0x3e) and None not in stream[at + 2:at + 4] and \
                        any(stream[b:b + 4] == [0x1f, 0x3f] + stream[at + 2:at + 4] for b in range(len(stream) - 3)):
                    payload[i + 5 + at] = 0x3f
                    count += 1
            i = end
        else:
            heads.append((i + 1, payload[i + 2], code))
            i = next((n for n in range(i + 2, len(payload)) if payload[n] == 0x1f), len(payload)) + 1
    for (_, line_a, code_a), (_, line_b, code_b), (at, line, code) in zip(heads, heads[1:], heads[2:]):
        if code in FAULTING_CODES and code_a == code_b and code_b not in FAULTING_CODES and line is not None and \
                line == line_b:
            payload[at] = code_b
            count += 1
    return payload, count

# The trailer is SIGN.EXE's checksum of the inner length and payload, sixteen bits of it, so one in
# 65,536 wrong payloads checks by chance: where a story fails it, the checksum picks among the
# values its copies received and its repairs replaced, the fewest changed, only where one choice
# alone checks and at most CHECKED_CHOICES were tried; fills a pair of bytes none received, where
# one filling alone checks whose bytes stand beside their neighbours as bytes of the capture's
# own do somewhere, letters of either case alike and any digit for another; or puts back a single
# bit, where one alone checks and leaves its byte the same kind.
CHECKED_CHOICES = 1024
CHECKED_LOST = 2

def _checks(payload, trailer):
    return sign_checksum(bytes([len(payload) & 0xff, len(payload) >> 8]) + bytes(payload)) == trailer

def _states(data, start, each):
    """ The checksum of `data` from `start` for each of the candidates `each(n)` gives the bytes of
    at n, as an array """
    c2 = c3 = 0
    for n in range(start):
        c2 = (c2 + (data[n] ^ n & 0xff)) & 0xffff
        c3 = (c3 + c2) & 0xffff
        c3 = c3 >> 1 | (c3 & 1) << 15
    c2, c3 = np.int64(c2), np.int64(c3)
    for n in range(start, len(data)):
        c2 = (c2 + (each(n) ^ n & 0xff)) & 0xffff
        c3 = (c3 + c2) & 0xffff
        c3 = c3 >> 1 | (c3 & 1) << 15
    return c3

def _filled_checks(payload, lost, trailer):
    """ Every filling of the places `lost` that makes `payload` check """
    data = [len(payload) & 0xff, len(payload) >> 8] + [value or 0 for value in payload]
    tried = np.arange(256 ** len(lost), dtype=np.int64)
    values = {at + 2: tried >> 8 * j & 0xff for j, at in enumerate(lost)}
    found = np.flatnonzero(_states(data, lost[0] + 2, lambda n: values.get(n, data[n])) == trailer)
    return [tuple(int(values[at + 2][i]) for at in lost) for i in found]

def _flipped_checks(payload, trailer):
    """ `[(place, bit)]`, every single bit whose flip makes `payload` check """
    data = [len(payload) & 0xff, len(payload) >> 8] + list(payload)
    at, bit = np.repeat(np.arange(len(payload)), 8) + 2, np.tile(1 << np.arange(8), len(payload))
    found = np.flatnonzero(_states(data, 2, lambda n: np.where(at == n, data[n] ^ bit, data[n])) == trailer)
    return [(int(at[i]) - 2, int(bit[i])) for i in found]

def _kind(value):
    return 0 if value < 0x20 else 1 if value < 0x80 else 2

def _folded(value):
    return 0x30 if 0x30 <= value <= 0x39 else value & ~0x20 if 0x61 <= value <= 0x7a else value

def _bigrams(payloads):
    """ The pairs of neighbouring bytes the payloads received, folded """
    return {(_folded(a), _folded(b)) for payload in payloads for a, b in zip(payload, payload[1:])
            if None not in (a, b)}

def _checked(payload, trailers, others, bigrams, unsure=()):
    """ `(payload, count)`: `payload`, None where lost, as the first of `trailers` it can be made to
    check against says it is, and how many of its bytes that set; `others` holds a place's other
    values `{place: [value]}`, `unsure` the places the capture's context filled, `bigrams` what
    `_bigrams` gives of the capture """
    lost = [n for n, value in enumerate(payload) if value is None]
    if not lost and any(_checks(payload, trailer) for trailer in trailers):
        return payload, 0
    open_ = sorted(set(lost) | set(unsure))
    for trailer in trailers:
        if open_ and len(open_) <= CHECKED_LOST:
            fits = []
            for filling in _filled_checks(payload, open_, trailer):
                got = list(payload)
                for n, value in zip(open_, filling):
                    got[n] = value
                if all((_folded(got[n]), _folded(got[m])) in bigrams for at in open_
                       for n, m in ((at - 1, at), (at, at + 1)) if 0 <= n and m < len(got)):
                    fits.append(got)
            if len(fits) == 1:
                return fits[0], sum(a != b for a, b in zip(payload, fits[0]))
        if lost:
            continue
        places = sorted(others)
        changes = ((weight, dict(zip(chosen, values))) for weight in range(1, len(places) + 1)
                   for chosen in itertools.combinations(places, weight)
                   for values in itertools.product(*(others[n] for n in chosen)))
        fits, tried, whole = [], 0, True
        for weight, change in changes:
            if fits and weight > fits[0][1]:
                break
            if tried == CHECKED_CHOICES:
                whole = False
                break
            tried += 1
            got = [change.get(n, value) for n, value in enumerate(payload)]
            if _checks(got, trailer):
                fits.append((got, weight))
        if len(fits) == 1 and whole:
            return fits[0]
        if fits or open_:
            continue
        fits = [(n, bit) for n, bit in _flipped_checks(payload, trailer) if _kind(payload[n] ^ bit) == _kind(payload[n])]
        if len(fits) == 1:
            got = list(payload)
            got[fits[0][0]] ^= fits[0][1]
            return got, 1
    return payload, 0

def _lined_up(members, reference, same, tail):
    """ `{member: place}`, the most of a pass's `members` that line up in order with places in
    the `reference` pass, taking the latest where `tail`, as the end of a loop """
    a, b = (members[::-1], reference[::-1]) if tail else (members, reference)
    longest = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) - 1, -1, -1):
        for j in range(len(b) - 1, -1, -1):
            longest[i][j] = max(longest[i + 1][j], longest[i][j + 1],
                                longest[i + 1][j + 1] + 1 if same(a[i], b[j]) else 0)
    out, i, j = {}, 0, 0
    while i < len(a) and j < len(b):
        if same(a[i], b[j]) and longest[i][j] == longest[i + 1][j + 1] + 1:
            out[a[i]] = reference.index(b[j])
            i, j = i + 1, j + 1
        elif longest[i + 1][j] >= longest[i][j + 1]:
            i += 1
        else:
            j += 1
    return out

def _stories(sent, copies, marks):
    """ The messages `(when, key, parsed)` in the order each first went out, as stories: each
    story's sendings, `(index, pass)`. A channel whose loop starts again - a mark of part 0 -
    is lined up pass by pass with its first whole pass, or as much of it as there is: a story
    is a place in that pass and whatever fills it in the others, and a place the others don't
    line up with is a story of its own. On a channel without, a story sent again is the same
    payload, or near enough. """
    channel = _channel_of([parsed[1] for _, _, parsed in sent])
    homes = set(channel.values())
    payloads = [_voted(_sending(copies, key, parsed)) for _, key, parsed in sent]
    same = lambda i, j: _same_story(payloads[i], payloads[j])
    by_channel, unnumbered = defaultdict(list), defaultdict(list)
    for index, (_, key, parsed) in enumerate(sent):
        # a record whose number no copy received reads as 0000: it can't have a place of its own
        (unnumbered if key[0] == 0 else by_channel)[channel[parsed[1]]].append(index)
    starts = defaultdict(list)
    for index, address, part, _ in marks:
        home = address if address in homes else _nearest(address, homes)
        if part == 0 and home is not None:
            starts[home].append(index)
    stories = []
    for home, indices in by_channel.items():
        bounds = starts[home]
        passes = [[i for i in indices if (n == 0 or bounds[n - 1] <= i) and (n == len(bounds) or i < bounds[n])]
                  for n in range(len(bounds) + 1)]
        if not bounds or not passes[1]:
            groups = []
            for index in sorted(indices + unnumbered.pop(home, [])):
                group = next((group for group in reversed(groups) if same(group[-1][0], index)), None)
                if group:
                    group.append((index, None))
                else:
                    groups.append([(index, None)])
            stories += groups
            continue
        places = [[(index, 1)] for index in passes[1]]
        for n, members in enumerate(passes):
            if n == 1:
                continue
            lined = _lined_up(members, passes[1], same, n == 0)
            for index in members:
                if index in lined:
                    places[lined[index]].append((index, n))
                else:
                    places.append([(index, n)])
        for index in unnumbered.pop(home, ()):
            # it joins the story it is a copy of, the nearest sent that matches it
            place = min((place for place in places if any(same(index, other) for other, _ in place)),
                        key=lambda place: min(abs(index - other) for other, _ in place), default=None)
            if place is None:
                places.append([(index, None)])
            else:
                place.append((index, place[0][1]))
        stories += [sorted(place) for place in places]
    for indices in unnumbered.values():
        stories += [[(index, None)] for index in indices]
    return sorted(stories)

# A channel's loop starts again at a record of FF alone, the ten bytes of a body with no
# length, and its parts begin at 00 0A and the part's number.
LOOP_START_BYTES = 10
PART_KIND = b'\x00\x0a'

# The sign group a message is addressed to is its channel: on both tapes c1 04 05 b0 20 gets
# the sports and c1 02 03 ab a0 the news and features, each with a YOU ARE WATCHING of its
# own, and WTTW has a third. An address only one message carries, a few bits from a
# channel's, is that channel's hit by a slicing error - WTTW's 00 06 07 b0 a0 and
# c1 00 05 b0 20.
def _nearest(address, channels):
    """ The channel of `channels` a few bits from `address`, or None """
    distance = lambda other: sum(bin(a ^ b).count('1') for a, b in zip(address, other))
    near = min((other for other in channels if len(other) == len(address)), key=distance, default=None)
    return near if near is not None and distance(near) <= 4 else None

def _channel_of(addresses):
    """ `{address: channel}` over the addresses messages went to """
    counts = Counter(addresses)
    common = [address for address, n in counts.items() if n > 1]
    return {address: address if address in common else _nearest(address, common) or address for address in counts}

def _channels(messages):
    """ `[(address, [message])]` for messages as `decode_silent_radio` keeps them, busiest
    channel first """
    channels = defaultdict(list)
    channel = _channel_of([message[2] for message in messages])
    for message in messages:
        channels[channel[message[2]]].append(message)
    return sorted(channels.items(), key=lambda channel: -len(channel[1]))

# Round a channel's loop - about fifteen minutes on KCET, 00 0A 01 to 05 marking its parts -
# a record number is sent again and again, every copy the same. A new number is the loop
# coming round with the same payload, as KCET's stock table comes back as 9F4A, or a story
# updated: the same command streams and text at least UPDATE_SIMILARITY alike, as SF at LA
# is inning by inning. Different stories under one heading - Stars of the Day, the betting
# line - come out at about half.
UPDATE_SIMILARITY = 0.6

def _lineage(kept):
    """ For a channel's messages in the order they arrived: None for new content, or
    `(relation, record)` for one that carries an earlier again or updates it """
    marks, seen = [], []
    for message in kept:
        payload = message[6]
        streams = [content for code, content in elements(payload) if code == 0x0c]
        text = ' '.join(value for item, value in message[4] if item == 'text')
        # a loop sends each of its stories in its own place: two it sends in one pass are two
        passes = {sending[3] for sending in message[9] if sending[3] is not None}
        mark = next((('a re-issue of', number) for number, other, _, _, other_passes in reversed(seen)
                     if other == payload and not passes & other_passes), None)
        if mark is None and streams:
            mark = next((('an update of', number)
                         for number, _, other_streams, other_text, other_passes in reversed(seen)
                         if other_streams == streams and not passes & other_passes and difflib.SequenceMatcher(
                             None, text, other_text, autojunk=False).ratio() >= UPDATE_SIMILARITY),
                        None)
        marks.append(mark)
        seen.append((message[1], payload, streams, text, passes))
    return marks


def _loop(kept, marks):
    """ A channel's stories in the order a sign on it played them, from its loop marks
    `(index, part, when)`: `[(heading, [(story, part, when the loop sent it)])]` - its loop from
    where the carousel first starts it again, part by part; before it the stories sent earlier
    that it doesn't carry, and after it any it adds. The sign keeps a channel's stories in slots
    it plays in turn, which the loop's start empties and the stories after it fill in order. """
    starts = [(index, when) for index, part, when in marks if part == 0]
    if not starts:
        return [(None, [(story, None, None) for story in kept])]
    begin, when = starts[0]
    end = starts[1][0] if len(starts) > 1 else float('inf')
    looped = sorted((min((index, sent_at) for index, sent_at, _, _ in story[9] if begin <= index < end), n)
                    for n, story in enumerate(kept) if any(begin <= index < end for index, _, _, _ in story[9]))
    parts = [(index, part) for index, part, _ in marks if part and begin <= index < end]
    part = lambda at: next((p for index, p in reversed(parts) if index <= at), None)
    inside = {n for _, n in looped}
    sections = [('Before the loop began', [(story, None, None) for n, story in enumerate(kept)
                                           if n not in inside and story[9][0][0] < begin]),
                ('The loop, from %s' % when, [(kept[n], part(at), sent_at) for (at, sent_at), n in looped]),
                ('After it', [(story, None, None) for n, story in enumerate(kept)
                              if n not in inside and story[9][0][0] >= end])]
    return [section for section in sections if section[1]]
