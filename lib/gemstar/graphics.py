"""Type B 0x1E graphics objects: Guide Plus+ ad screens, their "Click for More" detail pages
(2003) and PNG screens (2007), plus the 0x1B ad schedule.

Object transport (each 0x1E message is one part):
    after the header expression (`3F xx 00 0A`, see guide.object_header) come
        id(2) width(2) height(2) 80 total_len(3) part_len+7(2) 0B id(2) 00 offset(2) 00  then data
    as sent, width/height and all coordinates are transposed (y before x); drawn below with x across.
    2003 ads come in threes: N = picture (168 wide), N+1 = stub, N+2 = detail page (352 x 390).

Command stream inside an object:
    07 nn c1c2c3 000000      colour pair nn: two 12-bit RGB colours (fg, bg)
    06 nn 09 y0 x0 y1 x1     fill rectangle with pair nn's bg colour
    06 nn 05 flag y x len    bitmap (`len` data bytes follow)
    then text elements       02 style w1 w2 [y0 x0 y1 x1 | FFFF] len text
                             (no box: y = w1, w2 & 0x8000 = centred; ESC V..W bold, ESC C..D italic)

Bitmap data: byte0 bits 0-1 depth (0 = 1 bpp, 2 = 4 bpp); byte1 high nibble = colours - 1, low nibble =
palette base (pixel v uses entry v - base); then the 12-bit palette. 1 bpp adds [bytes/row][rows].
Pixels are run-length coded: c < L copies c+1 literal bytes, else the next byte repeats (c & 0x7F) + 3 times.
Usually L = 0x40 and the first 64 decoded bytes are a preamble; 1 bpp rows are usually XOR-delta coded.
The flags selecting L / preamble / XOR are not known; they are chosen per bitmap by fit (declared size,
layout space, image continuity) and every choice is reported in the notes.
From 2007 a bitmap may instead be a PNG file, which is extracted byte-exact (chunk CRCs checked).
"""

import os
import re
import struct
import zlib

import numpy as np

from lib.gemstar.guide import object_header

SPECIAL = {0x80: '℠', 0x81: '®', 0x82: '©', 0x83: '™', 0x85: '◆', 0x86: '•',
           0x88: '►', 0xA0: ' ', 0xA3: '¢', 0xAE: '—'}
SIZES = {0: (17, True), 1: (14, False), 2: (17, True), 3: (12, False)}


# ---- object reassembly ------------------------------------------------------------------------

def load_objects(messages):
    objs = {}
    for m in messages:
        b = m['data']
        if b[0] != 0x1E:
            continue
        k = object_header(b)
        if k is None or k + 19 > len(b):
            continue
        oid = struct.unpack_from('<H', b, k)[0]
        w, h = struct.unpack_from('<HH', b, k + 2)
        total = b[k + 7] | b[k + 8] << 8 | b[k + 9] << 16
        plen = struct.unpack_from('<H', b, k + 10)[0] - 7
        off = struct.unpack_from('<H', b, k + 16)[0]
        if total > 1 << 20:
            continue
        o = objs.setdefault(oid, dict(id=oid, w=w, h=h, total=total, parts=b[6], buf=bytearray(total), have=set()))
        d = b[k + 19:k + 19 + plen]
        o['buf'][off:off + len(d)] = d
        o['have'].add(b[5])
    for o in objs.values():
        o['complete'] = len(o['have']) == o['parts']
    return objs


def nib(bs):
    for x in bs:
        yield x >> 4
        yield x & 15


def pal12(b, n):
    v = list(nib(b))
    return [tuple(17 * v[3 * i + k] for k in range(3)) for i in range(n)]


def parse(buf):
    i = 0
    cmds = []
    while i < len(buf):
        op = buf[i]
        if op == 0x07 and i + 8 <= len(buf):
            n = list(nib(buf[i + 2:i + 5]))
            cmds.append(('pair', buf[i + 1], tuple(17 * x for x in n[:3]), tuple(17 * x for x in n[3:6])))
            i += 8
        elif op == 0x06 and i + 11 <= len(buf) and buf[i + 2] == 0x09:
            cmds.append(('rect', buf[i + 1]) + struct.unpack_from('<4H', buf, i + 3))
            i += 11
        elif op == 0x06 and i + 10 <= len(buf) and buf[i + 2] == 0x05:
            y, x, ln = struct.unpack_from('<3H', buf, i + 4)
            cmds.append(('bitmap', buf[i + 1], buf[i + 3], y, x, bytes(buf[i + 10:i + 10 + ln])))
            i += 10 + ln
        else:
            break
    return cmds, bytes(buf[i:])


def extract_pngs(buf):
    """Every complete PNG file inside an object (all chunk CRCs valid, ends with IEND)."""
    out = []
    pos = 0
    buf = bytes(buf)
    while True:
        i = buf.find(b'\x89PNG\r\n\x1a\n', pos)
        if i < 0:
            return out
        p = i + 8
        ok = False
        while p + 12 <= len(buf):
            ln, typ = struct.unpack('>I4s', buf[p:p + 8])
            if p + 12 + ln > len(buf):
                break
            if zlib.crc32(buf[p + 4:p + 8 + ln]) & 0xFFFFFFFF != struct.unpack('>I', buf[p + 8 + ln:p + 12 + ln])[0]:
                break
            p += 12 + ln
            if typ == b'IEND':
                ok = True
                break
        if ok:
            out.append(buf[i:p])
        pos = i + 8


# ---- bitmaps ----------------------------------------------------------------------------------

def unpack(data, need=None, lit_max=0x40):
    out = bytearray()
    i = 0
    while i < len(data) and (need is None or len(out) < need):
        c = data[i]
        i += 1
        if c < lit_max:
            out += data[i:i + c + 1]
            i += c + 1
        else:
            if i >= len(data):
                break
            out += bytes([data[i]]) * ((c & 0x7F) + 3)
            i += 1
    return bytes(out[:need] if need else out)


def _rough(x):
    x = x.astype(np.int16)
    return np.abs(np.diff(x, axis=0)).mean() + np.abs(np.diff(x, axis=1)).mean()


def _vfill(z):
    """Share of ink pixels with ink directly above or below (low for XOR data shown raw)."""
    z = z if z.mean() < 0.5 else 1 - z
    ink = z.sum()
    if ink == 0:
        return 0.0
    up = np.zeros_like(z)
    up[1:] = z[:-1]
    dn = np.zeros_like(z)
    dn[:-1] = z[1:]
    return float(((z == 1) & ((up == 1) | (dn == 1))).sum() / ink)


def decode_bitmap(d, wgap, hgap=None):
    """Bitmap command data -> (RGB array [h, w, 3] or None, note). wgap/hgap = room in the layout."""
    if len(d) < 4:
        return None, 'short bitmap'
    bpp = 1 << (d[0] & 3)
    ncol = (d[1] >> 4) + 1
    base = d[1] & 0x0F
    pb = (ncol * 3 + 1) // 2
    P = np.array(pal12(d[2:2 + pb], ncol) + [(255, 0, 255)] * (16 - ncol), np.uint8)
    if bpp == 1:
        s, h = d[2 + pb], d[3 + pb]
        if s == 0:
            return None, 'empty bitmap'
        stream = d[4 + pb:]
        exp = s * h
        full = {lm: unpack(stream, lit_max=lm) for lm in (0x40, 0x80)}
        lm, sk = min([(lm, sk) for lm in (0x40, 0x80) for sk in (64, 0)],
                     key=lambda c: (abs(len(full[c[0]]) - c[1] - exp) if h != 127 else 0, c[1] != 64, c[0] != 0x40))
        a = np.frombuffer(full[lm][sk:], np.uint8)
        note = f'1bpp L=0x{lm:02X} preamble={sk}'
        if h == 127:
            h = max(1, len(a) // s)
        buf = np.zeros(s * h, np.uint8)
        n = min(len(a), s * h)
        buf[:n] = a[:n]
        raw = np.unpackbits(buf.reshape(h, s), axis=1)
        acc = np.bitwise_xor.accumulate(raw, axis=0)
        nreal = max(2, min(h, n // s))
        if _vfill(raw[:nreal]) > _vfill(acc[:nreal]):
            v = raw
            note += ' raw-rows'
        else:
            v = acc
        return P[(v.astype(int) - base) % 16], note
    if bpp != 4:
        return None, f'{bpp} bpp not supported'
    s = max(1, (wgap + 1) // 2)
    cand = {}
    for lm in (0x80, 0x40):
        full = unpack(d[2 + pb:], lit_max=lm)
        a = np.frombuffer(full[64:], np.uint8)
        h = len(a) // s
        if h < 2:
            continue
        m = a[:h * s].reshape(h, s)
        v = np.stack([m >> 4, m & 15], 2).reshape(h, -1)
        rgb = P[(v.astype(int) - base) % 16]
        cand[lm] = (len(full), _rough(rgb), rgb)
    if not cand:
        return None, 'empty bitmap'
    if len(cand) == 2 and cand[0x40][0] != cand[0x80][0]:
        la, lb = cand[0x80][0], cand[0x40][0]
        exp = s * hgap + 64 if hgap else None
        if exp and abs(la - lb) / max(la, lb) > 0.15:
            lm = min(cand, key=lambda k: abs(cand[k][0] - exp))
            why = 'length'
        else:
            lm = min(cand, key=lambda k: cand[k][1])
            why = 'smoothness'
        return cand[lm][2], f'4bpp L=0x{lm:02X} by {why}'
    return next(iter(cand.values()))[2], ''


# ---- text -------------------------------------------------------------------------------------

def text_elements(rest):
    i = 0
    els = []
    while i < len(rest) - 10:
        if rest[i] != 0x02:
            i += 1
            continue
        style = rest[i + 1]
        w1, w2, w3 = struct.unpack_from('<3H', rest, i + 2)
        if w3 == 0xFFFF:
            box = None
            ln = struct.unpack_from('<H', rest, i + 8)[0]
            p = i + 10
        else:
            y0, x0, y1, x1, ln = struct.unpack_from('<5H', rest, i + 6)
            box = (y0, x0, y1, x1)
            p = i + 16
        if style > 7 or ln > 3000 or p + ln > len(rest) + 2:
            i += 1
            continue
        els.append(dict(style=style, y=w1, centered=bool(w2 & 0x8000), box=box, raw=rest[p:p + ln]))
        i = p + ln
    return els


def clean_text(raw):
    raw = re.sub(rb'\x1b[A-Za-z]', b'', raw)
    s = ''.join(SPECIAL.get(c, chr(c)) if (c >= 0x20 or c == 0x0A) and c != 0x7F else '' for c in raw)
    return s.strip('\x00 ')


def _font(size, bold):
    from PIL import ImageFont
    for name in (('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'),
                 '/usr/share/fonts/truetype/dejavu/' + ('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'),
                 ('Arial Bold.ttf' if bold else 'Arial.ttf'), ('arialbd.ttf' if bold else 'arial.ttf'),
                 '/System/Library/Fonts/Supplemental/' + ('Arial Bold.ttf' if bold else 'Arial.ttf')):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def _wrap(draw, text, font, width):
    lines = []
    for para in text.split('\n'):
        cur = ''
        for w in para.split(' '):
            t = (cur + ' ' + w).strip()
            if draw.textlength(t, font=font) <= width or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


# ---- rendering --------------------------------------------------------------------------------

def render(o, with_text=False):
    """Draw an object. Returns (PIL image, [text strings], [notes])."""
    from PIL import Image, ImageDraw
    cmds, rest = parse(o['buf'])
    W, H = o['h'], o['w']                      # transmitted transposed
    im = Image.new('RGB', (W, H))
    dr = ImageDraw.Draw(im)
    pairs = {c[1]: (c[2], c[3]) for c in cmds if c[0] == 'pair'}
    base = pairs.get(0x80, ((238, 238, 238), (17, 17, 17)))
    if with_text:
        dr.rectangle([0, 0, W, H], fill=base[1])
    regions, notes = [], []
    bms = [c for c in cmds if c[0] == 'bitmap']
    for c in cmds:
        if c[0] == 'rect':
            fg, bg = pairs.get(c[1], base)
            y0, x0, y1, x1 = c[2:6]
            dr.rectangle([x0, y0, x1 - 1, y1 - 1], fill=bg)
            regions.append((y0, y1, fg))
        elif c[0] == 'bitmap':
            y, x = c[3], c[4]
            right = [b[4] for b in bms if b[3] == y and b[4] > x]
            below = [b[3] for b in bms if b[4] == x and b[3] > y]
            rgb, note = decode_bitmap(c[5], (min(right) if right else W - 4) - x, (min(below) if below else H - 4) - y)
            if note:
                notes.append(f'bitmap@({x},{y}): {note}')
            if rgb is not None:
                b = Image.fromarray(rgb)
                im.paste(b.crop((0, 0, min(b.width, W - x), min(b.height, H - y))), (x, y))
    texts = []
    for e in text_elements(rest):
        txt = clean_text(e['raw'])
        texts.append(txt)
        if not with_text:
            continue
        size, bold = SIZES.get(e['style'], (12, False))
        if b'\x1bV' in e['raw'] and e['style'] == 3:
            bold = True
        font = _font(size, bold)
        if e['box']:
            y0, x0, y1, x1 = e['box']
            y = y0
        else:
            x0, x1, y = 6, W - 6, e['y']
        col = next((fg for a, b_, fg in regions if a <= y < b_), base[0])
        for line in _wrap(dr, txt, font, x1 - x0):
            if y > H:
                break
            xx = x0 + ((x1 - x0) - dr.textlength(line, font=font)) / 2 if (e['centered'] or not e['box']) else x0
            dr.text((xx, y), line, font=font, fill=col)
            y += size + 3
    return im, texts, notes


def schedule(messages):
    """0x1B ad schedule: name, the three 0x1E object ids, and timed slots (time base not identified)."""
    out = {}
    for m in messages:
        b = m['data']
        if b[0] != 0x1B:
            continue
        k = object_header(b)
        if k is None or k + 28 > len(b):
            continue
        # after the header: ... time(4) at +10, the three object ids at +15, name length at +27
        ids = struct.unpack_from('<3H', b, k + 15)
        nl = b[k + 27]
        name = b[k + 28:k + 28 + nl].decode('latin1')
        rest = b[k + 28 + nl:]
        times = []
        if len(rest) > 4 and rest[0] == 0x7F:
            p = 4
            for _ in range(rest[2]):
                if p + 6 > len(rest):
                    break
                times.append(rest[p + 1:p + 5][::-1].hex())
                p += 6
        e = out.setdefault(ids[0], dict(name=name, objects=[f'0x{x:04x}' for x in ids],
                                        header_time=b[k + 10:k + 14][::-1].hex(), slots=set()))
        e['slots'].update(times)
    for e in out.values():
        e['slots'] = sorted(e['slots'])
    return out


def export(messages, outdir):
    """Write every renderable 0x1E object to `outdir`. Returns the index list (also saved as index.json)."""
    import json
    from PIL import Image
    objs = load_objects(messages)
    if not objs:
        return []
    os.makedirs(outdir, exist_ok=True)
    sched = schedule(messages)
    index, tiles = [], []
    for oid in sorted(objs):
        o = objs[oid]
        entry = dict(object=f'0x{oid:04x}', size=f"{o['h']}x{o['w']}", complete=o['complete'],
                     parts=f"{len(o['have'])}/{o['parts']}")
        if not o['complete']:
            index.append(entry)
            continue
        pngs = extract_pngs(o['buf'])
        for k, png in enumerate(pngs):
            name = f'object_{oid:04x}' + (f'_{k}' if k else '') + '.png'
            open(os.path.join(outdir, name), 'wb').write(png)
            entry.setdefault('png', []).append(name)
        if pngs:
            index.append(entry)
            continue
        is_page = o['w'] == 390            # 2003 detail page (352 x 390 drawn)
        is_picture = o['h'] == 168          # 2003 ad picture
        if not (is_page or is_picture):
            cmds, rest = parse(o['buf'])
            if not cmds:
                index.append(entry)
                continue
        im, texts, notes = render(o, with_text=is_page)
        kind = 'page' if is_page else 'picture' if is_picture else 'object'
        name = f'{kind}_{oid:04x}.png'
        (im if is_page else im.resize((im.width * 2, im.height * 2), Image.NEAREST)).save(os.path.join(outdir, name))
        entry.update(kind=kind, image=name, text=texts, notes=notes)
        if oid in sched:
            entry['schedule'] = sched[oid]
        index.append(entry)
        if is_picture:
            tiles.append(im)
    # pair 2003 pictures with their detail pages (N and N+2)
    byid = {e['object']: e for e in index}
    for e in index:
        if e.get('kind') == 'picture':
            page = byid.get(f"0x{int(e['object'], 16) + 2:04x}")
            if page and page.get('kind') == 'page':
                e['detail_page'] = page['image']
                e['detail_text'] = page['text']
    if tiles:
        cols = 4
        tw = max(t.width for t in tiles) * 2 + 10
        th = max(t.height for t in tiles) * 2 + 10
        sheet = Image.new('RGB', (cols * tw, ((len(tiles) + cols - 1) // cols) * th), (35, 35, 35))
        for k, t in enumerate(tiles):
            sheet.paste(t.resize((t.width * 2, t.height * 2), Image.NEAREST), ((k % cols) * tw, (k // cols) * th))
        sheet.save(os.path.join(outdir, '_all_pictures.png'))
    json.dump(index, open(os.path.join(outdir, 'index.json'), 'w'), indent=1, ensure_ascii=False)
    return index
