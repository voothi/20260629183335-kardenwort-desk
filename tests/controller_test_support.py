"""Real controller/storage lifetimes with an owned translation HTTP service."""
from contextlib import contextmanager
from pathlib import Path
import sys
import threading
import time

import pytest

import kardenwort_desk as desk
from kardenwort_controller import ControllerRequestHandler, ProcessSupervisor, SessionArbiter
from tests.browser_test_server import _BrowserTestHTTPServer, create_browser_test_server

TRANSLATOR = Path(__file__).resolve().parents[2] / '20241122093311-deep-translator'
if str(TRANSLATOR) not in sys.path:
    sys.path.insert(0, str(TRANSLATOR))
FORK = TRANSLATOR / '20260209094544-deep-translator'
if str(FORK) not in sys.path:
    sys.path.insert(0, str(FORK))
from translate_server import TranslationHTTPServer, TranslationRequestHandler


class OwnedTranslationServer(_BrowserTestHTTPServer, TranslationHTTPServer):
    """The real service initialization with the shared socket/worker owner."""


class DeterministicTranslationHandler(TranslationRequestHandler):
    """Replace unmanaged engines after real provider routing, never controller/storage."""

    def translate(self, provider, text, source, target):
        self.server.provider_calls.append((text, source, target))
        return '\n'.join(f'{provider}: {line}' for line in text.splitlines())

    def _translate_google(self, text, source, target, *args):
        return self.translate('google', text, source, target)

    def _translate_deepl(self, text, source, target, *args):
        return self.translate('deepl', text, source, target)

    def _translate_argos(self, text, source, target, *args):
        return self.translate('argos', text, source, target)


@contextmanager
def translation_service(handler, auto_failover=False):
    server = create_browser_test_server(handler, OwnedTranslationServer,
        enable_cache=False, auto_failover=auto_failover, google_delay=0)
    server.provider_calls = []
    try:
        server.start()
        yield server
    finally:
        server.server_close()


def owned_configuration(owner, translation_url, backend):
    root = Path(desk.__file__).resolve().parent
    config, paths, goldendict, _ = desk.load_config(root / 'config.ini')
    results = owner / 'results'
    results.mkdir()
    database = owner / 'controller.db'
    config.read_dict({
        'storage': {'backend': backend, 'sqlite_db_path': str(database)},
        'project_structure': {'generated_results_dir': str(results)},
        'services': {'translation_server_url': translation_url, 'spacy_server_url': '',
                     'intellifiller_server_url': ''},
        'pipeline': {'text_provider_chain': 'google', 'lemma_provider_chain': 'google',
                     'text_base_provider': 'google', 'lemma_base_provider': 'google',
                     'text_reprocess_provider': 'google', 'failover_strategy': 'strict',
                     'fast_connectivity_check_ips': '', 'progressive_text_translation': 'false'},
        'timeouts': {'translation_timeout': '15'},
        'server': {'update_delivery': 'controller'},
    })
    paths.update(base_dir=owner, results_dir=results, generated_results_dir=results,
                 sqlite_db_path=database, storage_backend=backend)
    goldendict.update(run_intellifiller=False)
    return config, paths, goldendict


@contextmanager
def controller_lifetime(owner, translator, backend='sqlite'):
    config, paths, goldendict = owned_configuration(
        owner, f'http://127.0.0.1:{translator.server_port}', backend)
    server = create_browser_test_server(ControllerRequestHandler)
    arbiter = None
    try:
        server.config, server.resolved_paths, server.goldendict = config, paths, goldendict
        server.api_key = 'test-controller-api-key'
        server.seq_counter, server.seq_lock = 0, threading.Lock()
        server.start_time = time.time()
        arbiter = server.arbiter = SessionArbiter(config, paths, wordfill_cfg={'enabled': False})
        arbiter.goldendict = goldendict
        server.supervisor = ProcessSupervisor(config, paths, enabled=False)
        server.translation_service = translator
        server.start()
        yield f'http://127.0.0.1:{server.server_port}', server
    finally:
        try:
            server.server_close()
        finally:
            if arbiter is not None and server.resources_stopped():
                arbiter.enrichment_queue.shutdown(wait=True)
                desk.unregister_failover_callback(arbiter._on_global_failover)


@pytest.fixture
def controller_world(tmp_path, request):
    """A fresh controller and real database per contract; no domain replacements."""
    with translation_service(DeterministicTranslationHandler) as translator:
        with controller_lifetime(tmp_path, translator, getattr(request, 'param', 'sqlite')) as world:
            seed_cached_lookup_inputs(world[1])
            yield world


def seed_cached_lookup_inputs(server):
    """Explicit cache preconditions formerly inherited from earlier module cases."""
    adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
    headers = ['TokenOrder', 'Quotation', 'WordSource', 'SentenceSourceIndex',
               'SentenceSource', 'SentenceDestination', 'WordDestination']
    for zid, word in [('20261108113001', 'apple'), ('20261108113002', 'dog')]:
        adapter.save_session(session_zid=zid, slug=word, source_language='en', target_language='ru',
            text_mode='single', source_raw_text=word, headers=headers,
            data_rows=[['0', word, word, '1', word, f'cached: {word}', f'cached: {word}']],
            text_provenance='cached:fixture', working_tsv_path=server.resolved_paths['results_dir'] / f'{zid}-{word}.en.tsv')


def seed_toolbar_session(config, paths):
    """Back the static toolbar's injected bootstrap with its real stored session."""
    source, destination = 'Ein schneller Fuchs springt.', 'Быстрая лиса прыгает.'
    headers = ['TokenOrder', 'Quotation', 'WordSourceInflectedForm', 'WordSource',
               'WordSourcePOS', 'WordDestination', 'SentenceSourceIndex', 'SentenceSource', 'SentenceDestination']
    rows = [['0', 'Fuchs', 'Fuchs', 'Fuchs', 'n.', 'лиса', '1', source, destination],
            ['1', 'springt', 'springt', 'springen', 'v.', 'прыгает', '1', source, destination]]
    adapter = desk.get_storage_adapter(config, paths)
    adapter.save_session(session_zid='20261017230000', slug='ein-schneller-fuchs-springt',
        source_language='de', target_language='ru', text_mode='single', source_raw_text=source,
        headers=headers, data_rows=rows, text_provenance='cached:fixture')
