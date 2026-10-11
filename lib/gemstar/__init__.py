"""Gemstar EPG data (VCR Plus+ / Guide Plus+ / TV Guide On Screen) carried in the NTSC VBI.

Modules
    capture   readers for raw VBI captures (ld-decode 4fsc .tbc/.y16/.flac, 1440-sample card dumps)
    slicer    Gemstar 2X and 1X bit slicers
    streams   demultiplexing 2X lines into Type B blocks and `|B` packets, message assembly, 1X clock
    freqccd   runs Gemstar's own routines from a user-supplied FREQCCD_.EXE under emulation
    guide     SIP listings, genre tree, lineups and other message types -> JSON
    graphics  0x1E graphics objects (ad bitmaps, detail pages, PNG screens) -> images
    nabts     NABTS (EIA-516) packet slicer

See docs/gemstar.md.
"""
