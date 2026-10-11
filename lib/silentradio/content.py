#!/usr/local/bin/python
# coding: utf-8
""" What a record carries: its body's class, address and payload, a message's elements, and
the text and graphics the log shows of them

Public domain / Unlicense, as with the rest of ccDecoder.
"""

import re

from lib.silentradio.sign import SIGN_EFFECT_PARAMETERS
from lib.silentradio.table import SIGN_LEAGUES, SIGN_PRESENTATIONS, SIGN_SCOREBOARDS

# SIGN.EXE's checksum, started from 0 and stored low byte first: a record's word is it of the
# record number, length, 00 and class, an address ends with it of the address's bytes before
# it, and the trailer is it of the inner length and payload
def sign_checksum(data):
    c2 = c3 = 0
    for n, byte in enumerate(data):
        c2 = (c2 + (byte ^ n & 0xff)) & 0xffff
        c3 = (c3 + c2) & 0xffff
        c3 = c3 >> 1 | (c3 & 1) << 15
    return c3

# The body opens with 00, its class, a 16 bit word, an address and a little endian length
# of what follows it. The address runs five bytes on class 3, four on class 2 and twelve on
# the one class 10 record, and ends with its own check, which places the payload even where
# the length was received wrong; where the address was received wrong the length does, and
# where the check was lost as well, the class counts the address's bytes before its check and
# the trailer says whether that holds.
def parse_body(body, trailer=None):
    """ `(class, address, payload)`, or None if nothing places the payload """
    for count in range(1, 17):
        at = 4 + count + 2
        if at + 2 <= len(body) and sign_checksum(bytes(body[4:4 + count])) == body[4 + count] | body[5 + count] << 8:
            return body[1], bytes(body[4:at]), bytes(body[at + 2:])
    for at in range(4, min(len(body) - 1, 24)):
        if body[at] | body[at + 1] << 8 == len(body) - at - 2:
            return body[1], bytes(body[4:at]), bytes(body[at + 2:])
    at = 4 + body[1] + 2 if len(body) > 1 else len(body)
    if trailer is not None and at + 3 <= len(body):
        payload = bytes(body[at + 2:])
        if sign_checksum(bytes([len(payload) & 0xff, len(payload) >> 8]) + payload) == trailer[0] | trailer[1] << 8:
            return body[1], bytes(body[4:at]), payload
    return None

# 01 02 and a byte, sent after each FF to a channel's groups and one kind of sign: two bits a
# colour from bit 0, read as how bright those signs light each colour (sign.py, scene.py;
# docs/silentradio-unknowns.md) - KCET's 3F, WTTW's C6
LEVELS_KIND = b'\x01\x02'

# A display message's payload is a run of elements, and the first a command stream: 00 0C
# and its little endian length, 183 bytes on a 354 byte record.
MESSAGE_KIND = b'\x00\x0c'

# A graphic is a run of 16 bit columns, each with its top bit set to keep it clear of the
# 7 bit text and control bytes; the other fifteen are one column of the sign, bit 0 at the
# top, the only way up KCET's 'QUOTES & FACTS' and WTTW's 'NBA' read. Outside such a run
# 0x80 is a space: 'UN', 0x80, 'L' on the tape.
GRAPHIC_MIN_COLUMNS = 3

# A column whose top bit a slicing error cleared - 00 and a byte, between two that kept
# theirs - goes back into the run: WTTW's gavel for THE VERDICT loses one so, and every such
# column a second copy shows set it.
def _graphic(content, i):
    """ The columns of the graphic starting at `i`, or None if none starts there """
    count = 0
    while i + 2 * count + 1 < len(content) and (
            content[i + 2 * count] >= 0x80
            or count and content[i + 2 * count] == 0 and i + 2 * count + 3 < len(content)
            and content[i + 2 * count + 2] >= 0x80):
        count += 1
    if count < GRAPHIC_MIN_COLUMNS:
        return None
    return [0x8000 | content[i + 2 * k] << 8 | content[i + 2 * k + 1] for k in range(count)]

# What a line command escaped by 1F takes after it, as the sign reads it
ESCAPED_PARAMETERS = {0x00: 1, 0x01: 2, 0x02: 1, 0x03: 1, 0x0a: 1, 0x1c: 2, 0x1d: 1, 0x1f: 1}

def render(content):
    """ `[('text', line) | ('graphic', [column])]` from a display message's content

    Bytes below 0x20 are controls. 0x03 breaks a line and 0x06 and its parameter sit inside
    one - 'CONCER', 06 10, 'T' on the tape; the rest end one, and 0x1D, and 0x1F with the
    command or effect it escapes, carry parameter bytes that would otherwise print - on the
    tape `1F 2C` comes straight before 'STARS' and `1D 70` before 'OF'.
    """
    items = []
    line = []

    def end_line():
        text = ''.join(line).strip()
        if len(text) > 1:
            items.append(('text', text))
        line.clear()

    i = 0
    while i < len(content):
        byte = content[i]
        if byte >= 0x80:
            columns = _graphic(content, i)
            if columns is not None:
                end_line()
                items.append(('graphic', columns))
                i += 2 * len(columns)
                continue
            if byte == 0x80:
                line.append(' ')
        elif byte == 0x03:
            end_line()
        elif byte == 0x06:
            i += 2
            continue
        elif byte < 0x20:
            end_line()
            if byte == 0x1d:
                i += 1
            elif byte == 0x1f:
                code = content[i + 1] & 0x7f if i + 1 < len(content) else 0x20
                i += 2 + (ESCAPED_PARAMETERS.get(code, 0) if code < 0x20 else code in SIGN_EFFECT_PARAMETERS)
                continue
        else:
            line.append(chr(byte))
        i += 1
    end_line()
    return items

def elements(payload):
    """ `[(presentation, content)]`, a message's elements in order """
    out, i = [], 0
    while i + 3 < len(payload) and payload[i] == 0:
        code = payload[i + 1]
        if code == 0x0c:
            end = i + 4 + (payload[i + 2] | payload[i + 3] << 8)
            out.append((code, payload[i + 4:end]))
        else:
            stop = payload.find(b'\x1f', i + 2)
            stop = len(payload) if stop < 0 else stop
            out.append((code, payload[i + 2:stop]))
            end = stop + 1
        i = end
    return out

# A scoreboard, which WTTW's 1989 feed sends: a game to each 1F, opening 00 then '0' where it
# starts a league or '1' where it goes on with one, its fields split by tag bytes - 02 or 03
# the league, 05 and 06 the teams, 07 and 08 their scores, 09 the inning.
SCOREBOARD_KINDS = (b'\x000', b'\x001')

# The other codes the ROM plays a template for (sign.py), which a message can open with as well
PRESENTED = set(SIGN_PRESENTATIONS) | set(SIGN_LEAGUES)

def _fields(game):
    """ A scoreboard's game as a line of its fields """
    return ' '.join(field.decode('latin-1') for field in re.split(rb'[\x00-\x1f]', game) if field)

def displayed(payload):
    """ What a payload puts on the sign, as `render` gives it, or None for a control """
    if payload[:2] == MESSAGE_KIND:
        # eleven of WTTW's and one of KCET's have nothing to draw, so their bytes are shown
        return render(payload[4:]) or [('text', 'nothing to draw: ' + payload[4:].hex(' '))]
    if payload[:2] in SCOREBOARD_KINDS:
        return [('text', _fields(game[2:])) for game in payload.split(b'\x1f') if game]
    if len(payload) > 1 and payload[0] == 0 and payload[1] in PRESENTED:
        # each element as its kind shows: a presentation's text a line a part, as a stream's
        items = []
        for code, content in elements(payload):
            items += [('text', _fields(content))] if code in SIGN_SCOREBOARDS else render(content)
        return items or [('text', 'nothing to draw: ' + payload.hex(' '))]
    return None

def describe_record(number, body, trailer=None):
    """ The lines one record contributes to the log """
    parsed = parse_body(body, trailer)
    if parsed is None:
        return ['Record %04x  %d bytes, unparsed  %s' % (number, len(body), bytes(body[:16]).hex(' '))]
    kind, address, payload = parsed
    items = displayed(payload)
    if items is None:
        return ['Control %04x  class %d to %s  %s' % (number, kind, address.hex(), payload.hex(' '))]
    lines = ['Message %04x  to %s  %d bytes' % (number, address.hex(), len(body))]
    for item, value in items:
        lines.append('    ' + (value if item == 'text' else '[graphic, %d columns]' % len(value)))
    return lines
