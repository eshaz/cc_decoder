#!/usr/local/bin/python
# coding: utf-8
""" The StarSight text compression table

US6216265B1 defers this to "Appendix A", which no patent office ever printed, so the
table was recovered from a shipped implementation instead: `SSLOAD.DLL`, the "Microsoft
StarSight Guide Data Loader" that Microsoft put on the Windows 98 CD under a 1996
license from StarSight (the obligation to hand over "StarSight data formats" to build
that loader is recorded in StarSight's FY1996 10-K). The same tables appear byte for
byte in `SSSCAN.EXE` and `BPCCTL.DLL` on the same disc, and independently in Gemstar's
own DOS tool `FREQCCD_.EXE`, which still carries its Borland debug symbols.

It is Huffman over 127 symbols, stored in the DLL as a 256 byte binary tree. Symbols
0x01-0x1F are not control characters: they expand to whole words and fragments - ' the ',
'ing ', 'and ', "'s ", 'tion' - which is why the code averages about 6.3 bits per token
while a token is often several characters, and why reading it as a character code did
not work. Kraft sum is exactly 1.

Verified against the three 1998 captures: 11,029 of 11,031 compressed titles and 225 of
231 descriptions decode cleanly, the failures all in the noisiest capture.

See docs/starsight.md. Public domain / Unlicense, as with the rest of ccDecoder.
"""

# codeword, most significant bit first -> the text it expands to
STARSIGHT_HUFFMAN = {
    '0100': 'i', '0111': ' ', '1000': 'e', '00011': 's ', '00100': 'm', '00101': 'h',
    '00111': 'd', '01101': 'r', '10100': 'c', '10110': 's', '10111': 't', '11001': 'l',
    '11101': 'o', '11110': 'a', '000000': 'n', '000010': 'a ', '000011': 'ar',
    '000100': ', ', '001100': 'er', '010100': 'f', '010101': 'b', '010110': 'in',
    '011001': 'an', '100111': '\x00', '101010': 'g', '110000': '.', '110100': 'u',
    '110110': 'e ', '111000': 'p', '0000010': 'to ', '0011011': 'or', '0101111': ' the ',
    '0110000': 'er ', '0110001': 'A', '1001000': 'th', '1001001': 'al', '1001011': 'y',
    '1010110': 't ', '1010111': 'st', '1100011': 'k', '1101110': 'y ', '1110010': 'on',
    '1111101': 'v', '1111110': 'en', '1111111': 'w', '00000111': 'H', '00010100': 'for',
    '00010101': '-', '00010111': 'D', '00110100': 'J', '00110101': 'R', '01011101': "'s ",
    '10010101': 'B', '10011000': 'un', '10011010': 'of ', '11000100': 'ed ',
    '11000101': 'ur', '11010101': 'M', '11010110': 'C', '11010111': 'S', '11100110': 'ing ',
    '11100111': 'and ', '000001101': 'The ', '000101100': 'with ', '000101101': 'z',
    '010111000': 'K', '100101000': ';', '100110010': 'E', '100110011': 'x',
    '100110111': 'tion', '110101000': '"', '110111100': 'F', '110111101': 'P',
    '110111110': 'G', '110111111': 'W', '111110000': 'T', '111110011': 'L',
    '0101110010': 'O', '0101110011': 'q', '1001010011': 'V', '1001101100': 'when ',
    '1101010010': 'I', '1111100010': ':', '1111100100': 'N', '1111100101': 'j',
    '00000110000': 'Y', '00000110001': '(', '00000110010': ')', '10010100100': 'U',
    '10011011010': '9', '10011011011': '0', '11010100111': '1', '11111000111': "'",
    '000001100110': 'Z', '100101001010': '7', '110101001101': '2', '111110001101': ',',
    '1001010010110': '8', '1001010010111': '6', '1101010011000': '5', '1101010011001': 'Q',
    '1111100011000': '3', '1111100011001': '4', '00000110011100': '&',
    '00000110011111': '$', '000001100111010': '?', '000001100111100': 'X',
    '0000011001110111': '/', '0000011001111011': '!', '00000110011101100': '`',
    '00000110011101101': '*', '00000110011110100000': '+', '00000110011110100001': '<',
    '00000110011110100010': '=', '00000110011110100011': '>', '00000110011110100100': '@',
    '00000110011110100101': '[', '00000110011110100110': '\\', '00000110011110100111': ']',
    '00000110011110101000': '^', '00000110011110101001': '_', '00000110011110101010': '{',
    '00000110011110101011': '|', '00000110011110101100': '}', '00000110011110101101': '~',
    '00000110011110101110': '#', '00000110011110101111': '%'
}

STARSIGHT_HUFFMAN_MAX_BITS = max(len(c) for c in STARSIGHT_HUFFMAN)
