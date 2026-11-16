"""Authenticated controller effects for preferences, imports, and client views."""
from kardenwort_client_views import ClientViewAdapter, write_view
from kardenwort_events import EventDispatcher
from kardenwort_preference_contract import parse_preference_command
from kardenwort_state_runtime import STATE_LOCK, preference_store, project_preferences


STATE_ROUTES = frozenset({'/api/v1/config', '/api/v1/client-view', '/api/v1/browser-state/import', '/api/v1/set-language'})


def store_for(server):
    return preference_store(server.config, server.resolved_paths)


def config_snapshot(server, body):
    return project_snapshot(server, store_for(server).snapshot())


def project_snapshot(server, snapshot):
    with STATE_LOCK:
        projected = project_preferences(server.config, snapshot)
        static_ui = dict(getattr(server, 'ui_cfg', {}) or {})
        static_ui.update(projected['values'])
        static_ui.update(state_authority='sqlite', preference_revisions=dict(projected['revisions']))
        server.ui_cfg = static_ui
        return dict(ok=True, ui=dict(static_ui), default_language=projected['values']['default_language'],
                    preferences=projected, state_authority='sqlite')


def update_config(server, body):
    command = parse_preference_command(body)
    result = store_for(server).set(command.key, command.selected, body.get('expected_revision'))
    snapshot = project_snapshot(server, result['snapshot'])['preferences']
    EventDispatcher.get_instance().publish_global(dict(type='preferences_updated', preferences=snapshot))
    return dict(ok=True, section=command.section, key=command.key, value=command.value,
                committed_value=result['value'], revision=result['revision'])


def client_view(server, body):
    adapter = ClientViewAdapter(store_for(server).db)
    zid, client = body.get('session_zid'), body.get('client_id')
    if body.get('dispose') is True:
        adapter.dispose(zid, client)
        return dict(ok=True, disposed=True)
    if 'active_card' in body:
        result = adapter.set(zid, client, body['active_card'], body.get('active_tokens', []),
                             body.get('expected_revision'), include_import=True)
    else:
        result = adapter.get(zid, client, include_import=True)
    return dict(ok=True, **result)


def import_browser(server, body):
    store = store_for(server)
    zid, client = body.get('session_zid'), body.get('client_id')
    view = body.get('view')
    callback = None
    if view is not None:
        if not isinstance(view, dict) or not {'active_card', 'active_tokens'} <= view.keys():
            raise ValueError('Invalid browser view')
        callback = lambda connection: write_view(connection, zid, client, view['active_card'], view['active_tokens'], view.get('expected_revision'))
    result = store.import_browser(client, body.get('preferences', {}), body.get('revisions', {}), callback)
    snapshot = dict(values=result['values'], revisions=result['revisions'])
    projected = project_snapshot(server, snapshot)['preferences']
    EventDispatcher.get_instance().publish_global(dict(type='preferences_updated', preferences=projected))
    return dict(ok=True, **result)


def dispatch_state(server, path, method, body):
    handlers = {('/api/v1/config', 'GET'): config_snapshot, ('/api/v1/config', 'POST'): update_config,
        ('/api/v1/client-view', 'POST'): client_view, ('/api/v1/browser-state/import', 'POST'): import_browser,
        ('/api/v1/set-language', 'POST'): language_command}
    handler = handlers.get((path, method))
    if handler is None:
        raise ValueError('Unsupported state request method')
    return handler(server, body)


def language_command(server, body):
    return set_language(server, body.get('language') or body.get('lang'))


def set_language(server, language):
    result = update_config(server, dict(section='settings', key='default_language', value=language))
    return dict(ok=True, language=result['committed_value'], persisted=True, revision=result['revision'])
