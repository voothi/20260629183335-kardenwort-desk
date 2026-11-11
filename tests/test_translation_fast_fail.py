import configparser
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
import kardenwort_desk
from tests.controller_test_support import controller_world
from kardenwort_desk import run_render_flow, load_config, SEC_PIPELINE, _migrate_config


def test_config_fast_fail_timeout_parsing(tmp_path):
    # Test default fallback when omitted
    cfg = configparser.ConfigParser()
    cfg.read_string("[pipeline]\n")
    _migrate_config(cfg)
    assert cfg.getfloat(SEC_PIPELINE, "translation_fast_fail_timeout") == 3.0

    # Test explicit configuration
    cfg2 = configparser.ConfigParser()
    cfg2.read_string("[pipeline]\ntranslation_fast_fail_timeout = 1.5\n")
    _migrate_config(cfg2)
    assert cfg2.getfloat(SEC_PIPELINE, "translation_fast_fail_timeout") == 1.5


def test_slow_translation_triggers_fast_fail(controller_world, monkeypatch):
    import threading
    from tests.controller_test_support import DeterministicTranslationHandler
    from tests.render_pipeline_support import configure_render_pipeline
    from tests.thread_support import executor_tasks

    _, server = controller_world
    config, resolved_paths = server.config, server.resolved_paths
    configure_render_pipeline(server)
    config.set('pipeline', 'translation_fast_fail_timeout', '0.2')
    entered, release = threading.Event(), threading.Event()
    requests = []
    original = DeterministicTranslationHandler.translate

    def held(handler, provider, text, source, target):
        requests.append(text)
        entered.set()
        assert release.wait(5), 'Translation engine was not released'
        return original(handler, provider, text, source, target)

    monkeypatch.setattr(DeterministicTranslationHandler, 'translate', held)
    with executor_tasks(monkeypatch) as submitted:
        try:
            start_time = time.time()
            html = run_render_flow('First sentence. Second sentence.', 'und', '20260829013000', 'single',
                                   config, resolved_paths, spawn_children=False, wordfill_cfg={'enabled': False})
            elapsed = time.time() - start_time
            fallback_called = [request for request in requests if '\n' in request]
            assert elapsed < 1.0, f"Render flow took {elapsed}s; expected fast-fail under 1.0s"
            assert not fallback_called, "Fallback translation should not have been invoked after fast-fail"
            assert isinstance(html, str)
            assert len(html) > 0
            assert entered.is_set()
            assert any(not future.done() for _, future in submitted)
            assert requests == ['First sentence. Second sentence.']
            before = kardenwort_desk.SqliteStorageAdapter(config, resolved_paths).db.get_sentences_by_session('20260829013000')
            assert all(not row['sentence_destination'] for row in before)
            with server.arbiter.enrichment_queue._lock:
                progressive = server.arbiter.enrichment_queue._active_progressive_sessions['20260829013000']
            assert any(future is progressive for _, future in submitted)
            assert not progressive.done()
        finally:
            release.set()
    assert all(future.done() for _, future in submitted)
    cold = kardenwort_desk.SqliteStorageAdapter(config, resolved_paths)
    assert [(row['sentence_destination'], row['text_provenance'])
            for row in cold.db.get_sentences_by_session('20260829013000')] == [
                ('google: First sentence.', 'live:google'), ('google: Second sentence.', 'live:google')]


def test_fast_translation_completes_within_timeout(controller_world, monkeypatch):
    from tests.controller_test_support import DeterministicTranslationHandler
    from tests.render_pipeline_support import configure_render_pipeline
    from tests.thread_support import executor_tasks

    _, server = controller_world
    config, resolved_paths = server.config, server.resolved_paths
    configure_render_pipeline(server)
    config.read_dict({'pipeline': {'translation_fast_fail_timeout': '2.0',
        'text_base_provider': 'deepl', 'text_provider_chain': 'deepl'}})

    def translate(handler, provider, text, source, target):
        handler.server.provider_calls.append((text, source, target))
        return 'Первое предложение.\nВторое предложение.' if '\n' in text else 'Первое предложение. Второе предложение.'

    # Only the unmanaged engine supplies deterministic translated strings.
    monkeypatch.setattr(DeterministicTranslationHandler, 'translate', translate)
    with executor_tasks(monkeypatch):
        start_time = time.time()
        html = run_render_flow("First sentence. Second sentence.", "und", "20260829013001", "single",
            config, resolved_paths, spawn_children=False, wordfill_cfg={'enabled': False})
        elapsed = time.time() - start_time

        assert elapsed < 1.0
        assert "Первое предложение" in html
    cold = kardenwort_desk.SqliteStorageAdapter(config, resolved_paths)
    assert [(row['sentence_destination'], row['text_provenance'])
            for row in cold.db.get_sentences_by_session('20260829013001')] == [
                ('Первое предложение.', 'live:deepl'), ('Второе предложение.', 'live:deepl')]
    assert server.translation_service.provider_calls


def test_circuit_breaker_record_and_cooldown_expiration(monkeypatch):
    from types import SimpleNamespace
    clock = [100.0]
    monkeypatch.setattr("kardenwort_desk.time", SimpleNamespace(time=lambda: clock[0]))
    kardenwort_desk.clear_provider_cooldowns()
    assert not kardenwort_desk.is_provider_cooled_down("google")

    # Record cooldown for 0.1 seconds
    kardenwort_desk.record_provider_cooldown("google", duration=0.1)
    assert kardenwort_desk.is_provider_cooled_down("google")
    assert kardenwort_desk.get_provider_cooldown_remaining("google") > 0

    clock[0] += 0.15
    assert not kardenwort_desk.is_provider_cooled_down("google")
    assert kardenwort_desk.get_provider_cooldown_remaining("google") == 0.0


def test_circuit_breaker_trips_on_rate_limit_exception(tmp_path):
    kardenwort_desk.clear_provider_cooldowns()
    config = configparser.ConfigParser()
    config.add_section(SEC_PIPELINE)
    config.set(SEC_PIPELINE, "text_provider_chain", "google, mock")
    config.set(SEC_PIPELINE, "failover_strategy", "chain")
    config.set(SEC_PIPELINE, "provider_cooldown_seconds", "10.0")

    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}

    mock_google = MagicMock(side_effect=kardenwort_desk.TranslationException("Rate limited", envelope={"code": "ERR_GOOGLE_RATE_LIMIT", "details": {"http_code": 429}}))

    with patch("kardenwort_desk.run_google_translation", mock_google):
        res = kardenwort_desk._translate_text_impl("Test phrase", "en", "de", config, resolved_paths)
        assert res == "[MOCK] Test phrase"
        assert kardenwort_desk.is_provider_cooled_down("google")
        mock_google.assert_called_once()

    # Second call must bypass google completely because it is in cooldown
    mock_google.reset_mock()
    with patch("kardenwort_desk.run_google_translation", mock_google):
        res2 = kardenwort_desk._translate_text_impl("Second phrase", "en", "de", config, resolved_paths)
        assert res2 == "[MOCK] Second phrase"
        mock_google.assert_not_called()

    kardenwort_desk.clear_provider_cooldowns()


def test_multi_tier_lemma_failover_when_provider_cooled_down(tmp_path):
    kardenwort_desk.clear_provider_cooldowns()
    config = configparser.ConfigParser()
    config.add_section(SEC_PIPELINE)
    config.set(SEC_PIPELINE, "lemma_provider_chain", "google, mock")
    config.set(SEC_PIPELINE, "failover_strategy", "chain")

    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}

    # Put google in cooldown
    kardenwort_desk.record_provider_cooldown("google", duration=60.0)
    assert kardenwort_desk.is_provider_cooled_down("google")

    lemmas = ["Haus", "Baum"]
    mock_google = MagicMock(side_effect=Exception("Should not be called!"))

    with patch("kardenwort_desk.run_google_translation", mock_google):
        res = kardenwort_desk.translate_lemmas_fast_path(lemmas, "de", "en", config, resolved_paths, provider="google")
        assert res["Haus"] == "[MOCK] Haus"
        assert res["Baum"] == "Baum"
        assert res.provenance == "live:mock"
        mock_google.assert_not_called()

    kardenwort_desk.clear_provider_cooldowns()


def test_multi_tier_lemma_tier0_rate_limit_short_circuits_to_fallback(tmp_path):
    kardenwort_desk.clear_provider_cooldowns()
    config = configparser.ConfigParser()
    config.add_section(SEC_PIPELINE)
    config.set(SEC_PIPELINE, "lemma_provider_chain", "google, mock")
    config.set(SEC_PIPELINE, "failover_strategy", "chain")

    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}

    lemmas = ["Haus", "Baum"]

    def fake_translate_text(text, source, target, cfg, paths, provider=None, zid=None, trace_id=None):
        if provider == "google":
            raise kardenwort_desk.TranslationException("Google 429", envelope={"code": "ERR_GOOGLE_RATE_LIMIT", "details": {"http_code": 429}})
        elif provider == "mock":
            if ";" in text:
                return "house; tree"
            return f"[MOCK] {text}"
        raise Exception("Unknown provider")

    with patch("kardenwort_desk.translate_text", side_effect=fake_translate_text) as mock_trans:
        res = kardenwort_desk.translate_lemmas_fast_path(lemmas, "de", "en", config, resolved_paths, provider="google")
        assert res["Haus"] == "house"
        assert res["Baum"] == "tree"
        assert kardenwort_desk.is_provider_cooled_down("google")

    kardenwort_desk.clear_provider_cooldowns()
