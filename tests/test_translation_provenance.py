import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile
import configparser

from kardenwort_desk import (
    ProvenanceDict,
    format_provenance_tooltip,
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

    def test_provenance_dict_metadata(self):
        d = ProvenanceDict({"apple": "яблоко"}, provenance="live:argos")
        self.assertEqual(d["apple"], "яблоко")
        self.assertEqual(d.provenance, "live:argos")

    @patch("kardenwort_desk.translate_text")
    def test_translate_lemmas_fast_path_provenance(self, mock_trans):
        mock_trans.return_value = "дом"
        config = MagicMock()
        resolved_paths = {}
        res = translate_lemmas_fast_path(["haus"], "de", "ru", config, resolved_paths, provider="argos")
        self.assertEqual(res["haus"], "дом")
        self.assertEqual(getattr(res, "provenance", None), "live:argos")

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
            self.assertIn('data-provenance="cached:sqlite"', html)
            self.assertIn('title="Loaded from session cache (SQLite)"', html)

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

    def test_format_update_rows_dict_defaults_unassigned_translations_to_cached_sqlite(self):
        from kardenwort_desk import format_update_rows_dict
        headers = ["TokenOrder", "WordSource", "WordDestination"]
        role_fields = {"lemma": "WordSource", "word_translation": "WordDestination"}
        data_rows = [
            ["0", "run", "бег"],
            ["1", "walk", ""],
            ["2", "jump", '<span class="skeleton-loader">Argos...</span>'],
            ["3", "fly", '<button class="btn-retry-cell">Retry</button>'],
        ]
        # Without any row_provenances passed
        rows_dict = format_update_rows_dict(data_rows, headers, role_fields)
        self.assertEqual(rows_dict[0].get("provenance"), "cached:sqlite")
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

    # 1. Initial cached provenance attributes
    assert page.locator("td.col-translation").get_attribute("data-provenance") == "cached:sqlite"
    assert page.locator("td.col-translation").get_attribute("title") == "Loaded from session cache (SQLite)"
    assert page.locator("td.col-translation .scrollable-cell").get_attribute("data-provenance") == "cached:sqlite"

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

    resolved_paths = {
        "kardenwort_workspace": tmp_path,
        "results_dir": tmp_path,
        "anki_mapping_file": mapping_path,
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

    # Initial render should attribute pre-existing translation "бег" to cached:sqlite
    cells = page.locator("td.col-translation").all()
    assert len(cells) == 2
    assert cells[0].get_attribute("data-provenance") == "cached:sqlite"
    assert cells[0].get_attribute("title") == "Loaded from session cache (SQLite)"

    # Even after stage="source" progressive delta arrives without explicit provenance,
    # the client JS should preserve or default to cached:sqlite for the translated cell
    page.evaluate("""() => {
        window.receiveUpdate({
            stage: 'source',
            status: 'success',
            rows: {
                '0': { lemma: 'run', trans: 'бег', token_order: '0' },
                '1': { lemma: 'you', trans: '', token_order: '1' }
            }
        });
    }""")

    assert cells[0].get_attribute("data-provenance") == "cached:sqlite"
    assert cells[0].get_attribute("title") == "Loaded from session cache (SQLite)"

if __name__ == "__main__":
    unittest.main()

