"""Every CEA-608 code the decoder knows, checked against CEA-608-E.

The expected codes and glyphs are transcribed here from the standard's own tables - Tables 3 to
10, 45 and 49 to 53, section 8.4 and Annex G - rather than taken from lib/cc_decode.py, and
compared with the decoder over the whole seven bit code space. Names are in the decoder's
wording. Where the decoder deliberately draws a glyph other than the one the standard prints
(the box drawing set, the round bullet, the transparent space) its choice is what is asserted.

    python3 -m pytest tests/test_cea608_codes.py
"""

import os
import sys
from itertools import product

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

import lib.cc_decode as cc_decode
from lib.cc_decode import (
    ALL_CC_CONTROL_CODES, ALL_SPECIAL_CHARS, BACKGROUND_COLOR_CODES, CC1_BACKGROUND_CHARS,
    CC1_CONTROL_CODES, CC1_MID_ROW_CODES, CC1_PREAMBLE_COLS, CC2_BACKGROUND_CHARS,
    CC2_CONTROL_CODES, CC2_MID_ROW_CODES, CC2_PREAMBLE_COLS, CC3_CONTROL_CODES,
    CC4_CONTROL_CODES, CC_TABLE, CONTROL_CODES, EVEN_PREAMBLE, EXTENDED_PORTUGUESE_GERMAN_DANISH,
    EXTENDED_SPANISH_FRENCH, MID_ROW_CODES, NO_PARITY_TO_ODD_PARITY, PREAMBLE_ODD, ROLL_UP_LEN,
    SPECIAL_CHARS_TABLE, cea608_byte, cea608_parity_ok, decode_byte_pair, is_control)


def named(entries, **first_bytes):
    """ {(first byte, second byte): 'CCn name'} for every entry on every channel given """
    return {(first, entry[0]): '%s %s' % (channel, entry[-1])
            for channel, first in first_bytes.items() for entry in entries}


# CEA-608-E Table 52
MISCELLANEOUS = [
    (0x20, 'RCL', 'Resume Caption Loading'),
    (0x21, 'BS', 'Backspace'),
    (0x22, 'AOF', 'Reserved (Alarm Off)'),
    (0x23, 'AON', 'Reserved (Alarm On)'),
    (0x24, 'DER', 'Delete to End Of Row'),
    (0x25, 'RU2', 'Roll-Up Captions-2 Rows'),
    (0x26, 'RU3', 'Roll-Up Captions-3 Rows'),
    (0x27, 'RU4', 'Roll-Up Captions-4 Rows'),
    (0x28, 'FON', 'Mid-row: Flash On'),
    (0x29, 'RDC', 'Resume Direct Captioning'),
    (0x2A, 'TR', 'Text Restart'),
    (0x2B, 'RTD', 'Resume Text Display'),
    (0x2C, 'EDM', 'Erase Displayed Memory'),
    (0x2D, 'CR', 'Carriage Return'),
    (0x2E, 'ENM', 'Erase Non-Displayed Memory'),
    (0x2F, 'EOC', 'End of Caption (flip memory)'),
]
TAB_OFFSETS = [
    (0x21, 'TO1', 'Tab Offset 1'),
    (0x22, 'TO2', 'Tab Offset 2'),
    (0x23, 'TO3', 'Tab Offset 3'),
]

# CEA-608-E Table 51
MID_ROW = [(0x20 + at, 'Mid-row: ' + attribute) for at, attribute in enumerate([
    'White', 'White Underline', 'Green', 'Green Underline', 'Blue', 'Blue Underline',
    'Cyan', 'Cyan Underline', 'Red', 'Red Underline', 'Yellow', 'Yellow Underline',
    'Magenta', 'Magenta Underline', 'Italics', 'Italics Underline',
])]

# CEA-608-E Table 3
BACKGROUND = [
    (0x20, 'BWO', 'Background White'),
    (0x21, 'BWS', 'Background Semi-Transparent White'),
    (0x22, 'BGO', 'Background Green'),
    (0x23, 'BGS', 'Background Semi-Transparent Green'),
    (0x24, 'BBO', 'Background Blue'),
    (0x25, 'BBS', 'Background Semi-Transparent Blue'),
    (0x26, 'BCO', 'Background Cyan'),
    (0x27, 'BCS', 'Background Semi-Transparent Cyan'),
    (0x28, 'BRO', 'Background Red'),
    (0x29, 'BRS', 'Background Semi-Transparent Red'),
    (0x2A, 'BYO', 'Background Yellow'),
    (0x2B, 'BYS', 'Background Semi-Transparent Yellow'),
    (0x2C, 'BMO', 'Background Magenta'),
    (0x2D, 'BMS', 'Background Semi-Transparent Magenta'),
    (0x2E, 'BAO', 'Background Black'),
    (0x2F, 'BAS', 'Background Semi-Transparent Black'),
]
TRANSPARENT_AND_FOREGROUND = [
    (0x2D, 'BT', 'Background Transparent'),
    (0x2E, 'FA', 'Foreground Black'),
    (0x2F, 'FAU', 'Foreground Black Underline'),
]

# CEA-608-E Table 4
SPECIAL_ASSIGNMENTS = [
    (0x24, 'Standard Character Set'),
    (0x25, 'Standard Character Set Double Size'),
    (0x26, 'First Private Character Set'),
    (0x27, 'Second Private Character Set'),
    (0x28, 'GB 2312-80 Character Set'),
    (0x29, 'KSC 5601-1987 Character Set'),
    (0x2A, 'First Registered Character Set'),
]

# CEA-608-E G.2.2
ARTICLES = [
    (0x20, 'ANS', 'Article Name Start'),
    (0x30, 'ANE', 'Article Name End'),
    (0x31, 'AC', 'Article Clear'),
    (0x32, 'AE', 'Article End'),
]
# CEA-608-E G.2.3
ARTICLE_PAGES = [(0x21 + page - 2, 'Article Page %d' % page) for page in range(2, 17)]

# CEA-608-E Table 53
PAC_ROWS = {
    1: (0x11, 0x19, 0x40), 2: (0x11, 0x19, 0x60), 3: (0x12, 0x1A, 0x40), 4: (0x12, 0x1A, 0x60),
    5: (0x15, 0x1D, 0x40), 6: (0x15, 0x1D, 0x60), 7: (0x16, 0x1E, 0x40), 8: (0x16, 0x1E, 0x60),
    9: (0x17, 0x1F, 0x40), 10: (0x17, 0x1F, 0x60), 11: (0x10, 0x18, 0x40), 12: (0x13, 0x1B, 0x40),
    13: (0x13, 0x1B, 0x60), 14: (0x14, 0x1C, 0x40), 15: (0x14, 0x1C, 0x60),
}
PAC_ATTRIBUTES = [
    'White', 'White Underline', 'Green', 'Green Underline', 'Blue', 'Blue Underline',
    'Cyan', 'Cyan Underline', 'Red', 'Red Underline', 'Yellow', 'Yellow Underline',
    'Magenta', 'Magenta Underline', 'White Italics', 'White Italics Underline',
    'Indent 0', 'Indent 0 Underline', 'Indent 4', 'Indent 4 Underline',
    'Indent 8', 'Indent 8 Underline', 'Indent 12', 'Indent 12 Underline',
    'Indent 16', 'Indent 16 Underline', 'Indent 20', 'Indent 20 Underline',
    'Indent 24', 'Indent 24 Underline', 'Indent 28', 'Indent 28 Underline',
]


def preamble_address_codes():
    codes = {}
    for row, (data_channel_1, data_channel_2, white) in PAC_ROWS.items():
        entries = [(white + at, 'Pre: %s row %d' % (attribute, row))
                   for at, attribute in enumerate(PAC_ATTRIBUTES)]
        codes.update(named(entries, CC1=data_channel_1, CC2=data_channel_2))
    return codes


# CEA-608-E Table 52
EXPECTED_MISCELLANEOUS = named(MISCELLANEOUS, CC1=0x14, CC2=0x1C)
EXPECTED_TAB_OFFSETS = named(TAB_OFFSETS, CC1=0x17, CC2=0x1F)
# CEA-608-E 8.4, B.11.6
EXPECTED_FIELD_2_MISCELLANEOUS = named(MISCELLANEOUS, CC3=0x15, CC4=0x1D)
# CEA-608-E Table 51
EXPECTED_MID_ROW = named(MID_ROW, CC1=0x11, CC2=0x19)
# CEA-608-E Table 3
EXPECTED_ATTRIBUTES = {**named(BACKGROUND, CC1=0x10, CC2=0x18),
                       **named(TRANSPARENT_AND_FOREGROUND, CC1=0x17, CC2=0x1F)}
# CEA-608-E Table 4
EXPECTED_SPECIAL_ASSIGNMENTS = named(SPECIAL_ASSIGNMENTS, CC1=0x17, CC2=0x1F)
# CEA-608-E G.2.2, G.2.3
EXPECTED_ARTICLES = named(ARTICLES + ARTICLE_PAGES, CC1=0x16, CC2=0x1E)
# CEA-608-E Table 53
EXPECTED_PACS = preamble_address_codes()

EXPECTED_TABLES = [EXPECTED_MISCELLANEOUS, EXPECTED_TAB_OFFSETS, EXPECTED_FIELD_2_MISCELLANEOUS,
                   EXPECTED_MID_ROW, EXPECTED_ATTRIBUTES, EXPECTED_SPECIAL_ASSIGNMENTS,
                   EXPECTED_ARTICLES, EXPECTED_PACS]
EXPECTED_CONTROL_CODES = {code: name for table in EXPECTED_TABLES for code, name in table.items()}


# CEA-608-E D.2
NULL = 0x00

# CEA-608-E Table 50
STANDARD_CHARACTER_ROWS = [
    ' !"#$%&\'()á+,-./',
    '0123456789:;<=>?',
    '@ABCDEFGHIJKLMNO',
    'PQRSTUVWXYZ[é]íó',
    'úabcdefghijklmno',
    'pqrstuvwxyzç÷Ññ\u25a0',
]
STANDARD_CHARACTERS = {0x20 + 16 * row + column: glyph
                       for row, glyphs in enumerate(STANDARD_CHARACTER_ROWS)
                       for column, glyph in enumerate(glyphs)}

# CEA-608-E Table 45
DIFFERENT_FROM_ISO_8859_1 = [0x2A, 0x5C, 0x5E, 0x5F, 0x60, 0x7B, 0x7C, 0x7D, 0x7E]

# CEA-608-E Table 49, F.1.1.1
SPECIAL_CHARACTERS = [
    ((0x11, 0x19), {0x30: '®', 0x31: '°', 0x32: '½', 0x33: '¿', 0x34: '™', 0x35: '¢', 0x36: '£',
                    0x37: '♪', 0x38: 'à', 0x39: None, 0x3A: 'è', 0x3B: 'â', 0x3C: 'ê', 0x3D: 'î',
                    0x3E: 'ô', 0x3F: 'û'}),
]

# CEA-608-E 6.4.2
EXTENDED_CHARACTERS = [
    # CEA-608-E Table 5
    ((0x12, 0x1A), {0x20: 'Á', 0x21: 'É', 0x22: 'Ó', 0x23: 'Ú', 0x24: 'Ü', 0x25: 'ü',
                    0x26: '\u2018', 0x27: '¡'}),
    # CEA-608-E Table 6
    ((0x12, 0x1A), {0x28: '*', 0x29: "'", 0x2A: '\u2014', 0x2B: '©', 0x2C: '\u2120',
                    0x2D: '\u25cf', 0x2E: '\u201c', 0x2F: '\u201d'}),
    # CEA-608-E Table 7
    ((0x12, 0x1A), {0x30: 'À', 0x31: 'Â', 0x32: 'Ç', 0x33: 'È', 0x34: 'Ê', 0x35: 'Ë', 0x36: 'ë',
                    0x37: 'Î', 0x38: 'Ï', 0x39: 'ï', 0x3A: 'Ô', 0x3B: 'Ù', 0x3C: 'ù', 0x3D: 'Û',
                    0x3E: '«', 0x3F: '»'}),
    # CEA-608-E Table 8
    ((0x13, 0x1B), {0x20: 'Ã', 0x21: 'ã', 0x22: 'Í', 0x23: 'Ì', 0x24: 'ì', 0x25: 'Ò', 0x26: 'ò',
                    0x27: 'Õ', 0x28: 'õ', 0x29: '{', 0x2A: '}', 0x2B: '\\', 0x2C: '^', 0x2D: '_',
                    0x2E: '|', 0x2F: '~'}),
    # CEA-608-E Table 9
    ((0x13, 0x1B), {0x30: 'Ä', 0x31: 'ä', 0x32: 'Ö', 0x33: 'ö', 0x34: 'ß', 0x35: '¥', 0x36: '¤',
                    0x37: '\u23d0'}),
    # CEA-608-E Table 10
    ((0x13, 0x1B), {0x38: 'Å', 0x39: 'å', 0x3A: 'Ø', 0x3B: 'ø', 0x3C: '\u23a1', 0x3D: '\u23a4',
                    0x3E: '\u23a3', 0x3F: '\u23a6'}),
]

CHOSEN_GLYPHS = {
    # CEA-608-E Table 6 NOTE6, Table 9 NOTE8, Table 10 NOTE9
    (0x12, 0x2A): '\u2500', (0x13, 0x37): '\u2502',
    (0x13, 0x3C): '\u250c', (0x13, 0x3D): '\u2510', (0x13, 0x3E): '\u2514', (0x13, 0x3F): '\u2518',
    # CEA-608-E Table 6
    (0x12, 0x2D): '\u2022',
    # CEA-608-E Table 49
    (0x11, 0x39): ' ',
}


def two_byte_characters(character_sets):
    characters = {}
    for (data_channel_1, data_channel_2), printed in character_sets:
        for second, glyph in printed.items():
            glyph = CHOSEN_GLYPHS.get((data_channel_1, second), glyph)
            characters[(data_channel_1, second)] = characters[(data_channel_2, second)] = glyph
    return characters


EXPECTED_SPECIAL_CHARACTERS = two_byte_characters(SPECIAL_CHARACTERS)
EXPECTED_EXTENDED_CHARACTERS = two_byte_characters(EXTENDED_CHARACTERS)
EXPECTED_CHARACTERS = {**EXPECTED_SPECIAL_CHARACTERS, **EXPECTED_EXTENDED_CHARACTERS}


# CEA-608-E Table 1
OTHER_CHANNEL = {'CC1 ': 'CC2 ', 'CC2 ': 'CC1 ', 'CC3 ': 'CC4 ', 'CC4 ': 'CC3 '}


# CEA-608-E 3.2.2, 5.3
def odd_parity(byte):
    ones = 0
    for bit in range(8):
        ones ^= byte >> bit & 1
    return ones == 1


def pair(code):
    return '%02X %02X' % code


def differences(got, want):
    return [(key, got.get(key), want.get(key))
            for key in sorted(set(got) | set(want)) if got.get(key) != want.get(key)]


def by_second_byte(characters, first):
    return {second: glyph for (byte1, second), glyph in characters.items() if byte1 == first}


def control_mismatches(expected):
    mismatches = []
    for code, name in sorted(expected.items()):
        got = decode_byte_pair(True, *code) if is_control(*code) else None
        if got != name:
            mismatches.append((pair(code), got, name))
    return mismatches


def character_mismatches(expected):
    mismatches = []
    for code, glyph in sorted(expected.items()):
        for default_unicode in (True, False):
            got = decode_byte_pair(False, *code, default_unicode)
            if got != glyph:
                mismatches.append((pair(code), default_unicode, got, glyph))
    return mismatches


def one_byte(byte, unknown, default_unicode):
    if byte == NULL:
        return ''
    if byte in STANDARD_CHARACTERS:
        return STANDARD_CHARACTERS[byte]
    return unknown % byte if default_unicode else ''


def test_miscellaneous_control_codes():
    # CEA-608-E Table 52
    assert control_mismatches(EXPECTED_MISCELLANEOUS) == []


def test_field_2_miscellaneous_control_codes():
    # CEA-608-E 8.4, B.11.6
    assert control_mismatches(EXPECTED_FIELD_2_MISCELLANEOUS) == []


def test_tab_offsets():
    # CEA-608-E Table 52
    assert control_mismatches(EXPECTED_TAB_OFFSETS) == []


def test_mid_row_codes():
    # CEA-608-E Table 51
    assert control_mismatches(EXPECTED_MID_ROW) == []


def test_background_and_foreground_attribute_codes():
    # CEA-608-E Table 3
    assert control_mismatches(EXPECTED_ATTRIBUTES) == []
    assert differences(BACKGROUND_COLOR_CODES, {second: name for second, _, name in BACKGROUND}) == []


def test_special_assignments():
    # CEA-608-E Table 4
    assert control_mismatches(EXPECTED_SPECIAL_ASSIGNMENTS) == []


def test_article_codes():
    # CEA-608-E G.2.2, G.2.3
    assert control_mismatches(EXPECTED_ARTICLES) == []


def test_preamble_address_codes():
    # CEA-608-E Table 53
    assert len(EXPECTED_PACS) == 15 * 32 * 2
    assert control_mismatches(EXPECTED_PACS) == []
    assert CC1_PREAMBLE_COLS == [PAC_ROWS[row][0] for row in range(1, 16)]
    assert CC2_PREAMBLE_COLS == [PAC_ROWS[row][1] for row in range(1, 16)]
    for white, table in ((0x40, PREAMBLE_ODD), (0x60, EVEN_PREAMBLE)):
        want = {white + at: 'Pre: ' + attribute for at, attribute in enumerate(PAC_ATTRIBUTES)}
        assert differences(table, want) == []


def test_every_other_pair_is_not_a_control_code():
    assert sum(len(table) for table in EXPECTED_TABLES) == len(EXPECTED_CONTROL_CODES)
    stray = [pair((byte1, byte2)) for byte1, byte2 in product(range(128), repeat=2)
             if (byte1, byte2) not in EXPECTED_CONTROL_CODES and is_control(byte1, byte2)]
    assert stray == []
    assert differences(ALL_CC_CONTROL_CODES, EXPECTED_CONTROL_CODES) == []


def test_intermediate_tables_agree_with_the_standard():
    tables = [CC1_CONTROL_CODES, CC2_CONTROL_CODES, CC3_CONTROL_CODES, CC4_CONTROL_CODES,
              CC1_MID_ROW_CODES, CC2_MID_ROW_CODES, CC1_BACKGROUND_CHARS, CC2_BACKGROUND_CHARS]
    wrong = [(pair(code), name) for table in tables for code, name in table.items()
             if EXPECTED_CONTROL_CODES.get(code) != name]
    wrong += [(pair(code), name) for table in (CONTROL_CODES, MID_ROW_CODES)
              for code, name in table.items() if EXPECTED_CONTROL_CODES.get(code) != 'CC1 ' + name]
    assert wrong == []


def test_standard_characters():
    # CEA-608-E Table 50
    assert [len(glyphs) for glyphs in STANDARD_CHARACTER_ROWS] == [16] * 6
    # CEA-608-E D.2
    assert differences(CC_TABLE, {NULL: '', **STANDARD_CHARACTERS}) == []
    mismatches = []
    for code, glyph in sorted(STANDARD_CHARACTERS.items()):
        for byte1, byte2 in ((code, NULL), (NULL, code)):
            for default_unicode in (True, False):
                got = decode_byte_pair(False, byte1, byte2, default_unicode)
                if got != glyph:
                    mismatches.append((pair((byte1, byte2)), default_unicode, got, glyph))
    assert mismatches == []


def test_standard_characters_differ_from_iso_8859_1_only_where_table_45_says():
    # CEA-608-E Table 45
    different = [code for code in range(0x20, 0x7F) if decode_byte_pair(False, code, NULL) != chr(code)]
    assert ['%02X' % code for code in different] == ['%02X' % code for code in DIFFERENT_FROM_ISO_8859_1]


def test_special_characters():
    # CEA-608-E Table 49
    assert len(EXPECTED_SPECIAL_CHARACTERS) == 16 * 2
    assert character_mismatches(EXPECTED_SPECIAL_CHARACTERS) == []
    assert differences(SPECIAL_CHARS_TABLE, by_second_byte(EXPECTED_SPECIAL_CHARACTERS, 0x11)) == []


def test_extended_characters():
    # CEA-608-E 6.4.2, Tables 5-10
    assert len(EXPECTED_EXTENDED_CHARACTERS) == 64 * 2
    assert character_mismatches(EXPECTED_EXTENDED_CHARACTERS) == []
    assert differences(EXTENDED_SPANISH_FRENCH, by_second_byte(EXPECTED_EXTENDED_CHARACTERS, 0x12)) == []
    assert differences(EXTENDED_PORTUGUESE_GERMAN_DANISH,
                       by_second_byte(EXPECTED_EXTENDED_CHARACTERS, 0x13)) == []


def test_every_other_pair_decodes_one_byte_at_a_time():
    assert differences(ALL_SPECIAL_CHARS, EXPECTED_CHARACTERS) == []
    mismatches = []
    for byte1, byte2 in product(range(128), repeat=2):
        if (byte1, byte2) in EXPECTED_CONTROL_CODES or (byte1, byte2) in EXPECTED_CHARACTERS:
            continue
        for default_unicode in (True, False):
            if 0x10 <= byte1 <= 0x1F:
                # CEA-608-E 6.3
                want = '?b1(%02x)?b2(%02x)' % (byte1, byte2) if default_unicode else ''
            else:
                want = (one_byte(byte1, '?b1(%02x)', default_unicode)
                        + one_byte(byte2, '?b2(%02x)', default_unicode))
            got = decode_byte_pair(False, byte1, byte2, default_unicode)
            if got != want:
                mismatches.append((pair((byte1, byte2)), default_unicode, got, want))
    assert mismatches == []


def test_data_channel_2_is_data_channel_1_with_0x08_set():
    # CEA-608-E Tables 3-10, 51-53, F.1.1.1, B.13, C.18
    mismatches = []
    for (byte1, byte2), name in sorted(ALL_CC_CONTROL_CODES.items()):
        if (name[:4] in ('CC2 ', 'CC4 ')) != bool(byte1 & 0x08):
            mismatches.append((pair((byte1, byte2)), name))
        twin = ALL_CC_CONTROL_CODES.get((byte1 ^ 0x08, byte2))
        if twin != OTHER_CHANNEL.get(name[:4], '') + name[4:]:
            mismatches.append((pair((byte1, byte2)), name, twin))
    for (byte1, byte2), glyph in sorted(ALL_SPECIAL_CHARS.items()):
        if ALL_SPECIAL_CHARS.get((byte1 ^ 0x08, byte2)) != glyph:
            mismatches.append((pair((byte1, byte2)), glyph))
    assert mismatches == []

    assert [byte for byte in CC1_PREAMBLE_COLS if byte & 0x08] == []
    assert [byte | 0x08 for byte in CC1_PREAMBLE_COLS] == CC2_PREAMBLE_COLS
    for one, two in ((CC1_CONTROL_CODES, CC2_CONTROL_CODES), (CC1_MID_ROW_CODES, CC2_MID_ROW_CODES),
                     (CC1_BACKGROUND_CHARS, CC2_BACKGROUND_CHARS), (CC3_CONTROL_CODES, CC4_CONTROL_CODES)):
        want = {(byte1 | 0x08, byte2): OTHER_CHANNEL.get(name[:4], '') + name[4:]
                for (byte1, byte2), name in one.items()}
        assert differences(two, want) == []


def test_field_2_moves_exactly_the_miscellaneous_codes():
    # CEA-608-E 8.4, B.11.6
    assert sorted(CC3_CONTROL_CODES) == [(0x15, second) for second in range(0x20, 0x30)]
    assert sorted(CC4_CONTROL_CODES) == [(0x1D, second) for second in range(0x20, 0x30)]
    mismatches = []
    for second in range(0x20, 0x30):
        for field_1, field_2, channel in ((0x14, 0x15, 'CC3 '), (0x1C, 0x1D, 'CC4 ')):
            got = ALL_CC_CONTROL_CODES.get((field_2, second))
            want = channel + ALL_CC_CONTROL_CODES.get((field_1, second), '')[4:]
            if got != want:
                mismatches.append((pair((field_2, second)), got, want))
    assert mismatches == []
    field_2 = sorted(code for code, name in ALL_CC_CONTROL_CODES.items() if name[:4] in ('CC3 ', 'CC4 '))
    assert field_2 == sorted([*CC3_CONTROL_CODES, *CC4_CONTROL_CODES])


def test_tables_hold_only_seven_bit_codes():
    tables = ['CC_TABLE', 'SPECIAL_CHARS_TABLE', 'EXTENDED_SPANISH_FRENCH',
              'EXTENDED_PORTUGUESE_GERMAN_DANISH', 'ALL_SPECIAL_CHARS', 'CONTROL_CODES',
              'BACKGROUND_COLOR_CODES', 'CC1_BACKGROUND_CHARS', 'CC2_BACKGROUND_CHARS', 'MID_ROW_CODES',
              'PREAMBLE_ODD', 'EVEN_PREAMBLE', 'CC1_CONTROL_CODES', 'CC2_CONTROL_CODES',
              'CC3_CONTROL_CODES', 'CC4_CONTROL_CODES', 'CC1_MID_ROW_CODES', 'CC2_MID_ROW_CODES',
              'CC1_PREAMBLE_COLS', 'CC2_PREAMBLE_COLS', 'ALL_CC_CONTROL_CODES', 'ROLL_UP_LEN']
    wide = [(name, key) for name in tables for key in getattr(cc_decode, name)
            if not all(0x00 <= byte <= 0x7F for byte in (key if isinstance(key, tuple) else (key,)))]
    assert wide == []


def test_no_pair_is_both_a_control_code_and_a_character():
    assert sorted(set(ALL_CC_CONTROL_CODES) & set(ALL_SPECIAL_CHARS)) == []
    assert [pair(code) for code in sorted(ALL_SPECIAL_CHARS) if is_control(*code)] == []


def test_roll_up_lengths():
    # CEA-608-E Table 52
    assert ROLL_UP_LEN == {0x25: 2, 0x26: 3, 0x27: 4}


def test_odd_parity_table():
    # CEA-608-E 3.2.2, 5.3
    assert len(NO_PARITY_TO_ODD_PARITY) == 128
    wrong = ['%02X' % value for value, byte in enumerate(NO_PARITY_TO_ODD_PARITY)
             if not 0x00 <= byte <= 0xFF or byte & 0x7F != value or not odd_parity(byte)]
    assert wrong == []


def test_parity_check_for_every_byte():
    # CEA-608-E 3.2.2
    assert ['%02X' % byte for byte in range(256) if cea608_parity_ok(byte) != odd_parity(byte)] == []


def test_byte_layer_returns_the_data_bits_and_the_parity_check():
    # CEA-608-E Table 2
    wrong = [('%02X' % byte, offset) for byte, offset in product(range(256), (0, 8))
             if cea608_byte(byte, 0, offset) != (byte & 0x7F, odd_parity(byte))]
    assert wrong == []


def test_one_noisy_data_bit_is_corrected_by_parity():
    wrong = []
    for byte, offset, bit, other in product(range(256), (0, 8), range(7), (0x00, 0xFF)):
        noisy = 1 << (offset + bit) | other << (8 - offset)
        data = byte & 0x7F if odd_parity(byte) else (byte ^ 1 << bit) & 0x7F
        got = cea608_byte(byte, noisy, offset)
        if got != (data, True):
            wrong.append(('%02X' % byte, offset, bit, other, got))
    assert wrong == []


def test_no_correction_without_exactly_one_noisy_data_bit_and_a_clean_parity_bit():
    corrected = {1 << bit for bit in range(7)}
    wrong = []
    for byte, offset, mask, other in product(range(256), (0, 8), range(256), (0x00, 0xFF)):
        if mask in corrected:
            continue
        noisy = mask << offset | other << (8 - offset)
        got = cea608_byte(byte, noisy, offset)
        if got != (byte & 0x7F, odd_parity(byte)):
            wrong.append(('%02X' % byte, offset, '%02X' % mask, other, got))
    assert wrong == []
