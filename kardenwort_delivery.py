"""Explicit stage sinks and authenticated producer handoff."""
import json
from contextvars import ContextVar
from contextlib import contextmanager

_PRODUCER_INGRESS = ContextVar("producer_ingress", default=False)


@contextmanager
def controller_producer():
    token = _PRODUCER_INGRESS.set(True)
    try:
        yield
    finally:
        _PRODUCER_INGRESS.reset(token)

import urllib.request


def delivery_sinks(config, diskless=False):
    if diskless:
        return ('controller',)
    if config is None:
        return ('desktop_file',)
    if config.getboolean('server', 'diskless_updates', fallback=False):
        return ('controller',)
    backend = config.get('storage', 'backend', fallback='tsv')
    default = 'controller' if backend == 'sqlite' else 'desktop_file'
    mode = config.get('server', 'update_delivery', fallback=default)
    modes = {'controller': ('controller',), 'desktop_file': ('desktop_file',),
             'mixed': ('controller', 'desktop_file')}
    if mode not in modes:
        raise ValueError('Unknown update delivery mode')
    return modes[mode]


def deliver_stage(config, session_zid, payload, dispatcher):
    if dispatcher.controller_owned or not _PRODUCER_INGRESS.get():
        dispatcher.publish(session_zid, payload)
        return
    host = config.get('server', 'host', fallback='127.0.0.1')
    port = config.getint('server', 'controller_port', fallback=config.getint('server', 'port', fallback=18335))
    token = config.get('server', 'api_key', fallback='')
    request = urllib.request.Request(
        f'http://{host}:{port}/internal/stage', method='POST',
        data=json.dumps({'session_zid': session_zid, 'event': payload}).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'X-API-Token': token})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status != 200:
                raise RuntimeError('Controller rejected stage delivery')
    except Exception as exc:
        raise RuntimeError('Committed stage could not be delivered to controller') from exc
