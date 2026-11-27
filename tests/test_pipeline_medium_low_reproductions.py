"""Phase 1 isolated reproductions for PIPE-M1..M5, PIPE-M7..M10, and PIPE-L1..L4."""
from pathlib import Path
import json
import sqlite3

import pytest

import kardenwort_desk as desk
from kardenwort_db import KardenwortDB, QueryExecutionError, QuerySecurityError, sanitize_extra_fields
from tests.controller_test_support import owned_configuration
from tests.sqlite_document_support import island


def _build_env(tmp_path: Path, *, mapping_text: str | None = None, backend: str = "sqlite"):
    config, paths, _ = owned_configuration(tmp_path, "", backend)
    mapping_file = tmp_path / "mapping.ini"
    mapping_file.write_text(
        mapping_text
        or (
            "[fields]\n"
            "TokenOrder\nQuotation\nWordSource\nWordDestination\nWordSourceIPA\n"
            "WordSourceMorphologyAI\nWordSourcePOS\nSentenceSourceIndex\n"
            "SentenceSource\nSentenceDestination\nDeskSelected\n"
            "[desk_columns]\n"
            "WordSource=lemma\n"
            "WordDestination=word_translation\n"
            "WordSourceIPA=ipa\n"
            "WordSourceMorphologyAI=morphology\n"
            "WordSourcePOS=pos\n"
            "SentenceSourceIndex=sentence_index\n"
            "SentenceSource=sentence\n"
            "SentenceDestination=sentence_destination\n"
            "DeskSelected=selected\n"
            "[desk_editable]\n"
            "editable_columns=WordDestination,WordSourceIPA\n"
        ),
        encoding="utf-8",
    )
    config.read_dict({
        "settings": {
            "default_language": "de",
            "default_target_language": "en",
            "anki_mapping_file": str(mapping_file),
        },
        "triggers": {
            "run_text_translation": "manual",
            "run_lemma_base_translation": "manual",
            "run_lemma_enrichment": "manual",
        },
        "rendering": {"display_mode": "monolithic"},
        "pipeline": {"progressive_text_translation": "false"},
        "classification": {"enabled": "false"},
        "wordfill": {"enabled": "false"},
        "environment": {"kardenwort_workspace": str(tmp_path)},
    })
    paths["anki_mapping_file"] = mapping_file
    paths["kardenwort_workspace"] = tmp_path
    paths["favorites_output_dir"] = tmp_path / "favorites"
    adapter = desk.get_storage_adapter(config, paths)
    return config, paths, adapter


def test_pipe_m1_query_readonly_blocks_pragma_write_closes_on_error_and_never_creates_missing_db(tmp_path):
    """PIPE-M1: query_readonly must reject PRAGMA query_only = OFF, close conn on error, and not create missing DB."""
    # Missing DB must raise and NOT create file on disk
    missing_path = tmp_path / "does_not_exist.db"
    missing_db = KardenwortDB(db_path=missing_path)
    with pytest.raises((FileNotFoundError, QueryExecutionError, QuerySecurityError, sqlite3.OperationalError)):
        missing_db.query_readonly("SELECT 1;")
    assert not missing_path.exists()

    # Existing DB: PRAGMA query_only = OFF must be blocked
    db_path = tmp_path / "ro_guard.db"
    db = KardenwortDB(db_path=db_path)
    db.run_migrations()
    with pytest.raises(QuerySecurityError):
        db.query_readonly("PRAGMA query_only = OFF;")

    # Failed query must close connection in finally (verify file can be unlinked on Windows)
    with pytest.raises(QueryExecutionError):
        db.query_readonly("SELECT * FROM nonexistent_table_xyz;")
    db_path.unlink()
    assert not db_path.exists()


def test_pipe_m1_readonly_reset_refused_and_vacuum_backup_use_instance_logger(tmp_path):
    """PIPE-M1: read_only KardenwortDB must refuse reset(), and vacuum()/backup_snapshot() must log via self.logger."""
    db_path = tmp_path / "ro_reset.db"
    rw_db = KardenwortDB(db_path=db_path)
    rw_db.run_migrations()

    ro_db = KardenwortDB(db_path=db_path, read_only=True)
    with pytest.raises(QuerySecurityError):
        ro_db.reset(force=True)
    assert db_path.exists()

    logged = []

    class _SpyLogger:
        def log(self, level, message, **kwargs):
            logged.append((level, message))

    rw_db.logger = _SpyLogger()
    rw_db.vacuum()
    rw_db.backup_snapshot(backup_dir=tmp_path / "backups", zid="20261127200301")
    messages = " | ".join(m for _, m in logged)
    assert "VACUUM" in messages
    assert "backup snapshot" in messages.lower()


def test_pipe_m2_get_telemetry_reports_shm_and_schema_version_and_handles_missing_db(tmp_path):
    """PIPE-M2: get_telemetry must report real shm_size_bytes & schema_version and handle missing DB gracefully."""
    missing_path = tmp_path / "missing_tel.db"
    missing_db = KardenwortDB(db_path=missing_path)
    missing_tel = missing_db.get_telemetry()
    assert missing_tel["active_sessions"] == 0
    assert missing_tel["schema_version"] == 0
    assert not missing_path.exists()

    db_path = tmp_path / "present_tel.db"
    db = KardenwortDB(db_path=db_path)
    db.run_migrations()
    # Ensure SHM sidecar exists while connection is open
    with db.get_connection() as _conn:
        tel = db.get_telemetry()
    assert tel["schema_version"] > 0
    if db.shm_path.exists() and db.shm_path.stat().st_size > 0:
        assert tel["shm_size_bytes"] == db.shm_path.stat().st_size


def test_pipe_m3_batch_update_words_guards_missing_identifiers_and_updates_all_custom_rows(tmp_path):
    """PIPE-M3: batch_update_words skips identifier-less items without stale rowcount and updates all matching custom rows."""
    _, _, adapter = _build_env(tmp_path)
    zid = "20261127200302"
    adapter.save_session(
        session_zid=zid,
        slug="batch-upd",
        source_language="de",
        target_language="en",
        text_mode="multi",
        source_raw_text="Haus.\nBaum.",
        sentences=[
            {"sentence_index": 1, "sentence_source": "Haus."},
            {"sentence_index": 2, "sentence_source": "Baum."},
        ],
        words=[
            {"sentence_index": 1, "token_order": 0, "lemma": "Haus", "quotation": "Haus", "word_destination": "house"},
            {"sentence_index": 2, "token_order": 0, "lemma": "Baum", "quotation": "Baum", "word_destination": "tree"},
        ],
    )

    # Item 1 updates 1 row; Item 2 has NO id/token_order and must NOT increment updated_count
    count = adapter.db.batch_update_words(
        zid,
        [
            {"sentence_index": 1, "token_order": 0, "updates": {"word_destination": "home"}},
            {"updates": {"word_destination": "ghost"}},
        ],
    )
    assert count == 1

    # Custom field update with token_order=0 and sentence_index omitted must update BOTH matching rows
    custom_count = adapter.db.batch_update_words(
        zid,
        [{"token_order": 0, "updates": {"CustomNote": "shared-note"}}],
    )
    assert custom_count == 2
    words = adapter.db.get_words_by_session(zid, parse_json=True)
    assert all((w.get("extra_fields") or {}).get("CustomNote") == "shared-note" for w in words)


def test_pipe_m4_save_session_and_persist_retext_do_not_bloat_extra_fields(tmp_path):
    """PIPE-M4/PIPE-L3: save_session excludes TokenOrder/role cols from extra_fields; persist_retext does not write SentenceDestination there."""
    mapping_text = (
        "[fields]\n"
        "TokenOrder\nQuotation\nWordSource\nWordDestination\nCustomSentIdx\n"
        "SentenceSource\nSentenceDestination\nDeskSelected\n"
        "[desk_columns]\n"
        "WordSource=lemma\n"
        "WordDestination=word_translation\n"
        "CustomSentIdx=sentence_index\n"
        "SentenceSource=sentence\n"
        "SentenceDestination=sentence_destination\n"
        "DeskSelected=selected\n"
    )
    _, _, adapter = _build_env(tmp_path, mapping_text=mapping_text)
    zid = "20261127200303"
    headers = ["TokenOrder", "Quotation", "WordSource", "WordDestination", "CustomSentIdx", "SentenceSource", "SentenceDestination", "DeskSelected"]
    rows = [["0", "Haus", "Haus", "house", "1", "Haus.", "House.", "0"]]
    adapter.save_session(
        session_zid=zid,
        slug="no-bloat",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Haus.",
        headers=headers,
        data_rows=rows,
        sentences=[{"sentence_index": 1, "sentence_source": "Haus.", "sentence_destination": "House."}],
    )

    word = adapter.db.get_words_by_session(zid, parse_json=True)[0]
    ef = word.get("extra_fields") or {}
    assert "TokenOrder" not in ef
    assert "CustomSentIdx" not in ef

    adapter.persist_retext(zid, {1: "New house."}, {1: "Padded house."}, provenance="live:deepl")
    word_after = adapter.db.get_words_by_session(zid, parse_json=True)[0]
    ef_after = word_after.get("extra_fields") or {}
    assert "SentenceDestination" not in ef_after
    assert "SentenceDestination2" not in ef_after


def test_pipe_m5_build_chunks_emits_no_empty_chunk_for_long_line(tmp_path):
    """PIPE-M5: _build_chunks must not emit a leading empty chunk for lines exceeding adaptive_max_chars."""
    config, _, _ = _build_env(tmp_path)
    long_line = "a" * 1005
    chunks = desk._build_chunks([long_line], chunk_size=0, config=config)
    assert len(chunks) == 1
    assert chunks[0] == ([long_line], [0])


def test_pipe_m7_cross_pollinate_from_siblings_filters_source_and_target_language(tmp_path):
    """PIPE-M7: cross_pollinate_from_siblings must filter siblings by source_language and target_language."""
    _, paths, adapter = _build_env(tmp_path)
    sib_zid = "20261127200310"
    cur_zid = "20261127200315"

    adapter.save_session(
        session_zid=sib_zid,
        slug="sib-ru",
        source_language="de",
        target_language="ru",
        text_mode="single",
        source_raw_text="Haus.",
        sentences=[{"sentence_index": 1, "sentence_source": "Haus."}],
        words=[{"sentence_index": 1, "token_order": 0, "lemma": "Haus", "quotation": "Haus", "word_destination": "дом"}],
    )
    adapter.save_session(
        session_zid=cur_zid,
        slug="cur-en",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Haus.",
        sentences=[{"sentence_index": 1, "sentence_source": "Haus."}],
        words=[{"sentence_index": 1, "token_order": 0, "lemma": "Haus", "quotation": "Haus", "word_destination": ""}],
    )

    _, headers, rows = adapter.load_tsv_rows(cur_zid)
    role_fields = desk.get_role_fields(desk.load_anki_mapping(paths["anki_mapping_file"]), headers)
    out = desk.cross_pollinate_from_siblings(
        tmp_path / f"{cur_zid}-cur-en.de.tsv",
        rows,
        headers,
        role_fields,
        storage_adapter=adapter,
        is_sqlite=True,
    )
    assert out[0][headers.index("WordDestination")] == ""


def test_pipe_m8_headless_intellifiller_uses_target_column_and_validates_http_fields(tmp_path, monkeypatch):
    """PIPE-M8: _run_headless_intellifiller_impl resolves CLI --target-field via target_column and validates HTTP rows."""
    config, paths, _ = _build_env(tmp_path, backend="tsv")
    tsv_path = tmp_path / "20261127200320-test.de.tsv"
    headers = ["TokenOrder", "WordSource", "WordDestination", "WordSourceIPA", "SentenceSourceIndex"]
    desk.save_tsv_rows_safely(tsv_path, [], headers, [["0", "Haus", "house", "", "1"]])

    # (a) CLI path for ipa_extraction must pass --target-field WordSourceIPA
    config.set("services", "intellifiller_server_url", "")
    captured_cmd = []

    class _FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        return _FakeCompleted()

    monkeypatch.setattr(desk.subprocess, "run", fake_run)
    desk._run_headless_intellifiller_impl(
        tsv_path, "ipa_extraction", config, paths, selected_rows=[0], reprocess=False
    )
    assert "--target-field" in captured_cmd
    assert captured_cmd[captured_cmd.index("--target-field") + 1] == "WordSourceIPA"

    # (b) HTTP path must reject failed row / identity mutation via checked_fields
    config.set("services", "intellifiller_server_url", "http://127.0.0.1:9999")
    monkeypatch.setattr(desk, "check_endpoint_reachable", lambda *_a, **_kw: True)
    monkeypatch.setattr(
        desk,
        "query_intellifiller_server",
        lambda **_kw: {
            "status": "success",
            "enriched_rows": [{"row_id": 0, "status": "error", "TokenOrder": "999", "WordDestination": "[FAILED]"}],
        },
    )
    with pytest.raises(desk.IntelliFillerError):
        desk._run_headless_intellifiller_impl(
            tsv_path, "fixture", config, paths, selected_rows=[0], reprocess=False
        )


def test_pipe_m9_export_favorites_uses_custom_selected_role_and_does_not_mutate_unselected_or_all(tmp_path):
    """PIPE-M9: export_favorites resolves role_fields['selected'] and does not mutate SQLite selected in unselected/all modes."""
    mapping_text = (
        "[fields]\n"
        "TokenOrder\nQuotation\nWordSource\nWordDestination\nSentenceSourceIndex\nSentenceSource\nCustomSel\n"
        "[desk_columns]\n"
        "WordSource=lemma\n"
        "WordDestination=word_translation\n"
        "SentenceSourceIndex=sentence_index\n"
        "SentenceSource=sentence\n"
        "CustomSel=selected\n"
    )
    config, _, adapter = _build_env(tmp_path, mapping_text=mapping_text)
    zid = "20261127200321"
    adapter.save_session(
        session_zid=zid,
        slug="fav-export",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Haus Baum.",
        sentences=[{"sentence_index": 1, "sentence_source": "Haus Baum."}],
        words=[
            {"sentence_index": 1, "token_order": 0, "lemma": "Haus", "quotation": "Haus", "word_destination": "house", "selected": 1},
            {"sentence_index": 1, "token_order": 1, "lemma": "Baum", "quotation": "Baum", "word_destination": "tree", "selected": 0},
        ],
    )

    # Custom selected role works in default 'selected' mode
    config.set("settings", "export_selection_mode", "selected")
    res_sel = adapter.export_favorites(zid, save_to_favorites_override=True, send_to_anki_override=False)
    assert res_sel["status"] == "success"
    assert res_sel["exported_count"] == 1

    # 'unselected' mode must NOT set selected=1 on Baum in SQLite
    config.set("settings", "export_selection_mode", "unselected")
    res_unsel = adapter.export_favorites(zid, save_to_favorites_override=True, send_to_anki_override=False)
    assert res_unsel["status"] == "success"
    words_by_lem = {w["lemma"]: w for w in adapter.db.get_words_by_session(zid)}
    assert words_by_lem["Baum"]["selected"] == 0

    # 'all' mode must also leave Baum unselected in SQLite
    config.set("settings", "export_selection_mode", "all")
    res_all = adapter.export_favorites(zid, save_to_favorites_override=True, send_to_anki_override=False)
    assert res_all["status"] == "success"
    words_by_lem_all = {w["lemma"]: w for w in adapter.db.get_words_by_session(zid)}
    assert words_by_lem_all["Baum"]["selected"] == 0


def test_pipe_m10_translate_lemmas_fast_path_tracks_chunk_provenance_parses_chain_and_bypasses_offline(
    tmp_path, monkeypatch
):
    """PIPE-M10: per-chunk failover provenance, comma-chain provider parsing, and offline bypass in chain mode."""
    config, paths, _ = _build_env(tmp_path)
    config.set("translation", "lemma_batch_size", "1")
    config.set("pipeline", "lemma_provider_chain", "google, deepl, argos")
    config.set("pipeline", "failover_strategy", "chain")
    config.set("pipeline", "fast_connectivity_check_ips", "8.8.8.8")

    # (a) Per-chunk failover provenance: chunk 1 via google, chunk 2 fails over to deepl
    monkeypatch.setattr(desk, "is_network_online_multi", lambda **_kw: True)

    def fake_translate(text, source, target, cfg, rp, provider="google", **_kw):
        if provider == "google" and "beta" in text:
            raise RuntimeError("google failed on beta")
        return desk.ProvenanceString(f"{provider}:{text}", provenance=f"live:{provider}")

    monkeypatch.setattr(desk, "translate_text", fake_translate)
    res = desk.translate_lemmas_fast_path(["alpha", "beta"], "en", "ru", config, paths, "google")
    assert res["alpha"] == "google:alpha"
    assert res["beta"] == "deepl:beta"
    assert getattr(res["alpha"], "provenance", None) == "live:google"
    assert getattr(res["beta"], "provenance", None) == "live:deepl"

    # (b) Comma-separated provider chain passed explicitly as provider arg
    desk.clear_provider_cooldowns(config=config, resolved_paths=paths)
    seen_providers = []

    def chain_translate(text, source, target, cfg, rp, provider="google", **_kw):
        seen_providers.append(provider)
        if provider == "google":
            raise RuntimeError("google down")
        return desk.ProvenanceString(f"{provider}:{text}", provenance=f"live:{provider}")

    monkeypatch.setattr(desk, "translate_text", chain_translate)
    res_chain = desk.translate_lemmas_fast_path(["gamma"], "en", "ru", config, paths, "google, deepl")
    assert "google, deepl" not in seen_providers
    assert res_chain["gamma"] == "deepl:gamma"

    # (c) Offline bypass parity in strategy='chain'
    desk.clear_provider_cooldowns(config=config, resolved_paths=paths)
    offline_providers = []
    monkeypatch.setattr(desk, "is_network_online_multi", lambda **_kw: False)
    monkeypatch.setattr(
        desk,
        "translate_text",
        lambda text, s, t, c, r, provider="google", **_kw: offline_providers.append(provider)
        or desk.ProvenanceString(f"{provider}:{text}", provenance=f"live:{provider}"),
    )
    res_off = desk.translate_lemmas_fast_path(["delta"], "en", "ru", config, paths, "google")
    assert offline_providers == ["argos"]
    assert res_off["delta"] == "argos:delta"


def test_pipe_l1_der_tooltips_gate_russian_text_on_target_lang_and_scope_to_sentence(tmp_path):
    """PIPE-L1: der tooltips must not inject Russian strings for target_lang='en' and must scope per sentence."""
    config, paths, adapter = _build_env(tmp_path)
    config.set("settings", "default_language", "de")
    config.set("settings", "default_target_language", "en")
    config.read_dict({
        "multi_sentence": {"enabled": "true", "delivery_mode": "container", "deduplication_scope": "sentence"},
    })
    zid = "20261127200322"
    text = "Der Mann.\nDie Frau."
    adapter.save_session(
        session_zid=zid,
        slug="der-en",
        source_language="de",
        target_language="en",
        text_mode="multi",
        source_raw_text=text,
        sentences=[
            {"sentence_index": 1, "sentence_source": "Der Mann.", "sentence_destination": "The man."},
            {"sentence_index": 2, "sentence_source": "Die Frau.", "sentence_destination": "The woman."},
        ],
        words=[
            {"sentence_index": 1, "token_order": 0, "lemma": "der", "quotation": "Der", "inflected_form": "Der", "word_destination": "the", "pos": "art."},
            {"sentence_index": 2, "token_order": 0, "lemma": "der", "quotation": "Die", "inflected_form": "Die", "word_destination": "the", "pos": "art."},
        ],
    )

    html_out = desk.run_render_flow(
        text=text,
        language="de",
        zid=zid,
        text_mode="multi",
        config=config,
        resolved_paths=paths,
        theme="dark",
        tsv_path=zid,
        spawn_children=False,
        seq_num=1,
        wordfill_cfg={"enabled": False},
    )
    assert "определенный артикль" not in html_out
    assert "который" not in html_out
    assert "тот" not in html_out
    cards = {c["sentence_idx"]: c for c in island(html_out, "sentence-cards")}
    sent1_occ = cards[1]["words"][0]["occurrence_data"] or {}
    # Sentence 1 'der' row must not include sentence 2's 'die' occurrence
    assert all((v.get("form") or "").lower() != "die" for v in sent1_occ.values())


def test_pipe_l2_update_session_and_project_noop_on_unknown_fields_and_tree_respects_max_depth_and_cycles(tmp_path):
    """PIPE-L2: unknown-only update_session/update_project must return False without mutating updated_at; tree enforces max_depth and cycles."""
    _, _, adapter = _build_env(tmp_path)
    db = adapter.db
    zid = "20261127200323"
    adapter.save_session(
        session_zid=zid,
        slug="noop-test",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Haus.",
        sentences=[{"sentence_index": 1, "sentence_source": "Haus."}],
        words=[],
    )
    before_sess = db.get_session(zid)
    assert db.update_session(zid, {"nonexistent_col": "x"}) is False
    assert db.get_session(zid)["updated_at"] == before_sess["updated_at"]

    p1 = db.create_project("Root")
    p2 = db.create_project("Child", parent_id=p1)
    p3 = db.create_project("Grandchild", parent_id=p2)
    before_proj = db.get_project(p1)
    assert db.update_project(p1, {"nonexistent_col": "x"}) is False
    assert db.get_project(p1)["updated_at"] == before_proj["updated_at"]

    # max_depth=1 must cap grandchildren
    tree_d1 = db.get_project_tree(project_id=p1, max_depth=1)
    assert len(tree_d1[0]["children"]) == 1
    assert tree_d1[0]["children"][0]["children"] == []

    # Cycle protection: set p1.parent_id = p2 and verify get_project_tree returns JSON-serializable acyclic tree
    with db.get_connection() as conn:
        conn.execute("UPDATE projects SET parent_id = ? WHERE id = ?", (p2, p1))
    cyclic_tree = db.get_project_tree(project_id=p1, max_depth=5)
    serialized = json.dumps(cyclic_tree)
    assert serialized


def test_pipe_l3_tsv_core_edit_save_pads_short_rows(tmp_path):
    """PIPE-L3: TSV core_edit_save must pad short rows before assigning edited cells."""
    config, paths, _ = _build_env(tmp_path, backend="tsv")
    tsv_path = tmp_path / "20261127200324-short.de.tsv"
    tsv_path.write_text(
        "TokenOrder\tWordSource\tWordDestination\tWordSourceIPA\n"
        "0\tHaus\n",
        encoding="utf-8",
    )
    res = desk.core_edit_save(
        tsv_path,
        [{"row_id": 0, "column": "WordSourceIPA", "value": "/haʊs/"}],
        config,
        paths,
        zid="20261127200324",
    )
    assert res["status"] == "success"
    _, headers, rows = desk.load_tsv_rows(tsv_path)
    assert rows[0][headers.index("WordSourceIPA")] == "/haʊs/"


def test_pipe_l4_sanitize_extra_fields_preserves_falsy_outer_values_and_merges_duplicate_nested_keys():
    """PIPE-L4: sanitize_extra_fields preserves explicit falsy outer values and merges duplicate nested extra_fields keys."""
    out_falsy = sanitize_extra_fields({"flag": False, "count": 0, "note": "", "extra_fields": {"flag": True, "count": 5, "note": "inner"}})
    assert out_falsy["flag"] is False
    assert out_falsy["count"] == 0
    assert out_falsy["note"] == ""

    out_dup = sanitize_extra_fields({"extra_fields": {"a": 1}, "extrafields": {"b": 2}})
    assert out_dup["a"] == 1
    assert out_dup["b"] == 2
