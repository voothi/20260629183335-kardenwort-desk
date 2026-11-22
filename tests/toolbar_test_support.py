"""Real preference/view HTTP state alongside explicitly controlled component effects."""
import configparser
import json
import re
import pytest
import kardenwort_desk as desk


@pytest.fixture
def component_configuration(controller_world, monkeypatch):
    url, server = controller_world
    server.api_key = 'secret-test-token-42'
    server.config.set('server', 'api_key', server.api_key)
    server.goldendict['server_api_key'] = server.api_key
    loader = desk.load_config
    monkeypatch.setattr(desk, 'load_config', lambda path=None: configure_component_input(loader, server, path))
    return url, server


@pytest.fixture
def component_page(page, component_configuration):
    url, server = component_configuration
    page.set_extra_http_headers({'X-API-Token': server.api_key})
    page.evaluate(state_transport_script(url))
    yield page
    ready = page.evaluate("() => Promise.resolve(window.__stateReady).then(() => ({ready:true}), error => ({error:error.message}))")
    assert ready.get('ready'), ready


def state_transport_script(url):
    return '''(() => {
        const nativeFetch = window.fetch.bind(window);
        let componentFetch = nativeFetch;
        const statePaths = ['/api/v1/config','/api/v1/client-view','/api/v1/browser-state/import'];
        function fetchForBoundary(url, options) {
            const path = new URL(url, BACKEND).pathname;
            if (statePaths.includes(path)) return nativeFetch(new URL(path, BACKEND), options);
            return componentFetch(url, options);
        }
        Object.defineProperty(window, 'fetch', {configurable:true,
            get:() => fetchForBoundary, set:value => { componentFetch=value; }});
    })();'''.replace('BACKEND',json.dumps(url))


def configure_component_input(loader, server, path=None):
    if path is not None:
        return loader(path)
    config = configparser.ConfigParser(interpolation=None)
    config.read_dict({section:dict(server.config[section]) for section in server.config.sections()})
    config.read_dict({'audio':{'lmb_play':'false','rmb_play':'false'}})
    return config,dict(server.resolved_paths),dict(server.goldendict),{'enabled':False}


def with_translation_skeleton(html):
    return re.sub(r'(<div\b[^>]*\bid="translation-container"[^>]*>)',
        r'\1<span class="skeleton-loader" data-pending="true">Loading...</span>',html,count=1)


def controlled_event_stream_script():
    """Control only incoming transport bytes; execute the real authenticated SSE client."""
    return '''<script>
    window.__sseInstances = [];
    const previousFetch = window.fetch;
    window.fetch = function(url, options) {
        const pathname = new URL(url, 'http://fixture.invalid').pathname;
        if (pathname !== '/events' && pathname !== '/api/v1/events') {
            return previousFetch(url, options);
        }
        const connection = {closed:false, failed:false};
        const body = new ReadableStream({start(controller) {
            connection.send = payload => controller.enqueue(new TextEncoder().encode(
                'data: ' + JSON.stringify(payload) + '\\n\\n'));
            connection.fail = () => {
                connection.failed = true;
                controller.error(new Error('Connection refused'));
            };
            options.signal.addEventListener('abort', () => {
                connection.closed = true;
                if (!connection.failed) controller.close();
            }, {once:true});
        }});
        window.__sseInstances.push(connection);
        return Promise.resolve(new Response(body, {headers:{'Content-Type':'text/event-stream'}}));
    };
    </script>'''
