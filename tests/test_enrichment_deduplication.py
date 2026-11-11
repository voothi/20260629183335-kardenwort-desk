"""
Unit tests for EnrichmentQueue deduplication, concurrency bounding, and sibling session propagation.
"""
import pytest

from kardenwort_controller import EnrichmentQueue, SessionArbiter
from tests.controller_test_support import owned_configuration
from tests.enrichment_test_support import enrichment_engine, FutureWaiters
from tests.thread_support import Worker, finish_workers


@pytest.fixture
def desk_config_and_paths(tmp_path):
    config, paths, goldendict = owned_configuration(tmp_path, '', 'sqlite')
    return config, paths, goldendict, {'enabled': False}


def test_enrichment_queue_coalesces_concurrent_duplicate_lemmas(desk_config_and_paths, monkeypatch):
    config, resolved_paths, _, _ = desk_config_and_paths
    eq = EnrichmentQueue(config, resolved_paths, max_workers=2)
    waiters = FutureWaiters(monkeypatch)
    workers = []
    with enrichment_engine(config) as engine:
        try:
            workers = [Worker(lambda: waiters.call(lambda: eq.enrich_lemma("Kugelschreiber", "de")))
                       for _ in range(5)]
            waiters.wait(5)
            with engine.changed:
                assert engine.changed.wait_for(lambda: len(engine.requests) == 1, 5)
            assert all(worker.thread.is_alive() for worker in workers)
            engine.release.set()
            results = finish_workers(workers)
            call_count = len(engine.requests)
            assert len(results) == 5
            for r in results:
                assert r["WordDestination"] == "trans_Kugelschreiber"
                assert r["WordSourceIPA"] == "/ipa_Kugelschreiber/"
                assert r["WordSourceMorphologyAI"] == "morph_Kugelschreiber"
            assert call_count == 1
            cached_res = eq.enrich_lemma("Kugelschreiber", "de")
            call_count = len(engine.requests)
            assert cached_res["WordDestination"] == "trans_Kugelschreiber"
            assert call_count == 1
            assert engine.requests[0]['rows'] == [dict(row_id=0, WordSource='Kugelschreiber')]
            assert engine.requests[0]['language'] == 'de'
        finally:
            engine.release.set()
            try:
                finish_workers(workers)
            finally:
                eq.shutdown()


def test_enrichment_queue_concurrency_bounding(desk_config_and_paths, monkeypatch):
    config, resolved_paths, _, _ = desk_config_and_paths
    eq = EnrichmentQueue(config, resolved_paths, max_workers=1)
    waiters = FutureWaiters(monkeypatch)
    workers = []
    with enrichment_engine(config) as engine:
        try:
            workers = [Worker(lambda lemma=f'Lemma_{i}': waiters.call(lambda: eq.enrich_lemma(lemma, 'de')))
                       for i in range(3)]
            waiters.wait(3)
            with engine.changed:
                assert engine.changed.wait_for(lambda: len(engine.requests) == 1, 5)
            assert all(worker.thread.is_alive() for worker in workers)
            engine.release.set()
            results = finish_workers(workers)
            max_active_observed = engine.maximum_active
            assert max_active_observed <= 1
            assert max_active_observed == 1
            assert len(engine.requests) == 3
            assert {result['WordDestination'] for result in results} == {f'trans_Lemma_{i}' for i in range(3)}
        finally:
            engine.release.set()
            try:
                finish_workers(workers)
            finally:
                eq.shutdown()


def test_enrichment_queue_error_recovery(desk_config_and_paths):
    config, resolved_paths, _, _ = desk_config_and_paths
    eq = EnrichmentQueue(config, resolved_paths, max_workers=1)

    fail_first = True

    def mock_execute(lemma, language, prompt_name, zid, trace_id):
        nonlocal fail_first
        if fail_first:
            fail_first = False
            raise RuntimeError("Temporary LLM error")
        return {"WordDestination": "recovered_trans"}

    eq._execute_enrich_lemma = mock_execute

    # First attempt fails
    with pytest.raises(RuntimeError, match="Temporary LLM error"):
        eq.enrich_lemma("Flugzeug", "de")

    # In-flight lock and futures must be cleaned up
    assert ("Flugzeug", "de") not in eq._inflight_lemmas

    # Retry succeeds
    res = eq.enrich_lemma("Flugzeug", "de")
    assert res == {"WordDestination": "recovered_trans"}

    eq.shutdown()


def test_sibling_sessions_propagation_in_arbiter(desk_config_and_paths, tmp_path):
    config, resolved_paths, _, _ = desk_config_and_paths
    arbiter = SessionArbiter(config, resolved_paths)

    headers = ["WordSource", "WordDestination", "WordSourceIPA", "WordSourceMorphologyAI"]
    role_fields = {
        "lemma": "WordSource",
        "word_translation": "WordDestination",
        "ipa": "WordSourceIPA",
        "morphology": "WordSourceMorphologyAI",
    }

    sess_a_zid = "20260827120001"
    sess_b_zid = "20260827120002"

    rows_a = [["Onboarding-Unterstützung", "", "", ""]]
    rows_b = [["Onboarding-Unterstützung", "", "", ""]]

    arbiter.sessions[sess_a_zid] = {
        "data_rows": rows_a,
        "headers": headers,
        "role_fields": role_fields,
        "language": "de",
    }
    arbiter.sessions[sess_b_zid] = {
        "data_rows": rows_b,
        "headers": headers,
        "role_fields": role_fields,
        "language": "de",
    }

    # Register subscriber queue on sibling session B
    sub_q_b = arbiter.register_subscriber(sess_b_zid)

    # Propagate enriched fields
    enriched_data = {
        "Onboarding-Unterstützung": {
            "WordDestination": "onboarding support",
            "WordSourceIPA": "/ˈɔnˌbɔːrdɪŋ ˈʊntɐˌʃtʏtsʊŋ/",
            "WordSourceMorphologyAI": "Noun, feminine compound",
        }
    }

    arbiter.propagate_enrichment_to_siblings(enriched_data, exclude_session_zid=sess_a_zid, language="de")

    # Verify session B in-memory rows updated
    assert arbiter.sessions[sess_b_zid]["data_rows"][0][1] == "onboarding support"
    assert arbiter.sessions[sess_b_zid]["data_rows"][0][2] == "/ˈɔnˌbɔːrdɪŋ ˈʊntɐˌʃtʏtsʊŋ/"
    assert arbiter.sessions[sess_b_zid]["data_rows"][0][3] == "Noun, feminine compound"

    # Verify session B subscriber received update event
    event = sub_q_b.get(timeout=2.0)
    assert event["type"] == "update"
    assert event["stage"] == "enrichment"
    row0 = event["rows"][0] if 0 in event["rows"] else event["rows"]["0"]
    assert row0["lemma"] == "Onboarding-Unterstützung"
    assert row0["trans"] == "onboarding support"
