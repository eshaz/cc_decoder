#!/usr/local/bin/python
# coding: utf-8
""" The keyed swings: parts of a message's own frames turned and moved in 3D over the firmware's
frames along key poses, and the sign's lamps they light every display frame, as an SVG that plays
them. `POLAR_CASES` holds the messages keyed so, by the SHA-1 of the bytes the decoder plays, and
how each part is taken from the message's own frames. polar.js is the same, function for function,
for the players.

Public domain / Unlicense, as with the rest of ccDecoder.
"""

import hashlib
import html
import json
import math

import numpy as np

from lib.silentradio import scene, sign

WIDTH, ROWS = sign.SIGN_WIDTH, sign.SIGN_ROWS

# The viewer is D lamps in front of each part's own pivot
D = 400
RAD = math.pi / 180
IDENTITY = (1, 0, 0, 0, 1, 0, 0, 0, 1)

# A part settling on a key swings about it 7 times a second, damped
SWING = 2 * math.pi * 7 / 1000
DAMPING = 0.35
WD = SWING * math.sqrt(1 - DAMPING * DAMPING)

# The least brightness of each level a lamp is lit at: full, three quarters, half, a quarter; the
# SVG draws each level as a colour plane, which levels '1b' lights at 1, .75, .5 and .28
LEVELS = (0.875, 0.625, 0.39, 0.08)
POLAR_LEVELS = '1b'
BAYER = (0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5)

# How a part fades behind the sign's plane, and the motion trail: each lamp as bright as the
# brightest it has been over the last POLAR_TRAIL_MS, that light halving every POLAR_TRAIL_STEP_MS
POLAR_FADE = 'focus'
POLAR_TRAIL_MS = 80
POLAR_TRAIL_STEP_MS = 20

# How far behind the sign's plane a part fades out over, in lamps a millisecond of its message
POLAR_DEPTH_PER_MS = 100 / 35750

POLAR_NOTE = 'the keyed motion, invented: not in the bytes'

ROW_BITS = np.arange(ROWS)[:, None]
DITHER = (np.array(BAYER)[(ROW_BITS & 3) * 4 + (np.arange(WIDTH)[None, :] & 3)] + 0.5) / 16

def _key(t, w=(0, 0, 0), d=(0, 0, 0), mode='smooth', roll=0, stretch=1):
    return {'t': t, 'w': list(w), 'd': list(d), 'mode': mode, 'roll': roll, 'stretch': stretch}

def _swing(angle, tilt):
    """ A turn of `angle` degrees about the vertical axis leant `tilt` degrees toward the viewer """
    return 0, angle * math.cos(math.radians(tilt)), angle * math.sin(math.radians(tilt))

# The messages keyed, by the SHA-1 of their payload. Each part is drawn as the message's own frame
# at `at` ms shows it, inside `rect` [x0, y0, x1, y1] if given; `pages` makes a part of each display
# held `held` ms or more that starts before `before`, keyed from its start with `in` and from the
# next one's with `out`. A clear darkens the firmware's lamps in `rect`, or the lamps of the part
# `mask` names, over [t0, t1) ms; a case that `keeps` its own motion never hands back to the
# firmware.
POLAR_CASES = {
    # KCET_1992 9f15, and 9f3f in a later pass: the ball comes in to the racket where the firmware
    # draws them together, the racket swings through it from behind the sign and the ball leaves
    # the strings; then WOMEN'S comes in, and TENNIS once it has landed
    'a8bd4a1eb0c477d0463757d1884b90ed19a70c99': {
        'id': 'racket', 'title': "KCET 9f15, WOMEN'S TENNIS", 'window': [800, 4000],
        'clears': [{'t0': 980, 't1': 3000, 'rect': [0, 0, 49, 15]}, {'t0': 2909, 't1': 3630, 'rect': [49, 0, 112, 15]}],
        'parts': [
            {'id': 'racket', 'at': 2700, 'rect': [0, 0, 49, 15], 'pivot': [47, 7.5], 'tip': [0, 7.5],
             'keys': [_key(980, _swing(-70, 8), (0, 0, -140), 'hold', -15),
                      _key(2300, _swing(-70, 8), (0, 0, -40), 'glide', -15), _key(2516),
                      _key(2700, _swing(55, 8), (0, 0, 40), 'hold', 25), _key(3000, mode='glide')]},
            {'id': 'ball', 'at': 2400, 'pivot': [12, 7], 'tip': [12, 7],
             'keys': [_key(980, d=(33, 0, 200)), _key(2516, mode='linear')],
             'launch': {'from': 'racket', 'at': 2516, 'point': [12, 7], 'gain': 1.0, 'until': 2766}},
            {'id': "WOMEN'S", 'at': 3300, 'rect': [49, 0, 112, 7], 'pivot': [80, 3.5], 'tip': [80, 3.5],
             'keys': [_key(2910, d=(0, -7, 0), mode='hold'), _key(3090, d=(0, -3.5, 0)), _key(3270, mode='hold'),
                      _key(3630, mode='hold')]},
            {'id': 'TENNIS', 'at': 3300, 'rect': [49, 8, 112, 15], 'pivot': [80, 11.5], 'tip': [80, 11.5],
             'keys': [_key(3270, d=(0, 8, 0), mode='hold'), _key(3450, d=(0, 4, 0)), _key(3630, mode='hold')]}]},
    # WTTW_1989 88ef: the gavel swings down onto the block and bounces to rest, then arcs out toward
    # the viewer and stays there
    '4631cff30e333f06ac7f65bb1d7317280183d39e': {
        'id': 'gavel', 'title': 'WTTW 88ef, THE VERDICT IS IN: NOT GUILTY', 'window': [3300, 5400], 'keeps': True,
        'clears': [{'t0': 3579, 't1': 4886, 'rect': [0, 0, 65, 13]}],
        'parts': [
            {'id': 'gavel', 'at': 4000, 'rect': [0, 0, 65, 13], 'pivot': [58, 6.5], 'tip': [4, 6.5],
             'keys': [_key(3579, (0, 35, 20), mode='hold'), _key(3760, mode='fast>bounce'), _key(4335, mode='hold'),
                      _key(4886, _swing(75, -5), (0, 0, 60), 'hold'),
                      _key(5400, _swing(75, -5), (0, 0, 60), 'hold')]}]},
    # WTTW_1989 8a09: each page the message puts up at once scrolls in from the right and in front of
    # the sign and settles on its place, and falls out to the left behind it as the next comes
    '5afb356918ffa4cb71abedceeb15c902d3dd9990': {
        'id': 'report', 'title': 'WTTW 8a09, SPORTS REPORT', 'window': [900, 6400],
        'pages': {'held': 500, 'before': 6000, 'pivot': [56, 7.5], 'tip': [0, 7.5],
                  'in': [_key(0, d=(125, 0, 80), mode='fast'), _key(450, mode='settle'), _key(850, mode='hold')],
                  'out': [_key(0, mode='hold'), _key(351, d=(-150, 0, -120), mode='fast')]}},
    # KCET_1992 9f17: the pitch spins in from in front of the sign, the bat's hands move on an arc
    # about the batter - radius, azimuth and elevation about a centre right of the sign - as it
    # swings through the ball where the firmware draws them together, and the ball leaves the barrel
    '4d0b817e307b7188ddd99b971047b0f76e9f2507': {
        'id': 'bat', 'title': 'KCET 9f17, CIN AT PHI (PPD.)', 'window': [1100, 5400],
        'clears': [{'t0': 1260, 't1': 3642, 'rect': [0, 0, 112, 15]}, {'t0': 2600, 't1': 4600, 'mask': 'bat'}],
        'parts': [
            {'id': 'bat', 'at': 4300, 'pivot': [101, 7.5], 'tip': [0, 7.5], 'polar': {'centre': [112, 7.5, 0]},
             'keys': [_key(2600, _swing(-70, 6), (9, -70, -5.6), 'hold', -10),
                      _key(3050, _swing(-70, 6), (9, -70, -5.6), 'hold', -10), _key(3373, d=(11, 0, 0)),
                      _key(3620, _swing(70, 6), (10, 80, 5.9), 'hold', 15), _key(4250, d=(11, 0, 0), mode='settle'),
                      _key(4600, d=(11, 0, 0), mode='hold')]},
            {'id': 'ball', 'at': 3348, 'round': True, 'pivot': [17.5, 7.5], 'tip': [17.5, 1],
             'keys': [_key(1260, d=(101, 0, 150), mode='linear'), _key(3290, (0, 0, -1400), mode='linear'),
                      _key(3373, (0, 0, -1440), mode='hold')],
             'launch': {'from': 'bat', 'at': 3373, 'point': [17.5, 7.5], 'gain': 1.6, 'until': 3623,
                        'spin': [0, 0, -700]}}]},
}

def from_vector(w):
    """ The rotation matrix of a rotation vector in degrees """
    x, y, z = w[0] * RAD, w[1] * RAD, w[2] * RAD
    t = math.sqrt(x * x + y * y + z * z)
    if t < 1e-12:
        return list(IDENTITY)
    kx, ky, kz, c, s = x / t, y / t, z / t, math.cos(t), math.sin(t)
    v = 1 - c
    return [c + kx * kx * v, kx * ky * v - kz * s, kx * kz * v + ky * s,
            ky * kx * v + kz * s, c + ky * ky * v, ky * kz * v - kx * s,
            kz * kx * v - ky * s, kz * ky * v + kx * s, c + kz * kz * v]

def times(a, b):
    return [a[3 * i] * b[j] + a[3 * i + 1] * b[3 + j] + a[3 * i + 2] * b[6 + j] for i in range(3) for j in range(3)]

def rotation_of(v):
    """ A pose's rotation: rolled about the part's own long axis (x as drawn) by v[6] degrees, then
    turned by the rotation vector v[0:3] """
    R, roll = from_vector(v), (v[6] or 0) * RAD
    if not roll:
        return R
    c, s = math.cos(roll), math.sin(roll)
    return times(R, [1, 0, 0, 0, c, -s, 0, s, c])

def track(keys):
    """ A part's pose at `t` from its key poses, [turn x, y, z, position x, y, z, roll, stretch]: each
    coordinate carried on at the speed it has and bent toward the next key at one constant
    acceleration, arriving on it at its time. A key's mode, 'arriving>leaving' or one for both: held
    (hold), passed (smooth, fast), left straight for the next at one speed (linear), swung about
    damped (settle) or bounced back off damped (bounce), or glided into, slowing, never past it
    (glide). """
    n, T = len(keys), [float(k['t']) for k in keys]
    P = [[float(c) for c in list(k['w']) + list(k['d']) + [k.get('roll') or 0, k.get('stretch') or 1]] for k in keys]
    modes = []
    for k in keys:
        m = (k.get('mode') or 'smooth').split('>')
        modes.append((m[0], m[1] if len(m) > 1 and m[1] else m[0]))
    still = [0.0] * len(P[0])
    v, runs = still, []
    for i in range(n - 1):
        h = T[i + 1] - T[i]
        if modes[i][1] in ('settle', 'bounce'):
            runs.append(('swing', v, modes[i][1] == 'bounce'))
            v = still
            continue
        v0 = still if modes[i][1] == 'hold' else v
        if modes[i + 1][0] == 'glide':
            runs.append(('glide', v0, 8 / h))
            v = still
            continue
        if modes[i][1] == 'linear':
            v0 = [(P[i + 1][c] - p) / h for c, p in enumerate(P[i])]
            runs.append(('curve', v0, still))
            v = still if modes[i + 1][0] == 'hold' else v0
            continue
        acc = [2 * (P[i + 1][c] - p - v0[c] * h) / (h * h) for c, p in enumerate(P[i])]
        runs.append(('curve', v0, acc))
        v = still if modes[i + 1][0] == 'hold' else [x + acc[c] * h for c, x in enumerate(v0)]

    def at(t):
        if t <= T[0]:
            return list(P[0])
        if t >= T[n - 1]:
            return list(P[n - 1])
        i = 0
        while T[i + 1] < t:
            i += 1
        kind, speed, more = runs[i]
        tau = t - T[i]
        if kind == 'glide':
            fall = math.exp(-more * tau)
            return [p + (P[i][c] - p + (speed[c] + more * (P[i][c] - p)) * tau) * fall for c, p in enumerate(P[i + 1])]
        if kind == 'swing':
            e, sn = math.exp(-DAMPING * SWING * tau), math.sin(WD * tau)
            sn = -abs(sn) if more else sn
            return [p + sn * speed[c] / WD * e for c, p in enumerate(P[i])]
        return [p + speed[c] * tau + more[c] * tau * tau / 2 for c, p in enumerate(P[i])]
    return at

def offset(part, v):
    """ Where a pose puts the part's pivot from where it is drawn: the position as it is, or, for a
    part moved in polar coordinates about a centre, its radius, azimuth (from -x toward the viewer)
    and elevation (up) in degrees """
    if not part.get('polar'):
        return v[3:6]
    c, pivot, r, az, el = part['polar']['centre'], part['pivot'], v[3], v[4] * RAD, v[5] * RAD
    return [c[0] - r * math.cos(el) * math.cos(az) - pivot[0], c[1] - r * math.sin(el) - pivot[1],
            c[2] + r * math.cos(el) * math.sin(az)]

def place(part, px, py, R):
    """ A point of the part's drawing placed by the rotation R and its pose: stretched by `k` from
    the pivot, turned, and moved """
    k, pivot, d = part['k'] or 1, part['pivot'], part['d']
    u, v = (px - pivot[0]) * k, (py - pivot[1]) * k
    return [R[0] * u + R[1] * v + pivot[0] + d[0], R[3] * u + R[4] * v + pivot[1] + d[1], R[6] * u + R[7] * v + d[2]]

def project(p, centre):
    """ A point as the viewer sees it on the sign's plane, from straight in front of `centre` """
    s = D / (D - p[2])
    return [centre[0] + (p[0] - centre[0]) * s, centre[1] + (p[1] - centre[1]) * s]

def lamp_at(u, pivot):
    """ The lamp of the drawing a point `u` from the pivot falls in, a point on an edge taken by the
    lamp nearer the pivot """
    return np.where(u >= 0, np.ceil(u + pivot) - 1, np.floor(u + pivot)).astype(np.int64)

def _lit(grid, x, y):
    inside = (x >= 0) & (x < WIDTH) & (y >= 0) & (y < ROWS)
    out = np.zeros(len(x), bool)
    out[inside] = grid[y[inside], x[inside]]
    return out

def _grid(cols):
    """ Lamps given as columns of bits, rows by columns """
    return (np.asarray(cols, dtype=np.int64)[None, :] >> ROW_BITS & 1).astype(bool)

def _columns(grid):
    return (grid.astype(np.int64) << ROW_BITS).sum(axis=0)

def light(part):
    """ `(lit, depths)`, rows by columns: the lamps a posed part lights, each where the part is at its
    centre, and how far in front of the sign's plane it is at each. A round part keeps the outline
    it is drawn with and turns its seams inside it. """
    R = IDENTITY if part['round'] else part['R']
    pivot, d = part['pivot'], part['d']
    cx, cy = pivot[0] + d[0], pivot[1] + d[1]
    lit, depths = np.zeros((ROWS, WIDTH), bool), np.zeros((ROWS, WIDTH))
    # where on the sign it can be: its lamps' corners, seen
    p = place(part, part['corners'][0], part['corners'][1], R)
    seen = ~(p[2] > D - 1)
    if not seen.any():
        return lit, depths
    q = project([p[0][seen], p[1][seen], p[2][seen]], (cx, cy))
    x0, x1, y0, y1 = min(WIDTH, q[0].min()), max(-1, q[0].max()), min(ROWS, q[1].min()), max(-1, q[1].max())
    if x1 < 0:
        return lit, depths
    ty, tx = (a.ravel() for a in np.mgrid[max(0, math.floor(y0)):min(ROWS - 1, math.floor(y1)) + 1,
                                          max(0, math.floor(x0)):min(WIDTH - 1, math.floor(x1)) + 1])
    nx, ny, nz = R[2], R[5], R[8]
    qx, qy, qz = pivot[0] + d[0], pivot[1] + d[1], d[2]
    along = nx * (qx - cx) + ny * (qy - cy) + nz * (qz - D)
    # the ray from the viewer through each lamp, to the part's plane
    dx, dy, dz = tx + 0.5 - cx, ty + 0.5 - cy, -D
    den = nx * dx + ny * dy + nz * dz
    ok = ~(np.abs(den) < 1e-9)
    tx, ty, dx, dy, k = tx[ok], ty[ok], dx[ok], dy[ok], along / den[ok]
    ok = k > 0
    tx, ty, dx, dy, k = tx[ok], ty[ok], dx[ok], dy[ok], k[ok]
    rx, ry, rz = cx + k * dx - qx, cy + k * dy - qy, D + k * dz - qz
    stretch = part['k'] or 1
    hit = _lit(part['shape'], lamp_at((R[0] * rx + R[3] * ry + R[6] * rz) / stretch, pivot[0]),
               lamp_at((R[1] * rx + R[4] * ry + R[7] * rz) / stretch, pivot[1]))
    if part['round']:
        turn = part['R']
        hit &= ~_lit(part['seams'], lamp_at((turn[0] * rx + turn[3] * ry + turn[6] * rz) / stretch, pivot[0]),
                     lamp_at((turn[1] * rx + turn[4] * ry + turn[7] * rz) / stretch, pivot[1]))
    lit[ty[hit], tx[hit]] = True
    depths[ty[hit], tx[hit]] = (D + k * dz)[hit]
    return lit, depths

def frame_at(m, t):
    """ The firmware's lamps at `t` ms """
    frames, lo, hi = m['frames'], 0, len(m['frames']) - 1
    while lo < hi:
        mid = (lo + hi + 1) >> 1
        if frames[mid][0] <= t:
            lo = mid
        else:
            hi = mid - 1
    return frames[lo][1]

def scene_at(m, t):
    """ `(lamps, parts)`: what is on the sign at `t` ms, less what the case clears, and each part
    posed over it """
    bg = frame_at(m, t).copy()
    for t0, t1, lamps in m['clears']:
        if t0 <= t < t1:
            bg &= ~lamps
    parts = []
    for p in m['parts']:
        if p['keys'][0]['t'] <= t <= p['keys'][-1]['t']:
            v = p['track'](t)
            parts.append(dict(p, R=rotation_of(v), d=offset(p, v), k=v[7]))
    return bg, parts

def lamps_of(sc):
    out = sc[0].copy()
    for part in sc[1]:
        out |= _columns(light(part)[0])
    return out

def faded(z, depth):
    """ How bright a part is `z` lamps in front of the sign's plane: full on it and in front of it,
    fading behind it - a quarter halfway to `depth`, dark past it """
    d = 1 - np.minimum(1, -z / depth)
    return np.where(z >= 0, 1.0, d * d)

def speck(x, y, n):
    """ A number from 0 to 1 a lamp keeps from frame to frame """
    h = (x * 73856093 ^ y * 19349663 ^ (n + 1) * 83492791) & 0xffffffff
    h = (h ^ h >> 13) * 1274126177 & 0xffffffff
    h = h ^ h >> 16
    h = np.where(h >= 1 << 31, h - (1 << 32), h)
    return np.sign(h) * (np.abs(h) % 1000) / 1000

def _spread(xs, ys, z, b, depth):
    """ Each lamp's light spread over its neighbours as far as it is behind the sign's plane, added
    up lamp by lamp as polar.js adds it """
    sigma = np.where(z < 0, 3 * np.minimum(1, -z / depth), 0)
    near = sigma < 0.25
    if near.all():
        own = np.zeros((ROWS, WIDTH))
        own[ys, xs] = b
        return own
    reach = np.ceil(2 * sigma).astype(np.int64)
    most = reach[~near].max()
    dy, dx = np.divmod(np.arange((2 * most + 1) ** 2), 2 * most + 1)
    dy, dx = dy - most, dx - most
    # each lamp's gaussian over its own reach, dy by dx, in a square as wide as the widest
    far, r = ~near, reach[~near][:, None]
    kern = np.exp(-(dx * dx + dy * dy)[None, :] / (2 * sigma[far] * sigma[far])[:, None])
    kern[(np.abs(dx)[None, :] > r) | (np.abs(dy)[None, :] > r)] = 0
    weights = np.zeros((len(xs), len(dx)))
    weights[far] = b[far][:, None] * kern / np.cumsum(kern, axis=1)[:, -1:]
    weights[near, len(dx) // 2] = b[near]
    x, y = xs[:, None] + dx, ys[:, None] + dy
    cells = np.where((x >= 0) & (x < WIDTH) & (y >= 0) & (y < ROWS), y * WIDTH + x, ROWS * WIDTH)
    return np.bincount(cells.ravel(), weights.ravel(), ROWS * WIDTH + 1)[:-1].reshape(ROWS, WIDTH)

def _shifted(a, by, axis):
    """ `a` moved `by` lamps back along `axis`, what comes in dark """
    out = np.zeros_like(a)
    n = a.shape[axis]
    if abs(by) < n:
        src = [slice(None)] * 2
        dst = [slice(None)] * 2
        src[axis], dst[axis] = slice(max(0, by), n + min(0, by)), slice(max(0, -by), n - max(0, by))
        out[tuple(dst)] = a[tuple(src)]
    return out

def shade(sc, depth, fade=POLAR_FADE):
    """ Each lamp's brightness, 0 to 1, rows by columns: the sign's own lamps full, each part's as
    faded `depth` lamps behind the plane, and as `fade` has it fade: 'focus', each lamp spread over
    its neighbours as far as it is behind the plane, the light landing on each lamp its brightness,
    or 'dither', lit by an ordered dither of that light; 'sparkle', lit at random as bright as it
    is; 'blur', the whole part blurred as faded as it is; 'dim', dimmed. """
    bg, parts = sc
    out = _grid(bg).astype(float)
    for n, part in enumerate(parts):
        lit, depths = light(part)
        xs, ys = np.nonzero(lit.T)
        z = depths[ys, xs]
        b = faded(z, depth)
        if fade in ('focus', 'dither'):
            own = _spread(xs, ys, z, b, depth)
            if fade == 'focus':
                out = np.maximum(out, np.minimum(1, own))
            else:
                out[own > DITHER] = 1
            continue
        own = np.zeros((ROWS, WIDTH))
        own[ys, xs] = (speck(xs, ys, n) < b) * 1.0 if fade == 'sparkle' else 1.0 if fade == 'blur' else b
        a = np.cumsum(b)[-1] / len(b) if len(b) else 1
        if a < 1 and fade == 'blur':
            # a gaussian as wide as the part is faded, three lamps across at most, along rows then columns
            sigma = (1 - a) * 3
            reach = math.ceil(2.5 * sigma)
            kern, total = [math.exp(-m * m / (2 * sigma * sigma or 1)) for m in range(-reach, reach + 1)], 0
            for w in kern:
                total += w
            for axis in (1, 0):
                was, own = own, np.zeros((ROWS, WIDTH))
                for m in range(-reach, reach + 1):
                    own += _shifted(was, m, axis) * (kern[m + reach] / total)
            own = np.minimum(1, own * (0.4 + 0.6 * a) * 1.6)
        out = np.maximum(out, own)
    return out

def trailed(m, t, depth, fade, trail):
    """ The brightness at `t` ms with the motion trail """
    out = shade(scene_at(m, t), depth, fade)
    back, k = POLAR_TRAIL_STEP_MS, 0.5
    while back <= trail:
        out = np.maximum(out, shade(scene_at(m, t - back), depth, fade) * k)
        back, k = back + POLAR_TRAIL_STEP_MS, k / 2
    return out

def prepare(case, shown, tick_ms):
    """ A case as `polar_case` gives it over the firmware's frames `shown`, `(tick, lamps, ...)` each:
    each part's track, and a part launched off another's point carried on from there at `gain`
    times its speed, spinning at `spin` degrees a second """
    parts = []
    for p in case['parts']:
        part = {'id': p['id'], 'src': list(p['lamps']), 'pivot': p['pivot'], 'polar': p.get('polar'),
                'round': bool(p.get('round')), 'launch': p.get('launch'), 'keys': [dict(k) for k in p['keys']]}
        shape = part['src']
        if part['round']:
            # the outline, each row filled from its first lamp to its last, and the seams inside it
            shape, seams = [0] * WIDTH, [0] * WIDTH
            for y in range(ROWS):
                row = [x for x in range(WIDTH) if part['src'][x] >> y & 1]
                for x in range(row[0], row[-1] + 1) if row else ():
                    shape[x] |= 1 << y
                    seams[x] |= (~part['src'][x] >> y & 1) << y
            part['seams'] = _grid(seams)
        part['shape'] = _grid(shape)
        xs, ys = np.nonzero(part['shape'].T)
        part['corners'] = (np.concatenate([xs + i for i in (0, 1) for _ in (0, 1)]).astype(float),
                           np.concatenate([ys + j for _ in (0, 1) for j in (0, 1)]).astype(float))
        part['track'] = track(part['keys'])
        parts.append(part)
    for part in parts:
        launch = part['launch']
        if not launch:
            continue
        source = next(q for q in parts if q['id'] == launch['from'])

        def where(t):
            v = source['track'](t)
            return place({'pivot': source['pivot'], 'd': offset(source, v), 'k': v[7]}, launch['point'][0],
                         launch['point'][1], rotation_of(v))

        a, b = where(launch['at'] - 1), where(launch['at'] + 1)
        last, dt, spin = part['keys'][-1], launch['until'] - launch['at'], launch.get('spin') or (0, 0, 0)
        speed = [(b[i] - a[i]) / 2 * launch['gain'] for i in range(3)]
        last['mode'] = (last.get('mode') or 'smooth').split('>')[0] + '>linear'
        part['keys'].append({'t': launch['until'], 'mode': 'linear',
                             'w': [w + spin[i] * dt / 1000 for i, w in enumerate(last['w'])],
                             'd': [d + speed[i] * dt for i, d in enumerate(last['d'])]})
        part['track'] = track(part['keys'])
    clears = [(cl['t0'], cl['t1'], np.array(cl['mask'] if 'mask' in cl else scene.rect_fill(*cl['rect'])))
              for cl in case['clears']]
    return {'window': case['window'], 'depth': case['depth'], 'tick_ms': tick_ms, 'clears': clears, 'parts': parts,
            'frames': [(frame[0] * tick_ms, np.array(frame[1], dtype=np.int64)) for frame in shown]}

def _round(x):
    """ JavaScript's Math.round """
    return math.floor(x) + (x - math.floor(x) >= 0.5)

def sample(m, frame_ticks, fade=POLAR_FADE, trail=POLAR_TRAIL_MS, depth=None):
    """ `(frames, end)`: the lamps every `frame_ticks` over the case's window, `[tick from its start,
    lamps, low, high]` each, the level of each lit lamp as two planes, bit 0 in `low` and bit 1 in
    `high`, and the ticks it lasts """
    t0, t1 = _round(m['window'][0] / m['tick_ms']), _round(m['window'][1] / m['tick_ms'])
    frames = []
    for t in range(t0, t1, frame_ticks):
        got = trailed(m, t * m['tick_ms'], depth or m['depth'], fade, trail)
        level = sum((got < least).astype(np.int64) for least in LEVELS)
        lit = level < len(LEVELS)
        planes = lit, lit & (level & 1 > 0), lit & (level & 2 > 0)
        frames.append([t - t0] + [_columns(plane).tolist() for plane in planes])
    return frames, t1 - t0

def _at(shown, ms):
    lamps = shown[0][1]
    for t, got in shown:
        if t > ms:
            break
        lamps = got
    return list(lamps)

def _only(lamps, rect):
    rows = scene.row_mask(rect[1], rect[3])
    return [c & rows if rect[0] <= x < rect[2] else 0 for x, c in enumerate(lamps)]

def _resolve(spec, shown, end):
    """ A case as polar.js takes it, each part drawn from the message's frames `shown`, `(tick, lamps)`
    each, as `_shown` gives them, `end` the tick it ends on """
    tick_ms, full = sign.SIGN_TICK_MS, [0, 0, WIDTH, ROWS]
    shown = [(t * tick_ms, lamps) for t, lamps, *_ in shown]
    parts = [dict({k: v for k, v in p.items() if k not in ('at', 'rect')},
                  lamps=_only(_at(shown, p['at']), p.get('rect', full))) for p in spec.get('parts', ())]
    clears = list(spec.get('clears', ()))
    pages = spec.get('pages')
    if pages:
        stops = [t for t, _ in shown[1:]] + [end * tick_ms]
        starts = [(t, lamps) for (t, lamps), stop in zip(shown, stops)
                  if stop - t >= pages['held'] and t < pages['before']]
        for n, (t, lamps) in enumerate(starts):
            keys = [dict(k, t=t + k['t']) for k in pages['in']]
            clears.append({'t0': t, 't1': keys[-1]['t'], 'rect': full})
            keys += [dict(k, t=starts[n + 1][0] + k['t']) for k in pages['out']] if n + 1 < len(starts) else []
            parts.append({'id': 'page %d' % (n + 1), 'lamps': list(lamps), 'pivot': pages['pivot'], 'tip': pages['tip'],
                          'keys': keys})
    drawn = {p['id']: p['lamps'] for p in parts}
    return {'id': spec['id'], 'title': spec['title'], 'window': spec['window'], 'keeps': spec.get('keeps', False),
            'depth': round(POLAR_DEPTH_PER_MS * (end * tick_ms)),
            'clears': [dict(cl, mask=drawn[cl['mask']]) if 'mask' in cl else cl for cl in clears], 'parts': parts}

def _keyed(payload, run):
    """ `(case, shown)`: the case keyed for `payload` and the message's frames, or None """
    spec = POLAR_CASES.get(hashlib.sha1(bytes(payload)).hexdigest())
    if spec is None:
        return None
    frames, end = run or sign.sign_frames(payload)
    shown = sign._shown(frames, end)
    return _resolve(spec, shown, end), shown

def polar_case(payload, run=None):
    """ The case keyed for `payload`, the bytes the decoder plays, as polar.js takes it - each part
    drawn from the message's own frames - or None; `run` its `(frames, end)` as `sign_frames` gives
    them where the model has played it already """
    got = _keyed(payload, run)
    return got and got[0]

def _polar(payload, run):
    got = _keyed(payload, run)
    if got is None:
        return None
    case, shown = got
    return (case,) + sample(prepare(case, shown, sign.SIGN_TICK_MS), sign.SIGN_FRAME_TICKS)

def polar_frames(payload, run=None):
    """ None, or `(frames, end, title)`: the keyed motion over its case's window, sampled every
    display frame as `sample` gives it, the ticks it lasts, and the case's title """
    got = _polar(payload, run)
    return got and (got[1], got[2], got[0]['title'])

def _union(spans):
    """ Spans `[start, stop]` joined where they meet or overlap, in order """
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out

def polar_scene(story, payload, run=None):
    """ `story`, a message's scene as scene_of gives it, with its keyed swing if it has one: the
    swing's frames over its window a span of the scene whose colour planes are levels of brightness,
    scene.svg's `shades` """
    got = _polar(payload, run)
    if story is None or got is None:
        return story
    case, frames, end = got
    start = round(case['window'][0] / sign.SIGN_TICK_MS)
    stop = min(story['end'], start + end)
    played = [list(frame) for frame in story['played']]
    out = [frame for frame in played if frame[0] < start]
    out += [[start + frame[0]] + list(frame[1:]) for frame in frames if start + frame[0] < stop]
    if stop < story['end'] and not any(frame[0] == stop for frame in played):
        out.append([stop] + list(scene.track_at(played, stop)[1:]))
    out += [frame for frame in played if frame[0] >= stop]
    return dict(story, played=out, spans=_union([list(span) for span in story['spans']] + [[start, stop]]),
                shades=[[start, stop]])

# The swing as exact geometry, for a player that draws it itself. Each part is the outlines of its
# lamps as drawn, corner by corner; at every display frame of its keys each corner is placed by the
# part's pose and seen in perspective, unrounded, as `light` sees the lamps it lights: a round
# part's outline kept flat and its seams turned inside it
SWING_FORMAT = 'silentradio-swing/1'
SWING_PLACES = 6

def _loops(cols):
    return [np.array(loop, dtype=float) for loop in scene.corners(scene.cols_edges([int(c) for c in cols]))]

def _seen(part, loops, R):
    """ Each of `loops`, corners of the part's drawing, as `(x, y, z)` arrays: where the part posed
    by R puts each corner, seen, and how far in front of the sign's plane it is """
    centre = (part['pivot'][0] + part['d'][0], part['pivot'][1] + part['d'][1])
    out = []
    for loop in loops:
        p = place(part, loop[:, 0], loop[:, 1], R)
        q = project(p, centre)
        out.append((np.asarray(q[0], dtype=float), np.asarray(q[1], dtype=float), np.asarray(p[2], dtype=float)))
    return out

def polar_vectors(payload, run=None):
    """ None, or the keyed swing of `payload` as exact geometry: `{id, title, window, depth, D,
    clears, ground, parts}`, the firmware's frames under it and what the case clears of them, and
    for each part its outlines and at every display frame of its keys `(tick, R, d, k, outline,
    seams)`, the tick from the message's start, the pose's rotation, offset and stretch, and each
    outline and seam as `_seen` gives it """
    got = _keyed(payload, run)
    if got is None:
        return None
    case, shown = got
    tick_ms = sign.SIGN_TICK_MS
    m = prepare(case, shown, tick_ms)
    t0, t1 = _round(case['window'][0] / tick_ms), _round(case['window'][1] / tick_ms)
    parts = []
    for part in m['parts']:
        outline = _loops(_columns(part['shape']))
        if not outline:
            continue
        seams = _loops(_columns(part['seams'])) if part['round'] else []
        first, last = part['keys'][0]['t'], part['keys'][-1]['t']
        frames = []
        for t in range(t0, t1, sign.SIGN_FRAME_TICKS):
            if not first <= t * tick_ms <= last:
                continue
            v = part['track'](t * tick_ms)
            R = rotation_of(v)
            posed = dict(part, d=offset(part, v), k=v[7])
            frames.append((t, [float(r) for r in R], [float(c) for c in posed['d']], float(posed['k'] or 1),
                           _seen(posed, outline, IDENTITY if part['round'] else R), _seen(posed, seams, R)))
        if frames:
            parts.append(dict(part, outline=outline, seam_loops=seams, frames=frames))
    ground = [(t, lamps) for t, lamps, *_ in shown if t < t1]
    ground = [g for n, g in enumerate(ground) if n + 1 == len(ground) or ground[n + 1][0] > t0]
    return {'id': case['id'], 'title': case['title'], 'window': (t0, t1), 'depth': m['depth'], 'D': D,
            'clears': case['clears'], 'ground': ground, 'parts': parts}

def _place(x):
    out = ('%.*f' % (SWING_PLACES, x)).rstrip('0').rstrip('.')
    return '0' if out in ('', '-0') else out

def _path(loops):
    return ''.join('M%s %sL%sZ' % (_place(x[0]), _place(y[0]), ' '.join('%s %s' % (_place(a), _place(b))
                                                                       for a, b in zip(x[1:], y[1:])))
                   for x, y, _ in loops) or 'M0 0'

def swing_layer(vectors, tick_ms, at, prefix):
    """ The swing `vectors` as a layer of an SVG whose loop starts at `prefix`L, the message `at`
    ticks into it, hidden until a player shows it: each part a path of its outlines, a round part's
    seams a path cut from it, animated frame to frame through where they are seen and faded as far
    as the part's pivot is behind the sign's plane; and in its metadata the poses, outlines and
    frames under it to draw it from exactly """
    begin = prefix + 'L.begin'
    stamp = lambda t: math.floor((at + t) * tick_ms + 0.5)
    paths, meta = [], []

    def animated(cls, part, loops, a, b, times, extra=''):
        values = [_path(loops(f)) for f in part['frames']]
        out = '<path class="%s" data-part="%s" visibility="hidden" d="%s"><set attributeName="visibility" ' \
              'to="visible" begin="%s+%dms" end="%s+%dms"/>' % (cls, html.escape(part['id']), values[0], begin, a,
                                                               begin, max(b, a + 1))
        if times:
            out += '<animate attributeName="d" calcMode="linear" values="%s" keyTimes="%s" begin="%s+%dms" ' \
                   'dur="%dms" fill="freeze"/>' % (';'.join(values), times, begin, a, b - a) + extra
        return out + '</path>'

    for part in vectors['parts']:
        frames = part['frames']
        a, b = stamp(frames[0][0]), stamp(frames[-1][0])
        times = ';'.join(scene.fraction(stamp(f[0]) - a, b - a) for f in frames) if b > a else ''
        fade = ';'.join(_place(float(faded(np.array(f[2][2]), vectors['depth']))) for f in frames)
        extra = '<animate attributeName="opacity" calcMode="linear" values="%s" keyTimes="%s" begin="%s+%dms" ' \
                'dur="%dms" fill="freeze"/>' % (fade, times, begin, a, b - a) if times else ''
        paths.append(animated('swing-part', part, lambda f: f[4], a, b, times, extra))
        if part['seam_loops']:
            paths.append(animated('swing-seams', part, lambda f: f[5], a, b, times, extra))
        meta.append({'id': part['id'], 'pivot': part['pivot'], 'polar': part['polar'], 'round': part['round'],
                     'launch': part['launch'], 'keys': part['keys'], 'lamps': part['src'],
                     'outline': [loop.astype(int).tolist() for loop in part['outline']],
                     'seams': [loop.astype(int).tolist() for loop in part['seam_loops']],
                     'frames': [{'tick': t, 'R': R, 'd': d, 'k': k} for t, R, d, k, _, _ in frames]})
    data = json.dumps({'format': SWING_FORMAT, 'id': vectors['id'], 'title': vectors['title'], 'tick_ms': tick_ms,
                       'at': at, 'window': list(vectors['window']), 'D': vectors['D'], 'depth': vectors['depth'],
                       'clears': vectors['clears'], 'ground': [[t, list(lamps)] for t, lamps in vectors['ground']],
                       'parts': meta}, separators=(',', ':')).replace('<', '\\u003c').replace('&', '\\u0026')
    return '<g class="swing" id="%sW%d" display="none" fill-rule="nonzero"><metadata>%s</metadata>%s</g>' % (
        prefix, at, data, ''.join(paths))
