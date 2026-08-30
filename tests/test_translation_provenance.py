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

if __name__ == "__main__":
    unittest.main()

