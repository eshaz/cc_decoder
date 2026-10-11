#!/usr/bin/env python3
"""Dump a Gemstar Guide Plus+ database (.dbm) with Nova2, Gemstar's own PC receiver simulator.

Nova2 (nova2.0.13.0_pc.exe, 1,265,664 bytes, once offered on the Prevue Guide web site) is a Win32
build of the Guide Plus+ receiver software that still carries its COFF symbol table. This script loads
the user's copy of the EXE into a 32-bit CPU emulator (Unicorn), looks the routines up by name in that
symbol table, and calls the receiver's own database reader and logo decoder. Nothing from Nova2 is
distributed with this repository.

    pip install unicorn pefile pillow
    python3 tools/nova2/nova2_dump.py listings latest.dbm OUTDIR --exe nova2.0.13.0_pc.exe
    python3 tools/nova2/nova2_dump.py logos    latest.dbm OUTDIR --exe nova2.0.13.0_pc.exe

`listings` writes nova_listings.json / .txt (every channel and airing, with titles, descriptions and
cast); `logos` writes one PNG per distinct channel logo (TransMap images) plus a contact sheet.
The EXE can also be given with the NOVA_EXE environment variable.

Call sequence used (see docs/gemstar.md):
    _dbmStart()  - must run first: it fills the slot-duration table
    _DBMLoadDB(buf)
    _dbmGetChannelCount(&n) / _dbmGetStationID(i) / _dbmGetStationInfo(sid, info)
        info +12 call letters, +17 name, +0x1C logo pointer, +0x2C channel number
    _dbmInitShowRetrieveHandle(sid, time, 0x708, h) then _dbmRetrieveNextShow(h, show) until the window ends
        show +4 start (time_t), +8 duration (s), +12 show id (0xFFFF = empty slot)
    _dbmRetrieveShowTitle(show, out) (+8)  /  _dbmRetrieveShowDescription(show, out) (+0x40, cast +0x240)
    _TransMapDescriptionDecompress(...) for logos

Public domain / Unlicense, as the rest of this repository.
"""

import argparse
import collections
import json
import os
import struct
import time

EXE_SIZE = 1265664
STACK_TOP = 0x7F0F0000
RETURN = 0x7FFF0000              # HLT sentinel used as the return address
DBM_BUFFER_PTR = 0x515B18        # global holding Nova2's database work buffer


def load_symbols(data, pe):
    """name -> virtual address, from the COFF symbol table left in the EXE.
    The linker wrote the values relative to the section start minus 0x1000; the last definition of a
    name wins (static functions repeat across object files, none of the ones used here do)."""
    fh = pe.FILE_HEADER
    ptr, n = fh.PointerToSymbolTable, fh.NumberOfSymbols
    if not ptr or not n:
        raise SystemExit('this EXE has no COFF symbol table; nova2.0.13.0_pc.exe is needed')
    strtab = ptr + 18 * n
    sections = pe.sections
    base = pe.OPTIONAL_HEADER.ImageBase
    out = {}
    i = 0
    while i < n:
        e = data[ptr + 18 * i:ptr + 18 * i + 18]
        if e[:4] == b'\0\0\0\0':
            o = strtab + struct.unpack_from('<I', e, 4)[0]
            name = data[o:data.index(b'\0', o)].decode('latin1')
        else:
            name = e[:8].rstrip(b'\0').decode('latin1')
        value, sec, _, _, aux = struct.unpack_from('<IhHBB', e, 8)
        if 0 < sec <= len(sections):
            out[name] = base + sections[sec - 1].VirtualAddress + value - 0x1000
        i += 1 + aux
    return out


class Nova:
    """Nova2 loaded into a Unicorn 32-bit address space, with Windows imports stubbed out and the
    receiver's heap (UIMalloc/UIFree) replaced by a bump allocator."""

    def __init__(self, exe):
        try:
            import pefile
            from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
            from unicorn import x86_const as X
        except ImportError as e:
            raise SystemExit(f'{e}: pip install unicorn pefile')
        data = open(exe, 'rb').read()
        if data[:2] != b'MZ' or len(data) != EXE_SIZE:
            raise SystemExit(f'{exe} is not nova2.0.13.0_pc.exe ({EXE_SIZE:,} bytes)')
        pe = pefile.PE(data=data)
        self.X = X
        self.sym = load_symbols(data, pe)
        img = pe.get_memory_mapped_image()
        base = pe.OPTIONAL_HEADER.ImageBase
        mu = self.mu = Uc(UC_ARCH_X86, UC_MODE_32)
        mu.mem_map(base, 0x200000)
        mu.mem_write(base, img[:0x200000])
        mu.mem_map(0x10000000, 0x4000000)       # heap
        mu.mem_map(0x7F000000, 0x100000)        # stack
        mu.mem_map(RETURN, 0x1000)
        mu.mem_write(RETURN, b'\xf4' * 16)
        self.heap = 0x11000000

        # Every imported function returns 0 (allocators return heap memory).
        stubs = {}
        mu.mem_map(0x7FFE0000, 0x10000)
        k = 0
        for entry in getattr(pe, 'DIRECTORY_ENTRY_IMPORT', []):
            for imp in entry.imports:
                addr = 0x7FFE0000 + k * 16
                mu.mem_write(addr, b'\xc3')
                mu.mem_write(imp.address, struct.pack('<I', addr))
                stubs[addr] = (imp.name or b'?').decode('latin1')
                k += 1

        def on_import(mu, addr, size, _):
            name = stubs.get(addr, '?')
            esp = mu.reg_read(X.UC_X86_REG_ESP)
            ret = 0
            if name in ('HeapAlloc', 'GlobalAlloc', 'LocalAlloc', 'VirtualAlloc'):
                n = struct.unpack('<I', mu.mem_read(esp + (12 if name == 'HeapAlloc' else 8), 4))[0]
                ret = self.alloc(n)
            elif name in ('GetProcessHeap', 'HeapCreate'):
                ret = 1
            mu.reg_write(X.UC_X86_REG_EAX, ret)
        mu.hook_add(UC_HOOK_CODE, on_import, begin=0x7FFE0000, end=0x7FFEFFFF)

        def ret_with(value_fn):
            def hook(mu, addr, size, _):
                esp = mu.reg_read(X.UC_X86_REG_ESP)
                ret, arg = struct.unpack('<II', mu.mem_read(esp, 8))
                mu.reg_write(X.UC_X86_REG_EAX, value_fn(arg))
                mu.reg_write(X.UC_X86_REG_ESP, esp + 4)
                mu.reg_write(X.UC_X86_REG_EIP, ret)
            return hook
        for name, fn in (('_UIMalloc', lambda n: self.alloc(max(n, 1))), ('_UIFree', lambda p: 0)):
            a = self.sym[name]
            mu.hook_add(UC_HOOK_CODE, ret_with(fn), begin=a, end=a)
        mu.mem_write(self.sym['_OSDebugMessage'], b'\xc3')     # debug logger: return at once

        mu.mem_write(DBM_BUFFER_PTR, struct.pack('<I', self.alloc(0x300000)))
        self.call('_dbmStart', [])

    def alloc(self, n):
        p = self.heap
        self.heap += (n + 15) & ~15
        return p

    def call(self, fn, args, budget=50_000_000):
        mu, X = self.mu, self.X
        sp = STACK_TOP
        for a in reversed(args):
            sp -= 4
            mu.mem_write(sp, struct.pack('<I', a & 0xFFFFFFFF))
        sp -= 4
        mu.mem_write(sp, struct.pack('<I', RETURN))
        mu.reg_write(X.UC_X86_REG_ESP, sp)
        mu.reg_write(X.UC_X86_REG_EBP, sp)
        mu.emu_start(self.sym[fn], RETURN, count=budget)
        return mu.reg_read(X.UC_X86_REG_EAX)

    def put(self, data):
        p = self.alloc(len(data) + 16)
        self.mu.mem_write(p, bytes(data))
        return p

    def get(self, p, n):
        return bytes(self.mu.mem_read(p, n))

    def cstr(self, p, n=4096):
        return self.get(p, n).split(b'\0')[0].decode('latin1')

    def zero(self, p, n):
        self.mu.mem_write(p, bytes(n))

    def u32(self, p):
        return struct.unpack('<I', self.get(p, 4))[0]

    # ---- database ----
    def load(self, dbm):
        raw = open(dbm, 'rb').read()
        if self.call('_DBMLoadDB', [self.put(raw)]) != 0:
            raise SystemExit(f'Nova2 rejected {dbm}')
        return raw

    def stations(self):
        c = self.alloc(4)
        self.call('_dbmGetChannelCount', [c])
        info = self.alloc(0x30)
        for i in range(self.u32(c)):
            sid = self.call('_dbmGetStationID', [i])
            self.call('_dbmGetStationInfo', [sid, info])
            b = self.get(info, 0x30)
            yield dict(index=i, station_id=sid, call=b[12:17].split(b'\0')[0].decode('latin1').strip(),
                       name=b[17:28].split(b'\0')[0].decode('latin1').strip(),
                       channel=struct.unpack_from('<I', b, 0x2C)[0], logo=struct.unpack_from('<I', b, 0x1C)[0])


def dump_listings(nova, dbm, outdir):
    raw = nova.load(dbm)
    t0, span = struct.unpack_from('>I', raw, 16)[0], struct.unpack_from('>I', raw, 20)[0]
    show, title, desc, handle = nova.alloc(0x40), nova.alloc(0x400), nova.alloc(0x400), nova.alloc(0x20)
    channels, shows = [], []
    utc = lambda t: time.strftime('%Y-%m-%d %H:%M', time.gmtime(t))
    for ch in nova.stations():
        ch = {k: v for k, v in ch.items() if k != 'logo'}
        channels.append(ch)
        nova.call('_dbmInitShowRetrieveHandle', [ch['station_id'], t0, 0x708, handle])
        last = None
        for _ in range(2000):
            nova.zero(show, 0x40)
            nova.call('_dbmRetrieveNextShow', [handle, show])
            r = nova.get(show, 0x30)
            start, dur = struct.unpack_from('<II', r, 4)
            if start >= t0 + span or start == last:
                break
            last = start
            if r[12:14] == b'\xff\xff':
                continue
            nova.zero(title, 0x400)
            nova.zero(desc, 0x400)
            nova.call('_dbmRetrieveShowTitle', [show, title])
            nova.call('_dbmRetrieveShowDescription', [show, desc])
            shows.append(dict(channel=ch['channel'], call=ch['call'], start_utc=utc(start), minutes=dur // 60,
                              show_id=struct.unpack_from('<H', r, 12)[0], title=nova.cstr(title + 8),
                              description=nova.cstr(desc + 0x40), cast=nova.cstr(desc + 0x240)))
        if len(channels) % 50 == 0:
            print(f'{len(channels)} stations, {len(shows)} shows', flush=True)
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, 'nova_listings.json'), 'w') as f:
        json.dump(dict(window_start_utc=utc(t0), window_hours=span // 3600, channels=channels, shows=shows), f,
                  indent=1, ensure_ascii=False)
    with open(os.path.join(outdir, 'nova_listings.txt'), 'w') as f:
        f.write(f'{os.path.basename(dbm)}: {len(channels)} channels, {len(shows)} shows, '
                f'{utc(t0)} UTC + {span // 3600} h\n')
        cur = None
        for x in shows:
            if x['call'] != cur:
                cur = x['call']
                f.write(f"\n===== ch {x['channel']}  {x['call']}\n")
            f.write(f"{x['start_utc']}Z {x['minutes']:4d}m  {x['title']}\n")
            if x['description']:
                f.write(f"{'':24}{x['description']}\n")
            if x['cast']:
                f.write(f"{'':24}[{x['cast']}]\n")
    print(f'{len(shows)} shows on {len(channels)} channels -> {outdir}')


def decode_logo(nova, ptr):
    """TransMap: byte0 = type (bits 6-7) | colour count, byte1 = width/2, byte2 = height/2,
    colour count x 15-bit RGB, then compressed pixels. Returns a PIL image or None."""
    from PIL import Image
    bm, bpp, other, cmap, ncol = (nova.alloc(n) for n in (0x40, 4, 4, 0x100, 4))
    for a in (bpp, other, ncol):
        nova.zero(a, 4)
    if not nova.call('_TransMapDescriptionDecompress', [ptr, bm, bpp, bm + 6, bm + 8, other, cmap, 0x40, ncol]) & 0xFFFF:
        return None
    b = nova.get(bm, 0x1C)
    w, h = struct.unpack_from('<hh', b, 6)
    depth = nova.u32(bpp) & 0xFFFF
    n = nova.u32(ncol) & 0xFFFF
    cm = nova.get(cmap, 0xC0)
    pal = [tuple(cm[3 * i:3 * i + 3]) for i in range(max(n, 1))]
    if w <= 0 or h <= 0 or depth not in (1, 2, 4, 8):
        return None
    stride = (w * depth + 7) // 8
    pix = nova.get(struct.unpack('<I', b[:4])[0], stride * h)
    per, mask = 8 // depth, (1 << depth) - 1
    im = Image.new('RGB', (w, h))
    for y in range(h):
        for x in range(w):
            v = (pix[y * stride + x // per] >> ((per - 1 - x % per) * depth)) & mask
            im.putpixel((x, y), pal[v] if v < len(pal) else (255, 0, 255))
    return im


def dump_logos(nova, dbm, outdir):
    from PIL import Image, ImageDraw
    nova.load(dbm)
    users = collections.OrderedDict()
    for ch in nova.stations():
        if ch['logo']:
            users.setdefault(ch['logo'], []).append(ch['call'])
    os.makedirs(outdir, exist_ok=True)
    tiles, names = [], set()
    with open(os.path.join(outdir, 'logos_index.txt'), 'w') as f:
        for ptr, calls in users.items():
            im = decode_logo(nova, ptr)
            if im is None:
                continue
            stem = calls[0].replace('/', '_') or f'logo_{ptr:08x}'
            nm, k = stem, 2
            while nm in names:
                nm, k = f'{stem}_{k}', k + 1
            names.add(nm)
            im.save(os.path.join(outdir, nm + '.png'))
            tiles.append((nm, im))
            f.write(f"{nm}.png\t{im.width}x{im.height}\tused by: {', '.join(calls)}\n")
    if tiles:
        cols, cw, chh = 8, 130, 70
        sheet = Image.new('RGB', (cols * cw, ((len(tiles) + cols - 1) // cols) * chh), (40, 40, 40))
        d = ImageDraw.Draw(sheet)
        for k, (nm, im) in enumerate(sorted(tiles, key=lambda t: t[0])):
            x, y = (k % cols) * cw, (k // cols) * chh
            sheet.paste(im, (x + (cw - im.width) // 2, y + 2))
            d.text((x + 4, y + chh - 14), nm, fill=(220, 220, 220))
        sheet.save(os.path.join(outdir, '_contact_sheet.png'))
    print(f'{len(tiles)} logos ({sum(len(v) for v in users.values())} channels) -> {outdir}')


def main(argv=None):
    ap = argparse.ArgumentParser(description='Dump a Guide Plus+ .dbm database with Nova2 under emulation.')
    ap.add_argument('what', choices=['listings', 'logos'])
    ap.add_argument('dbm')
    ap.add_argument('outdir')
    ap.add_argument('--exe', default=os.environ.get('NOVA_EXE'), help='path to nova2.0.13.0_pc.exe (or set NOVA_EXE)')
    a = ap.parse_args(argv)
    if not a.exe:
        ap.error('give --exe or set NOVA_EXE')
    nova = Nova(a.exe)
    (dump_listings if a.what == 'listings' else dump_logos)(nova, a.dbm, a.outdir)


if __name__ == '__main__':
    main()
