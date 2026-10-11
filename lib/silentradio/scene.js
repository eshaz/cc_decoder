// The sign's run as layers of lamps, and the layers as an animated SVG: what sign.js's
// op-log did to the lamps becomes layers, each the sign's own function over time - a
// travelling line its strip shifted a column a display frame, a roll its rows shifted a row
// a step, a build-up the page shifted into each byte of eight columns, a show its page in a
// window that grows, a twinkle its page let through lamp by lamp - with the frames' flashing
// and border over them. `verify` holds the layers against the frames at every tick either
// changes; where they differ, the frames themselves are shown instead. `svg` writes each
// layer as SVG that SMIL animates: the lamps it lights as outlines lit through the lamp
// grid, and how they move, the window they show in and when as functions of time, each run
// of equal steps one animation that adds them up. scene.py is the same, function for
// function.
//
// A layer is shown for ticks [t0, t1): inside `clip` and its window at the time, and where
// its `comb` or `passes` let it through, it paints `fill` (lamps, bit r a row) unlit and then
// `dots` lit (or unlit), moved by its `move` and `cycle`; a `blink` layer only in the second
// half of each blink. Its dots' `hues`, where it has them, are which of its lamps are red and
// which green, yellow both.
//
// Public domain / Unlicense, as with the rest of ccDecoder.

var SilentRadioScene = (function () {
  'use strict';

  var Sign = typeof SilentRadioSign !== 'undefined' ? SilentRadioSign : require('./sign.js');
  var WIDTH = Sign.WIDTH, ROWS = Sign.ROWS;

  function filled(n, value) {
    var out = [], i;
    for (i = 0; i < n; i++) out.push(value);
    return out;
  }

  function rowMask(y0, y1) {
    return y1 > y0 ? (1 << y1) - (1 << y0) : 0;
  }

  // [first row, last row + 1) of a run of rows
  function rowsOf(mask) {
    var y0 = 0, y1;
    if (!mask) return [0, 0];
    while (!(mask >> y0 & 1)) y0++;
    y1 = y0;
    while (mask >> y1) y1++;
    return [y0, y1];
  }

  function rectFill(x0, y0, x1, y1) {
    var out = filled(WIDTH, 0), x;
    for (x = Math.max(0, x0); x < Math.min(WIDTH, x1); x++) out[x] = rowMask(y0, y1);
    return out;
  }

  function empty(r) {
    return r[2] <= r[0] || r[3] <= r[1];
  }

  function overlaps(a, b) {
    return !empty(a) && !empty(b) && a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];
  }

  function contains(outer, inner) {
    return empty(inner) || (inner[0] >= outer[0] && inner[2] <= outer[2] && inner[1] >= outer[1] && inner[3] <= outer[3]);
  }

  function intersect(a, b) {
    return [Math.max(a[0], b[0]), Math.max(a[1], b[1]), Math.min(a[2], b[2]), Math.min(a[3], b[3])];
  }

  // the smallest rectangle round a set of lamps
  function bounds(cols) {
    var r = [WIDTH, ROWS, 0, 0], x, y;
    for (x = 0; x < cols.length; x++) {
      if (!cols[x]) continue;
      y = rowsOf(cols[x]);
      r = [Math.min(r[0], x), Math.min(r[1], y[0]), Math.max(r[2], x + 1), Math.max(r[3], y[1])];
    }
    return r;
  }

  function sameList(a, b) {
    if (a.length !== b.length) return false;
    for (var i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
  }

  function samePlanes(a, b) {
    return a.length === b.length && a.every(function (plane, q) { return sameList(plane, b[q]); });
  }

  function layer(kind, t0, clip) {
    return {kind: kind, t0: t0, t1: null, clip: clip, fill: null, dots: null, lit: true,
            move: null, win: null, blink: false, cycle: null, comb: null, passes: null};
  }

  // the entry of a track in force at t, or null before its first
  function trackAt(track, t) {
    var lo = 0, hi = track.length - 1, mid, found = null;
    while (lo <= hi) {
      mid = (lo + hi) >> 1;
      if (track[mid][0] <= t) {
        found = track[mid];
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    return found;
  }

  // the step a cycle is on at t: 0 until `from`, then one more each `period`
  function cycleStep(cycle, t) {
    return t < cycle.from ? 0 : (1 + Math.floor((t - cycle.from) / cycle.period)) % cycle.offsets.length;
  }

  function offsetAt(l, t) {
    var dx = 0, dy = 0, at, step;
    if (l.move) {
      at = trackAt(l.move, t);
      if (at) {
        dx = at[1];
        dy = at[2];
      }
    }
    if (l.cycle) {
      step = l.cycle.offsets[cycleStep(l.cycle, t)];
      dx += step[0];
      dy += step[1];
    }
    return [dx, dy];
  }

  // the columns of each byte of eight a build-up's side shows at t, [first, last + 1)
  function combAt(comb, t) {
    var step = trackAt(comb.steps, t)[1];
    return comb.side === 'page' ? [0, step + 1] : [step + 1, 8];
  }

  // the rows of each column a layer's comb or passes let it paint at t
  function letThrough(l, t, rows) {
    var c, shown, n, x;
    if (l.comb) {
      c = combAt(l.comb, t);
      shown = [];
      for (x = 0; x < WIDTH; x++) shown.push(c[0] <= (x & 7) && (x & 7) < c[1] ? rows : 0);
      return shown;
    }
    if (l.passes) {
      shown = filled(WIDTH, 0);
      for (n = 0; n < l.passes.length && l.passes[n][0] <= t; n++) for (x = 0; x < WIDTH; x++) shown[x] |= l.passes[n][1][x];
      for (x = 0; x < WIDTH; x++) shown[x] &= rows;
      return shown;
    }
    return null;
  }

  // a column of bits moved to start at row y, cut at the sign's last row
  function place(column, y) {
    if (y >= ROWS || y <= -31) return 0;
    return y >= 0 ? (column & (1 << ROWS - y) - 1) << y : column >> -y;
  }

  // what layer `l` shows at t over `planes`, the lamps and their red and green
  function paint(l, t, planes, blinkTicks) {
    var v = l.clip, w, rows, x, off, i, m, got, cols, through, hues, q;
    if (t < l.t0 || (l.t1 !== null && t >= l.t1)) return;
    if (l.blink && !(Math.floor(t / blinkTicks) & 1)) return;
    if (l.win) {
      w = trackAt(l.win, t);
      if (!w) return;
      v = intersect(v, w.slice(1));
    }
    if (empty(v)) return;
    rows = rowMask(v[1], v[3]);
    off = offsetAt(l, t);
    through = letThrough(l, t, rows);
    hues = l.dots && l.lit ? l.dots.hues : null;
    for (x = v[0]; x < v[2]; x++) {
      m = through === null ? rows : through[x];
      if (!m) continue;
      if (l.fill) {
        q = l.fill[x] & m;
        planes[0][x] &= ~q;
        planes[1][x] &= ~q;
        planes[2][x] &= ~q;
      }
      if (!l.dots) continue;
      cols = l.dots.cols;
      i = x - l.dots.x - off[0];
      if (i < 0 || i >= cols.length || !cols[i]) continue;
      got = place(cols[i], l.dots.y + off[1]) & m;
      planes[1][x] &= ~got;
      planes[2][x] &= ~got;
      if (!l.lit) {
        planes[0][x] &= ~got;
        continue;
      }
      planes[0][x] |= got;
      if (hues && hues[0][i]) planes[1][x] |= place(hues[0][i], l.dots.y + off[1]) & m;
      if (hues && hues[1][i]) planes[2][x] |= place(hues[1][i], l.dots.y + off[1]) & m;
    }
  }

  // [lamps, red, green] the layers show at t
  function rasterize(scene, t, blinkTicks) {
    var planes = [filled(WIDTH, 0), filled(WIDTH, 0), filled(WIDTH, 0)], n;
    for (n = 0; n < scene.layers.length; n++) paint(scene.layers[n], t, planes, blinkTicks);
    return planes;
  }

  // The chasing border's four edges, each a run of lamps that steps along one place a step,
  // as `bordered` draws them: [clip, dots, offsets] for each, or null if one does not
  function borderEdges(c) {
    var phases = [0, 1, 2, 3, 4].map(function (s) { return Sign.bordered(filled(WIDTH, 0), (3 + 2 * s) % 5, c); });
    var edges = [[[0, 0, WIDTH, 1], WIDTH, function (j) { return [j, 0]; }, 0],
                 [[0, ROWS - 1, WIDTH, ROWS], WIDTH, function (j) { return [j, ROWS - 1]; }, 0],
                 [[0, 1, 1, ROWS - 1], ROWS - 2, function (j) { return [0, 1 + j]; }, 1],
                 [[WIDTH - 1, 1, WIDTH, ROWS - 1], ROWS - 2, function (j) { return [WIDTH - 1, 1 + j]; }, 1]];
    var out = [], e, len, at, bits, period, ext, offsets, s, sigma, j, ok, dots, xy;
    for (e = 0; e < edges.length; e++) {
      len = edges[e][1];
      at = edges[e][2];
      bits = phases.map(function (lamps) {
        var row = [];
        for (var q = 0; q < len; q++) {
          xy = at(q);
          row.push(lamps[xy[0]] >> xy[1] & 1);
        }
        return row;
      });
      for (period = 1; period <= 5; period++) {
        for (ok = true, j = 0; j + period < len && ok; j++) ok = bits[0][j] === bits[0][j + period];
        if (ok) break;
      }
      if (!ok) return null;
      ext = [];
      for (j = 0; j < len + 4; j++) ext.push(bits[0][j < len ? j : j - period * Math.ceil((j - len + 1) / period)]);
      offsets = [];
      for (s = 0; s < 5; s++) {
        for (sigma = 0; sigma < 5; sigma++) {
          for (ok = true, j = 0; j < len && ok; j++) ok = bits[s][j] === ext[j + sigma];
          if (ok) break;
        }
        if (!ok) return null;
        offsets.push(edges[e][3] ? [0, -sigma] : [-sigma, 0]);
      }
      if (edges[e][3]) {
        dots = {x: edges[e][0][0], y: 1, cols: [ext.reduce(function (col, bit, q) { return col | bit << q; }, 0)], hues: null};
      } else {
        dots = {x: 0, y: edges[e][0][1], cols: ext, hues: null};
      }
      out.push([edges[e][0], dots, offsets]);
    }
    return out;
  }

  // [[tick, lamps, red, green]] as the program plays them, from 0, each held until the next;
  // and the tick it ends on
  function played(result) {
    var out = [], at = 0, n, stop, shown = result.shown;
    for (n = 0; n < shown.length; n++) {
      stop = n + 1 < shown.length ? shown[n + 1][0] : result.end;
      if (stop > shown[n][0]) {
        out.push([at].concat(shown[n].slice(1)));
        at += stop - shown[n][0];
      }
    }
    return [out, at];
  }

  // The layers of a run `play` gave with its op-log
  function build(result, params, tables) {
    var ops = result.ops || [], frames = result.frames, play = played(result);
    var layers = [], live = {}, rolls = {};
    var n, op, s, l, y, key, h, top, i, j, t, t1, flashing, edges, band, got;

    // a line left waiting at a 00 by one that has finished goes on from its own tick, so
    // its frames come after later ones; the program plays them on from where it was
    for (n = 1; n < frames.length; n++) {
      if (frames[n][0] < frames[n - 1][0]) return {end: play[1], layers: [], spans: [[0, play[1]]], played: play[0]};
    }

    // An effect's layers go on taking its steps until another write lands in their window;
    // its next step then starts layers of its own over that write.
    function close(region, keep) {
      for (var name in live) if (name !== keep && overlaps(live[name].clip, region)) delete live[name];
    }

    function open(name, clip, made, page) {
      live[name] = {clip: clip, layers: made, page: page};
      made.forEach(function (m) { layers.push(m); });
      return made;
    }

    function strip(t, op, y) {
      var m = layer('travel', t, [op.a, y[0], op.b, y[1]]);
      m.fill = rectFill(op.a, y[0], op.b, y[1]);
      m.dots = {x: op.a, y: 0, cols: op.before.slice(), hues: op.before_hues.map(function (hue) { return hue.slice(); })};
      m.move = [];
      m.a = op.a;
      m.b = op.b;
      m.mask = op.mask;
      m.shifts = 0;
      return m;
    }

    function shownLayer(t, op, y) {
      var m = layer('show', t, [op.a, y[0], op.b, y[1]]);
      m.fill = rectFill(op.a, y[0], op.b, y[1]);
      m.dots = {x: op.a, y: 0, cols: op.page, hues: op.hues};
      m.win = [];
      return m;
    }

    // a build-up rotates the page into each byte of eight columns from its left, what was
    // shown going out at its right: the page shifted on, and what was shown, each through a
    // comb of the columns of each byte it has reached
    function side(t, op, y, name, cols, hues) {
      var m = layer('build', t, [op.a, y[0], op.b, y[1]]);
      m.fill = rectFill(op.a, y[0], op.b, y[1]);
      m.dots = {x: op.a, y: 0, cols: cols, hues: hues};
      m.move = [];
      m.comb = {side: name, steps: []};
      return m;
    }

    function rolled(roll, t) {
      var m = layer('roll', t, roll.clip);
      m.fill = rectFill(roll.clip[0], roll.clip[1], roll.clip[2], roll.clip[3]);
      m.dots = roll.dots;
      m.move = [];
      m.up = roll.up;
      return m;
    }

    for (n = 0; n < ops.length; n++) {
      // a line that goes on from its own tick after the other has finished writes after
      // what the other wrote, though at an earlier tick; with the frames in order, nothing
      // it wrote showed until the frame after it, so it takes effect from the one before
      op = ops[n];
      t = Math.max(op.t, op.after);
      if (op.op === 'travel') {
        key = 'travel' + op.k;
        y = rowsOf(op.mask);
        s = live[key] && live[key].layers[0];
        if (!s || s.a !== op.a || s.b !== op.b || s.mask !== op.mask) s = open(key, [op.a, y[0], op.b, y[1]], [strip(t, op, y)])[0];
        s.dots.cols.push(op.column);
        s.dots.hues[0].push(op.hues[0]);
        s.dots.hues[1].push(op.hues[1]);
        s.shifts += 1;
        s.move.push([t, -s.shifts, 0]);
        close(s.clip, key);
      } else if (op.op === 'show') {
        // each step shows the page as it is then, which the other line can draw into
        key = 'show' + op.k + ':' + op.id;
        y = rowsOf(op.window);
        l = live[key] && live[key].layers[0];
        if (!l || !samePlanes([l.dots.cols].concat(l.dots.hues), [op.page].concat(op.hues))) {
          l = open(key, [op.a, y[0], op.b, y[1]], [shownLayer(t, op, y)])[0];
        }
        y = rowsOf(op.mask);
        l.win.push([t, op.start, y[0], op.stop, y[1]]);
        close([op.start, y[0], op.stop, y[1]], key);
      } else if (op.op === 'build') {
        key = 'build' + op.k + ':' + op.id;
        y = rowsOf(op.mask);
        got = live[key];
        if (!got) {
          got = live[key] = {clip: [op.a, y[0], op.b, y[1]], layers: [side(t, op, y, 'shown', op.shown, op.shown_hues)],
                             page: null};
          layers.push(got.layers[0]);
        }
        if (!got.page || !samePlanes(got.page, [op.page].concat(op.page_hues))) {
          // the other line drew into the page: its next step shifts the page as it is now
          if (got.layers.length > 1) got.layers[1].t1 = t;
          got.layers[1] = side(t, op, y, 'page', op.page, op.page_hues);
          got.page = [op.page].concat(op.page_hues);
          layers.push(got.layers[1]);
        }
        got.layers[0].move.push([t, op.step + 1, 0]);
        got.layers[0].comb.steps.push([t, op.step]);
        got.layers[1].move.push([t, op.step - 7, 0]);
        got.layers[1].comb.steps.push([t, op.step]);
        close([op.a, y[0], op.b, y[1]], key);
      } else if (op.op === 'twinkle') {
        // the page let through lamp by lamp: a layer of it, with each pass's lamps it changes
        key = 'twinkle' + op.k + ':' + op.id;
        got = live[key];
        if (!got || !samePlanes(got.page, [op.page].concat(op.hues))) {
          l = layer('twinkle', t, [0, 0, WIDTH, ROWS]);
          l.fill = rectFill(0, 0, WIDTH, ROWS);
          l.dots = {x: 0, y: 0, cols: op.page, hues: op.hues};
          l.passes = [];
          open(key, l.clip, [l], [op.page].concat(op.hues));
        }
        if (op.changed.some(Boolean)) live[key].layers[0].passes.push([t, op.changed]);
        close(bounds(op.touched), key);
      } else if (op.op === 'roll') {
        h = op.bottom - op.top + 1;
        if (h <= 0) continue;
        top = op.top;
        // what was shown over the page, or under it rolling down
        var stacked = function (shown, pages) {
          return shown.map(function (was, q) {
            var page = pages[q] >> top & (1 << h) - 1;
            was = was >> top & (1 << h) - 1;
            return op.up ? was | page << h : page | was << h;
          });
        };
        rolls[op.id] = {clip: [op.a, top, op.b, op.bottom + 1], up: op.up, dots: {x: op.a, y: op.up ? top : top - h,
          cols: stacked(op.shown, op.page), hues: [0, 1].map(function (q) { return stacked(op.shown_hues[q], op.page_hues[q]); })}};
      } else if (op.op === 'rollstep') {
        key = 'roll' + op.id;
        if (!rolls[op.id]) continue;
        l = live[key] && live[key].layers[0];
        if (!l) l = open(key, rolls[op.id].clip, [rolled(rolls[op.id], t)])[0];
        l.move.push([t, 0, l.up ? -(op.step + 1) : op.step + 1]);
        close(l.clip, key);
      } else if (op.op === 'unborder') {
        l = layer('clear', t, [0, 0, WIDTH, ROWS]);
        l.fill = filled(WIDTH, 0).map(function (m, q) { return q < 8 || q >= WIDTH - 8 ? rowMask(1, ROWS - 1) : 0; });
        layers.push(l);
        close([0, 1, 8, ROWS - 1]);
        close([WIDTH - 8, 1, WIDTH, ROWS - 1]);
      }
    }
    layers = layers.filter(function (m) { return !m.passes || m.passes.length; });
    retire(layers);

    // flashing lamps go dark in the second half of each blink, a layer each pattern
    for (i = 0; i < frames.length; i = j) {
      flashing = frames[i][2];
      for (j = i + 1; j < frames.length && sameList(frames[j][2], flashing); j++);
      if (!flashing.some(Boolean)) continue;
      l = layer('flash', frames[i][0], [0, 0, WIDTH, ROWS]);
      l.t1 = j < frames.length ? frames[j][0] : result.end;
      l.dots = {x: 0, y: 0, cols: flashing.slice(), hues: null};
      l.lit = false;
      l.blink = true;
      layers.push(l);
    }

    // the chasing border over all of it, from the tick it started
    edges = borderEdges({t: tables});
    for (i = 0; i < frames.length; i = j) {
      for (j = i + 1; j < frames.length && frames[j][3] === frames[i][3]; j++);
      if (frames[i][3] === null) continue;
      t1 = j < frames.length ? frames[j][0] : result.end;
      band = layer('border', frames[i][0], [0, 0, WIDTH, ROWS]);
      band.t1 = t1;
      band.fill = filled(WIDTH, 0).map(function (m, q) { return q < 8 || q >= WIDTH - 8 ? rowMask(0, ROWS) : 1 | 1 << ROWS - 1; });
      layers.push(band);
      (edges || []).forEach(function (edge) {
        l = layer('border', frames[i][0], edge[0]);
        l.t1 = t1;
        l.dots = edge[1];
        l.cycle = {from: frames[i][3] + params.frame_ticks, period: params.border_ticks, offsets: edge[2]};
        layers.push(l);
      });
    }
    return {end: result.end, layers: layers, spans: [], played: play[0]};
  }

  // What a layer covers for good from some tick on, if anything: [rect, tick] - a build-up's
  // two sides together, so its first
  function cover(l) {
    var last;
    if (l.kind === 'travel' || l.kind === 'roll' || l.kind === 'build' && l.comb.side === 'shown') return [l.clip, l.t0];
    if (l.kind === 'show' && l.win.length) {
      last = l.win[l.win.length - 1];
      return [intersect(l.clip, last.slice(1)), last[0]];
    }
    return null;
  }

  function extent(l) {
    var cols;
    if (l.kind === 'twinkle') {
      cols = filled(WIDTH, 0);
      l.passes.forEach(function (pass) { cols = cols.map(function (v, x) { return v | pass[1][x]; }); });
      return bounds(cols);
    }
    return l.kind === 'clear' ? bounds(l.fill) : l.clip;
  }

  // A layer a later one covers for good goes when it does
  function retire(layers) {
    var i, j, got, reach = layers.map(extent);
    for (j = 1; j < layers.length; j++) {
      got = cover(layers[j]);
      if (!got) continue;
      for (i = 0; i < j; i++) {
        if (contains(got[0], reach[i]) && (layers[i].t1 === null || layers[i].t1 > got[1])) layers[i].t1 = Math.max(got[1], layers[i].t0);
      }
    }
  }

  // Every tick at which the layers or the frames can change
  function breakpoints(scene, shown, blinkTicks) {
    var ticks = {}, end = scene.end;

    function add(t) {
      if (t >= 0 && t < end) ticks[t] = true;
    }

    shown.forEach(function (frame) { add(frame[0]); });
    scene.layers.forEach(function (l) {
      var stop = l.t1 === null ? end : Math.min(l.t1, end), t;
      add(l.t0);
      if (l.t1 !== null) add(l.t1);
      (l.move || []).concat(l.win || [], l.passes || []).forEach(function (entry) { add(entry[0]); });
      if (l.cycle) for (t = l.cycle.from; t < stop; t += l.cycle.period) if (t >= l.t0) add(t);
      if (l.blink) for (t = Math.ceil(l.t0 / blinkTicks) * blinkTicks; t < stop; t += blinkTicks) add(t);
    });
    return Object.keys(ticks).map(Number).sort(function (a, b) { return a - b; });
  }

  // Hold the layers against the frames the program plays at every breakpoint; spans where
  // they differ go to the frames themselves. Returns how many breakpoints differed.
  function verify(scene, blinkTicks) {
    var shown = scene.played, ticks = breakpoints(scene, shown, blinkTicks), spans = scene.spans.slice();
    var frame = 0, n, t, next, bad = 0;
    if (!scene.layers.length) return 0;
    for (n = 0; n < ticks.length; n++) {
      t = ticks[n];
      while (frame + 1 < shown.length && shown[frame + 1][0] <= t) frame++;
      if (samePlanes(rasterize(scene, t, blinkTicks), shown[frame].slice(1))) continue;
      bad += 1;
      next = n + 1 < ticks.length ? ticks[n + 1] : scene.end;
      if (spans.length && spans[spans.length - 1][1] === t) spans[spans.length - 1][1] = next;
      else spans.push([t, next]);
    }
    scene.spans = spans;
    return bad;
  }

  // --- the layers as SVG that SMIL animates ---

  // Lit lamps are drawn in tiles of this many columns, so a long travelling strip is not one
  // path the browser draws whole each step
  var TILE = 64;

  // The colours a lamp is lit in, by its red and green: none given, red, green and yellow, each
  // drawn through a lamp pattern of its own
  var ON = ['on', 'on1', 'on2', 'on3'];

  // The sign, its unlit lamps and its lit ones in each colour, `#rrggbb`, and how bright a lit
  // lamp of each colour is at each of the four levels a sign's `01 02` byte can set it to. Red and
  // green are a lit sign's in a photograph of one, a deep red and the yellow-green of GaP LEDs;
  // yellow is the two lit together, their light added
  var SIGN_COLOURS = [['sign', '#080604'], ['lamp-off', '#221a0c'], ['lamp-on', '#ffffff'], ['lamp-red', '#ff1a12'],
                      ['lamp-green', '#d1ff19'], ['lamp-yellow', '#ffce19']];
  var LEVELS = ['.28', '.5', '.75', '1'];

  // The lamps as the SVG and the pages draw them, each colour at its level: unless a sign's byte
  // sets it, the brightest
  var SVG_LAMPS = '.ground{fill:var(--sign)}.dim,.on,.on1,.on2,.on3{stroke-width:var(--lamp);stroke-linecap:var(--cap);' +
    'fill:none}.dim{stroke:var(--lamp-off)}' + ON.map(function (on, n) {
      return '.' + on + '{stroke:var(--' + SIGN_COLOURS[n + 2][0] + ');stroke-opacity:var(--level-' + n + ',1)}';
    }).join('');
  var SVG_STYLE = ':root{' + SIGN_COLOURS.map(function (pair) { return '--' + pair[0] + ':' + pair[1]; }).join(';') +
    ';--lamp:.78;--cap:round}' + SVG_LAMPS;

  // Where a scene's `shades` say, its frames' two colour planes are a level of brightness instead: a
  // keyed swing's, full as a lamp of no colour and the rest in the same colour, three quarters, half
  // and a quarter as bright
  var SHADES = ['sh1', 'sh2', 'sh3'];
  var SVG_SHADES = '.sh1,.sh2,.sh3{stroke-width:var(--lamp);stroke-linecap:var(--cap);fill:none;stroke:var(--lamp-on)}' +
    SHADES.map(function (sh, n) { return '.' + sh + '{stroke-opacity:calc(var(--level-0,1)*' + ['.75', '.5', '.28'][n] + ')}'; })
      .join('');

  // The levels a sign given `01 02` and the byte `levels`, in hex, lights each colour at, as CSS:
  // two bits a colour, from bit 0
  function levelsStyle(levels) {
    return [0, 1, 2, 3].map(function (n) { return '--level-' + n + ':' + LEVELS[parseInt(levels, 16) >> 2 * n & 3]; }).join(';');
  }

  // Lamps given as columns of bits, and their red and green, as the lamps of each colour
  function coloured(cols, hues) {
    if (!hues) return [cols, filled(cols.length, 0), filled(cols.length, 0), filled(cols.length, 0)];
    return [0, 1, 2, 3].map(function (colour) {
      return cols.map(function (c, x) {
        return c & (colour & 1 ? hues[0][x] : ~hues[0][x]) & (colour & 2 ? hues[1][x] : ~hues[1][x]);
      });
    });
  }

  function pad(n, width) {
    var s = String(n);
    while (s.length < width) s = '0' + s;
    return s;
  }

  // The keyTime of `p` milliseconds into `whole`, cut to seven places, so that a clock of whole
  // milliseconds reaches it on the millisecond `p`
  function fraction(p, whole) {
    var n = Math.floor(p * 1e7 / whole);
    return n >= 10000000 ? '1' : n <= 0 ? '0' : ('0.' + pad(n, 7)).replace(/0+$/, '');
  }

  // Milliseconds, to three places
  function ms(x) {
    var n = Math.floor(x * 1000 + 0.5);
    return String(Math.floor(n / 1000)) + ('.' + pad(n % 1000, 3)).replace(/0+$/, '').replace(/\.$/, '');
  }

  // Every animation starts again with the loop, a `set` as long as the program, and lasts no
  // longer than it, so nothing is held over from one loop to the next. Firefox times SMIL in
  // whole milliseconds, so each tick's steps are on the millisecond nearest it, which shows the
  // tick until the next one's in every browser, and one that repeats within the loop does so
  // in chunks that last a whole number of milliseconds.
  // The fewest ticks that are a whole number of milliseconds, or null if a thousand aren't:
  function grid(tickMs) {
    for (var g = 1; g <= 1000; g++) if (Math.abs(g * tickMs - Math.floor(g * tickMs + 0.5)) < 1e-9) return g;
    return null;
  }

  function gcd(a, b) {
    while (b) {
      var t = a % b;
      a = b;
      b = t;
    }
    return a;
  }

  function same(a, b) {
    return Array.isArray(a) ? sameList(a, b) : a === b;
  }

  // [[tick, value]] from 0, each value from its tick on, none the same as the one before
  function steps(entries, before) {
    var out = [[0, before]], kept, n, t, value;
    for (n = 0; n < entries.length; n++) {
      t = entries[n][0];
      value = entries[n][1];
      if (t <= 0) out[0] = [0, value];
      else if (out[out.length - 1][0] === t) out[out.length - 1][1] = value;
      else if (!same(out[out.length - 1][1], value)) out.push([t, value]);
    }
    kept = [out[0]];
    for (n = 1; n < out.length; n++) if (!same(out[n][1], kept[kept.length - 1][1])) kept.push(out[n]);
    return kept;
  }

  // [[tick, apart, step, count]], a stepped track's changes as runs of equal steps equally
  // far apart, a value or a list of them
  function runs(track) {
    var out = [], n, t, value, before, step, last, gap;
    for (n = 1; n < track.length; n++) {
      t = track[n][0];
      value = track[n][1];
      before = track[n - 1][1];
      step = Array.isArray(value) ? value.map(function (v, q) { return v - before[q]; }) : value - before;
      last = out[out.length - 1];
      if (last) {
        gap = t - last[0] - last[1] * (last[3] - 1);
        if (same(last[2], step) && (last[3] === 1 || gap === last[1])) {
          out[out.length - 1] = [last[0], last[3] === 1 ? gap : last[1], last[2], last[3] + 1];
          continue;
        }
      }
      out.push([t, 0, step, 1]);
    }
    return out;
  }

  // The four sides of a lamp, as the neighbour past each and the edge along it that keeps the
  // lamp on its left: top, left, bottom, right
  var SIDES = [[0, -1, 1, 0, 0, 0], [-1, 0, 0, 0, 0, 1], [0, 1, 0, 1, 1, 1], [1, 0, 1, 1, 1, 0]];
  var TURNS = [function (dx, dy) { return [dy, -dx]; }, function (dx, dy) { return [dx, dy]; },
               function (dx, dy) { return [-dy, dx]; }];

  // Lamps' sides that face no lit lamp, [ax, ay, bx, by] each with its lamp on its left, as the
  // outlines round them, a subpath each, turning in at a corner two only touch
  function outline(edges) {
    var starts = {}, used, out = '', last = null, key, first, points, e;
    var ax, ay, bx, by, dx, dy, choices, turn, q, r, n, start, corners, cx, cy, i;

    function corner(k) {
      var p = points[(k - 1 + n) % n], c = points[k], nx = points[(k + 1) % n];
      return c[0] - p[0] !== nx[0] - c[0] || c[1] - p[1] !== nx[1] - c[1];
    }

    edges.forEach(function (edge, m) {
      key = edge[0] + ',' + edge[1];
      (starts[key] = starts[key] || []).push(m);
    });
    used = filled(edges.length, false);
    for (first = 0; first < edges.length; first++) {
      if (used[first]) continue;
      points = [];
      e = first;
      while (!used[e]) {
        used[e] = true;
        ax = edges[e][0];
        ay = edges[e][1];
        bx = edges[e][2];
        by = edges[e][3];
        points.push([ax, ay]);
        dx = bx - ax;
        dy = by - ay;
        // the left turn, straight on, the right turn, back where it began closing it
        choices = (starts[bx + ',' + by] || []).filter(function (c) { return !used[c] || c === first; });
        e = first;
        search: for (q = 0; q < 3; q++) {
          turn = TURNS[q](dx, dy);
          for (r = 0; r < choices.length; r++) {
            if (edges[choices[r]][2] - bx === turn[0] && edges[choices[r]][3] - by === turn[1]) {
              e = choices[r];
              break search;
            }
          }
        }
      }
      // from the first corner, a line to each next corner
      n = points.length;
      for (start = 0; !corner(start); start++);
      corners = [];
      for (i = 0; i < n; i++) if (corner((start + i) % n)) corners.push(points[(start + i) % n]);
      cx = corners[0][0];
      cy = corners[0][1];
      out += last === null ? 'M' + cx + ' ' + cy : 'm' + (cx - last[0]) + ' ' + (cy - last[1]);
      for (i = 1; i < corners.length; i++) {
        out += corners[i][1] === cy ? 'h' + (corners[i][0] - cx) : 'v' + (corners[i][1] - cy);
        cx = corners[i][0];
        cy = corners[i][1];
      }
      out += 'z';
      last = corners[0];
    }
    return out;
  }

  // The outlines of lamps given as columns of bits, the first at column x0, bit 0 at row y0
  function colsOutline(cols, x0, y0) {
    var edges = [], i, y, s, col, facing, side;
    x0 = x0 || 0;
    y0 = y0 || 0;
    for (i = 0; i < cols.length; i++) {
      // its lamps' sides that face none, in the order of SIDES
      col = cols[i];
      facing = [col & ~(col << 1), col & ~(i ? cols[i - 1] : 0), col & ~(col >> 1),
                col & ~(i + 1 < cols.length ? cols[i + 1] : 0)];
      for (y = 0; col >> y; y++) {
        for (s = 0; s < 4; s++) {
          if (!(facing[s] >> y & 1)) continue;
          side = SIDES[s];
          edges.push([x0 + i + side[2], y0 + y + side[3], x0 + i + side[4], y0 + y + side[5]]);
        }
      }
    }
    return outline(edges);
  }

  // The sides of the lamps `lit(x, y)` lights in [x0, x0 + width) by [y0, y0 + height) that face
  // none there, in the order of SIDES
  function litEdges(lit, x0, y0, width, height) {
    var edges = [], x, y, s, side;
    function inside(x, y) {
      return x >= x0 && x < x0 + width && y >= y0 && y < y0 + height && lit(x, y);
    }
    for (x = x0; x < x0 + width; x++) {
      for (y = y0; y < y0 + height; y++) {
        if (!lit(x, y)) continue;
        for (s = 0; s < 4; s++) {
          side = SIDES[s];
          if (!inside(x + side[0], y + side[1])) edges.push([x + side[2], y + side[3], x + side[4], y + side[5]]);
        }
      }
    }
    return edges;
  }

  // Lanczos of three lobes, the kernel lamps are drawn finer with
  var LOBES = 3;

  function lanczos(x) {
    if (x === 0) return 1;
    if (Math.abs(x) >= LOBES) return 0;
    var px = Math.PI * x;
    return LOBES * Math.sin(px) * Math.sin(px / LOBES) / (px * px);
  }

  // For each of the `k` places across a lamp drawn `k` to a lamp, the lamps it is drawn from:
  // the first's offset and each one's weight, together one
  var tapsMade = {};

  function taps(k) {
    var out = [], a, c, first, w, i, sum;
    if (tapsMade[k]) return tapsMade[k];
    for (a = 0; a < k; a++) {
      c = (a + 0.5) / k - 0.5;
      first = Math.floor(c) - LOBES + 1;
      w = [];
      sum = 0;
      for (i = first; i <= Math.floor(c) + LOBES; i++) {
        w.push(lanczos(c - i));
        sum += w[w.length - 1];
      }
      out.push({first: first, weights: w.map(function (v) { return v / sum; })});
    }
    return (tapsMade[k] = out);
  }

  // Lamps given as columns of bits drawn `k` to a lamp: each a block of lamps where `blocky`,
  // or scaled up through Lanczos and cut at half, so what they draw keeps its shape and stays
  // sharp. {x0, y0, width, height, at(x, y)}, in lamps `k` to a lamp.
  function finer(cols, x0, y0, k, blocky) {
    var t = taps(k), margin = blocky ? 0 : 1, width = (cols.length + 2 * margin) * k, height = (31 + 2 * margin) * k;
    var fx0 = (x0 - margin) * k, fy0 = (y0 - margin) * k, cells = new Uint8Array(width * height);
    var x, y, a, b, j, w, sum, across, near;

    function get(cx, cy) {
      var i = cx - x0, row = cy - y0;
      return i >= 0 && i < cols.length && row >= 0 && row < 31 && cols[i] >> row & 1 ? 1 : 0;
    }

    for (x = x0 - margin; x < x0 + cols.length + margin; x++) {
      across = null;
      for (y = y0 - margin; y < y0 + 31 + margin; y++) {
        if (blocky) {
          if (!get(x, y)) continue;
          for (b = 0; b < k; b++) for (a = 0; a < k; a++) cells[(x * k + a - fx0) * height + (y * k + b - fy0)] = 1;
          continue;
        }
        // only a lamp lit or beside one can be drawn lit
        for (near = 0, a = -1; a <= 1 && !near; a++) for (b = -1; b <= 1 && !near; b++) near = get(x + a, y + b);
        if (!near) continue;
        if (!across) {
          // what each place across column x takes from the columns about it, by row
          across = [];
          for (a = 0; a < k; a++) {
            across.push({});
            for (j = y0 - margin - LOBES; j < y0 + 31 + margin + LOBES; j++) {
              for (sum = 0, b = 0, w = t[a].weights; b < w.length; b++) sum += w[b] * get(x + t[a].first + b, j);
              if (sum) across[a][j] = sum;
            }
          }
        }
        for (b = 0; b < k; b++) {
          for (a = 0; a < k; a++) {
            for (sum = 0, j = 0, w = t[b].weights; j < w.length; j++) sum += w[j] * (across[a][y + t[b].first + j] || 0);
            if (sum >= 0.5) cells[(x * k + a - fx0) * height + (y * k + b - fy0)] = 1;
          }
        }
      }
    }
    return {x0: fx0, y0: fy0, width: width, height: height,
            at: function (fx, fy) {
              var i = fx - fx0, j = fy - fy0;
              return i >= 0 && i < width && j >= 0 && j < height ? cells[i * height + j] : 0;
            }};
  }

  // A layer's lamps as outlines, a path for each tile of columns, `k` to a lamp
  function tiles(dots, k, blocky) {
    var out = [], i, d, fine;
    if ((k || 1) === 1) {
      for (i = 0; i < dots.cols.length; i += TILE) {
        d = colsOutline(dots.cols.slice(i, i + TILE), dots.x + i, dots.y);
        if (d) out.push(d);
      }
      return out;
    }
    fine = finer(dots.cols, dots.x, dots.y, k, blocky);
    for (i = fine.x0; i < fine.x0 + fine.width; i += TILE * k) {
      d = outline(litEdges(fine.at, i, fine.y0, Math.min(TILE * k, fine.x0 + fine.width - i), fine.height));
      if (d) out.push(d);
    }
    return out;
  }

  function lampsPath(lamps, k, blocky) {
    return tiles({x: 0, y: 0, cols: lamps}, k, blocky).join('') || 'M0 0';
  }

  // The tick of the display the program holds longest, the first if more than one
  function poster(scene) {
    var best = -1, at = 0, played = scene.played, n, held;
    for (n = 0; n < played.length; n++) {
      held = (n + 1 < played.length ? played[n + 1][0] : scene.end) - played[n][0];
      if (held > best) {
        best = held;
        at = played[n][0];
      }
    }
    return at;
  }

  function escapeHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
      .replace(/'/g, '&#x27;');
  }

  // The scene as an SVG of its own that plays it: `meta` goes in its metadata as JSON, `params`
  // as sign_parameters gives them time it, and `options` are its ids' `prefix` - SMIL's timing
  // reads a hyphen in one as a minus - its `px` to a lamp and how many lamps it draws to each of
  // the sign's, its `density`, smoothed unless `smooth` is false. Where nothing animates it,
  // it shows the display held longest.
  function svg(scene, meta, params, options) {
    options = options || {};
    var prefix = options.prefix === undefined ? 'sr_' : options.prefix, px = options.px || 8, k = options.density || 1;
    var blocky = options.smooth === false, tickMs = params.tick_ms, blinkTicks = params.blink_ticks, end = scene.end;
    var loop = Math.max(1, Math.ceil(end * tickMs)), g = grid(tickMs), at = poster(scene);
    var begin = prefix + 'L.begin', finish = prefix + 'L.end', defs = [], body = [], clips = {}, frames = '';
    var sides = [['x', function (r) { return r[0]; }], ['y', function (r) { return r[1]; }],
                 ['width', function (r) { return Math.max(0, r[2] - r[0]); }],
                 ['height', function (r) { return Math.max(0, r[3] - r[1]); }]];
    var scaled = k === 1 ? '' : '<g transform="scale(' + 1 / k + ')">';

    function stamp(t) {
      return Math.floor(t * tickMs + 0.5);
    }

    function when(t) {
      return begin + '+' + stamp(t) + 'ms';
    }

    function text(value) {
      return Array.isArray(value) ? value[0] + ' ' + value[1] : String(value);
    }

    function times(value, n) {
      return Array.isArray(value) ? value.map(function (v) { return v * n; }) : value * n;
    }

    function base(track) {
      return trackAt(track, at)[1];
    }

    // how many times `ticks` are the fewest that last a whole number of milliseconds, or 0 if
    // too many
    function whole(ticks) {
      var n = g ? g / gcd(ticks, g) : 0;
      return n <= 64 ? n : 0;
    }

    function list(count, f) {
      var out = [];
      for (var q = 0; q < count; q++) out.push(f(q));
      return out.join(';');
    }

    // values from `start` milliseconds into the loop, each `at` milliseconds after it, the last
    // held to the loop's end
    function held(head, values, at, start) {
      if (values.length === 1) {
        return head + ' additive="sum" values="' + values[0] + '" begin="' + begin + '+' + start + 'ms" end="' +
          finish + '"/>';
      }
      return head + ' additive="sum" calcMode="discrete" values="' + values.join(';') + '" keyTimes="' +
        at.map(function (p) { return fraction(p, loop - start); }).join(';') + '" begin="' + begin + '+' + start +
        'ms" dur="' + (loop - start) + 'ms"/>';
    }

    // a stepped track as its value at the loop's start and each run of equal steps after it,
    // every step added on to the ones before
    function functional(name, attribute, track, kind) {
      var head = '<' + name + ' attributeName="' + attribute + '"' + (kind || '');
      var out = head + ' values="' + text(track[0][1]) + '" begin="' + begin + '" end="' + finish + '"/>';
      runs(track.filter(function (e) { return e[0] < end; })).forEach(function (run) {
        var t = run[0], apart = run[1], step = run[2], count = run[3], first = stamp(t), at = [], chunk, done, q;
        for (q = 0; q < count; q++) at.push(stamp(t + q * apart) - first);
        chunk = whole(apart) ? Math.max(2, whole(apart)) : 0;
        done = chunk && count >= 2 * chunk ? Math.floor(count / chunk) * chunk : 0;
        if (done) {
          // a chunk of steps repeated, each time on from where the last left off
          out += head + ' additive="sum" accumulate="sum" calcMode="discrete" values="' +
            list(chunk, function (q) { return text(times(step, q + 1)); }) + '" keyTimes="' +
            list(chunk, function (q) { return fraction(at[q], at[chunk]); }) + '" begin="' + when(t) + '" dur="' +
            at[chunk] + 'ms" repeatCount="' + done / chunk + '"/>';
        }
        if (done < count) {
          // the steps after the chunks
          out += held(head, at.slice(done).map(function (p, q) { return text(times(step, done + q + 1)); }),
                      at.slice(done).map(function (p) { return p - at[done]; }), first + at[done]);
        } else {
          // where the chunks leave off
          out += held(head, [text(times(step, count))], [0], first + done / chunk * at[chunk]);
        }
      });
      return out;
    }

    // on for ticks [t0, stop): its display where nothing animates it, and when it is otherwise
    function shown(t0, stop) {
      var visible = t0 <= at && at < stop, out = '';
      (visible ? [[0, t0], [stop, end]] : [[t0, stop]]).forEach(function (span) {
        if (span[0] < span[1] && span[0] < end) {
          out += '<set attributeName="display" to="' + (visible ? 'none' : 'inline') + '" begin="' + when(span[0]) +
            '" end="' + (span[1] >= end ? finish : when(span[1])) + '"/>';
        }
      });
      return [visible ? 'inline' : 'none', out];
    }

    // values[0] until `start`, then values[1 + k] from `k` periods after it, in a chunk of
    // `count` periods that starts on a whole millisecond and repeats until the loop ends
    function chunked(name, attribute, period, count, start, values, kind) {
      var r = g && g < period ? start % g : 0, ticks = r ? [0] : [], q;
      for (q = 0; q < count; q++) ticks.push(r + q * period);
      return '<' + name + ' attributeName="' + attribute + '"' + (kind || '') + ' calcMode="discrete" values="' +
        (r ? values.slice(0, 1) : []).concat(values.slice(1, count + 1)).join(';') + '" keyTimes="' +
        ticks.map(function (t) { return fraction(stamp(t), stamp(count * period)); }).join(';') + '" begin="' +
        begin + '+' + ms((start - r) * tickMs) + 'ms" dur="' + ms(count * period * tickMs) +
        'ms" repeatCount="indefinite" end="' + finish + '"/>';
    }

    scene.layers.forEach(function (l) {
      var n = body.length, stop, life, out, rects, clip, r0, rect, window, mask, closing, start, count, move, c;
      var offsets, part, now, comb, reveal, vals, q;
      if (l.t1 !== null && l.t1 <= l.t0) return;
      stop = l.t1 === null ? end : Math.min(l.t1, end);
      life = l.t0 > 0 || stop < end ? shown(l.t0, stop) : ['inline', ''];
      out = '<g' + (life[0] === 'inline' ? '' : ' display="none"') + '>' + life[1];
      // the window it shows in
      if (l.win !== null) {
        rects = l.win.map(function (w) { return [w[0], intersect(l.clip, w.slice(1))]; });
        clip = steps(rects, rects.length ? rects[0][1] : l.clip);
      } else {
        clip = [[0, l.clip]];
      }
      r0 = base(clip);
      rect = '<rect ' + sides.map(function (s) { return s[0] + '="' + s[1](r0) + '"'; }).join(' ') + '>';
      if (clip.length > 1) {
        sides.forEach(function (s) {
          var track = steps(clip.slice(1).map(function (e) { return [e[0], s[1](e[1])]; }), s[1](clip[0][1]));
          if (track.length > 1) rect += functional('animate', s[0], track);
        });
        window = ' clip-path="url(#' + prefix + 'c' + n + ')"';
        defs.push('<clipPath id="' + prefix + 'c' + n + '">' + rect + '</rect></clipPath>');
      } else if (sameList(clip[0][1], [0, 0, WIDTH, ROWS])) {
        window = '';
      } else {
        if (clips[clip[0][1].join()] === undefined) {
          clips[clip[0][1].join()] = n;
          defs.push('<clipPath id="' + prefix + 'c' + n + '">' + rect + '</rect></clipPath>');
        }
        window = ' clip-path="url(#' + prefix + 'c' + clips[clip[0][1].join()] + ')"';
      }
      mask = '';
      if (l.comb) {
        // a build-up's comb, the columns of each byte it lets through
        part = steps(l.comb.steps.map(function (e) { return [e[0], combAt(l.comb, e[0])]; }), combAt(l.comb, l.t0));
        now = combAt(l.comb, Math.max(at, l.t0));
        comb = '<rect x="' + now[0] + '" width="' + (now[1] - now[0]) + '" height="' + ROWS + '" fill="#fff">';
        [['x', function (v) { return v[0]; }], ['width', function (v) { return v[1] - v[0]; }]].forEach(function (s) {
          var track = steps(part.slice(1).map(function (e) { return [e[0], s[1](e[1])]; }), s[1](part[0][1]));
          if (track.length > 1) comb += functional('animate', s[0], track);
        });
        defs.push('<pattern id="' + prefix + 'k' + n + '" width="8" height="' + ROWS + '" patternUnits="userSpaceOnUse">' +
                  comb + '</rect></pattern><mask id="' + prefix + 'm' + n + '" maskUnits="userSpaceOnUse" x="0" y="0" ' +
                  'width="' + WIDTH + '" height="' + ROWS + '"><rect width="' + WIDTH + '" height="' + ROWS +
                  '" fill="url(#' + prefix + 'k' + n + ')"/></mask>');
        mask = ' mask="url(#' + prefix + 'm' + n + ')"';
      } else if (l.passes) {
        // a twinkle's passes, each letting through the lamps it changes from its tick on
        reveal = '';
        l.passes.forEach(function (pass) {
          var got = shown(pass[0], end);
          reveal += '<path fill="#fff"' + (got[0] === 'inline' ? '' : ' display="none"') + ' d="' + colsOutline(pass[1]) +
            '">' + got[1] + '</path>';
        });
        defs.push('<mask id="' + prefix + 'm' + n + '" maskUnits="userSpaceOnUse" x="0" y="0" width="' + WIDTH +
                  '" height="' + ROWS + '">' + reveal + '</mask>');
        mask = ' mask="url(#' + prefix + 'm' + n + ')"';
      }
      out += '<g' + window + mask + '>';
      closing = '</g></g>';
      if (l.blink) {
        // dark in the second half of each blink, counted from the start of its message
        start = l.blink_from || 0;
        count = 2 * (whole(2 * blinkTicks) || 1);
        vals = ['1'];
        for (q = 0; q < count; q++) vals.push(q & 1 ? '1' : '0');
        out += '<g opacity="' + (at >= start ? Math.floor((at - start) / blinkTicks) & 1 : 0) + '">' +
          chunked('animate', 'opacity', blinkTicks, count, start, vals);
        closing = '</g>' + closing;
      }
      if (l.fill) out += '<path fill="url(#' + prefix + 'off)" d="' + colsOutline(l.fill) + '"/>';
      if (l.dots) {
        if (l.move !== null) {
          move = steps(l.move.map(function (m) { return [m[0], [m[1], m[2]]]; }), [0, 0]);
          r0 = base(move);
          out += '<g' + (r0[0] || r0[1] ? ' transform="translate(' + r0[0] + ' ' + r0[1] + ')"' : '') + '>';
          if (move.length > 1) out += functional('animateTransform', 'transform', move, ' type="translate"');
        }
        if (l.cycle) {
          c = l.cycle;
          offsets = c.offsets.map(function (o) { return o[0] + ' ' + o[1]; });
          count = offsets.length * (whole(offsets.length * c.period) || 1);
          vals = [];
          for (q = 0; q <= count; q++) vals.push(offsets[q % offsets.length]);
          out += '<g transform="translate(' + offsets[0] + ')">' +
            chunked('animateTransform', 'transform', c.period, count, c.from, vals, ' type="translate"');
        }
        if (l.lit) {
          out += scaled + coloured(l.dots.cols, l.dots.hues).map(function (cols, colour) {
            return tiles({x: l.dots.x, y: l.dots.y, cols: cols}, k, blocky).map(function (d) {
              return '<path fill="url(#' + prefix + ON[colour] + ')" d="' + d + '"/>';
            }).join('');
          }).join('') + (scaled ? '</g>' : '');
        } else {
          out += '<path fill="url(#' + prefix + 'off)" d="' + colsOutline(l.dots.cols, l.dots.x, l.dots.y) + '"/>';
        }
        out += (l.cycle ? '</g>' : '') + (l.move !== null ? '</g>' : '');
      }
      body.push(out + closing);
    });

    // what the layers can't show, the frames the program plays show over them, a path each
    // colour they light
    if (scene.spans.length) {
      var played = scene.played, shades = scene.shades || [], shows = [], drawn = ON.concat(SHADES).map(function () { return []; });
      var paths = '';
      var keyed = function (attribute, track) {
        return '<animate attributeName="' + attribute + '" calcMode="discrete" values="' +
          track.map(function (e) { return e[1]; }).join(';') + '" keyTimes="' +
          track.map(function (e) { return fraction(stamp(e[0]), loop); }).join(';') + '" begin="' + begin + '" dur="' +
          loop + 'ms"/>';
      };
      var draw = function (t, frame) {
        var split = coloured(frame[1], frame.slice(2)), none = filled(frame[1].length, 0);
        split = shades.some(function (s) { return s[0] <= t && t < s[1]; }) ? [split[0], none, none, none].concat(split.slice(1)) :
          split.concat(SHADES.map(function () { return none; }));
        split.forEach(function (cols, n) { drawn[n].push([t, lampsPath(cols, k, blocky)]); });
      };
      scene.spans.forEach(function (span) {
        shows.push([span[0], 'inline'], [span[1], 'none']);
        draw(span[0], trackAt(played, span[0]));
        played.forEach(function (f) { if (span[0] < f[0] && f[0] < span[1]) draw(f[0], f); });
      });
      shows = steps(shows.filter(function (e) { return e[0] < end; }), 'none');
      drawn.forEach(function (track, colour) {
        track = steps(track, track[0][1]);
        if (colour && track.length === 1 && track[0][1] === 'M0 0') return;
        paths += '<path fill="url(#' + prefix + ON.concat(SHADES)[colour] + ')" d="' + base(track) + '">' +
          (track.length > 1 ? keyed('d', track) : '') + '</path>';
      });
      frames = '<g display="' + base(shows) + '">' + (shows.length > 1 ? keyed('display', shows) : '') +
        '<rect fill="url(#' + prefix + 'off)" width="' + WIDTH + '" height="' + ROWS + '"/>' + scaled + paths +
        (scaled ? '</g>' : '') + '</g>';
    }

    var shaded = !!(scene.spans.length && scene.shades && scene.shades.length);
    var label = escapeHtml((meta.text || []).join(' / ') || 'Silent Radio');
    var data = JSON.stringify(meta).replace(/</g, '\\u003c').replace(/&/g, '\\u0026');
    var levels = meta.levels ? ' style="' + levelsStyle(meta.levels) + '"' : '';
    return '<svg xmlns="http://www.w3.org/2000/svg" class="silent-radio" viewBox="0 0 ' + WIDTH + ' ' + ROWS +
      '" width="' +
      WIDTH * px + '" height="' + ROWS * px + '"' + levels + ' role="img" aria-label="' + label + '"><title>' + label +
      '</title><metadata id="' + prefix + 'data">' + data + '</metadata><style>' + SVG_STYLE + (shaded ? SVG_SHADES : '') + '</style><defs><pattern id="' +
      prefix + 'off" width="1" height="1" patternUnits="userSpaceOnUse"' +
      (k === 1 ? '' : ' patternTransform="scale(' + 1 / k + ')"') + '><rect class="ground" width="1" height="1"/>' +
      '<path class="dim" d="M.5 .5h0"/></pattern>' + ON.concat(shaded ? SHADES : []).map(function (on) {
        return '<pattern id="' + prefix + on + '" width="1" height="1" patternUnits="userSpaceOnUse"><path class="' + on +
          '" d="M.5 .5h0"/></pattern>';
      }).join('') + defs.join('') + '</defs><rect fill="url(#' +
      prefix + 'off)" width="' + WIDTH + '" height="' + ROWS + '"/><rect width="0" height="0"><set id="' + prefix + 'L" ' +
      'attributeName="x" to="0" begin="0s;' + finish + '" dur="' + loop + 'ms"/></rect><g class="layers">' + body.join('') +
      '</g>' + frames + '</svg>';
  }

  return {build: build, rasterize: rasterize, verify: verify, breakpoints: breakpoints, played: played, trackAt: trackAt,
          offsetAt: offsetAt, cycleStep: cycleStep, combAt: combAt, letThrough: letThrough, rowMask: rowMask,
          rowsOf: rowsOf, place: place, svg: svg, poster: poster, outline: outline, coloured: coloured, samePlanes: samePlanes,
          ON: ON, SHADES: SHADES, SVG_SHADES: SVG_SHADES, SIGN_COLOURS: SIGN_COLOURS, LEVELS: LEVELS, levelsStyle: levelsStyle,
          colsOutline: colsOutline, tiles: tiles, lampsPath: lampsPath, finer: finer, runs: runs, fraction: fraction,
          ms: ms, grid: grid, steps: steps};
})();

if (typeof module !== 'undefined') module.exports = SilentRadioScene;
