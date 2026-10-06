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


def test_verify_language_russian_detection():
    cfg = _make_config(enabled=True, action="prompt")
    res = verify_language(
        "Это прекрасный солнечный день в Москве и Санкт-Петербурге",
        expected_lang="en",
        config=cfg,
    )
    assert not res.is_match
    assert res.detected_lang == "ru"
    assert res.action == "prompt"


def test_verify_language_undetermined_fallback():
    cfg = _make_config(enabled=True, action="prompt", confidence_threshold=0.99)
    # A string of digits/symbols with length > min_char_length that yields low/zero language confidence
    res = verify_language(
        "1234567890 0987654321 1234567890 0987654321",
        expected_lang="en",
        config=cfg,
    )
    assert not res.is_match
    assert res.detected_lang == "und"
    assert res.action == "prompt"


def test_verify_language_expected_und_always_matches():
    cfg = _make_config(enabled=True, action="prompt")
    res = verify_language(
        "Любой текст на любом языке или 123456789",
        expected_lang="und",
        config=cfg,
    )
    assert res.is_match
    assert res.detected_lang == "und"
    assert res.action == "proceed"


def test_core_lookup_und_language():
    from pathlib import Path

    cfg = _make_config(enabled=True, action="prompt")
    cfg.add_section(SEC_LANGUAGES)

    with patch("kardenwort_desk.run_lookup_flow") as mock_flow:
        mock_flow.return_value = ([], ["WordSource"], [["тест"]], "перевод", Path("results/test.und.tsv"))
        res = core_lookup(
            text="Это произвольный текст для проверки",
            language="und",
            config=cfg,
            resolved_paths={"kardenwort_workspace": Path(".")},
            goldendict={"format": "html", "sections": ["source"], "lemma_columns": ["lemma"], "run_intellifiller": False},
        )
        assert res["language"] == "und"
        assert mock_flow.called


def test_generate_und_tsv(tmp_path):
    from kardenwort_desk import generate_und_tsv

    mapping = {
        "fields": {
            "WordSource": "",
            "WordSourceInflectedForm": "",
            "SentenceSource": "",
            "DeskSelected": "",
        }
    }
    cfg = _make_config(enabled=True)
    out_tsv = tmp_path / "test.und.tsv"
    sample_text = "Привет, мир! Это тестовый текст."

    generate_und_tsv(sample_text, "single", out_tsv, mapping, cfg)
    assert out_tsv.exists()

    content = out_tsv.read_text(encoding="utf-8")
    assert "Generated by Kardenwort Desk (und)" in content
    assert "Привет" in content
    assert "мир" in content
    assert "Это" in content


def test_render_verify_language_html_foreign_dropdown_label():
    from kardenwort_desk import render_verify_language_html

    # Case 1: Detected Russian (outside en/de) -> explicit informative label
    mismatch_ru = {
        "is_mismatch": True,
        "detected_language": "ru",
        "expected_language": "en",
        "detected_name": "Russian",
        "expected_name": "English",
        "session_zid": "20261006123456",
    }
    html_ru = render_verify_language_html(mismatch_ru)
    assert 'Undefined / Generic (und) - Process Russian (ru) without lemmatization' in html_ru
    assert 'value="und" selected="selected"' in html_ru

    # Case 2: Detected German (supported study language) -> clean standard label
    mismatch_de = {
        "is_mismatch": True,
        "detected_language": "de",
        "expected_language": "en",
        "detected_name": "German",
        "expected_name": "English",
        "session_zid": "20261006123457",
    }
    html_de = render_verify_language_html(mismatch_de)
    assert '<option value="und">Undefined / Generic (und)</option>' in html_de
    assert 'value="de" selected="selected"' in html_de


def test_und_session_enrichment_guard(tmp_path, monkeypatch):
    import kardenwort_desk

    results_dir = tmp_path / "results"
    results_dir.mkdir()

    cfg = configparser.ConfigParser()
    cfg.add_section(kardenwort_desk.SEC_ENVIRONMENT)
    cfg.set(kardenwort_desk.SEC_ENVIRONMENT, "kardenwort_python", "python")
    cfg.set(kardenwort_desk.SEC_ENVIRONMENT, "kardenwort_workspace", str(tmp_path))
    cfg.add_section(kardenwort_desk.SEC_LANGUAGES)
    cfg.set(kardenwort_desk.SEC_LANGUAGES, "en_prompt", "prompt_en")
    cfg.set(kardenwort_desk.SEC_LANGUAGES, "de_prompt", "prompt_de")
    # No und_prompt
    cfg.add_section(kardenwort_desk.SEC_PIPELINE)
    cfg.set(kardenwort_desk.SEC_PIPELINE, "lemma_reprocess_provider", "intellifiller")
    cfg.add_section(kardenwort_desk.SEC_TRIGGERS)
    cfg.set(kardenwort_desk.SEC_TRIGGERS, "run_lemma_enrichment", "auto")
    cfg.set(kardenwort_desk.SEC_TRIGGERS, "run_lemma_base_translation", "manual")
    cfg.set(kardenwort_desk.SEC_TRIGGERS, "run_text_translation", "manual")
    cfg.add_section(kardenwort_desk.SEC_RENDERING)
    cfg.set(kardenwort_desk.SEC_RENDERING, "auto_inject_updates", "false")
    cfg.add_section(kardenwort_desk.SEC_SETTINGS)
    cfg.set(kardenwort_desk.SEC_SETTINGS, "default_language", "en")

    headless_calls = []
    monkeypatch.setattr(kardenwort_desk, "run_headless_intellifiller", lambda *args, **kwargs: headless_calls.append(args))
    monkeypatch.setattr(kardenwort_desk, "run_progressive_worker_async", lambda *args, **kwargs: headless_calls.append(args))

    anki_mapping = tmp_path / "anki-mapping.ini"
    anki_mapping.write_text("[fields]\nWordSource\nDeskSelected\n", encoding="utf-8")

    tsv_path = results_dir / "20261006123456.und.tsv"
    tsv_path.write_text("# Test\nWordSource\tDeskSelected\nпривет\t1\n", encoding="utf-8")

    resolved_paths = {
        "kardenwort_workspace": tmp_path,
        "results_dir": results_dir,
        "base_dir": tmp_path,
        "anki_mapping_file": anki_mapping,
    }

    # Should run without error and without scheduling intellifiller
    out = kardenwort_desk._run_render_flow_impl(
        text="привет",
        language="und",
        zid="20261006123456",
        text_mode="single",
        config=cfg,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_path),
        spawn_children=False,
    )
    assert out is not None
    assert len(headless_calls) == 0


