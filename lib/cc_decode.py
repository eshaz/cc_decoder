#!/usr/local/bin/python
# coding: utf-8
"""
ccDecoder is a Python Closed Caption Decoder.
Presented by Max Smith and notonbluray.com

Python 2.7+ and Python 3 compatible

Public domain / Unlicense
But attribution is always appreciated where possible.

See spec: http://www.gpo.gov/fdsys/pkg/CFR-2007-title47-vol1/pdf/CFR-2007-title47-vol1-sec15-119.pdf
"""

__author__ = "Max Smith"
__copyright__ = "Copyright 2014-2025 Max Smith"
__credits__ = ["Max Smith"]
__license__ = """
This is free and unencumbered software released into the public domain.

Anyone is free to copy, modify, publish, use, compile, sell, or
distribute this software, either in source code form or as a compiled
binary, for any purpose, commercial or non-commercial, and by any
means.

In jurisdictions that recognize copyright laws, the author or authors
of this software dedicate any and all copyright interest in the
software to the public domain. We make this dedication for the benefit
of the public at large and to the detriment of our heirs and
successors. We intend this dedication to be an overt act of
relinquishment in perpetuity of all present and future rights to this
software under copyright law.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.

For more information, please refer to <http://unlicense.org/>
"""
__version__ = "1.0.0"
__maintainer__ = "Max Smith"
__email__ = None  # Sorry, I get far too much spam as it is. Track me down at http://www.notonbluray.com

import re
import sys
import math

from collections import Counter

from html import escape, unescape

import numpy as np
import matplotlib.pyplot as plt

from setproctitle import setproctitle
from multiprocessing import current_process


PREAMBLE_RUN_IN_COUNT = 6.5

# How wide one bit may be, as a fraction of the image width.
MIN_CLOCK_FRACTION = 0.035
MAX_CLOCK_FRACTION = 0.041

# Everything well above the data's own bandwidth is noise. The bits are NRZ at the clock
# rate, so their spectrum is a sinc whose first null is at the bit rate
BAND_LIMIT_PASS = 1.0
BAND_LIMIT_STOP = 1.5
_BAND_LIMIT_MASKS = {}

def band_limit(line):
    """ The line with everything above the data's own bandwidth taken out """
    width = len(line)
    mask = _BAND_LIMIT_MASKS.get(width)

    if mask is None:
        bit = 1.0 / (0.5 * (MIN_CLOCK_FRACTION + MAX_CLOCK_FRACTION) * width)
        stop = BAND_LIMIT_STOP * bit
        start = BAND_LIMIT_PASS * bit

        frequency = np.fft.rfftfreq(width)
        mask = np.ones(len(frequency))
        mask[frequency >= stop] = 0.0
        shoulder = (frequency > start) & (frequency < stop)
        mask[shoulder] = 0.5 * (1 + np.cos(np.pi * (frequency[shoulder] - start) / (stop - start)))
        _BAND_LIMIT_MASKS[width] = mask

    return np.fft.irfft(np.fft.rfft(line) * mask, width)
START_BIT_ZEROS_COUNT = 2
START_BIT_ONES_COUNT = 1
START_BIT_COUNT = START_BIT_ZEROS_COUNT + START_BIT_ONES_COUNT
DATA_BIT_COUNT = 16
PRE_COMPUTED_PREAMBLE_TEMPLATES = []

# a bit whose sample window varies by more than this is treated as unreliable
MIN_STD_DEV_FOR_CORRECTION = 0.3

CC_TABLE = {
    0x00: '',  # Special - included here to clear a few things up
    0x20: ' ', 0x21: '!', 0x22: '"', 0x23: '#', 0x24: '$', 0x25: '%', 0x26: '&', 0x27: "'", 0x28: "(", 0x29: ")",
    0x2A: 'á', 0x2B: '+', 0x2C: ',', 0x2D: '-', 0x2E: '.', 0x2F: '/', 0x3A: ':', 0x3B: ';', 0x3C: '<', 0x3D: '=',
    0x3E: '>', 0x3F: '?', 0x40: '@', 0x5B: '[', 0x5C: 'é', 0x5D: ']', 0x5E: 'í', 0x5F: 'ó', 0x60: 'ú', 0x7B: 'ç',
    0x7C: '÷', 0x7D: 'Ñ', 0x7E: 'ñ', 0x7F: '■',
}

# Populate standard ASCII codes ASCII ranges that are shared
CC_TABLE.update({i: chr(i) for nr in [(0x41, 0x5B), (0x61, 0x7B), (0x30, 0x3A)] for i in range(nr[0], nr[1])})


# Two byte chars
SPECIAL_CHARS_TABLE = {
    0x30: '®', 0x31: '°', 0x32: '½', 0x33: '¿', 0x34: '™', 0x35: '¢', 0x36: '£', 0x37: '♪',
    0x38: 'à', 0x39: ' ', 0x3A: 'è', 0x3B: 'â', 0x3C: 'ê', 0x3D: 'î', 0x3E: 'ô', 0x3F: 'û',
}

# Extended Western European Character Set

EXTENDED_SPANISH_FRENCH = {
    0x20: 'Á',
    0x21: 'É',
    0x22: 'Ó',
    0x23: 'Ú',
    0x24: 'Ü',
    0x25: 'ü',
    0x26: '‘',
    0x27: '¡',
    0x28: '*',
    0x29: "'",
    0x2A: '─',
    0x2B: '©',
    0x2C: '℠',
    0x2D: '•',
    0x2E: '“',
    0x2F: '”',
    0x30: 'À',
    0x31: 'Â',
    0x32: 'Ç',
    0x33: 'È',
    0x34: 'Ê',
    0x35: 'Ë',
    0x36: 'ë',
    0x37: 'Î',
    0x38: 'Ï',
    0x39: 'ï',
    0x3A: 'Ô',
    0x3B: 'Ù',
    0x3C: 'ù',
    0x3D: 'Û',
    0x3E: '«',
    0x3F: '»',
}

EXTENDED_PORTUGUESE_GERMAN_DANISH = {
    0x20: 'Ã',
    0x21: 'ã',
    0x22: 'Í',
    0x23: 'Ì',
    0x24: 'ì',
    0x25: 'Ò',
    0x26: 'ò',
    0x27: 'Õ',
    0x28: 'õ',
    0x29: "{",
    0x2A: '}',
    0x2B: '\\',
    0x2C: '^',
    0x2D: '_',
    0x2E: '|',
    0x2F: '~',
    0x30: 'Ä',
    0x31: 'ä',
    0x32: 'Ö',
    0x33: 'ö',
    0x34: 'ß',
    0x35: '¥',
    0x36: '¤',
    0x37: '│',
    0x38: 'Å',
    0x39: 'å',
    0x3A: 'Ø',
    0x3B: 'ø',
    0x3C: '┌',
    0x3D: '┐',
    0x3E: '└',
    0x3F: '┘',
}

CC1_SPECIAL_CHARS_TABLE = {(0x11, a): b for (a, b) in SPECIAL_CHARS_TABLE.items()}
CC2_SPECIAL_CHARS_TABLE = {(0x19, a): b for (a, b) in SPECIAL_CHARS_TABLE.items()}
CC1_SPECIAL_CHARS_TABLE.update( {(0x12, a): b for (a, b) in EXTENDED_SPANISH_FRENCH.items()} )
CC2_SPECIAL_CHARS_TABLE.update( {(0x1A, a): b for (a, b) in EXTENDED_SPANISH_FRENCH.items()} )
CC1_SPECIAL_CHARS_TABLE.update( {(0x13, a): b for (a, b) in EXTENDED_PORTUGUESE_GERMAN_DANISH.items()} )
CC2_SPECIAL_CHARS_TABLE.update( {(0x1B, a): b for (a, b) in EXTENDED_PORTUGUESE_GERMAN_DANISH.items()} )

# Achieving compatibility with Python2 and 3 makes us do strange things
ALL_SPECIAL_CHARS = CC1_SPECIAL_CHARS_TABLE.copy()
ALL_SPECIAL_CHARS.update(CC2_SPECIAL_CHARS_TABLE)

CONTROL_CODES = {
    (0x14, 0x20): 'Resume Caption Loading',     (0x14, 0x21): 'Backspace',
    (0x14, 0x22): 'Reserved (Alarm Off)',       (0x14, 0x23): 'Reserved (Alarm On)',
    (0x14, 0x24): 'Delete to End Of Row',       (0x14, 0x25): 'Roll-Up Captions-2 Rows',
    (0x14, 0x26): 'Roll-Up Captions-3 Rows',    (0x14, 0x27): 'Roll-Up Captions-4 Rows',
    (0x14, 0x2A): 'Text Restart',               (0x14, 0x29): 'Resume Direct Captioning',
    (0x14, 0x2C): 'Erase Displayed Memory',     (0x14, 0x2B): 'Resume Text Display',
    (0x14, 0x2E): 'Erase Non-Displayed Memory', (0x14, 0x2D): 'Carriage Return',
    (0x17, 0x21): 'Tab Offset 1',               (0x14, 0x2F): 'End of Caption (flip memory)',
    (0x17, 0x22): 'Tab Offset 2',               (0x17, 0x23): 'Tab Offset 3'
}

# CEA-608-E Table 4
CONTROL_CODES.update({
    (0x17, 0x24): 'Standard Character Set',     (0x17, 0x25): 'Standard Character Set Double Size',
    (0x17, 0x26): 'First Private Character Set', (0x17, 0x27): 'Second Private Character Set',
    (0x17, 0x28): 'GB 2312-80 Character Set',   (0x17, 0x29): 'KSC 5601-1987 Character Set',
    (0x17, 0x2A): 'First Registered Character Set',
})

# CEA-608-E G.2.2, G.2.3
CONTROL_CODES.update({
    (0x16, 0x20): 'Article Name Start',         (0x16, 0x30): 'Article Name End',
    (0x16, 0x31): 'Article Clear',              (0x16, 0x32): 'Article End',
})
CONTROL_CODES.update({(0x16, a): 'Article Page %d' % (a - 0x1F) for a in range(0x21, 0x30)})

# CEA-608-E Table 3
BACKGROUND_COLOR_CODES = {
    0x20: 'Background White',
    0x21: 'Background Semi-Transparent White',
    0x22: 'Background Green',
    0x23: 'Background Semi-Transparent Green',
    0x24: 'Background Blue',
    0x25: 'Background Semi-Transparent Blue',
    0x26: 'Background Cyan',
    0x27: 'Background Semi-Transparent Cyan',
    0x28: 'Background Red',
    0x29: 'Background Semi-Transparent Red',
    0x2A: 'Background Yellow',
    0x2B: 'Background Semi-Transparent Yellow',
    0x2C: 'Background Magenta',
    0x2D: 'Background Semi-Transparent Magenta',
    0x2E: 'Background Black',
    0x2F: 'Background Semi-Transparent Black',
}

CC1_BACKGROUND_CHARS = { (0x10,x): 'CC1 ' + y for (x,y) in BACKGROUND_COLOR_CODES.items() }  # Also CC3
CC2_BACKGROUND_CHARS = { (0x18,x): 'CC2 ' + y for (x,y) in BACKGROUND_COLOR_CODES.items() }  # Also CC4

CC1_BACKGROUND_CHARS.update( { (0x17, 0x2D) : 'CC1 Background Transparent' } )  # Also CC3
CC2_BACKGROUND_CHARS.update( { (0x1F, 0x2D) : 'CC2 Background Transparent' } )  # Also CC4

ROLL_UP_LEN = {
    0x25: 2, # Roll-Up Captions-2 Rows
    0x26: 3, # Roll-Up Captions-3 Rows
    0x27: 4, # Roll-Up Captions-4 Rows
}

MID_ROW_CODES = {
    (0x11, 0x20): 'Mid-row: White',   (0x11, 0x21): 'Mid-row: White Underline',
    (0x11, 0x22): 'Mid-row: Green',   (0x11, 0x23): 'Mid-row: Green Underline',
    (0x11, 0x24): 'Mid-row: Blue',    (0x11, 0x25): 'Mid-row: Blue Underline',
    (0x11, 0x26): 'Mid-row: Cyan',    (0x11, 0x27): 'Mid-row: Cyan Underline',
    (0x11, 0x28): 'Mid-row: Red',     (0x11, 0x29): 'Mid-row: Red Underline',
    (0x11, 0x2A): 'Mid-row: Yellow',  (0x11, 0x2B): 'Mid-row: Yellow Underline',
    (0x11, 0x2C): 'Mid-row: Magenta', (0x11, 0x2D): 'Mid-row: Magenta Underline',
    (0x11, 0x2E): 'Mid-row: Italics', (0x11, 0x2F): 'Mid-row: Italics Underline',
    (0x17, 0x2E): 'Foreground Black', (0x17, 0x2F): 'Foreground Black Underline',  # CEA-608-E Table 3
    (0x14, 0x28): 'Mid-row: Flash On'
}

## Preamble for odd columns except where it isn't
PREAMBLE_ODD = {
    0x40: 'Pre: White',         0x41: 'Pre: White Underline',
    0x42: 'Pre: Green',         0x43: 'Pre: Green Underline',
    0x44: 'Pre: Blue',          0x45: 'Pre: Blue Underline',
    0x46: 'Pre: Cyan',          0x47: 'Pre: Cyan Underline',
    0x48: 'Pre: Red',           0x49: 'Pre: Red Underline',
    0x4A: 'Pre: Yellow',        0x4B: 'Pre: Yellow Underline',
    0x4C: 'Pre: Magenta',       0x4D: 'Pre: Magenta Underline',
    0x4E: 'Pre: White Italics', 0x4F: 'Pre: White Italics Underline',
    0x50: 'Pre: Indent 0',      0x51: 'Pre: Indent 0 Underline',
    0x52: 'Pre: Indent 4',      0x53: 'Pre: Indent 4 Underline',
    0x54: 'Pre: Indent 8',      0x55: 'Pre: Indent 8 Underline',
    0x56: 'Pre: Indent 12',     0x57: 'Pre: Indent 12 Underline',
    0x58: 'Pre: Indent 16',     0x59: 'Pre: Indent 16 Underline',
    0x5A: 'Pre: Indent 20',     0x5B: 'Pre: Indent 20 Underline',
    0x5C: 'Pre: Indent 24',     0x5D: 'Pre: Indent 24 Underline',
    0x5E: 'Pre: Indent 28',     0x5F: 'Pre: Indent 28 Underline',
}
EVEN_PREAMBLE = {a + 0x20: b for (a, b) in PREAMBLE_ODD.items()}

CC1_CONTROL_CODES = {(a[0], a[1]): 'CC1 ' + b for (a, b) in CONTROL_CODES.items()}
CC2_CONTROL_CODES = {(a[0] | 0x08, a[1]): 'CC2 ' + b for (a, b) in CONTROL_CODES.items()}
CC1_MID_ROW_CODES = {(a[0],        a[1]): 'CC1 ' + b for (a, b) in MID_ROW_CODES.items()}
CC2_MID_ROW_CODES = {(a[0] | 0x08, a[1]): 'CC2 ' + b for (a, b) in MID_ROW_CODES.items()}

# Field 2 sends the miscellaneous control codes, 0x20 to 0x2F, with 0x15 and 0x1D where field 1
# uses 0x14 and 0x1C (CEA-608-E 8.4)
# Every other code - PACs, mid-row codes, tab offsets, specials - is the same on
# both fields, so for those it is the line a code arrived on that says whether it is CC1 or CC3.
MISC_CODES = {a[1]: b for (a, b) in {**CONTROL_CODES, **MID_ROW_CODES}.items() if a[0] == 0x14}
CC3_CONTROL_CODES = {(0x15, a): 'CC3 ' + b for (a, b) in MISC_CODES.items()}
CC4_CONTROL_CODES = {(0x1D, a): 'CC4 ' + b for (a, b) in MISC_CODES.items()}

## Columns headings 
CC1_PREAMBLE_COLS = [0x11, 0x11, 0x12, 0x12, 0x15, 0x15, 0x16, 0x16, 0x17, 0x17, 0x10, 0x13, 0x13, 0x14, 0x14]
CC2_PREAMBLE_COLS = [0x19, 0x19, 0x1A, 0x1A, 0x1D, 0x1D, 0x1E, 0x1E, 0x1F, 0x1F, 0x18, 0x1B, 0x1B, 0x1C, 0x1C]

COL_PREAMBLE = [PREAMBLE_ODD, EVEN_PREAMBLE, PREAMBLE_ODD, EVEN_PREAMBLE, PREAMBLE_ODD, EVEN_PREAMBLE,
                PREAMBLE_ODD, EVEN_PREAMBLE, PREAMBLE_ODD, EVEN_PREAMBLE, PREAMBLE_ODD, PREAMBLE_ODD,  # Candance change
                EVEN_PREAMBLE, PREAMBLE_ODD, EVEN_PREAMBLE]


def _cc_preamble_table():
    """ Function generated due to complexity - it could be a list comp, but it'd be ugly """
    table = dict()
    for col, val in enumerate(COL_PREAMBLE):
        for (row_code, text) in val.items():
            table[(CC1_PREAMBLE_COLS[col], row_code)] = 'CC1 %s row %d' % (text, (col + 1))
            table[(CC2_PREAMBLE_COLS[col], row_code)] = 'CC2 %s row %d' % (text, (col + 1))
    return table

# Achieving compatibility with Python2 and 3 makes us do strange things
ALL_CC_CONTROL_CODES = _cc_preamble_table()
ALL_CC_CONTROL_CODES.update(CC1_CONTROL_CODES)
ALL_CC_CONTROL_CODES.update(CC2_CONTROL_CODES)
ALL_CC_CONTROL_CODES.update(CC3_CONTROL_CODES)
ALL_CC_CONTROL_CODES.update(CC4_CONTROL_CODES)
ALL_CC_CONTROL_CODES.update(CC1_MID_ROW_CODES)
ALL_CC_CONTROL_CODES.update(CC2_MID_ROW_CODES)
ALL_CC_CONTROL_CODES.update(CC1_BACKGROUND_CHARS)
ALL_CC_CONTROL_CODES.update(CC2_BACKGROUND_CHARS)

NO_PARITY_TO_ODD_PARITY = [
    0x80, 0x01, 0x02, 0x83, 0x04, 0x85, 0x86, 0x07, 0x08, 0x89, 0x8a, 0x0b, 0x8c, 0x0d, 0x0e, 0x8f,
    0x10, 0x91, 0x92, 0x13, 0x94, 0x15, 0x16, 0x97, 0x98, 0x19, 0x1a, 0x9b, 0x1c, 0x9d, 0x9e, 0x1f,
    0x20, 0xa1, 0xa2, 0x23, 0xa4, 0x25, 0x26, 0xa7, 0xa8, 0x29, 0x2a, 0xab, 0x2c, 0xad, 0xae, 0x2f,
    0xb0, 0x31, 0x32, 0xb3, 0x34, 0xb5, 0xb6, 0x37, 0x38, 0xb9, 0xba, 0x3b, 0xbc, 0x3d, 0x3e, 0xbf,
    0x40, 0xc1, 0xc2, 0x43, 0xc4, 0x45, 0x46, 0xc7, 0xc8, 0x49, 0x4a, 0xcb, 0x4c, 0xcd, 0xce, 0x4f,
    0xd0, 0x51, 0x52, 0xd3, 0x54, 0xd5, 0xd6, 0x57, 0x58, 0xd9, 0xda, 0x5b, 0xdc, 0x5d, 0x5e, 0xdf,
    0xe0, 0x61, 0x62, 0xe3, 0x64, 0xe5, 0xe6, 0x67, 0x68, 0xe9, 0xea, 0x6b, 0xec, 0x6d, 0x6e, 0xef,
    0x70, 0xf1, 0xf2, 0x73, 0xf4, 0x75, 0x76, 0xf7, 0xf8, 0x79, 0x7a, 0xfb, 0x7c, 0xfd, 0xfe, 0x7f,
]

US_TV_PARENTAL_GUIDELINE_RATING = ['Not rated', 'TV-Y', 'TV-Y7', 'TV-G', 'TV-PG', 'TV-14', 'TV-MA', 'Not rated']

MPA_RATING = ['N/A', 'G', 'PG', 'PG-13', 'R', 'NC-17', 'X', 'Not Rated']

CANADIAN_ENGLISH_RATINGS = ['E', 'C', 'C8+', 'G', 'PG', '14+', '18+', 'Invalid']
CANADIAN_FRENCH_RATINGS = ['E', 'G', '8 ans +', '13 ans +', '16 ans +', '18 ans +', 'Invalid', 'Invalid']


# For rating TV-Y7, Violence becomes fantasy violence
VCHIP_FLAGS_BYTE1 = [(0x20, 'Sexually suggestive dialog')]
VCHIP_FLAGS_BYTE2 = [(0x20, 'Violence'), (0x10, 'Sexual situations'), (0x08, 'Strong language')]

XDS_GENRE_CODES = {
    0x20: 'Education',    0x21: 'Entertainment', 0x22: 'Movie',       0x23: 'News',          0x24: 'Religious',
    0x25: 'Sports',       0x26: 'Other',         0x27: 'Action',      0x28: 'Advertisement', 0x29: 'Animated',
    0x2A: 'Anthology',    0x2B: 'Automobile',    0x2C: 'Awards',      0x2D: 'Baseball',      0x2E: 'Basketball',
    0x2F: 'Bulletin',     0x30: 'Business',      0x31: 'Classical',   0x32: 'College',       0x33: 'Combat',
    0x34: 'Comedy',       0x35: 'Commentary',    0x36: 'Concert',     0x37: 'Consumer',      0x38: 'Contemporary',
    0x39: 'Crime',        0x3A: 'Dance',         0x3B: 'Documentary', 0x3C: 'Drama',         0x3D: 'Elementary',
    0x3E: 'Erotica',      0x3F: 'Exercise',      0x40: 'Fantasy',     0x41: 'Farm',          0x42: 'Fashion',
    0x43: 'Fiction',      0x44: 'Food',          0x45: 'Football',    0x46: 'Foreign',       0x47: 'Fund Raiser',
    0x48: 'Game/Quiz',    0x49: 'Garden',        0x4A: 'Golf',        0x4B: 'Government',    0x4C: 'Health',
    0x4D: 'High School',  0x4E: 'History',       0x4F: 'Hobby',       0x50: 'Hockey',        0x51: 'Home',
    0x52: 'Horror',       0x53: 'Information',   0x54: 'Instruction', 0x55: 'International', 0x56: 'Interview',
    0x57: 'Language',     0x58: 'Legal',         0x59: 'Live',        0x5A: 'Local',         0x5B: 'Math',
    0x5C: 'Medical',      0x5D: 'Meeting',       0x5E: 'Military',    0x5F: 'Miniseries',    0x60: 'Music',
    0x61: 'Mystery',      0x62: 'National',      0x63: 'Nature',      0x64: 'Police',        0x65: 'Politics',
    0x66: 'Premier',      0x67: 'Prerecorded',   0x68: 'Product',     0x69: 'Professional',  0x6A: 'Public',
    0x6B: 'Racing',       0x6C: 'Reading',       0x6D: 'Repair',      0x6E: 'Repeat',        0x6F: 'Review',
    0x70: 'Romance',      0x71: 'Science',       0x72: 'Series',      0x73: 'Service',       0x74: 'Shopping',
    0x75: 'Soap Opera',   0x76: 'Special',       0x77: 'Suspense',    0x78: 'Talk',          0x79: 'Technical',
    0x7A: 'Tennis',       0x7B: 'Travel',        0x7C: 'Variety',     0x7D: 'Video',         0x7E: 'Weather',
    0x7F: 'Western',
}

XDS_AUDIO_SERVICES_LANGUAGE = ['Unknown', 'English', 'Spanish', 'French', 'German', 'Italian', 'Other', 'None']

XDS_AUDIO_SERVICES_TYPE_MAIN = [
    'Unknown', 'Mono', 'Simulated Stereo', 'Stereo', 'Stereo Surround', 'Data Service', 'Other', 'None'
]

XDS_AUDIO_SERVICES_TYPE_SECONDARY = list(XDS_AUDIO_SERVICES_TYPE_MAIN)
XDS_AUDIO_SERVICES_TYPE_SECONDARY[2] = 'Video Descriptions'
XDS_AUDIO_SERVICES_TYPE_SECONDARY[3] = 'Non-program Audio'
XDS_AUDIO_SERVICES_TYPE_SECONDARY[4] = 'Special Effects'

XDS_CAPTION_SERVICES = [
    'field one, channel C1, captioning', 'field one, channel C1, Text',
    'field one, channel C2, captioning', 'field one, channel C2, Text',
    'field two, channel C1, captioning', 'field two, channel C1, Text',
    'field two, channel C2, captioning', 'field two, channel C2, Text',
]

WEATHER_CATEGORY_CODES = {
    'EAN': 'Emergency Action Notification (National only)',
    'EAT': 'Emergency Action Termination (National only)',
    'NIC': 'National Information Center',
    'NPT': 'National Periodic Test',
    'RMT': 'Required Monthly Test',
    'RWT': 'Required Weekly Test',
    'ADR': 'Administrative Message',      'HUA': 'Hurricane Watch',
    'AVW': 'Avalanche Warning',           'HUW': 'Hurricane Warning',
    'AVA': 'Avalanche Watch',             'LAE': 'Local Area Emergency',
    'BZW': 'Blizzard Warning',            'LEW': 'Law Enforcement Warning',
    'CAE': 'Child Abduction Emergency',   'NMN': 'Network Message Notification',
    'CDW': 'Civil Danger Warning',        'NUW': 'Nuclear Power Plant Warning',
    'CEM': 'Civil Emergency Message',     'RHW': 'Radiological Hazard Warning',
    'CFW': 'Coastal Flood Warning',       'SMW': 'Special Marine Warning',
    'CFA': 'Coastal Flood Watch',         'SPS': 'Special Weather Statement',
    'DFW': 'Dust Storm Warning',          'SPW': 'Shelter in Place Warning',
    'DMO': 'Practice/Demo Warning',       'SVA': 'Severe Thunderstorm Watch',
    'EQW': 'Earthquake Warning',          'SVR': 'Severe Thunderstorm Warning',
    'EVI': 'Evacuation Immediate',        'SVS': 'Severe Weather Statement',
    'FFA': 'Flash Flood Watch',           'TOA': 'Tornado Watch',
    'FFS': 'Flash Flood Statement',       'TOE': '911 Telephone Outage Emergency',
    'FFW': 'Flash Flood Warning',         'TOR': 'Tornado Warning',
    'FLA': 'Flood Watch',                 'TRA': 'Tropical Storm Watch',
    'FLS': 'Flood Statement',             'TRW': 'Tropical Storm Warning',
    'FLW': 'Flood Warning',               'TSA': 'Tsunami Watch',
    'FRW': 'Fire Warning',                'TSW': 'Tsunami Warning',
    'HLS': 'Hurricane Statement',         'VOW': 'Volcano Warning',
    'HMW': 'Hazardous Materials Warning', 'WSA': 'Winter Storm Watch',
    'HWA': 'High Wind Watch',             'WSW': 'Winter Storm Warning',
    'HWW': 'High Wind Warning',
    'LFP': 'Service Area Forecast',       'BRT': 'Composite Broadcast Statement',
}

XDS_CGMS = [
    'Copying is permitted without restriction', 'No more copies (one generation copy has been made)',
    'One generation of copies may be made',     'No copying is permitted'
]

XDS_CGMS_APS = [  # Macrovision, etc
    'No Analogue protection',                              'Analogue protection: PSP On; Split Burst Off',
    'Analogue protection: PSP On; 2 line Split Burst On',  'Analogue protection: PSP On; 4 line Split Burst On',
]

XDS_DAY_OF_WEEK = {
    0x41 : 'Sun',
    0x42 : 'Mon',
    0x43 : 'Tue',
    0x44 : 'Wed',
    0x45 : 'Thu',
    0x46 : 'Fri',
    0x47 : 'Sat',
}

XDS_MONTH = {
    0x1 : 'Jan',
    0x2 : 'Feb',
    0x3 : 'Mar',
    0x4 : 'Apr',
    0x5 : 'May',
    0x6 : 'Jun',
    0x7 : 'Jul',
    0x8 : 'Aug',
    0x9 : 'Sep',
    0xa : 'Oct',
    0xb : 'Nov',
    0xc : 'Dec',
}

CC_CHANNEL_TO_FIELD = {
    'CC1': 0,
    'CC2': 0,
    'CC3': 1,
    'CC4': 1,
}

CC_DATA_CHANNEL = {
    'CC1': 1,
    'CC2': 2,
    'CC3': 1,
    'CC4': 2,
}

CC_CHANNEL_TO_TEXT_CHANNEL = {
    'CC1': 'T1',
    'CC2': 'T2',
    'CC3': 'T3',
    'CC4': 'T4',
}

def memoize(f):
    """ Memoization decorator for performance on inner loop"""

    class Memodict(dict):
        def __getitem__(self, *key):
            return dict.__getitem__(self, key)

        def __missing__(self, key):
            ret = self[key] = f(*key)
            return ret

    return Memodict().__getitem__

@memoize
def is_control(byte1, byte2):
    return (byte1, byte2) in ALL_CC_CONTROL_CODES

@memoize
def decode_byte_pair(control, byte1, byte2, default_unicode=True):
    """ Decode a pair of bytes"""
    controlcode = (byte1, byte2)
    if control:
        return ALL_CC_CONTROL_CODES.get(controlcode)
    if controlcode in ALL_SPECIAL_CHARS:
        return ALL_SPECIAL_CHARS.get(controlcode)
    if 0x10 <= byte1 <= 0x1F:
        return '?b1(%02x)?b2(%02x)' % (byte1, byte2) if default_unicode else '' # CEA-608-E 6.3
    return '' + CC_TABLE.get(byte1, '?b1(%02x)' % (byte1) if default_unicode else "") + \
           CC_TABLE.get(byte2, '?b2(%02x)' % (byte2) if default_unicode else "")

def precompute_sine_templates(image_width, preamble_run_in_count):
    # granularity of period width
    min_clock_len = round(MIN_CLOCK_FRACTION * image_width) # lower boundary for period width
    max_clock_len = round(MAX_CLOCK_FRACTION * image_width) # upper boundary for period width
    num_steps = 5 # fractional amount to search for pixel width

    steps = (max_clock_len - min_clock_len) * num_steps
    search_widths = np.linspace(min_clock_len, max_clock_len, steps)

    templates = []
    # precompute the preamble clock run-in search parameters
    for i in range(len(search_widths)):
        pixels_per_cycle = search_widths[i]
        run_len = round(preamble_run_in_count * pixels_per_cycle)
        max_width = round((preamble_run_in_count + START_BIT_COUNT + DATA_BIT_COUNT) * pixels_per_cycle)

        if max_width >= image_width:
            # any match will be too long to fit in a line
            break

        template = np.concatenate((
            -np.sin(2 * np.pi * (np.arange(run_len) / pixels_per_cycle - preamble_run_in_count)), #    clock run in, CEA-608-E Table 2
            np.full(round(START_BIT_ZEROS_COUNT * pixels_per_cycle), -1), # 0,0 at 0 IRE
            np.full(round(START_BIT_ONES_COUNT * pixels_per_cycle), 1) #    1
        ))
        template -= template.mean()
        var_t = np.sum(template ** 2)

        lag_count = image_width - len(template) + 1

        templates.append((pixels_per_cycle, max_width, run_len, template, len(template),
                          var_t, lag_count))

    # ---- ONE SCORING PASS FOR ALL OF THEM ----
    #
    # Every template's lags laid end to end in one flat run, so the scoring is nine array
    # operations for the whole search rather than nine for each template. At these sizes a
    # numpy call costs more than the arithmetic it does, so the number of calls is what the
    # search costs, and doing it flat is about four times fewer.
    #
    # `lows` and `highs` index the line's prefix sums for each lag's window, and `scale`
    # and `spread` carry each template's own constants out to every lag that belongs to it.
    total = sum(count for _, _, _, _, _, _, count in templates)
    lows = np.empty(total, dtype=np.intp)
    highs = np.empty(total, dtype=np.intp)
    scale = np.empty(total)
    spread = np.empty(total)   # the window length, to divide by

    at = 0
    spans = []
    for pixels_per_cycle, max_width, run_len, template, template_len, var_t, count in templates:
        lags = np.arange(count, dtype=np.intp)
        lows[at:at + count] = lags
        highs[at:at + count] = lags + template_len
        scale[at:at + count] = var_t
        spread[at:at + count] = template_len
        spans.append((at, count, template, template_len, max_width, run_len, pixels_per_cycle))
        at += count

    return PreambleSearch(tuple(spans), lows, highs, scale, spread,
                          np.empty(total), np.empty(total), np.empty(total))

class PreambleSearch:
    """ Every preamble template, arranged so one pass scores all of them """

    __slots__ = ('spans', 'lows', 'highs', 'scale', 'spread', 'conv', 'sums', 'variance')

    def __init__(self, spans, lows, highs, scale, spread, conv, sums, variance):
        self.spans = spans
        self.lows = lows
        self.highs = highs
        self.scale = scale
        self.spread = spread
        self.conv = conv
        self.sums = sums
        self.variance = variance

def sync_to_preamble(img, row):
    # synchronize to the clock run in sine wave as well as the three start bits
    # Read and normalize line
    line = band_limit(img[row])

    line_min, line_max = line.min(), line.max()
    if line_min == line_max:
        return None

    norm = (line - line_min) / (line_max - line_min)
    norm_len = len(norm)

    # ---- CLOCK RUN-IN MATCH ----
    # Cumulative sums for fast windowed variance, padded with a leading zero so a
    # window is one subtraction of two slices - the unpadded form needed a fresh
    # concatenate per template per line, which is 600,000 allocations over a capture.
    cumsum = np.empty(norm_len + 1)
    cumsum2 = np.empty(norm_len + 1)
    cumsum[0] = cumsum2[0] = 0.0
    np.cumsum(norm, out=cumsum[1:])
    np.cumsum(norm * norm, out=cumsum2[1:])

    search = PRE_COMPUTED_PREAMBLE_TEMPLATES
    if not search.spans:
        # no template fits a line this narrow, so there is nothing here to find
        return None

    # correlate every template over the line, into one flat run
    for at, count, template, template_len, _, _, _ in search.spans:
        search.conv[at:at + count] = np.correlate(norm, template, mode='valid')

    # then score the whole run at once. This is the same normalized correlation as before,
    # conv squared over the windowed variance of the line times the template's own, with
    # each template's constants carried alongside its lags rather than applied in a loop.
    sums = search.sums
    variance = search.variance
    np.subtract(cumsum[search.highs], cumsum[search.lows], out=sums)
    np.subtract(cumsum2[search.highs], cumsum2[search.lows], out=variance)
    np.multiply(sums, sums, out=sums)
    np.divide(sums, search.spread, out=sums)
    np.subtract(variance, sums, out=variance)
    np.multiply(variance, search.scale, out=variance)
    variance += 1e-12

    score = np.multiply(search.conv, search.conv, out=search.conv)
    score /= variance

    # the best lag of each template, and the template whose best both fits and wins
    best_score = -np.inf
    preamble_start = None
    preamble_run_in_len = None
    bit_width = None

    for at, count, template, _, max_width, run_len, pixels_per_cycle in search.spans:
        window = score[at:at + count]
        idx = int(window.argmax())
        if idx + max_width >= norm_len:
            # best match would be too long to fit in a line
            continue

        if window[idx] > best_score:
            best_score = window[idx]
            preamble_start = idx
            preamble_run_in_len = run_len
            bit_width = pixels_per_cycle
            run_in = template[:run_len]

    if preamble_start is None:
        return None

    # CEA-608-E 5.2: without its third start bit a line locks one bit late, onto the next 0,0,1.
    # Across the KET and KCET captures the run-in one bit before a real lock never fits past 0.85.
    earlier = preamble_start - round(bit_width)
    if earlier >= 0:
        fit_earlier = np.corrcoef(norm[earlier:earlier + preamble_run_in_len], run_in)[0, 1]
        if fit_earlier >= 0.9 and fit_earlier > np.corrcoef(norm[preamble_start:preamble_start + preamble_run_in_len], run_in)[0, 1]:
            preamble_start = earlier

    preamble_end = preamble_start + preamble_run_in_len

    return {
        "normalized_line": norm,
        "preamble_start": preamble_start,
        "preamble_end": preamble_end,
        "bit_width": bit_width,
        "score": best_score,
        "cumsum": cumsum,
        "cumsum2": cumsum2,
    }

def bit_window(bit_index, bit_width, bit_padding, normalized_line, preamble_end):
    """ The samples this bit was sliced from """
    start = preamble_end + bit_index * bit_width
    s = round(start) + bit_padding
    e = round(start + bit_width) - bit_padding

    return normalized_line[s:e]

def get_bit_value(bit_index, bit_width, bit_padding, normalized_line, normalized_median,
                  preamble_end):
    """ Just the bit, for the start bits, whose settledness nothing asks about """
    seg = bit_window(bit_index, bit_width, bit_padding, normalized_line, preamble_end)

    # `ndarray.mean` is the same arithmetic with several layers of dtype handling on
    # top, and this runs about ninety thousand times a second
    return 1 if seg.sum() / seg.size > normalized_median else 0

# How many times the slicing level is re-measured from the cells it has just decided, and
# how many cells each level needs before its drift is worth fitting rather than assuming.
LEVEL_FIT_PASSES = 2
MIN_CELLS_PER_LEVEL = 3

def _level_line(cells, high, side):
    """ Where one of the two levels sits at each cell, as a straight line fitted to it

    Least squares in closed form rather than `polyfit`, which would be called twice a pass
    on every line of a capture and does not need to solve a general system to do it.
    """
    count = 0
    sum_x = sum_y = 0.0
    for index, value in enumerate(cells):
        if high[index] is side:
            count += 1
            sum_x += index
            sum_y += value

    mean_x = sum_x / count
    mean_y = sum_y / count

    covariance = variance = 0.0
    for index, value in enumerate(cells):
        if high[index] is side:
            offset = index - mean_x
            covariance += offset * (value - mean_y)
            variance += offset * offset

    if variance == 0.0:
        return [mean_y] * len(cells)

    slope = covariance / variance
    return [slope * (index - mean_x) + mean_y for index in range(len(cells))]

def decode_bytes(normalized_line, preamble_start, preamble_end, bit_width, best_score, debug_plot, cumsum, cumsum2):
    """ Slice the two data bytes out of a line

    Both bytes come back as the eight bits that were on the wire, low bit first, with
    no interpretation of the eighth: CEA-608 uses it for odd parity, StarSight uses it
    for data, and which it is belongs to the consumer rather than here. `noisy` flags
    the bits whose sample window was too unsettled to trust, one flag per data bit,
    which is what the CEA-608 layer needs to correct a single bit error.

    `confidence` is the other half of that, and the one StarSight needs, since StarSight
    spends the eighth bit on data and so has no parity to lean on. It is one number per
    data bit: how far that bit sat from the slicing level, over how far a bit on this line
    sits from it typically. A bit that reads 1.0 is as clear as this line gets and one that
    reads near 0 was all but a coin toss, which is what a tape dropout leaves behind.
    """
    # Fraction of each bit cell to drop at its edges before averaging what is left. The
    # edges are where the channel's ringing from the previous cell still sits, so they
    # carry that cell as much as this one; sampling the middle half instead is the usual
    # answer and measures better here. On the 1994 KCET tape it is worth 82 packets to 87;
    # on both 1998 KET captures it changes nothing at all - 337 and 397 packets at 0.1,
    # 0.25 and 0.35 alike - because a clean line decodes either way. Anywhere in 0.2 to
    # 0.4 measures the same on this evidence, so this is the middle of that range rather
    # than the best single number in it.
    bit_width_padding = 0.25

    # ---- BIT DECODING ----
    preamble = normalized_line[round(preamble_start):round(preamble_end)]
    normalized_median = preamble.sum() / preamble.size
    bit_padding = math.ceil(bit_width_padding * bit_width)

    # assert start bits
    #
    # The three are 0, 0, 1, but only the last two are asserted. The first sits directly
    # against the end of the clock run-in, and on a tape the run-in's last cycle can
    # overshoot and decay across it, so it slices as 1 on a line that is otherwise
    # perfect. Measured on the 1994 KCET VHS capture, that single bit was rejecting 89
    # StarSight lines in 8,180 - and since a StarSight packet spans about a hundred
    # fields, each lost line destroys a whole packet. Asserting the last two costs
    # nothing where the signal is clean: over 20,000 fields of both 1998 KET captures it
    # admits no line that the strict form did not, and the 11 lines it adds on the 1994
    # tape all carry valid CEA-608 parity, so they are recovered data rather than noise.
    if (
        get_bit_value(1, bit_width, bit_padding, normalized_line, normalized_median, preamble_end) != 0
        or get_bit_value(2, bit_width, bit_padding, normalized_line, normalized_median, preamble_end) != 1
    ):
        return None, None, 0, ()

    # ---- SLICING LEVEL ----
    #
    # The run-in's own mean is only a first guess at where to slice. It is measured at the
    # head of the line and then asked to hold for all nineteen cells after it, and on tape
    # it does not: measured over the 1994 KCET capture the two data levels sit at -0.348
    # and +0.318 about it, so their real midpoint is 0.015 below where the run-in puts it,
    # and the eye closes from 0.675 at the middle of the line to 0.628 at the last bit.
    #
    # Both are correctable from the line itself, because every cell is a sample of one
    # level or the other. Taking the cells apart by which side they fell and fitting a line
    # through each gives the two levels where they actually are, and the midpoint of those
    # is where to slice. Worth 107 packets to 115 on that capture; on both 1998 KET
    # captures it changes nothing, 337 and 397 either way, because a clean line's levels do
    # not move.
    #
    # The three start bits go into the fit on the same footing as the rest, by which side
    # they fell, rather than being pinned to the 0, 0, 1 they are known to be. Pinning them
    # measures identically - 140 packets and the same captions either way - because the
    # assertion above has already settled the only two that could have gone the other way.
    # Each cell's mean and spread is two subtractions off the prefix sums, so the whole
    # line is read once rather than once per cell. Nineteen cells of a dozen samples is
    # far too little data to hand to numpy a cell at a time - the call overhead is worth
    # more than the arithmetic - so the fitting below is plain Python over plain floats.
    limit = len(normalized_line)

    # Every cell boundary, rounded once. The loop used to round both ends of each cell,
    # which rounds every boundary twice over - and at two hundred and sixty thousand calls
    # over six thousand lines that was a twelfth of the slicer's time.
    edges = [round(preamble_end + index * bit_width)
             for index in range(START_BIT_COUNT + DATA_BIT_COUNT + 1)]

    cells = []
    spreads = []
    for index in range(START_BIT_COUNT + DATA_BIT_COUNT):
        first = edges[index] + bit_padding
        last = edges[index + 1] - bit_padding
        if first < 0 or last > limit or last <= first:
            return None, None, 0, ()
        width = last - first
        # native floats from here on: these are scalars, and numpy's are several times
        # dearer to add and compare than Python's own
        mean = float(cumsum[last] - cumsum[first]) / width
        cells.append(mean)
        spreads.append(float(cumsum2[last] - cumsum2[first]) / width - mean * mean)

    threshold = [float(normalized_median)] * len(cells)
    for _ in range(LEVEL_FIT_PASSES):
        high = [value > level for value, level in zip(cells, threshold)]
        if high.count(True) < MIN_CELLS_PER_LEVEL or high.count(False) < MIN_CELLS_PER_LEVEL:
            break
        low_line = _level_line(cells, high, False)
        high_line = _level_line(cells, high, True)
        threshold = [0.5 * (a + b) for a, b in zip(low_line, high_line)]

    margins = [abs(value - level)
               for value, level in zip(cells[START_BIT_COUNT:], threshold[START_BIT_COUNT:])]
    bits = [1 if value > level else 0
            for value, level in zip(cells[START_BIT_COUNT:], threshold[START_BIT_COUNT:])]

    noisy = 0
    for bit_index, variance in enumerate(spreads[START_BIT_COUNT:]):
        if variance > MIN_STD_DEV_FOR_CORRECTION * MIN_STD_DEV_FOR_CORRECTION:
            noisy |= 1 << bit_index

    # Scale by this line's own typical margin rather than a fixed level, so the number
    # means the same thing on a strong line and a weak one. The median is the right middle
    # here because the bits worth flagging are exactly the outliers.
    eye = sorted(margins)[len(margins) // 2]
    confidence = tuple(m / eye for m in margins) if eye > 0 else (0.0,) * DATA_BIT_COUNT

    byte1 = sum(bit << i for i, bit in enumerate(bits[0:8]))
    byte2 = sum(bit << i for i, bit in enumerate(bits[8:16]))

    if debug_plot:
        start_bits = [get_bit_value(i, bit_width, bit_padding, normalized_line,
                                    normalized_median, preamble_end)
                      for i in range(START_BIT_COUNT)]
        show_debug_plot(
            normalized_line,
            round(preamble_start),
            round(preamble_end),
            round(bit_width),
            best_score,
            start_bits + bits,
            bit_width,
            bit_width_padding,
            [byte1, byte2],
            [cea608_byte(byte1, noisy, 0)[1], cea608_byte(byte2, noisy, 8)[1]]
        )

    return byte1, byte2, noisy, confidence

def show_debug_plot(line, preamble_start, preamble_end, width, best_score, bits, bit_width, bit_width_padding, byte_data, byte_parity):
    import numpy as np
    import matplotlib.pyplot as plt

    line = np.asarray(line, dtype=float)
    n = len(line)

    # Compute mid and amplitude from preamble region
    mid = np.mean(line[preamble_start:preamble_end])
    high = np.max(line[preamble_start:preamble_end])
    low = np.min(line[preamble_start:preamble_end])
    amplitude = (high - low) / 2

    # Generate full sine reference
    t = np.arange(n)
    phase_offset = 2 * np.pi * preamble_start / width
    sine_full = mid + amplitude * np.sin(2 * np.pi * t / width - phase_offset)

    fig, ax = plt.subplots(figsize=(12, 5))

    # Plot waveform and sine
    ax.plot(line, label='Line Waveform', color="black")
    ax.plot(sine_full, label='Clock', color='red', alpha=0.7)

    # Clock run-in markers
    ax.axvline(preamble_start, color='green', linestyle='--', label='Preamble start')
    ax.axvline(preamble_end, color='purple', linestyle='--', label='Preamble end')

    # ---- Bit highlighting ----
    bit_count = len(bits)
    bit_padding = bit_width_padding * width
    starts = preamble_end + np.arange(bit_count) * width + bit_padding
    ends = starts + width - 2 * bit_padding

    for s, e, b in zip(starts, ends, bits):
        color = 'blue' if b == 1 else 'orange'
        ax.axvspan(s, e, color=color, alpha=0.3)
        ax.text((s + e) / 2,
                mid + amplitude * 1.1,
                str(b),
                color='black',
                ha='center',
                va='bottom')

    # Labels and legend
    ax.set_xlabel('Position (Width)')
    ax.set_ylabel('Amplitude')
    ax.set_title('Bit Decoding Debug Plot')
    ax.legend()

    # ---- Byte information box BELOW plot, left-aligned to axes ----
    info_lines = []

    for i, (byte_val, parity_ok) in enumerate(zip(byte_data, byte_parity)):
        byte_num = i + 1
        hex_text = f"0x{byte_val:02X}"
        parity_text = "GOOD" if parity_ok else "BAD"
        info_lines.append(f"Byte {byte_num}: {hex_text} Parity: {parity_text}")

    control = (byte_data[0], byte_data[1]) in ALL_CC_CONTROL_CODES
    info_text = f'Score: {round(best_score, 3)} | Bit Width: {round(bit_width, 3)} | {" | ".join(info_lines)} | Code: "{decode_byte_pair(control, byte_data[0], byte_data[1])}"'

    # Make room at bottom
    plt.subplots_adjust(bottom=0.2)

    # Get axes position in figure coordinates
    pos = ax.get_position()

    # Place box aligned to left edge of inner axes
    fig.text(
        pos.x0,           # left edge of axes
        0.05,             # vertical position below plot
        info_text,
        ha='left',
        va='center',
        fontsize=11,
        bbox=dict(
            boxstyle="round",
            facecolor="white",
            edgecolor="black",
            alpha=0.95
        )
    )

    plt.show()

def decode_row(img, row, min_correlation, debug_plot):
    """ Slice one line, as `(row, byte1, byte2, noisy, confidence)`, or None

    The whole of the slicing, for one line. No format is assumed: the bytes come back as
    they were on the wire and it is for each consumer - captions, XDS, StarSight - to say
    what the eighth bit of each means. The decoder gives every line a process of its own
    and calls this, since the lines of one field have nothing to say to each other.

    None means the line carried nothing this field - no run-in correlated well enough to
    be worth slicing. A row whose start bits did not check out comes back with both bytes
    None instead, because the line was there and the slicing is what failed, and the two
    are not the same thing to a consumer counting what it missed.
    """
    match = sync_to_preamble(img, row)
    if match is None or match["score"] <= min_correlation:
        return None

    byte1, byte2, noisy, confidence = decode_bytes(
        match["normalized_line"],
        match["preamble_start"],
        match["preamble_end"],
        match["bit_width"],
        match["score"],
        debug_plot,
        match["cumsum"],
        match["cumsum2"],
    )

    return (row, byte1, byte2, noisy, confidence)

def cea608_byte(byte, noisy, offset):
    """ Apply the CEA-608 byte layer to one raw byte

    CEA-608 carries seven data bits and odd parity in the eighth. A single data bit
    that was sliced from an unsettled window is corrected from the parity bit, which
    is what the parity is there for. Returns `(value, parity_ok)`.
    """
    data = byte & 0x7F
    parity_bit = byte >> 7
    calculated = (bin(data).count('1') + 1) % 2

    errors = [i for i in range(7) if noisy & (1 << (offset + i))]
    if (
        len(errors) == 1                              # only one data bit error
        and parity_bit != calculated                  # parity miss-match
        and not noisy & (1 << (offset + 7))           # parity bit is probably good
    ):
        data ^= 1 << errors[0]
        calculated = parity_bit

    return data, parity_bit == calculated

def cea608_parity_ok(byte):
    """ True if a raw byte carries valid CEA-608 odd parity

    Seven data bits plus an odd parity bit means all eight sum odd. A line that fails
    this far more often than noise explains is not carrying captions.
    """
    return bin(byte).count('1') % 2 == 1

class LineParityRate:
    """ How often each line's bytes carry valid CEA-608 odd parity

    Shared measurement, because it is what tells the VBI services apart: a caption
    line passes on very nearly every field, while StarSight - which uses the eighth bit
    for data - and noise that merely correlated with the preamble pass at chance. Each
    format decides for itself which side of that it wants.
    """

    MIN_FIELDS = 20

    def __init__(self):
        self._fields = Counter()
        self._passed = Counter()

    def update(self, row_num, byte1, byte2):
        self._fields[row_num] += 1
        if cea608_parity_ok(byte1) and cea608_parity_ok(byte2):
            self._passed[row_num] += 1

    def rate(self, row_num):
        """ None until there are enough fields to judge the line """
        seen = self._fields[row_num]
        if seen < self.MIN_FIELDS:
            return None
        return self._passed[row_num] / seen

    def seen(self, row_num):
        """ How many fields this line has decoded on """
        return self._fields[row_num]

# What a VBI line is carrying. One line carries one service - a line is never shared - so
# naming it once is enough for the whole pipeline to route by.
LINE_CAPTIONS = 'captions'
LINE_STARSIGHT = 'starsight'
LINE_NOTHING = 'nothing'

class LineService:
    """ Which service a line carries, from the one measurement that tells them apart

    CEA-608 spends the eighth bit of each byte on odd parity and so passes it on very
    nearly every field; StarSight spends it on data and so passes at chance, as does a line
    of noise that merely correlated with the preamble. What separates those last two is how
    often the line is there at all: the guide is sent on every field, noise only sometimes.

    Unmeasured lines are called captions, which is not a guess but the behavior the two
    classes this replaced had between them: the caption side accepted a line while it was
    still being measured, so captions are not held up at the start of a file, and the guide
    side refused one, so guide data is never read from a line that has not been judged.
    """

    MIN_PARITY_RATE = 0.75
    MIN_PRESENCE_RATE = 0.5

    def __init__(self):
        self._parity = LineParityRate()
        self._frames = 0

    def frame(self):
        """ One more frame has been sliced, whether or not this line decoded on it """
        self._frames += 1

    def update(self, byte1, byte2):
        self._parity.update(0, byte1, byte2)

    def service(self):
        rate = self._parity.rate(0)
        if rate is None or rate >= self.MIN_PARITY_RATE:
            return LINE_CAPTIONS
        if self._parity.seen(0) >= self.MIN_PRESENCE_RATE * max(self._frames, 1):
            return LINE_STARSIGHT
        return LINE_NOTHING

class Cea608Lines:
    """ Which lines the caption formats should read

    Every line in range is sliced and offered to every format, so the caption side has
    to turn away lines that are not CEA-608. A line is accepted while it is still
    being measured, so captions are never held up at the start of a file, and dropped
    once its parity rate says it is carrying something else.
    """

    MIN_PARITY_RATE = 0.75

    def __init__(self):
        self._parity = LineParityRate()
        self._rejected = set()

    def update(self, row):
        row_num, byte1, byte2 = row[:3]
        if byte1 is None:
            return

        self._parity.update(row_num, byte1, byte2)
        rate = self._parity.rate(row_num)
        if rate is None:
            return

        if rate < self.MIN_PARITY_RATE:
            self._rejected.add(row_num)
        else:
            self._rejected.discard(row_num)

    def accepts(self, row_num):
        return row_num not in self._rejected

def decode_cea608_row(row):
    """ Turn one raw row into the CEA-608 tuple its consumers expect

    Returns None for a row the spec says to drop: a control code whose second byte
    failed parity.
    """
    row_num, byte1, byte2, noisy = row[:4]

    if byte1 is None:
        # the start bits did not check out, so there are no bytes to read
        return (row_num, decode_byte_pair(False, 0x7f, 0x7f), False, 0x7f, False, 0x7f, False)

    b1, b1_parity = cea608_byte(byte1, noisy, 0)
    b2, b2_parity = cea608_byte(byte2, noisy, 8)

    control = (b1, b2) in ALL_CC_CONTROL_CODES

    # handle parity errors
    # https://www.law.cornell.edu/cfr/text/47/79.101
    if not b2_parity:
        if control:
            return None
        b2 = 0x7f

    if not b1_parity and 0x10 <= b1 <= 0x1F:
        # a damaged control code copy: on the KCET 1994 tape, 72 of 73 sit beside their clean twin
        return None

    if not b1_parity:
        control = False # treat this as a print character when parity fails
        b1 = 0x7f

    return (row_num, decode_byte_pair(control, b1, b2), control, b1, b1_parity, b2, b2_parity)

def decode_cea608_rows(rows, lines=None):
    """ The CEA-608 byte layer over one frame of raw rows

    Pass a `Cea608Lines` to have lines that are not carrying captions dropped. The
    debug and status writers pass nothing, since they are there to show every line.
    """
    decoded = []
    for row in rows:
        if lines is not None:
            lines.update(row)
            if not lines.accepts(row[0]):
                continue

        converted = decode_cea608_row(row)
        if converted is not None:
            decoded.append(converted)
    return decoded

def get_output_function(extension, output_filename, end="\n"):
    if output_filename is not None:
        f = open(output_filename + f".{extension}", 'w')
        out_func = lambda out : print(out, end=end, file=f)
    else:
        f = None
        out_func = lambda out : print(out, end=end)

    return out_func, f

def decode_captions_raw(rx, output_filename, options):
    """ Raw output, show the frame caption codes and frame numbers
         rx                 - input connection for decoded cc data
         merge_text         - merge runs of text together and display in a block
         fixed_line         - check a particular line for cc-signal (and no others)
         ccfilter           - ignored 
         output_filename    - file name without extension to save decoded captions
    """
    setproctitle(current_process().name)
    buff = {}  # CC Buffer, per line
    frame = 0
    lines = Cea608Lines()

    out_func, f = get_output_function("captions.raw", output_filename)

    while True:
        try:
            rows = rx.recv()
            if rows == "DONE":
                break
        except:
            break

        for row in decode_cea608_rows(rows, lines):
            row_num, code, control, b1, _, b2, _ = row

            if code is None:
                out_func('%i %i skip - no preamble' % (frame, row_num))
            else:
                if code and not control:
                    buff[row_num] = buff.get(row_num, '') + code
                elif buff.get(row_num):
                    out_func('%i %i - [%02x, %02x] - Text:%s' % (frame, row_num, b1, b2, buff.pop(row_num)))
                if control:
                    out_func('%i %i - [%02x, %02x] - %s' % (frame, row_num, b1, b2, code))
        frame += 1

    for row_num, text in buff.items():
        if text:
            out_func('%i %i - Text:%s' % (frame, row_num, text))

    if f is not None:
        f.close()

def decode_captions_debug(rx, output_filename, options):
    setproctitle(current_process().name)
    frame = 0
    codes = []

    out_func, f = get_output_function("captions.debug", output_filename)

    while True:
        try:
            rows = rx.recv()
            if rows == "DONE":
                break
        except:
            break

        for row in decode_cea608_rows(rows):
           row_num, code, _, b1, b1_parity, b2, b2_parity = row

           if code is None:
               out_func('%i %i skip - no preamble' % (frame, row_num))
           else:
               out_func('%i %i - bytes: 0x%02x 0x%02x - parity: %s %s: %s' % (frame, row_num, b1, b2, 'T' if b1_parity else 'F', 'T' if b2_parity else 'F', code))
               codes.append([b1, b2])
        frame += 1
    
    if f is not None:
        f.close()
    
    return codes

def scc_timecode(frames):
    """ Return a drop frame SCC timecode for a frame number """
    frame_number = frames + 18 * (frames // 17982) + 2 * max(((frames % 17982) - 2) // 1798, 0)
    frs = frame_number % 30
    s = (frame_number // 30) % 60
    m = ((frame_number // 30) // 60) % 60
    h = (((frame_number // 30) // 60) // 60) % 24
    return '%02d:%02d:%02d;%02d' % (h, m, s, frs)


class CaptionTrack:
    def __init__(self, cc_track, output_filename, options, extension):
        self._cc_track = cc_track
        self._field_number = CC_CHANNEL_TO_FIELD.get(self._cc_track)
        self._output_filename = output_filename
        self._options = options
        self._extension = extension

        self.prev_code = None
        self.mode = "pop_on"
        self.caption_mode = "pop_on"

        self._buffer_on_screen = []
        self._buffer_off_screen = []
        self._roll_up_buffer = []
        self._text_buffer = []
        self._text_cursor = 0

        self.output_end = "\n"

        self.f = None
        self.f_text = None

    def open(self):
        self.out, self.f = get_output_function(self._cc_track + "." + self._extension, self._output_filename, self.output_end)

    def open_text(self):
        self.out_text, self.f_text = get_output_function(CC_CHANNEL_TO_TEXT_CHANNEL[self._cc_track] + "." + self._extension, self._output_filename, self.output_end)

    def close(self):
        if self.f is not None:
            self.f.close()
        if self.f_text is not None:
            self.f_text.close()

    def add_data(self, data, frames):
        _, code, _, byte1, _, byte2, _ = data
        if code is not None and not (byte1 == 0 and byte2 == 0):
            is_global_code = self._handle_global_control(data, frames)
    
            # write code
            if not is_global_code:
                if self.mode == "pop_on":
                    self.add_off_screen(data)
                elif self.mode == "paint_on":
                    self.add_on_screen(data, frames)
                elif self.mode == "roll_up":
                    self.add_on_screen_roll_up(data, frames)
                elif self.mode == "text":
                    self.add_text(data, frames)
    
            self.prev_code = code

    def _handle_global_control(self, data, frames):
        _, code, _, b1, byte1_parity, _, byte2_parity = data
        
        if not (byte1_parity or byte2_parity):
            # ignore global control status when parity issues
            return False
        if 'Resume Caption Loading' in code:
            if code != self.prev_code:
                self.global_resume_loading(data, frames)
            self.prev_code = code
            return True
        elif 'Resume Direct Captioning' in code:
            if code != self.prev_code:
                self.global_resume_direct(data, frames)
            self.prev_code = code
            return True
        elif 'End of Caption (flip memory)' in code:
            if code != self.prev_code:
                self.global_flip_buffers(data, frames)
            self.prev_code = code
            return True
        elif 'Erase Non-Displayed Memory' in code:
            if code != self.prev_code:
                self.global_erase_non_displayed_memory(data, frames)
            return True
        elif 'Erase Displayed Memory' in code:
            if code != self.prev_code:
                self.global_erase_displayed_memory(data, frames)
            return True
        elif 'Roll-Up Captions' in code:
            if code != self.prev_code:
                self.global_start_roll_up(data, frames)
            return True
        elif 'Resume Text Display' in code:
            if code != self.prev_code:
                self.global_start_text_mode(data, frames)
            return True
        elif 'Text Restart' in code:
            if code != self.prev_code:
                self.global_start_text_mode(data, frames)
                self.global_text_reset(data, frames)
            return True
        else:
            # not a global code
            return False
        
    def _end_roll_up(self):
        if self.caption_mode == "roll_up":
            # CEA-608-E C.10, C.11
            self._buffer_on_screen = self._roll_up_buffer
            self._roll_up_buffer = []

    def global_resume_loading(self, data, frames):
        self._end_roll_up()
        self.mode = self.caption_mode = "pop_on"
        
    def global_resume_direct(self, data, frames):
        self._end_roll_up()
        self.mode = self.caption_mode = "paint_on"

    def global_start_roll_up(self, data, frames):
        _, code, _, _, _, byte2, _ = data

        if self.caption_mode != "roll_up":
            # CEA-608-E C.10
            self.global_erase_displayed_memory(data, frames)
            self.global_erase_non_displayed_memory(data, frames)
        self.mode = self.caption_mode = "roll_up"
        self.roll_up_length = ROLL_UP_LEN[byte2]

    def global_start_text_mode(self, data, frames):
        self.mode = "text"
        if self.f_text is None:
            self.open_text()

    def global_text_reset(self, data, frames):
        raise NotImplemented
        
    def global_flip_buffers(self, data, frames):
        self._end_roll_up()
        buffer_off_screen = self._buffer_off_screen
        self._buffer_off_screen = self._buffer_on_screen
        self._buffer_on_screen = buffer_off_screen
        self.mode = self.caption_mode = "pop_on" # CEA-608-E C.10
    
    def global_erase_non_displayed_memory(self, data, frames):
        self._buffer_off_screen = []

    def global_erase_displayed_memory(self, data, frames):
        if self.caption_mode == "roll_up": # CEA-608-E C.16
            self._roll_up_buffer = []
        else:
            self._buffer_on_screen = []

    def add_on_screen(self, data, frames):
        raise NotImplemented

    def add_off_screen(self, data):
        raise NotImplemented
    
    def add_on_screen_roll_up(self, data, frames):
        raise NotImplemented
    
    def add_text(self, data, frames):
        self._text_buffer.append(data)

    def clear_text(self):
        self._text_buffer = []
    
    def write_text(self, frames):
        if self.f_text is None:
            self.open_text()
    
    def write_caption(self, data, frames):
        if self.f is None:
            self.open()

class SCCCaptionTrack(CaptionTrack):
    def __init__(self, cc_track, output_filename, options):
        super().__init__(cc_track, output_filename, options, "scc")
    
    def open(self):
        super().open()
        self.out('Scenarist_SCC V1.0\n')

    def open_text(self):
        super().open_text()
        self.out_text('Scenarist_SCC V1.0\n')

    def global_resume_loading(self, data, frames):
        super().global_resume_loading(data, frames)
        self._buffer_off_screen.append(data)

    def global_resume_direct(self, data, frames):
        super().global_resume_direct(data, frames)

        self._buffer_on_screen.append(data)
        self.write_caption(self._buffer_on_screen, frames)

    def global_start_roll_up(self, data, frames):
        super().global_start_roll_up(data, frames)
        self.write_caption([data], frames)

    def global_start_text_mode(self, data, frames):
        super().global_start_text_mode(data, frames)
        self.add_text(data, frames)

    def global_text_reset(self, data, frames):
        self.add_text(data, frames)
        self.clear_text()
        
    def global_flip_buffers(self, data, frames):
        super().global_flip_buffers(data, frames)

        self._buffer_on_screen.append(data)
        self.write_caption(self._buffer_on_screen, frames)

    def global_erase_displayed_memory(self, data, frames):
        super().global_erase_displayed_memory(data, frames)

        self.write_caption([data], frames) # send clear screen command
    
    def add_on_screen(self, data, frames):
        self._buffer_on_screen.append(data)
        self.write_caption(self._buffer_on_screen, frames)

    def add_off_screen(self, data):
        self._buffer_off_screen.append(data)

    def add_on_screen_roll_up(self, data, frames):
        self.write_caption([data], frames)

    def add_text(self, data, frames):
        super().add_text(data, frames)
        self.text_frame = frames
        _, code, _, _, _, _, _ = data

        if 'Carriage Return' in code:
            self._write(self.out_text, self._text_buffer, frames)
            self.clear_text()

    def close(self):
        if len(self._text_buffer) > 0:
            self._write(self.out_text, self._text_buffer, self.text_frame)
        super().close()

    def write_text(self, frames):
        super().write_text(frames)
        self._write(self.out_text, self._text_buffer, frames)

    def write_caption(self, data, frames):
        super().write_caption(data, frames)
        self._write(self.out, data, frames)

    def _write(self, out_func, data, frames):
        scc_data = [self._get_subtitle_data(n) for n in data]
        out_func('%s\t%s' % (self._get_timecode(frames), "".join(scc_data)))

    def _get_timecode(self, frames):
        return scc_timecode(frames)
    
    def _get_subtitle_data(self, data):
        _, _, _, byte1, _, byte2, _ = data
        return '%02x%02x ' % (NO_PARITY_TO_ODD_PARITY[byte1], NO_PARITY_TO_ODD_PARITY[byte2])
    
class TextCaptionTrack(CaptionTrack):
    def __init__(self, cc_track, output_filename, options, extension = "txt"):
        super().__init__(cc_track, output_filename, options, extension)

        self.prev_char = None
        self.previous_frames = 0
        self.space_character = " "
        self.line_break_character = "\n"
        self.output_end = ""
        self.pending_indent = None
        self.last_frame = 0

    def close(self):
        if len(self._text_buffer) > 0:
            self.write_text(None)

        super().close()

    def add_data(self, data, frames):
        _, code, _, byte1, _, byte2, _ = data
        self.last_frame = frames
        if byte1 == 0 and byte2 == 0:
            self.prev_code = None # CEA-608-E D.2
        elif 0x10 <= byte1 <= 0x1F and code == self.prev_code:
            self.prev_code = None # CEA-608-E D.2, B.14
            return

        super().add_data(data, frames)

    def global_text_reset(self, data, frames):
        self.clear_text()
        self.pending_indent = None # CEA-608-E 7.4
    
    def row_length(self, caption_text):
        return len(caption_text.split(self.line_break_character)[-1])

    def backspace(self, caption_text):
        if caption_text.endswith(self.line_break_character):
            return caption_text # CEA-608-E B.12
        return caption_text[0:-1]

    def dedupe_bad_data_from_text(self, code):
        if self.prev_char == None:
            self.prev_char = code
            return code
        
        if self.prev_char == '■' and code == '■':
            return ''
        
        self.prev_char = code
        return code
    
    def handle_row(self, code, caption_text, current_row, rows):
        match = re.search(r'row (?P<row_number>\d+)$', code)
        if match:
            row = int(match.group("row_number"))
            if row != current_row:
                # CEA-608-E C.7
                rows[current_row] = caption_text
                caption_text = rows.pop(row, "")
            current_row = row

        return caption_text, current_row
    
    def handle_cr(self, code, caption_text):
        if code.endswith("Carriage Return"):
            caption_text += self.line_break_character

        return caption_text
    
    def handle_bs(self, code, caption_text):
        if code.endswith("Backspace"):
            caption_text = self.backspace(caption_text)

        return caption_text

    def handle_tab(self, code, caption_text):
        tab_match = re.search(r'Tab Offset (?P<tab_offset>\d+)', code)
        if tab_match:
            tab = int(tab_match["tab_offset"])
            tab = max(min(31 - self.row_length(caption_text), tab), 0) # CEA-608-E C.13
            caption_text += self.space_character * tab

        return caption_text

    def handle_indent(self, code, caption_text):
        intent_match = re.search(r'Indent (?P<indent_offset>\d+)', code)
        if intent_match:
            indent = int(intent_match["indent_offset"])
            caption_text += self.space_character * max(indent - self.row_length(caption_text), 0) # CEA-608-E C.7
        
        return caption_text
    
    def handle_character(self, caption_text, has_writable, byte1, byte2):
        code = decode_byte_pair(False, byte1, byte2, False)
        if code is not None:
            if byte1 in (0x12, 0x13, 0x1A, 0x1B) and 0x20 <= byte2 <= 0x3F:
                caption_text = self.backspace(caption_text) # CEA-608-E 6.4.2
            for char in str(code):
               if char != " ":
                   has_writable = True
               caption_text += self.dedupe_bad_data_from_text(char)

        return caption_text, has_writable
    
    def handle_style(self, code, caption_text):
        if "Mid-row" in code:
            # mid row style updates add a space
            caption_text += self.space_character
        elif "Background" in code or "Foreground" in code:
            caption_text = self.backspace(caption_text) + self.space_character # CEA-608-E 6.2

        return caption_text

    def get_caption_text(self, data, text_mode = False):
        rows = {}
        current_row = None
        caption_text = ""
        has_writable = False
        self.prev_char = None
        
        for _, code, control, byte1, byte1_parity, byte2, byte2_parity in data:
            if control:
                # repeated control codes should be filtered out at this point
                if byte1_parity and byte2_parity:
                    if not text_mode: # CEA-608-E 7.4
                        caption_text, current_row = self.handle_row(code, caption_text, current_row, rows)
                    # update style first to avoid using old style while adding spaces
                    caption_text = self.handle_style(code, caption_text)
                    # handle spacing updates
                    caption_text = self.handle_cr(code, caption_text)
                    caption_text = self.handle_bs(code, caption_text)
                    caption_text = self.handle_tab(code, caption_text)
                    caption_text = self.handle_indent(code, caption_text)
            else:
                # get only a printable code (no byte values)
                caption_text, has_writable = self.handle_character(caption_text, has_writable, byte1, byte2)

        rows[current_row] = caption_text
        rows = [rows[row] for row in sorted(rows, key=lambda row: -1 if row is None else row) if row is not None or rows[row]]
        return self.line_break_character.join(rows), has_writable
    
    # only enable text for .txt format
    def add_text(self, data, frames):
        _, code, control, _, _, _, _ = data

        if control:
            if code != self.prev_code:
                # only handle control characters once
                super().add_text(data, frames)
                if 'Carriage Return' in code:
                    self.pending_indent = None
                    self.write_text(frames)
                    self.clear_text()
                elif 'Pre:' in code: # CEA-608-E 7.4
                    # compatibility with TeleCaption I decoder
                    # when there's a data interruption, the decoder resets the cursor to first column
                    # for forwards compatibility, an indent is sent without a carriage return to avoid repeated characters
                    # see ANSI-CEA-608-E, Annex D.3 Text-Mode Multiplexing (Informative), pg. 78
                    #
                    # the reset waits for a character to actually arrive. A text
                    # service sends the indent both before a retransmitted line and
                    # again just before the carriage return that commits it, so
                    # clearing here outright would throw the line away a moment
                    # before it is written.
                    self.pending_indent = len(self._text_buffer) - 1
        else:
            if self.pending_indent is not None:
                # characters are following the indent, so the line is being sent again
                self._text_buffer = self._text_buffer[self.pending_indent:]
                self.pending_indent = None
            super().add_text(data, frames)

        self.previous_frames = frames

    def write_text(self, frames):
        super().write_text(frames)
        caption_text, _ = self.get_caption_text(self._text_buffer, True)
        self.out_text(caption_text)

    def add_on_screen(self, data, frames):
        pass

    def add_off_screen(self, data):
        pass
    
    def add_on_screen_roll_up(self, data, frames):
        pass
    
class SRTCaptionTrack(TextCaptionTrack):
    def __init__(self, cc_track, output_filename, options):
        super().__init__(cc_track, output_filename, options, "srt")

        self.subtitle_count = 1
        self.subtitle_start_frame = 0
        self.subtitle_end_frame = 0

        self.text_count = 1
        self.text_start_frame = 0
        self.text_current_frame = 0
        self.text_end_frame = 0

        self.output_end = "\n"

        self.fps = options["frame_rate"]

    def close(self):
        displayed = self._roll_up_buffer if self.caption_mode == "roll_up" else self._buffer_on_screen
        if len(displayed) > 0:
            self.write_caption(displayed, self.last_frame)
        if len(self._text_buffer) > 0:
            self.write_text(self.text_current_frame)

        CaptionTrack.close(self)

    def global_resume_direct(self, data, frames):
        # start a new subtitle entry
        super().global_resume_direct(data, frames)
        if len(self._buffer_on_screen) == 0:
            self.subtitle_start_frame = frames # CEA-608-E C.10

    def global_start_roll_up(self, data, frames):
        starting = self.caption_mode != "roll_up"
        super().global_start_roll_up(data, frames)
        if starting:
            self.subtitle_start_frame = frames

    def global_start_text_mode(self, data, frames):
        super().global_start_text_mode(data, frames)
        if self.text_start_frame == 0:
            self.text_start_frame = frames

    def global_flip_buffers(self, data, frames):
        displayed = self._roll_up_buffer if self.caption_mode == "roll_up" else self._buffer_on_screen
        if len(displayed) > 0:
            self.write_caption(displayed, frames) # CEA-608-E B.8.3
        super().global_flip_buffers(data, frames)
        self.subtitle_start_frame = frames

    def global_erase_displayed_memory(self, data, frames):
        # end the subtitle and write to the screen
        if self.caption_mode == "roll_up":
            if len(self._roll_up_buffer) > 0:
                self.write_caption(self._roll_up_buffer, frames)
        else:
            if len(self._buffer_on_screen) > 0:
                self.write_caption(self._buffer_on_screen, frames)

        # clear the on screen buffer
        super().global_erase_displayed_memory(data, frames)

    def add_on_screen(self, data, frames):
        self._buffer_on_screen.append(data)
        # write the onscreen buffer to screen
        self.write_caption(self._buffer_on_screen, frames)

    def add_off_screen(self, data):
        self._buffer_off_screen.append(data)

    def add_on_screen_roll_up(self, data, frames):
        _, code, _, _, _, _, _ = data
        if code.endswith("Carriage Return"):
            # CEA-608-E 7.4
            if len(self._roll_up_buffer) > 0:
                self.write_caption(self._roll_up_buffer, frames)
            self.subtitle_start_frame = frames

            line_breaks = [i for i, d in enumerate(self._roll_up_buffer) if d[1].endswith("Carriage Return")]
            if len(line_breaks) >= self.roll_up_length - 1:
                self._roll_up_buffer = self._roll_up_buffer[line_breaks[-(self.roll_up_length - 1)] + 1:]
            if len(self._roll_up_buffer) == 0:
                return

        self._roll_up_buffer.append(data)

    def add_text(self, data, frames):
        super().add_text(data, frames)
        self.text_current_frame = frames

    def write_text(self, frames):
        self.text_end_frame = frames
        caption_text, has_writable = self.get_caption_text(self._text_buffer, True)
        if has_writable:
            CaptionTrack.write_text(self, frames)
            self._write(
                self.out_text,
                self.text_start_frame,
                self.text_end_frame,
                self.text_count,
                caption_text
            )
            self.text_start_frame = frames
            self.text_count += 1

    def write_caption(self, data, frames):
        CaptionTrack.write_caption(self, data, frames)

        self.subtitle_end_frame = frames
        caption_text, _ = self.get_caption_text(data)
        self._write(
            self.out,
            self.subtitle_start_frame,
            self.subtitle_end_frame,
            self.subtitle_count,
            caption_text
        )
        self.subtitle_count += 1

    def _write(self, out_func, start_frame, end_frame, count, caption_text):
        out_func(count) # Required by: https://docs.fileformat.com/video/srt/
        out_func('%s --> %s\n%s\n' % (self._get_timecode(start_frame), self._get_timecode(end_frame), caption_text.rstrip('\n')))
        return True

    def _get_timecode(self, frames):
        """ Returns an SRT format timestamp """
        milliseconds = round(frames * 1000 / self.fps)
        return '%02d:%02d:%02d,%03d' % (milliseconds // 3600000, milliseconds // 60000 % 60,
                                        milliseconds // 1000 % 60, milliseconds % 1000)

class HTMLCaptionTrack(TextCaptionTrack):
    def __init__(self, cc_track, output_filename, options, extension = "html"):
        super().__init__(cc_track, output_filename, options, extension)
        # html colors, must be in hex format
        self.colors = {
            "White": "#FFFFFF",
            "Green": "#20e000",
            "Blue": "#00b7ff",
            "Cyan": "#7fffff",
            "Red": "#c02000",
            "Yellow": "#ffd000",
            "Magenta": "#a070e0",
            "Black": "#000000"
        }
        self.semi_transparent_alpha = "80" # appended to the end of the colors
        self.styles = {
            "Underline": "underline",
            "Italics": "italics"
        }
        self.font_size_normal = "12px"
        self.font_size_double = "24px"

        self.colors_regex = r"\b(" + "|".join(self.colors) + r")\b"
        self.styles_regex = r"\b(" + "|".join(self.styles) + r")\b"

        self.line_break_character = "<br>"
        self._element_line_break = "<!--\n-->"

        self._default_background_color = "background-black"
        self._default_text_color = "text-white"
        self._default_text_style = ""
        self._default_font_style = "caption-font-normal"

        self._background_color = self._default_background_color
        self._text_color = self._default_text_color
        self._text_style = self._default_text_style
        self._font_style = self._default_font_style

    def get_html_start(self, channel_type, channel=None):
        base_style = "body { font-family: monospace, monospace; background-color: black; } pre { margin: 0; display: inline; }"
        text_styles = ".underline { text-decoration: underline; } .italics { font-style: italic; } @keyframes blink { 50% { color: transparent; } } .flashing { animation: blink step-start 1s infinite; }"
        background_colors = ".background-transparent { background-color: transparent; }" + " ".join([f".background-{color_key.lower()} {{ background-color: {color_value}; }}" for color_key, color_value in self.colors.items()])
        background_st_colors = " ".join([f".background-{color_key.lower()}-semi-transparent {{ background-color: {color_value}{self.semi_transparent_alpha}; }}" for color_key, color_value in self.colors.items()])
        text_colors = " ".join([f".text-{color_key.lower()} {{ color: {color_value}; }}" for color_key, color_value in self.colors.items()])
        font_sizes = f".caption-font-normal {{ font-size: {self.font_size_normal}; }} .caption-font-double {{ font-size: {self.font_size_double}; }}"

        style_tag = "<style>" + "\n".join([base_style, text_styles, background_colors, background_st_colors, text_colors, font_sizes]) + " </style>"

        return f"<!DOCTYPE html><html><head><meta charset='UTF-8'><meta name='description' content='Decoded by https://github.com/eshaz/cc_decoder'><title>{channel or self._cc_track} {channel_type}</title>{style_tag}</head><body>{self._element_line_break}<div id='captions' >{self.get_pre_tag()}"
    
    def get_html_end(self):
        return f"</pre>{self._element_line_break}</div></body></html>"

    def _get_pre_tag(self, styles):
        return f"{self._element_line_break}<pre class='{' '.join([s for s in styles if s.strip()])}'>"
    
    def get_pre_tag(self):
        return self._get_pre_tag([self._font_style, self._background_color, self._text_color, self._text_style])
    
    def get_pre_tag_background_only(self):
        return self._get_pre_tag([self._font_style, self._background_color])

    def open(self):
        super().open()
        self.out(self.get_html_start("Closed Captions"))

    def open_text(self):
        super().open_text()
        self.out_text(self.get_html_start("Text Mode", CC_CHANNEL_TO_TEXT_CHANNEL[self._cc_track]))

    def close(self):
        displayed = self._roll_up_buffer if self.caption_mode == "roll_up" else self._buffer_on_screen
        if len(displayed) > 0:
            self.write_caption(displayed, self.last_frame, True)
        if len(self._text_buffer) > 0:
            self.write_text(self.last_frame)

        html_end = self.get_html_end()
    
        if self.f is not None:
            self.out(html_end)
            self.f.close()
        if self.f_text is not None:
            self.out_text(html_end)
            self.f_text.close()

    def global_flip_buffers(self, data, frames):
        displayed = self._roll_up_buffer if self.caption_mode == "roll_up" else self._buffer_on_screen
        if len(displayed) > 0:
            self.write_caption(displayed, frames, True) # CEA-608-E B.8.3
        super().global_flip_buffers(data, frames)

    def global_erase_displayed_memory(self, data, frames):
        # end the subtitle and write to the screen
        if self.caption_mode == "roll_up":
            if len(self._roll_up_buffer) > 0:
                self.write_caption(self._roll_up_buffer, frames, True)
        else:
            if len(self._buffer_on_screen) > 0:
                self.write_caption(self._buffer_on_screen, frames, True)

        # clear the on screen buffer
        super().global_erase_displayed_memory(data, frames)

    def handle_row(self, code, caption_text, current_row, rows):
        caption_text, row = super().handle_row(code, caption_text, current_row, rows)
        if row != current_row:
            self._background_color = self._default_background_color # CEA-608-E 6.2
        return caption_text, row

    def handle_cr(self, code, caption_text):
        if code.endswith("Carriage Return"):
            # CEA-608-E 6.2, C.14
            self._background_color = self._default_background_color
            self._text_color = self._default_text_color
            self._text_style = self._default_text_style
            caption_text = f"{caption_text}{self.line_break_character}</pre>{self.get_pre_tag()}"
        return caption_text

    def row_length(self, caption_text):
        return len(unescape(re.sub(r'<[^>]*>', '', caption_text.split(self.line_break_character)[-1])))

    def backspace(self, caption_text):
        return re.sub(r"(&[#\w]+;|[^>])((?:</pre>|<!--\n-->|<pre [^>]*>)*)$", r"\2", caption_text, count=1)

    def handle_style(self, code, caption_text):
        attribute = "Background" in code or "Foreground" in code
        if attribute:
            caption_text = self.backspace(caption_text) # CEA-608-E 6.2

        color = None
        color_match = re.search(self.colors_regex, code)
        if color_match:
            color = color_match[0].lower()

        style_match = re.findall(self.styles_regex, code)

        if "Background" in code:
            # background color update
            if color:
                if "Semi-Transparent" in code:
                    self._background_color = "background-" + color + "-semi-transparent"
                else:
                    self._background_color = "background-" + color
            elif "Background Transparent" in code:
                self._background_color = "background-transparent"
            else:
                self._background_color = self._default_background_color
        else:
            # check for text color / style updates
            is_pre = "Pre:" in code
            is_mid = "Mid-row" in code or "Foreground" in code  # CEA-608-E 6.2

            # mid-row updates for style, (i.e. underline, italics, flashing) without a color specified DO NOT clear the color
            # mid-row updates for color without a style specified DO clear the style
            # pre updates always clear style or color if unset
            clear_other_style = is_pre or (is_mid and color_match)

            if color_match:
                self._text_color = "text-" + color
            elif clear_other_style:
                self._text_color = self._default_text_color

            if style_match:
                self._text_style = " ".join([self.styles[s] for s in style_match])
            elif clear_other_style:
                self._text_style = self._default_text_style

            if "Flash" in code:
                self._text_style += " flashing"

        caption_text = f"{caption_text}</pre>{self.get_pre_tag()}"
        if attribute:
            caption_text += self.space_character

        if "Mid-row" in code:
            # mid row style updates add a space without text color or text style
            caption_text = f"{caption_text}</pre>{self.get_pre_tag_background_only()}{self.space_character}</pre>{self.get_pre_tag()}"

        return caption_text
    
    def dedupe_bad_data_from_text(self, code):
        character = super().dedupe_bad_data_from_text(code)
        character = escape(character)
        return character

    def write_caption(self, data, frames, add_line_break = False):
        caption_text, _ = self.get_caption_text(data)
        if add_line_break:
            caption_text += self.line_break_character
        CaptionTrack.write_caption(self, data, frames)
        self.out(caption_text)

    def add_on_screen(self, data, frames):
        self._buffer_on_screen.append(data)
        # write the onscreen buffer to screen
        self.write_caption(self._buffer_on_screen, frames, True)

    def add_off_screen(self, data):
        self._buffer_off_screen.append(data)
    
    def add_on_screen_roll_up(self, data, frames):
        self._roll_up_buffer.append(data)


class CaptionTrackFactory():
    def __init__(self, track_class, output_filename, options):
        self._field_to_active_track = [None, None] # stores the active track for each field
        self._tracks = {} # stores the created tracks
        self._row_to_field = {} # maps the row to the detected field
        self._output_filename = output_filename
        self._track_class = track_class
        self._options = options
        self._lines = Cea608Lines()
        self._misc_codes = Counter() # miscellaneous control codes seen, by row and field
        self._xds_rows = set() # field 2 rows part way through an XDS packet

    def add_data(self, rows, frame):
        for row in decode_cea608_rows(rows, self._lines):
            detected_field = None
            row_num, code, _, b1, b1_parity, b2, b2_parity = row

            # Which field a row carries. Only the miscellaneous control codes differ between
            # the two - 0x14/0x1C on field 1, 0x15/0x1D on field 2 - so a row is field 2 once it
            # has sent more of field 2's.
            if b1_parity and b2_parity and b1 in (0x14, 0x1C, 0x15, 0x1D) and 0x20 <= b2 <= 0x2F:
                self._misc_codes[row_num, b1 & 1] += 1
            field_two = self._misc_codes[row_num, 1] > self._misc_codes[row_num, 0]
            if b1_parity and b2_parity and b1 in (0x14, 0x1C, 0x15, 0x1D) and 0x20 <= b2 <= 0x2F and (b1 & 1) != field_two:
                continue # CEA-608-E 8.4

            # XDS shares field 2 with CC3, CC4, T3 and T4. From an XDS control byte until the
            # next caption control code, the row is carrying XDS, not caption text (CEA-608-E
            # 7.7 and 8.6.2). A first byte in either range has passed parity, since a failed one
            # comes back as 0x7f. Field 1 is left alone: KET's text service sends bytes in
            # 0x01-0x0F as part of its own stream.
            if field_two:
                if 0x01 <= b1 <= 0x0F:
                    self._xds_rows.add(row_num)
                    track = self._field_to_active_track[1]
                    if track is not None:
                        track.prev_code = None # CEA-608-E D.2
                        if track.mode == "text":
                            track.mode = None # CEA-608-E 7.7
                    continue
                if 0x10 <= b1 <= 0x1F:
                    self._xds_rows.discard(row_num)
                elif row_num in self._xds_rows:
                    continue

            # determine field for row
            if b1_parity and b2_parity:
                # the data channel is in the code, and the field is the row's
                data_channel = CC_DATA_CHANNEL.get(code[:3])
                if data_channel is not None:
                    cc_track = 'CC%d' % (data_channel + 2 * field_two)
                    # cc channels have a defined field order
                    detected_field = CC_CHANNEL_TO_FIELD[cc_track]
                    self._row_to_field[row_num] = detected_field

                    # create new track from CC channel, if not existing
                    if cc_track not in self._tracks:
                        self._tracks[cc_track] = self._track_class(cc_track, self._output_filename, self._options)

                    self._field_to_active_track[detected_field] = self._tracks[cc_track]

            # add data
            if row_num in self._row_to_field:
                current_field = self._row_to_field[row_num]
                current_track = self._field_to_active_track[current_field]

                if current_track is not None:
                    current_track.add_data(row, frame)

    def close_tracks(self):
        for track in self._tracks.values():
            track.close()

def decode_to_scc(rx, output_filename, options):
    setproctitle(current_process().name)
    track_factory = CaptionTrackFactory(SCCCaptionTrack, output_filename, options)
        
    frame = 0        
    while True:
        try:
            rows = rx.recv()
            if rows == "DONE":
                break
        except:
            break

        track_factory.add_data(rows, frame)
        frame += 1

    track_factory.close_tracks()

def decode_to_srt(rx, output_filename, options):
    setproctitle(current_process().name)
    track_factory = CaptionTrackFactory(SRTCaptionTrack, output_filename, options)
        
    frame = 0        
    while True:
        try:
            rows = rx.recv()
            if rows == "DONE":
                break
        except:
            break

        track_factory.add_data(rows, frame)
        frame += 1

    track_factory.close_tracks()

def decode_to_text(rx, output_filename, options):
    setproctitle(current_process().name)
    track_factory = CaptionTrackFactory(TextCaptionTrack, output_filename, options)
        
    frame = 0        
    while True:
        try:
            rows = rx.recv()
            if rows == "DONE":
                break
        except:
            break

        track_factory.add_data(rows, frame)
        frame += 1

    track_factory.close_tracks()

def decode_to_html(rx, output_filename, options):
    setproctitle(current_process().name)
    track_factory = CaptionTrackFactory(HTMLCaptionTrack, output_filename, options)
        
    frame = 0        
    while True:
        try:
            rows = rx.recv()
            if rows == "DONE":
                break
        except:
            break

        track_factory.add_data(rows, frame)
        frame += 1

    track_factory.close_tracks()


def compute_xds_packet_checksum(packet_bytes):
    """ Return the true if the xds packet checksum is okay """
    def twos_complement(bitvalue):
        """ Return the passed value translated to a 7 bit two's completement value """
        return 128 - bitvalue if (bitvalue & 0x7f) != 0 else bitvalue

    if packet_bytes:  # Whole packet should sum to zero in two's complement
        return not(sum(twos_complement(b1) + twos_complement(b2) for (b1, b2) in packet_bytes) & 0x07f)
    return False


def _assert_len(xds_inputbytes, minimum):
    """ Asserts that there are least minimum bytes in the passed xds input bytes buffer """
    if len(xds_inputbytes) * 2 < minimum:
        raise RuntimeWarning('Malformed packet')


def decode_xds_string(pbytes):
    """ Return a string from a series of packet bytes """
    xds_string = ''
    while pbytes:
        strbyte1, strbyte2 = pbytes.pop(0)
        if strbyte1 == 0x0f:
            break
        control = is_control(strbyte1, strbyte2)
        xds_string += decode_byte_pair(control, strbyte1, strbyte2)
    return xds_string


def decode_xds_minutes_hours(pbytes, short=False):
    """ Pull minutes, then hours from a packet """
    _assert_len(pbytes, 2)
    minb, hourb = pbytes.pop(0)
    return minb & 63, hourb & 31 if short else hourb & 63


def decode_xds_time_of_day(packet_bytes):
    """ Decode the Time of Day packets """
    _assert_len(packet_bytes, 6)
    pbytes = [b for bytepair in packet_bytes for b in bytepair]  # Flatten the nested list, to make it
    dst = 'D' if (pbytes[1] & 0x20) else 'S'  # Daylight savinggs
    zero_seconds = 'Z' if pbytes[3] & 0x20 else '_'
    tape_delayed = 'T' if pbytes[3] & 0x10 else 'S'
    leap_day = 'L' if pbytes[2] & 0x20 else 'A'
    day_of_month = pbytes[2] & 0x1F  # CEA-608-E Table 15

    month_key = pbytes[3] & 0xF
    month = XDS_MONTH[month_key] if month_key in XDS_MONTH else "--"
    
    day_of_week_key = pbytes[4]
    day_of_week = XDS_DAY_OF_WEEK[day_of_week_key] if day_of_week_key in XDS_DAY_OF_WEEK else "--"

    year = 1990 + ( pbytes[5] - 0x40 )
    minutes = pbytes[0] - 0x40
    hours = pbytes[1] & 0x1F
    return f'TM {hours:0>2}:{minutes:0>2}{dst} {zero_seconds}{tape_delayed}{leap_day} {month} {day_of_month:0>2} {year} {day_of_week}'

def decode_xds_local_time_zone(pbytes):
    """ Decode the Local Time Zone packets """
    _assert_len(pbytes, 2)
    data, _ = pbytes.pop(0)

    tz = data & 0b11111  # CEA-608-E Table 38
    tz = 24 - tz if tz > 11 else -tz
    dst = 'observes DST' if (data & 0b100000) else 'no DST'

    return f'{tz} {dst}'

def decode_xds_content_advisory(pbytes):
    """ Decode content advisory packet, returning a string describing the rating """
    _assert_len(pbytes, 2)
    ca1, ca2 = pbytes.pop(0)
    # a1 a0 are bits 4 and 3 of the first character (CEA-608-E Table 18). Written unbracketed,
    # `>>` binds before `&` and this read the MPA rating's own low bits instead.
    system = (ca1 >> 3) & 3
    rating = ''
    if system == 0 or system == 2:  # MPA
        rating = MPA_RATING[ca1 & 7]
    elif system == 1:  # US TV Parent Guidelines
        rating_code = ca2 & 7  # g2-g0 are in the second character, not the first
        rating = US_TV_PARENTAL_GUIDELINE_RATING[rating_code]
        if rating_code == 2:
            rating += ' Fantasy Violence' if ca2 & 32 else ''
        elif 4 <= rating_code <= 6:
            rating += ' Violence' if ca2 & 32 else ''
            rating += ' Sexual Situations' if ca2 & 16 else ''
            rating += ' Adult Language' if ca2 & 8 else ''
            rating += ' Sexually Suggestive Dialogue' if ca1 & 32 and rating_code != 6 else ''  # CEA-608-E Table 21
    elif system == 3:  # International
        # a2 is bit 5 of the first character and a3 bit 3 of the second (Table 19)
        subsystem = ((ca2 >> 3) & 1, (ca1 >> 5) & 1)
        if subsystem == (0, 0):  # CAD English
            rating = CANADIAN_ENGLISH_RATINGS[ca2 & 7]
        elif subsystem == (0, 1):
            rating = CANADIAN_FRENCH_RATINGS[ca2 & 7]
        else:  # Reserved for some international system
            rating = 'International reserved code %s' % str((ca1, ca2))
    return 'XDS Rating: %s' % rating


def describe_xds_packet(packet_bytes):
    """ Given a set of bytes representing an XDS packet, describe it """
    if packet_bytes:
        if not compute_xds_packet_checksum(packet_bytes):
            return 'XDS Rejected Packet - Incorrect Checksum'
        b1, b2 = packet_bytes.pop(0)
        del packet_bytes[-1:]  # CEA-608-E 8.6.1
        if len(packet_bytes) > 16:  # CEA-608-E 8.6.6
            return 'XDS Rejected Packet - Too Long'
        if b1 in (0x01, 0x03) and b2 <= 0x03:  # CEA-608-E 9.5.2
            pref = ['Current', 'Future'][b1 // 2]
            if b2 == 0x01:  # Program identification number
                _assert_len(packet_bytes, 4)
                if packet_bytes[:2] == [(0x7F, 0x7F)] * 2:  # CEA-608-E 9.5.1.1
                    return 'XDS %s End of Program' % pref
                minutes, hours = decode_xds_minutes_hours(packet_bytes, short=True)
                dateb, monthb = packet_bytes.pop(0)
                tape_delay = '(Tape Delayed)' if (monthb & 16) else ''
                return ('XDS %s Scheduled Start Time: %02i:%02i on Day %02i of Month %02i %s'
                        % (pref, hours, minutes, dateb & 31, monthb & 15, tape_delay))
            elif b2 == 0x02:  # Length and elapsed
                _assert_len(packet_bytes, 2)
                minutes, hours = decode_xds_minutes_hours(packet_bytes)
                msg = 'XDS %s Length of Show: %02i:%02i' % (pref, hours, minutes)
                if packet_bytes:
                    minutes, hours = decode_xds_minutes_hours(packet_bytes)
                    seconds = 0
                    if packet_bytes:
                        seconds = packet_bytes.pop(0)[0] & 63
                    msg += ' XDS %s Elapsed time: %02i:%02i:%02i' % (pref, hours, minutes, seconds)
                return msg
            elif b2 == 0x03:  # Program Name
                return 'XDS %s Program Name: %s' % (pref, decode_xds_string(packet_bytes))
        if b1 == 0x01:
            if b2 == 0x04:  # Program Type
                program_genre = ''
                while packet_bytes:
                    n1, n2 = packet_bytes.pop(0)
                    if n1 == 0x0f:
                        break
                    program_genre += '%s %s ' % (XDS_GENRE_CODES.get(n1, ''), XDS_GENRE_CODES.get(n2, ''))
                return 'XDS Program Genre: %s' % program_genre
            elif b2 == 0x05:  # Content advisory - Vchip !
                return decode_xds_content_advisory(packet_bytes)
            elif b2 == 0x06:  # Audio services
                main, sap = packet_bytes.pop(0)
                main_language = XDS_AUDIO_SERVICES_LANGUAGE[(main >> 3) & 7]
                main_type = XDS_AUDIO_SERVICES_TYPE_MAIN[main & 7]
                sap_language = XDS_AUDIO_SERVICES_LANGUAGE[(sap >> 3) & 7]
                sap_type = XDS_AUDIO_SERVICES_TYPE_SECONDARY[sap & 7]
                return 'XDS Audio Services: Main:%s(%s) Sap:%s(%s)' % (main_language, main_type, sap_language, sap_type)
            elif b2 == 0x07:  # Caption services
                return 'XDS Caption Services'  # TODO
            elif b2 == 0x08:  # Copy and Redistribution Control Packe
                _assert_len(packet_bytes, 2)
                c1, _ = packet_bytes.pop(0)
                copying = XDS_CGMS[(c1 >> 3) & 3]       # CGMS-A is b4 b3
                protection = XDS_CGMS_APS[(c1 >> 1) & 3]  # APS is b2 b1; b0 is ASB
                return 'XDS Copy protection: %s %s' % (copying, protection)
            elif b2 == 0x09:  # Aspect ratio
                _assert_len(packet_bytes, 2)
                startl, endl = packet_bytes.pop(0)
                anamorp = False
                if packet_bytes:
                    anamorp, _ = packet_bytes.pop(0)
                return 'XDS Aspect Ratio: start line: %i end line: %i %s' \
                       % (22 + (startl & 63), 262 - (endl & 63), 'Anamorphic' if anamorp & 1 else '')
            elif b2 == 0x0c:  # Composite packet
                return 'Composite packet 1 %d' % len(packet_bytes)  # TODO - pending confirmation of the spec

            elif b2 == 0x0d:
                return 'Composite packet 2 %d' % len(packet_bytes)  # TODO
            elif 0x10 <= b2 <= 0x17:  # Program description
                return 'XDS Program description line: %i :%s ' % ((b2 - 0x0F), decode_xds_string(packet_bytes))

        if b1 == 0x05:  # Channel Information class
            if b2 == 0x01:  # Network Name (Affiliation)
                return 'XDS Channel Name: %s' % decode_xds_string(packet_bytes)
            if b2 == 0x02:  # Call Letters (Station ID) and Native Channel 
                call_letters = decode_xds_string(packet_bytes)
                if call_letters[4:].isdigit():  # CEA-608-E 9.5.3.2
                    return 'XDS Channel Station Call-Sign: %s Native Channel: %s' % (call_letters[:4].strip(), call_letters[4:])
                return 'XDS Channel Station Call-Sign: %s' % call_letters
            if b2 == 0x03:  # Tape delay
                minutes, hours = decode_xds_minutes_hours(packet_bytes, short=True)
                return 'XDS Channel Tape Delay: %02i:%02i' % (hours, minutes)
            if b2 == 0x04:
                return 'XDS Transmission Signal Identifier (TSID)'

        if b1 == 0x07:  # Misc
            if b2 == 0x01:  # Time of day
                return f'XDS Time of day (UTC): {decode_xds_time_of_day(packet_bytes)}'
            if b2 == 0x02:  # Impulse Capture ID
                return 'XDS Impulse Capture ID'
            if b2 == 0x03:  # Supplemental Data Location
                return 'XDS Supplemental Data Location'
            if b2 == 0x04:  # Local Time Zone
                return f'XDS Local Time Zone: {decode_xds_local_time_zone(packet_bytes)}'
            if b2 == 0x40:  # Out-of-Band Channel Number
                return 'XDS Out-of-Band Channel Number'
            if b2 == 0x41:  # Channel Map Pointer
                return 'XDS Channel Map Pointer'
            if b2 == 0x42:  # Channel Map Header Packet
                return 'XDS Channel Map Header Packet'
            if b2 == 0x43:  # Channel Map Packet
                return 'XDS Channel Map Packet'


        if b1 == 0x09:  # Public service
            if b2 == 0x01:  # Weather advisory WRSAME format
                return 'XDS Public Service - WRSAME message: %s' % str(packet_bytes)  # TODO, the spec is a bit vague
            if b2 == 0x02:  # Weather message
                return 'XDS Public Service - Weather: %s' % decode_xds_string(packet_bytes)

        return 'Could not decode ---> XDS describes: %02x %02x' % (b1, b2)
    return 'XDS - Empty Packet'


def decode_xds_packets(rx, output_filename, options):
    setproctitle(current_process().name)
    frame = 0
    lines = Cea608Lines()

    # Every line is read as its own stream
    # Following one line across the file let any XDS-range byte on another line take it
    # over part way through a packet, and KET's field 1 text service sends hundreds of them.
    #
    # A packet can be interrupted by captions, Text or another packet and resumed later with its
    # Continue code (CEA-608-E 8.6), so each one a line has begun is kept until its End.
    current = {}  # line -> the packet it is sending, absent between packets
    begun = {}    # (line, class, type) -> the packet's pairs, from its Start on
    nulls = {}

    out_func = None
    f = None

    while True:
        try:
            rows = rx.recv()
            if rows == "DONE":
                break
        except:
            break

        frame += 1

        # the second byte of each line as it was sliced, before parity was applied to it
        sliced = {row[0]: row[2] for row in rows}

        for row_num, _, _, b1, _, b2, b2_parity in decode_cea608_rows(rows, lines):
            if b1 == 0 and b2 == 0:  # Stuffing, ignore and continue
                if row_num in current:
                    nulls[row_num] = nulls.get(row_num, 0) + 1
                continue
            held = nulls.pop(row_num, 0)

            if 0x01 <= b1 <= 0x0e:
                # A Start (odd) begins a packet, or begins it again. A Continue (even) resumes
                # one, and is not kept, as it is not part of the checksum.
                key = (row_num, (b1 + 1) // 2, b2)
                if b1 & 1:
                    begun[key] = [(b1, b2)]
                if key in begun:
                    current[row_num] = key
                else:
                    current.pop(row_num, None)
            elif b1 == 0x0f:
                # An End closes the packet being sent. One with nothing open is a lost Start,
                # not a packet, so there is nothing to report.
                key = current.pop(row_num, None)
                if key is None:
                    continue

                # The checksum covers all seven data bits of its own byte, so it says for
                # itself whether they are right, and the byte's parity bit adds nothing. Nor can
                # the bit be trusted: KCET's 1994 encoder sets it whenever bit 6 is set, as well
                # as when parity needs it, so every checksum from 0x40 up with an odd number of
                # ones goes out with even parity - 10,740 of 23,126 packets over the whole tape,
                # its call sign every time. The waveform is clean; that is what was sent.
                if not b2_parity:
                    b2 = sliced[row_num] & 0x7f

                try:
                    if out_func == None:
                        out_func, f = get_output_function("xds", output_filename)

                    out_func(f"{frame}: {describe_xds_packet(begun.pop(key) + [(b1, b2)])}")
                except (KeyError, IndexError, RuntimeWarning) as e:
                    print("WARN: Unhandled error in XDS data, may be bad data or a bug", e, file=sys.stderr)
            elif 0x10 <= b1 <= 0x1f:
                # a caption or Text control code: the line has gone back to captions
                current.pop(row_num, None)
            elif row_num in current:
                # CEA-608-E 8.6.1: nulls between characters hold places, nulls before a control code are filler
                begun[current[row_num]] += [(0, 0)] * held + [(b1, b2)]

    if f is not None:
        f.close()
