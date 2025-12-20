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

from html import escape

import numpy as np
import matplotlib.pyplot as plt

from setproctitle import setproctitle
from multiprocessing import current_process

CLOCK_RUN_IN_COUNT = 7
START_BIT_COUNT = 3
DATA_BIT_COUNT = 16
PRE_COMPUTED_SINE_TEMPLATES = []

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
    0x26: '´',
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
    0x37: '|',
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
    (0x14, 0x28): 'Flash On',                   (0x14, 0x29): 'Resume Direct Captioning',
    (0x14, 0x2A): 'Text Restart',               (0x14, 0x2B): 'Resume Text Display',
    (0x14, 0x2C): 'Erase Displayed Memory',     (0x14, 0x2D): 'Carriage Return',
    (0x14, 0x2E): 'Erase Non-Displayed Memory', (0x14, 0x2F): 'End of Caption (flip memory)',
    (0x17, 0x21): 'Tab Offset 1',               (0x17, 0x22): 'Tab Offset 2',
    (0x17, 0x23): 'Tab Offset 3',
}

# This is an unofficial extension to the standard
# See http://www.theneitherworld.com/mcpoodle/SCC_TOOLS/DOCS/CC_CODES.HTML
# These
BACKGROUND_COLOR_CODES = {
    0x20: 'Background White',
    0x21: 'Background Semi-Transparent White',  # Doc says A1.. but seems to be 21
    0x22: 'Background Green',                   # ditto
    0x23: 'Background Semi-Transparent White',
    0x24: 'Background Blue',                    # ditto
    0x25: 'Background Semi-Transparent Blue',
    0x26: 'Background Cyan',
    0x27: 'Background Semi-Transparent Cyan',   # ditto
    0x28: 'Background Red',                     # ditto
    0x29: 'Background Semi-Transparent Red',
    0x2A: 'Background Yellow',                  # ditto
    0x2B: 'Background Semi-Transparent Yellow', # ditto
    0x2C: 'Background Magenta',
    0x2D: 'Background Semi-Transparent Magenta',# ditto
    0x2E: 'Background Black',                   # ditto
    0x2F: 'Background Semi-Transparent Black',
}

CC1_BACKGROUND_CHARS = { (0x10,x): y for (x,y) in BACKGROUND_COLOR_CODES.items() }  # Also CC3
CC2_BACKGROUND_CHARS = { (0x18,x): y for (x,y) in BACKGROUND_COLOR_CODES.items() }  # Also CC4

CC1_BACKGROUND_CHARS.update( { (0x17, 0x2D) : 'Background Transparent',
                               (0x17, 0x2E) : 'Foreground Black ',
                               (0x17, 0x2F) : 'Foreground Black Underline', } )  # Also CC3
CC2_BACKGROUND_CHARS.update( { (0x1F, 0xAD) : 'Background Transparent',
                               (0x1F, 0x2E) : 'Foreground Black ',
                               (0x1F, 0x2F) : 'Foreground Black Underline', } )  # Also CC4

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
CC2_CONTROL_CODES = {(a[0] == 0x14 and 0x1C or 0x1F, a[1]): 'CC2 ' + b for (a, b) in CONTROL_CODES.items()}
CC1_MID_ROW_CODES = {(0x11, a[1]): 'CC1 ' + b for (a, b) in MID_ROW_CODES.items()}
CC2_MID_ROW_CODES = {(0x19, a[1]): 'CC2 ' + b for (a, b) in MID_ROW_CODES.items()}

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
    0x75: 'Soap',         0x76: 'Special',       0x77: 'Suspense',    0x78: 'Talk',          0x79: 'Technical',
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
    'Copying is permitted without restriction', 'Condition not to be used',
    'One generation of copies may be made',     'No copying is permitted'
]

XDS_CGMS_APS = [  # Macrovision, etc
    'No Analogue protection',                              'Analogue protection: PSP On; Split Burst Off',
    'Analogue protection: PSP On; 2 line Split Burst On',  'Analogue protection: PSP On; 4 line Split Burst On',
]

XDS_DAY_OF_WEEK = {
    0xc1 : 'Sun',
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
    return '' + CC_TABLE.get(byte1, '?b1(%02x)' % (byte1) if default_unicode else "") + \
           CC_TABLE.get(byte2, '?b2(%02x)' % (byte2) if default_unicode else "")

def precompute_sine_templates(image_width):
    # granularity of period width
    min_clock_len = round(0.035 * image_width) # lower boundary for period width
    max_clock_len = round(0.041 * image_width) # upper boundary for period width
    num_steps = 5 # fractional amount to search for pixel width

    steps = (max_clock_len - min_clock_len) * num_steps
    search_widths = np.linspace(min_clock_len, max_clock_len, steps)

    templates = []
    # precompute the preamble clock run-in search parameters
    for i in range(len(search_widths)):
        pixels_per_cycle = search_widths[i]
        run_len = round(CLOCK_RUN_IN_COUNT * pixels_per_cycle)
        max_width = round((CLOCK_RUN_IN_COUNT + START_BIT_COUNT + DATA_BIT_COUNT) * pixels_per_cycle)

        if max_width >= image_width:
            # any match will be too long to fit in a line
            break
    
        t = np.arange(run_len)
        template = np.sin(2 * np.pi * t / pixels_per_cycle)
        template -= template.mean()
        template_rev = template[::-1]
        var_t = np.sum(template ** 2)
        
        templates.append((
            pixels_per_cycle,
            max_width,
            run_len,
            template,
            template_rev,
            var_t
        ))

    return np.asarray(templates, dtype=tuple)

def sync_to_preamble(img, row):
    # Read and normalize line
    line = img[row]

    line_min, line_max = line.min(), line.max()
    if line_min == line_max:
        return None

    norm = (line - line_min) / (line_max - line_min)
    norm_len = len(norm)

    # ---- CLOCK RUN-IN MATCH ----
    # Precompute cumulative sums for fast variance computation
    cumsum = np.cumsum(norm)
    cumsum2 = np.cumsum(norm ** 2)

    best_score = -np.inf
    preamble_start = None
    bit_width = None
    best_sine_template = None

    for (pixels_per_cycle, max_width, run_len, sine_template, sine_template_rev, var_t) in PRE_COMPUTED_SINE_TEMPLATES:
        # normalized correlation
        conv = np.convolve(norm, sine_template_rev, mode='valid')
        sum_x = cumsum[run_len-1:] - np.concatenate(([0], cumsum[:-run_len]))
        sum_x2 = cumsum2[run_len-1:] - np.concatenate(([0], cumsum2[:-run_len]))
        var_x = sum_x2 - sum_x ** 2 / run_len
        score = (conv ** 2) / (var_t * var_x + 1e-12)

        idx = np.argmax(score)
        if idx + max_width >= norm_len:
            # best match would be too long to fit in a line
            continue

        if score[idx] > best_score:
            best_score = score[idx]
            preamble_start = idx
            bit_width = pixels_per_cycle
            best_sine_template = sine_template

    if best_sine_template is None:
        return None

    # ---- PHASE CORRECTION ----
    run_len = round(CLOCK_RUN_IN_COUNT * bit_width)
    seg = norm[round(preamble_start):round(preamble_start) + run_len]
    if np.dot(seg - seg.mean(), best_sine_template) < 0:
        # flip phase
        preamble_start += bit_width / 2

    preamble_end = preamble_start + (CLOCK_RUN_IN_COUNT - 0.5) * bit_width

    return {
        "normalized_line": norm,
        "preamble_start": preamble_start,
        "preamble_end": preamble_end,
        "bit_width": bit_width,
        "score": best_score,
    }

def get_bit(bit_index, bit_width, bit_padding, normalized_line, normalized_median, preamble_end):
    start = preamble_end + bit_index * bit_width
    s = round(start) + bit_padding
    e = round(start + bit_width) - bit_padding

    seg = normalized_line[s:e]
    mean = seg.mean()
    std = math.sqrt((abs(seg - mean) ** 2).mean())

    return 1 if mean > normalized_median else 0, std

def decode_bytes(normalized_line, preamble_start, preamble_end, bit_width, best_score):
    # fraction of data to remove at edges of each detected bit
    bit_width_padding = 0.1
    min_std_dev_for_correction = 0.3

    # ---- BIT DECODING ----
    normalized_median = np.mean(normalized_line[round(preamble_start):round(preamble_end)])
    bit_padding = math.ceil(bit_width_padding * bit_width)

    # assert start bit
    if (
        get_bit(0, bit_width, bit_padding, normalized_line, normalized_median, preamble_end)[0] != 0
        or get_bit(1, bit_width, bit_padding, normalized_line, normalized_median, preamble_end)[0] != 0
        or get_bit(2, bit_width, bit_padding, normalized_line, normalized_median, preamble_end)[0] != 1
    ):
        return None, None, None, None

    # build each byte
    byte_data = np.ndarray(2, dtype=int)
    byte_parity = np.ndarray(2, dtype=bool)
    b_bits = np.ndarray(7, dtype=int)
    b_stds = np.ndarray(7, dtype=float)

    for i in range(2):
        b_data_start = START_BIT_COUNT + i * 8
        b_parity_idx = b_data_start + 7
        b_worst_error_idx = 0
        b_worst_error = 0
        b_error_count = 0
        b_parity_calculated = 1

        # get data bits
        for b_idx in range(0, 7):
            bit, std = get_bit(b_idx + b_data_start, bit_width, bit_padding, normalized_line, normalized_median, preamble_end)
            b_bits[b_idx] = bit
            b_stds[b_idx] = std
            # gather parity
            b_parity_calculated += bit

            # check for possible errors
            if std > min_std_dev_for_correction:
               b_error_count += 1
               if b_worst_error < std:
                  b_worst_error_idx = b_idx
                  b_worst_error = std

        # get parity bit
        b_parity_calculated %= 2
        b_parity_bit, b_parity_bit_std = get_bit(b_parity_idx, bit_width, bit_padding, normalized_line, normalized_median, preamble_end)

        # correct single bit errors using parity
        if (
            b_error_count == 1 # only one data bit error
            and b_parity_bit != b_parity_calculated # parity miss-match
            and b_parity_bit_std < min_std_dev_for_correction # parity bit is probably good
        ):
            b_bits[b_worst_error_idx] = 1 if b_bits[b_worst_error_idx] == 0 else 0
            b_parity_calculated = b_parity_bit

        # write out the bytes
        byte_data[i] = (
            b_bits[0]
            | (b_bits[1] << 1)
            | (b_bits[2] << 2)
            | (b_bits[3] << 3)
            | (b_bits[4] << 4)
            | (b_bits[5] << 5)
            | (b_bits[6] << 6)
        )
        byte_parity[i] = b_parity_bit == b_parity_calculated

    # uncomment to debug
    #if b_error_count >= 1 or not (byte_parity[0] or byte_parity[1]):
    #    bits = [get_bit(i, bit_width, bit_padding, normalized_line, normalized_median, preamble_end)[0] for i in range(START_BIT_COUNT + DATA_BIT_COUNT)]
    #    print("bit width", bit_width, "score", best_score, "errors", b_error_count)
    #    debug_plot(normalized_line, round(preamble_start), round(preamble_end), round(bit_width), bits, bit_width_padding)

    return byte_data[0], byte_parity[0], byte_data[1], byte_parity[1]

def debug_plot(line, preamble_start, preamble_end, width, bits, bit_width_padding):
    line = np.asarray(line, dtype=float)
    n = len(line)
    
    # Compute mid, amplitude for sine wave
    mid = np.mean(line[preamble_start:preamble_end])
    high = np.max(line[preamble_start:preamble_end])
    low = np.min(line[preamble_start:preamble_end])
    amplitude = (high - low) / 2
    
    # Full sine wave for the line
    t = np.arange(n)
    phase_offset = 2 * np.pi * preamble_start / width
    sine_full = mid + amplitude * np.sin(2 * np.pi * t / width - phase_offset)
    
    # Plot the waveform
    plt.figure(figsize=(12, 4))
    plt.plot(line, label='Line waveform', color="black")
    plt.plot(sine_full, label='Sine wave', color='red', alpha=0.7)
    
    # Clock run-in start and end
    plt.axvline(preamble_start, color='green', linestyle='--', label='Clock start')
    plt.axvline(preamble_end, color='purple', linestyle='--', label='Clock end')
    
    # Overlay bit markers
    bit_count = len(bits)
    bit_padding = bit_width_padding * width
    starts = preamble_end + np.arange(bit_count) * width + bit_padding
    ends = starts + width - 2 * bit_padding
    
    for i, (s, e, b) in enumerate(zip(starts, ends, bits)):
        color = 'blue' if b == 1 else 'orange'
        plt.axvspan(s, e, color=color, alpha=0.3)
        plt.text((s+e)/2, mid + amplitude*1.1, str(b), color='black', ha='center', va='bottom')
    
    plt.xlabel('Pixel position')
    plt.ylabel('Amplitude')
    plt.title('Line waveform with sine wave and decoded bits')
    plt.legend()
    plt.show()

def find_and_decode_rows(img, start_line, search_lines):
    rows_found = []
    field_0_idx = None

    for row_idx in range(0, search_lines):
        if field_0_idx and field_0_idx + 1 < row_idx:
            # break if the second field was skipped
            break
        start_idx = row_idx + start_line
        preamble_match = sync_to_preamble(img, start_idx)

        if preamble_match is not None and preamble_match["score"] > 0.7:
            b1, b1_parity, b2, b2_parity = decode_bytes(
                preamble_match["normalized_line"],
                preamble_match["preamble_start"],
                preamble_match["preamble_end"],
                preamble_match["bit_width"],
                preamble_match["score"],
            )

            rows_found.append((start_idx, b1, b1_parity, b2, b2_parity))
            if field_0_idx == None:
                field_0_idx = row_idx

    return rows_found

def extract_closed_caption_bytes(img, start_line, search_lines):
    """ Returns a tuple of byte values from the passed image object that supports get_pixel_luma """
    # text decoded code, is control, byte 1, byte 1 parity valid, byte 2, byte 2 parity valid
    decoded_rows = []
    for row_num, b1, b1_parity, b2, b2_parity in find_and_decode_rows(img, start_line, search_lines):
        control = (b1, b2) in ALL_CC_CONTROL_CODES
    
        # handle parity errors
        # https://www.law.cornell.edu/cfr/text/47/79.101
        if not b2_parity:
            if control:
                continue
            else:
                b2 = 0x7f

        if not b1_parity:
            control = False # treat this as a print character when parity fails
            b1 = 0x7f

        code = decode_byte_pair(control, b1, b2)
        decoded_rows.append((row_num, code, control, b1, b1_parity, b2, b2_parity))

    return decoded_rows
    
def get_output_function(extension, output_filename):
    if output_filename is not None:
        f = open(output_filename + f".{extension}", 'w')
        out_func = lambda out : print(out, file=f)
    else:
        f = None
        out_func = print

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
    buff = ''  # CC Buffer
    frame = 0

    out_func, f = get_output_function("captions.raw", output_filename)

    while True:
        try:
            rows = rx.recv()
            if rows == "DONE":
                break
        except:
            break

        for row in rows:
            row_num, code, control, b1, _, b2, _ = row

            if code is None:
                out_func('%i %i skip - no preamble' % (frame, row_num))
            else:
                if code and not control:
                    buff += code
                elif buff:
                    out_func('%i %i - [%02x, %02x] - Text:%s' % (frame, row_num, b1, b2, buff))
                    buff = ''
                if control:
                    out_func('%i %i - [%02x, %02x] - %s' % (frame, row_num, b1, b2, code))
        frame += 1

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

        for row in rows:
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

class CaptionTrack:
    def __init__(self, cc_track, output_filename, options, extension):
        self._cc_track = cc_track
        self._field_number = CC_CHANNEL_TO_FIELD[self._cc_track]
        self._output_filename = output_filename
        self._options = options
        self._extension = extension

        self.prev_code = None
        self.mode = "pop_on"

        self._buffer_on_screen = []
        self._buffer_off_screen = []
        self._roll_up_buffer = []
        self._text_buffer = np.ndarray(32, dtype=object)
        self._text_cursor = 0

        self.f = None
        self.f_text = None

    def open(self):
        self.out, self.f = get_output_function(self._cc_track + "." + self._extension, self._output_filename)

    def open_text(self):
        self.out_text, self.f_text = get_output_function(CC_CHANNEL_TO_TEXT_CHANNEL[self._cc_track] + "." + self._extension, self._output_filename)

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
        
    def global_resume_loading(self, data, frames):
        self.mode = "pop_on"
        
    def global_resume_direct(self, data, frames):
        self.mode = "paint_on"

    def global_start_roll_up(self, data, frames):
        _, code, _, _, _, byte2, _ = data

        self.mode = "roll_up"
        self.roll_up_length = ROLL_UP_LEN[byte2]
        self.global_erase_displayed_memory(data, frames)
        self.global_erase_non_displayed_memory(data, frames)

    def global_start_text_mode(self, data, frames):
        self.mode = "text"
        if self.f_text is None:
            self.open_text()

    def global_text_reset(self, data, frames):
        raise NotImplemented
        
    def global_flip_buffers(self, data, frames):
        buffer_off_screen = self._buffer_off_screen
        self._buffer_off_screen = self._buffer_on_screen
        self._buffer_on_screen = buffer_off_screen
    
    def global_erase_non_displayed_memory(self, data, frames):
        self._buffer_off_screen = []

    def global_erase_displayed_memory(self, data, frames):
        if self.mode == "roll_up":
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
        self._text_buffer[self._text_cursor] = data
        self._text_cursor = min(self._text_cursor + 1, len(self._text_buffer) - 1)

    def clear_text(self):
        self._text_buffer[:] = None
        self._text_cursor = 0
    
    def write_text(self, data, frames):
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
        self.write_text([data], frames)

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
        _, code, _, _, _, _, _ = data

        if 'Carriage Return' in code:
            self._write(self.out_text, self._text_buffer[:self._text_cursor], frames)
            self.clear_text()

    def write_text(self, data, frames):        
        super().write_text(data, frames)
        self._write(self.out_text, data, frames)

    def write_caption(self, data, frames):
        super().write_caption(data, frames)
        self._write(self.out, data, frames)

    def _write(self, out_func, data, frames):
        scc_data = [self._get_subtitle_data(n) for n in data]
        out_func('%s\t%s' % (self._get_timecode(frames), "".join(scc_data)))

    def _get_timecode(self, frames):
        frame_number = frames + 18 * (frames / 17982) + 2 * max(((frames % 17982) - 2) / 1798, 0)
        frs = frame_number % 30
        s = (frame_number / 30) % 60
        m = ((frame_number / 30) / 60) % 60
        h = (((frame_number / 30) / 60) / 60) % 24
        return '%02d:%02d:%02d;%02d' % (h, m, s, frs)
    
    def _get_subtitle_data(self, data):
        _, _, _, byte1, _, byte2, _ = data
        return '%x%x ' % (NO_PARITY_TO_ODD_PARITY[byte1], NO_PARITY_TO_ODD_PARITY[byte2])
    
class TextCaptionTrack(CaptionTrack):
    def __init__(self, cc_track, output_filename, options, extension = "txt"):
        super().__init__(cc_track, output_filename, options, extension)

        self.prev_char = None
        self.previous_frames = 0

    def global_text_reset(self, data, frames):
        self.clear_text()
    
    def dedupe_bad_data_from_text(self, code):
        if self.prev_char == None:
            self.prev_char = code
            return code
        
        if self.prev_char == '■' and code == '■':
            return ''
        
        self.prev_char = code
        return code
    
    def handle_row(self, code, caption_text, current_row, line_break = "\n"):
        match = re.search(r'row (?P<row_number>\d+)$', code)
        if match:
            row = int(match.group("row_number"))
            if current_row is not None and current_row < row:
                caption_text += line_break
            current_row = row

        return caption_text, current_row
    
    def handle_cr(self, code, caption_text, line_break = "\n"):
        if code.endswith("Carriage Return"):
            caption_text += line_break

        return caption_text
    
    def handle_bs(self, code, caption_text):
        if code.endswith("Backspace"):
            caption_text = caption_text[0:-1]

        return caption_text

    def handle_tab(self, code, caption_text, space_character = " "):
        if "Tab" in code:
            tab_match = re.search(r'Tab Offset (?P<tab_offset>\d+)', code)
            if tab_match:
                tab = int(tab_match["tab_offset"])
                tab = max(32 - len(caption_text), tab) # Tab Offsets shall not move the cursor beyond the 32nd column of the current row.
                caption_text += space_character * tab

        return caption_text

    def handle_indent(self, code, caption_text, space_character = " "):
        if "Indent" in code:
            intent_match = re.search(r'Indent (?P<indent_offset>\d+)', code)
            if intent_match:
                indent = int(intent_match["indent_offset"])
                caption_text += space_character * indent
        
        return caption_text
    
    def handle_character(self, code, caption_text, byte1, byte2):
        code = decode_byte_pair(False, byte1, byte2, False)
        has_writable = False
        if code is not None:
            for char in str(code):
               if char != " ":
                   has_writable = True
               caption_text += self.dedupe_bad_data_from_text(char)

        return caption_text, has_writable
    
    def handle_style(self, code, caption_text):
        return caption_text

    def get_caption_text(self, data):
        current_row = None
        caption_text = ""
        has_writable = False
        
        for decoded_row in data:
            if decoded_row is None:
                # write a space?
                continue

            (_, code, control, byte1, byte1_parity, byte2, byte2_parity) = decoded_row

            # add a line break if row advances
            if control:
                # ignore control codes with bad parity
                if byte1_parity and byte2_parity:
                    caption_text, current_row = self.handle_row(code, caption_text, current_row)
                    caption_text = self.handle_cr(code, caption_text)
                    caption_text = self.handle_bs(code, caption_text)
                    caption_text = self.handle_tab(code, caption_text)
                    caption_text = self.handle_indent(code, caption_text)
                    caption_text = self.handle_style(code, caption_text)
            else:
                # get only a printable code (no byte values)
                caption_text, has_writable = self.handle_character(code, caption_text, byte1, byte2)

        return caption_text, has_writable
    
    # only enable text for .txt format
    def add_text(self, data, frames):
        _, code, _, _, _, _, _ = data

        # compatibility with TeleCaption I decoder
        # when there's a data interruption, the decoder resets the cursor to first column
        # for forwards compatibility, an indent is sent without a carriage return to avoid repeated characters
        # see ANSI-CEA-608-E, Annex D.3 Text-Mode Multiplexing (Informative), pg. 78
        if (
            "Indent" in code
        ):
            intent_match = re.search(r'.*Indent (?P<indent_offset>\d+)', code)
            if intent_match:
                indent = int(intent_match["indent_offset"])
                self._text_cursor = indent

        if 'Carriage Return' in code and code == self.prev_code:
            self.write_text(self._text_buffer[:self._text_cursor], frames)
            self.clear_text()
        else:
            super().add_text(data, frames)

        self.previous_frames = frames

    def write_text(self, data, frames):
        caption_text, has_writable = self.get_caption_text(data)
        if has_writable:
            super().write_text(data, frames)
            # remove the last linebreak since one will be added by print
            self.out_text(caption_text.rstrip('\n'))

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
        self.text_end_frame = 0

        self.fps = 29.97

    def global_resume_direct(self, data, frames):
        # start a new subtitle entry
        super().global_resume_direct(data, frames)
        self.subtitle_start_frame = frames
        self._roll_up_buffer = []

    def global_start_roll_up(self, data, frames):
        super().global_start_roll_up(data, frames)
        self.subtitle_start_frame = frames

    def global_start_text_mode(self, data, frames):
        super().global_start_text_mode(data, frames)
        if self.text_start_frame == 0:
            self.text_start_frame = frames

    def global_text_reset(self, data, frames):
        self.clear_text()

    def global_flip_buffers(self, data, frames):
        super().global_flip_buffers(data, frames)
        self.subtitle_start_frame = frames

    def global_erase_displayed_memory(self, data, frames):
        # end the subtitle and write to the screen
        if self.mode == "roll_up":
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
        self._roll_up_buffer.append(data)

        #line_breaks = []
        #for i in range(len(self._roll_up_buffer)):
        #    if self._roll_up_buffer[i][0].endswith("Carriage Return"):
        #        line_breaks.append(i)
        #
        # roll-up the captions
        #print(len(line_breaks), self.roll_up_length)
        #if len(line_breaks) > self.roll_up_length:
        #    # keep the last n rows
        #    lines_to_remove = line_breaks[-(self.roll_up_length)]
        #    # roll-up and away the oldest row
        #    self._roll_up_buffer = self._roll_up_buffer[lines_to_remove+1:]
        #    # trigger a subtitle update
        #    self._write(self._roll_up_buffer, frames)
        #    self.subtitle_start_frame = frames

    def add_text(self, data, frames):
        _, code, _, _, _, _, _ = data

        if 'Carriage Return' in code and code == self.prev_code:
            self.write_text(self._text_buffer[:self._text_cursor], frames)
            self.clear_text()
        else:
            super().add_text(data, frames)
    
    def write_text(self, data, frames):
        self.text_end_frame = frames
        caption_text, has_writable = self.get_caption_text(data)
        if has_writable:
            super().write_text(data, frames)
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
        super().write_caption(data, frames)

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
        out_func('%s --> %s\n%s\n' % (self._get_timecode(start_frame), self._get_timecode(end_frame), caption_text))
        return True

    def _get_timecode(self, frames):
        """ Returns an SRT format timestamp """
        seconds = frames / self.fps
        milliseconds = int((seconds - int(seconds)) * 1000)
        hours = int(seconds / 3600)
        minutes = int((seconds - 3600 * hours) / 60)
        seconds_disp = seconds - (minutes * 60 + hours * 3600)
        return '%02d:%02d:%02d,%03d' % (hours, minutes, seconds_disp, milliseconds)

class HTMLCaptionTrack(TextCaptionTrack):
    def __init__(self, cc_track, output_filename, options, extension = "html"):
        super().__init__(cc_track, output_filename, options, extension)
        self._style_tag = (
"""
<style>
:root {
  /* color variables */
  --white: white;
  --green: green;
  --blue: blue;
  --cyan: cyan;
  --red: red;
  --yellow: yellow;
  --magenta: magenta;
  --black: black;
}

body { font-family: monospace, monospace; background-color: black; }

.text-white { color: var(--white); }
.text-green { color: var(--green); }
.text-blue { color: var(--blue); }
.text-cyan { color: var(--cyan); }
.text-red { color: var(--red); }
.text-yellow { color: var(--yellow); }
.text-magenta { color: var(--magenta); }
.text-black { color: var(--black); }

.background-transparent { background-color: none; }
.background-white { background-color: var(--white); }
.background-green { background-color: var(--green); }
.background-blue { background-color: var(--blue); }
.background-cyan { background-color: var(--cyan); }
.background-red { background-color: var(--red); }
.background-yellow { background-color: var(--yellow); }
.background-magenta { background-color: var(--magenta); }
.background-black { background-color: var(--black); }

.background-white-semi-transparent { background-color: rgb(var(--white) / 0.5); }
.background-green-semi-transparent { background-color: rgb(var(--green) / 0.5); }
.background-blue-semi-transparent { background-color: rgb(var(--blue) / 0.5); }
.background-cyan-semi-transparent { background-color: rgb(var(--cyan) / 0.5); }
.background-red-semi-transparent { background-color: rgb(var(--red) / 0.5); }
.background-yellow-semi-transparent { background-color: rgb(var(--yellow) / 0.5); }
.background-magenta-semi-transparent { background-color: rgb(var(--magenta) / 0.5); }
.background-black-semi-transparent { background-color: rgb(var(--black) / 0.5); }

.underline { text-decoration: underline; }
.italics { font-style: italic; }
</style>
""")
        self._background_color = "background-black"
        self._text_color = "text-white"
        self._text_style = ""
    
    def open_text(self):
        super().open_text()
        self.out_text(f"<html><head>{self._style_tag}</head><body><span class='{self._background_color} {self._text_color} {self._text_style}'>")

    def close(self):
        self.out_text("</span></body></html>")
        super().close()

    def global_text_reset(self, data, frames):
        self.clear_text()

    def handle_row(self, code, caption_text, current_row):
        return super().handle_row(code, caption_text, current_row, "<br>")
    
    def handle_cr(self, code, caption_text):
        return super().handle_cr(code, caption_text, "<br>")
    
    def handle_tab(self, code, caption_text):
        return super().handle_tab(code, caption_text, "&nbsp;")
    
    def handle_indent(self, code, caption_text):
        return super().handle_indent(code, caption_text, "&nbsp;")
    
    def handle_style(self, code, caption_text):
        styles_updated = False
        background_color = self._background_color
        text_color = self._text_color
        text_style = self._text_style

        # check for updated styles
        style_match = re.findall(r"\b(Underline|Italics)\b", code)
        if style_match:
            text_style = " ".join([s.lower() for s in style_match])

        color_match = re.search(r"\b(White|Green|Blue|Cyan|Red|Yellow|Magenta|Black)\b", code)
        if color_match:
            color = color_match[0].lower()
            if "Background" in code:
                if "Semi-Transparent" in code:
                    background_color = "background-semi-transparent-" + color
                else:
                    background_color = "background-" + color
            else:
                if not style_match:
                    # color update without style assumes the style is cleared
                    text_style = ""

                text_color = "text-" + color
        elif "Background Transparent" in code:
            background_color = "background-transparent"           

        # update span tag if any styles changed
        if background_color != self._background_color:
            styles_updated = True
            self._background_color = background_color

        if text_color != self._text_color:
            styles_updated = True
            self._text_color = text_color

        if text_style != self._text_style:
            styles_updated = True
            self._text_style = text_style

        if styles_updated:
            return f"{caption_text}</span><span class='{self._background_color} {self._text_color} {self._text_style}'>"

        return caption_text
    
    def dedupe_bad_data_from_text(self, code):
        return escape(super().dedupe_bad_data_from_text(code))

    def write_text(self, data, frames):
        caption_text, has_writable = self.get_caption_text(data)
        if has_writable:
            CaptionTrack.write_text(self, data, frames)
            # remove the last linebreak since one will be added by print
            caption_text = re.sub(r" {2,}", lambda m: "&nbsp;" * len(m.group()), caption_text)

            self.out_text(caption_text)

    def add_on_screen(self, data, frames):
        pass

    def add_off_screen(self, data):
        pass
    
    def add_on_screen_roll_up(self, data, frames):
        pass


class CaptionTrackFactory():
    def __init__(self, track_class, output_filename, options):
        self._field_to_active_track = [None, None] # stores the active track for each field
        self._tracks = {} # stores the created tracks
        self._row_to_field = {} # maps the row to the detected field
        self._output_filename = output_filename
        self._track_class = track_class
        self._options = options

    def add_data(self, rows, frame):
        for row in rows:
            detected_field = None
            row_num, code, _, b1, b1_parity, _, b2_parity = row

            # determine field for row
            if b1_parity and b2_parity:
                cc_track = code[:3]
                if cc_track in CC_CHANNEL_TO_FIELD:
                    # cc channels have a defined field order
                    detected_field = CC_CHANNEL_TO_FIELD[cc_track]
                    self._row_to_field[row_num] = detected_field

                    # create new track from CC channel, if not existing
                    if cc_track not in self._tracks:
                        self._tracks[cc_track] = self._track_class(cc_track, self._output_filename, self._options)

                    self._field_to_active_track[detected_field] = self._tracks[cc_track]

                # elif b1 < 0x0f and b1 > 0x00:
                #     # xds data is always field 1
                #     detected_field = 1
                #     self._row_to_field[row_num] = detected_field

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
    day_of_month = ( pbytes[2] - 0x40 )  # TODO: There is some possible interaction with leapday here, ignore for now

    month_key = pbytes[3] & 0xF
    month = XDS_MONTH[month_key] if month_key in XDS_MONTH else "--"
    
    day_of_week_key = pbytes[4]
    day_of_week = XDS_DAY_OF_WEEK[day_of_week_key] if day_of_week_key in XDS_DAY_OF_WEEK else "--"

    year = 1990 + ( pbytes[5] - 0x40 )
    minutes = pbytes[0] - 0x40
    hours = pbytes[1] & 0x1F
    return f'TM {hours:0>2}:{minutes:0>2}{dst} {zero_seconds}{tape_delayed}{leap_day} {month} {day_of_month:0>2} {year} {day_of_week}'

def decode_xds_local_time_zone(pbytes):
    # TODO: convert to +-12
    """ Decode the Local Time Zone packets """
    _assert_len(pbytes, 2)
    data, _ = pbytes.pop(0)

    tz = -(data & 0b11111)
    if tz > 11:
        tz = 24 - tz
    dst = 'DST' if (data & 0b100000) else 'ST'

    return f'{tz} {dst}'

def decode_xds_content_advisory(pbytes):
    """ Decode content advisory packet, returning a string describing the rating """
    _assert_len(pbytes, 2)
    ca1, ca2 = pbytes.pop(0)
    system = ca1 & 24 >> 3
    rating = ''
    if system == 0 or system == 2:  # MPA
        rating = MPA_RATING[ca1 & 7]
    elif system == 1:  # US TV Parent Guidelines
        rating_code = ca1 & 7
        rating = US_TV_PARENTAL_GUIDELINE_RATING[rating_code]
        if rating_code == 2:
            rating += ' Fantasy Violence' if ca2 & 32 else ''
        elif 4 <= rating_code <= 6:
            rating += ' Violence' if ca2 & 32 else ''
            rating += ' Sexual Situations' if ca2 & 16 else ''
            rating += ' Adult Language' if ca2 & 8 else ''
            rating += ' Sexually Suggestive Dialogue' if ca1 & 32 else ''
    elif system == 3:  # International
        subsystem = (ca1 & 32 >> 5) + (ca2 & 8 >> 2)
        if subsystem == 1:  # CAD English
            rating = CANADIAN_ENGLISH_RATINGS[ca2 & 7]
        elif subsystem == 2:
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
        if b1 <= 0x02 and b2 <= 0x03:  # TODO continues
            pref = ['Current', 'Next Program'][b1-1]
            if b2 == 0x01:  # Program identification number
                _assert_len(packet_bytes, 4)
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
                main_language = XDS_AUDIO_SERVICES_LANGUAGE[main & 56 >> 3]
                main_type = XDS_AUDIO_SERVICES_TYPE_MAIN[main & 7]
                sap_language = XDS_AUDIO_SERVICES_LANGUAGE[sap & 56 >> 3]
                sap_type = XDS_AUDIO_SERVICES_TYPE_SECONDARY[sap & 7]
                return 'XDS Audio Services: Main:%s(%s) Sap:%s(%s)' % (main_language, main_type, sap_language, sap_type)
            elif b2 == 0x07:  # Caption services
                return 'XDS Caption Services'  # TODO
            elif b2 == 0x08:  # Copy and Redistribution Control Packe
                _assert_len(packet_bytes, 2)
                c1, _ = packet_bytes.pop(0)
                copying = XDS_CGMS[c1 & 24 >> 3]
                protection = XDS_CGMS_APS[c1 & 7]
                return 'XDS Copy protection: %s %s' % (copying, protection)
            elif b2 == 0x09:  # Aspect ratio
                _assert_len(packet_bytes, 2)
                startl, endl = packet_bytes.pop(0)
                anamorp = False
                if packet_bytes:
                    anamorp, _ = packet_bytes.pop(0)
                return 'XDS Aspect Ratio: start line: %i end line: %i %s' \
                       % (22 + (startl & 63), 262 - (endl & 63), (anamorp & 1) and 'Anamorphic')
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
                return 'XDS Channel Station Call-Sign: %s' % decode_xds_string(packet_bytes)
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
    packetbuf = []
    xds_row = -1
    gather_xds_bytes = False

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

        # check for xds row, and replace row if found in another row
        for row in rows:
            row_num, code, _, b1, b1_parity, b2, b2_parity = row

            if b1 > 0 and b1 <= 0xf and b1_parity and b2_parity:
                xds_row = row_num

        # if xds is found, read it
        if xds_row != -1:
            for row in rows:
                row_num, code, _, b1, b1_parity, b2, b2_parity = row

                if xds_row == row_num:
                    if code is not None:
                        if not (b1 == 0 and b2 == 0):  # Stuffing, ignore and continue
                            if b1 <= 0x0e:  # Start of XDS packet'
                                gather_xds_bytes = True
                            if gather_xds_bytes:
                                packetbuf.append((b1, b2))
                            if b1 == 0x0f:  # End of XDS packet
                                gather_xds_bytes = False
                                try:
                                    if out_func == None:
                                        out_func, f = get_output_function("xds", output_filename)
    
                                    out_func(f"{frame}: {describe_xds_packet(packetbuf)}")
                                except KeyError as e:
                                    print("WARN: Unhandled key error in XDS data, may be bad data or a bug", e, file=sys.stderr)
                                    pass
                                packetbuf = []
                    break

    if f is not None:
        f.close()
