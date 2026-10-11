"""Reed-Solomon RS(76,68) for Gemstar Type B blocks (FREQCCD Syndrome4 / ErrCorrect).
GF(256), field polynomial 0x11D, roots alpha^1..alpha^8. The codeword is bytes 4..79 of the 80-byte block, sent as
19 four-byte 2X frames; within each frame the bytes are taken last to first (byte 3, 2, 1, 0), highest degree first.
Corrects up to 4 bad bytes per block."""
EXP = [0] * 512; LOG = [0] * 256
x = 1
for i in range(255):
    EXP[i] = x; LOG[x] = i; x <<= 1
    if x & 0x100: x ^= 0x11D
for i in range(255, 512): EXP[i] = EXP[i - 255]
NSYM = 8

def mul(a, b): return 0 if a == 0 or b == 0 else EXP[LOG[a] + LOG[b]]
def inv(a): return EXP[255 - LOG[a]]

def to_cw(block):
    return [c for k in range(4, 80, 4) for c in block[k:k + 4][::-1]]

def from_cw(block, cw):
    out = bytearray(block)
    for f in range(19):
        out[4 + 4 * f:8 + 4 * f] = bytes(cw[4 * f:4 * f + 4][::-1])
    return bytes(out)

def syndromes(cw):
    s = []
    for j in range(1, NSYM + 1):
        v = 0; r = EXP[j]
        for c in cw: v = mul(v, r) ^ c
        s.append(v)
    return s

def correct(block):
    """Returns (corrected block, number of bytes fixed) or (None, -1) if uncorrectable."""
    cw = to_cw(block); n = len(cw); S = syndromes(cw)
    if not any(S): return bytes(block), 0
    # Berlekamp-Massey
    C = [1]; B = [1]; L = 0; m = 1; b = 1
    for k in range(NSYM):
        d = S[k]
        for i in range(1, L + 1): d ^= mul(C[i], S[k - i])
        if d == 0: m += 1; continue
        T = C[:]; coef = mul(d, inv(b))
        C = C + [0] * (len(B) + m - len(C))
        for i, bi in enumerate(B): C[i + m] ^= mul(coef, bi)
        if 2 * L <= k: L = k + 1 - L; B = T; b = d; m = 1
        else: m += 1
    if L > NSYM // 2: return None, -1
    # Chien search: position p (0 = first byte, highest degree n-1-p) is an error if C(alpha^-(n-1-p)) = 0
    pos = []
    for p in range(n):
        e = n - 1 - p; xi = EXP[(255 - e) % 255]; v = 0
        for c in reversed(C): v = mul(v, xi) ^ c
        if v == 0: pos.append(p)
    if len(pos) != L: return None, -1
    # Forney (first root alpha^1): Omega = S(x) * C(x) mod x^NSYM
    Om = [0] * NSYM
    for i in range(NSYM):
        for j in range(min(i + 1, len(C))): Om[i] ^= mul(S[i - j], C[j])
    for p in pos:
        e = n - 1 - p; X = EXP[e]; Xi = inv(X)
        num = 0
        for c in reversed(Om): num = mul(num, Xi) ^ c
        den = 0
        for i in range(1, len(C), 2): den ^= mul(C[i], EXP[(LOG[Xi] * (i - 1)) % 255] if Xi else 0)
        if den == 0: return None, -1
        cw[p] ^= mul(num, inv(den))       # b = 1: error value = Omega(Xi) / C'(Xi)
    if any(syndromes(cw)): return None, -1
    return from_cw(block, cw), len(pos)
