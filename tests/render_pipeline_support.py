"""Real pipeline lifetimes with a rendezvous at the two unmanaged engines."""
import threading

import pytest

from tests.controller_test_support import (controller_lifetime, translation_service,
    token_service, DeterministicTranslationHandler)
from tests.test_spacy_service import MockSpacyRequestHandler


def configure_render_pipeline(server):
    server.config.read_dict({
        'sentences_mode': {'enabled': 'true', 'delivery_mode': 'container'},
        'rendering': {'display_mode': 'monolithic'},
        'pipeline': {'parallelize_core_and_translation': 'true'},
        'triggers': {'run_text_translation': 'auto', 'run_lemma_base_translation': 'manual',
                     'run_lemma_enrichment': 'manual'}})


@pytest.fixture
def overlapping_pipeline(tmp_path):
    rendezvous = threading.Barrier(2, timeout=5)
    observed, lock = set(), threading.Lock()

    def arrive(port):
        with lock:
            if port in observed:
                return
            observed.add(port)
        rendezvous.wait()

    class TranslationEngine(DeterministicTranslationHandler):
        def translate(self, provider, text, source, target):
            arrive('translation')
            return super().translate(provider, text, source, target)

    class TokenEngine(MockSpacyRequestHandler):
        def do_POST(self):
            if self.path == '/tokenize':
                arrive('extraction')
            return super().do_POST()

    with translation_service(TranslationEngine) as translator, token_service(TokenEngine) as tokenizer:
        with controller_lifetime(tmp_path, translator) as (_, server):
            server.config.set('services', 'spacy_server_url', f'http://127.0.0.1:{tokenizer.server_port}')
            configure_render_pipeline(server)
            server.render_preparation_rendezvous = rendezvous
            try:
                yield server, observed
            finally:
                rendezvous.abort()
