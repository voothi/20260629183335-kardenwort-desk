import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile
import configparser

from kardenwort_desk import (
    ProvenanceString,
    ProvenanceDict,
    format_provenance_tooltip,
    translate_text,
    translate_source_text,
    translate_lemmas_fast_path,
    find_wordfill_match,
    format_update_rows_dict,
    safe_write_update_js,
    _run_render_flow_impl,
)

class TestTranslationProvenance(unittest.TestCase):
    def test_format_provenance_tooltip(self):
        self.assertEqual(format_provenance_tooltip("live:argos"), "Translated via Argos (offline)")
        self.assertEqual(format_provenance_tooltip("live:google"), "Translated via Google")
        self.assertEqual(format_provenance_tooltip("live:deepl"), "Translated via DeepL")
        self.assertEqual(format_provenance_tooltip("live:lingva"), "Translated via Lingva")
        self.assertEqual(format_provenance_tooltip("corpus:wordfill"), "Pre-filled from Corpus (WordFill)")
        self.assertEqual(format_provenance_tooltip("corpus:wordfill:20260101120000"), "Pre-filled from Corpus (ZID: 20260101120000)")
        self.assertEqual(format_provenance_tooltip("cached:sqlite"), "Loaded from session cache (SQLite)")
        self.assertEqual(format_provenance_tooltip("fallback:google"), "Translated via fallback (google)")
        self.assertEqual(format_provenance_tooltip(""), "")
        self.assertEqual(format_provenance_tooltip(None), "")

    def test_provenance_string_metadata(self):
        s = ProvenanceString("дерево", provenance="live:google")
        self.assertEqual(s, "дерево")
        self.assertEqual(s.provenance, "live:google")
        self.assertEqual(s._provenance, "live:google")
        stripped = s.strip()
        self.assertEqual(stripped.provenance, "live:google")

    def test_provenance_dict_metadata(self):
        d = ProvenanceDict({"apple": "яблоко"}, provenance="live:argos")
        self.assertEqual(d["apple"], "яблоко")
        self.assertEqual(d.provenance, "live:argos")

    @patch("kardenwort_desk.dispatch_single_provider")
    def test_translate_text_failover_provenance(self, mock_dispatch):
        def side_effect(provider, text, source, target, config, resolved_paths, zid=None, trace_id=None):
            if provider == "argos":
                raise RuntimeError("Argos offline model not found")
            if provider == "google":
                return "яблоко"
            raise ValueError(f"Unknown {provider}")

        mock_dispatch.side_effect = side_effect

        config = configparser.ConfigParser()
        config.add_section("pipeline")
        config.set("pipeline", "text_base_provider", "argos, google")
        config.set("pipeline", "translation_strategy", "chain")
        resolved_paths = {}

        res = translate_text("apple", "en", "ru", config, resolved_paths, provider="argos, google")
        self.assertEqual(res, "яблоко")
        self.assertEqual(getattr(res, "provenance", None), "live:google")

    @patch("kardenwort_desk.translate_text")
    def test_translate_source_text_failover_provenance(self, mock_trans):
        mock_trans.return_value = ProvenanceString("яблоко", provenance="live:google")
        config = configparser.ConfigParser()
        config.add_section("pipeline")
        config.set("pipeline", "text_base_provider", "argos, google")
        resolved_paths = {}

        res = translate_source_text("apple", "en", "ru", "single", config, resolved_paths, provider="argos, google")
        self.assertIsInstance(res, ProvenanceDict)
        self.assertEqual(getattr(res, "provenance", None), "live:google")

    @patch("kardenwort_desk.translate_text")
    def test_translate_lemmas_fast_path_provenance(self, mock_trans):
        mock_trans.return_value = ProvenanceString("дом", provenance="live:argos")
        config = MagicMock()
        resolved_paths = {}
        res = translate_lemmas_fast_path(["haus"], "de", "ru", config, resolved_paths, provider="argos")
        self.assertEqual(res["haus"], "дом")
        self.assertEqual(getattr(res, "provenance", None), "live:argos")

    @patch("kardenwort_desk.translate_text")
    def test_translate_lemmas_fast_path_failover_provenance(self, mock_trans):
        mock_trans.return_value = ProvenanceString("дом", provenance="live:google")
        config = configparser.ConfigParser()
        config.add_section("translation")
        config.set("translation", "lemma_batch_size", "15")
        resolved_paths = {}

        res = translate_lemmas_fast_path(["haus"], "de", "ru", config, resolved_paths, provider="argos")
        self.assertEqual(res["haus"], "дом")
        self.assertEqual(getattr(res, "provenance", None), "live:google")

    def test_find_wordfill_match_provenance_tagging(self):
        wordfill_cfg = {
            "enabled": True,
            "corpus_tsv_dir": None,
            "db_path": None,
        }
        # When no matches exist, returns None
        self.assertIsNone(find_wordfill_match("nonexistent_word_xyz", "en", wordfill_cfg))

    def test_format_update_rows_dict_with_provenance(self):
        headers = ["TokenOrder", "WordSource", "WordDestination", "SentenceSourceIndex"]
        role_fields = {
            "lemma": "WordSource",
            "word_translation": "WordDestination",
            "inflected": "WordSource",
            "morphology": "WordSourceMorphology",
            "ipa": "WordSourceIPA",
            "sentence_index": "SentenceSourceIndex"
        }
        data_rows = [
            ["0", "tree", "дерево", "1"],
            ["1", "sky", "небо", "1"]
        ]
        row_provenances = {
            0: "live:argos",
            1: "corpus:wordfill"
        }
        res = format_update_rows_dict(data_rows, headers, role_fields, row_provenances=row_provenances)
        self.assertEqual(res[0]["trans"], "дерево")
        self.assertEqual(res[0]["provenance"], "live:argos")
        self.assertEqual(res[1]["trans"], "небо")
        self.assertEqual(res[1]["provenance"], "corpus:wordfill")

    def test_write_update_js_with_text_and_row_provenance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tsv_path = Path(tmpdir) / "20260830180000-sample.en.tsv"
            tsv_path.write_text("TokenOrder\tWordSource\tWordDestination\n0\ttree\tдерево\n", encoding="utf-8")
            headers = ["TokenOrder", "WordSource", "WordDestination", "SentenceSourceIndex"]
            role_fields = {
                "lemma": "WordSource",
                "word_translation": "WordDestination",
                "inflected": "WordSource",
                "morphology": "WordSourceMorphology",
                "ipa": "WordSourceIPA",
                "sentence_index": "SentenceSourceIndex"
            }
            data_rows = [["0", "tree", "дерево", "1"]]
            row_provenances = {0: "live:argos"}
            
            res_path = safe_write_update_js(
                tsv_path, data_rows, headers, role_fields,
                stage="translated",
                text_provenance="live:argos",
                row_provenances=row_provenances,
                zid="20260830180000"
            )
            self.assertIsNotNone(res_path)
            content = Path(res_path).read_text(encoding="utf-8")
            self.assertIn("live:argos", content)
            self.assertIn("window.receiveUpdate", content)

    @patch("kardenwort_desk.run_progressive_worker_async")
    @patch("kardenwort_desk.translate_source_text")
    def test_render_desk_html_provenance_attributes(self, mock_trans_text, mock_prog):
        mock_trans_text.return_value = {0: "Привет мир"}
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            tsv_path = tmp_path / "20260830180000-hello.en.tsv"
            tsv_path.write_text("TokenOrder\tWordSource\tWordDestination\tSentenceSourceIndex\n0\thello\tпривет\t1\n", encoding="utf-8")
            
            mapping_path = tmp_path / "mapping.ini"
            mapping_path.write_text("[roles]\nlemma=WordSource\nword_translation=WordDestination\nsentence_destination=SentenceDestination\nsentence_index=SentenceSourceIndex\n[fields]\nTokenOrder=\nWordSource=\nWordDestination=\nSentenceSourceIndex=\nSentenceDestination=\n", encoding="utf-8")

            config = configparser.ConfigParser()
            config.add_section("pipeline")
            config.set("pipeline", "text_base_provider", "argos")
            config.set("pipeline", "lemma_base_provider", "argos")
            config.add_section("rendering")
            config.set("rendering", "display_mode", "monolithic")
            config.add_section("settings")
            config.set("settings", "default_target_language", "ru")
            config.add_section("languages")
            config.set("languages", "en_prompt", "standard")
            config.add_section("storage")
            config.set("storage", "backend", "sqlite")
            config.set("storage", "sqlite_path", str(tmp_path / "test.db"))

            from kardenwort_db import KardenwortDB
            db = KardenwortDB(db_path=tmp_path / "test.db")
            db.run_migrations()
            db.insert_session({
                "zid": "20260830180000",
                "slug": "hello",
                "source_language": "en",
                "source_raw_text": "Hello world",
            })
            db.insert_sentence({
                "session_zid": "20260830180000",
                "sentence_index": 1,
                "sentence_source": "Hello world",
                "sentence_destination": "Привет мир",
                "text_provenance": "live:argos",
            })
            db.insert_words([
                {
                    "session_zid": "20260830180000",
                    "sentence_index": 1,
                    "token_order": 0,
                    "quotation": "hello",
                    "lemma": "hello",
                    "word_destination": "привет",
                    "word_provenance": "live:argos",
                }
            ])

            resolved_paths = {
                "kardenwort_workspace": tmp_path,
                "results_dir": tmp_path,
                "anki_mapping_file": mapping_path,
                "sqlite_db_path": tmp_path / "test.db",
                "storage_backend": "sqlite",
            }

            html = _run_render_flow_impl(
                text="Hello world",
                language="en",
                zid="20260830180000",
                text_mode="single",
                config=config,
                resolved_paths=resolved_paths,
                tsv_path=tsv_path,
            )

            # Check that container or table cells have data-provenance
            self.assertIn('id="translation-container"', html)
            self.assertIn('data-provenance="', html)
            self.assertIn('data-provenance="live:argos"', html)
            self.assertIn('title="Translated via Argos (offline)"', html)

    def test_format_update_rows_dict_token_order_priority(self):
        headers = ["TokenOrder", "WordSource", "WordDestination", "SentenceSourceIndex"]
        role_fields = {
            "lemma": "WordSource",
            "word_translation": "WordDestination",
            "inflected": "WordSource",
            "morphology": "WordSourceMorphology",
            "ipa": "WordSourceIPA",
            "sentence_index": "SentenceSourceIndex"
        }
        # Simulate reordered rows (e.g. sorted by frequency where row 0 is token 5 and row 1 is token 2)
        data_rows = [
            ["5", "tree", "дерево", "1"],
            ["2", "apple", "яблоко", "1"]
        ]
        row_provenances = {
            "5": "live:argos",
            "2": "corpus:wordfill",
            0: "wrong:index"
        }
        res = format_update_rows_dict(data_rows, headers, role_fields, row_provenances=row_provenances)
        # TokenOrder matching takes priority over raw row index
        self.assertEqual(res[0]["provenance"], "live:argos")
        self.assertEqual(res[1]["provenance"], "corpus:wordfill")

    def test_terminal_finished_payload_forwards_row_provenance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tsv_path = Path(tmpdir) / "20260830180000-sample.en.tsv"
            tsv_path.write_text("TokenOrder\tWordSource\tWordDestination\n0\ttree\tдерево\n", encoding="utf-8")
            headers = ["TokenOrder", "WordSource", "WordDestination"]
            role_fields = {
                "lemma": "WordSource",
                "word_translation": "WordDestination",
                "inflected": "WordSource",
                "sentence_index": "SentenceSourceIndex"
            }
            data_rows = [["0", "tree", "дерево"]]
            row_prov = {"0": "live:argos"}
            res_path = safe_write_update_js(
                tsv_path, data_rows, headers, role_fields,
                stage="finished",
                status="success",
                row_provenances=row_prov,
                zid="20260830180000"
            )
            self.assertIsNotNone(res_path)
            content = Path(res_path).read_text(encoding="utf-8")
            self.assertIn('"row_provenances": {"0": "live:argos"}', content)
            self.assertIn('"stage": "finished"', content)

    @patch("kardenwort_controller.translate_lemmas_fast_path")
    def test_retry_session_rows_returns_and_attaches_provenance(self, mock_fast_path):
        from kardenwort_controller import SessionArbiter
        mock_fast_path.return_value = ProvenanceDict({"apple": "яблоко"}, provenance="live:argos")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            mapping_path = tmp_p / "mapping.ini"
            mapping_path.write_text("[roles]\nlemma=WordSource\nword_translation=WordDestination\nsentence_destination=SentenceDestination\n", encoding="utf-8")
            
            tsv_path = tmp_p / "20260830195500.en.tsv"
            tsv_path.write_text("TokenOrder\tWordSource\tWordDestination\n0\tapple\t\n", encoding="utf-8")
            
            config = configparser.ConfigParser()
            config.add_section("pipeline")
            config.set("pipeline", "lemma_base_provider", "argos")
            config.add_section("settings")
            config.set("settings", "default_language", "en")
            config.set("settings", "default_target_language", "ru")
            config.add_section("paths")
            config.set("paths", "results_dir", str(tmp_p))
            
            resolved_paths = {
                "anki_mapping_file": mapping_path,
                "results_dir": tmp_p,
            }
            
            arbiter = SessionArbiter(config, resolved_paths)
            res = arbiter.retry_session_rows(
                session_zid="20260830195500",
                row_ids=[0]
            )
            
            self.assertEqual(res["status"], "success")
            self.assertIn("row_provenances", res)
            self.assertEqual(res["row_provenances"].get(0) or res["row_provenances"].get("0"), "live:argos")
            self.assertEqual(res["rows"][0]["provenance"], "live:argos")

    def test_format_update_rows_dict_pre_migration_unassigned_does_not_emit_cached_sqlite(self):
        from kardenwort_desk import format_update_rows_dict
        headers = ["TokenOrder", "WordSource", "WordDestination"]
        role_fields = {"lemma": "WordSource", "word_translation": "WordDestination"}
        data_rows = [
            ["0", "run", "бег"],
            ["1", "walk", ""],
            ["2", "jump", '<span class="skeleton-loader">Argos...</span>'],
            ["3", "fly", '<button class="btn-retry-cell">Retry</button>'],
        ]
        # Without any row_provenances passed (pre-migration / unassigned)
        rows_dict = format_update_rows_dict(data_rows, headers, role_fields)
        self.assertIsNone(rows_dict[0].get("provenance"))
        self.assertIsNone(rows_dict[1].get("provenance"))
        self.assertIsNone(rows_dict[2].get("provenance"))
        self.assertIsNone(rows_dict[3].get("provenance"))

    def test_session_status_fallback_attaches_cached_sqlite_provenance(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            config = configparser.ConfigParser()
            config.add_section("rendering")
            config.set("rendering", "results_dir", str(tmp_path))
            config.add_section("pipeline")
            config.set("pipeline", "lemma_base_provider", "argos")
            config.add_section("settings")
            config.set("settings", "default_target_language", "ru")
            resolved_paths = {
                "results_dir": tmp_path,
                "anki_mapping_file": tmp_path / "mapping.ini",
            }
            from kardenwort_controller import SessionArbiter
            arbiter = SessionArbiter(config, resolved_paths)
            headers = ["TokenOrder", "WordSource", "WordDestination", "SentenceSourceIndex", "SentenceDestination"]
            data_rows = [["0", "run", "бег", "1", "Текст"]]
            role_fields = {"lemma": "WordSource", "word_translation": "WordDestination", "sentence_destination": "SentenceDestination"}
            from kardenwort_desk import format_update_rows_dict
            fallback_row_provs = {}
            col_word_dest = headers.index(role_fields['word_translation'])
            for r_i, r in enumerate(data_rows):
                if len(r) > col_word_dest and r[col_word_dest].strip():
                    fallback_row_provs[r_i] = "cached:sqlite"
            rows_dict = format_update_rows_dict(data_rows, headers, role_fields, row_provenances=fallback_row_provs)
            self.assertEqual(rows_dict[0].get("provenance"), "cached:sqlite")

def test_progressive_and_terminal_provenance_playwright(page, tmp_path):
    tsv_path = tmp_path / "20260830180000-hello.en.tsv"
    tsv_path.write_text("TokenOrder\tWordSource\tWordDestination\tSentenceSourceIndex\n0\thello\tпривет\t1\n", encoding="utf-8")
    
    mapping_path = tmp_path / "mapping.ini"
    mapping_path.write_text("[roles]\nlemma=WordSource\nword_translation=WordDestination\nsentence_destination=SentenceDestination\n", encoding="utf-8")

    config = configparser.ConfigParser()
    config.add_section("pipeline")
    config.set("pipeline", "text_base_provider", "argos")
    config.set("pipeline", "lemma_base_provider", "argos")
    config.add_section("rendering")
    config.set("rendering", "display_mode", "monolithic")
    config.add_section("settings")
    config.set("settings", "default_target_language", "ru")

    resolved_paths = {
        "kardenwort_workspace": tmp_path,
        "results_dir": tmp_path,
        "anki_mapping_file": mapping_path,
    }

    with patch("kardenwort_desk.translate_source_text", return_value={0: "Привет мир"}), patch("kardenwort_desk.run_progressive_worker_async"):
        html = _run_render_flow_impl(
            text="Hello world",
            language="en",
            zid="20260830180000",
            text_mode="single",
            config=config,
            resolved_paths=resolved_paths,
            tsv_path=tsv_path,
        )

    page.set_content(html)

    # 1. Initial pre-migration/empty provenance has no data-provenance attribute
    assert page.locator("td.col-translation").get_attribute("data-provenance") is None

    # 2. Progressive translation update arrives with live:argos
    page.evaluate("""() => {
        window.AppState.applyDeltas({
            stage: 'translated',
            rows: {
                '0': { trans: 'новое_слово', token_order: '0' }
            },
            row_provenances: {
                '0': 'live:argos'
            }
        });
    }""")

    assert page.locator("td.col-translation").get_attribute("data-provenance") == "live:argos"
    assert page.locator("td.col-translation").get_attribute("title") == "Translated via Argos (offline)"
    assert page.locator("td.col-translation .scrollable-cell").text_content() == "новое_слово"

    # 3. Terminal finished event preserves provenance and tooltip
    page.evaluate("""() => {
        window.AppState.applyDeltas({
            stage: 'finished',
            status: 'success'
        });
    }""")

    assert page.locator("td.col-translation").get_attribute("data-provenance") == "live:argos"
    assert page.locator("td.col-translation").get_attribute("title") == "Translated via Argos (offline)"

def test_translation_container_word_spans_and_skeleton_proportions(page, tmp_path):
    tsv_path = tmp_path / "20260830180001-sample.en.tsv"
    tsv_path.write_text("TokenOrder\tWordSource\tWordDestination\tSentenceSourceIndex\n0\thello\tпривет\t1\n", encoding="utf-8")
    
    mapping_path = tmp_path / "mapping.ini"
    mapping_path.write_text("[roles]\nlemma=WordSource\nword_translation=WordDestination\nsentence_destination=SentenceDestination\n", encoding="utf-8")

    config = configparser.ConfigParser()
    config.add_section("pipeline")
    config.set("pipeline", "text_base_provider", "argos")
    config.set("pipeline", "lemma_base_provider", "argos")
    config.add_section("rendering")
    config.set("rendering", "display_mode", "progressive")
    config.add_section("settings")
    config.set("settings", "default_target_language", "ru")

    resolved_paths = {
        "kardenwort_workspace": tmp_path,
        "results_dir": tmp_path,
        "anki_mapping_file": mapping_path,
    }

    # 1. Test skeleton loader proportions in progressive pending state
    with patch("kardenwort_desk.run_progressive_worker_async"):
        html = _run_render_flow_impl(
            text="Hello world",
            language="en",
            zid="20260830180001",
            text_mode="single",
            config=config,
            resolved_paths=resolved_paths,
            tsv_path=tsv_path,
        )

    assert "#translation-container .skeleton-loader" in html
    assert "min-height: 1.6em" in html

    page.set_content(html)
    skel = page.locator("#translation-container .skeleton-loader")
    assert skel.count() == 1
    assert "Argos..." in skel.text_content()

    # 2. Receive text translation update with provenance
    page.evaluate("""() => {
        window.receiveUpdate({
            stage: 'translated_text',
            status: 'success',
            translatedText: 'Привет мир',
            textProvenance: 'live:argos',
            rows: {}
        });
    }""")

    tc = page.locator("#translation-container")
    assert tc.get_attribute("data-provenance") == "live:argos"
    assert tc.get_attribute("title") == "Translated via Argos (offline)"

    # Child word spans must also receive the provenance and title attributes
    word_spans = tc.locator("span.word").all()
    if word_spans:
        for span in word_spans:
            assert span.get_attribute("data-provenance") == "live:argos"
            assert span.get_attribute("title") == "Translated via Argos (offline)"

def test_single_sentence_tokenization_and_retry_tooltips(page, tmp_path):
    tsv_path = tmp_path / "20260830180002-sample.en.tsv"
    tsv_path.write_text("TokenOrder\tWordSource\tWordDestination\tSentenceSourceIndex\n0\thello\tпривет\t1\n", encoding="utf-8")
    
    mapping_path = tmp_path / "mapping.ini"
    mapping_path.write_text("[roles]\nlemma=WordSource\nword_translation=WordDestination\nsentence_destination=SentenceDestination\n", encoding="utf-8")

    config = configparser.ConfigParser()
    config.add_section("pipeline")
    config.set("pipeline", "text_base_provider", "argos")
    config.set("pipeline", "lemma_base_provider", "argos")
    config.add_section("rendering")
    config.set("rendering", "display_mode", "monolithic")
    config.add_section("settings")
    config.set("settings", "default_target_language", "ru")

    resolved_paths = {
        "kardenwort_workspace": tmp_path,
        "results_dir": tmp_path,
        "anki_mapping_file": mapping_path,
    }

    with patch("kardenwort_desk.translate_source_text", return_value={0: "Привет мир"}), patch("kardenwort_desk.run_progressive_worker_async"):
        html = _run_render_flow_impl(
            text="Hello world",
            language="en",
            zid="20260830180002",
            text_mode="single",
            config=config,
            resolved_paths=resolved_paths,
            tsv_path=tsv_path,
        )

    page.set_content(html)
    
    # Send retry-like update with retried lemma provenance and single-sentence translation
    page.evaluate("""() => {
        window.receiveUpdate({
            stage: 'translated',
            status: 'success',
            translatedText: 'Привет мир',
            textProvenance: 'live:argos',
            rows: {
                '0': { trans: 'яблоко', provenance: 'live:argos', token_order: '0' }
            },
            row_provenances: {
                '0': 'live:argos'
            }
        });
    }""")

    # Check that retried table cell receives live:argos tooltip
    cell = page.locator("td.col-translation")
    assert cell.get_attribute("data-provenance") == "live:argos"
    assert cell.get_attribute("title") == "Translated via Argos (offline)"

    # Check that single-sentence span.word elements receive tooltip
    tc = page.locator("#translation-container")
    assert tc.get_attribute("data-provenance") == "live:argos"
    assert tc.get_attribute("title") == "Translated via Argos (offline)"
    spans = tc.locator("span.word").all()
    assert len(spans) > 0
    for s in spans:
        assert s.get_attribute("data-provenance") == "live:argos"
        assert s.get_attribute("title") == "Translated via Argos (offline)"

def test_sqlite_cached_lemma_provenance_tooltips(page, tmp_path):
    tsv_path = tmp_path / "20260830201500-sample.en.tsv"
    tsv_path.write_text("TokenOrder\tWordSource\tWordDestination\tSentenceSourceIndex\n0\trun\tбег\t1\n1\tyou\t\t1\n", encoding="utf-8")
    
    mapping_path = tmp_path / "mapping.ini"
    mapping_path.write_text("[roles]\nlemma=WordSource\nword_translation=WordDestination\nsentence_destination=SentenceDestination\n", encoding="utf-8")

    config = configparser.ConfigParser()
    config.add_section("pipeline")
    config.set("pipeline", "text_base_provider", "argos")
    config.set("pipeline", "lemma_base_provider", "argos")
    config.add_section("rendering")
    config.set("rendering", "display_mode", "progressive")
    config.add_section("settings")
    config.set("settings", "default_target_language", "ru")
    config.add_section("languages")
    config.set("languages", "en_prompt", "standard")

    config.add_section("storage")
    config.set("storage", "backend", "sqlite")
    config.set("storage", "sqlite_path", str(tmp_path / "test.db"))

    from kardenwort_db import KardenwortDB
    db = KardenwortDB(db_path=tmp_path / "test.db")
    db.run_migrations()
    db.insert_session({
        "zid": "20260830201500",
        "slug": "sample",
        "source_language": "en",
        "source_raw_text": "run you",
    })
    db.insert_sentence({
        "session_zid": "20260830201500",
        "sentence_index": 1,
        "sentence_source": "run you",
        "sentence_destination": "Текст",
        "text_provenance": "live:argos",
    })
    db.insert_words([
        {
            "session_zid": "20260830201500",
            "sentence_index": 1,
            "token_order": 0,
            "quotation": "run",
            "lemma": "run",
            "word_destination": "бег",
            "word_provenance": "live:argos",
        },
        {
            "session_zid": "20260830201500",
            "sentence_index": 1,
            "token_order": 1,
            "quotation": "you",
            "lemma": "you",
            "word_destination": "",
        },
    ])

    resolved_paths = {
        "kardenwort_workspace": tmp_path,
        "results_dir": tmp_path,
        "anki_mapping_file": mapping_path,
        "sqlite_db_path": tmp_path / "test.db",
        "storage_backend": "sqlite",
    }

    with patch("kardenwort_desk.translate_source_text", return_value={0: "Текст"}), patch("kardenwort_desk.run_progressive_worker_async"):
        html = _run_render_flow_impl(
            text="run you",
            language="en",
            zid="20260830201500",
            text_mode="single",
            config=config,
            resolved_paths=resolved_paths,
            tsv_path=tsv_path,
        )

    page.set_content(html)

    # Initial render should attribute stored translation "бег" to live:argos
    cells = page.locator("td.col-translation").all()
    assert len(cells) == 2
    assert cells[0].get_attribute("data-provenance") == "live:argos"
    assert cells[0].get_attribute("title") == "Translated via Argos (offline)"

    # When progressive delta arrives with explicit live:argos
    page.evaluate("""() => {
        window.receiveUpdate({
            stage: 'source',
            status: 'success',
            rows: {
                '0': { lemma: 'run', trans: 'бег', token_order: '0', provenance: 'live:argos' },
                '1': { lemma: 'you', trans: '', token_order: '1' }
            },
            row_provenances: {
                '0': 'live:argos'
            }
        });
    }""")

    assert cells[0].get_attribute("data-provenance") == "live:argos"
    assert cells[0].get_attribute("title") == "Translated via Argos (offline)"


def test_progressive_worker_stores_and_reloads_live_provenance(tmp_path):
    """Test 6.1: progressive worker stores live:argos provenance and it is returned unchanged on session reload."""
    from kardenwort_db import KardenwortDB
    from kardenwort_desk import _progressive_worker_stage_translation_impl, get_storage_adapter

    db_path = tmp_path / "desk.db"
    db = KardenwortDB(db_path=db_path)
    db.run_migrations()

    sess_zid = "20260830225801"
    db.insert_session({
        "zid": sess_zid,
        "slug": "live-prov-test",
        "source_language": "en",
        "source_raw_text": "apple tree",
    })
    db.insert_sentence({
        "session_zid": sess_zid,
        "sentence_index": 1,
        "sentence_source": "apple tree",
    })
    db.insert_words([
        {
            "session_zid": sess_zid,
            "sentence_index": 1,
            "token_order": 0,
            "quotation": "apple",
            "lemma": "apple",
        },
        {
            "session_zid": sess_zid,
            "sentence_index": 1,
            "token_order": 1,
            "quotation": "tree",
            "lemma": "tree",
        },
    ])

    mapping_path = tmp_path / "mapping.ini"
    mapping_path.write_text("[roles]\nlemma=WordSource\nword_translation=WordDestination\nsentence_destination=SentenceDestination\nsentence_index=SentenceSourceIndex\n[fields]\nTokenOrder=\nWordSource=\nWordDestination=\nSentenceSourceIndex=\nSentenceDestination=\n", encoding="utf-8")

    config = configparser.ConfigParser()
    config.add_section("pipeline")
    config.set("pipeline", "text_base_provider", "argos")
    config.set("pipeline", "lemma_base_provider", "argos")
    config.add_section("storage")
    config.set("storage", "backend", "sqlite")
    config.set("storage", "sqlite_path", str(db_path))
    config.add_section("settings")
    config.set("settings", "default_language", "en")
    config.set("settings", "default_target_language", "ru")
    config.add_section("languages")
    config.set("languages", "en_prompt", "standard")
    config.add_section("triggers")
    config.set("triggers", "run_text_translation", "auto")
    config.set("triggers", "run_lemma_base_translation", "auto")

    resolved_paths = {
        "results_dir": tmp_path,
        "anki_mapping_file": mapping_path,
        "sqlite_db_path": db_path,
        "storage_backend": "sqlite",
    }
    tsv_path = tmp_path / f"{sess_zid}-live-prov-test.en.tsv"
    headers = ["TokenOrder", "WordSource", "WordDestination", "SentenceSourceIndex", "SentenceDestination"]
    tsv_path.write_text("\t".join(headers) + "\n0\tapple\t\t1\t\n1\ttree\t\t1\t\n", encoding="utf-8")
    role_fields = {
        "lemma": "WordSource",
        "word_translation": "WordDestination",
        "sentence_index": "SentenceSourceIndex",
        "sentence_destination": "SentenceDestination",
    }
    data_rows = [
        ["0", "apple", "", "1", ""],
        ["1", "tree", "", "1", ""],
    ]

    args = MagicMock()
    args.language = "en"
    args.target_lang = "ru"
    args.text_mode = "single"
    args.zid = sess_zid
    args.trace_id = f"{sess_zid}:test"

    with patch("kardenwort_desk.translate_source_text", return_value={0: "яблоня"}), \
         patch("kardenwort_desk.translate_lemmas_fast_path", return_value=ProvenanceDict({"apple": "яблоко", "tree": "дерево"}, provenance="live:argos")):
        _progressive_worker_stage_translation_impl(
            tsv_path=tsv_path,
            args=args,
            config=config,
            resolved_paths=resolved_paths,
            data_rows=data_rows,
            headers=headers,
            role_fields=role_fields,
            zid=sess_zid,
            trace_id=f"{sess_zid}:test",
            row_provenances={},
        )

    # Verify DB persistence of provenance
    sents = db.get_sentences_by_session(sess_zid)
    words = db.get_words_by_session(sess_zid)

    assert len(sents) == 1
    assert sents[0]["text_provenance"] == "live:argos"
    assert len(words) == 2
    assert words[0]["word_provenance"] == "live:argos"
    assert words[1]["word_provenance"] == "live:argos"


def test_progressive_worker_wordfill_stores_and_reloads_corpus_provenance(tmp_path):
    """Test 6.2: session translated via corpus wordfill stores and retrieves corpus:wordfill:<zid> correctly."""
    from kardenwort_db import KardenwortDB
    from kardenwort_desk import _progressive_worker_stage_translation_impl

    db_path = tmp_path / "desk_wf.db"
    db = KardenwortDB(db_path=db_path)
    db.run_migrations()

    sess_zid = "20260830225802"
    db.insert_session({
        "zid": sess_zid,
        "slug": "wf-prov-test",
        "source_language": "en",
        "source_raw_text": "tree",
    })
    db.insert_sentence({
        "session_zid": sess_zid,
        "sentence_index": 1,
        "sentence_source": "tree",
    })
    db.insert_words([
        {
            "session_zid": sess_zid,
            "sentence_index": 1,
            "token_order": 0,
            "quotation": "tree",
            "lemma": "tree",
        }
    ])

    mapping_path = tmp_path / "mapping.ini"
    mapping_path.write_text("[roles]\nlemma=WordSource\nword_translation=WordDestination\nsentence_index=SentenceSourceIndex\n[fields]\nTokenOrder=\nWordSource=\nWordDestination=\nSentenceSourceIndex=\n", encoding="utf-8")

    config = configparser.ConfigParser()
    config.add_section("pipeline")
    config.set("pipeline", "text_base_provider", "google")
    config.set("pipeline", "lemma_base_provider", "google")
    config.add_section("storage")
    config.set("storage", "backend", "sqlite")
    config.set("storage", "sqlite_path", str(db_path))
    config.add_section("settings")
    config.set("settings", "default_language", "en")
    config.set("settings", "default_target_language", "ru")
    config.add_section("languages")
    config.set("languages", "en_prompt", "standard")
    config.add_section("triggers")
    config.set("triggers", "run_text_translation", "manual")
    config.set("triggers", "run_lemma_base_translation", "auto")
    config.add_section("wordfill")
    config.set("wordfill", "enabled", "true")

    resolved_paths = {
        "results_dir": tmp_path,
        "anki_mapping_file": mapping_path,
        "sqlite_db_path": db_path,
        "storage_backend": "sqlite",
    }
    tsv_path = tmp_path / f"{sess_zid}-wf-prov-test.en.tsv"
    headers = ["TokenOrder", "WordSource", "WordDestination", "SentenceSourceIndex"]
    tsv_path.write_text("\t".join(headers) + "\n0\ttree\t\t1\n", encoding="utf-8")
    role_fields = {
        "lemma": "WordSource",
        "word_translation": "WordDestination",
        "sentence_index": "SentenceSourceIndex",
    }
    data_rows = [["0", "tree", "", "1"]]

    args = MagicMock()
    args.language = "en"
    args.target_lang = "ru"
    args.text_mode = "single"
    args.zid = sess_zid
    args.trace_id = f"{sess_zid}:test"

    match_result = {
        "WordDestination": "дерево",
        "_provenance": "corpus:wordfill:20260712134500",
    }

    with patch("kardenwort_desk.find_wordfill_match", return_value=match_result):
        _progressive_worker_stage_translation_impl(
            tsv_path=tsv_path,
            args=args,
            config=config,
            resolved_paths=resolved_paths,
            data_rows=data_rows,
            headers=headers,
            role_fields=role_fields,
            zid=sess_zid,
            trace_id=f"{sess_zid}:test",
            row_provenances={},
        )

    words = db.get_words_by_session(sess_zid)
    assert len(words) == 1
    assert words[0]["word_destination"] == "дерево"
    assert words[0]["word_provenance"] == "corpus:wordfill:20260712134500"


def test_render_session_wordfill_persists_provenance_to_sqlite(tmp_path):
    """Test 6.3: render_session initial WordFill step persists corpus:wordfill:<zid> into SQLite words table."""
    from kardenwort_db import KardenwortDB
    from kardenwort_desk import _run_render_flow_impl

    db_path = tmp_path / "desk_wf_render.db"
    db = KardenwortDB(db_path=db_path)
    db.run_migrations()

    sess_zid = "20260830235901"
    db.insert_session({
        "zid": sess_zid,
        "slug": "render-wf-test",
        "source_language": "en",
        "source_raw_text": "tree",
    })
    db.insert_sentence({
        "session_zid": sess_zid,
        "sentence_index": 1,
        "sentence_source": "tree",
    })
    db.insert_words([
        {
            "session_zid": sess_zid,
            "sentence_index": 1,
            "token_order": 0,
            "quotation": "tree",
            "lemma": "tree",
            "word_destination": "",
        }
    ])

    mapping_path = tmp_path / "mapping.ini"
    mapping_path.write_text("[roles]\nlemma=WordSource\nword_translation=WordDestination\nsentence_destination=SentenceDestination\nsentence_index=SentenceSourceIndex\n[fields]\nTokenOrder=\nWordSource=\nWordDestination=\nSentenceSourceIndex=\nSentenceDestination=\n", encoding="utf-8")

    config = configparser.ConfigParser()
    config.add_section("pipeline")
    config.set("pipeline", "text_base_provider", "google")
    config.set("pipeline", "lemma_base_provider", "google")
    config.add_section("rendering")
    config.set("rendering", "display_mode", "progressive")
    config.add_section("storage")
    config.set("storage", "backend", "sqlite")
    config.set("storage", "sqlite_path", str(db_path))
    config.add_section("settings")
    config.set("settings", "default_language", "en")
    config.set("settings", "anki_mapping_file", mapping_path.as_posix())
    config.add_section("languages")
    config.set("languages", "en_prompt", "standard")
    config.add_section("triggers")
    config.set("triggers", "run_text_translation", "manual")
    config.set("triggers", "run_lemma_base_translation", "manual")
    config.add_section("wordfill")
    config.set("wordfill", "enabled", "true")

    resolved_paths = {
        "kardenwort_workspace": tmp_path,
        "results_dir": tmp_path,
        "anki_mapping_file": mapping_path,
        "sqlite_db_path": db_path,
        "storage_backend": "sqlite",
    }
    tsv_path = tmp_path / f"{sess_zid}-render-wf-test.en.tsv"
    headers = ["TokenOrder", "WordSource", "WordDestination", "SentenceSourceIndex", "SentenceDestination"]
    tsv_path.write_text("\t".join(headers) + "\n0\ttree\t\t1\t\n", encoding="utf-8")

    match_result = {
        "WordDestination": "дерево",
        "_provenance": "corpus:wordfill:20260715120000",
    }

    with patch("kardenwort_desk.find_wordfill_match", return_value=match_result), \
         patch("kardenwort_desk.run_progressive_worker_async"):
        _run_render_flow_impl(
            text="tree",
            language="en",
            zid=sess_zid,
            text_mode="single",
            config=config,
            resolved_paths=resolved_paths,
            tsv_path=tsv_path,
        )

    words = db.get_words_by_session(sess_zid)
    assert len(words) >= 1
    target_w = next(w for w in words if w.get("lemma") == "tree")
    assert target_w["word_destination"] == "дерево"
    assert target_w["word_provenance"] == "corpus:wordfill:20260715120000"


def test_cmd_reprocess_preserves_stored_provenance(tmp_path):
    """Test 6.4: cmd_reprocess_worker preserves existing stored word_provenance from SQLite."""
    from kardenwort_db import KardenwortDB
    from kardenwort_desk import cmd_reprocess_worker

    db_path = tmp_path / "desk_reproc.db"
    db = KardenwortDB(db_path=db_path)
    db.run_migrations()

    sess_zid = "20260830235902"
    db.insert_session({
        "zid": sess_zid,
        "slug": "reproc-test",
        "source_language": "en",
        "source_raw_text": "apple",
    })
    db.insert_sentence({
        "session_zid": sess_zid,
        "sentence_index": 1,
        "sentence_source": "apple",
    })
    db.insert_words([
        {
            "session_zid": sess_zid,
            "sentence_index": 1,
            "token_order": 0,
            "quotation": "apple",
            "lemma": "apple",
            "word_destination": "яблоко",
            "word_provenance": "corpus:wordfill:20260101111111",
        }
    ])

    mapping_path = tmp_path / "mapping.ini"
    mapping_path.write_text("[roles]\nlemma=WordSource\nword_translation=WordDestination\nsentence_index=SentenceSourceIndex\n[fields]\nTokenOrder=\nWordSource=\nWordDestination=\nSentenceSourceIndex=\n", encoding="utf-8")

    db_str = db_path.as_posix()
    mapping_str = mapping_path.as_posix()
    config_path = tmp_path / "config.ini"
    config_path.write_text(f"""[pipeline]
text_base_provider=google
lemma_base_provider=google
lemma_reprocess_provider=google
run_lemmatizer=false
[storage]
backend=sqlite
sqlite_path={db_str}
[settings]
default_language=en
default_target_language=ru
anki_mapping_file={mapping_str}
[triggers]
run_lemma_enrichment=manual
[classification]
enabled=false
""", encoding="utf-8")

    tsv_path = tmp_path / f"{sess_zid}-reproc-test.en.tsv"
    tsv_path.write_text("TokenOrder\tWordSource\tWordDestination\tSentenceSourceIndex\n0\tapple\tяблоко\t1\n", encoding="utf-8")

    args = MagicMock()
    args.config = config_path
    args.tsv = str(tsv_path)
    args.rows = "0"
    args.zid = sess_zid
    args.trace_id = f"{sess_zid}:reproc"

    with patch("kardenwort_desk.safe_write_update_js") as mock_safe_write:
        cmd_reprocess_worker(args)
        assert mock_safe_write.called
        kwargs = mock_safe_write.call_args[1]
        row_provs = kwargs.get("row_provenances", {})
        assert row_provs.get("0") == "corpus:wordfill:20260101111111"
        assert row_provs.get(0) == "corpus:wordfill:20260101111111"
        assert "cached:sqlite" not in row_provs.values()


def test_cmd_retext_preserves_stored_provenance(tmp_path):
    """Test 6.5: cmd_retext_worker preserves existing stored word_provenance from SQLite."""
    from kardenwort_db import KardenwortDB
    from kardenwort_desk import cmd_retext_worker

    db_path = tmp_path / "desk_retext.db"
    db = KardenwortDB(db_path=db_path)
    db.run_migrations()

    sess_zid = "20260830235903"
    db.insert_session({
        "zid": sess_zid,
        "slug": "retext-test",
        "source_language": "en",
        "source_raw_text": "apple",
    })
    db.insert_sentence({
        "session_zid": sess_zid,
        "sentence_index": 1,
        "sentence_source": "apple",
    })
    db.insert_words([
        {
            "session_zid": sess_zid,
            "sentence_index": 1,
            "token_order": 0,
            "quotation": "apple",
            "lemma": "apple",
            "word_destination": "яблоко",
            "word_provenance": "corpus:wordfill:20260101111111",
        }
    ])

    mapping_path = tmp_path / "mapping.ini"
    mapping_path.write_text("[roles]\nlemma=WordSource\nword_translation=WordDestination\nsentence_index=SentenceSourceIndex\n[fields]\nTokenOrder=\nWordSource=\nWordDestination=\nSentenceSourceIndex=\n", encoding="utf-8")

    db_str = db_path.as_posix()
    mapping_str = mapping_path.as_posix()
    config_path = tmp_path / "config.ini"
    config_path.write_text(f"""[pipeline]
text_base_provider=google
text_reprocess_provider=google
[storage]
backend=sqlite
sqlite_path={db_str}
[settings]
default_language=en
default_target_language=ru
anki_mapping_file={mapping_str}
""", encoding="utf-8")

    tsv_path = tmp_path / f"{sess_zid}-retext-test.en.tsv"
    tsv_path.write_text("TokenOrder\tWordSource\tWordDestination\tSentenceSourceIndex\n0\tapple\tяблоко\t1\n", encoding="utf-8")

    args = MagicMock()
    args.config = config_path
    args.tsv = str(tsv_path)
    args.language = "en"
    args.text_mode = "single"
    args.zid = sess_zid
    args.trace_id = f"{sess_zid}:retext"

    with patch("kardenwort_desk.translate_source_text", return_value={0: "яблоко"}), \
         patch("kardenwort_desk.safe_write_update_js") as mock_safe_write:
        cmd_retext_worker(args)
        assert mock_safe_write.called
        kwargs = mock_safe_write.call_args[1]
        row_provs = kwargs.get("row_provenances", {})
        assert row_provs.get("0") == "corpus:wordfill:20260101111111"
        assert "cached:sqlite" not in row_provs.values()


def test_controller_session_creation_and_status_provenance(tmp_path):
    import kardenwort_desk
    from kardenwort_controller import SessionArbiter
    from kardenwort_db import KardenwortDB

    db_path = tmp_path / "kardenwort_ctrl_test.db"
    db = KardenwortDB(db_path)
    db.run_migrations()

    sess_zid = "20260831003000"
    db.insert_session({
        "zid": sess_zid,
        "slug": "ctrl-test",
        "source_language": "en",
        "target_language": "ru",
        "text_mode": "single",
        "source_raw_text": "apple",
    })
    db.insert_sentence({
        "session_zid": sess_zid,
        "sentence_index": 1,
        "sentence_source": "apple",
        "sentence_destination": "яблоко",
        "text_provenance": "live:google",
    })
    db.insert_word({
        "session_zid": sess_zid,
        "sentence_index": 1,
        "token_order": 0,
        "quotation": "apple",
        "lemma": "apple",
        "word_source": "apple",
        "word_destination": "яблоко",
        "word_provenance": "live:google",
    })

    mapping_path = tmp_path / "mapping.ini"
    mapping_path.write_text("[roles]\nlemma=WordSource\nword_translation=WordDestination\nsentence_index=SentenceSourceIndex\n[fields]\nTokenOrder=\nWordSource=\nWordDestination=\nSentenceSourceIndex=\n", encoding="utf-8")

    db_str = db_path.as_posix()
    mapping_str = mapping_path.as_posix()
    config_path = tmp_path / "config.ini"
    ws_str = tmp_path.as_posix()
    config_path.write_text(f"""[pipeline]
text_base_provider=google
text_reprocess_provider=google
[storage]
backend=sqlite
sqlite_path={db_str}
[settings]
default_language=en
default_target_language=ru
anki_mapping_file={mapping_str}
[environment]
kardenwort_workspace={ws_str}
[languages]
en_prompt=test
""", encoding="utf-8")

    config, resolved_paths, _, _ = kardenwort_desk.load_config(config_path)
    arbiter = SessionArbiter(config=config, resolved_paths=resolved_paths)

    tsv_path = tmp_path / f"{sess_zid}-ctrl-test.en.tsv"
    tsv_path.write_text("TokenOrder\tWordSource\tWordDestination\tSentenceSourceIndex\n0\tapple\tяблоко\t1\n", encoding="utf-8")

    res = arbiter.create_session(
        text="apple",
        language="en",
        target_lang="ru",
        text_mode="single",
        sections="all",
        theme="dark",
        zid=sess_zid,
        bypass_lang_check=True,
    )

    assert res.get("text_provenance") == "live:google"
    row_provs = res.get("row_provenances", {})
    assert row_provs.get("0") == "live:google"
    assert "cached:sqlite" not in row_provs.values()


if __name__ == "__main__":
    unittest.main()



