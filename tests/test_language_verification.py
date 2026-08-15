import configparser
import pytest
from unittest.mock import patch

from kardenwort_desk import (
    LanguageCheckConfig,
    LanguageVerificationResult,
    verify_language,
    core_lookup,
    StructuredError,
    ErrorCode,
    SEC_LANGUAGE_CHECK,
    SEC_LANGUAGES,
)


def _make_config(enabled=True, languages="en, de", min_char_length=4, confidence_threshold=0.60, action="prompt"):
    config = configparser.ConfigParser()
    config.add_section(SEC_LANGUAGE_CHECK)
    config.set(SEC_LANGUAGE_CHECK, "enabled", str(enabled).lower())
    config.set(SEC_LANGUAGE_CHECK, "languages", languages)
    config.set(SEC_LANGUAGE_CHECK, "min_char_length", str(min_char_length))
    config.set(SEC_LANGUAGE_CHECK, "confidence_threshold", str(confidence_threshold))
    config.set(SEC_LANGUAGE_CHECK, "action_on_mismatch", action)
    return config


def test_language_check_config_parsing():
    cfg = _make_config(enabled=True, languages="en, de", min_char_length=5, confidence_threshold=0.75, action="block")
    parsed = LanguageCheckConfig.from_config(cfg)
    assert parsed.enabled is True
    assert parsed.languages == ("en", "de")
    assert parsed.min_char_length == 5
    assert parsed.confidence_threshold == 0.75
    assert parsed.action_on_mismatch == "block"


def test_language_check_config_fallback_to_languages_section():
    cfg = configparser.ConfigParser()
    cfg.add_section(SEC_LANGUAGES)
    cfg.set(SEC_LANGUAGES, "en_prompt", "English Prompt")
    cfg.set(SEC_LANGUAGES, "de_prompt", "German Prompt")
    parsed = LanguageCheckConfig.from_config(cfg)
    assert parsed.enabled is True
    assert set(parsed.languages) == {"en", "de"}


def test_verify_language_disabled_zero_cost():
    cfg = _make_config(enabled=False)
    # Even with obvious German text and expected English, if disabled, it must immediately match
    result = verify_language("Das ist ein Test", "en", cfg)
    assert result.is_match is True
    assert result.action == "proceed"
    assert result.detected_lang is None


def test_verify_language_bypass():
    cfg = _make_config(enabled=True)
    result = verify_language("Das ist ein schönes deutsches Haus", "en", cfg, bypass=True)
    assert result.is_match is True
    assert result.action == "proceed"


def test_verify_language_min_char_length():
    cfg = _make_config(enabled=True, min_char_length=10)
    result = verify_language("Hi there", "de", cfg)
    assert result.is_match is True
    assert result.action == "proceed"
    assert "below minimum threshold" in result.message


def test_verify_language_matching_german():
    cfg = _make_config(enabled=True)
    result = verify_language("Das ist ein schönes Haus in Berlin", "de", cfg)
    assert result.is_match is True
    assert result.detected_lang == "de"
    assert result.expected_lang == "de"
    assert result.confidence >= 0.60
    assert result.action == "proceed"


def test_verify_language_matching_english():
    cfg = _make_config(enabled=True)
    result = verify_language("The quick brown fox jumps over the lazy dog", "en", cfg)
    assert result.is_match is True
    assert result.detected_lang == "en"
    assert result.expected_lang == "en"
    assert result.confidence >= 0.60
    assert result.action == "proceed"


def test_verify_language_mismatch_detected_prompt():
    cfg = _make_config(enabled=True, action="prompt")
    result = verify_language("Das ist ein schönes deutsches Haus", "en", cfg)
    assert result.is_match is False
    assert result.detected_lang == "de"
    assert result.expected_lang == "en"
    assert result.action == "prompt"
    assert "Language mismatch detected" in result.message


def test_verify_language_mismatch_detected_block():
    cfg = _make_config(enabled=True, action="block")
    result = verify_language("This is a lovely English sentence for testing", "de", cfg)
    assert result.is_match is False
    assert result.detected_lang == "en"
    assert result.expected_lang == "de"
    assert result.action == "block"


def test_verify_language_mismatch_detected_warn():
    cfg = _make_config(enabled=True, action="warn")
    result = verify_language("This is a lovely English sentence for testing", "de", cfg)
    assert result.is_match is False
    assert result.detected_lang == "en"
    assert result.expected_lang == "de"
    assert result.action == "warn"


def test_core_lookup_raises_language_mismatch():
    cfg = _make_config(enabled=True, action="prompt")
    cfg.add_section(SEC_LANGUAGES)
    cfg.set(SEC_LANGUAGES, "en_prompt", "English Prompt")

    with pytest.raises(StructuredError) as exc_info:
        core_lookup(
            text="Das ist ein wunderbares deutsches Haus",
            language="en",
            config=cfg,
            resolved_paths={},
            goldendict={"format": "html", "sections": ["source"], "lemma_columns": ["lemma"], "run_intellifiller": False},
            bypass_lang_check=False,
        )
    assert exc_info.value.error_code == ErrorCode.LANGUAGE_MISMATCH
    assert exc_info.value.details["detected_language"] == "de"
    assert exc_info.value.details["expected_language"] == "en"


def test_core_lookup_passes_when_bypassed():
    cfg = _make_config(enabled=True, action="prompt")
    cfg.add_section(SEC_LANGUAGES)
    cfg.set(SEC_LANGUAGES, "en_prompt", "English Prompt")

    # With bypass_lang_check=True, verify_language will not raise LANGUAGE_MISMATCH.
    # It will proceed past the verification gate.
    with patch("kardenwort_desk.run_lookup_flow") as mock_flow:
        from pathlib import Path
        mock_flow.return_value = ([], ["WordSource"], [["test"]], "test translation", Path("results/test.tsv"))
        res = core_lookup(
            text="Das ist ein wunderbares deutsches Haus",
            language="en",
            config=cfg,
            resolved_paths={"kardenwort_workspace": Path(".")},
            goldendict={"format": "html", "sections": ["source"], "lemma_columns": ["lemma"], "run_intellifiller": False},
            bypass_lang_check=True,
        )
        assert res["language"] == "en"


# ===================================================================================
# Matrix Test Suites for Language Verification
# ===================================================================================

@pytest.mark.parametrize(
    "text, expected_lang, enabled, bypass, min_char_length, action, expected_is_match, expected_action",
    [
        # Disabled or Bypassed Matrix combinations -> always match & proceed
        ("Das ist ein Test", "en", False, False, 4, "prompt", True, "proceed"),
        ("Das ist ein Test", "en", False, True, 4, "prompt", True, "proceed"),
        ("Das ist ein Test", "en", True, True, 4, "prompt", True, "proceed"),
        ("The quick brown fox", "de", True, True, 4, "block", True, "proceed"),
        # Short text threshold boundary matrix
        ("Ja", "en", True, False, 4, "prompt", True, "proceed"),
        ("Hi", "de", True, False, 4, "prompt", True, "proceed"),
        ("Das ist ein deutsches Haus", "en", True, False, 100, "prompt", True, "proceed"),
        # Matching language matrix
        ("Das ist ein wunderbares deutsches Haus in Berlin", "de", True, False, 4, "prompt", True, "proceed"),
        ("The quick brown fox jumps over the lazy dog in London", "en", True, False, 4, "prompt", True, "proceed"),
        # Mismatch with distinct actions matrix
        ("Das ist ein wunderbares deutsches Haus in Berlin", "en", True, False, 4, "prompt", False, "prompt"),
        ("Das ist ein wunderbares deutsches Haus in Berlin", "en", True, False, 4, "block", False, "block"),
        ("Das ist ein wunderbares deutsches Haus in Berlin", "en", True, False, 4, "warn", False, "warn"),
        ("The quick brown fox jumps over the lazy dog in London", "de", True, False, 4, "prompt", False, "prompt"),
        ("The quick brown fox jumps over the lazy dog in London", "de", True, False, 4, "block", False, "block"),
        ("The quick brown fox jumps over the lazy dog in London", "de", True, False, 4, "warn", False, "warn"),
    ],
)
def test_verify_language_matrix(
    text, expected_lang, enabled, bypass, min_char_length, action, expected_is_match, expected_action
):
    cfg = _make_config(
        enabled=enabled,
        languages="en, de",
        min_char_length=min_char_length,
        confidence_threshold=0.60,
        action=action,
    )
    result = verify_language(text, expected_lang, cfg, bypass=bypass)
    assert result.is_match == expected_is_match
    assert result.action == expected_action


@pytest.mark.parametrize(
    "action, bypass, expect_error",
    [
        ("prompt", False, True),
        ("block", False, True),
        ("warn", False, False),
        ("prompt", True, False),
        ("block", True, False),
        ("warn", True, False),
    ],
)
def test_core_lookup_action_matrix(action, bypass, expect_error):
    cfg = _make_config(enabled=True, action=action)
    cfg.add_section(SEC_LANGUAGES)
    cfg.set(SEC_LANGUAGES, "en_prompt", "English Prompt")

    from pathlib import Path

    with patch("kardenwort_desk.run_lookup_flow") as mock_flow:
        mock_flow.return_value = ([], ["WordSource"], [["test"]], "test translation", Path("results/test.tsv"))

        if expect_error:
            with pytest.raises(StructuredError) as exc_info:
                core_lookup(
                    text="Das ist ein schönes deutsches Haus in Berlin",
                    language="en",
                    config=cfg,
                    resolved_paths={"kardenwort_workspace": Path(".")},
                    goldendict={"format": "html", "sections": ["source"], "lemma_columns": ["lemma"], "run_intellifiller": False},
                    bypass_lang_check=bypass,
                )
            assert exc_info.value.error_code == ErrorCode.LANGUAGE_MISMATCH
        else:
            res = core_lookup(
                text="Das ist ein schönes deutsches Haus in Berlin",
                language="en",
                config=cfg,
                resolved_paths={"kardenwort_workspace": Path(".")},
                goldendict={"format": "html", "sections": ["source"], "lemma_columns": ["lemma"], "run_intellifiller": False},
                bypass_lang_check=bypass,
            )
            assert res["language"] == "en"
