// The players' 3D view: each line's control bits read as signals over the whole message, modulated
// controls for how the line's coordinates change over time - like a gyroscope turned and carried
// through space - but for bits 0 and 1, the colour, which move nothing. A bit driving a position or a turn is its acceleration: while 1 its
// coordinate speeds up its way, while 0 it is pushed back the other way, the two adding up over
// time, so the signal's bias speeds a line up or slows it down as it started faster or slower; a
// bit can instead drive the line's time, which then runs only while the bit is 1. The firmware
// moves nothing this way; the view draws the sign's lamps where the reading takes each line - text
// and graphics alike - in perspective, faded out the farther back they go behind the sign's plane,
// with the path each line takes and all eight of its control bits over the whole message.
//
// Public domain / Unlicense, as with the rest of ccDecoder.

var SilentRadioMotion = (function () {
  'use strict';

  var WIDTH = 112, ROWS = 15, RAD = Math.PI / 180, STEP = 5;

  // what a bit can drive: a coordinate (0-2 position, 3-5 turn) and its way, or the line's time; the
  // colour bits, and an indicator, drive nothing
  var TARGETS = {'+x': [0, 1], '-x': [0, -1], '+y': [1, 1], '-y': [1, -1], '+z': [2, 1], '-z': [2, -1],
                 'turn +x': [3, 1], 'turn -x': [3, -1], 'turn +y': [4, 1], 'turn -y': [4, -1],
                 'turn +z': [5, 1], 'turn -z': [5, -1], 'time': [6, 1], 'red': null, 'green': null,
                 'indicator': null, 'nothing': null};
  var BITS = 8;

  // bits 0 to 7 of a line's control byte, each driving what `dirs` names for it
  function defaults() {
    return {dirs: ['red', 'green', '+z', 'indicator', 'turn +x', 'turn +y', 'nothing', 'nothing'], push: true, accel: 60,
            top: 30, start: 0,
            turnAccel: 720, turnTop: 360, turnStart: 0, smooth: 0, depth: 120};
  }

  // each line's control bytes (its `font` ops) and rows as [tick, value] runs, from a run's op-log
  function signals(ops) {
    var lines = [{fonts: [], rows: []}, {fonts: [], rows: []}];
    (ops || []).forEach(function (op) {
      if (op.op === 'font') lines[op.k].fonts.push([op.t, op.byte]);
      if (op.op === 'font' || op.op === 'rows') lines[op.k].rows.push([op.t, op.rows]);
    });
    return lines;
  }

  // the three passes of a running mean over `radius` samples each side, which come near a
  // gaussian, the ends held
  function smoothed(values, radius) {
    var n = values.length, out = values.slice(), pass, i, sum, lo, hi, next;
    if (radius < 1 || n < 2) return out;
    for (pass = 0; pass < 3; pass++) {
      next = new Array(n);
      sum = 0;
      for (i = -radius; i <= radius; i++) sum += out[Math.min(n - 1, Math.max(0, i))];
      for (i = 0; i < n; i++) {
        next[i] = sum / (2 * radius + 1);
        lo = Math.max(0, i - radius);
        hi = Math.min(n - 1, i + radius + 1);
        sum += out[hi] - out[lo];
      }
      out = next;
    }
    return out;
  }

  // a line's six coordinates and its own time, every STEP ms of the message: position in lamps,
  // turns in degrees, time in ms; smoothed along every axis over the whole message by
  // `settings.smooth` seconds
  function vector(line, endMs, tickMs, settings) {
    var fonts = line.fonts, e = 0, byte = 0, at = [0, 0, 0, 0, 0, 0], speed = [0, 0, 0, 0, 0, 0], time = 0, t, c;
    var timed = settings.dirs.indexOf('time') >= 0, axes = [[], [], [], [], [], [], []];
    // each coordinate a bit drives starts at the starting speed, the bit's way
    settings.dirs.forEach(function (d) {
      var target = TARGETS[d];
      if (target && target[0] < 6) speed[target[0]] += target[1] * (target[0] < 3 ? settings.start : settings.turnStart);
    });
    for (t = 0; t <= endMs; t++) {
      while (e < fonts.length && fonts[e][0] * tickMs <= t) byte = fonts[e++][1];
      if (t % STEP === 0) {
        for (c = 0; c < 6; c++) axes[c].push(at[c]);
        axes[6].push(time);
      }
      var drive = [0, 0, 0, 0, 0, 0], driven = [false, false, false, false, false, false], run = timed ? 0 : 1;
      settings.dirs.forEach(function (d, bit) {
        var target = TARGETS[d], on = byte >> bit & 1;
        if (!target) return;
        if (target[0] === 6) {
          if (on) run = 1;
          return;
        }
        driven[target[0]] = true;
        drive[target[0]] += on ? target[1] : settings.push ? -target[1] : 0;
      });
      // the line moves in its own time, which runs while a bit driving it is 1
      var dt = run / 1000;
      for (c = 0; c < 6; c++) {
        var accel = c < 3 ? settings.accel : settings.turnAccel, top = c < 3 ? settings.top : settings.turnTop;
        if (drive[c]) speed[c] += drive[c] * accel * dt;
        else if (driven[c]) speed[c] -= Math.sign(speed[c]) * Math.min(Math.abs(speed[c]), accel * dt);
        speed[c] = Math.min(top, Math.max(-top, speed[c]));
        at[c] += speed[c] * dt;
      }
      time += run;
    }
    if (settings.smooth > 0) {
      var radius = Math.round(settings.smooth * 1000 / STEP / 2);
      axes = axes.map(function (values) { return smoothed(values, radius); });
    }
    return {axes: axes, rows: line.rows, fonts: line.fonts};
  }

  // every line's vector, from a run's op-log
  function vectors(ops, endTicks, tickMs, settings) {
    return signals(ops).map(function (line) { return vector(line, endTicks * tickMs, tickMs, settings); });
  }

  // a line's coordinates at `ms`: [x, y, z, turn x, turn y, turn z, its time]
  function at(v, ms) {
    var n = Math.max(0, Math.min(v.axes[0].length - 1, Math.round(ms / STEP)));
    return v.axes.map(function (values) { return values[n]; });
  }

  // a line's rows at `tick`
  function rowsAt(v, tick) {
    var rows = 0;
    for (var i = 0; i < v.rows.length && v.rows[i][0] <= tick; i++) rows = v.rows[i][1];
    return rows;
  }

  // the rotation of a turn about x, y and z at once, in degrees, as a matrix
  function rotation(w) {
    var x = w[0] * RAD, y = w[1] * RAD, z = w[2] * RAD, t = Math.sqrt(x * x + y * y + z * z);
    if (t < 1e-12) return [1, 0, 0, 0, 1, 0, 0, 0, 1];
    var kx = x / t, ky = y / t, kz = z / t, c = Math.cos(t), s = Math.sin(t), v = 1 - c;
    return [c + kx * kx * v, kx * ky * v - kz * s, kx * kz * v + ky * s,
            ky * kx * v + kz * s, c + ky * ky * v, ky * kz * v - kx * s,
            kz * kx * v - ky * s, kz * ky * v + kx * s, c + kz * kz * v];
  }

  // a camera turned `yaw` and `pitch` degrees about `target` (the sign's centre unless given),
  // `distance` lamp widths from it at a zoom of 1, farther as it zooms out, so that paths far
  // from the sign come in view
  function camera(width, height, yaw, pitch, zoom, distance, target) {
    target = target || [WIDTH / 2, ROWS / 2, 0];
    var cy = Math.cos(yaw * RAD), sy = Math.sin(yaw * RAD), cp = Math.cos(pitch * RAD), sp = Math.sin(pitch * RAD);
    var far = distance / zoom, scale = width / WIDTH * distance * 0.92;
    // seen from `pitch` degrees above, so what is in front of the sign is drawn below it, nearer you
    function turn(p) {
      var x1 = cy * p[0] + sy * p[2], z1 = -sy * p[0] + cy * p[2];
      return [x1, cp * p[1] + sp * z1, -sp * p[1] + cp * z1];
    }
    return {
      facing: function (n) { return turn(n)[2]; },
      project: function (p) {
        var q = turn([p[0] - target[0], p[1] - target[1], p[2] - target[2]]), s = scale / (far - q[2]);
        return q[2] >= far - 1 ? null : [width / 2 + q[0] * s, height / 2 + q[1] * s, q[2]];
      }
    };
  }

  // how bright a lamp `z` lamp widths from the sign's plane is: full on it and in front of it, and
  // fading out behind it - a quarter as bright halfway to `depth`, dark past it
  function brightness(z, depth) {
    if (z >= 0) return 1;
    var d = 1 - Math.min(1, -z / depth);
    return d * d;
  }

  function mix(colour, k) {
    var n = parseInt(colour.slice(1), 16);
    return 'rgb(' + Math.round((n >> 16 & 255) * k) + ',' + Math.round((n >> 8 & 255) * k) + ',' +
      Math.round((n & 255) * k) + ')';
  }

  // A keyed swing's posed part, as polar.js's sceneAt gives it, as lamps: each of its drawing's lit
  // lamps placed by its pose, stretched by `k` from the pivot, turned by R and moved by `d`, with
  // the two half-axes of its disc; a round part keeps its outline facing the viewer and turns its
  // seams inside it, as polar.js lights it
  function partLamps(part, r) {
    var k = part.k || 1, R = part.R, M = part.round ? [1, 0, 0, 0, 1, 0, 0, 0, 1] : R, pivot = part.pivot, d = part.d;
    var a = [M[0] * r * k, M[3] * r * k, M[6] * r * k], b = [M[1] * r * k, M[4] * r * k, M[7] * r * k], out = [];
    var drawn = part.round ? part.fill : part.src;
    for (var x = 0; x < WIDTH; x++) {
      if (!drawn[x]) continue;
      for (var y = 0; y < ROWS; y++) {
        if (!(drawn[x] >> y & 1)) continue;
        var u = (x + 0.5 - pivot[0]) * k, v = (y + 0.5 - pivot[1]) * k;
        if (part.round && part.seams) {
          // where the turn takes this point of the outline from, on the part's own drawing
          var sx = Math.floor((R[0] * u + R[3] * v) / k + pivot[0]), sy = Math.floor((R[1] * u + R[4] * v) / k + pivot[1]);
          if (sx >= 0 && sx < WIDTH && sy >= 0 && sy < ROWS && part.seams[sx] >> sy & 1) continue;
        }
        out.push({c: [M[0] * u + M[1] * v + pivot[0] + d[0], M[3] * u + M[4] * v + pivot[1] + d[1], M[6] * u + M[7] * v + d[2]],
                  a: a, b: b});
      }
    }
    return out;
  }

  // The sign at a moment: `view.frame` the frame shown, `view.lines` each line's rows, pose and
  // the frame at its own time, `view.paths` each line's path; a keyed swing's `view.parts`, each
  // posed as polar.js's sceneAt gives it, faded over `view.partDepth`, and `view.partPaths` the
  // paths their pivots take; `view.colours` the lamps' colours: unlit, then lit in no colour, red,
  // green and yellow
  function draw(canvas, view) {
    var g = canvas.getContext('2d'), cam = camera(canvas.width, canvas.height, view.yaw, view.pitch, view.zoom,
      view.distance, view.target), r = 0.42, items = [], still = 0, colours = view.colours;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.fillStyle = colours.sign;
    g.fillRect(0, 0, canvas.width, canvas.height);
    // the sign's plane, and its unlit lamps
    var corners = [[0, 0, 0], [WIDTH, 0, 0], [WIDTH, ROWS, 0], [0, ROWS, 0]].map(cam.project);
    if (corners.every(Boolean)) {
      g.strokeStyle = colours.plane;
      g.beginPath();
      corners.forEach(function (p, i) { if (i) g.lineTo(p[0], p[1]); else g.moveTo(p[0], p[1]); });
      g.closePath();
      g.stroke();
    }
    view.lines.forEach(function (line) { still |= line.rows; });
    function lamp(colour, k, centre, a, b, depth) {
      items.push({c: centre, a: a, b: b, fill: mix(colour, k), depth: depth});
    }
    function colourOf(frame, x, y) {
      var red = frame[2] ? frame[2][x] >> y & 1 : 0, green = frame[3] ? frame[3][x] >> y & 1 : 0;
      return colours.lit[red | green << 1];
    }
    for (var x = 0; x < WIDTH; x++) {
      for (var y = 0; y < ROWS; y++) {
        var centre = [x + 0.5, y + 0.5, 0];
        if (!(still >> y & 1) && view.frame[1][x] >> y & 1) lamp(colourOf(view.frame, x, y), 1, centre, [r, 0, 0], [0, r, 0], 0);
        else lamp(colours.unlit, 1, centre, [r, 0, 0], [0, r, 0], -1e-3);
      }
    }
    // each line's lamps, where its pose takes them
    view.lines.forEach(function (line) {
      var R = line.R, pivot = line.pivot, d = line.d, a = [R[0] * r, R[3] * r, R[6] * r], b = [R[1] * r, R[4] * r, R[7] * r];
      for (var x = 0; x < WIDTH; x++) {
        var column = line.frame[1][x] & line.rows;
        if (!column) continue;
        for (var y = 0; y < ROWS; y++) {
          if (!(column >> y & 1)) continue;
          var u = x + 0.5 - pivot[0], v = y + 0.5 - pivot[1];
          var p = [R[0] * u + R[1] * v + pivot[0] + d[0], R[3] * u + R[4] * v + pivot[1] + d[1], R[6] * u + R[7] * v + d[2]];
          lamp(colourOf(line.frame, x, y), brightness(p[2], view.depth), p, a, b, p[2]);
        }
      }
    });
    // a keyed swing's parts, where their poses take them
    (view.parts || []).forEach(function (part) {
      partLamps(part, r).forEach(function (l) {
        lamp(colours.lit[0], brightness(l.c[2], view.partDepth || view.depth), l.c, l.a, l.b, l.c[2]);
      });
    });
    items.forEach(function (it) {
      it.o = cam.project(it.c);
      it.pa = cam.project([it.c[0] + it.a[0], it.c[1] + it.a[1], it.c[2] + it.a[2]]);
      it.pb = cam.project([it.c[0] + it.b[0], it.c[1] + it.b[1], it.c[2] + it.b[2]]);
    });
    items = items.filter(function (it) { return it.o && it.pa && it.pb; });
    items.sort(function (p, q) { return p.o[2] - q.o[2] || p.depth - q.depth; });
    items.forEach(function (it) {
      g.setTransform(it.pa[0] - it.o[0], it.pa[1] - it.o[1], it.pb[0] - it.o[0], it.pb[1] - it.o[1], it.o[0], it.o[1]);
      g.fillStyle = it.fill;
      g.beginPath();
      g.arc(0, 0, 1, 0, 2 * Math.PI);
      g.fill();
    });
    g.setTransform(1, 0, 0, 1, 0, 0);
    // each line's path over the message, and where it is now; then each swinging part's
    view.paths.concat(view.partPaths || []).forEach(function (path, k) {
      g.strokeStyle = k < view.paths.length ? colours.paths[k] : colours.partPath;
      g.lineWidth = 1.2;
      g.beginPath();
      var moved = false;
      path.points.forEach(function (w) {
        var s = cam.project(w);
        if (!s) { moved = false; return; }
        if (moved) g.lineTo(s[0], s[1]); else g.moveTo(s[0], s[1]);
        moved = true;
      });
      g.stroke();
      var now = cam.project(path.now);
      if (now) {
        g.fillStyle = k < view.paths.length ? colours.paths[k] : colours.partPath;
        g.beginPath();
        g.arc(now[0], now[1], 3.5, 0, 2 * Math.PI);
        g.fill();
      }
    });
  }

  // each line's signals over the whole message - a bar where each of bits 0 to 7 is 1, named and
  // coloured by what it drives - and the coordinates they drive: positions, turns (dashed) and how
  // far the line's time runs behind the sign's (dotted), each on its own scale; `ms` marks the moment
  function timeline(canvas, vectors, endMs, tickMs, ms, settings, colours) {
    var g = canvas.getContext('2d'), w = canvas.width, left = 110, right = w - 8, y = 4, lane = 9, panel = 42;
    var X = function (t) { return left + (right - left) * t / Math.max(1, endMs); };
    var AXIS = ['#ff5a4a', '#5ad65a', '#5aa8ff', '#ff8ad8', '#c77dff', '#4dd0c8'];
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.fillStyle = colours.sign;
    g.fillRect(0, 0, w, canvas.height);
    g.font = '10px system-ui, sans-serif';
    vectors.forEach(function (v, k) {
      g.fillStyle = colours.text;
      g.fillText('line ' + k, 4, y + 9);
      y += 12;
      for (var bit = 0; bit < BITS; bit++) {
        var target = settings.dirs[bit] || 'nothing', on = null, t0 = 0;
        g.fillStyle = colours.muted;
        g.fillText('bit ' + bit + ' \u2192 ' + target.replace('-', '\u2212'), 4, y + 7);
        g.fillStyle = colours.lane;
        g.fillRect(left, y, right - left, lane - 2);
        g.fillStyle = TARGETS[target] ? (TARGETS[target][0] === 6 ? '#d8c070' : AXIS[TARGETS[target][0]])
                                      : target === 'red' ? colours.lit[1] : target === 'green' ? colours.lit[2] : colours.muted;
        v.fonts.concat([[endMs / tickMs, 0]]).forEach(function (f) {
          var now = f[1] >> bit & 1, at = f[0] * tickMs;
          if (on && at > t0) g.fillRect(X(t0), y, Math.max(1, X(at) - X(t0)), lane - 2);
          if (now !== on) {
            if (!on) t0 = at;
            on = now;
          }
        });
        y += lane;
      }
      [[0, 'position', []], [3, 'turn', [4, 3]]].forEach(function (part) {
        var most = 1e-9, from = part[0];
        for (var c = from; c < from + 3; c++) v.axes[c].forEach(function (a) { most = Math.max(most, Math.abs(a)); });
        g.fillStyle = colours.lane;
        g.fillRect(left, y + 2, right - left, panel - 4);
        g.fillStyle = colours.muted;
        g.fillText(part[1] + ' \u00b1' + (most < 10 ? most.toFixed(1) : Math.round(most)) + (from ? '\u00b0' : ' lamps'), 4, y + 14);
        g.setLineDash(part[2]);
        for (c = from; c < from + 3; c++) {
          g.strokeStyle = AXIS[c];
          g.beginPath();
          v.axes[c].forEach(function (a, n) {
            var px = X(n * STEP), py = y + panel / 2 - a / most * (panel / 2 - 4);
            if (n) g.lineTo(px, py); else g.moveTo(px, py);
          });
          g.stroke();
        }
        if (!from) {
          // how far the line's time is behind the sign's
          var late = 1e-9;
          v.axes[6].forEach(function (a, n) { late = Math.max(late, n * STEP - a); });
          if (late > 1) {
            g.setLineDash([1, 3]);
            g.strokeStyle = '#d8c070';
            g.beginPath();
            v.axes[6].forEach(function (a, n) {
              var px = X(n * STEP), py = y + panel - 4 - (n * STEP - a) / late * (panel - 8);
              if (n) g.lineTo(px, py); else g.moveTo(px, py);
            });
            g.stroke();
            g.fillStyle = '#d8c070';
            g.fillText('time behind 0-' + (late / 1000).toFixed(1) + ' s', 4, y + 26);
          }
        }
        g.setLineDash([]);
        y += panel;
      });
      y += 6;
    });
    g.strokeStyle = colours.text;
    g.beginPath();
    g.moveTo(X(ms), 0);
    g.lineTo(X(ms), y);
    g.stroke();
    return y;
  }

  // where a line's pivot goes over the message, every `every` ms
  function path(v, pivot, endMs, every) {
    var points = [];
    for (var ms = 0; ms <= endMs; ms += every) {
      var c = at(v, ms);
      points.push([pivot[0] + c[0], pivot[1] + c[1], c[2]]);
    }
    return points;
  }

  // a line's pivot: the middle of its rows, halfway along the sign
  function pivotOf(rows) {
    var top = -1, bottom = -1;
    for (var y = 0; y < ROWS; y++) {
      if (rows >> y & 1) {
        if (top < 0) top = y;
        bottom = y;
      }
    }
    return [WIDTH / 2, top < 0 ? ROWS / 2 : (top + bottom + 1) / 2];
  }

  // where to look and how far out to be to see all of `paths` and the sign: their middle, and a
  // zoom that fits their widest extent in the view
  function fit(paths) {
    var lo = [0, 0, 0], hi = [WIDTH, ROWS, 0];
    paths.forEach(function (points) {
      points.forEach(function (p) {
        for (var i = 0; i < 3; i++) {
          lo[i] = Math.min(lo[i], p[i]);
          hi[i] = Math.max(hi[i], p[i]);
        }
      });
    });
    var extent = Math.max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2], WIDTH);
    return {target: [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2],
            zoom: Math.max(0.01, Math.min(3, 0.9 * WIDTH / extent))};
  }

  return {TARGETS: TARGETS, defaults: defaults, signals: signals, smoothed: smoothed, vector: vector, fit: fit,
          vectors: vectors, at: at, rowsAt: rowsAt, rotation: rotation, camera: camera, brightness: brightness,
          partLamps: partLamps, draw: draw, path: path, pivotOf: pivotOf, timeline: timeline};
})();

if (typeof module !== 'undefined') module.exports = SilentRadioMotion;
