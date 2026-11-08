"""Serve an existing rendered document against owned real state endpoints."""
import json
import re

import kardenwort_desk as desk
from kardenwort_state_http import config_snapshot


def island(html, name):
    return json.loads(re.search(r'<script[^>]*id="'+name+r'"[^>]*>(.*?)</script>', html, re.S).group(1))


def replace_island(html, name, value):
    return re.sub(r'(<script[^>]*id="'+name+r'"[^>]*>).*?(</script>)',
        lambda match: match[1]+desk.serialize_json_island(value)+match[2], html, flags=re.S)


def serve_document(page, world, html, stored=None):
    url, server, path = world
    boot = island(html, 'kardenwort-bootstrap')
    session = boot.get('session_data')
    if session is None:
        session = dict(boot, headers=stored['headers'], data_rows=stored['data_rows'])
    boot['session_data'] = session
    zid = session.get('session_zid') or session['zid']
    adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
    server.arbiter.storage_adapter = adapter
    adapter.save_session(session_zid=zid, source_language=session.get('language', 'de'),
        target_language=session.get('target_lang', 'en'), source_raw_text=session.get('source_text', ''),
        text_mode='multi', headers=session['headers'], data_rows=session['data_rows'])
    session.update(server_url=url, api_token=server.api_key, server_enabled=True)
    boot.update(server_url=url, server_enabled=True)
    boot['ui_config'].update(config_snapshot(server, {})['ui'])
    html = replace_island(html, 'session-data', session)
    html = replace_island(html, 'ui-config', boot['ui_config'])
    html = replace_island(html, 'kardenwort-bootstrap', boot)
    page.context.set_extra_http_headers({'X-API-Token': server.api_key})
    def document(route):
        boot['ui_config'].update(config_snapshot(server, {})['ui'])
        current = replace_island(html, 'ui-config', boot['ui_config'])
        current = replace_island(current, 'kardenwort-bootstrap', boot)
        route.fulfill(content_type='text/html', body=current)
    page.route(url+'/', document)
    page.goto(url+'/')
    wait_state(page)
    return adapter


def wait_state(page):
    page.wait_for_function('window.__stateReady !== undefined')
    page.evaluate('window.__stateReady')
