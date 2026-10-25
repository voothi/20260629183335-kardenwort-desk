"""Phase-1 release contracts. Transport ordering is controlled; domain code is real.

These deliberately fail against the audited release until phases 2 and 3 repair
the boundaries. Chromium execution is not physical MSHTML verification.
"""
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

import pytest
from playwright.sync_api import TimeoutError as BrowserTimeout

import kardenwort_desk as desk
from kardenwort_events import EventDispatcher
from tests.test_controller_integration import running_controller  # noqa: F401


ZID = '20261023221555'
OTHER_ZID = '20261025104756'
TOKEN = 'test-controller-api-key'
HEADERS = ['Quotation', 'WordSource', 'WordDestination', 'SentenceSourceIndex',
           'SentenceSource', 'DeskSelected', 'TokenOrder']


def post(url, path, body, token=TOKEN):
    request = urllib.request.Request(url + path, data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json', 'X-API-Token': token})
    try:
        response = urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)


def data(payload):
    return payload.get('data', payload)


def seed_session(server, adapter, selected=False, zid=ZID):
    rows = [['Haus', 'Haus', 'house', '1', 'Das Haus.', '1' if selected else '0', '0'],
            ['Baum', 'Baum', 'tree', '2', 'Der Baum.', '0', '1']]
    path = Path(server.resolved_paths['results_dir']) / (zid + '-boundary.de.tsv')
    adapter.save_session(session_zid=zid, slug='boundary', source_language='de', target_language='en',
                         text_mode='single', source_raw_text='Das Haus. Der Baum.',
                         headers=HEADERS, data_rows=rows, working_tsv_path=path)
    return path


def use_backend(server, backend):
    server.config.set('storage', 'backend', backend)
    server.resolved_paths['storage_backend'] = backend
    server.resolved_paths['generated_results_dir'] = server.resolved_paths['results_dir']
    if backend == 'tsv':
        server.arbiter.storage_adapter = desk.TsvStorageAdapter(server.config, server.resolved_paths)
    return server.arbiter.storage_adapter


def selection_delta(row, selected):
    return dict(row_id=row, token_order=row, sentence_idx=row + 1,
                column='DeskSelected', value='1' if selected else '0')


def save_selection(url, row, selected, zid=ZID):
    status, response = post(url, '/session/save', dict(session_zid=zid, language='de',
                                                     deltas=[selection_delta(row, selected)]))
    assert status == 200, response
    return response


def restored_membership(adapter, zid=ZID):
    restored = adapter.restore_session(zid)
    headers = restored['headers']
    return {row[headers.index('WordSource')].strip().lower(): row[headers.index('DeskSelected')] == '1'
            for row in restored['data_rows']}


def snapshot(directory):
    # Diagnostics are allowed; data, markers, preferences, directories and
    # database/WAL/SHM files are the read-only contract under inspection.
    return {p.relative_to(directory).as_posix(): (p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in directory.rglob('*') if p.is_file() and p.suffix != '.log'} | {
                p.relative_to(directory).as_posix() + '/': None
                for p in directory.rglob('*') if p.is_dir()}


def wait_for_held(page, held, count):
    deadline = time.monotonic() + 5
    while len(held) < count and time.monotonic() < deadline:
        page.evaluate('Boolean(window.getDeltas)')
    assert len(held) == count


def wait_for_membership(page, count):
    try:
        page.wait_for_function('count => __receivedMembership >= count', arg=count)
    except BrowserTimeout:
        raise AssertionError(page.evaluate("({received:__receivedMembership, wire:__wireEvents, zid:SSEBridge.getActiveZid(), url:__testStream.source.url})")) from None
    assert page.evaluate('__receivedMembership') == count, page.evaluate('__wireEvents')


# Real EventSource reads the controller stream. Only delivery to the production
# onmessage callback is gated, so event/ack order does not depend on machine speed.
GATED_SSE = """<script>
window.__membershipEvents = [];
window.__wireEvents = [];
window.__receivedMembership = 0;
window.__saveRequests = 0;
const RealEventSource = window.EventSource;
window.EventSource = class {
    constructor(url) {
        if (window.__testStream) window.__testStream.close();
        window.__testStream = this;
        this.source = new RealEventSource(url);
        this.source.onopen = e => { if (this.onopen) this.onopen(e); };
        this.source.onerror = e => { if (this.onerror) this.onerror(e); };
        this.source.onmessage = e => {
            if (this !== window.__testStream) return;
            window.__wireEvents.push(e.data);
            if (JSON.parse(e.data).shared_selection) {
                window.__receivedMembership++;
                window.__membershipEvents.push(e.data);
            } else if (this.onmessage) this.onmessage(e);
        };
    }
    close() { this.source.close(); }
};
window.__deliverMembership = function() {
    while (__membershipEvents.length) __testStream.onmessage({data:__membershipEvents.shift()});
};
</script>"""


def boundary_html(tmp_path, selected=False):
    config, paths, _, _ = desk.load_config()
    config.set('storage', 'backend', 'tsv')
    config.set('storage', 'sqlite_db_path', str(tmp_path / 'render.db'))
    config.set('server', 'api_key', TOKEN)
    config.set('rendering', 'filter_selected_only', 'false')
    config.set('rendering', 'highlight_same_lemma', 'false')
    config.set('rendering', 'auto_save_on_edit', 'false')
    config.set('sentences_mode', 'enabled', 'false')
    paths.update(storage_backend='tsv', sqlite_db_path=tmp_path / 'render.db',
                 results_dir=tmp_path, generated_results_dir=tmp_path)
    tsv = tmp_path / (ZID + '-browser.de.tsv')
    tsv.write_text('\t'.join(HEADERS) + '\n' +
                   '\t'.join(['Haus', 'Haus', 'house', '1', 'Das Haus.', '1' if selected else '0', '0']) + '\n' +
                   '\t'.join(['Baum', 'Baum', 'tree', '2', 'Der Baum.', '0', '1']) + '\n', encoding='utf-8')
    return desk.run_render_flow(text='Das Haus. Der Baum.', language='de', zid=ZID,
                                text_mode='single', config=config, resolved_paths=paths,
                                tsv_path=str(tsv), spawn_children=False)


def open_browser(page, tmp_path, url, selected=False):
    page.set_default_timeout(5000)
    html = boundary_html(tmp_path, selected)
    page.route(url + '/', lambda route: route.fulfill(content_type='text/html', body=html.replace('<head>', '<head>' + GATED_SSE)))
    page.set_extra_http_headers({'X-API-Token': TOKEN})
    page.goto(url + '/')
    page.wait_for_function("typeof toggleRowSelection === 'function'")
    page.evaluate('args => { SSEBridge.disconnect(); SSEBridge.connect(args.zid, args.url); }',
                  dict(zid=ZID, url=url))
    page.wait_for_function("document.getElementById('sse-status-indicator').dataset.status === 'connected'")


@pytest.mark.parametrize('remote_selected', [True, False], ids=['remote-select', 'remote-deselect'])
@pytest.mark.parametrize('event_first', [True, False], ids=['event-before-ack', 'ack-before-event'])
@pytest.mark.parametrize('reversal', [False, True], ids=['no-reversal', 'new-local-reversal'])
def test_disjoint_remote_membership_converges_after_held_save(page, tmp_path, running_controller,
                                                              remote_selected, event_first, reversal):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    seed_session(server, adapter, selected=not remote_selected)
    open_browser(page, tmp_path, url, selected=not remote_selected)
    held = []
    page.route('**/session/save', lambda route: held.append(route))
    page.evaluate('toggleRowSelection(1, true); void onSaveClick(true)')
    page.wait_for_function("document.getElementById('kw-btn-save').disabled")
    wait_for_held(page, held, 1)
    save_selection(url, 0, remote_selected)
    wait_for_membership(page, 1)
    if reversal:
        page.evaluate('toggleRowSelection(1, false)')
    response = held[0].fetch()
    assert response.ok, response.text()
    wait_for_membership(page, 2)
    if event_first:
        page.evaluate('__deliverMembership()')
    held[0].fulfill(response=response)
    page.wait_for_function("document.getElementById('kw-btn-save').textContent.indexOf('Saving') < 0")
    if not event_first:
        page.evaluate('__deliverMembership()')
    if reversal:
        # Acknowledgment must expose the newer reversal rather than erase it.
        assert any(d['column'] == 'DeskSelected' and d['value'] == ''
                   for d in json.loads(page.evaluate('getDeltas()')))
        page.evaluate('void onSaveClick(true)')
        page.wait_for_function("document.getElementById('kw-btn-save').disabled")
        wait_for_held(page, held, 2)
        second = held[1].fetch()
        wait_for_membership(page, 3)
        page.evaluate('__deliverMembership()')
        held[1].fulfill(response=second)
    page.wait_for_function('!isDirty()')
    expected = {'haus': remote_selected, 'baum': not reversal}
    durable = restored_membership(adapter)
    visible = page.evaluate("({haus:!!AppState.selectedLemmas.haus, baum:!!AppState.selectedLemmas.baum})")
    assert durable == expected
    assert visible == durable, dict(durable=durable, visible=visible, received=page.evaluate('__receivedMembership'))
    # Advance the real debounce clock: no read/event-triggered autosave echo.
    page.clock.install()
    page.clock.run_for(1000)
    assert len(held) == (2 if reversal else 1)


def test_failed_save_retry_retains_local_and_remote_membership(page, tmp_path, running_controller):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    seed_session(server, adapter)
    open_browser(page, tmp_path, url)
    held = []
    page.route('**/session/save', lambda route: held.append(route))
    page.evaluate('toggleRowSelection(1, true); void onSaveClick(true)')
    page.wait_for_function("document.getElementById('kw-btn-save').disabled")
    wait_for_held(page, held, 1)
    save_selection(url, 0, True)
    wait_for_membership(page, 1)
    page.evaluate('__deliverMembership()')
    held[0].fulfill(status=503, content_type='application/json', body='{"ok":false,"error":"held transport unavailable"}')
    page.wait_for_function("document.querySelector('.kw-toast-error') !== null")
    assert page.evaluate('isDirty()')
    assert any(d['column'] == 'DeskSelected' and d['value'] == '1' for d in json.loads(page.evaluate('getDeltas()')))
    page.evaluate('void onSaveClick(true)')
    page.wait_for_function("document.getElementById('kw-btn-save').disabled")
    wait_for_held(page, held, 2)
    response = held[1].fetch()
    wait_for_membership(page, 2)
    page.evaluate('__deliverMembership()')
    held[1].fulfill(response=response)
    page.wait_for_function('!isDirty()')
    assert restored_membership(adapter) == {'haus': True, 'baum': True}
    assert page.evaluate('!!AppState.selectedLemmas.haus && !!AppState.selectedLemmas.baum')
    page.clock.install()
    page.clock.run_for(1000)
    assert len(held) == 2


@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
def test_authenticated_membership_roundtrip_is_read_only(running_controller, tmp_path, backend):
    url, server, _ = running_controller
    adapter = use_backend(server, backend)
    seed_session(server, adapter)
    seed_session(server, adapter, zid=OTHER_ZID)
    for selected in (True, False):
        save_selection(url, 0, selected)
        assert restored_membership(adapter) == {'haus': selected, 'baum': False}
        assert restored_membership(adapter, OTHER_ZID) == {'haus': False, 'baum': False}
        before = snapshot(tmp_path)
        for _ in range(2):
            status, payload = post(url, '/internal/selection', dict(session_zid=ZID, client_id=f'backend-{backend}-{selected}'))
            assert snapshot(tmp_path) == before, 'Membership query wrote a file or database sidecar'
            assert status == 200, payload
            assert data(payload)['shared_selection'] == {'haus': selected, 'baum': False}
            assert data(payload)['events'] == []


@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
@pytest.mark.parametrize('session', ['', 'not-a-session', '20990101000000'])
def test_invalid_or_unavailable_membership_is_structured_failure(running_controller, tmp_path, backend, session):
    url, server, _ = running_controller
    use_backend(server, backend)
    before = snapshot(tmp_path)
    status, payload = post(url, '/internal/selection', dict(session_zid=session))
    assert snapshot(tmp_path) == before
    assert 400 <= status < 500, payload
    assert payload['status'] == 'error'
    assert payload['error_code']
    assert 'shared_selection' not in data(payload)


@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
def test_membership_authentication_precedes_backend_read(running_controller, tmp_path, backend):
    url, server, _ = running_controller
    adapter = use_backend(server, backend)
    seed_session(server, adapter)
    before = snapshot(tmp_path)
    status, payload = post(url, '/internal/selection', dict(session_zid=ZID), token='invalid')
    assert status in (401, 403), payload
    assert snapshot(tmp_path) == before


def native_window(browser, html, url, host_identity):
    context = browser.new_context()
    page = context.new_page()
    page.clock.install()
    page.add_init_script('window.EventSource = undefined')
    requests = []
    page.on('console', lambda message: requests.append(json.loads(message.text[len('NATIVE_SELECTION:'):]))
            if message.text.startswith('NATIVE_SELECTION:') else None)
    page.route(url + '/native', lambda route: route.fulfill(body=html, content_type='text/html'))
    page.goto(url + '/native')
    # The actual native host currently supplies no argument. Supplying the
    # proposed explicit identity keeps this regression useful after the repair.
    # Queue the synchronous native transport requests, then send each through
    # the real authenticated controller endpoint in drain_native.
    page.evaluate("""() => {
        window.__bridgeCalls = [];
        window.ahkCall = (action, body) => {
            if (action === 'get_shared_selection') {
                __bridgeCalls.push(JSON.parse(body));
                console.log('NATIVE_SELECTION:' + body);
            }
            return JSON.stringify({ok:true,events:[],shared_selection:{}});
        };
    }""")
    page.evaluate('startSelectionBridge', host_identity)
    return context, page, requests


def drain_native(page, url, requests, ticks=1):
    if not page.is_closed():
        page.clock.run_for(200 * ticks)
    bodies = list(requests)
    requests.clear()
    results = []
    for body in bodies:
        status, payload = post(url, '/internal/selection', body)
        assert status == 200, payload
        results.append((body, data(payload)))
        if not page.is_closed() and page.evaluate("typeof receiveUpdate === 'function'"):
            page.evaluate("payload => { (payload.events || []).forEach(receiveUpdate); if (payload.shared_selection) applySharedSelection(payload.shared_selection); }", data(payload))
    return results


def test_independent_native_windows_do_not_starve_each_other(browser, tmp_path, running_controller):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    seed_session(server, adapter)
    html = boundary_html(tmp_path)
    windows = [native_window(browser, html, url, identity) for identity in ('window-A', 'window-B')]
    try:
        pages = [window[1] for window in windows]
        generations = [page.evaluate('__deskInstanceId') for page in pages]
        first = [drain_native(page, url, window[2])[0][0] for page, window in zip(pages, windows)]
        for stage in ('translated_text', 'finished'):
            server.arbiter.emit_event(ZID, {'stage': stage})
        received = [drain_native(page, url, window[2])[0][1]['events'] for page, window in zip(pages, windows)]
        observed = dict(generations=generations, identities=[body['client_id'] for body in first],
                        events=[[event['stage'] for event in events] for events in received])
        assert generations == [1, 1]
        assert observed['events'] == [['translated_text', 'finished']] * 2, observed
        assert len(set(observed['identities'])) == 2, observed
    finally:
        for context, _, _ in windows:
            context.close()


@pytest.mark.parametrize('lifecycle', ['stop', 'pagehide', 'remount', 'reload', 'close'])
def test_native_lifecycle_disposes_its_subscription(browser, tmp_path, running_controller, lifecycle):
    url, server, adapter = running_controller
    use_backend(server, 'sqlite')
    seed_session(server, adapter)
    context, page, requests = native_window(browser, boundary_html(tmp_path), url, 'lifecycle-window')
    try:
        body = drain_native(page, url, requests)[0][0]
        dispatcher = EventDispatcher.get_instance()
        key = (ZID, body['client_id'])
        assert key in dispatcher._polling_clients
        # Separate consumer/session queues must remain untouched by teardown.
        dispatcher.poll_events(ZID, 'independent-window')
        dispatcher.poll_events(OTHER_ZID, body['client_id'])
        if lifecycle == 'pagehide':
            page.evaluate("dispatchEvent(new Event('pagehide')); dispatchEvent(new Event('pagehide'))")
        elif lifecycle == 'remount':
            for _ in range(2):
                page.add_script_tag(content=(Path(desk.__file__).parent / 'web/js/app.js').read_text(encoding='utf-8'))
        elif lifecycle == 'reload':
            page.reload()
            page.reload()
        elif lifecycle == 'close':
            page.close(run_before_unload=True)
        else:
            page.evaluate('stopSelectionBridge(); stopSelectionBridge()')
        drain_native(page, url, requests)
        assert (ZID, 'independent-window') in dispatcher._polling_clients
        assert (OTHER_ZID, body['client_id']) in dispatcher._polling_clients
        assert key not in dispatcher._polling_clients, dict(lifecycle=lifecycle, requests=requests)
    finally:
        context.close()


def test_dispatcher_distinct_consumers_pending_close_reload_and_session_isolation():
    dispatcher = EventDispatcher()
    for session, client in ((ZID, 'A'), (ZID, 'B'), (OTHER_ZID, 'A')):
        assert dispatcher.poll_events(session, client) == []
    dispatcher.publish(ZID, {'stage': 'translated_text'})
    assert dispatcher.poll_events(ZID, 'A', accept=False) == []
    assert [e['stage'] for e in dispatcher.poll_events(ZID, 'B')] == ['translated_text']
    assert dispatcher.poll_events(OTHER_ZID, 'A') == []
    assert [e['stage'] for e in dispatcher.poll_events(ZID, 'A')] == ['translated_text']
    for _ in range(3):
        assert dispatcher.poll_events(ZID, 'A', close=True) == []
        assert dispatcher.poll_events(ZID, 'A', close=True) == []
        assert dispatcher.poll_events(ZID, 'A') == []
        dispatcher.publish(ZID, {'stage': 'finished'})
        assert [e['stage'] for e in dispatcher.poll_events(ZID, 'A')] == ['finished']
        assert [e['stage'] for e in dispatcher.poll_events(ZID, 'B')] == ['finished']
        assert dispatcher.poll_events(OTHER_ZID, 'A') == []
