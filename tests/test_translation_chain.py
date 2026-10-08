import sys
import configparser
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

import kardenwort_desk
from kardenwort_desk import (
    resolve_provider_chain,
    translate_text,
    _translate_text_impl,
    translate_lemmas_fast_path,
    _migrate_config,
    format_provider_skeleton_label,
    TranslationException,
    SEC_PIPELINE,
)

DEEP_TRANSLATOR_DIR = Path(__file__).resolve().parent.parent.parent / "20241122093311-deep-translator"
if str(DEEP_TRANSLATOR_DIR) not in sys.path:
    sys.path.insert(0, str(DEEP_TRANSLATOR_DIR))


def make_config(chain=None, lemma_chain=None, strategy=None, text_base=None, auto_fallback=None):
    config = configparser.ConfigParser()
    config.add_section(SEC_PIPELINE)
    if chain is not None:
        config.set(SEC_PIPELINE, "text_provider_chain", chain)
    if lemma_chain is not None:
        config.set(SEC_PIPELINE, "lemma_provider_chain", lemma_chain)
    if strategy is not None:
        config.set(SEC_PIPELINE, "failover_strategy", strategy)
    if text_base is not None:
        config.set(SEC_PIPELINE, "text_base_provider", text_base)
    if auto_fallback is not None:
        config.set(SEC_PIPELINE, "auto_offline_fallback", str(auto_fallback).lower())
    return config


def test_resolve_provider_chain_declarative():
    config = make_config(chain="argos, google, deepl", strategy="chain")
    providers, strategy = resolve_provider_chain(config, task_type="text")
    assert providers == ["argos", "google", "deepl"]
    assert strategy == "chain"


def test_resolve_provider_chain_strict():
    config = make_config(chain="argos, google", strategy="strict")
    providers, strategy = resolve_provider_chain(config, task_type="text")
    assert providers == ["argos", "google"]
    assert strategy == "strict"


def test_resolve_provider_chain_backward_compat_auto_fallback():
    config = make_config(text_base="google", auto_fallback=True)
    providers, strategy = resolve_provider_chain(config, task_type="text")
    assert "google" in providers
    assert "argos" in providers
    assert strategy == "offline_fallback"


def test_migrate_configuration_populates_chains():
    config = make_config(text_base="deepl", auto_fallback=False)
    _migrate_config(config)
    assert config.get(SEC_PIPELINE, "text_provider_chain") == "deepl"
    assert config.get(SEC_PIPELINE, "failover_strategy") == "chain"


def test_strict_strategy_stops_on_error(tmp_path):
    config = make_config(chain="google, mock", strategy="strict")
    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}

    mock_google = MagicMock(side_effect=Exception("Google failed 500"))
    mock_mock = MagicMock(return_value="[MOCK] Translated")

    with patch("kardenwort_desk.run_google_translation", mock_google):
        with patch.dict("kardenwort_desk.__dict__", {"run_google_translation": mock_google}):
            with pytest.raises(Exception) as exc_info:
                _translate_text_impl("Hello", "en", "de", config, resolved_paths)
            assert "Google failed 500" in str(exc_info.value)
            mock_google.assert_called_once()
            mock_mock.assert_not_called()


def test_chain_strategy_sequential_fallback(tmp_path):
    config = make_config(chain="deepl, google, mock", strategy="chain")
    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}

    mock_deepl = MagicMock(side_effect=Exception("DeepL auth failed"))
    mock_google = MagicMock(side_effect=Exception("Google rate limit 429"))

    with patch("kardenwort_desk.run_deepl_translation", mock_deepl), \
         patch("kardenwort_desk.run_google_translation", mock_google):
        result = _translate_text_impl("Hello world", "en", "de", config, resolved_paths)
        assert result == "[MOCK] Hello world"
        mock_deepl.assert_called_once()
        mock_google.assert_called_once()


def test_offline_fallback_strategy_probes(tmp_path):
    config = make_config(chain="google, argos", strategy="offline_fallback")
    config.set(SEC_PIPELINE, "fast_connectivity_check_ips", "8.8.8.8")
    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}

    mock_argos = MagicMock(return_value="Offline translated text")
    mock_google = MagicMock(return_value="Online translated text")

    with patch("kardenwort_desk.is_network_online_multi", return_value=False), \
         patch("kardenwort_desk.run_argos_translation", mock_argos), \
         patch("kardenwort_desk.run_google_translation", mock_google):
        result = _translate_text_impl("Test offline", "en", "de", config, resolved_paths)
        assert result == "Offline translated text"
        mock_google.assert_not_called()
        mock_argos.assert_called_once()


def test_chain_head_determines_base_provider_and_skeleton_label():
    config = make_config(chain="argos, deepl, google", lemma_chain="argos, deepl, google", strategy="chain")
    _migrate_config(config)
    assert config.get(SEC_PIPELINE, "text_base_provider") == "argos"
    assert config.get(SEC_PIPELINE, "lemma_base_provider") == "argos"
    assert format_provider_skeleton_label(config.get(SEC_PIPELINE, "text_base_provider")) == "Argos..."
    assert format_provider_skeleton_label(config.get(SEC_PIPELINE, "lemma_base_provider")) == "Argos..."


def test_chain_head_initiates_argos_translation(tmp_path):
    config = make_config(chain="argos, deepl, google", strategy="chain")
    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}

    mock_argos = MagicMock(return_value="Argos translated sentence")
    mock_deepl = MagicMock(return_value="DeepL translated sentence")
    mock_google = MagicMock(return_value="Google translated sentence")

    with patch("kardenwort_desk.run_argos_translation", mock_argos), \
         patch("kardenwort_desk.run_deepl_translation", mock_deepl), \
         patch("kardenwort_desk.run_google_translation", mock_google):
        result = _translate_text_impl("Hello world", "en", "de", config, resolved_paths)
        assert result == "Argos translated sentence"
        mock_argos.assert_called_once()
        mock_deepl.assert_not_called()
        mock_google.assert_not_called()


def test_chain_head_preserves_order_when_primary_provider_passed(tmp_path):
    config = make_config(chain="argos, deepl, google", strategy="chain")
    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}

    mock_argos = MagicMock(return_value="Argos translation")
    mock_google = MagicMock(return_value="Google translation")

    with patch("kardenwort_desk.run_argos_translation", mock_argos), \
         patch("kardenwort_desk.run_google_translation", mock_google):
        result = _translate_text_impl("Hello", "en", "de", config, resolved_paths, provider="argos")
        assert result == "Argos translation"
        mock_argos.assert_called_once()
        mock_google.assert_not_called()


def test_chain_override_prefers_explicit_provider(tmp_path):
    config = make_config(chain="argos, deepl, google", strategy="chain")
    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}

    mock_argos = MagicMock(return_value="Argos translation")
    mock_deepl = MagicMock(return_value="DeepL translation")

    with patch("kardenwort_desk.run_argos_translation", mock_argos), \
         patch("kardenwort_desk.run_deepl_translation", mock_deepl):
        result = _translate_text_impl("Hello", "en", "de", config, resolved_paths, provider="deepl")
        assert result == "DeepL translation"
        mock_deepl.assert_called_once()
        mock_argos.assert_not_called()


def test_chained_google_translation_uses_fast_fail_timeout(tmp_path):
    config = make_config(chain="google, deepl, argos", strategy="chain")
    resolved_paths = {
        "results_dir": tmp_path,
        "base_dir": tmp_path,
        "deep_translator_python": "python",
        "translate_google_script": "dummy_google.py"
    }

    mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout="Привет\n"))
    with patch("subprocess.run", mock_run):
        res = kardenwort_desk.run_google_translation("Hello", "en", "ru", config, resolved_paths)
        assert res == "Привет"
        assert mock_run.called
        cmd_args = mock_run.call_args[0][0]
        assert "--max-total-time" in cmd_args
        assert "--timeout" in cmd_args
        # Subprocess timeout must be bounded (not 60s)
        timeout_arg = mock_run.call_args[1].get("timeout")
        assert timeout_arg is not None
        assert timeout_arg <= 15.0


def test_provider_cooldown_cross_process_persistence(tmp_path):
    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}
    config = make_config(chain="google, deepl", strategy="chain")

    # Clear before test
    kardenwort_desk.clear_provider_cooldowns(config=config, resolved_paths=resolved_paths)
    assert not kardenwort_desk.is_provider_cooled_down("google", config=config, resolved_paths=resolved_paths)

    # Record cooldown
    kardenwort_desk.record_provider_cooldown("google", duration=60.0, config=config, resolved_paths=resolved_paths)
    assert kardenwort_desk.is_provider_cooled_down("google", config=config, resolved_paths=resolved_paths)

    # Clear in-memory dictionary to simulate a separate process starting with empty memory
    kardenwort_desk._provider_cooldowns.clear()
    assert len(kardenwort_desk._provider_cooldowns) == 0

    # Cross-process check should load the shared cooldown file and recognize Google as cooled down
    assert kardenwort_desk.is_provider_cooled_down("google", config=config, resolved_paths=resolved_paths)
    assert kardenwort_desk.get_provider_cooldown_remaining("google", config=config, resolved_paths=resolved_paths) > 0.0

    # Cleanup
    kardenwort_desk.clear_provider_cooldowns(config=config, resolved_paths=resolved_paths)
    assert not kardenwort_desk.is_provider_cooled_down("google", config=config, resolved_paths=resolved_paths)


def test_lemma_fast_path_candidate_fallback_on_single_lemma_failure(tmp_path):
    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}
    config = make_config(lemma_chain="argos, google", strategy="chain")

    def mock_translate(text, source, target, cfg, paths, provider=None, zid=None, trace_id=None):
        if provider == "argos":
            raise RuntimeError("Argos dropped query")
        elif provider == "google":
            return "Дом"
        return ""

    with patch("kardenwort_desk.translate_text", side_effect=mock_translate):
        result = translate_lemmas_fast_path(["Haus"], "de", "ru", config, resolved_paths, provider="argos")
        assert result == {"Haus": "Дом"}
        assert getattr(result, "provenance", None) == "live:google"


def test_lemma_fast_path_strict_strategy_does_not_fallback(tmp_path):
    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}
    config = make_config(lemma_chain="argos, google", strategy="strict")

    def mock_translate(text, source, target, cfg, paths, provider=None, zid=None, trace_id=None):
        if provider == "argos":
            raise RuntimeError("Argos dropped query")
        elif provider == "google":
            return "Дом"
        return ""

    with patch("kardenwort_desk.translate_text", side_effect=mock_translate):
        result = translate_lemmas_fast_path(["Haus"], "de", "ru", config, resolved_paths, provider="argos")
        assert result == {"Haus": ""}
        assert getattr(result, "provenance", None) == "live:argos"


def test_persistence_guard_zero_empty_field_protection(tmp_path):
    from kardenwort_controller import SessionArbiter
    arbiter = SessionArbiter(config=None, resolved_paths={"results_dir": tmp_path, "base_dir": tmp_path})
    session_zid = "20261008120000"
    arbiter.sessions[session_zid] = {
        "language": "de",
        "data_rows": [["Haus", "ExistingTranslation", "1", "0"]],
        "headers": ["WordSource", "WordDestination", "SentenceIndex", "TokenOrder"],
        "role_fields": {"lemma": "WordSource", "word_translation": "WordDestination"},
        "comments": [],
    }

    # Propagating an empty translation must not wipe existing translation
    arbiter.propagate_translations_to_siblings({"Haus": ""}, exclude_session_zid=None, language="de")
    assert arbiter.sessions[session_zid]["data_rows"][0][1] == "ExistingTranslation"

    # Propagating a placeholder or failed row must not wipe if empty
    arbiter.sessions[session_zid]["data_rows"][0][1] = "[FAILED]"
    arbiter.propagate_translations_to_siblings({"Haus": ""}, exclude_session_zid=None, language="de")
    assert arbiter.sessions[session_zid]["data_rows"][0][1] == "[FAILED]"

    # Propagating a valid translation updates properly
    arbiter.propagate_translations_to_siblings({"Haus": "Дом"}, exclude_session_zid=None, language="de")
    assert arbiter.sessions[session_zid]["data_rows"][0][1] == "Дом"


def test_record_session_active_provider_keyword_signatures_and_persistence(tmp_path):
    import kardenwort_desk
    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}
    config = make_config()
    test_zid = "20261008215501"

    # 1. Positional calls
    kardenwort_desk.record_session_active_provider(test_zid, "text", "google", config=config, resolved_paths=resolved_paths)
    provs = kardenwort_desk.get_session_active_providers(test_zid, config=config, resolved_paths=resolved_paths)
    assert provs.get("active_text_provider") == "google"
    assert provs.get("active_provider") == "google"

    # 2. Keyword calls as used during offline fast connectivity check in desk and controller
    # (previously crashed with TypeError: got unexpected keyword argument 'text')
    kardenwort_desk.record_session_active_provider(
        test_zid,
        text="argos",
        lemma="argos",
        provider="argos",
        config=config,
        resolved_paths=resolved_paths,
    )
    provs = kardenwort_desk.get_session_active_providers(test_zid, config=config, resolved_paths=resolved_paths)
    assert provs.get("active_text_provider") == "argos"
    assert provs.get("active_lemma_provider") == "argos"
    assert provs.get("active_provider") == "argos"

    # 3. Verify file persistence and reloading from disk
    kardenwort_desk._active_session_providers.clear()
    provs_reloaded = kardenwort_desk.get_session_active_providers(test_zid, config=config, resolved_paths=resolved_paths)
    assert provs_reloaded.get("active_text_provider") == "argos"
    assert provs_reloaded.get("active_lemma_provider") == "argos"
    assert provs_reloaded.get("active_provider") == "argos"


def test_offline_fast_connectivity_probes_records_session_active_provider(tmp_path):
    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}
    config = make_config(chain="google, argos", strategy="offline_fallback")
    config.set(SEC_PIPELINE, "fast_connectivity_check_ips", "8.8.8.8")
    test_zid = "20261008215502"

    mock_argos = MagicMock(return_value="Offline translated text")

    with patch("kardenwort_desk.is_network_online_multi", return_value=False), \
         patch("kardenwort_desk.run_argos_translation", mock_argos):
        res = _translate_text_impl("Test offline text", "en", "de", config, resolved_paths, zid=test_zid)
        assert res == "Offline translated text"

    # Session active provider was recorded via failover callback without TypeError
    import kardenwort_desk
    provs = kardenwort_desk.get_session_active_providers(test_zid, config=config, resolved_paths=resolved_paths)
    assert provs.get("active_text_provider") == "argos"


def test_orthogonal_chains_execute_independently(tmp_path):
    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}
    config = make_config(chain="deepl, argos", lemma_chain="google, argos", strategy="chain")

    text_providers, text_strat = resolve_provider_chain(config, task_type="text")
    lemma_providers, lemma_strat = resolve_provider_chain(config, task_type="lemma")

    assert text_providers == ["deepl", "argos"]
    assert lemma_providers == ["google", "argos"]

    mock_deepl = MagicMock(return_value="DeepL sentence")
    mock_google = MagicMock(return_value="Google sentence")

    with patch("kardenwort_desk.run_deepl_translation", mock_deepl), \
         patch("kardenwort_desk.run_google_translation", mock_google):
        text_res = _translate_text_impl("Test sentence", "en", "de", config, resolved_paths)
        assert text_res == "DeepL sentence"
        mock_deepl.assert_called_once()
        mock_google.assert_not_called()

    # Fast path lemma translation queries google, not deepl
    def mock_translate(text, source, target, cfg, paths, provider=None, zid=None, trace_id=None):
        if provider == "google":
            return "Apfel"
        elif provider == "deepl":
            raise RuntimeError("DeepL should not be invoked for lemma chain")
        return ""

    with patch("kardenwort_desk.translate_text", side_effect=mock_translate):
        lemma_res = translate_lemmas_fast_path(["Apple"], "en", "de", config, resolved_paths, provider="google")
        assert lemma_res == {"Apple": "Apfel"}


def test_task_scoped_failover_state_tracking(tmp_path):
    from kardenwort_controller import SessionArbiter
    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}
    config = make_config(chain="google, argos", lemma_chain="deepl, argos", strategy="chain")
    arbiter = SessionArbiter(config=config, resolved_paths=resolved_paths)
    test_zid = "20261008223900"

    arbiter.sessions[test_zid] = {
        "language": "de",
        "data_rows": [],
        "headers": [],
        "role_fields": {},
        "comments": [],
    }

    # Text failover must update active_text_provider independently
    arbiter.notify_provider_failover(test_zid, task="text", from_provider="google", to_provider="argos")
    sess = arbiter.sessions[test_zid]
    assert sess.get("active_text_provider") == "argos"
    assert sess.get("active_lemma_provider") is None

    # Lemma failover must update active_lemma_provider independently
    arbiter.notify_provider_failover(test_zid, task="lemma", from_provider="deepl", to_provider="argos")
    sess = arbiter.sessions[test_zid]
    assert sess.get("active_text_provider") == "argos"
    assert sess.get("active_lemma_provider") == "argos"


def test_standardized_provider_timeout_calculation(tmp_path):
    config = make_config(chain="google, deepl, argos", strategy="chain")
    config.set(SEC_PIPELINE, "provider_call_timeout", "4.0")
    resolved_paths = {
        "results_dir": tmp_path,
        "base_dir": tmp_path,
        "deep_translator_python": "python",
        "translate_google_script": "dummy_google.py"
    }

    mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout="Translated\n"))
    with patch("subprocess.run", mock_run):
        res = kardenwort_desk.run_google_translation("Hello", "en", "de", config, resolved_paths)
        assert res == "Translated"
        cmd_args = mock_run.call_args[0][0]
        max_idx = cmd_args.index("--max-total-time")
        assert cmd_args[max_idx + 1] == "4.0"
        t_idx = cmd_args.index("--timeout")
        assert cmd_args[t_idx + 1] == "3.0"
        subproc_timeout = mock_run.call_args[1].get("timeout")
        assert subproc_timeout == 6.0  # 4.0 + 2.0 buffer


def test_fast_connectivity_probe_timeouts_bounded():
    assert kardenwort_desk.MICROSERVICE_CONNECT_TIMEOUT_DEFAULT < 0.5
    # verify check_endpoint_reachable caps effective timeout
    with patch("socket.create_connection") as mock_conn:
        kardenwort_desk.check_endpoint_reachable("http://127.0.0.1:8080", connect_timeout=5.0)
        assert mock_conn.called
        assert mock_conn.call_args[1].get("timeout") <= 0.4


def test_resolve_provider_chain_canonical_precedence():
    # Canonical text_provider_chain overrides legacy text_base_provider
    config = make_config(chain="google, deepl, argos", text_base="argos", strategy="chain")
    providers, strategy = resolve_provider_chain(config, task_type="text")
    assert providers == ["google", "deepl", "argos"]
    assert providers[0] == "google"
    assert strategy == "chain"

    # Canonical lemma_provider_chain overrides legacy lemma_base_provider
    config_lemma = configparser.ConfigParser()
    config_lemma.add_section(SEC_PIPELINE)
    config_lemma.set(SEC_PIPELINE, "lemma_provider_chain", "deepl, argos")
    config_lemma.set(SEC_PIPELINE, "lemma_base_provider", "google")
    l_provs, _ = resolve_provider_chain(config_lemma, task_type="lemma")
    assert l_provs == ["deepl", "argos"]
    assert l_provs[0] == "deepl"

    # Explicit failover_strategy overrides legacy auto_offline_fallback
    config_strat = make_config(chain="google, argos", strategy="chain", auto_fallback=True)
    _, strategy2 = resolve_provider_chain(config_strat, task_type="text")
    assert strategy2 == "chain"

    # Legacy text_base_provider works when text_provider_chain is absent
    config_leg = make_config(text_base="deepl")
    leg_provs, _ = resolve_provider_chain(config_leg, task_type="text")
    assert leg_provs == ["deepl"]


def test_network_failure_fast_path_text_bypasses_intermediate_online_providers(tmp_path):
    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}
    config = make_config(chain="google, deepl, argos", strategy="chain")

    mock_google = MagicMock(side_effect=ConnectionError("Network unreachable"))
    mock_deepl = MagicMock(return_value="DeepL should not be called")
    mock_argos = MagicMock(return_value="Argos translated sentence")

    with patch("kardenwort_desk.run_google_translation", mock_google), \
         patch("kardenwort_desk.run_deepl_translation", mock_deepl), \
         patch("kardenwort_desk.run_argos_translation", mock_argos):
        res = _translate_text_impl("Test text", "en", "de", config, resolved_paths)
        assert res == "Argos translated sentence"
        mock_google.assert_called_once()
        mock_deepl.assert_not_called()
        mock_argos.assert_called_once()


def test_network_failure_fast_path_lemmas_bypasses_intermediate_online_providers(tmp_path):
    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}
    config = make_config(lemma_chain="google, deepl, argos", strategy="chain")

    def mock_translate(text, source, target, cfg, paths, provider=None, zid=None, trace_id=None):
        if provider == "google":
            raise ConnectionError("Host unreachable")
        elif provider == "deepl":
            raise RuntimeError("DeepL should not be attempted on network disconnect")
        elif provider == "argos":
            return "Haus"
        return ""

    with patch("kardenwort_desk.translate_text", side_effect=mock_translate):
        res = translate_lemmas_fast_path(["House"], "en", "de", config, resolved_paths, provider="google")
        assert res == {"House": "Haus"}
        assert getattr(res, "provenance", None) == "live:argos"


def test_rate_limit_sequential_fallback_text_tries_next_provider(tmp_path):
    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}
    config = make_config(chain="google, deepl, argos", strategy="chain")

    mock_google = MagicMock(side_effect=Exception("HTTP 429 Too Many Requests"))
    mock_deepl = MagicMock(return_value="DeepL translated sentence")
    mock_argos = MagicMock(return_value="Argos translated sentence")

    with patch("kardenwort_desk.run_google_translation", mock_google), \
         patch("kardenwort_desk.run_deepl_translation", mock_deepl), \
         patch("kardenwort_desk.run_argos_translation", mock_argos):
        res = _translate_text_impl("Test text", "en", "de", config, resolved_paths)
        assert res == "DeepL translated sentence"
        mock_google.assert_called_once()
        mock_deepl.assert_called_once()
        mock_argos.assert_not_called()


def test_rate_limit_sequential_fallback_lemmas_tries_next_provider(tmp_path):
    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}
    config = make_config(lemma_chain="google, deepl, argos", strategy="chain")

    def mock_translate(text, source, target, cfg, paths, provider=None, zid=None, trace_id=None):
        if provider == "google":
            raise Exception("HTTP 429 Too Many Requests")
        elif provider == "deepl":
            return "Haus"
        elif provider == "argos":
            return "Argos Haus"
        return ""

    with patch("kardenwort_desk.translate_text", side_effect=mock_translate):
        res = translate_lemmas_fast_path(["House"], "en", "de", config, resolved_paths, provider="google")
        assert res == {"House": "Haus"}
        assert getattr(res, "provenance", None) == "live:deepl"


def test_orthogonal_chains_disparate_configurations(tmp_path):
    resolved_paths = {"results_dir": tmp_path, "base_dir": tmp_path}
    config = make_config(chain="google, deepl", lemma_chain="argos", strategy="chain")

    mock_google = MagicMock(return_value="Google text")
    mock_argos = MagicMock(return_value="Argos lemma")

    with patch("kardenwort_desk.run_google_translation", mock_google):
        text_res = _translate_text_impl("Sentence", "en", "de", config, resolved_paths)
        assert text_res == "Google text"
        mock_google.assert_called_once()

    def mock_translate(text, source, target, cfg, paths, provider=None, zid=None, trace_id=None):
        if provider == "argos":
            return "Wort"
        raise RuntimeError(f"Unexpected provider: {provider}")

    with patch("kardenwort_desk.translate_text", side_effect=mock_translate):
        lemma_res = translate_lemmas_fast_path(["Word"], "en", "de", config, resolved_paths, provider="argos")
        assert lemma_res == {"Word": "Wort"}
        assert getattr(lemma_res, "provenance", None) == "live:argos"





