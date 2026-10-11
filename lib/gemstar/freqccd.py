"""Run routines from Gemstar's own FREQCCD_.EXE (the 1996 "VBI Data Monitor", a 16-bit DOS program)
under CPU emulation, so the Type B decryption, the monthly `|B` descrambling and the SIP text
decompression are done by Gemstar's code rather than a re-implementation.

Nothing from the program is distributed here. The user points the decoder at their own copy of
FREQCCD_.EXE (476,362 bytes, MD5 b32498891374543ba5ee0c68a7e96ec7) and, for `|B` packets and listings,
the .INP tables and DDPACKET.BIN shipped next to it. Requires `pip install unicorn`.

The program is loaded at segment 0 (so it needs no relocation) and each routine is called with a
far-call stack frame whose return address points at a HLT.
"""

import glob
import os
import struct

EXE_SIZE = 476362
DGROUP = 0x23CF

# Routine addresses (segment:offset with the image loaded at segment 0), from the program's own
# Turbo Debugger symbol table.
DECRYPT = (0x13CD, 0x0561)        # Decrypt(payload far*, hdr far*, year-1980, month)
DESCRAMBLE = (0x0BAC, 0x0168)     # Descramble(packet far*, length) using the loaded .INP table
SIP_CTOR = (0x1436, 0x0696)       # SIP::SIP(obj, data, len, year, month, day, ...)
SIP_PROCESS = (0x152F, 0x0367)
SIP_START = (0x152F, 0x10EA)
SIP_END = (0x152F, 0x1154)
SIP_CATEGORY = (0x152F, 0x1189)
SIP_MISC = (0x152F, 0x11A9)        # SIP::GetMisc: per-program flag byte (CC / stereo / SAP / live)
DECOMP = (0x1A4F, 0x000C)         # decomp(src far*, len, dst far*)


class FreqccdMissing(RuntimeError):
    pass


def _load_image(exe_path):
    try:
        d = open(exe_path, 'rb').read()
    except OSError as e:
        raise FreqccdMissing(f'cannot read FREQCCD_.EXE: {e}')
    if d[:2] != b'MZ' or len(d) != EXE_SIZE:
        raise FreqccdMissing(f'{exe_path} is not the expected FREQCCD_.EXE ({EXE_SIZE:,} bytes)')
    cblp, cp, _, cparhdr = struct.unpack_from('<HHHH', d, 2)
    return d[cparhdr * 16:(cp - 1) * 512 + (cblp or 512)]


class _Machine:
    """One emulated 1 MB real-mode address space with the program image loaded."""

    def __init__(self, image, budget=5_000_000):
        try:
            from unicorn import Uc, UC_ARCH_X86, UC_MODE_16
            from unicorn import x86_const as X
        except ImportError:
            raise FreqccdMissing('the unicorn package is needed for decryption: pip install unicorn')
        self.X = X
        self.mu = Uc(UC_ARCH_X86, UC_MODE_16)
        self.mu.mem_map(0, 0x100000)
        self.mu.mem_write(0, image)
        self.mu.mem_write(0x80000, b'\xf4')         # HLT = return address
        self.budget = budget

    def call(self, routine, args):
        seg, off = routine
        mu, X = self.mu, self.X
        sp = 0xFF00
        for a in reversed(args):
            sp -= 2
            mu.mem_write(0x90000 + sp, struct.pack('<H', a & 0xFFFF))
        for w in (0x8000, 0x0000):                   # far return to 8000:0000
            sp -= 2
            mu.mem_write(0x90000 + sp, struct.pack('<H', w))
        mu.reg_write(X.UC_X86_REG_SS, 0x9000)
        mu.reg_write(X.UC_X86_REG_SP, sp)
        mu.reg_write(X.UC_X86_REG_DS, DGROUP)
        mu.reg_write(X.UC_X86_REG_ES, DGROUP)
        mu.reg_write(X.UC_X86_REG_CS, seg)
        mu.reg_write(X.UC_X86_REG_IP, off)
        mu.emu_start(seg * 16 + off, 0x80000, count=self.budget)
        return mu.reg_read(X.UC_X86_REG_AX)

    def write(self, addr, data):
        self.mu.mem_write(addr, bytes(data))

    def read(self, addr, n):
        return bytes(self.mu.mem_read(addr, n))


class Freqccd:
    """Gemstar routines from a user-supplied FREQCCD_.EXE."""

    def __init__(self, exe_path, inp_dir=None, ddpacket=None):
        self.image = _load_image(exe_path)
        here = os.path.dirname(os.path.abspath(exe_path))
        self.inp_dir = inp_dir or here
        self.ddpacket = ddpacket or os.path.join(here, 'DDPACKET.BIN')
        self._dec = self._desc = self._sip = None
        self._table = None

    # ---- Type B block decryption ------------------------------------------------------------
    def decrypt(self, payload, block_no, key_byte, year, month):
        """Decrypt the 64-byte payload of one Type B block. `year` is the full year."""
        if self._dec is None:
            self._dec = _Machine(self.image, 2_000_000)
        m = self._dec
        m.write(0x70000, payload)
        m.write(0x71000, bytes([block_no, key_byte]))
        m.call(DECRYPT, [0x0000, 0x7000, 0x0000, 0x7100, year - 1980, month])
        return m.read(0x70000, len(payload))

    # ---- |B packet descrambling ---------------------------------------------------------------
    def tables(self):
        """{name: [ints]} for every .INP table found (name = MMYY, e.g. '1201')."""
        out = {}
        for f in sorted(glob.glob(os.path.join(self.inp_dir, '*.INP')) + glob.glob(os.path.join(self.inp_dir, '*.inp'))):
            name = os.path.basename(f)[:-4]
            if name.isdigit():
                out[name] = [int(x) for x in open(f).read().split()]
        return out

    def load_table(self, name, values):
        if self._desc is None:
            self._desc = _Machine(self.image, 2_000_000)
        m = self._desc
        base = DGROUP * 16
        m.write(base + 0x68E4, bytes(0x3EA * 2))
        for i, v in enumerate(values):
            m.write(base + 0x68E4 + 2 * (i + 1), struct.pack('<h', v))
            m.write(base + 0x6CCE + 2 * (i + 1), struct.pack('<h', v))
        m.write(base + 0x70B8, struct.pack('<HH', len(values), len(values)))
        m.write(base + 0x70BC, bytes([int(name) // 100]))
        self._table = name

    def descramble(self, packet):
        """Descramble one 7-bit packet (bytes from 07 to the trailing checksum).
        Returns (plain bytes, checksum_ok)."""
        m = self._desc
        m.write(0x72000, packet)
        r = m.call(DESCRAMBLE, [0, 0x7200, len(packet)]) & 0xFF
        return m.read(0x72000, len(packet)), ((r + packet[11]) & 0x7F) == 0

    # ---- SIP listings (type 0) ----------------------------------------------------------------
    def _sip_machine(self):
        if self._sip is None:
            try:
                dic = open(self.ddpacket, 'rb').read()
            except OSError as e:
                raise FreqccdMissing(f'cannot read DDPACKET.BIN (text dictionary): {e}')
            m = _Machine(self.image, 5_000_000)
            m.write(DGROUP * 16 + 0xB71E, dic)
            m.write(DGROUP * 16 + 0xCF26, dic[:32])
            self._sip = m
        return self._sip

    def decomp(self, raw):
        m = self._sip_machine()
        m.write(0x73000, raw)
        m.write(0x74000, bytes(0x1000))
        n = m.call(DECOMP, [0, 0x7300, len(raw), 0, 0x7400])
        return m.read(0x74000, min(n, 0x1000)).decode('latin1')

    def sip(self, data, year, month, day):
        """Run SIP::Process on one decrypted type-0 message.
        Returns (guide_channel, date_code, [(start, end, category, title, info, description, misc), ...])."""
        m = self._sip_machine()
        OBJ = 0x6000
        o = OBJ * 16
        m.write(o, bytes(0x800))
        m.write(0x75000, data)
        m.call(SIP_CTOR, [0, OBJ, 0, 0x7500, len(data), year, month, day, 0, 0])
        m.call(SIP_PROCESS, [0, OBJ])
        n = struct.unpack('<H', m.read(o + 0x466, 2))[0]
        gch, date = struct.unpack('<ii', m.read(o + 0x46E, 8))
        rows = []
        for i in range(min(n, 32)):
            st = m.call(SIP_START, [0, OBJ, i])
            en = m.call(SIP_END, [0, OBJ, i])
            cat = m.call(SIP_CATEGORY, [0, OBJ, i]) & 0xFF
            misc = m.call(SIP_MISC, [0, OBJ, i]) & 0xFF
            s = []
            for lo, oo in ((0x4C0, 0x500), (0x540, 0x580), (0x5C0, 0x600)):
                ln = struct.unpack('<H', m.read(o + lo + 2 * i, 2))[0]
                off = struct.unpack('<H', m.read(o + oo + 2 * i, 2))[0]
                s.append(self.decomp(m.read(o + off + 2, min(ln, 0x400))) if ln else '')
            rows.append((st, en, cat, s[0], s[1], s[2], misc))
        return gch, date, rows
