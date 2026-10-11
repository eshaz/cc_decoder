// The keyed swings: parts of a message's own frames turned and moved in 3D over the firmware's
// frames along key poses, and the sign's lamps they light every display frame. polar.py holds
// the cases and does the same, function for function.
//
// Public domain / Unlicense, as with the rest of ccDecoder.

var SilentRadioPolar = (function () {
  'use strict';

  // the viewer is D lamps in front of each part's own pivot
  var WIDTH = 112, ROWS = 15, D = 400, RAD = Math.PI / 180;
  var IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1];
  // a part settling on a key swings about it 7 times a second, damped
  var SWING = 2 * Math.PI * 7 / 1000, DAMPING = 0.35, WD = SWING * Math.sqrt(1 - DAMPING * DAMPING);
  // the least brightness of each level a lamp is lit at: full, three quarters, half, a quarter
  var LEVELS = [0.875, 0.625, 0.39, 0.08];
  var BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5];
  // how a part fades behind the sign's plane, and the motion trail: each lamp as bright as the
  // brightest it has been over the last TRAIL ms, that light halving every TRAIL_STEP ms
  var FADE = 'focus', TRAIL = 80, TRAIL_STEP = 20;

  // the rotation matrix of a rotation vector in degrees
  function fromVector(w) {
    var x = w[0] * RAD, y = w[1] * RAD, z = w[2] * RAD, t = Math.sqrt(x * x + y * y + z * z);
    if (t < 1e-12) return IDENTITY.slice();
    var kx = x / t, ky = y / t, kz = z / t, c = Math.cos(t), s = Math.sin(t), v = 1 - c;
    return [c + kx * kx * v, kx * ky * v - kz * s, kx * kz * v + ky * s,
            ky * kx * v + kz * s, c + ky * ky * v, ky * kz * v - kx * s,
            kz * kx * v - ky * s, kz * ky * v + kx * s, c + kz * kz * v];
  }

  function times(a, b) {
    var out = [], i, j;
    for (i = 0; i < 3; i++) {
      for (j = 0; j < 3; j++) out.push(a[3 * i] * b[j] + a[3 * i + 1] * b[3 + j] + a[3 * i + 2] * b[6 + j]);
    }
    return out;
  }

  // a pose's rotation: rolled about the part's own long axis (x as drawn) by v[6] degrees, then
  // turned by the rotation vector v[0..2]
  function rotationOf(v) {
    var R = fromVector(v), roll = (v[6] || 0) * RAD;
    if (!roll) return R;
    var c = Math.cos(roll), s = Math.sin(roll);
    return times(R, [1, 0, 0, 0, c, -s, 0, s, c]);
  }

  // A part's pose at `t` from its key poses, [turn x, y, z, position x, y, z, roll, stretch]: each
  // coordinate carried on at the speed it has and bent toward the next key at one constant
  // acceleration, arriving on it at its time. A key's mode, 'arriving>leaving' or one for both:
  // held (hold), passed (smooth, fast), left straight for the next at one speed (linear), swung
  // about damped (settle) or bounced back off damped (bounce), or glided into, slowing, never past
  // it (glide).
  function track(keys) {
    var n = keys.length, T = keys.map(function (k) { return k.t; });
    var P = keys.map(function (k) { return k.w.concat(k.d, [k.roll || 0, k.stretch || 1]); });
    var modes = keys.map(function (k) {
      var m = (k.mode || 'smooth').split('>');
      return [m[0], m[1] || m[0]];
    });
    var still = P[0].map(function () { return 0; }), v = still, runs = [], i;
    for (i = 0; i + 1 < n; i++) {
      var h = T[i + 1] - T[i];
      if (modes[i][1] === 'settle' || modes[i][1] === 'bounce') {
        runs.push({swing: v, bounce: modes[i][1] === 'bounce'});
        v = still;
        continue;
      }
      var v0 = modes[i][1] === 'hold' ? still : v;
      if (modes[i + 1][0] === 'glide') {
        runs.push({glide: v0, from: P[i], rate: 8 / h});
        v = still;
        continue;
      }
      if (modes[i][1] === 'linear') {
        v0 = P[i].map(function (p, c) { return (P[i + 1][c] - p) / h; });
        runs.push({v0: v0, acc: still});
        v = modes[i + 1][0] === 'hold' ? still : v0;
        continue;
      }
      var acc = P[i].map(function (p, c) { return 2 * (P[i + 1][c] - p - v0[c] * h) / (h * h); });
      runs.push({v0: v0, acc: acc});
      v = modes[i + 1][0] === 'hold' ? still : v0.map(function (x, c) { return x + acc[c] * h; });
    }
    return function (t) {
      if (t <= T[0]) return P[0].slice();
      if (t >= T[n - 1]) return P[n - 1].slice();
      var i = 0;
      while (T[i + 1] < t) i++;
      var r = runs[i], tau = t - T[i];
      if (r.glide) {
        var fall = Math.exp(-r.rate * tau);
        return P[i + 1].map(function (p, c) {
          var e0 = r.from[c] - p;
          return p + (e0 + (r.glide[c] + r.rate * e0) * tau) * fall;
        });
      }
      if (r.swing) {
        var e = Math.exp(-DAMPING * SWING * tau), sn = Math.sin(WD * tau);
        return P[i].map(function (p, c) { return p + (r.bounce ? -Math.abs(sn) : sn) * r.swing[c] / WD * e; });
      }
      return P[i].map(function (p, c) { return p + r.v0[c] * tau + r.acc[c] * tau * tau / 2; });
    };
  }

  // where a pose puts the part's pivot from where it is drawn: the position as it is, or, for a
  // part moved in polar coordinates about a centre, its radius, azimuth (from -x toward the
  // viewer) and elevation (up) in degrees
  function offset(part, v) {
    if (!part.polar) return v.slice(3, 6);
    var c = part.polar.centre, r = v[3], az = v[4] * RAD, el = v[5] * RAD;
    return [c[0] - r * Math.cos(el) * Math.cos(az) - part.pivot[0], c[1] - r * Math.sin(el) - part.pivot[1],
            c[2] + r * Math.cos(el) * Math.sin(az)];
  }

  // a point of the part's drawing placed by the rotation R and its pose: stretched by `k` from
  // the pivot, turned, and moved
  function place(part, px, py, R) {
    var k = part.k || 1, u = (px - part.pivot[0]) * k, v = (py - part.pivot[1]) * k;
    return [R[0] * u + R[1] * v + part.pivot[0] + part.d[0], R[3] * u + R[4] * v + part.pivot[1] + part.d[1],
            R[6] * u + R[7] * v + part.d[2]];
  }

  // a point as the viewer sees it on the sign's plane, from straight in front of `centre`
  function project(p, centre) {
    var s = D / (D - p[2]);
    return [centre[0] + (p[0] - centre[0]) * s, centre[1] + (p[1] - centre[1]) * s];
  }

  function inside(x, y) {
    return x >= 0 && x < WIDTH && y >= 0 && y < ROWS;
  }

  function lit(cols, x, y) {
    return inside(x, y) && cols[x] >> y & 1;
  }

  // the lamp of the drawing a point `u` from the pivot falls in, a point on an edge taken by the
  // lamp nearer the pivot
  function lampAt(u, pivot) {
    return u >= 0 ? Math.ceil(u + pivot) - 1 : Math.floor(u + pivot);
  }

  // The lamps a posed part lights, ORed into `out`, each lit where the part is at its centre;
  // `depths`, if given, takes how far in front of the sign's plane the part is at each. A round
  // part keeps the outline it is drawn with and turns its seams inside it.
  function light(part, out, depths) {
    var shape = part.round ? part.fill : part.src, R = part.round ? IDENTITY : part.R;
    var centre = [part.pivot[0] + part.d[0], part.pivot[1] + part.d[1]], cx = centre[0], cy = centre[1];
    var x0 = WIDTH, x1 = -1, y0 = ROWS, y1 = -1, x, y, i, j;
    // where on the sign it can be: its lamps' corners, seen
    for (x = 0; x < WIDTH; x++) {
      if (!shape[x]) continue;
      for (y = 0; y < ROWS; y++) {
        if (!(shape[x] >> y & 1)) continue;
        for (i = 0; i < 2; i++) {
          for (j = 0; j < 2; j++) {
            var p = place(part, x + i, y + j, R);
            if (p[2] > D - 1) continue;
            var q = project(p, centre);
            if (q[0] < x0) x0 = q[0];
            if (q[0] > x1) x1 = q[0];
            if (q[1] < y0) y0 = q[1];
            if (q[1] > y1) y1 = q[1];
          }
        }
      }
    }
    if (x1 < 0) return out;
    var stretch = part.k || 1, turn = part.R, nx = R[2], ny = R[5], nz = R[8];
    var qx = part.pivot[0] + part.d[0], qy = part.pivot[1] + part.d[1], qz = part.d[2];
    var along = nx * (qx - cx) + ny * (qy - cy) + nz * (qz - D);
    for (var ty = Math.max(0, Math.floor(y0)); ty <= Math.min(ROWS - 1, Math.floor(y1)); ty++) {
      for (var tx = Math.max(0, Math.floor(x0)); tx <= Math.min(WIDTH - 1, Math.floor(x1)); tx++) {
        // the ray from the viewer through the lamp, to the part's plane
        var dx = tx + 0.5 - cx, dy = ty + 0.5 - cy, dz = -D, den = nx * dx + ny * dy + nz * dz;
        if (Math.abs(den) < 1e-9) continue;
        var k = along / den;
        if (k <= 0) continue;
        var rx = cx + k * dx - qx, ry = cy + k * dy - qy, rz = D + k * dz - qz;
        var lx = lampAt((R[0] * rx + R[3] * ry + R[6] * rz) / stretch, part.pivot[0]);
        var ly = lampAt((R[1] * rx + R[4] * ry + R[7] * rz) / stretch, part.pivot[1]);
        if (!lit(shape, lx, ly)) continue;
        if (part.round) {
          var sx = lampAt((turn[0] * rx + turn[3] * ry + turn[6] * rz) / stretch, part.pivot[0]);
          var sy = lampAt((turn[1] * rx + turn[4] * ry + turn[7] * rz) / stretch, part.pivot[1]);
          if (lit(part.seams, sx, sy)) continue;
        }
        out[tx] |= 1 << ty;
        if (depths) depths[ty * WIDTH + tx] = D + k * dz;
      }
    }
    return out;
  }

  // the firmware's lamps at `t` ms
  function frameAt(m, t) {
    var lo = 0, hi = m.frames.length - 1;
    while (lo < hi) {
      var mid = (lo + hi + 1) >> 1;
      if (m.frames[mid].t <= t) lo = mid;
      else hi = mid - 1;
    }
    return m.frames[lo].lamps;
  }

  // what is on the sign at `t` ms, less what the case clears, and each part posed over it
  function sceneAt(m, t) {
    var bg = Int32Array.from(frameAt(m, t)), parts = [];
    m.clears.forEach(function (cl) {
      if (t < cl.t0 || t >= cl.t1) return;
      for (var x = 0; x < WIDTH; x++) bg[x] &= ~cl.lamps[x];
    });
    m.parts.forEach(function (p) {
      if (t < p.keys[0].t || t > p.keys[p.keys.length - 1].t) return;
      var v = p.track(t);
      parts.push({src: p.src, round: p.round, fill: p.fill, seams: p.seams, pivot: p.pivot, R: rotationOf(v),
                  d: offset(p, v), k: v[7]});
    });
    return {bg: bg, parts: parts};
  }

  function lampsOf(sc) {
    var out = Int32Array.from(sc.bg);
    sc.parts.forEach(function (p) { light(p, out); });
    return out;
  }

  // how bright a part is `z` lamps in front of the sign's plane: full on it and in front of it,
  // fading behind it - a quarter halfway to `depth`, dark past it
  function faded(z, depth) {
    if (z >= 0) return 1;
    var d = 1 - Math.min(1, -z / depth);
    return d * d;
  }

  // a number from 0 to 1 a lamp keeps from frame to frame
  function speck(x, y, n) {
    var h = (x * 73856093 ^ y * 19349663 ^ (n + 1) * 83492791) >>> 0;
    h = Math.imul(h ^ h >>> 13, 1274126177) >>> 0;
    return (h ^ h >>> 16) % 1000 / 1000;
  }

  // Each lamp's brightness, 0 to 1, row by row: the sign's own lamps full, each part's as faded
  // `depth` lamps behind the plane, and as `fade` has it fade: 'focus', each lamp spread over its
  // neighbours as far as it is behind the plane, the light landing on each lamp its brightness,
  // or 'dither', lit by an ordered dither of that light; 'sparkle', lit at random as bright as it
  // is; 'blur', the whole part blurred as faded as it is; 'dim', dimmed.
  function shade(sc, depth, fade) {
    var out = new Float64Array(WIDTH * ROWS), spread = fade === 'focus' || fade === 'dither', x, y;
    for (x = 0; x < WIDTH; x++) for (y = 0; y < ROWS; y++) if (sc.bg[x] >> y & 1) out[y * WIDTH + x] = 1;
    sc.parts.forEach(function (p, n) {
      var cols = new Int32Array(WIDTH), depths = new Float64Array(WIDTH * ROWS), own = new Float64Array(WIDTH * ROWS);
      var count = 0, sum = 0, a = 1, i, z, b, sigma, reach, kern, total, m, dx, dy, pass, from, v;
      light(p, cols, depths);
      for (x = 0; x < WIDTH; x++) {
        if (!cols[x]) continue;
        for (y = 0; y < ROWS; y++) {
          if (!(cols[x] >> y & 1)) continue;
          z = depths[y * WIDTH + x];
          b = faded(z, depth);
          if (!spread) {
            sum += b;
            count++;
            own[y * WIDTH + x] = fade === 'sparkle' ? (speck(x, y, n) < b ? 1 : 0) : fade === 'blur' ? 1 : b;
            continue;
          }
          sigma = z < 0 ? 3 * Math.min(1, -z / depth) : 0;
          if (sigma < 0.25) {
            own[y * WIDTH + x] += b;
            continue;
          }
          reach = Math.ceil(2 * sigma);
          kern = [];
          total = 0;
          for (dy = -reach; dy <= reach; dy++) {
            for (dx = -reach; dx <= reach; dx++) {
              kern.push(Math.exp(-(dx * dx + dy * dy) / (2 * sigma * sigma)));
              total += kern[kern.length - 1];
            }
          }
          for (m = 0, dy = -reach; dy <= reach; dy++) {
            for (dx = -reach; dx <= reach; dx++, m++) {
              if (inside(x + dx, y + dy)) own[(y + dy) * WIDTH + x + dx] += b * kern[m] / total;
            }
          }
        }
      }
      if (spread) {
        for (y = 0; y < ROWS; y++) {
          for (x = 0; x < WIDTH; x++) {
            i = y * WIDTH + x;
            if (fade === 'focus') out[i] = Math.max(out[i], Math.min(1, own[i]));
            else if (own[i] > (BAYER[(y & 3) * 4 + (x & 3)] + 0.5) / 16) out[i] = 1;
          }
        }
        return;
      }
      if (count) a = sum / count;
      if (a < 1 && fade === 'blur') {
        // a gaussian as wide as the part is faded, three lamps across at most, along rows then columns
        sigma = (1 - a) * 3;
        reach = Math.ceil(2.5 * sigma);
        kern = [];
        total = 0;
        for (m = -reach; m <= reach; m++) {
          kern.push(Math.exp(-m * m / (2 * sigma * sigma || 1)));
          total += kern[kern.length - 1];
        }
        kern = kern.map(function (k) { return k / total; });
        for (pass = 0; pass < 2; pass++) {
          from = own;
          own = new Float64Array(WIDTH * ROWS);
          for (y = 0; y < ROWS; y++) {
            for (x = 0; x < WIDTH; x++) {
              for (v = 0, m = -reach; m <= reach; m++) {
                dx = pass ? 0 : m;
                dy = pass ? m : 0;
                if (inside(x + dx, y + dy)) v += from[(y + dy) * WIDTH + x + dx] * kern[m + reach];
              }
              own[y * WIDTH + x] = v;
            }
          }
        }
        for (i = 0; i < WIDTH * ROWS; i++) own[i] = Math.min(1, own[i] * (0.4 + 0.6 * a) * 1.6);
      }
      for (i = 0; i < WIDTH * ROWS; i++) out[i] = Math.max(out[i], own[i]);
    });
    return out;
  }

  // the brightness at `t` ms with the motion trail
  function trailed(m, t, depth, fade, trail) {
    var out = shade(sceneAt(m, t), depth, fade);
    for (var back = TRAIL_STEP, k = 0.5; back <= trail; back += TRAIL_STEP, k /= 2) {
      var then = shade(sceneAt(m, t - back), depth, fade);
      for (var i = 0; i < WIDTH * ROWS; i++) out[i] = Math.max(out[i], then[i] * k);
    }
    return out;
  }

  // A case as polar.py gives it - its window and fade depth in ms and lamps, its clears, and its
  // parts, each its drawing as columns, pivot, key poses and launch - over the firmware's frames
  // `shown`, [tick, lamps, ...] each: each part's track, and a part launched off another's point
  // carried on from there at `gain` times its speed, spinning at `spin` degrees a second
  function prepare(c, shown, tickMs) {
    var parts = c.parts.map(function (p) {
      var q = {id: p.id, src: p.lamps, pivot: p.pivot, polar: p.polar || null, round: !!p.round,
               launch: p.launch || null, keys: p.keys.map(function (k) {
                 return {t: k.t, w: k.w, d: k.d, mode: k.mode, roll: k.roll, stretch: k.stretch};
               })};
      if (q.round) {
        // the outline, each row filled from its first lamp to its last, and the seams inside it
        q.fill = new Int32Array(WIDTH);
        q.seams = new Int32Array(WIDTH);
        for (var y = 0; y < ROWS; y++) {
          var row = [], x;
          for (x = 0; x < WIDTH; x++) if (q.src[x] >> y & 1) row.push(x);
          if (!row.length) continue;
          for (x = row[0]; x <= row[row.length - 1]; x++) {
            q.fill[x] |= 1 << y;
            if (!(q.src[x] >> y & 1)) q.seams[x] |= 1 << y;
          }
        }
      }
      q.track = track(q.keys);
      return q;
    });
    parts.forEach(function (p) {
      if (!p.launch) return;
      var L = p.launch, from = parts.filter(function (q) { return q.id === L.from; })[0];
      var where = function (t) {
        var v = from.track(t);
        return place({pivot: from.pivot, d: offset(from, v), k: v[7]}, L.point[0], L.point[1], rotationOf(v));
      };
      var a = where(L.at - 1), b = where(L.at + 1), last = p.keys[p.keys.length - 1], dt = L.until - L.at;
      var speed = [0, 1, 2].map(function (i) { return (b[i] - a[i]) / 2 * L.gain; });
      last.mode = (last.mode || 'smooth').split('>')[0] + '>linear';
      p.keys.push({t: L.until, mode: 'linear',
                   w: last.w.map(function (w, i) { return w + (L.spin || [0, 0, 0])[i] * dt / 1000; }),
                   d: last.d.map(function (d, i) { return d + speed[i] * dt; })});
      p.track = track(p.keys);
    });
    var clears = c.clears.map(function (cl) {
      var lamps = [], rows = cl.rect ? ((1 << cl.rect[3]) - 1) & ~((1 << cl.rect[1]) - 1) : 0;
      for (var x = 0; x < WIDTH; x++) lamps.push(cl.mask ? cl.mask[x] : x >= cl.rect[0] && x < cl.rect[2] ? rows : 0);
      return {t0: cl.t0, t1: cl.t1, lamps: lamps};
    });
    return {window: c.window, depth: c.depth, tickMs: tickMs, clears: clears, parts: parts,
            frames: shown.map(function (f) { return {t: f[0] * tickMs, lamps: f[1]}; })};
  }

  // The lamps every `frameTicks` over the case's window, {end, frames: [[tick from its start,
  // lamps, low, high]]}, the level of each lit lamp as two planes: bit 0 in `low`, bit 1 in `high`
  function sample(m, frameTicks, fade, trail, depth) {
    var t0 = Math.round(m.window[0] / m.tickMs), t1 = Math.round(m.window[1] / m.tickMs), out = [], t, x, y;
    var got, lamps, low, high, l, a, b, level;
    fade = fade || FADE;
    trail = trail === undefined ? TRAIL : trail;
    for (t = t0; t < t1; t += frameTicks) {
      got = trailed(m, t * m.tickMs, depth || m.depth, fade, trail);
      lamps = [];
      low = [];
      high = [];
      for (x = 0; x < WIDTH; x++) {
        for (l = a = b = y = 0; y < ROWS; y++) {
          for (level = 0; level < LEVELS.length && !(got[y * WIDTH + x] >= LEVELS[level]); level++);
          if (level === LEVELS.length) continue;
          l |= 1 << y;
          if (level & 1) a |= 1 << y;
          if (level & 2) b |= 1 << y;
        }
        lamps.push(l);
        low.push(a);
        high.push(b);
      }
      out.push([t - t0, lamps, low, high]);
    }
    return {end: t1 - t0, frames: out};
  }

  // A message's scene with its keyed swing `c`: the swing's frames over its window a span of the
  // scene whose colour planes are levels of brightness, the SVG's `shades` (polar_scene)
  function merge(scene, c, tickMs, frameTicks) {
    var got = sample(prepare(c, scene.played, tickMs), frameTicks), played = scene.played, out = [], spans = [];
    var start = Math.round(c.window[0] / tickMs), stop = Math.min(scene.end, start + got.end), last = null;
    played.forEach(function (f) {
      if (f[0] < start) out.push(f);
      if (f[0] <= stop) last = f;
    });
    got.frames.forEach(function (f) { if (start + f[0] < stop) out.push([start + f[0]].concat(f.slice(1))); });
    if (stop < scene.end && last && last[0] !== stop) out.push([stop].concat(last.slice(1)));
    played.forEach(function (f) { if (f[0] >= stop) out.push(f); });
    scene.spans.concat([[start, stop]]).map(function (s) { return [s[0], s[1]]; })
      .sort(function (a, b) { return a[0] - b[0] || a[1] - b[1]; }).forEach(function (s) {
        if (spans.length && s[0] <= spans[spans.length - 1][1]) spans[spans.length - 1][1] = Math.max(spans[spans.length - 1][1], s[1]);
        else spans.push(s);
      });
    return {end: scene.end, layers: scene.layers, spans: spans, played: out, shades: [[start, stop]]};
  }

  return {WIDTH: WIDTH, ROWS: ROWS, D: D, IDENTITY: IDENTITY, LEVELS: LEVELS, FADE: FADE, TRAIL: TRAIL, merge: merge,
          fromVector: fromVector, rotationOf: rotationOf, track: track, offset: offset, place: place, project: project,
          lampAt: lampAt, light: light, frameAt: frameAt, sceneAt: sceneAt, lampsOf: lampsOf, faded: faded,
          speck: speck, shade: shade, trailed: trailed, prepare: prepare, sample: sample};
})();

if (typeof module !== 'undefined') module.exports = SilentRadioPolar;
