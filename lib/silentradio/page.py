#!/usr/local/bin/python
# coding: utf-8
""" The page of every story, as each channel's sign played them, and beside it each story's
and channel's SVG and player

Public domain / Unlicense, as with the rest of ccDecoder.
"""

import html
import json
import os

from multiprocessing import Pool

from lib.cc_decode import get_output_function
from lib.silentradio import scene
from lib.silentradio.polar import polar_case, polar_vectors, swing_layer
from lib.silentradio.records import _channels, _lineage, _loop, _nearest
from lib.silentradio.sign import SIGN_TICK_MS, SIGN_WIDTH, sign_parameters, sign_tables

SILENT_RADIO_HTML_STYLE = """
:root{color-scheme:light dark;--bg:#fff;--fg:#1a1a1a;--muted:#666;--led:#ffb000;--line:#ddd;--lamp:.78;--cap:round}
@media(prefers-color-scheme:dark){:root{--bg:#161616;--fg:#e8e8e8;--muted:#999;--line:#333}}
body{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;margin:0 auto;
max-width:960px;padding:16px}
h1{font-size:22px}.meta{color:var(--muted);font-size:13px;margin:18px 0 4px}
.tab{position:absolute;opacity:0}
.tab+label{display:inline-block;margin:22px 2px 0 0;padding:6px 12px;border:1px solid var(--line);
border-bottom:0;border-radius:6px 6px 0 0;color:var(--muted);font-size:13px;cursor:pointer}
.tab:checked+label{color:var(--led);background:var(--sign);border-color:var(--sign)}
.tab:focus-visible+label{outline:2px solid var(--led)}
.channel{border-top:1px solid var(--line)}
.sign{background:var(--sign);color:var(--led);border-radius:6px;padding:10px 14px;
font:14px/1.5 ui-monospace,monospace;overflow-x:auto;border:1px solid var(--line)}
.sign p{margin:0 0 4px;white-space:pre-wrap}
.led{position:relative;width:100%;max-width:896px;aspect-ratio:112/15;margin:4px 0 12px}
.led img{display:block;width:100%;height:100%}
.led svg{display:block;width:100%;height:100%}.led.live{cursor:pointer}.led.live img{display:none}
.led.paused{outline:1px dashed var(--muted);outline-offset:2px}.led:focus-visible{outline:2px solid var(--led)}
.loop{font-size:15px;margin:22px 0 2px}.part{color:var(--muted);font-size:12px;letter-spacing:.08em;
text-transform:uppercase;margin:14px 0 0}
.shade{border-top:1px solid var(--line);margin-top:4px}
.pull{display:block;width:100%;padding:5px 0 3px;border:0;background:none;color:var(--led);opacity:.7;
font:13px/1.4 ui-monospace,monospace;text-align:left;white-space:nowrap;overflow:hidden;
text-overflow:ellipsis;cursor:pointer}
.pull::before{content:'\\25be';display:inline-block;margin-right:.6em;transition:transform .3s}
.pull:hover,.shade.open .pull{opacity:1}.shade.open .pull::before{transform:rotate(180deg)}
.pull:focus-visible{outline:1px solid var(--led)}
.panel{display:grid;grid-template-rows:0fr;transition:grid-template-rows .4s ease}
.shade.open .panel{grid-template-rows:1fr}.panel>div{overflow:hidden;min-height:0;padding-top:2px}
@media(prefers-reduced-motion:reduce){.panel,.pull::before{transition:none}}
""" + ':root{%s}' % ';'.join('--%s:%s' % pair for pair in scene.SIGN_COLOURS) + scene.SVG_LAMPS + scene.SVG_SHADES

# Each message's text as sent sits under a shade: a click pulls it down, and it rolls back
# up SHADE_UP_MS after the mouse leaves it.
SHADE_UP_MS = 2000
SILENT_RADIO_HTML_SCRIPT = """
[].forEach.call(document.querySelectorAll('.shade'), function(shade) {
  var pull = shade.querySelector('.pull'), timer = null;
  function roll(open) {
    shade.classList.toggle('open', open);
    pull.setAttribute('aria-expanded', open);
  }
  pull.addEventListener('click', function() { roll(!shade.classList.contains('open')); });
  shade.addEventListener('mouseenter', function() { clearTimeout(timer); });
  shade.addEventListener('mouseleave', function() {
    clearTimeout(timer);
    if (shade.classList.contains('open')) timer = setTimeout(function() { roll(false); }, %d);
  });
});
""" % SHADE_UP_MS

# Beside the page, for each message and channel an SVG that plays it (scene.py)
# and a player: a page of its own that runs the model of the firmware in the browser, draws the
# same SVG and plays it from its own clock, with what can be changed of the sign's program. Its
# script is this package's JS, inlined whole, with polar.js where a message has a keyed swing.
SILENT_RADIO_PLAYER_SCRIPTS = ('sign.js', 'scene.js', 'motion.js', 'player.js')
SILENT_RADIO_POLAR_SCRIPT = 'polar.js'
SILENT_RADIO_PLAYER_STYLE = """
:root{color-scheme:light dark;--bg:#fff;--fg:#1a1a1a;--muted:#666;--line:#ddd;--accent:#9a5c00;--lamp:.78;--cap:round}
@media(prefers-color-scheme:dark){:root{--bg:#161616;--fg:#e8e8e8;--muted:#999;--line:#333;--accent:#ffb000}}
body{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;margin:0 auto;max-width:1000px;
padding:16px}
h1{font-size:20px;margin:0}h2{font-size:15px;margin:14px 0 4px}.meta{color:var(--muted);font-size:13px;margin:2px 0 10px}
.stage{background:var(--sign);border:1px solid var(--line);border-radius:6px;padding:10px}
.stage svg{display:block;width:100%;height:auto}
.stage img{display:block;width:100%;height:auto}
.glow .stage svg{filter:drop-shadow(0 0 .4px var(--lamp-on))}
button,select,input{font:inherit;color:inherit}
button{padding:3px 10px;border:1px solid var(--line);border-radius:4px;background:transparent;cursor:pointer}
button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:2px solid var(--accent)}
.transport{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;margin:10px 0 4px}
.transport input[type=range]{flex:1 1 220px}.play{min-width:3em}
.time{font:13px ui-monospace,monospace;color:var(--muted);white-space:nowrap}
.status{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:center;font-size:13px;color:var(--muted)}
.playlist{max-height:15em;overflow:auto;margin:10px 0;padding-left:3em;font-size:13px}
.playlist button{border:0;padding:1px 4px;text-align:left}.playlist [aria-current]{color:var(--accent);font-weight:600}
.said p{margin:0 0 4px;white-space:pre-wrap;font:14px/1.5 ui-monospace,monospace}
.panels details{border-top:1px solid var(--line);padding:6px 0}summary{cursor:pointer;font-weight:600}
.grid,.settings{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:8px 14px;margin:8px 0}
.motion-view,.motion-chart{display:block;width:100%;height:auto;background:var(--sign);border:1px solid var(--line);border-radius:6px;margin:8px 0}.motion-view{touch-action:none;cursor:grab}
fieldset{border:1px solid var(--line);border-radius:4px;margin:0;padding:4px 8px 8px}
fieldset label{display:inline-block;margin:2px 10px 2px 0}legend{font-size:13px;color:var(--muted)}
input[type=number]{width:6.5em}.hex{font-family:ui-monospace,monospace}
.hint,.downloads,.note{color:var(--muted);font-size:13px}[aria-invalid=true]{outline:2px solid #d33}
.actions button{margin-right:8px}
.fallback .transport,.fallback .status,.fallback .panels,.fallback .playlist{display:none}
""" + ':root{%s}' % ';'.join('--%s:%s' % pair for pair in scene.SIGN_COLOURS) + scene.SVG_LAMPS + scene.SVG_SHADES
SILENT_RADIO_PLAYER_FALLBACK = """
function srFallback() {
  var root = document.getElementById('player'), note = document.createElement('p');
  root.classList.add('fallback');
  note.className = 'note';
  note.textContent = 'This browser could not run the player here, so the SVG plays on its own.';
  root.insertBefore(note, root.querySelector('.stage'));
}
"""

def _player_script(polar=False):
    """ The players' JS, with polar.js if `polar`, or None where this package doesn't have it """
    names = SILENT_RADIO_PLAYER_SCRIPTS
    if polar:
        names = names[:-1] + (SILENT_RADIO_POLAR_SCRIPT,) + names[-1:]
    try:
        return '\n'.join(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), name)).read() for name in names)
    except OSError:
        return None

def _player_page(script, title, meta, entries, overview, svg, svg_src):
    """ A player's page: `entries` the messages it plays in turn, as its data holds them, and
    `svg` the SVG of one to show until the player plays it, or None to show the SVG at `svg_src` """
    data = json.dumps({'tables': sign_tables(), 'params': sign_parameters(), 'messages': entries},
                      separators=(',', ':')).replace('</', '<\\/')
    said = ''.join('<div class="said" data-message="%d"%s>%s</div>' % (
        n, ' hidden' if n else '', ''.join('<p>%s</p>' % html.escape(text) for text in entry['text']))
        for n, entry in enumerate(entries))
    alt = ' / '.join(entries[0]['text']) if len(entries) == 1 else 'the channel as a sign on it played the tape'
    downloads = 'Download <a href="%s" download>the SVG</a>, drawn by the model of the firmware' % svg_src
    downloads += ' &middot; <a href="%s">every message</a>' % overview if overview else ''
    shown = svg or '<img src="%s" width="%d" height="%d" alt="%s">' % (svg_src, SIGN_WIDTH * 8, 15 * 8, html.escape(alt))
    return ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"><title>%s</title>'
            '<style>%s</style></head><body><main id="player" class="player"><header><h1>%s</h1>'
            '<p class="meta">%s</p></header><div class="stage">%s</div><section class="sent"><h2>As sent</h2>%s'
            '</section><p class="downloads">%s</p></main>'
            '<script>%s</script><script type="application/json" id="sign-data">%s</script>'
            '<script>%s\nSilentRadioPlayer.boot();</script><script>if (!window.SR_OK) srFallback();</script>'
            '</body></html>' % (html.escape(title), SILENT_RADIO_PLAYER_STYLE, html.escape(title), meta, shown, said,
                                downloads, SILENT_RADIO_PLAYER_FALLBACK, data, script)).encode()

# What a story's SVG holds of it, to play it again from
SILENT_RADIO_SVG_FORMAT = 'silentradio/1'

def _svg_meta(message, levels):
    meta = {'format': SILENT_RADIO_SVG_FORMAT, 'record': '%04x' % message[1], 'when': message[0],
            'text': [value for item, value in message[4] if item == 'text'], 'payload': message[7].hex()}
    if levels is not None:
        meta['levels'] = '%02x' % levels
    return meta

def _svg(messages, channel, levels):
    """ The SVG of a story, or of a channel's stories played in turn, on a sign `levels` sets """
    params = sign_parameters()
    if channel is None:
        return scene.svg(messages[0][11], dict(_svg_meta(messages[0], levels), params=params), params)
    meta = {'format': SILENT_RADIO_SVG_FORMAT, 'channel': channel.hex(' '),
            'text': ['Silent Radio %s, %d stories' % (channel.hex(' '), len(messages))],
            'messages': [_svg_meta(message, levels) for message in messages], 'params': params}
    if levels is not None:
        meta['levels'] = '%02x' % levels
    return scene.svg(scene.sequence([message[11] for message in messages]), meta, params)

def _swings(messages, prefix='sr_'):
    """ The keyed swings of `messages`, played in turn, as layers of their SVG """
    out, at = [], 0
    for message in messages:
        vectors = polar_vectors(message[7])
        if vectors:
            out.append(swing_layer(vectors, SIGN_TICK_MS, at, prefix))
        at += message[11]['end']
    return ''.join(out)

def _drawing(job):
    """ `(svg, swings)`: the SVG of a story or of a channel `(messages, channel, levels)`, lit as
    its channel's `01 02` byte `levels` sets, and its keyed swings as `_swings` gives them """
    return _svg(*job), _swings(job[0])

def _with_swings(drawing):
    """ A drawing's SVG, its swings in it """
    svg, swings = drawing
    return (svg[:-len('</svg>')] + swings + '</svg>').encode()

def _drawings(jobs):
    """ Each job's `_drawing`, in parallel """
    with Pool() as pool:
        return pool.map(_drawing, jobs, chunksize=1)

def _player_entry(message, mark, levels, polar=True):
    """ What a player's data holds of a message, on a sign its channel's `01 02` byte `levels`
    sets, with its keyed swing if `polar` """
    when, number, _, copies, items, program, _, received, _, _, _, _ = message
    return {'record': '%04x' % number, 'when': when, 'copies': copies,
            'mark': '%s record %04x' % mark if mark else None,
            'text': [value for item, value in items if item == 'text'], 'payload': received.hex(),
            'ticks': round(sum(step[0] for step in program) / SIGN_TICK_MS),
            'levels': None if levels is None else '%02x' % levels, 'polar': polar_case(received) if polar else None}

def _seconds(program):
    return sum(step[0] for step in program) / 1000

def write_silent_radio_html(output_filename, messages, marks=(), levels=None):
    """ Every story the carousel sent, each channel's in the order a sign on it played them -
    its loop, after its loop `marks` `(index, address, part, when)` - and beside the page an SVG
    and a player for each channel and story, named for its address or record. The page plays them
    live, and shows the SVGs, which play themselves, to a browser that can't. Each
    channel's signs light their colours at the levels its `01 02` byte in `levels`, by the
    channel's two groups, sets. """
    out_func, f = get_output_function('silentradio.html', output_filename)
    levels = levels or {}
    # a message's by its channel's, its address mended as the page places it
    levels_of = lambda address: levels.get(bytes(home(address)[1:3]))
    written = set()
    script = _player_script() if output_filename is not None else None
    overview = html.escape(os.path.basename('%s.silentradio.html' % output_filename)) if output_filename else None
    entries, lives = {}, []
    shades = [0]

    def beside(name, extension, data):
        # a page sent to stdout has nowhere beside it for them
        if output_filename is None:
            return None
        path = '%s.silentradio.%s.%s' % (output_filename, name, extension)
        with open(path, 'wb') as out:
            out.write(data)
        return html.escape(os.path.basename(path))

    def unique(name):
        name += '.%d' % len(written) if name in written else ''
        written.add(name)
        return name

    def live(stories):
        # what a sign on the page plays live, the stories in turn
        lives.append([entries.setdefault(id(message), (len(entries), _player_entry(message, mark, levels_of(
            message[2]))))[0] for message, mark in stories])
        return len(lives) - 1

    def story(address, message, mark, sent_at=None):
        when, number, _, copies, items, program, _, _, drawn, sendings, counts, _ = message
        lost, filled, restored, patterned, checks, settled = counts
        when = sent_at or when
        shown, player = '', None
        if program:
            name = unique('%04x' % number)
            drawing = drawings.get(id(message))
            vector = beside(name, 'svg', _with_swings(drawing)) if drawing else None
            if vector and script:
                entry = _player_entry(message, mark, levels_of(address))
                player = beside(name, 'html', _player_page(
                    _player_script(True) if entry['polar'] else script, 'Silent Radio \u00b7 record %04x' % number,
                    '%s &middot; %s &middot; %d cop%s' % (address.hex(' '), when, copies, 'y' if copies == 1 else 'ies'),
                    [entry], overview, drawing[0], vector))
            if vector:
                shown = ('<div class="led" data-live="%d"><img src="%s" width="%d" height="%d" alt="the story '
                         'as the sign plays it"></div>' % (live([(message, mark)]), vector, SIGN_WIDTH * 8, 15 * 8))
        out_func('<div class="meta">%s &middot; record %04x%s &middot; %d cop%s%s%s%s%s%s%s%s</div>' % (
            when, number, ', sent again as %s' % ', '.join('%04x' % n for _, _, n, _ in sendings[1:])
            if len(sendings) > 1 else '', copies, 'y' if copies == 1 else 'ies',
            ' &middot; %d bytes lost%s' % (lost, ', %d filled in from the rest of the capture' % filled if filled
                                          else '') if lost else '',
            ' &middot; %d graphic column%s mended' % (restored, '' if restored == 1 else 's') if restored else '',
            ' &middot; %d byte%s put back from its own pattern' % (patterned, '' if patterned == 1 else 's')
            if patterned else '',
            ' &middot; its checksum checks%s' % (', %d byte%s set by it' % (settled, '' if settled == 1 else 's')
                                                 if settled else '') if checks else ' &middot; fails its checksum',
            ' &middot; %s record %04x' % mark if mark else '',
            ' &middot; %.0f s on the sign' % _seconds(program) if program else '',
            ' &middot; <a href="%s">live player</a>' % player if player else ''))
        texts = [html.escape(value) for item, value in items if item == 'text']
        if texts:
            shades[0] += 1
            shown += ('<div class="shade"><button class="pull" aria-expanded="false" '
                      'aria-controls="shade%d">%s</button><div class="panel" id="shade%d"><div>%s'
                      '</div></div></div>' % (shades[0], ' &middot; '.join(texts), shades[0],
                                              ''.join('<p>%s</p>' % text for text in texts)))
        out_func('<div class="sign">%s</div>' % shown)

    out_func('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
             '<meta name="viewport" content="width=device-width, initial-scale=1">'
             '<title>Silent Radio</title><style>%s</style><noscript><style>.panel{grid-template-rows:1fr}'
             '.pull::before{content:none}</style></noscript></head><body>' % SILENT_RADIO_HTML_STYLE)
    channels = _channels(messages)
    lineages = [_lineage(kept) for _, kept in channels]
    homes = [channel for channel, _ in channels]
    home = lambda address: address if address in homes else _nearest(address, homes)
    loops = [_loop(kept, [(index, part, when) for index, address, part, when in marks if home(address) == channel])
             for channel, kept in channels]
    out_func('<h1>Silent Radio</h1><p>%d stories on %d channels, as the sign would have shown them.</p>'
             % (len(messages), len(channels)))
    # what each channel's sign plays: its loop, or where it has none each new story and update
    # in turn
    marks_of = [dict(zip(map(id, kept), lineage)) for (_, kept), lineage in zip(channels, lineages)]
    playings = []
    for (address, kept), lineage, loop, mark in zip(channels, lineages, loops, marks_of):
        stories = next((section for heading, section in loop if heading and heading.startswith('The loop')), None)
        playing = [(message, mark[id(message)]) for message, _, _ in stories] if stories else [
            (message, m) for message, m in zip(kept, lineage) if m is None or m[0] == 'an update of']
        playings.append((stories, [(message, m) for message, m in playing if message[5]]))
    # the files beside the page, drawn all at once: each story's and each channel's
    drawings = {}
    if output_filename is not None:
        drawable = [message for message in messages if message[5]]
        channel_plays = [(address, [message for message, _ in plays])
                         for (address, _), (_, plays) in zip(channels, playings) if plays]
        jobs = [([message], None, levels_of(message[2])) for message in drawable] + [
            (plays, address, levels_of(address)) for address, plays in channel_plays]
        drawings = dict(zip([id(message) for message in drawable] + [address for address, _ in channel_plays],
                            _drawings(jobs)))
    # every channel as a sign on it played the tape, and under them a tab for each channel's
    # stories
    for (address, kept), (stories, playing) in zip(channels, playings):
        name = unique(address.hex())
        drawing = drawings.get(address)
        vector = beside(name, 'svg', _with_swings(drawing)) if drawing else None
        player = None
        if vector and script:
            played = [_player_entry(message, m, levels_of(address)) for message, m in playing]
            player = beside(name, 'html', _player_page(
                _player_script(True) if any(entry['polar'] for entry in played) else script,
                'Silent Radio \u00b7 %s' % address.hex(' '),
                '%d stories as a sign on this channel played them%s' % (
                    len(playing), ', its loop in order' if stories else ''),
                played, overview, None, vector))
        out_func('<div class="meta">%s &middot; %d stories%s%s</div>%s' % (
            address.hex(' '), len(kept), ' &middot; <a href="%s">%s</a>' % (vector, vector) if vector else '',
            ' &middot; <a href="%s">live player</a>' % player if player else '',
            '<div class="led" data-live="%d"><img src="%s" width="%d" height="%d" alt="the channel as a sign '
            'on it played the tape"></div>' % (live(playing), vector, SIGN_WIDTH * 8, 15 * 8) if vector else ''))
    out_func('<style>.channel{display:none}%s</style>' % ''.join(
        '#t%d:checked~#c%d{display:block}' % (n, n) for n in range(len(channels))))
    for n, (address, kept) in enumerate(channels):
        out_func('<input class="tab" type="radio" name="channel" id="t%d"%s><label for="t%d">%s &middot; '
                 '%d stories</label>' % (n, ' checked' if n == 0 else '', n, address.hex(' '), len(kept)))
    for n, ((address, kept), lineage, loop, mark) in enumerate(zip(channels, lineages, loops, marks_of)):
        out_func('<section class="channel" id="c%d"><p>%d stories, sent %d times, %d of them updates.</p>' % (
            n, len(kept), sum(len(message[9]) for message in kept),
            sum(1 for m in lineage if m and m[0] == 'an update of')))
        for heading, section in loop:
            if heading:
                out_func('<h2 class="loop">%s</h2>' % html.escape(heading))
            previous = None
            for message, part, sent_at in section:
                if part is not None and part != previous:
                    out_func('<div class="part">part %d</div>' % part)
                previous = part
                story(address, message, mark[id(message)], sent_at)
        out_func('</section>')
    out_func('<script>%s</script>' % SILENT_RADIO_HTML_SCRIPT)
    if script and lives:
        stories = [entry for _, entry in entries.values()]
        data = {'tables': sign_tables(), 'params': sign_parameters(), 'stories': stories, 'lives': lives}
        out_func('<script type="application/json" id="sign-data">%s</script><script>%s\n'
                 'SilentRadioPlayer.overview();</script>' % (json.dumps(data, separators=(',', ':')).replace('</', '<\\/'),
                                                            _player_script(True) if any(entry['polar'] for entry in stories)
                                                            else script))
    out_func('</body></html>')
    if f is not None:
        f.close()
