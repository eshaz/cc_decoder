#!/usr/local/bin/python
# coding: utf-8
""" The Silent Radio sign's run as layers of lamps, and the layers as an animated SVG

What the model's op-log (`sign.sign_frames` given `ops`) did to the lamps becomes layers,
each the sign's own function over time: a travelling line is its strip shifted a column a
display frame, a roll its rows shifted a row a step, a build-up the page shifted into each
byte of eight columns, a show its page in a window that grows, a twinkle its page let
through lamp by lamp. The frames' flashing and border go over them, and `verify` holds the
layers against the frames at every tick either changes; where they differ the frames
themselves are shown. `svg` writes each layer as SVG that SMIL animates: the lamps it lights
as outlines lit through the lamp grid, and how they move, the window they show in and when
as functions of time, each run of equal steps one animation that adds them up. scene.js is
the same, function for function, for the players to run again.

A layer is shown for ticks [t0, t1): inside `clip` and its window at the time, and where
its `comb` or `passes` let it through, it paints `fill` (lamps, bit r a row) unlit and then
`dots` lit (or unlit), moved by its `move` and `cycle`; a `blink` layer only in the second
half of each blink. Its dots' `hues`, where it has them, are which of its lamps are red and
which green, yellow both.

Public domain / Unlicense, as with the rest of ccDecoder.
"""

import bisect
import html
import json
import math

from lib.silentradio import sign

WIDTH = sign.SIGN_WIDTH
ROWS = sign.SIGN_ROWS

def row_mask(y0, y1):
    return (1 << y1) - (1 << y0) if y1 > y0 else 0

def rows_of(mask):
    """ [first row, last row + 1) of a run of rows """
    if not mask:
        return [0, 0]
    return [(mask & -mask).bit_length() - 1, mask.bit_length()]

def rect_fill(x0, y0, x1, y1):
    rows = row_mask(y0, y1)
    return [rows if max(0, x0) <= x < min(WIDTH, x1) else 0 for x in range(WIDTH)]

def empty(r):
    return r[2] <= r[0] or r[3] <= r[1]

def overlaps(a, b):
    return not empty(a) and not empty(b) and a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

def contains(outer, inner):
    return empty(inner) or (inner[0] >= outer[0] and inner[2] <= outer[2] and inner[1] >= outer[1] and
                            inner[3] <= outer[3])

def intersect(a, b):
    return [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]

def bounds(cols):
    """ The smallest rectangle round a set of lamps """
    r = [WIDTH, ROWS, 0, 0]
    for x, col in enumerate(cols):
        if col:
            y0, y1 = rows_of(col)
            r = [min(r[0], x), min(r[1], y0), max(r[2], x + 1), max(r[3], y1)]
    return r

def layer(kind, t0, clip):
    return {'kind': kind, 't0': t0, 't1': None, 'clip': clip, 'fill': None, 'dots': None, 'lit': True,
            'move': None, 'win': None, 'blink': False, 'cycle': None, 'comb': None, 'passes': None}

def track_at(track, t):
    """ The entry of a track in force at t, or None before its first """
    lo, hi, found = 0, len(track) - 1, None
    while lo <= hi:
        mid = (lo + hi) >> 1
        if track[mid][0] <= t:
            found = track[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    return found

def cycle_step(cycle, t):
    """ The step a cycle is on at t: 0 until `from`, then one more each `period` """
    return 0 if t < cycle['from'] else (1 + (t - cycle['from']) // cycle['period']) % len(cycle['offsets'])

def offset_at(l, t):
    dx = dy = 0
    if l['move']:
        at = track_at(l['move'], t)
        if at:
            dx, dy = at[1], at[2]
    if l['cycle']:
        step = l['cycle']['offsets'][cycle_step(l['cycle'], t)]
        dx += step[0]
        dy += step[1]
    return dx, dy

def comb_at(comb, t):
    """ The columns of each byte of eight a build-up's side shows at t, [first, last + 1) """
    step = track_at(comb['steps'], t)[1]
    return [0, step + 1] if comb['side'] == 'page' else [step + 1, 8]

def let_through(l, t, rows):
    """ The rows of each column a layer's comb or passes let it paint at t """
    if l['comb']:
        first, stop = comb_at(l['comb'], t)
        return [rows if first <= x & 7 < stop else 0 for x in range(WIDTH)]
    if l['passes']:
        shown = [0] * WIDTH
        for at, cols in l['passes']:
            if at > t:
                break
            shown = [a | b for a, b in zip(shown, cols)]
        return [m & rows for m in shown]
    return None

def place(column, y):
    """ A column of bits moved to start at row y, cut at the sign's last row """
    if y >= ROWS or y <= -31:
        return 0
    return (column & (1 << ROWS - y) - 1) << y if y >= 0 else column >> -y

def paint(l, t, planes, blink_ticks):
    """ What layer `l` shows at t over `planes`, the lamps and their red and green """
    if t < l['t0'] or (l['t1'] is not None and t >= l['t1']):
        return
    if l['blink'] and not (t // blink_ticks & 1):
        return
    v = l['clip']
    if l['win'] is not None:
        w = track_at(l['win'], t)
        if not w:
            return
        v = intersect(v, w[1:])
    if empty(v):
        return
    rows = row_mask(v[1], v[3])
    dx, dy = offset_at(l, t)
    fill, dots, through = l['fill'], l['dots'], let_through(l, t, rows)
    lamps, red, green = planes
    lit = l['lit']
    if dots:
        cols, hues, x0, y = dots['cols'], dots['hues'] if lit else None, dots['x'] + dx, dots['y'] + dy
        count = len(cols)
    for x in range(v[0], v[2]):
        m = rows if through is None else through[x]
        if not m:
            continue
        if fill:
            off = fill[x] & m
            lamps[x] &= ~off
            red[x] &= ~off
            green[x] &= ~off
        if dots:
            i = x - x0
            if 0 <= i < count and cols[i]:
                got = place(cols[i], y) & m
                red[x] &= ~got
                green[x] &= ~got
                if not lit:
                    lamps[x] &= ~got
                    continue
                lamps[x] |= got
                if hues and hues[0][i]:
                    red[x] |= place(hues[0][i], y) & m
                if hues and hues[1][i]:
                    green[x] |= place(hues[1][i], y) & m

def border_edges():
    """ The chasing border's four edges, each a run of lamps that steps along one place a step,
    as `_bordered` draws them: [clip, dots, offsets] for each, or None if one does not """
    phases = [sign._bordered([0] * WIDTH, (3 + 2 * s) % 5) for s in range(5)]
    edges = [([0, 0, WIDTH, 1], WIDTH, lambda j: (j, 0), False),
             ([0, ROWS - 1, WIDTH, ROWS], WIDTH, lambda j: (j, ROWS - 1), False),
             ([0, 1, 1, ROWS - 1], ROWS - 2, lambda j: (0, 1 + j), True),
             ([WIDTH - 1, 1, WIDTH, ROWS - 1], ROWS - 2, lambda j: (WIDTH - 1, 1 + j), True)]
    out = []
    for clip, length, at, down in edges:
        bits = [[lamps[at(q)[0]] >> at(q)[1] & 1 for q in range(length)] for lamps in phases]
        period = next((period for period in range(1, 6)
                       if all(bits[0][j] == bits[0][j + period] for j in range(length - period))), None)
        if period is None:
            return None
        ext = [bits[0][j if j < length else j - period * -(-(j - length + 1) // period)] for j in range(length + 4)]
        offsets = []
        for s in range(5):
            sigma = next((sigma for sigma in range(5) if all(bits[s][j] == ext[j + sigma] for j in range(length))),
                         None)
            if sigma is None:
                return None
            offsets.append([0, -sigma] if down else [-sigma, 0])
        if down:
            dots = {'x': clip[0], 'y': 1, 'cols': [sum(bit << q for q, bit in enumerate(ext))], 'hues': None}
        else:
            dots = {'x': 0, 'y': clip[1], 'cols': ext, 'hues': None}
        out.append((clip, dots, offsets))
    return out

def played(shown, end):
    """ `([[tick, lamps, red, green]], end)`: what the program plays, from 0, each held until
    the next """
    out, at = [], 0
    for n, (tick, *planes) in enumerate(shown):
        stop = shown[n + 1][0] if n + 1 < len(shown) else end
        if stop > tick:
            out.append([at] + [list(plane) for plane in planes])
            at += stop - tick
    return out, at

def build(frames, end, ops, shown):
    """ The layers of a run of the model: its `frames` and `end` as `sign_frames` gives them
    with its `ops`, and what it `shown` as `_shown` gives it """
    play, stop = played(shown, end)
    # a line left waiting at a 00 by one that has finished goes on from its own tick, so its
    # frames come after later ones; the program plays them on from where it was
    if any(frames[n][0] < frames[n - 1][0] for n in range(1, len(frames))):
        return {'end': stop, 'layers': [], 'spans': [[0, stop]], 'played': play}
    layers, live, rolls = [], {}, {}

    # An effect's layers go on taking its steps until another write lands in their window;
    # its next step then starts layers of its own over that write.
    def close(region, keep=None):
        for name in [name for name, got in live.items() if name != keep and overlaps(got['clip'], region)]:
            del live[name]

    def open_(name, clip, made, **kept):
        live[name] = dict(kept, clip=clip, layers=made)
        layers.extend(made)
        return made

    def strip(t, op, y):
        m = layer('travel', t, [op['a'], y[0], op['b'], y[1]])
        m['fill'] = rect_fill(op['a'], y[0], op['b'], y[1])
        m['dots'] = {'x': op['a'], 'y': 0, 'cols': list(op['before']), 'hues': [list(hue) for hue in op['before_hues']]}
        m['move'] = []
        m.update(a=op['a'], b=op['b'], mask=op['mask'], shifts=0)
        return m

    def shown_(t, op, y):
        m = layer('show', t, [op['a'], y[0], op['b'], y[1]])
        m['fill'] = rect_fill(op['a'], y[0], op['b'], y[1])
        m['dots'] = {'x': op['a'], 'y': 0, 'cols': op['page'], 'hues': op['hues']}
        m['win'] = []
        return m

    def side(t, op, y, name, cols, hues):
        # a build-up rotates the page into each byte of eight columns from its left, what was
        # shown going out at its right: the page shifted on, and what was shown, each through
        # a comb of the columns of each byte it has reached
        m = layer('build', t, [op['a'], y[0], op['b'], y[1]])
        m['fill'] = rect_fill(op['a'], y[0], op['b'], y[1])
        m['dots'] = {'x': op['a'], 'y': 0, 'cols': cols, 'hues': hues}
        m['move'] = []
        m['comb'] = {'side': name, 'steps': []}
        return m

    def rolled(roll, t):
        m = layer('roll', t, roll['clip'])
        m['fill'] = rect_fill(*roll['clip'])
        m['dots'] = roll['dots']
        m['move'] = []
        m['up'] = roll['up']
        return m

    for op in ops:
        # a line that goes on from its own tick after the other has finished writes after
        # what the other wrote, though at an earlier tick; with the frames in order, nothing
        # it wrote showed until the frame after it, so it takes effect from the one before
        t = max(op['t'], op['after'])
        kind = op['op']
        if kind == 'travel':
            key = 'travel%d' % op['k']
            y = rows_of(op['mask'])
            s = live[key]['layers'][0] if key in live else None
            if not s or s['a'] != op['a'] or s['b'] != op['b'] or s['mask'] != op['mask']:
                s = open_(key, [op['a'], y[0], op['b'], y[1]], [strip(t, op, y)])[0]
            s['dots']['cols'].append(op['column'])
            for hue, value in zip(s['dots']['hues'], op['hues']):
                hue.append(value)
            s['shifts'] += 1
            s['move'].append([t, -s['shifts'], 0])
            close(s['clip'], key)
        elif kind == 'show':
            # each step shows the page as it is then, which the other line can draw into
            key = 'show%d:%d' % (op['k'], op['id'])
            y = rows_of(op['window'])
            l = live[key]['layers'][0] if key in live else None
            if not l or l['dots']['cols'] != op['page'] or l['dots']['hues'] != op['hues']:
                l = open_(key, [op['a'], y[0], op['b'], y[1]], [shown_(t, op, y)])[0]
            y = rows_of(op['mask'])
            l['win'].append([t, op['start'], y[0], op['stop'], y[1]])
            close([op['start'], y[0], op['stop'], y[1]], key)
        elif kind == 'build':
            key = 'build%d:%d' % (op['k'], op['id'])
            y = rows_of(op['mask'])
            got = live.get(key)
            if not got:
                got = live[key] = {'clip': [op['a'], y[0], op['b'], y[1]],
                                   'layers': [side(t, op, y, 'shown', op['shown'], op['shown_hues'])]}
                layers.append(got['layers'][0])
            if got.get('page') != [op['page'], op['page_hues']]:
                # the other line drew into the page: its next step shifts the page as it is now
                if len(got['layers']) > 1:
                    got['layers'][1]['t1'] = t
                got['layers'][1:] = [side(t, op, y, 'page', op['page'], op['page_hues'])]
                got['page'] = [op['page'], op['page_hues']]
                layers.append(got['layers'][1])
            got['layers'][0]['move'].append([t, op['step'] + 1, 0])
            got['layers'][0]['comb']['steps'].append([t, op['step']])
            got['layers'][1]['move'].append([t, op['step'] - 7, 0])
            got['layers'][1]['comb']['steps'].append([t, op['step']])
            close([op['a'], y[0], op['b'], y[1]], key)
        elif kind == 'twinkle':
            # the page let through lamp by lamp: a layer of it, with each pass's lamps it changes
            key = 'twinkle%d:%d' % (op['k'], op['id'])
            got = live.get(key)
            if not got or got['page'] != [op['page'], op['hues']]:
                l = layer('twinkle', t, [0, 0, WIDTH, ROWS])
                l['fill'] = rect_fill(0, 0, WIDTH, ROWS)
                l['dots'] = {'x': 0, 'y': 0, 'cols': op['page'], 'hues': op['hues']}
                l['passes'] = []
                got = open_(key, l['clip'], [l], page=[op['page'], op['hues']])
            if any(op['changed']):
                live[key]['layers'][0]['passes'].append([t, op['changed']])
            close(bounds(op['touched']), key)
        elif kind == 'roll':
            h = op['bottom'] - op['top'] + 1
            if h <= 0:
                continue
            top = op['top']

            def stacked(shown, pages):
                # what was shown over the page, or under it rolling down
                cols = []
                for was, page in zip(shown, pages):
                    page = page >> top & (1 << h) - 1
                    was = was >> top & (1 << h) - 1
                    cols.append(was | page << h if op['up'] else page | was << h)
                return cols

            rolls[op['id']] = {'clip': [op['a'], top, op['b'], op['bottom'] + 1], 'up': op['up'],
                               'dots': {'x': op['a'], 'y': top if op['up'] else top - h,
                                        'cols': stacked(op['shown'], op['page']),
                                        'hues': [stacked(shown, page) for shown, page in
                                                 zip(op['shown_hues'], op['page_hues'])]}}
        elif kind == 'rollstep':
            key = 'roll%d' % op['id']
            if op['id'] not in rolls:
                continue
            l = live[key]['layers'][0] if key in live else None
            if not l:
                l = open_(key, rolls[op['id']]['clip'], [rolled(rolls[op['id']], t)])[0]
            l['move'].append([t, 0, -(op['step'] + 1) if l['up'] else op['step'] + 1])
            close(l['clip'], key)
        elif kind == 'unborder':
            l = layer('clear', t, [0, 0, WIDTH, ROWS])
            l['fill'] = [row_mask(1, ROWS - 1) if x < 8 or x >= WIDTH - 8 else 0 for x in range(WIDTH)]
            layers.append(l)
            close([0, 1, 8, ROWS - 1])
            close([WIDTH - 8, 1, WIDTH, ROWS - 1])
    layers = [l for l in layers if l['passes'] != []]
    retire(layers)

    # flashing lamps go dark in the second half of each blink, a layer each pattern
    i = 0
    while i < len(frames):
        flashing = list(frames[i][2])
        j = i + 1
        while j < len(frames) and list(frames[j][2]) == flashing:
            j += 1
        if any(flashing):
            l = layer('flash', frames[i][0], [0, 0, WIDTH, ROWS])
            l['t1'] = frames[j][0] if j < len(frames) else end
            l['dots'] = {'x': 0, 'y': 0, 'cols': flashing, 'hues': None}
            l['lit'] = False
            l['blink'] = True
            layers.append(l)
        i = j

    # the chasing border over all of it, from the tick it started
    edges = border_edges()
    i = 0
    while i < len(frames):
        j = i + 1
        while j < len(frames) and frames[j][3] == frames[i][3]:
            j += 1
        if frames[i][3] is not None:
            t1 = frames[j][0] if j < len(frames) else end
            band = layer('border', frames[i][0], [0, 0, WIDTH, ROWS])
            band['t1'] = t1
            band['fill'] = [row_mask(0, ROWS) if x < 8 or x >= WIDTH - 8 else 1 | 1 << ROWS - 1 for x in range(WIDTH)]
            layers.append(band)
            for clip, dots, offsets in edges or ():
                l = layer('border', frames[i][0], clip)
                l['t1'] = t1
                l['dots'] = dots
                l['cycle'] = {'from': frames[i][3] + sign.SIGN_FRAME_TICKS, 'period': sign.SIGN_BORDER_TICKS,
                              'offsets': offsets}
                layers.append(l)
        i = j
    return {'end': end, 'layers': layers, 'spans': [], 'played': play}

def cover(l):
    """ What a layer covers for good from some tick on, if anything: (rect, tick) - a build-up's
    two sides together, so its first """
    if l['kind'] in ('travel', 'roll') or l['kind'] == 'build' and l['comb']['side'] == 'shown':
        return l['clip'], l['t0']
    if l['kind'] == 'show' and l['win']:
        last = l['win'][-1]
        return intersect(l['clip'], last[1:]), last[0]
    return None

def extent(l):
    if l['kind'] == 'twinkle':
        cols = [0] * WIDTH
        for _, changed in l['passes']:
            cols = [a | b for a, b in zip(cols, changed)]
        return bounds(cols)
    return bounds(l['fill']) if l['kind'] == 'clear' else l['clip']

def retire(layers):
    """ A layer a later one covers for good goes when it does """
    reach = [extent(l) for l in layers]
    for j in range(1, len(layers)):
        got = cover(layers[j])
        if not got:
            continue
        for i in range(j):
            if contains(got[0], reach[i]) and (layers[i]['t1'] is None or layers[i]['t1'] > got[1]):
                layers[i]['t1'] = max(got[1], layers[i]['t0'])

def breakpoints(scene, shown, blink_ticks):
    """ Every tick at which the layers or the frames can change """
    end, ticks = scene['end'], set()

    def add(t):
        if 0 <= t < end:
            ticks.add(t)

    for frame in shown:
        add(frame[0])
    for l in scene['layers']:
        stop = end if l['t1'] is None else min(l['t1'], end)
        add(l['t0'])
        if l['t1'] is not None:
            add(l['t1'])
        for entry in (l['move'] or []) + (l['win'] or []) + (l['passes'] or []):
            add(entry[0])
        if l['cycle']:
            for t in range(l['cycle']['from'], stop, l['cycle']['period']):
                if t >= l['t0']:
                    add(t)
        if l['blink']:
            for t in range(-(-l['t0'] // blink_ticks) * blink_ticks, stop, blink_ticks):
                add(t)
    return sorted(ticks)

def verify(scene, blink_ticks):
    """ Hold the layers against the frames the program plays at every breakpoint; spans where
    they differ go to the frames themselves. Returns how many breakpoints differed. """
    shown, layers = scene['played'], scene['layers']
    ticks = breakpoints(scene, shown, blink_ticks)
    spans, frame, bad = [list(span) for span in scene['spans']], 0, 0
    if not layers:
        return 0
    # the layers shown at each tick, in order, as the ticks go by
    starts = sorted(range(len(layers)), key=lambda i: layers[i]['t0'])
    alive, started = [], 0
    for n, t in enumerate(ticks):
        while frame + 1 < len(shown) and shown[frame + 1][0] <= t:
            frame += 1
        while started < len(starts) and layers[starts[started]]['t0'] <= t:
            bisect.insort(alive, starts[started])
            started += 1
        alive = [i for i in alive if layers[i]['t1'] is None or layers[i]['t1'] > t]
        planes = [[0] * WIDTH for _ in range(3)]
        for i in alive:
            paint(layers[i], t, planes, blink_ticks)
        if planes == shown[frame][1:]:
            continue
        bad += 1
        after = ticks[n + 1] if n + 1 < len(ticks) else scene['end']
        if spans and spans[-1][1] == t:
            spans[-1][1] = after
        else:
            spans.append([t, after])
    scene['spans'] = spans
    return bad

def scene_of(payload, run=None):
    """ A payload's run of the model as verified layers, `run` its `(frames, end, ops, shown)`
    where the model has played it already, `shown` as `_shown` gives it with `hues` """
    if run is None:
        ops = []
        frames, end = sign.sign_frames(payload, ops)
        run = frames, end, ops, sign._shown(frames, end, hues=True)
    scene = build(*run)
    verify(scene, sign.SIGN_BLINK_TICKS)
    return scene

# --- the layers as SVG that SMIL animates ---

# Lit lamps are drawn in tiles of this many columns, so a long travelling strip is not one
# path the browser draws whole each step
TILE = 64

# The colours a lamp is lit in, by its red and green: none given, red, green and yellow, each
# drawn through a lamp pattern of its own
ON = ('on', 'on1', 'on2', 'on3')

# The sign, its unlit lamps and its lit ones in each colour, `#rrggbb`, and how bright a lit
# lamp of each colour is at each of the four levels a sign's `01 02` byte can set it to. Red and
# green are a lit sign's in a photograph of one, a deep red and the yellow-green of GaP LEDs;
# yellow is the two lit together, their light added
SIGN_COLOURS = (('sign', '#080604'), ('lamp-off', '#221a0c'), ('lamp-on', '#ffffff'), ('lamp-red', '#ff1a12'),
                ('lamp-green', '#d1ff19'), ('lamp-yellow', '#ffce19'))
LEVELS = ('.28', '.5', '.75', '1')

# The lamps as the SVG and the pages draw them, each colour at its level: unless a sign's byte
# sets it, the brightest
SVG_LAMPS = ('.ground{fill:var(--sign)}.dim,.on,.on1,.on2,.on3{stroke-width:var(--lamp);stroke-linecap:var(--cap);'
             'fill:none}.dim{stroke:var(--lamp-off)}' + ''.join(
                 '.%s{stroke:var(--%s);stroke-opacity:var(--level-%d,1)}' % (on, colour, n)
                 for n, (on, (colour, _)) in enumerate(zip(ON, SIGN_COLOURS[2:]))))
SVG_STYLE = ':root{%s;--lamp:.78;--cap:round}' % ';'.join('--%s:%s' % pair for pair in SIGN_COLOURS) + SVG_LAMPS

# Where a scene's `shades` say, its frames' two colour planes are a level of brightness instead: a
# keyed swing's, full as a lamp of no colour and the rest in the same colour, three quarters, half
# and a quarter as bright
SHADES = ('sh1', 'sh2', 'sh3')
SVG_SHADES = ('.sh1,.sh2,.sh3{stroke-width:var(--lamp);stroke-linecap:var(--cap);fill:none;stroke:var(--lamp-on)}' +
              ''.join('.%s{stroke-opacity:calc(var(--level-0,1)*%s)}' % (sh, level)
                      for sh, level in zip(SHADES, ('.75', '.5', '.28'))))

def levels_style(levels):
    """ The levels a sign given `01 02` and the byte `levels`, in hex, lights each colour at, as
    CSS: two bits a colour, from bit 0 """
    return ';'.join('--level-%d:%s' % (n, LEVELS[int(levels, 16) >> 2 * n & 3]) for n in range(4))

def fraction(p, whole):
    """ The keyTime of `p` milliseconds into `whole`, cut to seven places, so that a clock of
    whole milliseconds reaches it on the millisecond `p` """
    n = p * 10000000 // whole
    return '1' if n >= 10000000 else '0' if n <= 0 else ('0.%07d' % n).rstrip('0')

def ms(x):
    """ Milliseconds, to three places """
    n = math.floor(x * 1000 + 0.5)
    return '%d' % (n // 1000) + ('.%03d' % (n % 1000)).rstrip('0').rstrip('.')

# Every animation starts again with the loop, a `set` as long as the program, and lasts no
# longer than it, so nothing is held over from one loop to the next. Firefox times SMIL in
# whole milliseconds, so each tick's steps are on the millisecond nearest it, which shows the
# tick until the next one's in every browser, and one that repeats within the loop does so
# in chunks that last a whole number of milliseconds.
def grid(tick_ms):
    """ The fewest ticks that are a whole number of milliseconds, or None if a thousand aren't """
    return next((g for g in range(1, 1001) if abs(g * tick_ms - math.floor(g * tick_ms + 0.5)) < 1e-9), None)

def steps(entries, before):
    """ `[[tick, value]]` from 0, each value from its tick on, none the same as the one before """
    out = [[0, before]]
    for t, value in entries:
        if t <= 0:
            out[0] = [0, value]
        elif out[-1][0] == t:
            out[-1][1] = value
        elif out[-1][1] != value:
            out.append([t, value])
    kept = [out[0]]
    for entry in out[1:]:
        if entry[1] != kept[-1][1]:
            kept.append(entry)
    return kept

def runs(track):
    """ `[(tick, apart, step, count)]`, a stepped track's changes as runs of equal steps equally
    far apart, a value or a list of them """
    out = []
    for n in range(1, len(track)):
        t, value = track[n]
        before = track[n - 1][1]
        step = [a - b for a, b in zip(value, before)] if isinstance(value, list) else value - before
        if out:
            first, apart, same, count = out[-1]
            gap = t - first - apart * (count - 1)
            if same == step and (count == 1 or gap == apart):
                out[-1] = (first, gap if count == 1 else apart, same, count + 1)
                continue
        out.append((t, 0, step, 1))
    return out

# The four sides of a lamp, as the neighbour past each and the edge along it that keeps the
# lamp on its left: top, left, bottom, right
SIDES = ((0, -1, 1, 0, 0, 0), (-1, 0, 0, 0, 0, 1), (0, 1, 0, 1, 1, 1), (1, 0, 1, 1, 1, 0))

def corners(edges):
    """ Lamps' sides that face no lit lamp, `(ax, ay, bx, by)` each with its lamp on its left, as
    the outlines round them, the corners of each in order, turning in at a corner two only touch """
    starts = {}
    for n, (ax, ay, _, _) in enumerate(edges):
        starts.setdefault((ax, ay), []).append(n)
    used, out = [False] * len(edges), []
    for first in range(len(edges)):
        if used[first]:
            continue
        points, e = [], first
        while not used[e]:
            used[e] = True
            ax, ay, bx, by = edges[e]
            points.append((ax, ay))
            dx, dy = bx - ax, by - ay
            # the left turn, straight on, the right turn, back where it began closing it
            choices = [c for c in starts.get((bx, by), ()) if not used[c] or c == first]
            e = next((c for turn in ((dy, -dx), (dx, dy), (-dy, dx)) for c in choices
                      if (edges[c][2] - bx, edges[c][3] - by) == turn), first)
        # from the first corner, each next corner
        n = len(points)
        corner = lambda i: (points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]) != \
            (points[(i + 1) % n][0] - points[i][0], points[(i + 1) % n][1] - points[i][1])
        start = next(i for i in range(n) if corner(i))
        out.append([points[(start + i) % n] for i in range(n) if corner((start + i) % n)])
    return out

def outline(edges):
    """ The outlines of `corners`, a subpath each """
    out, last = '', None
    for loop in corners(edges):
        x, y = loop[0]
        out += 'M%d %d' % (x, y) if last is None else 'm%d %d' % (x - last[0], y - last[1])
        for cx, cy in loop[1:]:
            out += 'h%d' % (cx - x) if cy == y else 'v%d' % (cy - y)
            x, y = cx, cy
        out += 'z'
        last = loop[0]
    return out

def cols_edges(cols, x0=0, y0=0):
    """ The sides of lamps given as columns of bits, the first at column x0, bit 0 at row y0, that
    face no lit lamp, as `outline` takes them """
    edges = []
    for i, col in enumerate(cols):
        # its lamps' sides that face none, in the order of SIDES
        facing = (col & ~(col << 1), col & ~(cols[i - 1] if i else 0), col & ~(col >> 1),
                  col & ~(cols[i + 1] if i + 1 < len(cols) else 0))
        for y in range(col.bit_length()):
            for (_, _, ax, ay, bx, by), bits in zip(SIDES, facing):
                if bits >> y & 1:
                    edges.append((x0 + i + ax, y0 + y + ay, x0 + i + bx, y0 + y + by))
    return edges

def cols_outline(cols, x0=0, y0=0):
    """ The outlines of lamps given as columns of bits, the first at column x0, bit 0 at row y0 """
    return outline(cols_edges(cols, x0, y0))

def tiles(dots):
    """ A layer's lamps as outlines, a path for each tile of columns """
    out = []
    for i in range(0, len(dots['cols']), TILE):
        d = cols_outline(dots['cols'][i:i + TILE], dots['x'] + i, dots['y'])
        if d:
            out.append(d)
    return out

def lamps_path(lamps):
    return ''.join(tiles({'x': 0, 'y': 0, 'cols': lamps})) or 'M0 0'

def coloured(cols, hues):
    """ Lamps given as columns of bits, and their red and green, as the lamps of each colour """
    if not hues:
        return [cols, [0] * len(cols), [0] * len(cols), [0] * len(cols)]
    return [[c & (r if colour & 1 else ~r) & (g if colour & 2 else ~g) for c, r, g in zip(cols, *hues)]
            for colour in range(4)]

def _plain(value):
    """ What JSON of `value` holds, as JS writes it: a whole float as a whole number """
    if isinstance(value, float) and value == int(value):
        return int(value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value

def sequence(scenes):
    """ Scenes played one after another, each on a sign of its own, as one """
    layers, spans, shades, shown, at = [], [], [], [], 0
    for scene in scenes:
        for l in scene['layers']:
            l = dict(l, t0=l['t0'] + at, t1=(scene['end'] if l['t1'] is None else l['t1']) + at, blink_from=at)
            for name in ('move', 'win', 'passes'):
                if l[name] is not None:
                    l[name] = [[e[0] + at] + e[1:] for e in l[name]]
            if l['comb']:
                l['comb'] = dict(l['comb'], steps=[[t + at, s] for t, s in l['comb']['steps']])
            if l['cycle']:
                l['cycle'] = dict(l['cycle'], **{'from': l['cycle']['from'] + at})
            layers.append(l)
        spans += [[s + at, e + at] for s, e in scene['spans']]
        shades += [[s + at, e + at] for s, e in scene.get('shades', ())]
        shown += [[frame[0] + at] + list(frame[1:]) for frame in scene['played']]
        at += scene['end']
    return {'end': at, 'layers': layers, 'spans': spans, 'played': shown, 'shades': shades}

def poster(scene):
    """ The tick of the display the program holds longest, the first if more than one """
    best, at, played = -1, 0, scene['played']
    for n, (t, *_) in enumerate(played):
        held = (played[n + 1][0] if n + 1 < len(played) else scene['end']) - t
        if held > best:
            best, at = held, t
    return at

def svg(scene, meta, params, prefix='sr_', px=8):
    """ The scene as an SVG of its own that plays it: `meta` goes in its metadata as JSON,
    `params` as `sign_parameters` gives them time it, and its ids start with `prefix`, which
    SMIL's timing reads a hyphen in as a minus. Where nothing animates it, it shows the display
    held longest. """
    tick_ms, blink_ticks, end = params['tick_ms'], params['blink_ticks'], scene['end']
    loop = max(1, math.ceil(end * tick_ms))
    g = grid(tick_ms)
    at = poster(scene)
    begin, finish = prefix + 'L.begin', prefix + 'L.end'
    defs, body, clips = [], [], {}

    def stamp(t):
        return math.floor(t * tick_ms + 0.5)

    def when(t):
        return '%s+%dms' % (begin, stamp(t))

    def text(value):
        return '%d %d' % tuple(value) if isinstance(value, list) else '%d' % value

    def times(value, k):
        return [v * k for v in value] if isinstance(value, list) else value * k

    def base(track):
        return track_at(track, at)[1]

    def whole(ticks):
        # how many times `ticks` are the fewest that last a whole number of milliseconds, or 0
        # if too many
        n = g // math.gcd(ticks, g) if g else 0
        return n if n <= 64 else 0

    def held(head, values, at, start):
        # values from `start` milliseconds into the loop, each `at` milliseconds after it, the
        # last held to the loop's end
        if len(values) == 1:
            return '%s additive="sum" values="%s" begin="%s+%dms" end="%s"/>' % (head, values[0], begin, start, finish)
        return '%s additive="sum" calcMode="discrete" values="%s" keyTimes="%s" begin="%s+%dms" dur="%dms"/>' % (
            head, ';'.join(values), ';'.join(fraction(p, loop - start) for p in at), begin, start, loop - start)

    def functional(name, attribute, track, kind=''):
        # a stepped track as its value at the loop's start and each run of equal steps after it,
        # every step added on to the ones before
        head = '<%s attributeName="%s"%s' % (name, attribute, kind)
        out = '%s values="%s" begin="%s" end="%s"/>' % (head, text(track[0][1]), begin, finish)
        for t, apart, step, count in runs([e for e in track if e[0] < end]):
            first = stamp(t)
            at = [stamp(t + k * apart) - first for k in range(count)]
            chunk = max(2, whole(apart)) if whole(apart) else 0
            done = count // chunk * chunk if chunk and count >= 2 * chunk else 0
            if done:
                # a chunk of steps repeated, each time on from where the last left off
                out += '%s additive="sum" accumulate="sum" calcMode="discrete" values="%s" keyTimes="%s" begin="%s" ' \
                       'dur="%dms" repeatCount="%d"/>' % (
                           head, ';'.join(text(times(step, k + 1)) for k in range(chunk)),
                           ';'.join(fraction(at[k], at[chunk]) for k in range(chunk)), when(t), at[chunk],
                           done // chunk)
            if done < count:
                # the steps after the chunks
                out += held(head, [text(times(step, k + 1)) for k in range(done, count)],
                            [p - at[done] for p in at[done:]], first + at[done])
            else:
                # where the chunks leave off
                out += held(head, [text(times(step, count))], [0], first + done // chunk * at[chunk])
        return out

    def shown(t0, stop):
        # on for ticks [t0, stop): its display where nothing animates it, and when it is otherwise
        visible = t0 <= at < stop
        out = ''
        spans = [(t0, stop)] if not visible else [(0, t0), (stop, end)]
        for a, b in spans:
            if a < b and a < end:
                out += '<set attributeName="display" to="%s" begin="%s" end="%s"/>' % (
                    'none' if visible else 'inline', when(a), finish if b >= end else when(b))
        return ('inline' if visible else 'none'), out

    def chunked(name, attribute, period, count, start, values, kind=''):
        # values[0] until `start`, then values[1 + k] from `k` periods after it, in a chunk
        # of `count` periods that starts on a whole millisecond and repeats until the loop ends
        r = start % g if g and g < period else 0
        ticks = ([0] if r else []) + [r + k * period for k in range(count)]
        return ('<%s attributeName="%s"%s calcMode="discrete" values="%s" keyTimes="%s" begin="%s+%sms" '
                'dur="%sms" repeatCount="indefinite" end="%s"/>' % (
                    name, attribute, kind, ';'.join((values[:1] if r else []) + values[1:count + 1]),
                    ';'.join(fraction(stamp(t), stamp(count * period)) for t in ticks), begin,
                    ms((start - r) * tick_ms), ms(count * period * tick_ms), finish))

    sides = (('x', lambda r: r[0]), ('y', lambda r: r[1]), ('width', lambda r: max(0, r[2] - r[0])),
             ('height', lambda r: max(0, r[3] - r[1])))
    for l in scene['layers']:
        if l['t1'] is not None and l['t1'] <= l['t0']:
            continue
        n = len(body)
        stop = end if l['t1'] is None else min(l['t1'], end)
        display, sets = shown(l['t0'], stop) if l['t0'] > 0 or stop < end else ('inline', '')
        out = '<g%s>%s' % ('' if display == 'inline' else ' display="none"', sets)
        # the window it shows in
        if l['win'] is not None:
            rects = [[w[0], intersect(l['clip'], w[1:])] for w in l['win']]
            clip = steps(rects, rects[0][1] if rects else l['clip'])
        else:
            clip = [[0, l['clip']]]
        rect = '<rect %s>' % ' '.join('%s="%d"' % (name, of(base(clip))) for name, of in sides)
        if len(clip) > 1:
            for name, of in sides:
                side = steps([[t, of(r)] for t, r in clip[1:]], of(clip[0][1]))
                if len(side) > 1:
                    rect += functional('animate', name, side)
            window = ' clip-path="url(#%sc%d)"' % (prefix, n)
            defs.append('<clipPath id="%sc%d">%s</rect></clipPath>' % (prefix, n, rect))
        elif clip[0][1] == [0, 0, WIDTH, ROWS]:
            window = ''
        else:
            if tuple(clip[0][1]) not in clips:
                clips[tuple(clip[0][1])] = n
                defs.append('<clipPath id="%sc%d">%s</rect></clipPath>' % (prefix, n, rect))
            window = ' clip-path="url(#%sc%d)"' % (prefix, clips[tuple(clip[0][1])])
        mask = ''
        if l['comb']:
            # a build-up's comb, the columns of each byte it lets through
            part = steps([[t, comb_at(l['comb'], t)] for t, _ in l['comb']['steps']], comb_at(l['comb'], l['t0']))
            now = comb_at(l['comb'], max(at, l['t0']))
            comb = '<rect x="%d" width="%d" height="%d" fill="#fff">' % (now[0], now[1] - now[0], ROWS)
            for name, of in (('x', lambda c: c[0]), ('width', lambda c: c[1] - c[0])):
                track = steps([[t, of(c)] for t, c in part[1:]], of(part[0][1]))
                if len(track) > 1:
                    comb += functional('animate', name, track)
            defs.append('<pattern id="%sk%d" width="8" height="%d" patternUnits="userSpaceOnUse">%s</rect></pattern>'
                        '<mask id="%sm%d" maskUnits="userSpaceOnUse" x="0" y="0" width="%d" height="%d"><rect '
                        'width="%d" height="%d" fill="url(#%sk%d)"/></mask>' % (
                            prefix, n, ROWS, comb, prefix, n, WIDTH, ROWS, WIDTH, ROWS, prefix, n))
            mask = ' mask="url(#%sm%d)"' % (prefix, n)
        elif l['passes']:
            # a twinkle's passes, each letting through the lamps it changes from its tick on
            reveal = ''
            for t, cols in l['passes']:
                display, sets = shown(t, end)
                reveal += '<path fill="#fff"%s d="%s">%s</path>' % (
                    '' if display == 'inline' else ' display="none"', cols_outline(cols), sets)
            defs.append('<mask id="%sm%d" maskUnits="userSpaceOnUse" x="0" y="0" width="%d" height="%d">%s</mask>' % (
                prefix, n, WIDTH, ROWS, reveal))
            mask = ' mask="url(#%sm%d)"' % (prefix, n)
        out += '<g%s%s>' % (window, mask)
        closing = '</g></g>'
        if l['blink']:
            # dark in the second half of each blink, counted from the start of its message
            start = l.get('blink_from', 0)
            count = 2 * (whole(2 * blink_ticks) or 1)
            out += '<g opacity="%d">' % ((at - start) // blink_ticks & 1 if at >= start else 0)
            out += chunked('animate', 'opacity', blink_ticks, count, start,
                           ['1'] + ['1' if k & 1 else '0' for k in range(count)])
            closing = '</g>' + closing
        if l['fill']:
            out += '<path fill="url(#%soff)" d="%s"/>' % (prefix, cols_outline(l['fill']))
        if l['dots']:
            if l['move'] is not None:
                move = steps([[m[0], [m[1], m[2]]] for m in l['move']], [0, 0])
                out += '<g%s>' % (' transform="translate(%d %d)"' % tuple(base(move)) if base(move) != [0, 0] else '')
                if len(move) > 1:
                    out += functional('animateTransform', 'transform', move, ' type="translate"')
            if l['cycle']:
                c = l['cycle']
                offsets = ['%d %d' % tuple(o) for o in c['offsets']]
                count = len(offsets) * (whole(len(offsets) * c['period']) or 1)
                out += '<g transform="translate(%s)">' % offsets[0]
                out += chunked('animateTransform', 'transform', c['period'], count, c['from'],
                               [offsets[k % len(offsets)] for k in range(count + 1)], ' type="translate"')
            if l['lit']:
                out += ''.join('<path fill="url(#%s%s)" d="%s"/>' % (prefix, ON[colour], d)
                               for colour, cols in enumerate(coloured(l['dots']['cols'], l['dots']['hues']))
                               for d in tiles(dict(l['dots'], cols=cols)))
            else:
                out += '<path fill="url(#%soff)" d="%s"/>' % (prefix, cols_outline(l['dots']['cols'], l['dots']['x'],
                                                                                    l['dots']['y']))
            out += '</g>' * ((l['move'] is not None) + bool(l['cycle']))
        body.append(out + closing)

    # what the layers can't show, the frames the program plays show over them, a path each
    # colour they light
    frames = ''
    if scene['spans']:
        played, shades = scene['played'], scene.get('shades', ())
        runs_ = steps([e for s, t in scene['spans'] for e in ([s, 'inline'], [t, 'none']) if e[0] < end], 'none')
        drawn = [[] for _ in ON + SHADES]
        for s, e in scene['spans']:
            for t, frame in [(s, track_at(played, s))] + [(frame[0], frame) for frame in played if s < frame[0] < e]:
                split, none = coloured(frame[1], frame[2:]), [0] * len(frame[1])
                split = [split[0], none, none, none] + split[1:] if any(a <= t < b for a, b in shades) else \
                    split + [none] * len(SHADES)
                for track, cols in zip(drawn, split):
                    track.append([t, lamps_path(cols)])

        def keyed(attribute, track):
            return '<animate attributeName="%s" calcMode="discrete" values="%s" keyTimes="%s" begin="%s" dur="%dms"/>' % (
                attribute, ';'.join(v for _, v in track), ';'.join(fraction(stamp(t), loop) for t, _ in track),
                begin, loop)

        paths = ''
        for colour, track in enumerate(drawn):
            track = steps(track, track[0][1])
            if colour and track == [[0, 'M0 0']]:
                continue
            paths += '<path fill="url(#%s%s)" d="%s">%s</path>' % (prefix, (ON + SHADES)[colour], base(track),
                                                                  keyed('d', track) if len(track) > 1 else '')
        frames = '<g display="%s">%s<rect fill="url(#%soff)" width="%d" height="%d"/>%s</g>' % (
            base(runs_), keyed('display', runs_) if len(runs_) > 1 else '', prefix, WIDTH, ROWS, paths)

    label = html.escape(' / '.join(meta.get('text', ())) or 'Silent Radio')
    data = json.dumps(_plain(meta), separators=(',', ':'), ensure_ascii=False).replace('<', '\\u003c').replace(
        '&', '\\u0026')
    levels = ' style="%s"' % levels_style(meta['levels']) if meta.get('levels') else ''
    shaded = bool(scene['spans'] and scene.get('shades'))
    lit = ''.join('<pattern id="%s%s" width="1" height="1" patternUnits="userSpaceOnUse"><path class="%s" '
                  'd="M.5 .5h0"/></pattern>' % (prefix, on, on) for on in ON + (SHADES if shaded else ()))
    return ('<svg xmlns="http://www.w3.org/2000/svg" class="silent-radio" viewBox="0 0 %d %d" width="%d" height="%d"%s '
            'role="img" aria-label="%s"><title>%s</title><metadata id="%sdata">%s</metadata><style>%s</style><defs>'
            '<pattern id="%soff" width="1" height="1" patternUnits="userSpaceOnUse"><rect class="ground" width="1" '
            'height="1"/><path class="dim" d="M.5 .5h0"/></pattern>%s%s</defs><rect fill="url(#%soff)" '
            'width="%d" height="%d"/><rect width="0" height="0"><set id="%sL" attributeName="x" to="0" '
            'begin="0s;%s" dur="%dms"/></rect><g class="layers">%s</g>%s</svg>' % (
                WIDTH, ROWS, WIDTH * px, ROWS * px, levels, label, label, prefix, data,
                SVG_STYLE + (SVG_SHADES if shaded else ''), prefix, lit,
                ''.join(defs), prefix, WIDTH, ROWS, prefix, finish, loop, ''.join(body), frames))
