"""Readers for raw VBI captures that carry Gemstar data.

Three layouts are supported. Each reader yields chunks of fields as float32 arrays shaped
(fields, lines, samples) together with a `Geometry` that tells the slicers where the run-in sits.

* ``tbc``     raw unsigned 16-bit samples, 910 per line (4 x fsc), 16 lines per field starting at
              NTSC line 9: ld-decode ``.tbc`` / ``.vbi`` / ``.y16`` VBI exports.
* ``flac``    the same data packed as FLAC audio by ld-decode (decoded through ffmpeg).
* ``cap1440`` 8-bit capture-card VBI dumps, 1440 samples per line at 27 MHz, 12 lines per field
              starting at NTSC line 10 (for example ``.vbi`` files from bttv / saa713x cards).
"""

import os
import subprocess
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Geometry:
    name: str
    samples: int          # samples stored per line
    line_period: float    # samples per full 63.56 us line at this sample rate
    lines: int            # lines stored per field
    first_line: int       # NTSC line number of stored line index 0
    run_in_2x: tuple      # (lo, hi) sample window used to measure the 2X run-in phase
    first_bit_2x: float   # phase reference: first 2X bit sits at or after this sample
    thr_2x: tuple         # window used for the slicing threshold
    run_in_1x: tuple
    first_bit_1x: float
    thr_1x: tuple


TBC = Geometry('tbc', 910, 910.0, 16, 9, (130, 300), 140.0, (150, 264), (130, 330), 135.0, (140, 310))
CAP1440 = Geometry('cap1440', 1440, 1716.0, 12, 10, (10, 290), 15.0, (20, 234), (10, 290), 10.0, (20, 234))

FORMATS = {'tbc': TBC, 'flac': TBC, 'cap1440': CAP1440}


def detect_format(path):
    """Guess the capture layout from the extension and file size."""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.flac':
        return 'flac'
    size = os.path.getsize(path)
    tbc_ok = size % (TBC.samples * TBC.lines * 2) == 0
    cap_ok = size % (CAP1440.samples * CAP1440.lines) == 0
    if tbc_ok and not cap_ok:
        return 'tbc'
    if cap_ok and not tbc_ok:
        return 'cap1440'
    if ext in ('.y16', '.tbc'):
        return 'tbc'
    # Both fit: a 16-bit TBC dump has sync tips near 0 and peaks far above 255.
    with open(path, 'rb') as f:
        head = np.frombuffer(f.read(TBC.samples * 2 * 64), dtype='<u2')
    return 'tbc' if head.max() > 1024 else 'cap1440'


def read_fields(path, fmt=None, chunk=2000, max_fields=None, ffmpeg='ffmpeg'):
    """Yield (geometry, array[fields, lines, samples] float32) chunks from a capture."""
    fmt = fmt or detect_format(path)
    geo = FORMATS[fmt]
    if fmt == 'flac':
        frame = geo.samples * geo.lines * 2
        proc = subprocess.Popen([ffmpeg, '-loglevel', 'error', '-i', path, '-f', 's16le', '-acodec', 'pcm_s16le', '-'],
                                stdout=subprocess.PIPE, bufsize=1 << 24)
        read = proc.stdout.read
        convert = lambda b: np.frombuffer(b, dtype='<i2').astype(np.float32)
    else:
        f = open(path, 'rb')
        read = f.read
        if fmt == 'tbc':
            frame = geo.samples * geo.lines * 2
            convert = lambda b: np.frombuffer(b, dtype='<u2').astype(np.float32) - 32768.0
        else:
            frame = geo.samples * geo.lines
            convert = lambda b: np.frombuffer(b, dtype=np.uint8).astype(np.float32)
    done = 0
    try:
        while max_fields is None or done < max_fields:
            n = chunk if max_fields is None else min(chunk, max_fields - done)
            buf = read(n * frame)
            k = len(buf) // frame
            if k == 0:
                break
            yield geo, convert(buf[:k * frame]).reshape(k, geo.lines, geo.samples)
            done += k
    finally:
        if fmt == 'flac':
            proc.kill()
        else:
            f.close()
