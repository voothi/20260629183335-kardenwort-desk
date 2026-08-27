"""
Unit tests for EnrichmentQueue deduplication, concurrency bounding, and sibling session propagation.
"""
import time
import queue
import threading
import concurrent.futures
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

import kardenwort_desk
from kardenwort_controller import EnrichmentQueue, SessionArbiter


@pytest.fixture
def desk_config_and_paths():
    desk_dir = Path(__file__).resolve().parent.parent
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config(desk_dir / "config.ini")
    return config, resolved_paths, goldendict, wordfill


def test_enrichment_queue_coalesces_concurrent_duplicate_lemmas(desk_config_and_paths):
    config, resolved_paths, _, _ = desk_config_and_paths
    eq = EnrichmentQueue(config, resolved_paths, max_workers=2)

    call_count = 0
    call_lock = threading.Lock()

    def mock_execute(lemma, language, prompt_name, zid, trace_id):
        nonlocal call_count
        with call_lock:
            call_count += 1
        time.sleep(0.05)
        return {
            "WordDestination": f"trans_{lemma}",
            "WordSourceIPA": f"/ipa_{lemma}/",
            "WordSourceMorphologyAI": f"morph_{lemma}",
        }

    eq._execute_enrich_lemma = mock_execute

    # Concurrently submit 5 requests for the exact same lemma
    results = []
    def worker():
        res = eq.enrich_lemma("Kugelschreiber", "de")
        results.append(res)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 5
    for r in results:
        assert r["WordDestination"] == "trans_Kugelschreiber"
        assert r["WordSourceIPA"] == "/ipa_Kugelschreiber/"
        assert r["WordSourceMorphologyAI"] == "morph_Kugelschreiber"

    # LLM execution should happen exactly once due to in-flight coalescing
    assert call_count == 1

    # Follow-up query should hit cache with zero additional executions
    cached_res = eq.enrich_lemma("Kugelschreiber", "de")
    assert cached_res["WordDestination"] == "trans_Kugelschreiber"
    assert call_count == 1

    eq.shutdown()


def test_enrichment_queue_concurrency_bounding(desk_config_and_paths):
    config, resolved_paths, _, _ = desk_config_and_paths
    eq = EnrichmentQueue(config, resolved_paths, max_workers=1)

    active_workers = 0
    max_active_observed = 0
    active_lock = threading.Lock()

    def mock_execute(lemma, language, prompt_name, zid, trace_id):
        nonlocal active_workers, max_active_observed
        with active_lock:
            active_workers += 1
            if active_workers > max_active_observed:
                max_active_observed = active_workers
        time.sleep(0.04)
        with active_lock:
            active_workers -= 1
        return {"WordDestination": f"trans_{lemma}"}

    eq._execute_enrich_lemma = mock_execute

    # Concurrently submit 3 distinct lemmas
    threads = [
        threading.Thread(target=eq.enrich_lemma, args=(f"Lemma_{i}", "de"))
        for i in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert max_active_observed <= 1
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
