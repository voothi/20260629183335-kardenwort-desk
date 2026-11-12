"""Browser component lifecycle contracts, with a controlled unmanaged media device."""
from pathlib import Path
import json

import pytest
from kardenwort_desk import get_bundled_css

from tests.audio_test_support import pcm
from tests.test_ui_interactions import extract_desk_js

ROOT = Path(__file__).resolve().parents[1]

DEVICE = '''() => {
    window.players = []; window.urls = []; window.revoked = []; window.states = []; window.errors = [];
    URL.createObjectURL = () => { let url = 'blob:owned-' + urls.length; urls.push(url); return url; };
    URL.revokeObjectURL = url => revoked.push(url);
    window.Audio = class extends EventTarget {
        constructor() { super(); this.listeners = 0; this.paused = false; players.push(this); }
        addEventListener(...args) { this.listeners++; super.addEventListener(...args); }
        removeEventListener(...args) { this.listeners--; super.removeEventListener(...args); }
        play() { return window.blocked ? Promise.reject(new Error('Autoplay blocked')) : Promise.resolve(); }
        pause() { this.paused = true; }
        removeAttribute(name) { delete this[name]; }
        load() {}
    };
    window.requests = [];
    window.fetch = (url, options) => new Promise((resolve, reject) => {
        requests.push({url, options, resolve, reject});
    });
}'''


@pytest.fixture
def audio_page(page):
    page.set_content('<button data-testid="play">Play</button><span data-testid="word">Word</span>')
    page.evaluate(DEVICE)
    page.add_script_tag(path=str(ROOT/'web/js/features/audio.js'))
    page.evaluate('''() => {
        window.player = new DeskAudioPlayback({url: () => '/api/v1/audio/synthesize', token: () => 'owned-token',
            onState: detail => states.push(detail), onError: message => errors.push(message)});
    }''')
    yield page
    page.evaluate('player.cancel()')
    assert page.evaluate('players.every(p => p.listeners === 0 && p.paused)')
    assert page.evaluate('urls.length === revoked.length')


def resolve_audio(page, index=0, kind='audio/wav', contents='valid'):
    page.evaluate('''([index, kind, contents]) => requests[index].resolve({ok: true,
        blob: () => Promise.resolve(new Blob([contents], {type: kind}))})''', [index,kind,contents])


def test_audio_waits_for_play_event_and_sequences_constituents(audio_page):
    page = audio_page
    assert page.evaluate('player.getState()') == 'idle'
    before = page.get_by_test_id('word').bounding_box()
    page.evaluate("player.play('bei ||| der', 'de')")
    assert page.evaluate('player.getState()') == 'loading'
    assert page.evaluate('requests.length') == 1
    resolve_audio(page)
    page.wait_for_function('players.length === 1')
    assert page.evaluate('player.getState()') == 'loading'
    page.evaluate("players[0].dispatchEvent(new Event('play'))")
    assert page.evaluate('player.getState()') == 'playing'
    page.evaluate("players[0].dispatchEvent(new Event('ended'))")
    assert page.evaluate('requests.length') == 2
    assert page.evaluate('JSON.parse(requests[0].options.body)') == {'text':'bei','language':'de'}
    assert page.evaluate('JSON.parse(requests[1].options.body)') == {'text':'der','language':'de'}
    assert page.evaluate("requests[0].options.headers['X-API-Token']") == 'owned-token'
    assert page.evaluate('requests[0].url') == '/api/v1/audio/synthesize'
    resolve_audio(page, 1)
    page.wait_for_function('players.length === 2')
    page.evaluate("players[1].dispatchEvent(new Event('play')); players[1].dispatchEvent(new Event('ended'))")
    assert page.evaluate('player.getState()') == 'finished'
    assert page.get_by_test_id('word').bounding_box() == before


def test_replacement_aborts_request_ignores_stale_completion_and_releases_media(audio_page):
    page = audio_page
    page.evaluate("player.play('old', 'en'); player.play('new', 'ru')")
    assert page.evaluate('requests[0].options.signal.aborted')
    resolve_audio(page,0)
    resolve_audio(page,1)
    page.wait_for_function('players.length === 1')
    page.evaluate("players[0].dispatchEvent(new Event('play')); player.play('latest','de')")
    assert page.evaluate('players[0].paused && players[0].listeners === 0')
    assert page.evaluate('revoked.length') == 1
    page.evaluate("players[0].dispatchEvent(new Event('error'))")
    assert page.evaluate('player.getState()') == 'loading'
    page.evaluate('player.cancel()')
    assert page.evaluate('requests[2].options.signal.aborted')
    assert page.evaluate('player.getState()') == 'cancelled'


@pytest.mark.parametrize('failure', ['synthesis','network','empty','mime','autoplay','device'])
def test_audio_errors_are_observable_and_retry_is_deliberate(audio_page, failure):
    page = audio_page
    page.evaluate("player.play('word', 'en')")
    if failure == 'synthesis':
        page.evaluate("requests[0].resolve({ok:false,status:500,json:()=>Promise.resolve({message:'Engine failed'})})")
    elif failure == 'network':
        page.evaluate("requests[0].reject(new Error('Network failed'))")
    elif failure in ('empty','mime'):
        resolve_audio(page, kind='text/plain' if failure=='mime' else 'audio/wav', contents='' if failure=='empty' else 'valid')
    else:
        page.evaluate('window.blocked = ' + ('true' if failure == 'autoplay' else 'false'))
        resolve_audio(page)
        page.wait_for_function('players.length === 1')
        if failure == 'device':
            page.evaluate("players[0].dispatchEvent(new Event('error'))")
    page.wait_for_function("player.getState() === 'failed'")
    assert page.evaluate('errors.length') == 1
    assert page.evaluate('requests.length') == 1
    page.evaluate('player.retry()')
    assert page.evaluate('requests.length') == 2
    assert page.evaluate('player.getState()') == 'loading'


@pytest.mark.parametrize('mime',['audio/wav','audio/mpeg'])
def test_actual_browser_decodes_returned_media_and_finishes(page,mime):
    # Real HTMLAudioElement and browser events; silent media is not a listening test.
    data = pcm() if mime == 'audio/wav' else (ROOT/'tests/fixtures/silence.mp3').read_bytes()
    page.route('**/api/v1/audio/synthesize', lambda route: route.fulfill(status=200,
        content_type=mime, body=data))
    page.route('http://audio.invalid/', lambda route: route.fulfill(content_type='text/html', body='<html></html>'))
    page.goto('http://audio.invalid/')
    page.set_content('<button data-testid="play">Play</button>')
    page.add_script_tag(path=str(ROOT/'web/js/features/audio.js'))
    page.evaluate('''() => {
        window.states=[];
        window.player=new DeskAudioPlayback({url:()=>'/api/v1/audio/synthesize',token:()=> 'owned',
            onState: event=>states.push(event.state),onError: message=>{throw Error(message)}});
        document.querySelector('button').onclick=()=>player.play('word','en');
    }''')
    page.get_by_test_id('play').click()
    page.wait_for_function("player.getState() === 'finished'")
    assert page.evaluate('states') == ['loading','playing','finished']


def test_client_timeout_owns_and_releases_its_request(audio_page):
    page = audio_page
    page.clock.install()
    page.evaluate("player.play('word','en')")
    page.clock.fast_forward(75000)
    assert page.evaluate('player.getState()') == 'failed'
    assert page.evaluate('requests[0].options.signal.aborted')
    assert page.evaluate('errors') == ['Audio request timed out']


def gesture_document():
    manifest = [{'text':'beim','is_word':True,'visual_idx':0,'lower_clean':'beim','row_ids':[76,74]}]
    return '''<div class="container"><div class="source-text" id="source-container">
        <span class="word" data-testid="token" data-word-idx="0" data-line-idx="0" data-lower-clean="beim">beim</span>
        </div><table id="lemma-table"><tbody>
        <tr data-row-id="0" data-all-row-ids="0,11,49,74"><td data-col="WordSource">der</td>
        <td data-col="WordSourceInflectedForm">am, im, beim, die</td><td data-col="WordDestination">the</td></tr>
        <tr data-row-id="76"><td data-col="WordSource">bei</td><td data-col="WordSourceInflectedForm">beim, bei</td>
        <td data-col="WordDestination">at</td></tr></tbody></table></div>
        <script id="session-lang" type="text/plain">de</script>
        <script id="session-target-lang" type="text/plain">en</script>
        <script id="token-map" type="application/json">''' + json.dumps(manifest) + '</script>'


def inflection_document():
    return '''<div class="container"><div class="source-text" id="source-container">
        <span class="word" data-testid="token" data-word-idx="0" data-line-idx="0" data-lower-clean="spielen">spielen</span>
        </div><table id="lemma-table"><tbody><tr data-row-id="0"><td data-col="WordSource">spielen</td>
        <td data-col="WordSourceInflectedForm">spiel, spielen</td><td data-col="WordDestination">play</td></tr></tbody></table></div>
        <script id="session-lang" type="text/plain">de</script><script id="session-target-lang" type="text/plain">en</script>
        <script id="token-map" type="application/json">[{"text":"spielen","is_word":true,"visual_idx":0,"lower_clean":"spielen","row_ids":[0]}]</script>'''


@pytest.mark.parametrize('button,mode,source,expected,language',[
    ('left','joined','lemma',['bei der'],'de'), ('left','separate','lemma',['bei','der'],'de'),
    ('left','joined','inflection',['spielen'],'de'),
    ('right','joined','lemma',['at the'],'en'),('right','separate','lemma',['at','the'],'en')])
def test_browser_gesture_resolution_mouseup_and_suppression(page,button,mode,source,expected,language):
    page.set_content(inflection_document() if source == 'inflection' else gesture_document())
    page.add_style_tag(content=get_bundled_css())
    page.evaluate(DEVICE)
    page.evaluate(extract_desk_js(lmb_play=True, rmb_play=True,lmb_source=source,
        lmb_chain_mode=mode,rmb_chain_mode=mode))
    token = page.get_by_test_id('token')
    geometry = token.bounding_box()
    token.hover()
    page.mouse.down(button=button)
    assert page.evaluate('requests.length') == 0
    page.mouse.up(button=button)
    page.wait_for_function('requests.length === 1')
    # RMB replaces visible text before media arrives. Assert audio lifecycle
    # geometry against that completed gesture, retaining the lexical flip.
    if button == 'right':
        geometry = token.bounding_box()
    for index, text in enumerate(expected):
        assert page.evaluate(f'JSON.parse(requests[{index}].options.body)') == {'text':text,'language':language}
        resolve_audio(page,index)
        page.wait_for_function(f'players.length === {index+1}')
        page.evaluate(f"players[{index}].dispatchEvent(new Event('play')); players[{index}].dispatchEvent(new Event('ended'))")
    assert page.evaluate('audioPlayback.getState()') == 'finished'
    assert token.bounding_box() == geometry
    # Repeating the same gesture deselects/unflips, so it must remain silent.
    token.click(button=button)
    assert page.evaluate('requests.length') == len(expected)
    page.evaluate("playAudio('new','de'); dispatchEvent(new Event('pagehide'))")
    assert page.evaluate('requests[requests.length-1].options.signal.aborted')
    assert page.evaluate('audioPlayback.getState()') == 'cancelled'
    assert page.evaluate('players.every(p => p.listeners === 0 && p.paused)')
    assert page.evaluate('urls.length === revoked.length')


@pytest.mark.parametrize('mode,expected', [('joined', 'at the'), ('separate', 'at')])
def test_rmb_contraction_engine_failure_then_unflip_stays_silent(page, mode, expected):
    page.set_content(gesture_document())
    page.add_style_tag(content=get_bundled_css())
    page.evaluate(DEVICE)
    page.evaluate(extract_desk_js(lmb_play=False, rmb_play=True, rmb_chain_mode=mode))
    token = page.get_by_test_id('token')
    token.click(button='right')
    page.wait_for_function('requests.length === 1')
    assert page.evaluate('JSON.parse(requests[0].options.body)') == {'text': expected, 'language': 'en'}
    geometry = token.bounding_box()
    page.evaluate("requests[0].resolve({ok:false,status:503,json:()=>Promise.resolve({message:'Owned engine unavailable'})})")
    page.wait_for_function("audioPlayback.getState() === 'failed'")
    assert page.evaluate('requests.length') == 1
    assert page.evaluate('players.length') == 0
    assert token.bounding_box() == geometry
    token.click(button='right')
    assert page.evaluate('requests.length') == 1
    assert 'at the' not in token.inner_text()
    page.evaluate("dispatchEvent(new Event('pagehide'))")
    assert page.evaluate('urls.length === revoked.length')


@pytest.mark.parametrize('area,range_mode,ctrl,chain,expected',[
    ('source','all',False,'joined',['Haus Baum']),
    ('source','all',False,'separate',['Haus','Baum']),
    ('source','none',False,'joined',[]),('source','none',True,'joined',['Haus Baum']),
    ('table','none',False,'joined',[]),('table','none',True,'separate',['Haus','Baum']),
    ('table','all',False,'joined',['Haus Baum'])])
def test_browser_drag_range_modes_and_ctrl_override(page,area,range_mode,ctrl,chain,expected):
    page.set_content('''<div class="container"><div class="source-text" id="source-container">
        <span class="word" data-testid="source0" data-word-idx="0" data-lower-clean="haus">Haus</span>
        <span class="word" data-testid="source1" data-word-idx="1" data-lower-clean="baum">Baum</span></div>
        <table id="lemma-table"><tbody><tr data-row-id="0" data-testid="table0"><td data-col="WordSource">Haus</td></tr>
        <tr data-row-id="1" data-testid="table1"><td data-col="WordSource">Baum</td></tr></tbody></table></div>
        <script id="session-lang" type="text/plain">de</script><script id="token-map" type="application/json">
        [{"text":"Haus","is_word":true,"visual_idx":0,"lower_clean":"haus","row_ids":[0]},
        {"text":"Baum","is_word":true,"visual_idx":1,"lower_clean":"baum","row_ids":[1]}]</script>''')
    page.evaluate(DEVICE)
    page.evaluate(extract_desk_js(lmb_play=True,lmb_chain_mode=chain,
        source_range_mode=range_mode,table_range_mode=range_mode))
    if ctrl:
        page.keyboard.down('Control')
    page.get_by_test_id(area+'0').hover()
    page.mouse.down()
    page.get_by_test_id(area+'1').hover()
    assert page.evaluate('requests.length') == 0
    page.mouse.up()
    if ctrl:
        page.keyboard.up('Control')
    assert page.evaluate('requests.length') == (1 if expected else 0)
    for index,text in enumerate(expected):
        assert page.evaluate(f'JSON.parse(requests[{index}].options.body)') == {'text':text,'language':'de'}
        resolve_audio(page,index)
        page.wait_for_function(f'players.length === {index+1}')
        page.evaluate(f"players[{index}].dispatchEvent(new Event('ended'))")
    if expected:
        assert page.evaluate('audioPlayback.getState()') == 'finished'
