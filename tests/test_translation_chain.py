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
    _migrate_config,
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
