import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

import kardenwort_desk
from kardenwort_desk import SqliteStorageAdapter
from kardenwort_controller import SessionArbiter


def test_intellifiller_prompt_constraints():
    config_path = Path("U:/voothi/20251206123938-intellifiller-ai-addon-for-anki/IntelliFiller/config.json")
    assert config_path.exists(), "IntelliFiller config.json must exist"
    
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    prompts_list = data.get("prompts", []) if isinstance(data, dict) else data
    prompts = {p.get("promptName"): p.get("prompt") for p in prompts_list if isinstance(p, dict)}
    
    # Check German Prompt
    de_prompt = prompts.get("German Vocabulary Analysis and Translation (JSON)")
    assert de_prompt is not None
    assert 'based strictly on its contextual meaning in "Context"' in de_prompt
    assert "Do NOT copy, blend, or comma-concatenate machine translations" in de_prompt
    assert "Output ONE single dictionary word (Nominative/Infinitive)" in de_prompt

    # Check English Prompt
    en_prompt = prompts.get("English Vocabulary Analysis and Translation (JSON)")
    assert en_prompt is not None
    assert 'based strictly on its contextual meaning in "Context"' in en_prompt
    assert "Do NOT copy, blend, or comma-concatenate machine translations" in en_prompt
    assert "Output ONE single dictionary word (Nominative/Infinitive)" in en_prompt


def test_sqlite_storage_adapter_multi_sentence_retext_persistence(tmp_path):
    db_file = tmp_path / "test_retext_persistence.db"
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    resolved_paths["sqlite_db_path"] = str(db_file)
    
    adapter = SqliteStorageAdapter(config=config, resolved_paths=resolved_paths, db_path=str(db_file))
    session_zid = "20261012010101"
    
    headers = [
        "TokenOrder", "Quotation", "WordSource", "SentenceSourceIndex",
        "SentenceSource", "SentenceDestination", "WordDestination"
    ]
    initial_rows = [
        ["0", "Hallo", "Hallo", "1", "Hallo Welt.", "Привет мир.", "привет"],
        ["1", "Danke", "danke", "2", "Vielen Dank.", "Большое спасибо.", "спасибо"]
    ]
    
    # Initial save creates 2 sentences
    adapter.save_session(
        session_zid=session_zid,
        slug="test-slug",
        source_language="de",
        target_language="ru",
        text_mode="multi",
        source_raw_text="Hallo Welt.\nVielen Dank.",
        data_rows=initial_rows,
        headers=headers,
    )
    
    sents = adapter.db.get_sentences_by_session(session_zid)
    assert len(sents) == 2
    sents_map = {s["sentence_index"]: s["sentence_destination"] for s in sents}
    assert sents_map[1] == "Привет мир."
    assert sents_map[2] == "Большое спасибо."
    
    # Re-text updates translations in data_rows for multiple sentences
    updated_rows = [
        ["0", "Hallo", "Hallo", "1", "Hallo Welt.", "Здравствуй, мир!", "привет"],
        ["1", "Danke", "danke", "2", "Vielen Dank.", "Сердечное спасибо!", "спасибо"]
    ]
    adapter.save_session(
        session_zid=session_zid,
        slug="test-slug",
        source_language="de",
        target_language="ru",
        text_mode="multi",
        source_raw_text="Hallo Welt.\nVielen Dank.",
        data_rows=updated_rows,
        headers=headers,
    )
    
    # Verify that multi-sentence translations were updated and persisted in SQLite
    reloaded_sents = adapter.db.get_sentences_by_session(session_zid)
    assert len(reloaded_sents) == 2
    reloaded_map = {s["sentence_index"]: s["sentence_destination"] for s in reloaded_sents}
    assert reloaded_map[1] == "Здравствуй, мир!"
    assert reloaded_map[2] == "Сердечное спасибо!"


def test_reword_session_scopes_sibling_propagation_to_master_overview(tmp_path):
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    if not config.has_section("pipeline"):
        config.add_section("pipeline")
    config.set("pipeline", "lemma_reprocess_provider", "google")
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    resolved_paths["results_dir"] = str(results_dir)
    
    arbiter = SessionArbiter(config=config, resolved_paths=resolved_paths)
    session_zid = "20261012020202"
    
    # Set up in-memory session with data rows
    headers = [
        "TokenOrder", "Quotation", "WordSource", "SentenceSourceIndex",
        "SentenceSource", "SentenceDestination", "WordDestination"
    ]
    rows = [
        ["0", "gehen", "gehen", "1", "Wir gehen.", "Мы идем.", "идти"],
        ["1", "gehen", "gehen", "2", "Sie gehen.", "Они идут.", "идти"]
    ]
    tsv_file = results_dir / f"{session_zid}.de.tsv"
    tsv_file.write_text("\t".join(headers) + "\n" + "\n".join("\t".join(r) for r in rows), encoding="utf-8")
    
    arbiter.sessions[session_zid] = {
        "session_zid": session_zid,
        "language": "de",
        "target_lang": "ru",
        "text": "Wir gehen.\nSie gehen.",
        "tsv_path": str(tsv_file),
        "comments": [],
        "headers": headers,
        "data_rows": rows,
        "role_fields": {
            "lemma": "WordSource",
            "word_translation": "WordDestination",
            "sentence_index": "SentenceSourceIndex",
            "sentence_source": "SentenceSource",
            "sentence_destination": "SentenceDestination"
        },
        "row_provenances": {}
    }
    
    with patch.object(arbiter, "propagate_enrichment_to_siblings") as mock_enrich_prop, \
         patch.object(arbiter, "propagate_translations_to_siblings") as mock_trans_prop, \
         patch("kardenwort_controller.translate_lemmas_fast_path", return_value={"gehen": "шагать"}):
        
        # 1. Reword called with sentence_idx = 2 (specific sentence tab)
        arbiter.reword_session(
            session_zid=session_zid,
            selected_rows=[1],
            language="de",
            sentence_idx=2
        )
        # Propagation to siblings must NOT occur when sentence_idx != 0
        mock_enrich_prop.assert_not_called()
        mock_trans_prop.assert_not_called()
        
        # 2. Reword called with sentence_idx = 0 (Master Overview tab)
        arbiter.reword_session(
            session_zid=session_zid,
            selected_rows=[0],
            language="de",
            sentence_idx=0
        )
        # Propagation to siblings MUST occur when sentence_idx == 0
        assert mock_trans_prop.call_count == 1


def test_retext_session_failover_provider_chain(tmp_path):
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    if config.has_section("pipeline"):
        config.remove_option("pipeline", "text_reprocess_provider")
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    resolved_paths["results_dir"] = str(results_dir)
    
    arbiter = SessionArbiter(config=config, resolved_paths=resolved_paths)
    session_zid = "20261012030303"
    
    headers = [
        "TokenOrder", "Quotation", "WordSource", "SentenceSourceIndex",
        "SentenceSource", "SentenceDestination", "WordDestination"
    ]
    rows = [["0", "Test", "Test", "1", "Test sentence.", "Старый перевод.", "тест"]]
    tsv_file = results_dir / f"{session_zid}.de.tsv"
    tsv_file.write_text("\t".join(headers) + "\n" + "\t".join(rows[0]), encoding="utf-8")
    
    arbiter.sessions[session_zid] = {
        "session_zid": session_zid,
        "language": "de",
        "target_lang": "ru",
        "text": "Test sentence.",
        "tsv_path": str(tsv_file),
        "comments": [],
        "headers": headers,
        "data_rows": rows,
        "role_fields": {
            "sentence_destination": "SentenceDestination",
            "sentence_index": "SentenceSourceIndex"
        }
    }
    
    # Configure provider chain: first fails, second succeeds
    attempted_providers = []
    def mock_translate(text, src, tgt, mode, cfg, paths, prov, **kwargs):
        attempted_providers.append(prov)
        if prov == "failing_primary":
            raise ConnectionError("Primary provider timed out")
        return {0: "Успешный резервный перевод"}
    
    with patch("kardenwort_controller.resolve_provider_chain", return_value=(["failing_primary", "working_secondary"], "chain")), \
         patch("kardenwort_controller.translate_source_text", side_effect=mock_translate):
        
        res = arbiter.retext_session(session_zid=session_zid, language="de")
        
        assert res["status"] == "success"
        assert res["active_provider"] == "working_secondary"
        assert "Успешный резервный перевод" in res["translated_text"]
        assert attempted_providers == ["failing_primary", "working_secondary"]


def test_retext_session_sqlite_sentence_index_alignment(tmp_path):
    db_file = tmp_path / "test_retext_alignment.db"
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    resolved_paths["sqlite_db_path"] = str(db_file)
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    resolved_paths["results_dir"] = str(results_dir)

    adapter = SqliteStorageAdapter(config=config, resolved_paths=resolved_paths, db_path=str(db_file))
    session_zid = "20261013090001"

    headers = [
        "TokenOrder", "Quotation", "WordSource", "SentenceSourceIndex",
        "SentenceSource", "SentenceDestination", "WordDestination"
    ]
    initial_rows = [
        ["0", "Hallo", "Hallo", "1", "Hallo Welt.", "Alt 1", "привет"],
        ["1", "Danke", "danke", "2", "Vielen Dank.", "Alt 2", "спасибо"]
    ]
    tsv_file = results_dir / f"{session_zid}.de.tsv"
    tsv_file.write_text("\t".join(headers) + "\n" + "\n".join("\t".join(r) for r in initial_rows), encoding="utf-8")

    # Initial save creates 2 sentences in SQLite
    adapter.save_session(
        session_zid=session_zid,
        slug="test-retext-alignment",
        source_language="de",
        target_language="ru",
        text_mode="multi",
        source_raw_text="Hallo Welt.\nVielen Dank.",
        data_rows=initial_rows,
        headers=headers,
    )

    arbiter = SessionArbiter(config=config, resolved_paths=resolved_paths)
    arbiter.storage_adapter = adapter
    arbiter.sessions[session_zid] = {
        "session_zid": session_zid,
        "language": "de",
        "target_lang": "ru",
        "text": "Hallo Welt.\nVielen Dank.",
        "tsv_path": str(tsv_file),
        "comments": [],
        "headers": headers,
        "data_rows": [list(r) for r in initial_rows],
        "role_fields": {
            "sentence_destination": "SentenceDestination",
            "sentence_index": "SentenceSourceIndex"
        }
    }

    # Core returns 0-indexed dict with FULL_TEXT and PADDED keys
    mock_0_indexed_translations = {
        0: "Привет мир (retext).",
        1: "Большое спасибо (retext).",
        "FULL_TEXT": "Привет мир (retext). Большое спасибо (retext).",
        "PADDED": {
            0: "Привет мир (padded).",
            1: "Большое спасибо (padded)."
        }
    }

    with patch("kardenwort_controller.translate_source_text", return_value=mock_0_indexed_translations):
        res = arbiter.retext_session(session_zid=session_zid, language="de", text_mode="multi")
        assert res["status"] == "success"

    # Verify SQLite sentences table: 1-indexed sentences updated cleanly without key-0 rejection
    sents = adapter.db.get_sentences_by_session(session_zid)
    assert len(sents) == 2
    sents_map = {s["sentence_index"]: s for s in sents}

    assert 1 in sents_map
    assert sents_map[1]["sentence_destination"] == "Привет мир (retext)."
    assert sents_map[1]["sentence_destination2"] == "Привет мир (padded)."

    assert 2 in sents_map
    assert sents_map[2]["sentence_destination"] == "Большое спасибо (retext)."
    assert sents_map[2]["sentence_destination2"] == "Большое спасибо (padded)."

    assert 0 not in sents_map


def test_tab_reword_isolation_and_overview_sync(page, tmp_path):
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    raw_text = "Er geht nach Hause. Sie geht zum Park."
    
    tsv_content = (
        "TokenOrder\tQuotation\tWordSource\tWordSourcePOS\tWordDestination\tSentenceSourceIndex\n"
        "1\tgeht\tgehen\tv.\tидти (s1)\t1\n"
        "5\tgeht\tgehen\tv.\tидти (s2)\t2\n"
    )
    tsv_file = tmp_path / "20261012040404-test.de.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")
    
    html_out = kardenwort_desk.run_render_flow(
        text=raw_text,
        language="de",
        zid="20261012040404",
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=True,
        return_children=False,
    )
    
    page.set_content(html_out)
    page.wait_for_timeout(150)
    
    # Check card structure in WorkspaceTabs
    card_info = page.evaluate("""() => {
        var cards = window.WorkspaceTabs ? window.WorkspaceTabs.getCards() : [];
        return cards.map(c => ({
            seq: c.seq_num,
            sent_idx: c.sentence_idx,
            words: (c.words || []).map(w => ({ lemma: w.lemma, trans: w.translation, t_ord: w.token_order, row_id: w.row_id, all_row_ids: w.all_row_ids }))
        }));
    }""")
    assert len(card_info) >= 3
    
    # Find sequence number for Sentence 2 (sent_idx == 2)
    s2_seq = next(c["seq"] for c in card_info if c["sent_idx"] == 2)
    
    # Switch to Sentence Tab 2
    page.evaluate(f"() => {{ if (window.WorkspaceTabs) window.WorkspaceTabs.switchToTab({s2_seq}); }}")
    page.wait_for_timeout(50)
    
    # Apply progressive update via receiveUpdate targeting Sentence 2's token (token_order = 5, lemma = "gehen")
    # While active tab is Sentence 2
    page.evaluate("""() => {
        window.receiveUpdate({
            ok: true,
            status: 'success',
            rows: {
                "5": {
                    token_order: 5,
                    lemma: "gehen",
                    trans: "шагает (s2 новый)"
                }
            }
        });
    }""")
    page.wait_for_timeout(50)
    
    # Read updated cards from WorkspaceTabs
    updated_cards = page.evaluate("""() => {
        var cards = window.WorkspaceTabs ? window.WorkspaceTabs.getCards() : [];
        return cards.map(c => ({
            seq: c.seq_num,
            sent_idx: c.sentence_idx,
            words: (c.words || []).map(w => ({ lemma: w.lemma, trans: w.translation, t_ord: w.token_order }))
        }));
    }""")
    
    overview_card = next(c for c in updated_cards if c["sent_idx"] == 0)
    sent1_card = next(c for c in updated_cards if c["sent_idx"] == 1)
    sent2_card = next(c for c in updated_cards if c["sent_idx"] == 2)
    
    # 1. Active sentence card (Sentence 2) must be updated
    assert any("шагает (s2 новый)" in w["trans"] for w in sent2_card["words"])
    
    # 2. Master Overview card (Sentence 0) must be updated in sync
    assert any("шагает (s2 новый)" in w["trans"] for w in overview_card["words"])
    
    # 3. Sibling sentence card (Sentence 1) must NOT be mutated!
    assert all("шагает (s2 новый)" not in w["trans"] for w in sent1_card["words"])
    assert any("идти (s1)" in w["trans"] for w in sent1_card["words"])


def test_on_retext_click_updates_workspace_tabs_cards(page, tmp_path):
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    raw_text = "Er geht nach Hause. Sie geht zum Park."
    
    tsv_content = (
        "TokenOrder\tQuotation\tWordSource\tWordSourcePOS\tWordDestination\tSentenceSourceIndex\n"
        "1\tgeht\tgehen\tv.\tидти\t1\n"
        "5\tgeht\tgehen\tv.\tидти\t2\n"
    )
    tsv_file = tmp_path / "20261012050505-test.de.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")
    
    html_out = kardenwort_desk.run_render_flow(
        text=raw_text,
        language="de",
        zid="20261012050505",
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=True,
        return_children=False,
    )
    
    page.set_content(html_out)
    page.wait_for_timeout(150)
    
    # Simulate fetch /session/retext response inside onRetextClick
    page.evaluate("""() => {
        window.fetch = function(url, options) {
            return Promise.resolve({
                ok: true,
                status: 200,
                json: function() {
                    return Promise.resolve({
                        status: 'success',
                        data: {
                            translatedText: '<div>Он идет домой (обновлено).</div><div>Она идет в парк (обновлено).</div>',
                            translated_text: '<div>Он идет домой (обновлено).</div><div>Она идет в парк (обновлено).</div>'
                        }
                    });
                }
            });
        };
        window.onRetextClick();
    }""")
    page.wait_for_timeout(100)
    
    # Verify WorkspaceTabs cards updated
    cards_translated = page.evaluate("""() => {
        var cards = window.WorkspaceTabs ? window.WorkspaceTabs.getCards() : [];
        return cards.map(c => ({ sent_idx: c.sentence_idx, trans: c.translated_text }));
    }""")
    
    overview = next(c for c in cards_translated if c["sent_idx"] == 0)
    assert "Он идет домой (обновлено)" in overview["trans"]
    
    # Switch tabs back and forth to ensure translation is preserved and does not revert
    page.evaluate("() => { if (window.WorkspaceTabs) window.WorkspaceTabs.switchToTab(2); }")
    page.wait_for_timeout(50)
    page.evaluate("() => { if (window.WorkspaceTabs) window.WorkspaceTabs.switchToTab(1); }")
    page.wait_for_timeout(50)
    
    tc_text = page.locator("#translation-container").inner_text()
    assert "Он идет домой (обновлено)" in tc_text


def test_retext_from_child_sentence_tab_synchronizes_all_cards(page, tmp_path):
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    db_file = tmp_path / "test_retext_child_tab.db"
    resolved_paths["sqlite_db_path"] = str(db_file)
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    resolved_paths["results_dir"] = str(results_dir)

    adapter = SqliteStorageAdapter(config=config, resolved_paths=resolved_paths, db_path=str(db_file))
    session_zid = "20261013095555"

    headers = [
        "TokenOrder", "Quotation", "WordSource", "WordSourcePOS",
        "SentenceSourceIndex", "SentenceSource", "SentenceDestination", "WordDestination"
    ]
    initial_rows = [
        ["1", "geht", "gehen", "v.", "1", "Er geht nach Hause.", "Он идет домой.", "идти"],
        ["5", "geht", "gehen", "v.", "2", "Sie geht zum Park.", "Она идет в парк.", "идти"]
    ]
    tsv_file = results_dir / f"{session_zid}.de.tsv"
    tsv_file.write_text("\t".join(headers) + "\n" + "\n".join("\t".join(r) for r in initial_rows), encoding="utf-8")

    adapter.save_session(
        session_zid=session_zid,
        slug="test-retext-child-tab",
        source_language="de",
        target_language="ru",
        text_mode="multi",
        source_raw_text="Er geht nach Hause.\nSie geht zum Park.",
        data_rows=initial_rows,
        headers=headers,
    )

    raw_text = "Er geht nach Hause. Sie geht zum Park."
    html_out = kardenwort_desk.run_render_flow(
        text=raw_text,
        language="de",
        zid=session_zid,
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=True,
        return_children=False,
    )

    page.set_content(html_out)
    page.wait_for_timeout(150)

    # 1. Switch to Tab for Sentence 2 (sent_idx == 2)
    card_info = page.evaluate("""() => {
        var cards = window.WorkspaceTabs ? window.WorkspaceTabs.getCards() : [];
        return cards.map(c => ({ seq: c.seq_num, sent_idx: c.sentence_idx }));
    }""")
    s2_seq = next(c["seq"] for c in card_info if c["sent_idx"] == 2)

    page.evaluate(f"() => {{ if (window.WorkspaceTabs) window.WorkspaceTabs.switchToTab({s2_seq}); }}")
    page.wait_for_timeout(50)

    # Verify initial translation of Sentence 2 on screen
    initial_tc = page.locator("#translation-container").inner_text()
    assert "Она идет в парк." in initial_tc

    # 2. Simulate Retext execution while viewing Sentence 2 tab
    retext_full = "<div>Он шагает домой (retext).</div><div>Она шагает в парк (retext).</div>"
    page.evaluate(f"""() => {{
        window.fetch = function(url, options) {{
            return Promise.resolve({{
                ok: true,
                status: 200,
                json: function() {{
                    return Promise.resolve({{
                        status: 'success',
                        data: {{
                            translatedText: '{retext_full}',
                            translated_text: '{retext_full}',
                            sentences: {{
                                0: 'Он шагает домой (retext).',
                                1: 'Она шагает в парк (retext).'
                            }}
                        }}
                    }});
                }}
            }});
        }};
        window.onRetextClick();
    }}""")
    page.wait_for_timeout(100)

    # 3. Assert: Active tab (Sentence 2) displays ONLY Sentence 2's translation, NOT full paragraph!
    active_tc = page.locator("#translation-container").inner_text()
    assert "Она шагает в парк (retext)." in active_tc
    assert "Он шагает домой (retext)." not in active_tc

    # Interactive tokens must be preserved
    words = page.locator("#translation-container span.word")
    assert words.count() > 0

    # 4. Verify all card models were updated in WorkspaceTabs
    updated_cards = page.evaluate("""() => {
        var cards = window.WorkspaceTabs ? window.WorkspaceTabs.getCards() : [];
        return cards.map(c => ({ sent_idx: c.sentence_idx, trans: c.translated_text }));
    }""")
    overview = next(c for c in updated_cards if c["sent_idx"] == 0)
    sent1 = next(c for c in updated_cards if c["sent_idx"] == 1)
    sent2 = next(c for c in updated_cards if c["sent_idx"] == 2)

    assert "Он шагает домой (retext)." in overview["trans"]
    assert "Она шагает в парк (retext)." in overview["trans"]
    assert "Он шагает домой (retext)." in sent1["trans"]
    assert "Она шагает в парк (retext)." in sent2["trans"]

    # 5. Switch to Overview Tab (sent_idx == 0)
    overview_seq = next(c["seq"] for c in card_info if c["sent_idx"] == 0)
    page.evaluate(f"() => {{ if (window.WorkspaceTabs) window.WorkspaceTabs.switchToTab({overview_seq}); }}")
    page.wait_for_timeout(50)
    overview_tc = page.locator("#translation-container").inner_text()
    assert "Он шагает домой (retext)." in overview_tc
    assert "Она шагает в парк (retext)." in overview_tc

    # 6. Switch to Sentence 1 Tab (sent_idx == 1)
    sent1_seq = next(c["seq"] for c in card_info if c["sent_idx"] == 1)
    page.evaluate(f"() => {{ if (window.WorkspaceTabs) window.WorkspaceTabs.switchToTab({sent1_seq}); }}")
    page.wait_for_timeout(50)
    sent1_tc = page.locator("#translation-container").inner_text()
    assert "Он шагает домой (retext)." in sent1_tc
    assert "Она шагает в парк (retext)." not in sent1_tc

    # 7. Backend SQLite contract check: retext_session aligns SQLite rows at 1-indexed positions
    arbiter = SessionArbiter(config=config, resolved_paths=resolved_paths)
    arbiter.storage_adapter = adapter
    arbiter.sessions[session_zid] = {
        "session_zid": session_zid,
        "language": "de",
        "target_lang": "ru",
        "text": "Er geht nach Hause.\nSie geht zum Park.",
        "tsv_path": str(tsv_file),
        "comments": [],
        "headers": headers,
        "data_rows": [list(r) for r in initial_rows],
        "role_fields": {
            "sentence_destination": "SentenceDestination",
            "sentence_index": "SentenceSourceIndex"
        }
    }
    with patch("kardenwort_controller.translate_source_text", return_value={
        0: "Он шагает домой (backend).",
        1: "Она шагает в парк (backend)."
    }):
        arbiter.retext_session(session_zid=session_zid, language="de", text_mode="multi")

    sents = adapter.db.get_sentences_by_session(session_zid)
    assert len(sents) == 2
    sents_map = {s["sentence_index"]: s["sentence_destination"] for s in sents}
    assert sents_map[1] == "Он шагает домой (backend)."
    assert sents_map[2] == "Она шагает в парк (backend)."
    assert 0 not in sents_map


def test_on_retext_click_failfast_when_sentences_missing_or_malformed(page, tmp_path):
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    raw_text = "Er geht nach Hause. Sie geht zum Park."
    tsv_content = (
        "TokenOrder\tQuotation\tWordSource\tWordSourcePOS\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tWordDestination\n"
        "1\tgeht\tgehen\tv.\t1\tEr geht nach Hause.\tОн идет домой.\tидти\n"
        "5\tgeht\tgehen\tv.\t2\tSie geht zum Park.\tОна идет в парк.\tидти\n"
    )
    tsv_file = tmp_path / "20261013211705-test.de.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html_out = kardenwort_desk.run_render_flow(
        text=raw_text,
        language="de",
        zid="20261013211705",
        text_mode="multi",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=str(tsv_file),
        spawn_children=True,
        return_children=False,
    )
    page.set_content(html_out)
    page.wait_for_timeout(150)

    # Record initial card translations
    initial_cards = page.evaluate("""() => {
        var cards = window.WorkspaceTabs ? window.WorkspaceTabs.getCards() : [];
        return cards.map(c => ({ sent_idx: c.sentence_idx, trans: c.translated_text }));
    }""")

    # 1. Test missing 'sentences' in payload
    toast_messages = []
    page.expose_function("recordToastFailfast", lambda msg, typ: toast_messages.append((msg, typ)))
    page.evaluate("""() => {
        const origToast = window.showToast;
        window.showToast = function(msg, typ) {
            window.recordToastFailfast(msg, typ);
            if (origToast) origToast(msg, typ);
        };
        const origFetch = window.fetch;
        window.fetch = function(url, options) {
            if (url === '/session/retext' || (typeof url === 'string' && url.indexOf('/session/retext') !== -1)) {
                return Promise.resolve({
                    ok: true,
                    status: 200,
                    json: function() {
                        return Promise.resolve({
                            status: 'success',
                            data: {
                                translatedText: '<div>Corrupt line 1</div><div>Corrupt line 2</div>',
                                translated_text: '<div>Corrupt line 1</div><div>Corrupt line 2</div>'
                            }
                        });
                    }
                });
            }
            return origFetch ? origFetch(url, options) : Promise.reject('unhandled');
        };
        window.onRetextClick();
    }""")
    page.wait_for_timeout(100)

    assert any("Retext failed: invalid sentence translation payload from server" in t[0] and t[1] == "error" for t in toast_messages)

    current_cards = page.evaluate("""() => {
        var cards = window.WorkspaceTabs ? window.WorkspaceTabs.getCards() : [];
        return cards.map(c => ({ sent_idx: c.sentence_idx, trans: c.translated_text }));
    }""")
    assert current_cards == initial_cards

    # 2. Test malformed 'sentences' (array instead of mapping object)
    toast_messages.clear()
    page.evaluate("""() => {
        const origFetch = window.fetch;
        window.fetch = function(url, options) {
            if (url === '/session/retext' || (typeof url === 'string' && url.indexOf('/session/retext') !== -1)) {
                return Promise.resolve({
                    ok: true,
                    status: 200,
                    json: function() {
                        return Promise.resolve({
                            status: 'success',
                            data: {
                                translatedText: '<div>Line 1</div>',
                                translated_text: '<div>Line 1</div>',
                                sentences: ['malformed', 'array']
                            }
                        });
                    }
                });
            }
            return origFetch ? origFetch(url, options) : Promise.reject('unhandled');
        };
        window.onRetextClick();
    }""")
    page.wait_for_timeout(100)

    assert any("Retext failed: invalid sentence translation payload from server" in t[0] and t[1] == "error" for t in toast_messages)

    current_cards_after_array = page.evaluate("""() => {
        var cards = window.WorkspaceTabs ? window.WorkspaceTabs.getCards() : [];
        return cards.map(c => ({ sent_idx: c.sentence_idx, trans: c.translated_text }));
    }""")
    assert current_cards_after_array == initial_cards


def test_retext_session_backend_normalized_sentences_contract(tmp_path):
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    resolved_paths["results_dir"] = str(results_dir)

    arbiter = SessionArbiter(config=config, resolved_paths=resolved_paths)
    session_zid = "20261013211706"

    headers = [
        "TokenOrder", "Quotation", "WordSource", "SentenceSourceIndex",
        "SentenceSource", "SentenceDestination", "WordDestination"
    ]
    rows = [
        ["0", "Eins", "eins", "1", "Satz eins.", "Старый 1", "один"],
        ["1", "Zwei", "zwei", "2", "Satz zwei.", "Старый 2", "два"]
    ]
    tsv_file = results_dir / f"{session_zid}.de.tsv"
    tsv_file.write_text("\t".join(headers) + "\n" + "\n".join("\t".join(r) for r in rows), encoding="utf-8")

    arbiter.sessions[session_zid] = {
        "session_zid": session_zid,
        "language": "de",
        "target_lang": "ru",
        "text": "Satz eins.\nSatz zwei.",
        "tsv_path": str(tsv_file),
        "comments": [],
        "headers": headers,
        "data_rows": rows,
        "role_fields": {
            "sentence_destination": "SentenceDestination",
            "sentence_index": "SentenceSourceIndex"
        }
    }

    # 1. 0-indexed dict with FULL_TEXT and PADDED metadata must normalize to clean 1-indexed integers
    mock_0_indexed = {
        0: "Перевод первого предложения.",
        1: "Перевод второго предложения.",
        "FULL_TEXT": "Перевод первого предложения. Перевод второго предложения.",
        "PADDED": {0: "Padded 1", 1: "Padded 2"}
    }
    with patch("kardenwort_controller.translate_source_text", return_value=mock_0_indexed):
        res = arbiter.retext_session(session_zid=session_zid, language="de", text_mode="multi")
        assert res["status"] == "success"
        assert res["sentences"] == {
            1: "Перевод первого предложения.",
            2: "Перевод второго предложения."
        }
        # Non-integer keys must be excluded from sentences mapping
        assert "FULL_TEXT" not in res["sentences"]
        assert "PADDED" not in res["sentences"]
        assert 0 not in res["sentences"]

    # 2. String translation must normalize to 1-indexed dict {1: text}
    with patch("kardenwort_controller.translate_source_text", return_value="Единый перевод строки."):
        res_str = arbiter.retext_session(session_zid=session_zid, language="de", text_mode="single")
        assert res_str["status"] == "success"
        assert res_str["sentences"] == {1: "Единый перевод строки."}



