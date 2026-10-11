#!/usr/local/bin/python
# coding: utf-8
""" The sign, a model of its firmware: a message's bytes played through the ROM's router and
line interpreters into the lamps they light, tick by tick

Public domain / Unlicense, as with the rest of ccDecoder.
"""

import bisect

from lib.silentradio.table import SIGN_BORDER_DASHES, SIGN_BORDER_SIDES, SIGN_FONTS, SIGN_FONT_LOW, SIGN_FONT_ROWS, SIGN_LEAGUES, SIGN_PRESENTATIONS, SIGN_PRESETS, SIGN_SCOREBOARDS, SIGN_SCOREBOARD_FIELDS, SIGN_SERIAL, SIGN_SETTINGS

# The sign, as its firmware plays a message (docs/silentradio.md): 112 by 15 lamps, on a
# timer tick of 1.28 ms, refreshed a row a tick. The router hands a message's bytes to two
# line interpreters, and each draws into its own rows of one off-screen page and shows its
# window of it with an effect. This firmware lights a lamp or not; each lamp also keeps the
# colour it was drawn in, read from its font byte (docs/silentradio-unknowns.md).
SIGN_WIDTH = 112
SIGN_ROWS = 15
SIGN_TICK_MS = 1.28
SIGN_FRAME_TICKS = 16
SIGN_BLINK_TICKS = 128
SIGN_BORDER_TICKS = 48

# What drawing off the sign costs, measured on the ROM: ticks for a character, and for a
# column so many, so many more for each of the window's rows and for each byte of them; a
# clear the same way; and each line's share of the processor while the other draws too.
SIGN_CHARACTER_TICKS = 2.2
SIGN_COLUMN_TICKS = (0.1, 0.07, 0.0155)
SIGN_CLEAR_TICKS = (1, 0, 0.02)
SIGN_SHARES = (0.72, 0.53)

# A command's own tick, and 1E's, the filler after an effect that takes no byte of its own: an
# RTS the dispatch reaches, a fifth of a tick on the ROM; the slices drawing takes the processor
# in; a travelling line's column a display frame; and the 0E pause.
SIGN_COMMAND_TICKS = 1
SIGN_FILLER_TICKS = 0.2
SIGN_WORK_TICKS = 8
SIGN_TRAVEL_TICKS = SIGN_FRAME_TICKS
SIGN_PAUSE_TICKS = 800

# The effects' paces, in ticks between their steps: the show's three bands; a build-up's
# steps, so many and so many for each row over so many; a roll's, so many and one for each
# so many lamps it moves; the twinkle's passes, three of each five the faster; the unveil's
# steps and the wipes', faster over more than eight rows; and the row reveals'.
SIGN_SHOW_TICKS = 1
SIGN_BUILD_TICKS = (30, 9, 20)
SIGN_ROLL_TICKS = (36, 128)
SIGN_TWINKLE_TICKS = (15, 16)
SIGN_UNVEIL_TICKS = 9
SIGN_WIPE_TICKS = (9, 10)
SIGN_REVEAL_TICKS = 40

# Effects, 1F and a code, that take a byte after them
SIGN_EFFECT_PARAMETERS = {0x24, 0x25, 0x3b, 0x42, 0x46, 0x47}

def _sign_glyphs(fonts):
    return [[bytes.fromhex(glyph) for glyph in ''.join(font).split()] for font in fonts]

SIGN_GLYPHS = _sign_glyphs(SIGN_FONTS)
# What the glyph drawer reads for characters below 0x20, which only effects 42 and 45 ask for
SIGN_LOW_GLYPHS = _sign_glyphs(SIGN_FONT_LOW)

def _glyph(font, character):
    """ `(width byte, rows)` of a character in a font; past the font's glyphs, nothing """
    font = font if font < 5 else font - 5
    if 0 <= character < 0x20:
        glyph = SIGN_LOW_GLYPHS[font][character]
        return glyph[0], glyph[1:]
    glyphs = SIGN_GLYPHS[font]
    if 0 <= character - 0x20 < len(glyphs):
        glyph = glyphs[character - 0x20]
        return glyph[0], glyph[1:]
    return 0, bytes(SIGN_FONT_ROWS[font])

def _received(payload, lost):
    """ `payload` without the bytes at positions `lost`, which no copy received, and each
    stream's count cut to what is left of it: read as the zeros voting leaves them, a pair lost
    from a stream is a command that sets the two lines at odds for the rest of the message """
    out, i = bytearray(), 0
    while lost and i + 3 < len(payload) and payload[i] == 0:
        if payload[i + 1] == 0x0c:
            count = payload[i + 2] | payload[i + 3] << 8
            kept = bytes(payload[n] for n in range(i + 4, min(i + 4 + count, len(payload))) if n not in lost)
            out += bytes((0, 0x0c, len(kept) & 0xff, len(kept) >> 8)) + kept
            i += 4 + count
        else:
            # text never holds 00, so lost bytes before one took the 1F that ended the element
            stop = i + 2
            while stop < len(payload) and not (payload[stop] == 0x1f and stop not in lost) and not (
                    stop in lost and _next_received(payload, lost, stop) == 0):
                stop += 1
            out += bytes(payload[n] for n in range(i, stop) if n not in lost) + b'\x1f'
            i = stop + 1
            while i in lost:
                i += 1
    return bytes(out) + bytes(payload[n] for n in range(i, len(payload)) if n not in lost)

def _next_received(payload, lost, at):
    at += 1
    while at in lost:
        at += 1
    return payload[at] if at < len(payload) else None

def _part(values, a, b, mask):
    """ Columns [a, b) of `values`, masked """
    return [value & mask for value in values[a:b]]

def _template(part, settings):
    """ A template's bytes, its references to the settings filled in from `settings` """
    out = bytearray()
    for word in part.split():
        if word[0] == '@':
            offset, count = word[1:].split(':')
            out += settings[int(offset, 16):int(offset, 16) + int(count)]
        else:
            out.append(int(word, 16))
    return bytes(out)

def play_elements(payload, settings=None):
    """ The router bytes a message's elements play, with the sign's `settings`, as it boots
    with them if None """
    settings = bytes.fromhex(SIGN_SETTINGS) if settings is None else settings
    out, i = bytearray(), 0
    while i + 1 < len(payload) and payload[i] == 0:
        code = payload[i + 1]
        i += 2
        if code == 0x0c:
            count = payload[i] | payload[i + 1] << 8 if i + 1 < len(payload) else 0
            out += payload[i + 2:i + 2 + count]
            i += 2 + count
            continue
        templates = SIGN_SCOREBOARD_FIELDS if code in SIGN_SCOREBOARDS \
            else SIGN_LEAGUES[code][0] if code in SIGN_LEAGUES else SIGN_PRESENTATIONS.get(code)
        if templates is None:
            # a code the ROM presents nothing for
            stop = payload.find(b'\x1f', i)
            i = len(payload) if stop < 0 else stop + 1
            continue
        if code in SIGN_SCOREBOARDS:
            out += _template(SIGN_SCOREBOARDS[code][0], settings)
        line = payload[i] if i < len(payload) else 0x1f
        i += 1
        while line in templates:
            parts = templates[line].split('..')
            for n, part in enumerate(parts):
                out += _template(part, settings)
                if n + 1 < len(parts):
                    # the text runs to a byte below 20, but a graphic column's second byte, whatever
                    # it is, is text, as the ROM reads it
                    start = i
                    while i < len(payload) and (payload[i] >= 0x20 or _pair_due(out + payload[start:i])):
                        i += 1
                    out += payload[start:i]
                    line = payload[i] if i < len(payload) else 0x1f
                    i += 1
        if line != 0x1f:
            # a line it has no template for, as a damaged run end makes: the rest goes too
            stop = payload.find(b'\x1f', i)
            i = len(payload) if stop < 0 else stop + 1
        if code in SIGN_SCOREBOARDS:
            out += _template(SIGN_SCOREBOARDS[code][1], settings)
        if code in SIGN_LEAGUES:
            out += _template(SIGN_LEAGUES[code][1], settings)
    return bytes(out)

# What a reference to the settings plays, by the byte the template puts before it
SIGN_SETTING_KINDS = {0x1c: 'hold', 0x1f: 'effect'}

def sign_setting_fields():
    """ `[(offset, count, kind, codes)]`, each run of the settings a template plays: a hold,
    an effect, the top line's format and font (after `05` and its rows), or whether it draws
    tall; and the presentation codes that play it """
    fields = {}
    templates = [(code, text) for code, lines in SIGN_PRESENTATIONS.items() for text in lines.values()]
    templates += [(code, text) for code, parts in SIGN_SCOREBOARDS.items() for text in parts]
    templates += [(code, text) for code, (lines, closing) in SIGN_LEAGUES.items()
                  for text in [*lines.values(), closing]]
    for code, text in templates:
        words = text.split()
        for n, word in enumerate(words):
            if word[0] == '@':
                offset, count = (int(part, 16) for part in word[1:].split(':'))
                kind = SIGN_SETTING_KINDS.get(int(words[n - 1], 16), 'format' if count == 2 else 'tall')
                fields.setdefault((offset, count, kind), set()).add(code)
    return [key + (sorted(codes),) for key, codes in sorted(fields.items())]

def route_to_lines(data, state=False):
    """ `(top, bottom)`, the command streams the router gives the two lines; with `state`,
    `((top, bottom), line, pairs)`, the line it leaves picked and each line's graphic pairs as
    it leaves them: 1 after 08, 2 where a column's second byte is due

    01 and 02 pick the top line or the bottom for what follows, 03 sets both lines' rows
    again, 04 and 05 set rows, format and font for the line picked or for both, and 06 and
    07 are its format and font. 1B and 0B lay the sign out afresh. After 08 a byte from
    0x80 and the one after it are a graphic column.
    """
    lines, line, pairs, rows = (bytearray(), bytearray()), 0, [0, 0], [0xe8, 0x70]
    stream = iter(data)
    take = lambda: next(stream, 0)

    def rows_again():
        lines[0].extend((0, rows[0]))
        lines[1].extend((0, rows[1]))

    for byte in stream:
        if pairs[line] == 2:
            pairs[line] = 1
            lines[line].append(byte)
        elif pairs[line] and byte >= 0x80:
            pairs[line] = 2
            lines[line].append(byte)
        elif byte in (0x01, 0x02):
            line = byte - 1
        elif byte == 0x03:
            rows_again()
        elif byte == 0x04:
            rows[line] = take()
            lines[line].extend((0, rows[line]))
            lines[1 - line].extend((0, rows[1 - line]))
            lines[line].extend((1, take(), take()))
            rows_again()
        elif byte == 0x05:
            rows = [take(), take()]
            lines[0].extend((0, rows[0]))
            lines[1].extend((0, rows[1]))
            lines[0].extend((1, take(), take()))
            lines[1].extend((1, take(), take()))
            rows_again()
        elif byte in (0x06, 0x07):
            lines[line].extend((byte - 4, take()))
        elif byte in (0x08, 0x09):
            pairs[line] = byte == 0x08
            lines[line].append(byte)
        elif byte in (0x00, 0x0a, 0x1d, 0x1f):
            lines[line].extend((byte, take()))
        elif byte == 0x1c:
            lines[line].extend((byte, take(), take()))
        elif byte in SIGN_PRESETS:
            top, bottom = (bytes.fromhex(preset) for preset in SIGN_PRESETS[byte])
            line, pairs = 0, [0, 0]
            rows = [top[top.index(0) + 1], bottom[bottom.index(0) + 1]]
            lines[0].extend(top)
            lines[1].extend(bottom)
            rows_again()
        else:
            lines[line].append(byte)
    return (lines, line, pairs) if state else lines

def _pair_due(data):
    """ Whether the router, given `data`, is due a graphic column's second byte on the line it
    has picked """
    _, line, pairs = route_to_lines(data, True)
    return pairs[line] == 2

class SignLine:
    """ One line interpreter's state

    Its rows are counted from the top, its margins in bytes of eight columns. The page is
    shared with the other line, each drawing into its own rows of it; so are its `hues`, the
    page's red lamps and its green, which lamps lit in yellow are both.
    """

    def __init__(self, page, hues):
        self.top = self.bottom = None
        self.left = self.right = 0
        self.gap, self.lift = 1, 0
        self.font = 0
        # the colour it draws in: its font byte's bits 0 and 1, a red LED and a green one
        self.colour = 0
        self.inverse = self.double = self.flash = self.descend = False
        self.tall = self.travel = False
        self.blink = True
        self.clock = 0
        self.page, self.flags = page, [False] * SIGN_WIDTH
        self.hues = hues
        self.free = SIGN_WIDTH

    def painted(self, column):
        """ `(red, green)` of a column drawn in the line's colour """
        return column if self.colour & 1 else 0, column if self.colour & 2 else 0

    def columns(self):
        return 8 * self.left, SIGN_WIDTH - 8 * self.right

    def mask(self):
        return 0 if self.top is None else sum(1 << row for row in range(self.top, self.bottom + 1))

    def rows(self, byte):
        # the last row in the high nibble and the first in the low, counted from the bottom
        if byte == 0xff:
            self.top = self.bottom = None
        else:
            self.top, self.bottom = max(0, 14 - (byte >> 4)), 14 - (byte & 0x0f)

    def ticks(self, each, per_row, per_byte):
        """ Ticks for a pass over the window's rows and bytes """
        a, b = self.columns()
        return each + bin(self.mask()).count('1') * (per_row + per_byte * (b - a) / 8)

    def clear(self):
        a, b = self.columns()
        mask = self.mask()
        lit = mask if self.inverse else 0
        for plane, value in zip((self.page,) + self.hues, (lit,) + self.painted(lit)):
            for x in range(a, b):
                plane[x] = plane[x] & ~mask | value
        self.flags[a:b] = [False] * (b - a)
        self.free = b - a

    def shift(self, column, flash):
        # a column goes in at the window's right and what is there moves left
        a, b = self.columns()
        mask = self.mask()
        column = (column ^ (mask if self.inverse else 0)) & mask
        for plane, value in zip((self.page,) + self.hues, (column,) + self.painted(column)):
            for x in range(a, b - 1):
                plane[x] = plane[x] & ~mask | plane[x + 1] & mask
            plane[b - 1] = plane[b - 1] & ~mask | value
        self.flags[a:b] = self.flags[a + 1:b] + [flash]
        self.free = max(0, self.free - 1)

    def glyph(self, character, gap=0):
        """ The columns of a character, set on the window's bottom row and lifted, and `gap` more
        taken on from its bits past its width, as the drawer takes its gap """
        head, rows = _glyph(self.font, character)
        under = 0
        if self.descend:
            # a descender hangs from a line two rows up, drawn from its own glyph
            if head & 0x80:
                head, rows = _glyph(self.font, 0x80 + (head >> 4 & 7))
            else:
                under = 2
        width = head & 0x0f if head & 0x80 else head
        if self.tall:
            rows = bytes(row for row in rows for _ in (0, 1))
        base = (14 if self.bottom is None else self.bottom) - self.lift - under
        columns = []
        for c in range(width + gap):
            column = 0
            for r, bits in enumerate(rows):
                y = base - (len(rows) - 1 - r)
                if c < 8 and bits >> (7 - c) & 1 and 0 <= y < SIGN_ROWS:
                    column |= 1 << y
            columns.append(column)
        return columns

    def graphic(self, value):
        """ A graphic column, its bit 14 on the window's bottom row """
        base = (14 if self.bottom is None else self.bottom) - self.lift
        return sum(1 << base - (14 - bit) for bit in range(15)
                   if value >> bit & 1 and 0 <= base - (14 - bit) < SIGN_ROWS)

class Sign:
    """ The lamps and the two line interpreters

    Each line runs as a generator over its command stream, asking for ('work', ticks) as it
    draws, ('wait', ticks), ('until', tick) or ('sync',) at a 00.
    """

    def __init__(self, ops=None):
        self.lit = [0] * SIGN_WIDTH
        self.hues = ([0] * SIGN_WIDTH, [0] * SIGN_WIDTH)
        self.flashing = [[False] * SIGN_WIDTH, [False] * SIGN_WIDTH]
        page, hues = [0] * SIGN_WIDTH, ([0] * SIGN_WIDTH, [0] * SIGN_WIDTH)
        self.lines = [SignLine(page, hues), SignLine(page, hues)]
        self.now = [0, 0]
        self.border = None
        # what each write to the lamps did, for the scene (scene.py)
        self.ops = ops
        self.effects = 0
        self.effect_id = [0, 0]
        # the fault code effect 45 draws, 'A' at boot: none
        self.fault = 0x41

    def log(self, op, k, **fields):
        if self.ops is not None:
            self.ops.append(dict(op=op, t=self.now[k], k=k, **fields))

    def show(self, k, columns=None, rows=None):
        """ Line k's window of the page onto the lamps, or only `columns` of it and `rows` """
        line = self.lines[k]
        a, b = line.columns()
        mask = line.mask() if rows is None else rows & line.mask()
        columns = range(a, b) if columns is None else columns
        self.log('show', k, id=self.effect_id[k], start=columns.start, stop=columns.stop, mask=mask, a=a, b=b,
                 window=line.mask(), page=_part(line.page, a, b, line.mask()),
                 hues=[_part(hue, a, b, line.mask()) for hue in line.hues])
        for x in columns:
            self.lit[x] = self.lit[x] & ~mask | line.page[x] & mask
            for hue, page in zip(self.hues, line.hues):
                hue[x] = hue[x] & ~mask | page[x] & mask
            self.flashing[k][x] = line.flags[x]

    def run(self, k, stream):
        line = self.lines[k]
        stream = iter(stream)
        take = lambda: next(stream, 0)

        def draw(columns, flash):
            if not line.travel and columns:
                work = SIGN_CHARACTER_TICKS + len(columns) * line.ticks(*SIGN_COLUMN_TICKS)
                while work > 0:
                    yield ('work', min(work, SIGN_WORK_TICKS))
                    work -= SIGN_WORK_TICKS
            for column in columns:
                if line.travel:
                    # travelling, the window itself moves a column each display frame
                    a, b = line.columns()
                    mask = line.mask()
                    # margins that cross leave only the column at b - 1 written
                    start = a if a < b else (b - 1) % SIGN_WIDTH
                    stop = b if a < b else start + 1
                    column = (column ^ (mask if line.inverse else 0)) & mask
                    self.log('travel', k, a=start, b=stop, mask=mask, column=column, hues=list(line.painted(column)),
                             before=_part(self.lit, start, stop, mask),
                             before_hues=[_part(hue, start, stop, mask) for hue in self.hues])
                    for x in range(a, b - 1):
                        self.flashing[k][x] = self.flashing[k][x + 1]
                    for plane, value in zip((self.lit,) + self.hues, (column,) + line.painted(column)):
                        for x in range(a, b - 1):
                            plane[x] = plane[x] & ~mask | plane[x + 1] & mask
                        plane[b - 1] = plane[b - 1] & ~mask | value
                    self.flashing[k][b - 1] = flash
                    yield ('wait', SIGN_TRAVEL_TICKS)
                else:
                    line.shift(column, flash)

        def character(byte):
            if byte >= 0x80:
                column = line.graphic((byte << 8 | (take() if line.tall else 0)) & 0x7fff)
                return [column] * (1 + line.double + line.gap)
            if not line.double:
                return line.glyph(byte, line.gap)
            # doubled, an eight column glyph runs on into its gap
            columns = line.glyph(byte)
            tail = columns[-1] if len(columns) == 8 else 0
            return [column for column in columns for _ in (0, 1)] + [tail] * line.gap

        draw.character = lambda byte: draw(character(byte), line.flash)
        draw.take = take
        for byte in stream:
            if byte >= 0x20:
                yield from draw.character(byte)
            else:
                yield ('work', SIGN_FILLER_TICKS if byte == 0x1e else SIGN_COMMAND_TICKS)
                yield from self.command(k, byte, take, draw)

    def command(self, k, byte, take, draw):
        line = self.lines[k]
        if byte == 0x00:
            # the two lines wait for each other, then take their rows
            rows = take()
            yield ('sync',)
            line.rows(rows)
            self.log('rows', k, rows=line.mask())
        elif byte in (0x01, 0x02, 0x03):
            if byte != 0x03:
                self.format(line, take())
            if byte != 0x02:
                font = take()
                self.set_font(line, font)
                self.log('font', k, byte=font, rows=line.mask())
                if font & 0x08:
                    yield ('work', line.ticks(*SIGN_CLEAR_TICKS))
        elif byte in (0x08, 0x09):
            line.tall = byte == 0x08
        elif byte == 0x0a:
            self.margins(line, take())
        elif byte == 0x0c:
            # centres what is drawn
            yield from draw([0] * (line.free // 2), False)
        elif byte == 0x0d:
            yield from draw([0] * SIGN_WIDTH, False)
        elif byte == 0x0e:
            yield ('wait', SIGN_PAUSE_TICKS)
        elif byte == 0x10:
            line.travel = True
        elif byte in (0x11, 0x1d):
            if byte == 0x1d:
                self.margins(line, take())
            line.travel = False
            line.clear()
            yield ('work', line.ticks(*SIGN_CLEAR_TICKS))
        elif byte in (0x12, 0x13):
            line.flash = byte == 0x12
        elif byte in (0x14, 0x15):
            line.double = byte == 0x14
        elif byte in (0x16, 0x17):
            line.descend = byte == 0x16
        elif byte in (0x18, 0x19):
            line.inverse = byte == 0x19
        elif byte == 0x1c:
            # holds until the line's clock, which 1F 2C sets going, reaches the ticks given
            low, high = take(), take()
            yield ('until', line.clock + (low | high << 8))
        elif byte == 0x1f:
            code = take() & 0x7f
            if code < 0x20:
                yield from self.command(k, code, take, draw)
            else:
                yield from self.effect(k, code, take() if code in SIGN_EFFECT_PARAMETERS else None, draw)

    @staticmethod
    def format(line, byte):
        line.gap, line.lift = byte >> 4, byte & 0x0f

    @staticmethod
    def set_font(line, byte):
        line.font = byte & 7
        line.colour = byte & 3
        line.inverse, line.double, line.flash, line.descend = (bool(byte & bit) for bit in (0x80, 0x40, 0x20, 0x10))
        # bit 3 draws off the sign until an effect shows it; without it the line travels
        line.travel = not byte & 0x08
        if byte & 0x08:
            line.clear()

    @staticmethod
    def margins(line, byte):
        if byte >> 4 != 0x0f:
            line.left = byte >> 4
        if byte & 0x0f != 0x0f:
            line.right = byte & 0x0f

    def roll(self, k, top, bottom, steps, up):
        """ Scroll rows `top` to `bottom` of line k's window a row a step, the page coming in
        behind what is shown, from below if `up`; the page moves on with it """
        line = self.lines[k]
        self.effects += 1
        roll = self.effects
        if top is None:
            return
        a, b = line.columns()
        height = bottom - top + 1
        window = sum(1 << row for row in range(top, bottom + 1))
        self.log('roll', k, id=roll, a=a, b=b, top=top, bottom=bottom, steps=steps, up=up,
                 shown=_part(self.lit, a, b, window), page=_part(line.page, a, b, window),
                 shown_hues=[_part(hue, a, b, window) for hue in self.hues],
                 page_hues=[_part(hue, a, b, window) for hue in line.hues])
        # the lamps and their red and green, each rolled the same way
        planes = list(zip((self.lit,) + self.hues, (line.page,) + line.hues))
        rolled = {}
        for x in range(a, b):
            rolled[x] = []
            for lamps, pages in planes:
                shown = [lamps[x] >> row & 1 for row in range(top, bottom + 1)]
                page = [pages[x] >> row & 1 for row in range(top, bottom + 1)]
                gone = [0] * max(0, steps - height)
                run = shown + page + gone if up else gone + page + shown
                rolled[x].append([sum(v << top + r for r, v in enumerate(
                    run[n:n + height] if up else run[len(run) - height - n:len(run) - n]))
                    for n in range(1, steps + 1)])
                rest = (page + [0] * steps)[steps:steps + height] if up else ([0] * steps + page)[:height]
                pages[x] = pages[x] & ~window | sum(v << top + r for r, v in enumerate(rest))
        for n in range(steps):
            self.log('rollstep', k, id=roll, step=n)
            for x in range(a, b):
                for (lamps, _), values in zip(planes, rolled[x]):
                    lamps[x] = lamps[x] & ~window | values[n]
                self.flashing[k][x] = line.flags[x]
            yield ('wait', SIGN_ROLL_TICKS[0] + height * (b - a) // SIGN_ROLL_TICKS[1])

    def effect(self, k, code, parameter, draw):
        line = self.lines[k]
        self.effects += 1
        self.effect_id[k] = effect = self.effects
        a, b = line.columns()
        rows = [row for row in range(SIGN_ROWS) if line.mask() >> row & 1]
        if code == 0x20:
            for part in range(1, 4):
                self.show(k, rows=sum(1 << row for row in rows[:len(rows) * part // 3]))
                yield ('wait', SIGN_SHOW_TICKS)
        elif code == 0x21:
            # each byte of eight columns rotates the page in from its left, a column a step,
            # what was shown going out at its right; the page's bytes are left empty
            mask = line.mask()
            shown, flashing = self.lit[:], self.flashing[k][:]
            shown_hues = [hue[:] for hue in self.hues]
            for step in range(8):
                self.log('build', k, id=effect, step=step, a=a, b=b, mask=mask, page=_part(line.page, a, b, mask),
                         shown=_part(shown, a, b, mask), page_hues=[_part(hue, a, b, mask) for hue in line.hues],
                         shown_hues=[_part(hue, a, b, mask) for hue in shown_hues])
                for x in range(a, b):
                    came = x & 7 <= step
                    source = x + 7 - step if came else x - step - 1
                    self.lit[x] = self.lit[x] & ~mask | (line.page if came else shown)[source] & mask
                    for hue, page, was in zip(self.hues, line.hues, shown_hues):
                        hue[x] = hue[x] & ~mask | (page if came else was)[source] & mask
                    self.flashing[k][x] = (line.flags if came else flashing)[source]
                if step < 7:
                    yield ('wait', SIGN_BUILD_TICKS[0] + len(rows) * SIGN_BUILD_TICKS[1] // SIGN_BUILD_TICKS[2])
            for plane in (line.page,) + line.hues:
                for x in range(a, b):
                    plane[x] &= ~mask
        elif code == 0x22:
            if self.border is None:
                self.border = self.now[k]
        elif code in (0x23, 0x44):
            if self.border is not None or code == 0x44:
                self.border = None
                self.log('unborder', k)
                # it leaves the end bytes of rows 1 to 13 dark
                for plane in (self.lit,) + self.hues:
                    for x in list(range(8)) + list(range(SIGN_WIDTH - 8, SIGN_WIDTH)):
                        plane[x] &= ~0x3ffe
        elif code in (0x24, 0x25, 0x46, 0x47):
            yield from self.roll(k, line.top, line.bottom, parameter & 0x0f or 256, code in (0x25, 0x47))
        elif code == 0x26:
            # a lamp of each display byte at a time, bytes seven apart and the lamp moving on
            # five places, until 56 passes have shown them all; a line with no rows lights
            # none, in as long
            first, last = (14 - line.bottom, 14 - line.top) if line.top is not None else (0, -1)
            lamp, y = 1, 0
            turn = lambda v: (v << 5 | v >> 3) & 0xff
            for n in range(56):
                touched, changed = [0] * SIGN_WIDTH, [0] * SIGN_WIDTH
                while y < 0xf0:
                    row, byte = y >> 4, (y & 0x0f) - 1
                    if first <= row <= last and line.left <= byte <= 13 - line.right:
                        bit = 1 << 14 - row
                        for i in range(8):
                            if lamp >> i & 1:
                                x = 8 * byte + i
                                touched[x] |= bit
                                for lamps, page in zip((self.lit,) + self.hues, (line.page,) + line.hues):
                                    changed[x] |= (lamps[x] ^ page[x]) & bit
                                    lamps[x] = lamps[x] & ~bit | page[x] & bit
                                self.flashing[k][x] = line.flags[x]
                    lamp = turn(lamp)
                    y += 7
                y -= 0xf0
                lamp = turn(lamp)
                self.log('twinkle', k, id=effect, step=n, touched=touched, changed=changed,
                         page=_part(line.page, 0, SIGN_WIDTH, line.mask()),
                         hues=[_part(hue, 0, SIGN_WIDTH, line.mask()) for hue in line.hues])
                yield ('wait', SIGN_TWINKLE_TICKS[n % 5 >= 3])
        elif code == 0x27:
            # unveiled from the middle out
            middle = (a + b) // 2
            for step in range(1, (b - a) // 2 + 2):
                self.show(k, columns=range(max(a, middle - step), min(b, middle + step)))
                yield ('wait', SIGN_UNVEIL_TICKS)
        elif code in (0x28, 0x29):
            for step in range(1, b - a + 1):
                self.show(k, columns=range(a, a + step) if code == 0x28 else range(b - step, b))
                yield ('wait', SIGN_WIPE_TICKS[len(rows) <= 8])
        elif code in (0x2a, 0x2b):
            for step in range(1, len(rows) + 1):
                self.show(k, rows=sum(1 << row for row in (rows if code == 0x2a else rows[::-1])[:step]))
                yield ('wait', SIGN_REVEAL_TICKS)
        elif code == 0x2c:
            line.clock = self.now[k]
        elif code in (0x2f, 0x30):
            line.blink = code == 0x2f
        elif code == 0x31:
            # the Silent Radio logo, font 4's glyphs
            font, line.font = line.font, 4
            for character in range(0x20, 0x2e):
                yield from draw.character(character)
            line.font = font
        elif code == 0x3f:
            yield from draw([0] * line.free, False)
        elif code == 0x3e:
            # the window's lamps and the page trade places, their blinking and colour with them
            mask = line.mask()
            shown = [[plane[x] & mask for x in range(a, b)] for plane in (self.lit,) + self.hues]
            flashing = self.flashing[k][a:b]
            self.show(k)
            for plane, values in zip((line.page,) + line.hues, shown):
                for x, value in zip(range(a, b), values):
                    plane[x] = plane[x] & ~mask | value
            line.flags[a:b] = flashing
            yield ('work', line.ticks(*SIGN_CLEAR_TICKS))
        elif code == 0x3d:
            # the window is filled with the line's background and stops blinking; the page is kept
            mask = line.mask()
            lit = mask if line.inverse else 0
            values = (lit,) + line.painted(lit)
            self.log('show', k, id=effect, start=a, stop=b, mask=mask, a=a, b=b, window=mask, page=[lit] * (b - a),
                     hues=[[value] * (b - a) for value in values[1:]])
            for x in range(a, b):
                for plane, value in zip((self.lit,) + self.hues, values):
                    plane[x] = plane[x] & ~mask | value
                self.flashing[k][x] = False
            yield ('work', line.ticks(*SIGN_CLEAR_TICKS))
        elif code == 0x40:
            # drawn off the sign, it blanks the window; travelling, it runs the line off it
            if line.travel:
                yield from draw([0] * SIGN_WIDTH, False)
            else:
                line.clear()
                yield from self.effect(k, 0x20, None, draw)
        elif code == 0x41:
            # what is shown bounces up and is squeezed out between closing rows
            line.travel = False
            line.clear()
            for top, bottom, steps in ((0, 14, 1), (0, 13, 1), (2, 12, 1), (3, 11, 1), (4, 10, 1),
                                       (5, 9, 1), (6, 8, 2)):
                yield from self.roll(k, top, bottom, steps, True)
        elif code == 0x42:
            yield from draw.character(parameter & 0x3f)
        elif code == 0x2d:
            # the sign's serial number, a digit for each BCD nibble
            for byte in bytes.fromhex(SIGN_SERIAL):
                yield from draw.character(byte >> 4 | 0x30)
                yield from draw.character(byte & 0x0f | 0x30)
        elif code == 0x45:
            # the fault code, taken and cleared
            fault, self.fault = self.fault, 0
            yield from draw.character(fault)
        elif code == 0x43:
            # the next three characters step down in reverse bold, in the first 32 columns
            tall, line.tall = line.tall, False
            self.margins(line, 0x0a)
            self.set_font(line, 0x8b)
            self.log('font', k, byte=0x8b, rows=line.mask())
            for form in (0x27, 0x24, 0x01):
                self.format(line, form)
                yield from draw.character(draw.take())
            yield from draw([0] * (line.free // 2), False)
            self.margins(line, 0x40)
            line.tall = tall

def sign_frames(payload, ops=None):
    """ `([(tick, lamps, flashing rows, border, red, green)], end)`: each change as the sign
    plays a message, and the tick it ends on; each write to the lamps, and each font byte and rows a
    line takes, goes on the list `ops` """
    sign = Sign(ops)
    runs = [sign.run(k, stream) for k, stream in enumerate(route_to_lines(play_elements(payload)))]
    at, state = [0, 0], ['go', 'go']
    busy, owed = [-1, -1], [0.0, 0.0]
    frames = []

    def snapshot(tick):
        lit = tuple(sign.lit)
        top, bottom = (line.mask() if line.blink else 0 for line in sign.lines)
        flashing = tuple((top if a else 0) + (bottom if b else 0) for a, b in zip(*sign.flashing))
        frame = (tick, lit, flashing, sign.border) + tuple(tuple(hue) for hue in sign.hues)
        if frames and frames[-1][1:] == frame[1:]:
            return
        if frames and frames[-1][0] == tick:
            frames[-1] = frame
        else:
            frames.append(frame)

    while 'go' in state or 'sync' in state:
        if 'go' not in state:
            # a display frame answers a line waiting on 00; one left waiting by a line that
            # has finished goes on when the next message starts it
            waiting = max(t for t, s in zip(at, state) if s == 'sync')
            release = -(-waiting // SIGN_FRAME_TICKS) * SIGN_FRAME_TICKS
            for k in (0, 1):
                if state[k] == 'sync':
                    state[k], at[k] = 'go', release
        k = min((k for k in (0, 1) if state[k] == 'go'), key=lambda k: at[k])
        sign.now[k] = at[k]
        logged = len(ops) if ops is not None else 0
        request = next(runs[k], None)
        # each write, with the tick of the frame the lamps last changed on before it
        for op in (ops or [])[logged:]:
            op['after'] = frames[-1][0] if frames else 0
        snapshot(at[k])
        if request is None:
            state[k] = 'done'
        elif request[0] == 'work':
            owed[k] += request[1] / (SIGN_SHARES[k] if busy[1 - k] > at[k] else 1)
            at[k] += int(owed[k])
            owed[k] -= int(owed[k])
            busy[k] = at[k]
        elif request[0] == 'wait':
            at[k] += request[1]
        elif request[0] == 'until':
            at[k] = max(at[k], request[1])
        else:
            state[k] = 'sync'
    return frames, max(at)

def _bordered(lamps, phase):
    dashes, sides = bytes.fromhex(SIGN_BORDER_DASHES), bytes.fromhex(SIGN_BORDER_SIDES)
    out = list(lamps)
    for y, at in ((0, phase), (14, 5 - phase if phase else 0)):
        for x in range(SIGN_WIDTH):
            out[x] = out[x] & ~(1 << y) | (dashes[at + (x >> 3)] >> (x & 7) & 1) << y
    left = (0, 2, 4, 1, 3)[phase]
    for x in list(range(8)) + list(range(SIGN_WIDTH - 8, SIGN_WIDTH)):
        out[x] &= ~0x3ffe
    for i in range(13):
        out[0] |= (sides[left + i] & 1) << i + 1
        out[-1] |= (sides[5 - left + i] >> 7) << i + 1
    return tuple(out)

# The lamps `_bordered` draws: rows 0 and 14, and the end bytes' rows between
_BORDER_LAMPS = tuple(1 | 1 << 14 | (0x3ffe if x < 8 or x >= SIGN_WIDTH - 8 else 0) for x in range(SIGN_WIDTH))

def _shown(frames, end, hues=False):
    """ `[(tick, lamps)]`, flashing lamps dark for the second half of every blink, and the
    chasing border drawn over what it lights, moving two places each step; with `hues`, each
    `(tick, lamps, red, green)`, the border's lamps in no colour """
    if not hues:
        # a change of colour alone is none
        frames = [frame for n, frame in enumerate(frames) if not n or frame[1:4] != frames[n - 1][1:4]]
    out = []
    for n, (tick, lamps, flashing, border, red, green) in enumerate(frames):
        stop = frames[n + 1][0] if n + 1 < len(frames) else end
        t = tick
        while True:
            dark = t // SIGN_BLINK_TICKS & 1
            shown = tuple(c & ~f for c, f in zip(lamps, flashing)) if dark else lamps
            tinted = [tuple(c & ~f for c, f in zip(hue, flashing)) if dark else hue for hue in (red, green)] if hues \
                else ()
            nxt = stop
            if any(flashing):
                nxt = min(nxt, (t // SIGN_BLINK_TICKS + 1) * SIGN_BLINK_TICKS)
            if border is not None:
                steps = 0 if t < border + SIGN_FRAME_TICKS else \
                    1 + (t - border - SIGN_FRAME_TICKS) // SIGN_BORDER_TICKS
                shown = _bordered(shown, (3 + 2 * steps) % 5)
                tinted = [tuple(c & ~b for c, b in zip(hue, _BORDER_LAMPS)) for hue in tinted]
                nxt = min(nxt, border + SIGN_FRAME_TICKS + steps * SIGN_BORDER_TICKS)
            out.append((t, shown, *tinted) if hues else (t, shown))
            if nxt >= stop:
                break
            t = nxt
    return out

def sign_program(payload, lost=(), hues=False):
    """ `[(milliseconds, lamps)]`, what the sign shows as it plays a message, each display
    held until the next, as this model of its firmware plays it; with `hues`, each
    `(milliseconds, lamps, red, green)` """
    return _played(payload, lost, hues=hues)[0]

def _played(payload, lost=(), model=None, hues=False):
    """ `(program, by)`: a message's program, and who played it, the model. `model` is its
    `(shown, end)` where the model has played it already, as `_shown` gives it with `hues`,
    each display with its red and green """
    payload = _received(payload, lost)
    if model is None:
        frames, model_end = sign_frames(payload)
        model = _shown(frames, model_end, hues), model_end
    shown, end = model
    return [((stop - frame[0]) * SIGN_TICK_MS,) + tuple(list(plane) for plane in frame[1:]) for frame, stop
            in zip(shown, [frame[0] for frame in shown[1:]] + [end]) if stop > frame[0]], 'model'

# What a player can change of the model, by these names less SIGN_ in lower case
SIGN_PARAMETERS = ('SIGN_TICK_MS', 'SIGN_FRAME_TICKS', 'SIGN_TRAVEL_TICKS', 'SIGN_BLINK_TICKS',
                   'SIGN_BORDER_TICKS', 'SIGN_CHARACTER_TICKS', 'SIGN_COLUMN_TICKS', 'SIGN_CLEAR_TICKS',
                   'SIGN_COMMAND_TICKS', 'SIGN_FILLER_TICKS', 'SIGN_WORK_TICKS', 'SIGN_SHARES', 'SIGN_PAUSE_TICKS',
                   'SIGN_SHOW_TICKS', 'SIGN_BUILD_TICKS', 'SIGN_ROLL_TICKS', 'SIGN_TWINKLE_TICKS',
                   'SIGN_UNVEIL_TICKS', 'SIGN_WIPE_TICKS', 'SIGN_REVEAL_TICKS')

def sign_parameters():
    """ The model's parameters as they stand, and the settings the sign boots with """
    values = globals()
    parameters = {name[5:].lower(): values[name] for name in SIGN_PARAMETERS}
    parameters['settings'] = bytes.fromhex(SIGN_SETTINGS).hex()
    return parameters

def sign_tables():
    """ The firmware's tables the model plays from """
    return {'width': SIGN_WIDTH, 'rows': SIGN_ROWS, 'font_rows': SIGN_FONT_ROWS,
            'fonts': [''.join(font) for font in SIGN_FONTS], 'fonts_low': [''.join(font) for font in SIGN_FONT_LOW],
            'serial': SIGN_SERIAL,
            'presentations': SIGN_PRESENTATIONS, 'scoreboard_fields': SIGN_SCOREBOARD_FIELDS,
            'scoreboards': SIGN_SCOREBOARDS, 'leagues': SIGN_LEAGUES, 'presets': SIGN_PRESETS,
            'effect_parameters': sorted(SIGN_EFFECT_PARAMETERS),
            'border_dashes': SIGN_BORDER_DASHES, 'border_sides': SIGN_BORDER_SIDES,
            'setting_fields': sign_setting_fields()}
