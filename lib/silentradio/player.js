// A page that plays Silent Radio messages as the sign showed them: sign.js runs the
// firmware's model on each message's bytes, scene.js turns the run into layers of lamps and
// writes them as an SVG that SMIL animates - the SVG the decoder writes beside the page -
// and here that SVG is held paused and set to each moment of the page's own clock, so it
// plays at any speed and steps a display at a time. Whatever can be changed of the sign
// plays it again. The frames themselves and the GIF are there to compare.
//
// Public domain / Unlicense, as with the rest of ccDecoder.

var SilentRadioPlayer = (function () {
  'use strict';

  var Sign = typeof SilentRadioSign !== 'undefined' ? SilentRadioSign : require('./sign.js');
  var Scene = typeof SilentRadioScene !== 'undefined' ? SilentRadioScene : require('./scene.js');
  var Motion = typeof SilentRadioMotion !== 'undefined' ? SilentRadioMotion : require('./motion.js');
  // the keyed swings' model, which only the pages of messages that have one carry
  var Polar = typeof SilentRadioPolar !== 'undefined' ? SilentRadioPolar :
              typeof require === 'function' ? require('./polar.js') : null;

  // A message's run with its keyed swing in its scene, where it has one and it is wanted; the
  // firmware's own scene and the swing's model beside it, for the 3D view to pose its parts in
  function swung(run, message, wanted) {
    if (wanted && Polar && message.polar) {
      run.plain = run.scene;
      run.swing = Polar.prepare(message.polar, run.scene.played, run.params.tick_ms);
      run.scene = Polar.merge(run.scene, message.polar, run.params.tick_ms, run.params.frame_ticks);
    }
    return run;
  }
  var WIDTH = Sign.WIDTH, ROWS = Sign.ROWS, SVG = 'http://www.w3.org/2000/svg';

  function el(name, attrs, parent, svg) {
    var node = svg ? document.createElementNS(SVG, name) : document.createElement(name), key;
    for (key in attrs || {}) {
      if (key === 'text') node.textContent = attrs[key];
      else node.setAttribute(key, attrs[key]);
    }
    if (parent) parent.appendChild(node);
    return node;
  }

  var smil = typeof document !== 'undefined' && typeof SVGSVGElement !== 'undefined' &&
             typeof SVGSVGElement.prototype.setCurrentTime === 'function';

  // --- one sign: a scene's SVG, set to where the page's clock stands ---

  var made = 0;

  function SmilSign(holder, scene, params, meta, density) {
    this.scene = scene;
    this.params = params;
    this.prefix = 'sr' + ++made + '_';
    holder.insertAdjacentHTML('afterbegin', Scene.svg(scene, meta, params, {
      prefix: this.prefix, density: density ? density.k : 1, smooth: density ? density.smooth : true}));
    this.svg = holder.firstElementChild;
    this.svg.removeAttribute('width');
    this.svg.removeAttribute('height');
    this.svg.pauseAnimations();
  }

  // A tick's steps stand on the millisecond nearest it, so the sign is set 0.4 ms on from there:
  // past them and short of the next tick's, however the browser rounds the time to its
  // milliseconds - Firefox to the nearest; `loops` on from the first, the SVG's loop played
  // again. Firefox answers the first seek to a time with where the animations stood before it,
  // which a sign standing `still` asks again; and Chromium starts an SVG's clock again when the
  // page has loaded, so where it doesn't stand where it was set, it is set again.
  SmilSign.prototype.at = function (tick, still, loops) {
    var tickMs = this.params.tick_ms, t = Math.floor(Math.max(0, Math.min(tick, this.scene.end - 0.5)));
    var seconds = (Math.floor(t * tickMs + 0.5) + 0.4 + (loops || 0) * Math.max(1, Math.ceil(this.scene.end * tickMs))) /
      1000;
    if (seconds === this.seconds && (this.settled || !still) && this.svg.animationsPaused() &&
        Math.abs(this.svg.getCurrentTime() - seconds) < 0.002) return;
    this.seconds = seconds;
    this.settled = !!still;
    this.svg.pauseAnimations();
    this.svg.setCurrentTime(seconds);
    if (still) this.svg.setCurrentTime(seconds);
  };

  SmilSign.prototype.remove = function () {
    if (this.svg.parentNode) this.svg.parentNode.removeChild(this.svg);
  };

  // What the SVG shows where it stands, as the browser has animated it, [lamps, red, green]: the
  // check `#verify` makes of the browser against the frames
  SmilSign.prototype.lamps = function () {
    var self = this, groups = this.svg.querySelector('.layers').children, planes = [[], [], []], n = 0, x;
    for (x = 0; x < WIDTH; x++) planes.forEach(function (plane) { plane.push(0); });
    function child(node) {
      for (var c = node.firstElementChild; c; c = c.nextElementSibling) if (c.tagName === 'g') return c;
      return null;
    }
    // a group's translation, as animated
    function shift(node) {
      var list = node.transform.animVal, e = 0, f = 0, q;
      for (q = 0; q < list.numberOfItems; q++) {
        e += list.getItem(q).matrix.e;
        f += list.getItem(q).matrix.f;
      }
      return [Math.round(e), Math.round(f)];
    }
    function target(node, attribute) {
      var m = /url\(#([^)]+)\)/.exec(node.getAttribute(attribute) || '');
      return m ? self.svg.getElementById(m[1]) : null;
    }
    this.scene.layers.forEach(function (l) {
      var g, inner, clip, rect, v, mask, comb, reveal, rows, off, i, m, got, pass, q, hues = l.dots && l.lit ? l.dots.hues : null;
      if (l.t1 !== null && l.t1 <= l.t0) return;
      g = groups[n++];
      if (getComputedStyle(g).display === 'none') return;
      inner = child(g);
      v = [0, 0, WIDTH, ROWS];
      clip = target(inner, 'clip-path');
      if (clip) {
        rect = clip.firstElementChild;
        v = [Math.round(rect.x.animVal.value), Math.round(rect.y.animVal.value)];
        v.push(v[0] + Math.round(rect.width.animVal.value), v[1] + Math.round(rect.height.animVal.value));
      }
      mask = target(inner, 'mask');
      if (mask && l.comb) {
        rect = target(mask.firstElementChild, 'fill').firstElementChild;
        comb = [Math.round(rect.x.animVal.value)];
        comb.push(comb[0] + Math.round(rect.width.animVal.value));
      } else if (mask) {
        reveal = [];
        for (x = 0; x < WIDTH; x++) reveal.push(0);
        for (pass = mask.firstElementChild, i = 0; pass; pass = pass.nextElementSibling, i++) {
          if (getComputedStyle(pass).display === 'none') continue;
          for (x = 0; x < WIDTH; x++) reveal[x] |= l.passes[i][1][x];
        }
      }
      if (v[2] <= v[0] || v[3] <= v[1]) return;
      if (l.blink) {
        inner = child(inner);
        if (getComputedStyle(inner).opacity !== '1') return;
      }
      rows = Scene.rowMask(v[1], v[3]);
      off = [0, 0];
      if (l.dots && l.move !== null) {
        inner = child(inner);
        off = shift(inner);
      }
      if (l.dots && l.cycle) {
        got = shift(child(inner));
        off = [off[0] + got[0], off[1] + got[1]];
      }
      for (x = v[0]; x < v[2]; x++) {
        m = comb ? (comb[0] <= (x & 7) && (x & 7) < comb[1] ? rows : 0) : reveal ? reveal[x] & rows : rows;
        if (!m) continue;
        if (l.fill) for (q = 0; q < 3; q++) planes[q][x] &= ~(l.fill[x] & m);
        if (!l.dots) continue;
        i = x - l.dots.x - off[0];
        if (i < 0 || i >= l.dots.cols.length || !l.dots.cols[i]) continue;
        got = Scene.place(l.dots.cols[i], l.dots.y + off[1]) & m;
        for (q = 0; q < 3; q++) planes[q][x] &= ~got;
        if (!l.lit) continue;
        planes[0][x] |= got;
        if (hues) for (q = 0; q < 2; q++) planes[q + 1][x] |= Scene.place(hues[q][i], l.dots.y + off[1]) & m;
      }
    });
    return planes;
  };

  // The frames the program plays, drawn one at a time: to compare, and where SMIL can't
  var framed = 0;

  function FrameSign(holder, scene, density, levels) {
    var defs, off, group, id = 'srf' + ++framed + '_';
    this.scene = scene;
    this.density = density || {k: 1, smooth: true};
    this.svg = el('svg', {viewBox: '0 0 ' + WIDTH + ' ' + ROWS, 'class': 'silent-radio frames'}, null, true);
    if (levels) this.svg.setAttribute('style', Scene.levelsStyle(levels));
    defs = el('defs', {}, this.svg, true);
    off = el('pattern', {id: id + 'off', width: 1, height: 1, patternUnits: 'userSpaceOnUse'}, defs, true);
    if (this.density.k > 1) off.setAttribute('patternTransform', 'scale(' + 1 / this.density.k + ')');
    el('rect', {'class': 'ground', width: 1, height: 1}, off, true);
    el('path', {'class': 'dim', d: 'M.5 .5h0'}, off, true);
    Scene.ON.concat(Scene.SHADES).forEach(function (on) {
      el('path', {'class': on, d: 'M.5 .5h0'}, el('pattern', {id: id + on, width: 1, height: 1, patternUnits: 'userSpaceOnUse'},
                                                    defs, true), true);
    });
    el('rect', {fill: 'url(#' + id + 'off)', width: WIDTH, height: ROWS}, this.svg, true);
    group = this.density.k > 1 ? el('g', {transform: 'scale(' + 1 / this.density.k + ')'}, this.svg, true) : this.svg;
    // a path each colour, and each level of a keyed swing's brightness
    this.paths = Scene.ON.concat(Scene.SHADES).map(function (on) { return el('path', {fill: 'url(#' + id + on + ')'}, group, true); });
    this.shown = -1;
    holder.insertBefore(this.svg, holder.firstChild);
  }

  FrameSign.prototype.at = function (tick) {
    var self = this, n = frameAt(this.scene.played, tick), frame, split, none;
    if (n !== this.shown) {
      frame = this.scene.played[n];
      split = Scene.coloured(frame[1], frame.slice(2));
      none = frame[1].map(function () { return 0; });
      split = (this.scene.shades || []).some(function (s) { return s[0] <= frame[0] && frame[0] < s[1]; }) ?
        [split[0], none, none, none].concat(split.slice(1)) : split.concat([none, none, none]);
      split.forEach(function (cols, k) {
        self.paths[k].setAttribute('d', Scene.lampsPath(cols, self.density.k, !self.density.smooth));
      });
      this.shown = n;
    }
  };

  FrameSign.prototype.remove = SmilSign.prototype.remove;

  function frameAt(played, tick) {
    var lo = 0, hi = played.length - 1, mid;
    while (lo < hi) {
      mid = (lo + hi + 1) >> 1;
      if (played[mid][0] <= tick) lo = mid;
      else hi = mid - 1;
    }
    return lo;
  }

  // A story's run of the model and its layers, as a sign plays it with `params`, or without
  // its flashing with `steady`
  function runScene(payload, params, tables, steady) {
    var got, scene;
    if (steady) {
      params = copy(params);
      params.blink_ticks = 1e9;
    }
    got = Sign.play(payload, params, tables, {ops: true, limit: 300 * 781});
    scene = Scene.build(got, params, tables);
    Scene.verify(scene, params.blink_ticks);
    return {scene: scene, params: params, ops: got.ops};
  }

  // What a message's SVG holds of it, as the decoder writes it
  function metaOf(message, params) {
    var meta = {format: 'silentradio/1', record: message.record, when: message.when, text: message.text,
                payload: message.payload};
    if (message.levels) meta.levels = message.levels;
    meta.params = params;
    return meta;
  }

  // --- the page: a sign, how it plays, and what can be changed of it ---

  // The effects a setting can pick, by the names the model gives them
  var EFFECTS = [[0x20, 'show'], [0x21, 'build up'], [0x22, 'border on'], [0x23, 'border off'],
                 [0x24, 'roll down'], [0x25, 'roll up'], [0x26, 'twinkle'], [0x27, 'unveil from the middle'],
                 [0x28, 'wipe from the left'], [0x29, 'wipe from the right'], [0x2a, 'reveal rows from the top'],
                 [0x2b, 'reveal rows from the bottom'], [0x2c, 'start the clock'], [0x2f, 'flashing on'],
                 [0x30, 'flashing off'], [0x31, 'logo'], [0x3f, 'pad the line'], [0x40, 'blank'], [0x41, 'squeeze'],
                 [0x42, 'a character'], [0x44, 'clear the border'], [0x46, 'roll down'], [0x47, 'roll up']];
  var FONT_FLAGS = [[0x80, 'inverse'], [0x40, 'double width'], [0x20, 'flashing'], [0x10, 'descenders'],
                    [0x08, 'drawn off the sign, then shown by an effect (else it travels)']];

  // The model's parameters, as the panels list them: key, label, and a label for each of a
  // tuple's values
  var PANELS = [
    ['Words', [['travel_ticks', 'Ticks a travelling line takes to move a column'],
               ['character_ticks', 'Drawing a character off the sign, ticks'],
               ['column_ticks', 'Drawing a column, ticks', ['each', 'a row', 'a row per byte']],
               ['clear_ticks', 'Clearing the window, ticks', ['each', 'a row', 'a row per byte']],
               ['command_ticks', 'A command byte, ticks'],
               ['filler_ticks', 'A 1E, the filler after an effect, ticks'],
               ['work_ticks', 'Slices the drawing takes the processor in, ticks'],
               ['shares', 'Share of the processor while the other line draws', ['top line', 'bottom line']]]],
    ['Sign timing', [['tick_ms', 'The timer tick, ms'],
                     ['frame_ticks', 'A display frame, ticks'],
                     ['blink_ticks', 'Flashing lamps go dark every other, ticks'],
                     ['border_ticks', 'The border moves every, ticks'],
                     ['pause_ticks', '0E pauses for, ticks']]],
    ['Effect paces', [['show_ticks', '20 show: between its three bands'],
                      ['build_ticks', '21 build up: a step', ['ticks', 'and for each row', 'over']],
                      ['roll_ticks', 'Rolls and squeeze: a step', ['ticks', 'and one for each lamps']],
                      ['twinkle_ticks', '26 twinkle: a pass', ['three of five', 'the other two']],
                      ['unveil_ticks', '27 unveil: a step'],
                      ['wipe_ticks', '28, 29 wipes: a step', ['over more than 8 rows', 'up to 8 rows']],
                      ['reveal_ticks', '2A, 2B reveals: a row']]]];
  // The colours that can be changed, as the SVG's variables name them
  var LOOKS = [['sign', 'Sign'], ['lamp-off', 'Unlit lamps'], ['lamp-on', 'Lit, in no colour'], ['lamp-red', 'Lit red'],
               ['lamp-green', 'Lit green'], ['lamp-yellow', 'Lit yellow']].map(function (look) {
    return look.concat(Scene.SIGN_COLOURS.filter(function (pair) { return pair[0] === look[0]; })[0][1]);
  });
  // Parameters in fractions of a tick; the rest are whole ticks. Those that divide, or that
  // time the steps of a loop, can't go below one
  var FRACTIONAL = {tick_ms: true, character_ticks: true, column_ticks: true, clear_ticks: true, shares: true};
  var AT_LEAST_ONE = {frame_ticks: [0], blink_ticks: [0], border_ticks: [0], work_ticks: [0], build_ticks: [2],
                      roll_ticks: [1]};

  // How many lamps a player draws to each of the sign's, each way; as many again make a long
  // travelling line heavy for the browser past this
  var MOST_LAMPS = 6;

  function densityOf(looks) {
    return {k: Math.max(1, Math.min(MOST_LAMPS, Math.round(+looks.density) || 1)), smooth: looks.smooth !== '0'};
  }

  function hex2(n) {
    return (n < 16 ? '0' : '') + n.toString(16);
  }

  function clockLabel(ms) {
    var s = Math.max(0, ms) / 1000;
    return Math.floor(s / 60) + ':' + (s % 60 < 10 ? '0' : '') + (s % 60).toFixed(2);
  }

  // The presentation codes a message's elements name
  function codesOf(payload) {
    var codes = {}, i = 0, stop;
    while (i + 1 < payload.length && payload[i] === 0) {
      if (payload[i + 1] === 0x0c) {
        i += 4 + (payload[i + 2] | payload[i + 3] << 8);
        continue;
      }
      codes[payload[i + 1]] = true;
      stop = payload.indexOf(0x1f, i + 2);
      i = stop < 0 ? payload.length : stop + 1;
    }
    return codes;
  }

  function copy(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function readHash() {
    var out = {};
    location.hash.slice(1).split('&').forEach(function (part) {
      var at = part.indexOf('=');
      if (part) out[decodeURIComponent(at < 0 ? part : part.slice(0, at))] = at < 0 ? '' : decodeURIComponent(part.slice(at + 1));
    });
    return out;
  }

  // Where the sign stands, in its ticks: `speed` times as fast as a tick of `tickMs`
  function Clock() {
    this.tick = 0;
    this.speed = 1;
    this.tickMs = 1.28;
    this.playing = false;
    this.start = 0;
  }

  Clock.prototype.rate = function () {
    return this.speed / this.tickMs;
  };

  Clock.prototype.now = function () {
    return this.playing ? (performance.now() - this.start) * this.rate() : this.tick;
  };

  Clock.prototype.set = function (tick, playing) {
    this.tick = tick;
    this.playing = playing;
    this.start = performance.now() - tick / this.rate();
  };

  // How the 3D view stands: its settings, the camera, and each message's vectors once worked out
  function motionState() {
    return {settings: Motion.defaults(), yaw: 35, pitch: 18, zoom: 1, target: null, open: false, vectors: {}, paths: {},
            partPaths: {}};
  }

  // One page's messages: a message's own, or a channel's in turn
  function Player(root, data) {
    var self = this, hash = readHash();
    this.root = root;
    this.data = data;
    this.defaults = data.params;
    this.params = copy(data.params);
    this.looks = {};
    this.messages = data.messages;
    this.index = 0;
    this.clock = new Clock();
    this.cache = {};
    this.renderer = smil ? 'vector' : 'frames';
    this.swing = true;
    this.steady = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
    Object.keys(this.defaults).forEach(function (key) {
      if (hash[key] === undefined) return;
      var value = key === 'settings' ? hash[key] : hash[key].split(',').map(Number);
      self.params[key] = key === 'settings' || Array.isArray(self.defaults[key]) ? value : value[0];
    });
    LOOKS.concat([['lamp'], ['cap'], ['glow'], ['density'], ['smooth'], ['levels'], ['mono']]).forEach(function (look) {
      if (hash[look[0]] !== undefined) self.looks[look[0]] = hash[look[0]];
    });
    this.density = densityOf(this.looks);
    if (hash.r && (hash.r !== 'vector' || smil)) this.renderer = hash.r;
    if (hash.steady !== undefined) this.steady = hash.steady === '1';
    if (hash.swing === '0') this.swing = false;
    this.clock.tickMs = this.params.tick_ms;
    this.motion = motionState();
    this.build();
    this.load(Math.min(+hash.m || 0, this.messages.length - 1));
    if (hash.t) this.seek(+hash.t * 1000 / this.params.tick_ms);
    else if (this.steady) this.seek(this.longestHeld());
    else if (!('paused' in hash)) this.play();
    this.applyLooks();
    requestAnimationFrame(function step() {
      self.frame();
      requestAnimationFrame(step);
    });
  }

  Player.prototype.build = function () {
    var self = this, root = this.root, stage, bar, speeds, panels, row, list;
    stage = this.stage = root.querySelector('.stage');
    // the decoder's own SVG plays until the page's takes its place
    [].forEach.call(stage.querySelectorAll('svg, img'), function (svg) { svg.parentNode.removeChild(svg); });

    bar = el('div', {'class': 'transport'}, stage.parentNode);
    stage.parentNode.insertBefore(bar, stage.nextSibling);
    this.playButton = el('button', {type: 'button', 'class': 'play', 'aria-label': 'Play'}, bar);
    this.playButton.addEventListener('click', function () { self.toggle(); });
    el('button', {type: 'button', text: '←', 'aria-label': 'The display before', title: 'The display before (←)'}, bar)
      .addEventListener('click', function () { self.step(-1); });
    el('button', {type: 'button', text: '→', 'aria-label': 'The next display', title: 'The next display (→)'}, bar)
      .addEventListener('click', function () { self.step(1); });
    this.slider = el('input', {type: 'range', min: 0, max: 1, step: 1, value: 0, 'aria-label': 'Where the sign is'}, bar);
    this.slider.addEventListener('input', function () { self.seekOverall(+self.slider.value); });
    this.timeLabel = el('span', {'class': 'time'}, bar);
    row = el('label', {text: 'Speed '}, bar);
    speeds = el('select', {}, row);
    [0.25, 0.5, 1, 2, 4].forEach(function (speed) {
      el('option', {value: speed, text: speed + '×'}, speeds).selected = speed === 1;
    });
    speeds.addEventListener('change', function () {
      var tick = self.clock.now();
      self.clock.speed = +speeds.value;
      self.at(tick, self.clock.playing);
    });
    row = el('label', {}, bar);
    this.loop = el('input', {type: 'checkbox'}, row);
    this.loop.checked = true;
    row.appendChild(document.createTextNode(' Loop'));

    row = el('div', {'class': 'status'}, bar.parentNode);
    bar.parentNode.insertBefore(row, bar.nextSibling);
    el('span', {text: 'Drawn as '}, row);
    [['vector', 'its SVG'], ['frames', 'its frames']].forEach(function (mode) {
      var label = el('label', {}, row), input = el('input', {type: 'radio', name: 'renderer', value: mode[0]}, label);
      label.appendChild(document.createTextNode(' ' + mode[1] + ' '));
      input.checked = self.renderer === mode[0];
      input.disabled = mode[0] === 'vector' && !smil;
      input.addEventListener('change', function () { self.setRenderer(mode[0]); });
    });
    if (Polar && this.messages.some(function (m) { return m.polar; })) {
      var swingLabel = el('label', {}, row);
      this.swingBox = el('input', {type: 'checkbox'}, swingLabel);
      this.swingBox.checked = this.swing;
      swingLabel.appendChild(document.createTextNode(' Its keyed swing '));
      this.swingBox.addEventListener('change', function () {
        var tick = self.clock.now(), playing = self.clock.playing;
        self.swing = self.swingBox.checked;
        self.load(self.index, tick);
        self.at(tick, playing);
        self.saveHash();
      });
    }
    row = el('label', {}, row);
    this.steadyBox = el('input', {type: 'checkbox'}, row);
    this.steadyBox.checked = this.steady;
    row.appendChild(document.createTextNode(' Steady flashing'));
    this.steadyBox.addEventListener('change', function () {
      self.steady = self.steadyBox.checked;
      self.rerun();
    });
    this.note = el('span', {'class': 'note', 'aria-live': 'polite'}, row.parentNode);

    if (this.messages.length > 1) {
      list = el('ol', {'class': 'playlist'}, this.root);
      this.root.insertBefore(list, this.root.querySelector('.sent'));
      this.items = this.messages.map(function (message, n) {
        var item = el('li', {}, list), button = el('button', {type: 'button'}, item);
        button.textContent = message.when + ' · ' + message.record + ' · ' + (message.text[0] || '');
        button.addEventListener('click', function () {
          self.load(n);
          self.play();
        });
        return button;
      });
    }

    panels = el('div', {'class': 'panels'}, this.root);
    this.root.insertBefore(panels, this.root.querySelector('.downloads'));
    this.motionPanel(el('details', {'class': 'motion'}, panels));
    this.looksPanel(el('details', {}, panels));
    PANELS.forEach(function (panel) { self.paramsPanel(el('details', {}, panels), panel[0], panel[1]); });
    this.settingsPanel(el('details', {}, panels));
    row = el('p', {'class': 'actions'}, panels);
    el('button', {type: 'button', text: 'Reset all'}, row).addEventListener('click', function () { self.reset(); });
    el('button', {type: 'button', text: 'Copy link'}, row).addEventListener('click', function () { self.copyLink(); });
    el('button', {type: 'button', text: 'Save this SVG'}, row).addEventListener('click', function () { self.saveSvg(); });

    this.root.addEventListener('keydown', function (e) { self.key(e); });
    this.root.tabIndex = -1;
  };

  // --- what plays ---

  Player.prototype.run = function (n) {
    var key = n + (this.steady ? 's' : '') + (this.swing ? 'w' : '');
    if (!this.cache[key]) {
      this.cache[key] = swung(runScene(this.messages[n].payload, this.params, this.data.tables, this.steady),
                              this.messages[n], this.swing);
      this.messages[n].ticks = this.cache[key].scene.end;
    }
    return this.cache[key];
  };

  Player.prototype.load = function (n, tick) {
    var run = this.run(n), message = this.messages[n];
    this.index = n;
    if (this.sign) this.sign.remove();
    this.sign = null;
    this.scene = run.scene;
    if (this.renderer === 'vector') {
      try {
        this.sign = new SmilSign(this.stage, run.scene, run.params, metaOf(message, run.params), this.density);
      } catch (e) {
        this.renderer = 'frames';
      }
    }
    if (!this.sign) this.sign = new FrameSign(this.stage, run.scene, this.density, message.levels);
    this.applyLooks();
    this.note.textContent = run.scene.layers.length ?
      run.scene.layers.length + ' layers' + (run.scene.spans.length ? ', ' + run.scene.spans.length + ' spans drawn as frames' : '') :
      'drawn as frames: its lines run out of step';
    (this.items || []).forEach(function (item, q) {
      if (q === n) item.setAttribute('aria-current', 'true');
      else item.removeAttribute('aria-current');
    });
    this.root.querySelectorAll('[data-message]').forEach(function (node) { node.hidden = +node.getAttribute('data-message') !== n; });
    this.slider.max = this.totalTicks();
    this.at(tick || 0, this.clock.playing);
    this.showSettings();
    // a channel's next message, worked out while the page is idle
    if (n + 1 < this.messages.length && typeof requestIdleCallback === 'function') {
      var self = this;
      requestIdleCallback(function () { self.run(n + 1); });
    }
  };

  Player.prototype.totalTicks = function () {
    return this.messages.reduce(function (sum, m) { return sum + m.ticks; }, 0);
  };

  Player.prototype.before = function (n) {
    var sum = 0;
    for (var q = 0; q < n; q++) sum += this.messages[q].ticks;
    return sum;
  };

  Player.prototype.at = function (tick, playing) {
    this.clock.set(tick, playing);
    this.playButton.textContent = playing ? '❚❚' : '▶';
    this.playButton.setAttribute('aria-label', playing ? 'Pause' : 'Play');
    this.frame();
  };

  Player.prototype.play = function () {
    var tick = this.clock.now();
    this.at(tick >= this.scene.end ? 0 : tick, true);
  };

  Player.prototype.pause = function () {
    this.at(this.clock.now(), false);
  };

  Player.prototype.toggle = function () {
    if (this.clock.playing) this.pause();
    else this.play();
  };

  Player.prototype.seek = function (tick) {
    this.at(Math.max(0, Math.min(tick, this.scene.end)), this.clock.playing);
  };

  Player.prototype.seekOverall = function (tick) {
    var n = 0;
    while (n + 1 < this.messages.length && tick >= this.before(n + 1)) n++;
    if (n !== this.index) this.load(n, tick - this.before(n));
    else this.seek(tick - this.before(n));
  };

  // A display change before or after where the sign stands
  Player.prototype.step = function (way) {
    var played = this.scene.played, n = frameAt(played, this.clock.now());
    n = Math.max(0, Math.min(played.length - 1, n + way));
    this.at(played[n][0], false);
  };

  Player.prototype.longestHeld = function () {
    return Scene.poster(this.scene);
  };

  Player.prototype.frame = function () {
    var tick = this.clock.now(), scene = this.scene;
    if (tick >= scene.end && this.clock.playing) {
      if (this.index + 1 < this.messages.length) {
        this.load(this.index + 1, 0);
      } else if (this.loop.checked) {
        if (this.index) this.load(0, 0);
        else this.at(0, true);
      } else {
        this.at(scene.end, false);
      }
      return;
    }
    tick = Math.min(tick, scene.end);
    if (this.sign) this.sign.at(tick, !this.clock.playing);
    if (this.motion.open) this.drawMotion(tick);
    this.slider.value = this.before(this.index) + tick;
    this.slider.setAttribute('aria-valuetext', clockLabel(tick * this.params.tick_ms));
    this.timeLabel.textContent = clockLabel(tick * this.params.tick_ms) + ' / ' + clockLabel(scene.end * this.params.tick_ms);
  };

  Player.prototype.setRenderer = function (mode) {
    var tick = this.clock.now(), playing = this.clock.playing;
    this.renderer = mode;
    this.load(this.index, tick);
    this.at(tick, playing);
    this.saveHash();
  };

  // A change of the model's parameters plays every message afresh
  Player.prototype.rerun = function () {
    var self = this;
    clearTimeout(this.pending);
    this.pending = setTimeout(function () {
      var tick = self.clock.now(), playing = self.clock.playing;
      self.cache = {};
      self.motion.vectors = {};
      self.motion.paths = {};
      self.clock.tickMs = self.params.tick_ms;
      self.load(self.index, 0);
      self.at(Math.min(tick, self.scene.end), playing);
      self.saveHash();
    }, 150);
  };

  Player.prototype.key = function (e) {
    var tag = e.target.tagName;
    if (tag === 'INPUT' && e.target.type !== 'range' && e.target.type !== 'checkbox' && e.target.type !== 'radio' || tag === 'SELECT' || tag === 'TEXTAREA') return;
    if (e.key === ' ' && tag !== 'BUTTON') this.toggle();
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      if (tag === 'INPUT' && e.target.type === 'range') return;
      if (e.shiftKey) this.seek(this.clock.now() + (e.key === 'ArrowLeft' ? -5000 : 5000) / this.params.tick_ms);
      else this.step(e.key === 'ArrowLeft' ? -1 : 1);
    } else if (e.key === 'PageUp' || e.key === 'PageDown') {
      if (this.messages.length < 2) return;
      this.load((this.index + (e.key === 'PageUp' ? -1 : 1) + this.messages.length) % this.messages.length, 0);
    } else {
      return;
    }
    e.preventDefault();
  };

  // The SVG of what plays, as it plays with what has been changed, to keep
  Player.prototype.saveSvg = function () {
    var run = this.run(this.index), message = this.messages[this.index];
    var text = Scene.svg(run.scene, metaOf(message, run.params), run.params, {density: this.density.k, smooth: this.density.smooth});
    var link = el('a', {download: 'silentradio.' + message.record + '.svg',
                        href: URL.createObjectURL(new Blob([text], {type: 'image/svg+xml'}))}, document.body);
    link.click();
    link.parentNode.removeChild(link);
    setTimeout(function () { URL.revokeObjectURL(link.href); }, 1000);
  };

  // --- what can be changed ---

  // How bright each colour is lit, as CSS values, or null for each at its brightest: as the
  // byte in `looks.levels` sets it where one is given, and otherwise as the message's own signs
  // were sent it; all at their brightest in one colour
  Player.prototype.levels = function () {
    var byte = this.looks.mono === '1' ? 'ff' : this.looks.levels || this.messages[this.index].levels;
    return byte ? Scene.LEVELS.map(function (level, n) { return Scene.LEVELS[parseInt(byte, 16) >> 2 * n & 3]; }) : null;
  };

  // What is changed goes on the page and on each sign itself
  Player.prototype.applyLooks = function () {
    var looks = this.looks, styles = [document.documentElement.style], svgs = [], levels = this.levels();
    this.root.querySelectorAll('svg.silent-radio').forEach(function (svg) {
      styles.push(svg.style);
      svgs.push(svg);
    });
    styles.forEach(function (style) {
      LOOKS.forEach(function (look) {
        if (looks.mono === '1' && /^lamp-(red|green|yellow)$/.test(look[0])) style.setProperty('--' + look[0], 'var(--lamp-on)');
        else if (looks[look[0]]) style.setProperty('--' + look[0], '#' + looks[look[0]]);
        else style.removeProperty('--' + look[0]);
      });
      if (looks.lamp) style.setProperty('--lamp', looks.lamp);
      else style.removeProperty('--lamp');
      if (looks.cap) style.setProperty('--cap', looks.cap);
      else style.removeProperty('--cap');
    });
    svgs.forEach(function (svg) {
      Scene.LEVELS.forEach(function (level, n) {
        if (levels) svg.style.setProperty('--level-' + n, levels[n]);
        else svg.style.removeProperty('--level-' + n);
      });
    });
    this.root.classList.toggle('glow', looks.glow === '1');
  };

  Player.prototype.looksPanel = function (details) {
    var self = this, grid, input, label, field, lamps, smooth, levels, mono;
    el('summary', {text: 'Colours and lamps'}, details);
    el('p', {'class': 'hint', text: 'Each lamp is lit in the colour its line\'s font byte names in bits 0 and 1: none, red, ' +
                                     'green, or yellow for both. Each colour\'s brightness is two bits of the 01 02 byte ' +
                                     'the channel\'s signs were sent, from bit 0: 0 dimmest, 3 brightest.'}, details);
    grid = el('div', {'class': 'grid'}, details);
    LOOKS.forEach(function (look) {
      label = el('label', {text: look[1] + ' '}, grid);
      input = el('input', {type: 'color', value: self.looks[look[0]] ? '#' + self.looks[look[0]] : look[2]}, label);
      input.addEventListener('input', function (e) {
        self.looks[look[0]] = e.target.value.slice(1);
        self.applyLooks();
        self.saveHash();
      });
    });
    // how bright each colour is: as a byte a sign could be sent, FF each at its brightest
    label = el('label', {text: 'Brightness, as a sign sent 01 02 '}, grid);
    levels = el('input', {type: 'text', size: 2, 'class': 'hex', value: this.looks.levels || '',
                          placeholder: this.messages[this.index].levels || 'ff',
                          'aria-label': 'Brightness, as a sign sent 01 02 and this byte, in hex'}, label);
    levels.addEventListener('change', function () {
      if (levels.value && !/^[0-9a-fA-F]{1,2}$/.test(levels.value)) {
        levels.setAttribute('aria-invalid', 'true');
        return;
      }
      levels.removeAttribute('aria-invalid');
      self.looks.levels = levels.value.toLowerCase();
      self.applyLooks();
      self.saveHash();
    });
    label = el('label', {}, grid);
    mono = el('input', {type: 'checkbox'}, label);
    mono.checked = this.looks.mono === '1';
    label.appendChild(document.createTextNode(' One colour, as this firmware lights its lamps'));
    mono.addEventListener('change', function () {
      self.looks.mono = mono.checked ? '1' : '';
      self.applyLooks();
      self.saveHash();
    });
    label = el('label', {text: 'Lamp size '}, grid);
    input = el('input', {type: 'range', min: 0.3, max: 1, step: 0.01, value: this.looks.lamp || 0.78}, label);
    input.addEventListener('input', function (e) {
      self.looks.lamp = e.target.value;
      self.applyLooks();
      self.saveHash();
    });
    label = el('label', {text: 'Lamp shape '}, grid);
    input = el('select', {}, label);
    [['round', 'round'], ['square', 'square']].forEach(function (shape) {
      el('option', {value: shape[0], text: shape[1]}, input).selected = (self.looks.cap || 'round') === shape[0];
    });
    input.addEventListener('change', function (e) {
      self.looks.cap = e.target.value;
      self.applyLooks();
      self.saveHash();
    });
    label = el('label', {}, grid);
    input = el('input', {type: 'checkbox'}, label);
    input.checked = this.looks.glow === '1';
    label.appendChild(document.createTextNode(' Glow'));
    input.addEventListener('change', function (e) {
      self.looks.glow = e.target.checked ? '1' : '';
      self.applyLooks();
      self.saveHash();
    });
    // more lamps to each of the sign's, drawn from the same vectors
    field = el('fieldset', {}, grid);
    el('legend', {text: 'Lamps to each of the sign\'s, each way'}, field);
    label = el('label', {}, field);
    lamps = el('input', {type: 'number', min: 1, max: MOST_LAMPS, step: 1, value: this.density.k,
                         'aria-label': 'Lamps to each of the sign\'s, each way'}, label);
    label = el('label', {}, field);
    smooth = el('input', {type: 'checkbox'}, label);
    smooth.checked = this.density.smooth;
    label.appendChild(document.createTextNode(' smoothed (Lanczos), else in blocks'));
    [lamps, smooth].forEach(function (control) {
      control.addEventListener('change', function () {
        self.looks.density = lamps.value === '1' ? '' : lamps.value;
        self.looks.smooth = smooth.checked ? '' : '0';
        self.setDensity(densityOf(self.looks));
      });
    });
  };

  // --- the 3D view: each line's polar control bits as a signal that moves it ---

  Player.prototype.motionPanel = function (details) {
    var self = this, m = this.motion, grid, label, input, canvas, dragging = null;
    el('summary', {text: '3D view: the polar control bits as a signal'}, details);
    el('p', {'class': 'hint', text: 'Each line\'s polar control bits read as signals over the whole message, modulated ' +
                                     'controls for how its coordinates change over time, like a gyroscope turned and carried ' +
                                     'through space. A bit driving a position (x right, y down, z toward you) or a turn is its ' +
                                     'acceleration: while 1 it speeds its coordinate up its way, while 0 it pushes it back, the ' +
                                     'two adding up over time, so the bias speeds a line up or slows it down as it started. Bits 0 ' +
                                     'and 1 are the colour, red and green, and move nothing; by default bit 2 drives +z and bits 4 ' +
                                     'and 5 turns about x and y, bit 3 is an indicator and bits 6 and 7 drive nothing; a bit can ' +
                                     'instead drive the line\'s time, which then runs only while the bit is 1. The firmware moves nothing this ' +
                                     'way. Each line\'s text and graphics are drawn where the signals take it, larger as they come ' +
                                     'toward you and faded out the farther back they go behind the sign\'s plane, with the path ' +
                                     'its middle takes; under the view, all eight of each line\'s control bits and its ' +
                                     'coordinates over the whole message. A keyed swing\'s parts are drawn where their poses take ' +
                                     'them, the path each one\'s pivot takes in yellow; the sign above sees each part from straight ' +
                                     'in front of it, so from the front here a part coming toward you also moves out from the ' +
                                     'middle. Drag to turn the view; scroll to zoom.'}, details);
    canvas = this.motionCanvas = el('canvas', {width: 896, height: 400, 'class': 'motion-view',
                                               'aria-label': 'The sign in 3D, each line moved by its polar control bits'}, details);
    this.motionChart = el('canvas', {width: 896, height: 380, 'class': 'motion-chart',
                                     'aria-label': 'Each line\'s signals over the whole message, and what they drive'}, details);
    grid = el('div', {'class': 'grid'}, details);
    function changed() {
      m.vectors = {};
      m.paths = {};
      self.drawMotion(self.clock.now());
    }
    function control(text, key, min, max, step, into) {
      label = el('label', {text: text + ' '}, grid);
      input = el('input', {type: 'range', min: min, max: max, step: step, value: (into || m)[key]}, label);
      input.addEventListener('input', function (e) {
        (into || m)[key] = +e.target.value;
        if (into) changed();
        else self.drawMotion(self.clock.now());
      });
      return input;
    }
    m.yawInput = control('Turn the view', 'yaw', -180, 180, 1);
    m.pitchInput = control('Tilt the view', 'pitch', -89, 89, 1);
    m.zoomInput = control('Zoom', 'zoom', 0.01, 3, 0.01);
    label = el('span', {'class': 'fit'}, grid);
    el('button', {type: 'button', text: 'Fit the paths'}, label).addEventListener('click', function () {
      self.drawMotion(self.clock.now());
      var got = Motion.fit((m.paths[self.index] || []).concat(m.partPaths[self.index] || []));
      m.target = got.target;
      m.zoom = got.zoom;
      m.zoomInput.value = m.zoom;
      self.drawMotion(self.clock.now());
    });
    el('button', {type: 'button', text: 'Back to the sign'}, label).addEventListener('click', function () {
      m.target = null;
      m.zoom = 1;
      m.zoomInput.value = 1;
      self.drawMotion(self.clock.now());
    });
    control('Depth behind the sign at which lamps have faded out (lamps)', 'depth', 10, 800, 5, m.settings);
    control('Smoothing over the whole message (s)', 'smooth', 0, 10, 0.1, m.settings);
    control('Position: acceleration (lamps/s²)', 'accel', 1, 400, 1, m.settings);
    control('Position: top speed (lamps/s)', 'top', 1, 200, 1, m.settings);
    control('Position: starting speed, its bit\'s way (lamps/s)', 'start', -200, 200, 1, m.settings);
    control('Turn: acceleration (°/s²)', 'turnAccel', 10, 3000, 10, m.settings);
    control('Turn: top rate (°/s)', 'turnTop', 10, 1440, 10, m.settings);
    control('Turn: starting rate, its bit\'s way (°/s)', 'turnStart', -1440, 1440, 10, m.settings);
    m.settings.dirs.forEach(function (target, bit) {
      label = el('label', {text: 'Bit ' + bit + ' drives '}, grid);
      input = el('select', {}, label);
      Object.keys(Motion.TARGETS).forEach(function (name) {
        el('option', {value: name, text: name.replace('-', '−')}, input).selected = name === target;
      });
      input.addEventListener('change', function (e) {
        m.settings.dirs[bit] = e.target.value;
        changed();
      });
    });
    label = el('label', {text: 'A 0 bit '}, grid);
    input = el('select', {}, label);
    [['1', 'pushes its coordinate back, adding up'], ['', 'slows its coordinate to rest']].forEach(function (o) {
      el('option', {value: o[0], text: o[1]}, input).selected = (o[0] === '1') === m.settings.push;
    });
    input.addEventListener('change', function (e) {
      m.settings.push = e.target.value === '1';
      changed();
    });
    details.addEventListener('toggle', function () {
      m.open = details.open;
      if (m.open) self.drawMotion(self.clock.now());
    });
    canvas.addEventListener('pointerdown', function (e) {
      dragging = [e.clientX, e.clientY, m.yaw, m.pitch];
      canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      m.yaw = Math.max(-180, Math.min(180, dragging[2] + (e.clientX - dragging[0]) * 0.4));
      m.pitch = Math.max(-89, Math.min(89, dragging[3] + (e.clientY - dragging[1]) * 0.4));
      m.yawInput.value = m.yaw;
      m.pitchInput.value = m.pitch;
      self.drawMotion(self.clock.now());
    });
    canvas.addEventListener('pointerup', function () { dragging = null; });
    canvas.addEventListener('wheel', function (e) {
      e.preventDefault();
      m.zoom = Math.max(0.01, Math.min(3, m.zoom * Math.exp(-e.deltaY * 0.001)));
      m.zoomInput.value = m.zoom;
      self.drawMotion(self.clock.now());
    }, {passive: false});
  };

  // the colours the lamps are drawn in, as the page's looks set them
  function lampColours(root) {
    var style = getComputedStyle(root), fallback = {};
    Scene.SIGN_COLOURS.forEach(function (pair) { fallback[pair[0]] = pair[1]; });
    function colour(name) {
      var value = style.getPropertyValue('--' + name).trim();
      return /^#[0-9a-fA-F]{6}$/.test(value) ? value : fallback[name];
    }
    return {sign: colour('sign'), unlit: colour('lamp-off'), plane: '#3a2f22', paths: ['#6ebeff', '#ff8ad8'],
            partPath: '#ffd27a',
            lit: [colour('lamp-on'), colour('lamp-red'), colour('lamp-green'), colour('lamp-yellow')],
            text: '#e8e8e8', muted: '#9a917f', lane: '#1a150f'};
  }

  Player.prototype.drawMotion = function (tick) {
    var m = this.motion, run = this.run(this.index), scene = run.scene, tickMs = this.params.tick_ms, n = this.index;
    var v, lines = [], paths = [], ms, plain = run.plain || scene, swing = run.swing, parts = [];
    if (!this.motionCanvas) return;
    // the firmware's lamps at a tick, less what a keyed swing clears of them there, its parts drawn
    // in 3D where their poses take them rather than flattened onto the sign
    function frameAt(at) {
      var f = Scene.trackAt(plain.played, at);
      return swing ? [f[0], Polar.sceneAt(swing, at * tickMs).bg].concat(f.slice(2)) : f;
    }
    if (swing && !m.partPaths[n]) {
      // where each part's pivot goes over its keys
      m.partPaths[n] = swing.parts.map(function (p) {
        var points = [], t, t0 = p.keys[0].t, t1 = p.keys[p.keys.length - 1].t, d;
        for (t = t0; t <= t1; t += 20) {
          d = Polar.offset(p, p.track(t));
          points.push([p.pivot[0] + d[0], p.pivot[1] + d[1], d[2]]);
        }
        return points;
      });
    }
    if (!m.vectors[n]) m.vectors[n] = Motion.vectors(run.ops, scene.end, tickMs, m.settings);
    v = m.vectors[n];
    if (!m.paths[n]) {
      // where each line's middle goes over the whole message
      m.paths[n] = v.map(function (line) {
        var points = [], t, rows, c, pivot;
        for (t = 0; t <= scene.end * tickMs; t += 20) {
          rows = Motion.rowsAt(line, t / tickMs);
          if (!rows) continue;
          c = Motion.at(line, t);
          pivot = Motion.pivotOf(rows);
          points.push([pivot[0] + c[0], pivot[1] + c[1], c[2]]);
        }
        return points;
      });
    }
    tick = Math.max(0, Math.min(tick, scene.end));
    ms = tick * tickMs;
    v.forEach(function (line, k) {
      var rows = Motion.rowsAt(line, tick), c, pivot;
      if (!rows) return;
      c = Motion.at(line, ms);
      pivot = Motion.pivotOf(rows);
      lines.push({rows: rows, pivot: pivot, R: Motion.rotation(c.slice(3, 6)), d: c.slice(0, 3),
                  frame: frameAt(Math.max(0, Math.min(scene.end, c[6] / tickMs)))});
      paths.push({points: m.paths[n][k], now: [pivot[0] + c[0], pivot[1] + c[1], c[2]]});
    });
    var partPaths = [];
    if (swing) {
      parts = Polar.sceneAt(swing, ms).parts;
      swing.parts.forEach(function (p, k) {
        var t0 = p.keys[0].t, t1 = p.keys[p.keys.length - 1].t, d;
        if (ms < t0 || ms > t1) return;
        d = Polar.offset(p, p.track(ms));
        partPaths.push({points: m.partPaths[n][k], now: [p.pivot[0] + d[0], p.pivot[1] + d[1], d[2]]});
      });
    }
    var colours = lampColours(this.root);
    Motion.draw(this.motionCanvas, {frame: frameAt(tick), lines: lines, paths: paths, parts: parts,
                                    partPaths: partPaths, partDepth: swing && swing.depth, yaw: m.yaw,
                                    pitch: m.pitch, zoom: m.zoom, target: m.target, distance: 400, depth: m.settings.depth,
                                    colours: colours});
    Motion.timeline(this.motionChart, v, scene.end * tickMs, tickMs, ms, m.settings, colours);
  };

  Player.prototype.setDensity = function (density) {
    var tick = this.clock.now(), playing = this.clock.playing;
    this.density = density;
    this.load(this.index, tick);
    this.at(tick, playing);
    this.saveHash();
  };

  Player.prototype.paramsPanel = function (details, title, items) {
    var self = this, grid;
    el('summary', {text: title}, details);
    grid = el('div', {'class': 'grid'}, details);
    items.forEach(function (item) {
      var key = item[0], values = Array.isArray(self.params[key]) ? self.params[key] : [self.params[key]];
      var field = el('fieldset', {}, grid), legend = el('legend', {text: item[1]}, field);
      values.forEach(function (value, n) {
        var label = el('label', {text: item[2] ? item[2][n] + ' ' : ''}, field);
        var input = el('input', {type: 'number', step: 'any', min: key === 'shares' ? 0.05 : 0, value: value,
                                 'aria-label': item[1] + (item[2] ? ', ' + item[2][n] : '')}, label);
        input.dataset.key = key;
        input.dataset.n = n;
        input.addEventListener('change', function () { self.setParam(key, n, input); });
      });
      if (key === 'travel_ticks') {
        self.speedNote = el('span', {'class': 'hint'}, field);
        self.showSpeed();
      }
      legend.title = 'The model has ' + JSON.stringify(self.defaults[key]);
    });
  };

  Player.prototype.showSpeed = function () {
    if (this.speedNote) {
      this.speedNote.textContent = (1000 / (this.params.travel_ticks * this.params.tick_ms)).toFixed(1) + ' columns a second';
    }
  };

  Player.prototype.setParam = function (key, n, input) {
    var value = +input.value;
    if (!isFinite(value) || value < 0 || (!FRACTIONAL[key] && value !== Math.floor(value)) ||
        (AT_LEAST_ONE[key] && AT_LEAST_ONE[key].indexOf(n) >= 0 && value < 1) || (key === 'tick_ms' && value <= 0)) {
      input.setAttribute('aria-invalid', 'true');
      return;
    }
    if (key === 'shares') value = Math.max(0.05, Math.min(1, value));
    input.removeAttribute('aria-invalid');
    if (Array.isArray(this.params[key])) this.params[key][n] = value;
    else this.params[key] = value;
    this.showSpeed();
    this.rerun();
  };

  // The sign's settings: each run of them a template plays, as a hold, an effect, a format
  // and font, or whether a line draws tall
  Player.prototype.settingsPanel = function (details) {
    var self = this;
    el('summary', {text: 'Program ($06A8 settings)'}, details);
    el('p', {'class': 'hint', text: 'The settings the firmware boots with and the presentations play: ' +
                                     'how long each holds, and the format, font and effect each draws with.'}, details);
    var row = el('label', {}, details);
    this.allSettings = el('input', {type: 'checkbox'}, row);
    row.appendChild(document.createTextNode(' Show the settings no message here uses'));
    this.allSettings.addEventListener('change', function () { self.showSettings(); });
    this.settingsList = el('div', {'class': 'settings'}, details);
    row = el('label', {text: 'All 47 bytes '}, details);
    this.rawSettings = el('input', {type: 'text', size: 60, spellcheck: 'false', 'class': 'hex'}, row);
    this.rawSettings.addEventListener('change', function () {
      var text = self.rawSettings.value.replace(/\s+/g, '');
      if (!/^[0-9a-fA-F]{94}$/.test(text)) {
        self.rawSettings.setAttribute('aria-invalid', 'true');
        return;
      }
      self.rawSettings.removeAttribute('aria-invalid');
      self.params.settings = text.toLowerCase();
      self.showSettings();
      self.rerun();
    });
  };

  Player.prototype.settingBytes = function () {
    return Sign.hexBytes(this.params.settings);
  };

  Player.prototype.setSetting = function (offset, bytes) {
    var all = this.settingBytes();
    bytes.forEach(function (b, n) { all[offset + n] = b & 0xff; });
    this.params.settings = all.map(hex2).join('');
    this.rawSettings.value = all.map(hex2).join(' ');
    this.rerun();
  };

  Player.prototype.showSettings = function () {
    var self = this, list = this.settingsList, bytes = this.settingBytes(), used = {};
    if (!list) return;
    this.rawSettings.value = bytes.map(hex2).join(' ');
    this.messages.forEach(function (m) {
      var codes = codesOf(Sign.hexBytes(m.payload));
      for (var c in codes) used[c] = true;
    });
    list.textContent = '';
    this.data.tables.setting_fields.forEach(function (field) {
      var offset = field[0], count = field[1], kind = field[2], codes = field[3];
      if (!self.allSettings.checked && !codes.some(function (c) { return used[c]; })) return;
      var box = el('fieldset', {}, list), value = bytes.slice(offset, offset + count);
      el('legend', {text: '+' + hex2(offset) + ' ' + kind + ', for ' + codes.map(hex2).join(' ')}, box);
      self.settingEditor(box, offset, kind, value);
    });
    if (!list.firstChild) el('p', {'class': 'hint', text: 'No message here plays a presentation that reads them.'}, list);
  };

  Player.prototype.settingEditor = function (box, offset, kind, value) {
    var self = this, input, select, label, param, font;
    if (kind === 'hold') {
      label = el('label', {text: 'Hold, seconds '}, box);
      input = el('input', {type: 'number', step: 0.01, min: 0, value: ((value[0] | value[1] << 8) * this.params.tick_ms / 1000).toFixed(2)}, label);
      input.addEventListener('change', function () {
        var ticks = Math.max(0, Math.min(0xffff, Math.round(+input.value * 1000 / self.params.tick_ms)));
        self.setSetting(offset, [ticks & 0xff, ticks >> 8]);
      });
    } else if (kind === 'effect') {
      label = el('label', {text: 'Effect '}, box);
      select = el('select', {}, label);
      EFFECTS.forEach(function (effect) {
        el('option', {value: effect[0], text: hex2(effect[0]) + ' ' + effect[1]}, select).selected = effect[0] === (value[0] & 0x7f);
      });
      label = el('label', {text: ' with '}, box);
      param = el('input', {type: 'text', size: 2, 'class': 'hex', value: hex2(value[1]), 'aria-label': 'its byte, in hex'}, label);
      var update = function () {
        var code = +select.value, takes = self.data.tables.effect_parameters.indexOf(code) >= 0;
        param.disabled = !takes;
        return takes;
      };
      update();
      select.addEventListener('change', function () {
        self.setSetting(offset, [+select.value, update() ? parseInt(param.value, 16) || 0 : 0x1e]);
      });
      param.addEventListener('change', function () {
        if (/^[0-9a-fA-F]{1,2}$/.test(param.value)) self.setSetting(offset, [+select.value, parseInt(param.value, 16)]);
      });
    } else if (kind === 'format') {
      font = value[1];
      [['Gap', value[0] >> 4, function (v) { return [(v & 15) << 4 | value[0] & 15, font]; }],
       ['Raise', value[0] & 15, function (v) { return [value[0] & 0xf0 | v & 15, font]; }]].forEach(function (part) {
        label = el('label', {text: part[0] + ' '}, box);
        input = el('input', {type: 'number', min: 0, max: 15, step: 1, value: part[1]}, label);
        input.addEventListener('change', function (e) { self.setSetting(offset, part[2](+e.target.value)); });
      });
      label = el('label', {text: ' Font '}, box);
      select = el('select', {}, label);
      this.data.tables.font_rows.forEach(function (rows, n) {
        el('option', {value: n, text: n + ', ' + rows + ' rows'}, select).selected = (font & 7) % 5 === n;
      });
      select.addEventListener('change', function () { self.setSetting(offset, [value[0], font & 0xf8 | +select.value]); });
      FONT_FLAGS.forEach(function (flag) {
        label = el('label', {}, box);
        input = el('input', {type: 'checkbox'}, label);
        input.checked = !!(font & flag[0]);
        label.appendChild(document.createTextNode(' ' + flag[1]));
        input.addEventListener('change', function (e) {
          self.setSetting(offset, [value[0], e.target.checked ? font | flag[0] : font & ~flag[0]]);
        });
      });
    } else {
      label = el('label', {}, box);
      input = el('input', {type: 'checkbox'}, label);
      input.checked = value[0] === 0x08;
      label.appendChild(document.createTextNode(' Draws twice as tall'));
      input.addEventListener('change', function (e) { self.setSetting(offset, [e.target.checked ? 0x08 : 0x09]); });
    }
  };

  Player.prototype.reset = function () {
    this.params = copy(this.defaults);
    this.looks = {};
    this.density = densityOf(this.looks);
    this.steady = false;
    history.replaceState(null, '', location.pathname + location.search);
    this.root.querySelectorAll('.panels, .transport, .status, .playlist').forEach(function (node) {
      node.parentNode.removeChild(node);
    });
    if (this.sign) this.sign.remove();
    this.sign = null;
    this.cache = {};
    this.clock = new Clock();
    this.clock.tickMs = this.params.tick_ms;
    this.motion = motionState();
    this.build();
    this.applyLooks();
    this.load(this.index);
    this.play();
  };

  Player.prototype.saveHash = function () {
    var self = this, parts = [];
    Object.keys(this.defaults).forEach(function (key) {
      if (JSON.stringify(self.params[key]) !== JSON.stringify(self.defaults[key])) {
        parts.push(key + '=' + encodeURIComponent(Array.isArray(self.params[key]) ? self.params[key].join(',') : self.params[key]));
      }
    });
    Object.keys(this.looks).forEach(function (key) { if (self.looks[key]) parts.push(key + '=' + encodeURIComponent(self.looks[key])); });
    if (this.renderer !== 'vector') parts.push('r=' + this.renderer);
    if (this.steady) parts.push('steady=1');
    if (!this.swing) parts.push('swing=0');
    if (this.index) parts.push('m=' + this.index);
    history.replaceState(null, '', location.pathname + location.search + (parts.length ? '#' + parts.join('&') : ''));
  };

  Player.prototype.copyLink = function () {
    var self = this, link = location.href.split('#')[0] + '#' + (location.hash.slice(1) ? location.hash.slice(1) + '&' : '') +
               't=' + (this.clock.now() * this.params.tick_ms / 1000).toFixed(2);
    function done(text) { self.note.textContent = text; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(link).then(function () { done('Link copied'); }, function () { done(link); });
    } else {
      done(link);
    }
  };

  // #verify: every breakpoint of every message, as the browser has animated the SVG half a
  // tick after it in its first loop and its second and half a tick before it, against the
  // frames the model played; the result lands in window.SR_VERIFY
  Player.prototype.verify = function () {
    var self = this, out = {checked: 0, wrong: 0, first: null};
    this.messages.forEach(function (message, n) {
      self.load(n, 0);
      self.pause();
      if (!(self.sign instanceof SmilSign)) return;
      var scene = self.scene, frame = 0, points = Scene.breakpoints(scene, scene.played, self.run(n).params.blink_ticks);
      var breaks = {};
      function framed(t) {
        return scene.spans.some(function (span) { return span[0] <= t && t < span[1]; });
      }
      function check(t, loops, planes) {
        self.sign.at(t, true, loops);
        out.checked += 1;
        if (self.sign.lamps().join('|') !== planes.join('|')) {
          out.wrong += 1;
          if (!out.first) out.first = {message: n, tick: t, loops: loops};
        }
      }
      points.forEach(function (t) { breaks[t] = true; });
      points.forEach(function (t) {
        while (frame + 1 < scene.played.length && scene.played[frame + 1][0] <= t) frame++;
        if (t > 0 && !breaks[t - 1] && !framed(t - 1)) {
          check(t - 1, 0, scene.played[scene.played[frame][0] === t ? frame - 1 : frame].slice(1));
        }
        if (framed(t)) return;
        check(t, 0, scene.played[frame].slice(1));
        check(t, 1, scene.played[frame].slice(1));
      });
    });
    window.SR_VERIFY = out;
    this.note.textContent = 'Verified ' + out.checked + ' breakpoints: ' + out.wrong + ' differ';
    return out;
  };

  // --- the page of every story: each sign on it plays live while it is in view ---

  // A sign on the page of every story, playing `stories` in turn over its GIF
  function LiveSign(led, stories, data, still) {
    var self = this;
    this.led = led;
    this.stories = stories;
    this.data = data;
    this.index = 0;
    this.clock = new Clock();
    this.clock.tickMs = data.params.tick_ms;
    this.paused = still;
    this.seen = false;
    this.runs = {};
    this.sign = null;
    this.scene = null;
    led.classList.add('live');
    led.tabIndex = 0;
    led.setAttribute('aria-label', 'Pause or play this sign');
    function toggle() {
      self.paused = !self.paused;
      self.show(self.seen);
    }
    led.addEventListener('click', toggle);
    led.addEventListener('keydown', function (e) {
      if (e.key === ' ' || e.key === 'Enter') {
        e.preventDefault();
        toggle();
      }
    });
  }

  LiveSign.prototype.run = function (n) {
    if (!this.runs[n]) {
      // two at a time: the story playing and the one before it
      if (Object.keys(this.runs).length > 1) this.runs = {};
      this.runs[n] = swung(runScene(this.stories[n].payload, this.data.params, this.data.tables), this.stories[n], true);
      this.stories[n].ticks = this.runs[n].scene.end;
    }
    return this.runs[n];
  };

  LiveSign.prototype.load = function (n, tick) {
    var run = this.run(n);
    this.index = n;
    if (this.sign) this.sign.remove();
    this.scene = run.scene;
    this.sign = new SmilSign(this.led, run.scene, run.params, metaOf(this.stories[n], run.params));
    this.clock.set(tick || 0, this.clock.playing);
  };

  // In view it plays, unless paused; out of it, it gives up its SVG
  LiveSign.prototype.show = function (seen) {
    var tick = this.clock.now();
    this.seen = seen;
    this.led.classList.toggle('paused', this.paused);
    if (!seen) {
      this.clock.set(tick, false);
      if (this.sign) this.sign.remove();
      this.sign = null;
      this.scene = null;
      return;
    }
    if (!this.scene) {
      this.load(this.index, tick);
      if (this.paused && !tick) this.clock.set(Scene.poster(this.scene), false);
    }
    this.clock.set(this.clock.tick, !this.paused);
    this.frame();
  };

  LiveSign.prototype.frame = function () {
    var scene = this.scene, tick;
    if (!scene) return;
    tick = this.clock.now();
    if (tick >= scene.end && this.clock.playing) {
      this.load((this.index + 1) % this.stories.length, 0);
      this.clock.set(0, true);
      tick = 0;
    }
    this.sign.at(Math.min(tick, this.scene.end), !this.clock.playing);
  };

  // The page of every story: its signs play live where the browser can animate them
  function overview() {
    var data = JSON.parse(document.getElementById('sign-data').textContent), signs = [], still, observer;
    if (!smil || typeof IntersectionObserver === 'undefined') return;
    still = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
    [].forEach.call(document.querySelectorAll('.led[data-live]'), function (led) {
      var sign = new LiveSign(led, data.lives[+led.getAttribute('data-live')].map(function (id) {
        return copy(data.stories[id]);
      }), data, still);
      led.sign = sign;
      signs.push(sign);
    });
    observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) { entry.target.sign.show(entry.isIntersecting); });
    });
    signs.forEach(function (sign) { observer.observe(sign.led); });
    requestAnimationFrame(function step() {
      signs.forEach(function (sign) { sign.frame(); });
      requestAnimationFrame(step);
    });
    window.SR_SIGNS = signs;
    window.SR_OK = true;
    return signs;
  }

  // The page's data, its player, and the fallback taken away once it plays
  function boot() {
    var root = document.getElementById('player'), data = JSON.parse(document.getElementById('sign-data').textContent);
    var player = new Player(root, data);
    root.classList.add('live');
    if (/(^|&)verify(&|$)/.test(location.hash.slice(1))) {
      // once the page has loaded, when every browser has started its SVG's clock
      var check = function () { requestAnimationFrame(function () { player.verify(); }); };
      if (document.readyState === 'complete') check();
      else window.addEventListener('load', check);
    }
    window.SR_PLAYER = player;
    window.SR_OK = true;
    return player;
  }

  return {densityOf: densityOf, codesOf: codesOf, SmilSign: SmilSign, FrameSign: FrameSign, Player: Player,
          LiveSign: LiveSign, boot: boot, overview: overview};
})();

if (typeof module !== 'undefined') module.exports = SilentRadioPlayer;
