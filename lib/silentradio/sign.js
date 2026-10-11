// The Silent Radio sign, as sign.py models its firmware playing a message: the same
// functions in the same order, so each reads beside its Python. The tables and the
// parameters come from Python (sign_tables and sign_parameters); nothing here has defaults.
//
// Public domain / Unlicense, as with the rest of ccDecoder.

var SilentRadioSign = (function () {
  'use strict';

  var WIDTH = 112, ROWS = 15;

  function hexBytes(text) {
    var out = [], words = text.split(/\s+/), n;
    if (words.length === 1 && words[0].length > 2) {
      for (n = 0; n + 1 < text.length; n += 2) out.push(parseInt(text.substr(n, 2), 16));
      return out;
    }
    for (n = 0; n < words.length; n++) if (words[n]) out.push(parseInt(words[n], 16));
    return out;
  }

  function filled(n, value) {
    var out = [], i;
    for (i = 0; i < n; i++) out.push(value);
    return out;
  }

  function extend(out, items) {
    for (var i = 0; i < items.length; i++) out.push(items[i]);
  }

  function popcount(v) {
    var n = 0;
    for (; v; v &= v - 1) n++;
    return n;
  }

  function floorDiv(a, b) {
    return Math.floor(a / b);
  }

  function has(object, key) {
    return object != null && Object.prototype.hasOwnProperty.call(object, key);
  }

  // Python's l[a:b] = items
  function setSlice(list, a, b, items) {
    a = Math.min(Math.max(a, 0), list.length);
    b = Math.max(a, Math.min(b, list.length));
    Array.prototype.splice.apply(list, [a, b - a].concat(items));
  }

  // Python's l[x] for an x of -1
  function index(x) {
    return x < 0 ? WIDTH + x : x;
  }

  // columns [a, b) of `list`, masked
  function part(list, a, b, mask) {
    var out = [], x;
    for (x = a; x < b; x++) out.push(list[x] & mask);
    return out;
  }

  function context(params, tables, ops) {
    return {p: params, t: tables, ops: !!ops, settings: hexBytes(params.settings),
            glyphs: tables.fonts.map(function (font) { return font.split(/\s+/).filter(Boolean).map(hexBytes); }),
            low: (tables.fonts_low || []).map(function (font) { return font.split(/\s+/).filter(Boolean).map(hexBytes); })};
  }

  function glyphOf(c, font, character) {
    font = font < 5 ? font : font - 5;
    if (character >= 0 && character < 0x20 && c.low[font]) {
      var low = c.low[font][character];
      return [low[0], low.slice(1)];
    }
    var glyphs = c.glyphs[font];
    if (character - 0x20 >= 0 && character - 0x20 < glyphs.length) {
      var glyph = glyphs[character - 0x20];
      return [glyph[0], glyph.slice(1)];
    }
    return [0, filled(c.t.font_rows[font], 0)];
  }

  function templateBytes(part, settings) {
    var out = [], words = part.split(/\s+/), n, word, at, count;
    for (n = 0; n < words.length; n++) {
      word = words[n];
      if (!word) continue;
      if (word.charAt(0) === '@') {
        at = parseInt(word.substr(1).split(':')[0], 16);
        count = parseInt(word.split(':')[1], 10);
        extend(out, settings.slice(at, at + count));
      } else {
        out.push(parseInt(word, 16));
      }
    }
    return out;
  }

  function playElements(payload, settings, tables) {
    var out = [], i = 0, code, count, templates, board, league, stop, line, parts, n, start;
    while (i + 1 < payload.length && payload[i] === 0) {
      code = payload[i + 1];
      i += 2;
      if (code === 0x0c) {
        count = i + 1 < payload.length ? payload[i] | payload[i + 1] << 8 : 0;
        extend(out, payload.slice(i + 2, i + 2 + count));
        i += 2 + count;
        continue;
      }
      board = has(tables.scoreboards, code) ? tables.scoreboards[code] : null;
      league = has(tables.leagues, code) ? tables.leagues[code] : null;
      templates = board ? tables.scoreboard_fields : league ? league[0] :
        has(tables.presentations, code) ? tables.presentations[code] : null;
      if (templates === null) {
        // a code the ROM presents nothing for
        stop = payload.indexOf(0x1f, i);
        i = stop < 0 ? payload.length : stop + 1;
        continue;
      }
      if (board) extend(out, templateBytes(board[0], settings));
      line = i < payload.length ? payload[i] : 0x1f;
      i += 1;
      while (has(templates, line)) {
        parts = templates[line].split('..');
        for (n = 0; n < parts.length; n++) {
          extend(out, templateBytes(parts[n], settings));
          if (n + 1 < parts.length) {
            // the text runs to a byte below 20, but a graphic column's second byte, whatever
            // it is, is text, as the ROM reads it
            start = i;
            while (i < payload.length && (payload[i] >= 0x20 || pairDue(out, payload.slice(start, i), tables))) i += 1;
            extend(out, payload.slice(start, i));
            line = i < payload.length ? payload[i] : 0x1f;
            i += 1;
          }
        }
      }
      if (line !== 0x1f) {
        // a line it has no template for, as a damaged run end makes: the rest goes too
        stop = payload.indexOf(0x1f, i);
        i = stop < 0 ? payload.length : stop + 1;
      }
      if (board) extend(out, templateBytes(board[1], settings));
      if (league) extend(out, templateBytes(league[1], settings));
    }
    return out;
  }

  function routeToLines(data, tables, state) {
    var lines = [[], []], line = 0, pairs = [0, 0], rows = [0xe8, 0x70], i = 0, byte, top, bottom;

    function take() {
      return i < data.length ? data[i++] : 0;
    }

    function rowsAgain() {
      lines[0].push(0, rows[0]);
      lines[1].push(0, rows[1]);
    }

    while (i < data.length) {
      byte = data[i++];
      if (pairs[line] === 2) {
        pairs[line] = 1;
        lines[line].push(byte);
      } else if (pairs[line] && byte >= 0x80) {
        pairs[line] = 2;
        lines[line].push(byte);
      } else if (byte === 0x01 || byte === 0x02) {
        line = byte - 1;
      } else if (byte === 0x03) {
        rowsAgain();
      } else if (byte === 0x04) {
        rows[line] = take();
        lines[line].push(0, rows[line]);
        lines[1 - line].push(0, rows[1 - line]);
        lines[line].push(1, take(), take());
        rowsAgain();
      } else if (byte === 0x05) {
        rows = [take(), take()];
        lines[0].push(0, rows[0]);
        lines[1].push(0, rows[1]);
        lines[0].push(1, take(), take());
        lines[1].push(1, take(), take());
        rowsAgain();
      } else if (byte === 0x06 || byte === 0x07) {
        lines[line].push(byte - 4, take());
      } else if (byte === 0x08 || byte === 0x09) {
        pairs[line] = byte === 0x08 ? 1 : 0;
        lines[line].push(byte);
      } else if (byte === 0x00 || byte === 0x0a || byte === 0x1d || byte === 0x1f) {
        lines[line].push(byte, take());
      } else if (byte === 0x1c) {
        lines[line].push(byte, take(), take());
      } else if (has(tables.presets, byte)) {
        top = hexBytes(tables.presets[byte][0]);
        bottom = hexBytes(tables.presets[byte][1]);
        line = 0;
        pairs = [0, 0];
        rows = [top[top.indexOf(0) + 1], bottom[bottom.indexOf(0) + 1]];
        extend(lines[0], top);
        extend(lines[1], bottom);
        rowsAgain();
      } else {
        lines[line].push(byte);
      }
    }
    return state ? {lines: lines, line: line, pairs: pairs} : lines;
  }

  // Whether the router, given `out` and then `text`, is due a graphic column's second byte on
  // the line it has picked
  function pairDue(out, text, tables) {
    var data = out.slice(), routed;
    extend(data, text);
    routed = routeToLines(data, tables, true);
    return routed.pairs[routed.line] === 2;
  }

  function SignLine(page, hues, c) {
    this.c = c;
    this.top = this.bottom = null;
    this.left = this.right = 0;
    this.gap = 1;
    this.lift = 0;
    this.font = 0;
    // the colour it draws in: its font byte's bits 0 and 1, a red LED and a green one
    this.colour = 0;
    this.inverse = this.double = this.flash = this.descend = false;
    this.tall = this.travel = false;
    this.blink = true;
    this.clock = 0;
    this.page = page;
    this.flags = filled(WIDTH, false);
    this.hues = hues;
    this.free = WIDTH;
  }

  // [red, green] of a column drawn in the line's colour
  SignLine.prototype.painted = function (column) {
    return [this.colour & 1 ? column : 0, this.colour & 2 ? column : 0];
  };

  SignLine.prototype.columns = function () {
    return [8 * this.left, WIDTH - 8 * this.right];
  };

  SignLine.prototype.mask = function () {
    var mask = 0, row;
    if (this.top === null) return 0;
    for (row = this.top; row <= this.bottom; row++) mask |= 1 << row;
    return mask;
  };

  SignLine.prototype.rows = function (byte) {
    // the last row in the high nibble and the first in the low, counted from the bottom
    if (byte === 0xff) {
      this.top = this.bottom = null;
    } else {
      this.top = Math.max(0, 14 - (byte >> 4));
      this.bottom = 14 - (byte & 0x0f);
    }
  };

  SignLine.prototype.ticks = function (each, perRow, perByte) {
    var ab = this.columns();
    return each + popcount(this.mask()) * (perRow + perByte * (ab[1] - ab[0]) / 8);
  };

  SignLine.prototype.clear = function () {
    var ab = this.columns(), a = ab[0], b = ab[1], mask = this.mask(), lit = this.inverse ? mask : 0;
    var planes = [this.page].concat(this.hues), values = [lit].concat(this.painted(lit)), n, x;
    for (n = 0; n < planes.length; n++) for (x = a; x < b; x++) planes[n][x] = planes[n][x] & ~mask | values[n];
    setSlice(this.flags, a, b, filled(b - a, false));
    this.free = b - a;
  };

  SignLine.prototype.shift = function (column, flash) {
    // a column goes in at the window's right and what is there moves left
    var ab = this.columns(), a = ab[0], b = ab[1], mask = this.mask(), planes, values, plane, n, x, flags;
    column = (column ^ (this.inverse ? mask : 0)) & mask;
    planes = [this.page].concat(this.hues);
    values = [column].concat(this.painted(column));
    for (n = 0; n < planes.length; n++) {
      plane = planes[n];
      for (x = a; x < b - 1; x++) plane[x] = plane[x] & ~mask | plane[x + 1] & mask;
      x = index(b - 1);
      plane[x] = plane[x] & ~mask | values[n];
    }
    flags = this.flags.slice(a + 1, b);
    flags.push(flash);
    setSlice(this.flags, a, b, flags);
    this.free = Math.max(0, this.free - 1);
  };

  SignLine.prototype.glyph = function (character, gap) {
    // the columns of a character, set on the window's bottom row and lifted, and `gap` more taken on
    // from its bits past its width, as the drawer takes its gap
    var got = glyphOf(this.c, this.font, character), head = got[0], rows = got[1], under = 0;
    var width, base, columns = [], column, col, r, y, tall;
    if (this.descend) {
      // a descender hangs from a line two rows up, drawn from its own glyph
      if (head & 0x80) {
        got = glyphOf(this.c, this.font, 0x80 + (head >> 4 & 7));
        head = got[0];
        rows = got[1];
      } else {
        under = 2;
      }
    }
    width = head & 0x80 ? head & 0x0f : head;
    if (this.tall) {
      tall = [];
      for (r = 0; r < rows.length; r++) tall.push(rows[r], rows[r]);
      rows = tall;
    }
    base = (this.bottom === null ? 14 : this.bottom) - this.lift - under;
    for (col = 0; col < width + (gap || 0); col++) {
      column = 0;
      for (r = 0; r < rows.length; r++) {
        y = base - (rows.length - 1 - r);
        if (col < 8 && rows[r] >> (7 - col) & 1 && y >= 0 && y < ROWS) column |= 1 << y;
      }
      columns.push(column);
    }
    return columns;
  };

  SignLine.prototype.graphic = function (value) {
    // a graphic column, its bit 14 on the window's bottom row
    var base = (this.bottom === null ? 14 : this.bottom) - this.lift, column = 0, bit, y;
    for (bit = 0; bit < 15; bit++) {
      y = base - (14 - bit);
      if (value >> bit & 1 && y >= 0 && y < ROWS) column += 1 << y;
    }
    return column;
  };

  function Sign(c) {
    var page = filled(WIDTH, 0), hues = [filled(WIDTH, 0), filled(WIDTH, 0)];
    this.c = c;
    this.lit = filled(WIDTH, 0);
    this.hues = [filled(WIDTH, 0), filled(WIDTH, 0)];
    this.flashing = [filled(WIDTH, false), filled(WIDTH, false)];
    this.lines = [new SignLine(page, hues, c), new SignLine(page, hues, c)];
    this.now = [0, 0];
    this.border = null;
    // what each write to the lamps did, for the scene
    this.ops = c.ops ? [] : null;
    this.effects = 0;
    this.effectId = [0, 0];
    // the fault code effect 45 draws, 'A' at boot: none
    this.fault = 0x41;
  }

  // Line k's window of the page onto the lamps, or only columns [from, to) of it and `rows`
  Sign.prototype.show = function (k, columns, rows) {
    var line = this.lines[k], ab = line.columns(), mask = rows == null ? line.mask() : rows & line.mask();
    var from = columns ? columns[0] : ab[0], to = columns ? columns[1] : ab[1], x, n;
    if (this.ops) {
      this.ops.push({op: 'show', t: this.now[k], k: k, id: this.effectId[k], start: from, stop: to, mask: mask,
                     a: ab[0], b: ab[1], window: line.mask(), page: part(line.page, ab[0], ab[1], line.mask()),
                     hues: line.hues.map(function (hue) { return part(hue, ab[0], ab[1], line.mask()); })});
    }
    for (x = from; x < to; x++) {
      this.lit[x] = this.lit[x] & ~mask | line.page[x] & mask;
      for (n = 0; n < 2; n++) this.hues[n][x] = this.hues[n][x] & ~mask | line.hues[n][x] & mask;
      this.flashing[k][x] = line.flags[x];
    }
  };

  Sign.prototype.run = function* (k, stream) {
    var self = this, p = this.c.p, line = this.lines[k], i = 0, byte;

    function take() {
      return i < stream.length ? stream[i++] : 0;
    }

    function* draw(columns, flash) {
      var work, n, ab, a, b, mask, x, column, wa, wb, planes, values, q;
      if (!line.travel && columns.length) {
        work = p.character_ticks + columns.length * line.ticks(p.column_ticks[0], p.column_ticks[1], p.column_ticks[2]);
        while (work > 0) {
          yield ['work', Math.min(work, p.work_ticks)];
          work -= p.work_ticks;
        }
      }
      for (n = 0; n < columns.length; n++) {
        column = columns[n];
        if (line.travel) {
          // travelling, the window itself moves a column each display frame
          ab = line.columns();
          a = ab[0];
          b = ab[1];
          mask = line.mask();
          column = (column ^ (line.inverse ? mask : 0)) & mask;
          if (self.ops) {
            // margins that cross leave only the column at b - 1 written
            wa = a < b ? a : index(b - 1);
            wb = a < b ? b : wa + 1;
            self.ops.push({op: 'travel', t: self.now[k], k: k, a: wa, b: wb, mask: mask, column: column,
                           hues: line.painted(column), before: part(self.lit, wa, wb, mask),
                           before_hues: self.hues.map(function (hue) { return part(hue, wa, wb, mask); })});
          }
          for (x = a; x < b - 1; x++) self.flashing[k][x] = self.flashing[k][x + 1];
          planes = [self.lit].concat(self.hues);
          values = [column].concat(line.painted(column));
          for (q = 0; q < planes.length; q++) {
            for (x = a; x < b - 1; x++) planes[q][x] = planes[q][x] & ~mask | planes[q][x + 1] & mask;
            x = index(b - 1);
            planes[q][x] = planes[q][x] & ~mask | values[q];
          }
          self.flashing[k][index(b - 1)] = flash;
          yield ['wait', p.travel_ticks];
        } else {
          line.shift(column, flash);
        }
      }
    }

    function character(byte) {
      var column, columns, tail, doubled, n;
      if (byte >= 0x80) {
        column = line.graphic((byte << 8 | (line.tall ? take() : 0)) & 0x7fff);
        return filled(1 + (line.double ? 1 : 0) + line.gap, column);
      }
      if (!line.double) return line.glyph(byte, line.gap);
      // doubled, an eight column glyph runs on into its gap
      columns = line.glyph(byte);
      tail = columns.length === 8 ? columns[7] : 0;
      doubled = [];
      for (n = 0; n < columns.length; n++) doubled.push(columns[n], columns[n]);
      return doubled.concat(filled(line.gap, tail));
    }

    draw.character = function (byte) {
      return draw(character(byte), line.flash);
    };
    draw.take = take;
    while (i < stream.length) {
      byte = stream[i++];
      if (byte >= 0x20) {
        yield* draw.character(byte);
      } else {
        yield ['work', byte === 0x1e ? p.filler_ticks : p.command_ticks];
        yield* self.command(k, byte, take, draw);
      }
    }
  };

  Sign.prototype.command = function* (k, byte, take, draw) {
    var p = this.c.p, line = this.lines[k], rows, font, low, high, code;
    if (byte === 0x00) {
      // the two lines wait for each other, then take their rows
      rows = take();
      yield ['sync'];
      line.rows(rows);
      if (this.ops) this.ops.push({op: 'rows', t: this.now[k], k: k, rows: line.mask()});
    } else if (byte === 0x01 || byte === 0x02 || byte === 0x03) {
      if (byte !== 0x03) Sign.format(line, take());
      if (byte !== 0x02) {
        font = take();
        Sign.setFont(line, font);
        if (this.ops) this.ops.push({op: 'font', t: this.now[k], k: k, byte: font, rows: line.mask()});
        if (font & 0x08) yield ['work', line.ticks(p.clear_ticks[0], p.clear_ticks[1], p.clear_ticks[2])];
      }
    } else if (byte === 0x08 || byte === 0x09) {
      line.tall = byte === 0x08;
    } else if (byte === 0x0a) {
      Sign.margins(line, take());
    } else if (byte === 0x0c) {
      // centres what is drawn
      yield* draw(filled(floorDiv(line.free, 2), 0), false);
    } else if (byte === 0x0d) {
      yield* draw(filled(WIDTH, 0), false);
    } else if (byte === 0x0e) {
      yield ['wait', p.pause_ticks];
    } else if (byte === 0x10) {
      line.travel = true;
    } else if (byte === 0x11 || byte === 0x1d) {
      if (byte === 0x1d) Sign.margins(line, take());
      line.travel = false;
      line.clear();
      yield ['work', line.ticks(p.clear_ticks[0], p.clear_ticks[1], p.clear_ticks[2])];
    } else if (byte === 0x12 || byte === 0x13) {
      line.flash = byte === 0x12;
    } else if (byte === 0x14 || byte === 0x15) {
      line.double = byte === 0x14;
    } else if (byte === 0x16 || byte === 0x17) {
      line.descend = byte === 0x16;
    } else if (byte === 0x18 || byte === 0x19) {
      line.inverse = byte === 0x19;
    } else if (byte === 0x1c) {
      // holds until the line's clock, which 1F 2C sets going, reaches the ticks given
      low = take();
      high = take();
      yield ['until', line.clock + (low | high << 8)];
    } else if (byte === 0x1f) {
      code = take() & 0x7f;
      if (code < 0x20) {
        yield* this.command(k, code, take, draw);
      } else {
        yield* this.effect(k, code, this.c.t.effect_parameters.indexOf(code) >= 0 ? take() : null, draw);
      }
    }
  };

  Sign.format = function (line, byte) {
    line.gap = byte >> 4;
    line.lift = byte & 0x0f;
  };

  Sign.setFont = function (line, byte) {
    line.font = byte & 7;
    line.colour = byte & 3;
    line.inverse = !!(byte & 0x80);
    line.double = !!(byte & 0x40);
    line.flash = !!(byte & 0x20);
    line.descend = !!(byte & 0x10);
    // bit 3 draws off the sign until an effect shows it; without it the line travels
    line.travel = !(byte & 0x08);
    if (byte & 0x08) line.clear();
  };

  Sign.margins = function (line, byte) {
    if (byte >> 4 !== 0x0f) line.left = byte >> 4;
    if ((byte & 0x0f) !== 0x0f) line.right = byte & 0x0f;
  };

  // Scroll rows `top` to `bottom` of line k's window a row a step, the page coming in behind
  // what is shown, from below if `up`; the page moves on with it
  Sign.prototype.roll = function* (k, top, bottom, steps, up) {
    var p = this.c.p, line = this.lines[k], ab, a, b, height, window, rolled = {}, x, row, shown, page, gone;
    var run, n, r, slice, rest, sum, planes, q, values, id = ++this.effects;
    if (top === null) return;
    ab = line.columns();
    a = ab[0];
    b = ab[1];
    height = bottom - top + 1;
    window = 0;
    for (row = top; row <= bottom; row++) window += 1 << row;
    if (this.ops) {
      this.ops.push({op: 'roll', t: this.now[k], k: k, id: id, a: a, b: b, top: top, bottom: bottom, steps: steps,
                     up: up, shown: part(this.lit, a, b, window), page: part(line.page, a, b, window),
                     shown_hues: this.hues.map(function (hue) { return part(hue, a, b, window); }),
                     page_hues: line.hues.map(function (hue) { return part(hue, a, b, window); })});
    }
    // the lamps and their red and green, each rolled the same way
    planes = [[this.lit, line.page], [this.hues[0], line.hues[0]], [this.hues[1], line.hues[1]]];
    for (x = a; x < b; x++) {
      rolled[x] = [];
      for (q = 0; q < planes.length; q++) {
        shown = [];
        page = [];
        for (row = top; row <= bottom; row++) {
          shown.push(planes[q][0][x] >> row & 1);
          page.push(planes[q][1][x] >> row & 1);
        }
        gone = filled(Math.max(0, steps - height), 0);
        run = up ? shown.concat(page, gone) : gone.concat(page, shown);
        values = [];
        for (n = 1; n <= steps; n++) {
          slice = up ? run.slice(n, n + height) : run.slice(run.length - height - n, run.length - n);
          sum = 0;
          for (r = 0; r < slice.length; r++) sum += slice[r] << top + r;
          values.push(sum);
        }
        rolled[x].push(values);
        rest = up ? page.concat(filled(steps, 0)).slice(steps, steps + height) : filled(steps, 0).concat(page).slice(0, height);
        sum = 0;
        for (r = 0; r < rest.length; r++) sum += rest[r] << top + r;
        planes[q][1][x] = planes[q][1][x] & ~window | sum;
      }
    }
    for (n = 0; n < steps; n++) {
      if (this.ops) this.ops.push({op: 'rollstep', t: this.now[k], k: k, id: id, step: n});
      for (x = a; x < b; x++) {
        for (q = 0; q < planes.length; q++) planes[q][0][x] = planes[q][0][x] & ~window | rolled[x][q][n];
        this.flashing[k][x] = line.flags[x];
      }
      yield ['wait', p.roll_ticks[0] + floorDiv(height * (b - a), p.roll_ticks[1])];
    }
  };

  Sign.prototype.effect = function* (k, code, parameter, draw) {
    var self = this, p = this.c.p, line = this.lines[k], ab = line.columns(), a = ab[0], b = ab[1];
    var rows = [], row, third, step, x, source, mask, n, i, first, last, lamp, y, byte, bit, middle, band;
    var font, tall, forms, squeeze, serial, fault, touched, changed, shown, flashing, came, shownHues, planes, pages, q;
    var id = this.effectId[k] = ++this.effects;
    for (row = 0; row < ROWS; row++) if (line.mask() >> row & 1) rows.push(row);

    function rowsMask(list) {
      var m = 0;
      for (var j = 0; j < list.length; j++) m += 1 << list[j];
      return m;
    }

    function turn(v) {
      return (v << 5 | v >> 3) & 0xff;
    }

    if (code === 0x20) {
      for (third = 1; third < 4; third++) {
        this.show(k, null, rowsMask(rows.slice(0, floorDiv(rows.length * third, 3))));
        yield ['wait', p.show_ticks];
      }
    } else if (code === 0x21) {
      // each byte of eight columns rotates the page in from its left, a column a step,
      // what was shown going out at its right; the page's bytes are left empty
      mask = line.mask();
      shown = this.lit.slice();
      flashing = this.flashing[k].slice();
      shownHues = this.hues.map(function (hue) { return hue.slice(); });
      for (step = 0; step < 8; step++) {
        if (this.ops) {
          this.ops.push({op: 'build', t: this.now[k], k: k, id: id, step: step, a: a, b: b, mask: mask,
                         page: part(line.page, a, b, mask), shown: part(shown, a, b, mask),
                         page_hues: line.hues.map(function (hue) { return part(hue, a, b, mask); }),
                         shown_hues: shownHues.map(function (hue) { return part(hue, a, b, mask); })});
        }
        for (x = a; x < b; x++) {
          came = (x & 7) <= step;
          source = came ? x + 7 - step : x - step - 1;
          this.lit[x] = this.lit[x] & ~mask | (came ? line.page : shown)[source] & mask;
          for (q = 0; q < 2; q++) this.hues[q][x] = this.hues[q][x] & ~mask | (came ? line.hues[q] : shownHues[q])[source] & mask;
          this.flashing[k][x] = (came ? line.flags : flashing)[source];
        }
        if (step < 7) yield ['wait', p.build_ticks[0] + floorDiv(rows.length * p.build_ticks[1], p.build_ticks[2])];
      }
      [line.page].concat(line.hues).forEach(function (plane) { for (x = a; x < b; x++) plane[x] &= ~mask; });
    } else if (code === 0x22) {
      if (this.border === null) this.border = this.now[k];
    } else if (code === 0x23 || code === 0x44) {
      if (this.border !== null || code === 0x44) {
        this.border = null;
        if (this.ops) this.ops.push({op: 'unborder', t: this.now[k], k: k});
        // it leaves the end bytes of rows 1 to 13 dark
        [this.lit].concat(this.hues).forEach(function (plane) {
          for (x = 0; x < WIDTH; x++) if (x < 8 || x >= WIDTH - 8) plane[x] &= ~0x3ffe;
        });
      }
    } else if (code === 0x24 || code === 0x25 || code === 0x46 || code === 0x47) {
      yield* this.roll(k, line.top, line.bottom, parameter & 0x0f || 256, code === 0x25 || code === 0x47);
    } else if (code === 0x26) {
      // a lamp of each display byte at a time, bytes seven apart and the lamp moving on
      // five places, until 56 passes have shown them all; a line with no rows lights
      // none, in as long
      first = line.top !== null ? 14 - line.bottom : 0;
      last = line.top !== null ? 14 - line.top : -1;
      lamp = 1;
      y = 0;
      for (n = 0; n < 56; n++) {
        touched = filled(WIDTH, 0);
        changed = filled(WIDTH, 0);
        while (y < 0xf0) {
          row = y >> 4;
          byte = (y & 0x0f) - 1;
          if (first <= row && row <= last && line.left <= byte && byte <= 13 - line.right) {
            bit = 1 << 14 - row;
            for (i = 0; i < 8; i++) {
              if (lamp >> i & 1) {
                x = 8 * byte + i;
                touched[x] |= bit;
                planes = [this.lit].concat(this.hues);
                pages = [line.page].concat(line.hues);
                for (q = 0; q < 3; q++) {
                  changed[x] |= (planes[q][x] ^ pages[q][x]) & bit;
                  planes[q][x] = planes[q][x] & ~bit | pages[q][x] & bit;
                }
                this.flashing[k][x] = line.flags[x];
              }
            }
          }
          lamp = turn(lamp);
          y += 7;
        }
        y -= 0xf0;
        lamp = turn(lamp);
        if (this.ops) {
          this.ops.push({op: 'twinkle', t: this.now[k], k: k, id: id, step: n, touched: touched, changed: changed,
                         page: part(line.page, 0, WIDTH, line.mask()),
                         hues: line.hues.map(function (hue) { return part(hue, 0, WIDTH, line.mask()); })});
        }
        yield ['wait', p.twinkle_ticks[n % 5 >= 3 ? 1 : 0]];
      }
    } else if (code === 0x27) {
      // unveiled from the middle out
      middle = floorDiv(a + b, 2);
      for (step = 1; step < floorDiv(b - a, 2) + 2; step++) {
        this.show(k, [Math.max(a, middle - step), Math.min(b, middle + step)]);
        yield ['wait', p.unveil_ticks];
      }
    } else if (code === 0x28 || code === 0x29) {
      for (step = 1; step < b - a + 1; step++) {
        this.show(k, code === 0x28 ? [a, a + step] : [b - step, b]);
        yield ['wait', p.wipe_ticks[rows.length <= 8 ? 1 : 0]];
      }
    } else if (code === 0x2a || code === 0x2b) {
      band = code === 0x2a ? rows : rows.slice().reverse();
      for (step = 1; step < rows.length + 1; step++) {
        this.show(k, null, rowsMask(band.slice(0, step)));
        yield ['wait', p.reveal_ticks];
      }
    } else if (code === 0x2c) {
      line.clock = this.now[k];
    } else if (code === 0x2f || code === 0x30) {
      line.blink = code === 0x2f;
    } else if (code === 0x31) {
      // the Silent Radio logo, font 4's glyphs
      font = line.font;
      line.font = 4;
      for (i = 0x20; i < 0x2e; i++) yield* draw.character(i);
      line.font = font;
    } else if (code === 0x3f) {
      yield* draw(filled(line.free, 0), false);
    } else if (code === 0x3e) {
      // the window's lamps and the page trade places, their blinking and colour with them
      mask = line.mask();
      planes = [this.lit].concat(this.hues);
      shown = planes.map(function (plane) { return plane.slice(a, b).map(function (v) { return v & mask; }); });
      flashing = this.flashing[k].slice(a, b);
      this.show(k);
      pages = [line.page].concat(line.hues);
      for (n = 0; n < pages.length; n++) for (x = a; x < b; x++) pages[n][x] = pages[n][x] & ~mask | shown[n][x - a];
      setSlice(line.flags, a, b, flashing);
      yield ['work', line.ticks(p.clear_ticks[0], p.clear_ticks[1], p.clear_ticks[2])];
    } else if (code === 0x3d) {
      // the window is filled with the line's background and stops blinking; the page is kept
      mask = line.mask();
      lamp = line.inverse ? mask : 0;
      shownHues = line.painted(lamp);
      if (this.ops) {
        this.ops.push({op: 'show', t: this.now[k], k: k, id: id, start: a, stop: b, mask: mask, a: a, b: b, window: mask,
                       page: filled(b - a, lamp), hues: shownHues.map(function (v) { return filled(b - a, v); })});
      }
      planes = [this.lit].concat(this.hues);
      for (x = a; x < b; x++) {
        planes[0][x] = planes[0][x] & ~mask | lamp;
        for (n = 0; n < 2; n++) planes[n + 1][x] = planes[n + 1][x] & ~mask | shownHues[n];
        this.flashing[k][x] = false;
      }
      yield ['work', line.ticks(p.clear_ticks[0], p.clear_ticks[1], p.clear_ticks[2])];
    } else if (code === 0x40) {
      // drawn off the sign, it blanks the window; travelling, it runs the line off it
      if (line.travel) {
        yield* draw(filled(WIDTH, 0), false);
      } else {
        line.clear();
        yield* this.effect(k, 0x20, null, draw);
      }
    } else if (code === 0x41) {
      // what is shown bounces up and is squeezed out between closing rows
      line.travel = false;
      line.clear();
      squeeze = [[0, 14, 1], [0, 13, 1], [2, 12, 1], [3, 11, 1], [4, 10, 1], [5, 9, 1], [6, 8, 2]];
      for (i = 0; i < squeeze.length; i++) yield* this.roll(k, squeeze[i][0], squeeze[i][1], squeeze[i][2], true);
    } else if (code === 0x42) {
      yield* draw.character(parameter & 0x3f);
    } else if (code === 0x2d) {
      // the sign's serial number, a digit for each BCD nibble
      serial = hexBytes(this.c.t.serial || '');
      for (i = 0; i < serial.length; i++) {
        yield* draw.character(serial[i] >> 4 | 0x30);
        yield* draw.character(serial[i] & 0x0f | 0x30);
      }
    } else if (code === 0x45) {
      // the fault code, taken and cleared
      fault = this.fault;
      this.fault = 0;
      yield* draw.character(fault);
    } else if (code === 0x43) {
      // the next three characters step down in reverse bold, in the first 32 columns
      tall = line.tall;
      line.tall = false;
      Sign.margins(line, 0x0a);
      Sign.setFont(line, 0x8b);
      if (this.ops) this.ops.push({op: 'font', t: this.now[k], k: k, byte: 0x8b, rows: line.mask()});
      forms = [0x27, 0x24, 0x01];
      for (i = 0; i < forms.length; i++) {
        Sign.format(line, forms[i]);
        yield* draw.character(draw.take());
      }
      yield* draw(filled(floorDiv(line.free, 2), 0), false);
      Sign.margins(line, 0x40);
      line.tall = tall;
    }
  };

  function sameList(a, b) {
    if (a.length !== b.length) return false;
    for (var i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
  }

  // ([[tick, lamps, flashing rows, border, red, green]], end): each change as the sign plays a
  // message, and the tick it ends on; runs longer than `limit` ticks stop there
  function signFrames(payload, c, limit) {
    var p = c.p, sign = new Sign(c), streams = routeToLines(playElements(payload, c.settings, c.t), c.t);
    var runs = [sign.run(0, streams[0]), sign.run(1, streams[1])];
    var at = [0, 0], state = ['go', 'go'], busy = [-1, -1], owed = [0, 0], frames = [];
    var waiting, release, k, next, request, logged;

    function snapshot(tick) {
      var lit = sign.lit.slice(), flashing = [], masks = [0, 1].map(function (j) {
        return sign.lines[j].blink ? sign.lines[j].mask() : 0;
      }), x, last, frame;
      for (x = 0; x < WIDTH; x++) flashing.push((sign.flashing[0][x] ? masks[0] : 0) + (sign.flashing[1][x] ? masks[1] : 0));
      frame = [tick, lit, flashing, sign.border, sign.hues[0].slice(), sign.hues[1].slice()];
      if (frames.length) {
        last = frames[frames.length - 1];
        if (last[3] === frame[3] && sameList(last[1], lit) && sameList(last[2], flashing) && sameList(last[4], frame[4]) &&
            sameList(last[5], frame[5])) return;
        if (last[0] === tick) {
          frames[frames.length - 1] = frame;
          return;
        }
      }
      frames.push(frame);
    }

    while (state.indexOf('go') >= 0 || state.indexOf('sync') >= 0) {
      if (state.indexOf('go') < 0) {
        // a display frame answers a line waiting on 00; one left waiting by a line that
        // has finished goes on when the next message starts it
        waiting = Math.max(state[0] === 'sync' ? at[0] : -Infinity, state[1] === 'sync' ? at[1] : -Infinity);
        release = -floorDiv(-waiting, p.frame_ticks) * p.frame_ticks;
        for (k = 0; k < 2; k++) {
          if (state[k] === 'sync') {
            state[k] = 'go';
            at[k] = release;
          }
        }
      }
      k = state[0] === 'go' && (state[1] !== 'go' || at[0] <= at[1]) ? 0 : 1;
      sign.now[k] = at[k];
      logged = sign.ops ? sign.ops.length : 0;
      next = runs[k].next();
      request = next.done ? null : next.value;
      // each write, with the tick of the frame the lamps last changed on before it
      for (; sign.ops && logged < sign.ops.length; logged++) sign.ops[logged].after = frames.length ? frames[frames.length - 1][0] : 0;
      snapshot(at[k]);
      if (request === null) {
        state[k] = 'done';
      } else if (request[0] === 'work') {
        owed[k] += request[1] / (busy[1 - k] > at[k] ? p.shares[k] : 1);
        at[k] += Math.trunc(owed[k]);
        owed[k] -= Math.trunc(owed[k]);
        busy[k] = at[k];
      } else if (request[0] === 'wait') {
        at[k] += request[1];
      } else if (request[0] === 'until') {
        at[k] = Math.max(at[k], request[1]);
      } else {
        state[k] = 'sync';
      }
      if (limit && Math.max(at[0], at[1]) > limit) break;
    }
    return [frames, Math.max(at[0], at[1]), sign.ops];
  }

  function bordered(lamps, phase, c) {
    var dashes = hexBytes(c.t.border_dashes), sides = hexBytes(c.t.border_sides), out = lamps.slice();
    var edges = [[0, phase], [14, phase ? 5 - phase : 0]], e, y, at, x, left, i;
    for (e = 0; e < 2; e++) {
      y = edges[e][0];
      at = edges[e][1];
      for (x = 0; x < WIDTH; x++) out[x] = out[x] & ~(1 << y) | (dashes[at + (x >> 3)] >> (x & 7) & 1) << y;
    }
    left = [0, 2, 4, 1, 3][phase];
    for (x = 0; x < WIDTH; x++) if (x < 8 || x >= WIDTH - 8) out[x] &= ~0x3ffe;
    for (i = 0; i < 13; i++) {
      out[0] |= (sides[left + i] & 1) << i + 1;
      out[WIDTH - 1] |= (sides[5 - left + i] >> 7) << i + 1;
    }
    return out;
  }

  // The lamps `bordered` draws: rows 0 and 14, and the end bytes' rows between
  var BORDER_LAMPS = filled(WIDTH, 0).map(function (v, x) { return 1 | 1 << 14 | (x < 8 || x >= WIDTH - 8 ? 0x3ffe : 0); });

  function without(list, mask) {
    return list.map(function (v, x) { return v & ~mask[x]; });
  }

  // [[tick, lamps, red, green]], flashing lamps dark for the second half of every blink, and the
  // chasing border drawn over what it lights, moving two places each step, in no colour
  function shownFrames(frames, end, c) {
    var p = c.p, out = [], n, tick, lamps, flashing, border, stop, t, shown, tinted, nxt, steps, any, dark;
    for (n = 0; n < frames.length; n++) {
      tick = frames[n][0];
      lamps = frames[n][1];
      flashing = frames[n][2];
      border = frames[n][3];
      stop = n + 1 < frames.length ? frames[n + 1][0] : end;
      any = flashing.some(function (f) { return f; });
      t = tick;
      for (;;) {
        dark = floorDiv(t, p.blink_ticks) & 1;
        shown = dark ? without(lamps, flashing) : lamps;
        tinted = [frames[n][4], frames[n][5]].map(function (hue) { return dark ? without(hue, flashing) : hue; });
        nxt = stop;
        if (any) nxt = Math.min(nxt, (floorDiv(t, p.blink_ticks) + 1) * p.blink_ticks);
        if (border !== null) {
          steps = t < border + p.frame_ticks ? 0 : 1 + floorDiv(t - border - p.frame_ticks, p.border_ticks);
          shown = bordered(shown, (3 + 2 * steps) % 5, c);
          tinted = tinted.map(function (hue) { return without(hue, BORDER_LAMPS); });
          nxt = Math.min(nxt, border + p.frame_ticks + steps * p.border_ticks);
        }
        out.push([t, shown, tinted[0], tinted[1]]);
        if (nxt >= stop) break;
        t = nxt;
      }
    }
    return out;
  }

  // [[milliseconds, lamps, red, green]], each display held until the next
  function program(shown, end, c) {
    var out = [], n, stop;
    for (n = 0; n < shown.length; n++) {
      stop = n + 1 < shown.length ? shown[n + 1][0] : end;
      if (stop > shown[n][0]) {
        out.push([(stop - shown[n][0]) * c.p.tick_ms].concat(shown[n].slice(1).map(function (plane) { return plane.slice(); })));
      }
    }
    return out;
  }

  // What the sign shows as it plays `payload` (bytes, or hex), with `params` and `tables` as
  // sign_parameters and sign_tables give them; `options.ops` keeps the op-log, and a run
  // stops past `options.limit` ticks
  function play(payload, params, tables, options) {
    options = options || {};
    var c = context(params, tables, options.ops), bytes = typeof payload === 'string' ? hexBytes(payload) : payload;
    var got = signFrames(bytes, c, options.limit), shown = shownFrames(got[0], got[1], c);
    return {frames: got[0], end: got[1], shown: shown, program: program(shown, got[1], c), ops: got[2]};
  }

  return {play: play, playElements: playElements, routeToLines: routeToLines, templateBytes: templateBytes,
          hexBytes: hexBytes, context: context, signFrames: signFrames, shownFrames: shownFrames,
          bordered: bordered, program: program, Sign: Sign, SignLine: SignLine, WIDTH: WIDTH, ROWS: ROWS};
})();

if (typeof module !== 'undefined') module.exports = SilentRadioSign;
