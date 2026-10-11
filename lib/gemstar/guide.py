"""Decoded guide content: SIP program listings, genre tree, channel lineups and the other
Type B message types that are understood.

Message types (byte 0 of a decrypted Type B message):
    0x00  SIP  Show Information Packet: one channel's programs (FREQCCD SIP::Process)
    0x07  DPPGS table (1996 numbering)          0x09  Sort / genre menu (old form of 0x26)
    0x10  receiver UI / resource data (long-lived, identical 1999-2007)
    0x11  per-lineup table (undecoded)          0x12  numbered per-lineup records (undecoded)
    0x13  channel lineup                        0x14  per-lineup data (undecoded)
    0x16  object display trigger (2007)         0x1A  ad targeting / display rules
    0x1B  ad schedule                           0x1E  graphics objects (ads, detail pages, PNG screens)
    0x26  genre tree                            0x27  service promo panel
    0x28  channel sort order                    0x3A  small control message
    0x3D  file announcement (2007, e.g. /system/patch.bin)
    0x40  dynamic dictionary (DDPACKET.BIN is a saved copy)
"""

import collections
import re
import struct

NETWORK = {1: 'ABC', 2: 'CBS', 3: 'NBC', 4: 'FOX', 5: 'PBS'}
RATING = re.compile(r'^(TV(Y7|Y|G|PG|14|MA)(-[A-Z]+)?|G|PG|PG13|R|NC17|NR|X)$')
MOVIE = re.compile(r'^(?P<label>.*?)\s*\b(?P<year>(19|20)\d\d)\b\s*(?P<stars>\*+)?,?\s*(?P<rest>.*)$')
TYPE_NAMES = {0x00: 'SIP program listings', 0x07: 'DPPGS table', 0x09: 'sort / genre menu',
              0x10: 'receiver UI / resource data', 0x11: 'per-lineup table', 0x12: 'per-lineup numbered records',
              0x13: 'channel lineup', 0x14: 'per-lineup data', 0x16: 'object display trigger',
              0x1A: 'ad targeting / display rules', 0x1B: 'ad schedule', 0x1E: 'graphics objects',
              0x26: 'genre tree', 0x27: 'service promo panel', 0x28: 'channel sort order',
              0x3A: 'small control message', 0x3D: 'file announcement', 0x40: 'dynamic dictionary'}


# ---- SIP listings -----------------------------------------------------------------------------

def sip_listings(messages, fq, year, month, day):
    """Program rows from every type-0 message, decoded by FREQCCD.
    A message shorter than its declared length (bytes 1-2) lost its last block(s) on tape; decomp then
    runs off the end and pads with '&', so such text is cut back to ' [...]' and the row marked incomplete."""
    rows = []
    for mi, m in enumerate(messages):
        data = m['data']
        if data[0] != 0:
            continue
        complete = len(data) >= struct.unpack_from('<H', data, 1)[0]
        gch, date, progs = fq.sip(data, year, month, day)
        for i, (st, en, cat, title, info, desc, *extra) in enumerate(progs):
            s = [title, info, desc]
            trunc = False
            for k in range(3):
                j = s[k].find(' & &')
                if j < 0 and s[k].startswith('& &'):
                    j = 0
                if j >= 0:
                    s[k] = s[k][:j].rstrip() + ' [...]'
                    trunc = True
            row = dict(guide_channel=gch, date=date, start=st, end=en, category=cat, title=s[0], info=s[1],
                       description=s[2].replace('\x0c', ' / '), complete=complete and not trunc,
                       vcr_plus_code=data[4:8].hex(), message=mi, slot=i, misc=extra[0] if extra else None)
            if row['complete'] or row['title'].strip(' &[.]'):
                rows.append(row)
    return rows


def misc_flags(misc):
    """SIP::GetMisc flag byte (4th byte of each program record). FREQCCD prints it only as 'Misc=%02Xh';
    the meanings below come from comparing ~2,000 Dec 2001 listings against the programs:
      bit 7  closed captioned  (off for nearly all infomercials; on for I Love Lucy, M*A*S*H)
      bit 6  stereo            (almost never on pre-1980 films; on for current sitcoms)
      bit 5  SAP / second audio (probable: PBS shows with described video, some network prime time)
      bit 4  live              (sports events, SportsCenter, live religious shows)
      bits 0-3 always 0 so far."""
    if misc is None:
        return {}
    return dict(closed_captioned=bool(misc & 0x80), stereo=bool(misc & 0x40), sap=bool(misc & 0x20),
                live=bool(misc & 0x10), misc_raw=misc)


def flag_text(misc):
    f = misc_flags(misc)
    return ' '.join(n for k, n in (('closed_captioned', 'CC'), ('stereo', 'Stereo'), ('sap', 'SAP'), ('live', 'Live'))
                    if f.get(k))


def listings_text(rows):
    out = []
    for r in sorted(rows, key=lambda r: (r['guide_channel'], r['date'], r['start'])):
        out.append(f"ch {r['guide_channel']:4d}  {r['date']}  {r['start'] // 60:02d}:{r['start'] % 60:02d}-"
                   f"{r['end'] // 60:02d}:{r['end'] % 60:02d}  cat {r['category']:3d}  {r['title']}"
                   + (f"  [{flag_text(r.get('misc'))}]" if flag_text(r.get('misc')) else ''))
        for k in ('info', 'description'):
            if r[k].strip('&'):
                out.append(f'            {r[k]}')
        if not r['complete']:
            out.append('            (message incomplete on tape: last block(s) lost)')
    return '\n'.join(out) + '\n'


def split_info(s):
    """'Western 1967 *** John Wayne, Robert Mitchum, TVG' -> fields."""
    s = s.replace('\x0c', '').strip()
    d = dict(movie_genre_or_country=None, year=None, star_rating=None, cast=[], tv_rating=None)
    if not s or s.strip('&') == '':
        return d
    parts = [p.strip() for p in s.split(',')]
    if RATING.match(parts[-1]):
        d['tv_rating'] = parts.pop()
    s = ', '.join(parts)
    m = MOVIE.match(s)
    if m:
        d['movie_genre_or_country'] = m['label'] or None
        d['year'] = int(m['year'])
        d['star_rating'] = len(m['stars']) if m['stars'] else None
        d['cast'] = [c.strip() for c in m['rest'].split(',') if c.strip()]
    elif s:
        d['cast'] = [s]
    return d


# ---- other message types ----------------------------------------------------------------------

def parse_genres(msgs):
    """0x26 (or the older 0x09) genre tree: groups 0x10 Movies, 0x20 Sports ... 0x70 Series, each with
    subgenres; a SIP category byte is (group | subgenre)."""
    groups = {}
    for b in msgs:
        i = 6 if b[0] == 0x09 else 14         # 0x09: 09 len(2) ?? ?? ?? then the group; 0x26 has a longer header
        if i + 2 > len(b):
            continue
        code, ln = b[i], b[i + 1]
        name = b[i + 2:i + 2 + ln].decode('latin1')
        i += 2 + ln
        n = b[i]
        i += 1
        subs = []
        for _ in range(n):
            if i + 2 > len(b):
                break
            c, l = b[i], b[i + 1]
            if i + 2 + l > len(b):
                break
            subs.append(dict(code=c, name=b[i + 2:i + 2 + l].decode('latin1')))
            i += 2 + l
        groups[code] = dict(code=code, name=name, subgenres=subs)
    return [groups[k] for k in sorted(groups)]


def lineup_key(b):
    """Home station + lineup id + variant (byte 5: 0 analog, 1 digital)."""
    home = b[13:13 + b[12]].decode('latin1')
    return f"{home}-{b[2:4].hex()}-{'digital' if b[5] else 'analog'}"


def parse_lineup(b):
    """0x13: header ... 0A xx TAG LEN HOME TAG COUNT, then [position][network id][len][name] entries.
    Network id 1-5 = ABC, CBS, NBC, FOX, PBS for broadcast stations."""
    i = 13 + b[12] + 2
    rows = []
    while i + 3 <= len(b) and b[i]:
        pos, net, ln = b[i], b[i + 1], b[i + 2]
        name = b[i + 3:i + 3 + ln]
        if not ln or not all(32 <= c < 127 for c in name):
            break
        rows.append(dict(position=pos, network_id=net, name=name.decode(), network=NETWORK.get(net)))
        i += 3 + ln
    return rows


def object_header(b):
    """Offset of the object/record body: just after the `3F xx 00 0A` that closes the header expression
    (fixed in 2001-2003, preceded by a receiver-targeting expression in 2007)."""
    for i in range(10, min(len(b) - 4, 160)):
        if b[i] == 0x3F and b[i + 2] == 0x00 and b[i + 3] == 0x0A:
            return i + 4
    return None


def build_guide(messages, listings=None, a11=None, recording_year=None, source=None, extra=None):
    """Everything understood from one line's Type B messages, as one JSON-ready dict."""
    by = collections.defaultdict(list)
    for m in messages:
        by[m['data'][0]].append(m['data'])
    genres = parse_genres(by[0x26] or by[0x09])
    gname = {g['code']: g['name'] for g in genres}
    sname = {s['code']: s['name'] for g in genres for s in g['subgenres']}

    lineups = {}
    for b in by[0x13]:
        key = lineup_key(b)
        rows = parse_lineup(b)
        if len(rows) > len(lineups.get(key, [])):
            lineups[key] = rows

    records = {}
    for b in by[0x12]:
        k = (b[15], b[10])
        if k not in records or len(b) > len(records[k]):
            records[k] = b
    promos = [dict(strings=[s.decode('latin1') for s in re.findall(rb'[ -~]{4,}', b)], data_hex=b.hex()) for b in by[0x27]]
    order = {}
    for b in by[0x28]:
        sep = b[11]
        vals = []
        i = 10
        while i + 1 < len(b) and b[i + 1] == sep:
            vals.append(b[i])
            i += 2
        order[b[2:4].hex()] = vals

    progs = []
    for r in sorted(listings or [], key=lambda r: (r['date'], r['guide_channel'], r['start'])):
        d = r['date']
        y = None
        if recording_year:
            y = recording_year - recording_year % 10 + d // 10000
            if y > recording_year + 1:
                y -= 10
        cat = r['category']
        p = dict(air_date=f'{y:04d}-{d // 100 % 100:02d}-{d % 100:02d}' if y else str(d),
                 start=f"{(r['start'] // 60) % 24:02d}:{r['start'] % 60:02d}",
                 end=f"{(r['end'] // 60) % 24:02d}:{r['end'] % 60:02d}",
                 duration_min=(r['end'] - r['start']) % 1440, guide_channel=r['guide_channel'],
                 vcr_plus_code=r.get('vcr_plus_code'), title=r['title'].strip('&').strip() or None,
                 description=r['description'].replace('\x0c', '').strip('& ').strip() or None,
                 category_code=cat, genre=gname.get(cat & 0xF0) if cat else None,
                 subgenre=sname.get(cat) if cat and cat & 0x0F else None,
                 info_raw=r['info'].replace('\x0c', '').strip('& ').strip() or None,
                 complete=r['complete'], message=r.get('message'))
        p.update(split_info(r['info']))
        p.update(misc_flags(r.get('misc')))
        progs.append(p)

    other = []
    for t in sorted(by):
        if t in (0x00, 0x09, 0x12, 0x13, 0x26, 0x27, 0x28, 0x1E, 0x1B):
            continue
        for b in by[t]:
            item = dict(type=f'0x{t:02X}', meaning=TYPE_NAMES.get(t, 'unknown'), bytes=len(b), data_hex=b.hex())
            if t == 0x3D:
                item['strings'] = [s.decode('latin1') for s in re.findall(rb'[ -~]{4,}', b)]
            other.append(item)

    out = dict(
        capture=dict(source=source, recording_year=recording_year,
                     message_counts={f'0x{t:02X}': len(v) for t, v in sorted(by.items()) if v},
                     message_types={f'0x{t:02X}': TYPE_NAMES.get(t, 'unknown') for t in sorted(by) if by[t]}),
        genres=genres, lineups=lineups, a11_lineups=a11 or {},
        records_0x12=[dict(lineup_tag=f'0x{tag:02X}', record=rec, bytes=len(b), data_hex=b.hex())
                      for (tag, rec), b in sorted(records.items())],
        promos=promos, channel_sort_order=order, programs=progs, other_messages=other)
    if extra:
        out.update(extra)
    return out
