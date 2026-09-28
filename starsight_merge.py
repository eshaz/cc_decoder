#!/usr/bin/env python3
""" Merge decoded StarSight guides into one

A single capture is a half hour of a carousel, so it holds part of a schedule and part
of the names for it. Tapes of the same channel taken on different days overlap, and
merging them gives back more of the guide than any one of them carries: October's
schedule with November's titles against it, or a fortnight of listings end to end.

Takes the `.starsight.json` a decode writes - the normalized export, which is the whole
of what a capture knew - and writes a merged one beside a merged page, through the same
code that renders a single decode.

    python3 starsight_merge.py 'KET_1998-*.starsight.json' -o KET-merged
    python3 starsight_merge.py a.starsight.json b.starsight.json -o merged

Globs are expanded here as well as by the shell, so quoting them works either way.
Everything is keyed and deduplicated rather than concatenated: the same program rides
the carousel over and over, and two tapes a week apart share the days between them. A
key that arrives twice with two different values is reported rather than quietly
overwritten - if that count is large the inputs are probably not the same service, since
channel and show numbers are internal to one StarSight line-up and mean nothing across
markets.

Public domain / Unlicense, as with the rest of ccDecoder.
"""

import argparse
import glob
import json
import os
import sys

from lib.starsight import (merge_guides, read_starsight_json, write_starsight_html,
                           write_starsight_json)

# a season's worth of tapes disagrees on about one show number in a thousand; tapes six
# months apart disagree on one in seven. Anything above a couple of percent is the
# second case, and worth saying out loud.
REUSE_WARNING = 0.02


def expand(patterns):
    """ Every path the arguments name, globbed, deduplicated, in order """
    paths, seen = [], set()
    for pattern in patterns:
        found = sorted(glob.glob(pattern)) or ([pattern] if os.path.exists(pattern) else [])
        if not found:
            sys.exit('nothing matches %s' % pattern)
        for path in found:
            if path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def summarise(name, guide):
    channels = {slot[1] for slot in guide['slots']}
    return '%-34s %6d listings %4d channels %5d titles %4d descriptions' % (
        name, len(guide['slots']), len(channels), len(guide['titles']),
        len(guide['descriptions']))


def main():
    parser = argparse.ArgumentParser(
        description=__doc__.split('\n')[1],
        epilog='writes NAME.starsight.json and NAME.starsight.html',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('inputs', nargs='+',
                        help='decoded .starsight.json files, or globs matching them')
    parser.add_argument('-o', required=True, metavar='NAME',
                        help='output name, without extension')
    parser.add_argument('--expire', action='store_true',
                        help="drop listings that had already finished by the newest "
                             "capture's clock, the way a receiver would. Off by default: "
                             "on a merge it deletes every older tape's schedule")
    args = parser.parse_args()

    guides = []
    for path in expand(args.inputs):
        try:
            document = json.load(open(path))
        except ValueError as error:
            sys.exit('%s is not JSON: %s' % (path, error))
        if 'listings' not in document and 'programs' not in document:
            sys.exit('%s has no listings or programs - is it a .starsight.json?' % path)
        guide = read_starsight_json(document)
        guides.append(guide)
        print(summarise(os.path.basename(path), guide))

    merged, report = merge_guides(guides, expire=args.expire)
    print(summarise('merged (%d files)' % len(guides), merged))

    if report['channel days superseded']:
        print('  %d channel-days were carried by more than one input; the later one won'
              % report['channel days superseded'])
    if report['slots expired']:
        print('  %d listings had finished by the newest capture and were dropped'
              % report['slots expired'])
    if report['slots overlapping']:
        print('  %d listings start on a channel where another already does'
              % report['slots overlapping'])

    for table, label in (('titles', 'show numbers'), ('descriptions', 'description numbers')):
        shared, reused = report[table + ' shared'], report[table + ' reused']
        if not shared:
            continue
        print('  %d of %d %s shared between inputs name something different (%.0f%%)'
              % (reused, shared, label, 100.0 * reused / shared))
        if reused > shared * REUSE_WARNING:
            print('  ** these captures look like different seasons. StarSight reuses %s\n'
                  '  ** between them, so the merge is mixing two meanings of the same\n'
                  '  ** number and the first one seen wins. Merge tapes from one season.'
                  % label)

    write_starsight_json(args.o, merged)
    write_starsight_html(args.o, merged)
    print('wrote %s.starsight.json and %s.starsight.html' % (args.o, args.o))


if __name__ == '__main__':
    main()
