"""Acceptance tests for honoring ordinary enrichment vs explicit reprocessing policy.

Spec: specs/fs-free-sqlite-pipeline/spec.md
Requirement: Honor ordinary enrichment versus explicit reprocessing
"""
import configparser
from contextlib import contextmanager
from pathlib import Path
from http.server import BaseHTTPRequestHandler
import json
import threading

import pytest

import kardenwort_desk as desk
import kardenwort_reword as reword
from tests.browser_test_server import create_browser_test_server
from tests.enrichment_test_support import enrichment_engine
from tests.test_structured_enrichment import control_writes, prohibited


ZID = "20261122101840"
HEADERS = [
    "TokenOrder",
    "Quotation",
    "WordSource",
    "WordDestination",
    "SentenceSourceIndex",
    "SentenceSource",
    "WordSourceIPA",
    "WordSourceMorphologyAI",
]


def _build_sqlite_env(tmp_path: Path, mapping_text: str | None = None):
    mapping_file = tmp_path / "mapping.ini"
    mapping_file.write_text(
        mapping_text
        or (
            "[desk_columns]\n"
            "WordSource=lemma\n"
            "WordDestination=word_translation\n"
            "SentenceSourceIndex=sentence_index\n"
            "SentenceSource=sentence\n"
            "WordSourceIPA=ipa\n"
            "WordSourceMorphologyAI=morphology\n"
            "[desk_editable]\n"
            "editable_columns=WordDestination\n"
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "fixture.db"
    config = configparser.ConfigParser()
    config.read_dict({
        "storage": {
            "backend": "sqlite",
            "sqlite_db_path": str(db_path),
            "fallback_to_tsv": "false",
        },
        "settings": {
            "default_language": "de",
            "default_target_language": "en",
            "anki_mapping_file": str(mapping_file),
        },
        "pipeline": {"lemma_reprocess_provider": "intellifiller"},
        "languages": {"de_prompt": "fixture"},
        "classification": {"enabled": "false"},
        "wordfill": {"enabled": "false"},
        "triggers": {
            "run_text_translation": "manual",
            "run_lemma_base_translation": "manual",
            "run_lemma_enrichment": "manual",
        },
        "services": {"intellifiller_server_url": ""},
        "environment": {"kardenwort_workspace": str(tmp_path)},
    })
    paths = {
        "sqlite_db_path": db_path,
        "storage_backend": "sqlite",
        "results_dir": tmp_path,
        "generated_results_dir": tmp_path,
        "base_dir": tmp_path,
        "anki_mapping_file": mapping_file,
        "kardenwort_workspace": tmp_path,
    }
    adapter = desk.SqliteStorageAdapter(config, paths)
    return config, paths, adapter


def _seed_session(adapter, tmp_path: Path, rows, *, text: str, headers=None, provenances=None):
    headers = list(headers or HEADERS)
    sent_idx_col = headers.index("SentenceSourceIndex")
    sent_src_col = headers.index("SentenceSource")
    sentences = []
    seen_sentences = set()
    for row in rows:
        s_idx = int(row[sent_idx_col])
        if s_idx not in seen_sentences:
            seen_sentences.add(s_idx)
            sentences.append({"sentence_index": s_idx, "sentence_source": row[sent_src_col]})
    adapter.save_session(
        session_zid=ZID,
        slug="reprocess-policy",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text=text,
        headers=headers,
        data_rows=[list(r) for r in rows],
        comments=[],
        working_tsv_path=tmp_path / f"{ZID}.de.tsv",
        row_provenances=provenances or {i: "fixture:initial" for i in range(len(rows))},
        sentences=sentences,
    )


def _cold_words_by_key(config, paths, session_zid=ZID):
    cold = desk.SqliteStorageAdapter(config, paths)
    words = cold.db.get_words_by_session(session_zid)
    return {(int(w["sentence_index"]), int(w["token_order"])): w for w in words}


class _ErrorEnrichmentHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        assert self.path == "/enrich"
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(payload)
        body = json.dumps({"status": "error", "code": "ERR_LLM_REQUEST", "message": "fixture failure"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@contextmanager
def _failing_enrichment_engine(config):
    server = create_browser_test_server(_ErrorEnrichmentHandler)
    server.requests = []
    config.set("services", "intellifiller_server_url", f"http://127.0.0.1:{server.server_port}")
    try:
        server.start()
        yield server
    finally:
        server.server_close()


def test_ordinary_enrichment_skips_filled_target_and_preserves_value_and_provenance(tmp_path):
    """Scenario: Ordinary enrichment — filled target with reprocess=False is not dispatched or replaced."""
    config, paths, adapter = _build_sqlite_env(tmp_path)
    _seed_session(
        adapter,
        tmp_path,
        [
            ["0", "Haus", "Haus", "house", "1", "Haus.", "/haus/", "noun"],
            ["0", "Baum", "Baum", "tree", "2", "Baum.", "/baum/", "noun"],
        ],
        text="Haus. Baum.",
    )
    before = _cold_words_by_key(config, paths)

    with enrichment_engine(config) as engine:
        engine.release.set()
        with control_writes() as writes:
            ok = adapter.enrich_session_intellifiller(
                session_zid=ZID,
                prompt_name="fixture",
                selected_rows=[0],
                reprocess=False,
            )

    assert ok is True
    assert engine.requests == [], f"Filled target should not be dispatched: {engine.requests}"
    after = _cold_words_by_key(config, paths)
    assert after[(1, 0)]["word_destination"] == "house"
    assert after[(1, 0)]["word_provenance"] == "fixture:initial"
    assert after[(1, 0)] == before[(1, 0)]
    assert after[(2, 0)] == before[(2, 0)]
    assert prohibited(writes) == []


def test_ordinary_enrichment_whole_session_skips_all_filled_targets(tmp_path):
    """Scenario: Ordinary enrichment — whole-session call with reprocess=False skips all filled rows."""
    config, paths, adapter = _build_sqlite_env(tmp_path)
    _seed_session(
        adapter,
        tmp_path,
        [
            ["0", "Haus", "Haus", "house", "1", "Haus.", "/haus/", "noun"],
            ["0", "Baum", "Baum", "tree", "2", "Baum.", "/baum/", "noun"],
        ],
        text="Haus. Baum.",
    )
    before = _cold_words_by_key(config, paths)

    with enrichment_engine(config) as engine:
        engine.release.set()
        with control_writes() as writes:
            ok = adapter.enrich_session_intellifiller(
                session_zid=ZID,
                prompt_name="fixture",
                reprocess=False,
            )

    assert ok is True
    assert engine.requests == []
    assert _cold_words_by_key(config, paths) == before
    assert prohibited(writes) == []


def test_missing_target_field_enriches_only_unfinished_rows_and_preserves_filled_unrelated_targets(tmp_path):
    """Scenario: Missing target field — unfinished targets (empty despite filled IPA, or [FAILED]) are enriched."""
    config, paths, adapter = _build_sqlite_env(tmp_path)
    _seed_session(
        adapter,
        tmp_path,
        [
            ["0", "Haus", "Haus", "house", "1", "Haus.", "/haus/", "noun"],
            ["0", "Baum", "Baum", "", "2", "Baum.", "/baum/", ""],
            ["0", "Wald", "Wald", "[FAILED]", "3", "Wald.", "", ""],
        ],
        text="Haus. Baum. Wald.",
        provenances={0: "fixture:initial", 1: "fixture:initial", 2: "live:google"},
    )
    before_haus = _cold_words_by_key(config, paths)[(1, 0)]

    with enrichment_engine(config) as engine:
        engine.release.set()
        with control_writes() as writes:
            ok = adapter.enrich_session_intellifiller(
                session_zid=ZID,
                prompt_name="fixture",
                reprocess=False,
            )

    assert ok is True
    assert len(engine.requests) == 1
    dispatched_lemmas = [row["WordSource"] for row in engine.requests[0]["rows"]]
    assert dispatched_lemmas == ["Baum", "Wald"]

    after = _cold_words_by_key(config, paths)
    assert after[(1, 0)] == before_haus
    assert after[(1, 0)]["word_destination"] == "house"
    assert after[(1, 0)]["word_provenance"] == "fixture:initial"
    assert after[(2, 0)]["word_destination"] == "trans_Baum"
    assert after[(2, 0)]["word_provenance"] == "live:intellifiller"
    assert after[(3, 0)]["word_destination"] == "trans_Wald"
    assert after[(3, 0)]["word_provenance"] == "live:intellifiller"
    assert prohibited(writes) == []


def test_ordinary_enrichment_honors_configured_word_translation_role_column(tmp_path):
    """Scenario: Ordinary enrichment — configured word_translation role column governs eligibility."""
    custom_mapping = (
        "[desk_columns]\n"
        "WordSource=lemma\n"
        "WordRussian=word_translation\n"
        "SentenceSourceIndex=sentence_index\n"
        "SentenceSource=sentence\n"
        "WordSourceIPA=ipa\n"
        "WordSourceMorphologyAI=morphology\n"
        "[desk_editable]\n"
        "editable_columns=WordRussian\n"
    )
    config, paths, adapter = _build_sqlite_env(tmp_path, mapping_text=custom_mapping)
    custom_headers = [
        "TokenOrder",
        "Quotation",
        "WordSource",
        "WordRussian",
        "SentenceSourceIndex",
        "SentenceSource",
        "WordSourceIPA",
        "WordSourceMorphologyAI",
    ]
    _seed_session(
        adapter,
        tmp_path,
        [
            ["0", "Haus", "Haus", "дом", "1", "Haus.", "/haus/", "noun"],
            ["0", "Baum", "Baum", "", "2", "Baum.", "/baum/", ""],
        ],
        text="Haus. Baum.",
        headers=custom_headers,
    )
    before_haus = _cold_words_by_key(config, paths)[(1, 0)]
    restored = adapter.restore_session(ZID)
    assert restored["data_rows"][0][restored["headers"].index("WordRussian")] == "дом"
    assert restored["data_rows"][1][restored["headers"].index("WordRussian")] == ""

    def respond_russian(rows):
        return [
            {
                "row_id": row["row_id"],
                "WordRussian": "ru_" + row["WordSource"],
                "WordSourceIPA": "/ipa_" + row["WordSource"] + "/",
            }
            for row in rows
        ]

    with enrichment_engine(config, response=respond_russian) as engine:
        engine.release.set()
        with control_writes() as writes:
            ok = adapter.enrich_session_intellifiller(
                session_zid=ZID,
                prompt_name="fixture",
                reprocess=False,
            )

    assert ok is True
    assert len(engine.requests) == 1
    assert [row["WordSource"] for row in engine.requests[0]["rows"]] == ["Baum"]
    after = _cold_words_by_key(config, paths)
    assert after[(1, 0)] == before_haus
    assert after[(2, 0)]["word_provenance"] == "live:intellifiller"
    assert prohibited(writes) == []


def test_explicit_reprocess_replaces_selected_filled_target_while_preserving_unrelated_occurrence(tmp_path):
    """Scenario: Explicit reprocess (valid adjacent control) — reprocess=True replaces only selected target."""
    config, paths, adapter = _build_sqlite_env(tmp_path)
    _seed_session(
        adapter,
        tmp_path,
        [
            ["0", "Haus", "Haus", "house", "1", "Haus.", "/haus/", "noun"],
            ["0", "Baum", "Baum", "tree", "2", "Baum.", "/baum/", "noun"],
        ],
        text="Haus. Baum.",
    )
    before_baum = _cold_words_by_key(config, paths)[(2, 0)]

    with enrichment_engine(config) as engine:
        engine.release.set()
        with control_writes() as writes:
            ok = adapter.enrich_session_intellifiller(
                session_zid=ZID,
                prompt_name="fixture",
                target_coordinates=[{"sentence_idx": 1, "token_order": 0}],
                reprocess=True,
            )

    assert ok is True
    assert len(engine.requests) == 1
    assert [row["WordSource"] for row in engine.requests[0]["rows"]] == ["Haus"]
    after = _cold_words_by_key(config, paths)
    assert after[(1, 0)]["word_destination"] == "trans_Haus"
    assert after[(1, 0)]["word_provenance"] == "live:intellifiller"
    assert after[(2, 0)] == before_baum
    assert prohibited(writes) == []


def test_provider_failure_on_eligible_row_preserves_persisted_state(tmp_path):
    """Valid adjacent control — explicit provider failure leaves all rows and provenance unchanged."""
    config, paths, adapter = _build_sqlite_env(tmp_path)
    _seed_session(
        adapter,
        tmp_path,
        [
            ["0", "Haus", "Haus", "house", "1", "Haus.", "/haus/", "noun"],
            ["0", "Baum", "Baum", "", "2", "Baum.", "", ""],
        ],
        text="Haus. Baum.",
    )
    before = _cold_words_by_key(config, paths)

    with _failing_enrichment_engine(config) as engine:
        with control_writes() as writes:
            with pytest.raises(reword.InvalidRewordTarget, match="IntelliFiller structured request failed"):
                adapter.enrich_session_intellifiller(
                    session_zid=ZID,
                    prompt_name="fixture",
                    selected_rows=[1],
                    reprocess=False,
                )

    assert len(engine.requests) == 1
    assert _cold_words_by_key(config, paths) == before
    assert prohibited(writes) == []


def test_prompt_specific_ipa_extraction_uses_ipa_target_column_and_keeps_translation(tmp_path):
    """Prompt-specific ipa_extraction checks WordSourceIPA, not WordDestination."""
    config, paths, adapter = _build_sqlite_env(tmp_path)
    _seed_session(
        adapter,
        tmp_path,
        [
            ["0", "Haus", "Haus", "house", "1", "Haus.", "", "noun"],
            ["0", "Baum", "Baum", "", "2", "Baum.", "/baum/", ""],
        ],
        text="Haus. Baum.",
        provenances={0: "fixture:initial", 1: "fixture:initial"},
    )
    before_baum = _cold_words_by_key(config, paths)[(2, 0)]

    def respond_ipa(rows):
        return [{"row_id": row["row_id"], "WordSourceIPA": "/ipa_" + row["WordSource"] + "/"} for row in rows]

    with enrichment_engine(config, response=respond_ipa) as engine:
        engine.release.set()
        with control_writes() as writes:
            ok = adapter.enrich_session_intellifiller(
                session_zid=ZID,
                prompt_name="ipa_extraction",
                reprocess=False,
            )

    assert ok is True
    assert len(engine.requests) == 1
    assert [row["WordSource"] for row in engine.requests[0]["rows"]] == ["Haus"]
    after = _cold_words_by_key(config, paths)
    assert after[(1, 0)]["word_destination"] == "house"
    assert after[(1, 0)]["ipa"] == "/ipa_Haus/"
    assert after[(1, 0)]["word_provenance"] == "live:intellifiller"
    assert after[(2, 0)] == before_baum
    assert prohibited(writes) == []


def test_async_mixed_enrichment_filters_before_dispatch_and_commits_only_eligible_rows(tmp_path):
    """Deterministic async coordination: filled row is excluded before HTTP dispatch and survives concurrent edit."""
    from concurrent.futures import ThreadPoolExecutor

    config, paths, adapter = _build_sqlite_env(tmp_path)
    _seed_session(
        adapter,
        tmp_path,
        [
            ["0", "Haus", "Haus", "house", "1", "Haus.", "/haus/", "noun"],
            ["0", "Baum", "Baum", "", "2", "Baum.", "/baum/", ""],
            ["0", "Wald", "Wald", "forest", "3", "Wald.", "/wald/", "noun"],
        ],
        text="Haus. Baum. Wald.",
        provenances={0: "fixture:initial", 1: "fixture:initial", 2: "fixture:initial"},
    )
    before = _cold_words_by_key(config, paths)

    with enrichment_engine(config) as engine:
        with control_writes() as writes:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(
                    adapter.enrich_session_intellifiller,
                    session_zid=ZID,
                    prompt_name="fixture",
                    reprocess=False,
                )
                try:
                    with engine.changed:
                        assert engine.changed.wait_for(lambda: len(engine.requests) == 1, 5)
                    # Only unfinished Baum (sentence 2, token 0) is dispatched while engine is held
                    assert [row["WordSource"] for row in engine.requests[0]["rows"]] == ["Baum"]
                    # Concurrent edit to skipped filled Haus persists without being clobbered
                    adapter.db.update_word(before[(1, 0)]["id"], {"word_destination": "manual-house"})
                finally:
                    engine.release.set()
                assert pending.result(timeout=10) is True

    after = _cold_words_by_key(config, paths)
    assert after[(1, 0)]["word_destination"] == "manual-house"
    assert after[(1, 0)]["word_provenance"] == "fixture:initial"
    assert after[(2, 0)]["word_destination"] == "trans_Baum"
    assert after[(2, 0)]["word_provenance"] == "live:intellifiller"
    assert after[(3, 0)] == before[(3, 0)]
    assert prohibited(writes) == []


def test_complete_batch_validation_applies_to_filtered_eligible_subset(tmp_path):
    """Provider returning skipped row_id or omitting eligible row_id is rejected with zero DB writes."""
    config, paths, adapter = _build_sqlite_env(tmp_path)
    _seed_session(
        adapter,
        tmp_path,
        [
            ["0", "Haus", "Haus", "house", "1", "Haus.", "/haus/", "noun"],
            ["0", "Baum", "Baum", "", "2", "Baum.", "", ""],
        ],
        text="Haus. Baum.",
    )
    before = _cold_words_by_key(config, paths)

    # Return wrong row_id (0, which was skipped) instead of eligible row_id (1)
    def wrong_row_id_response(rows):
        return [{"row_id": 0, "WordDestination": "wrong_Haus"}]

    with enrichment_engine(config, response=wrong_row_id_response) as engine:
        engine.release.set()
        with control_writes() as writes:
            with pytest.raises(reword.InvalidRewordTarget, match="Provider changed the requested row batch"):
                adapter.enrich_session_intellifiller(
                    session_zid=ZID,
                    prompt_name="fixture",
                    reprocess=False,
                )

    assert len(engine.requests) == 1
    assert [row["row_id"] for row in engine.requests[0]["rows"]] == [1]
    assert _cold_words_by_key(config, paths) == before
    assert prohibited(writes) == []


def test_pure_eligible_selection_lockstep_and_unfinished_sentinels():
    """Pure functional core: lockstep rows/keys/visuals filtering and unfinished sentinel handling."""
    import kardenwort_enrichment as enrichment

    headers = ["WordSource", "WordDestination", "WordSourceIPA"]
    rows = [
        ["Haus", "house", "/haus/"],
        ["Baum", "", "/baum/"],
        ["Wald", "   ", ""],
        ["Berg", "[FAILED]", ""],
        ["See", "[Error: timeout]", ""],
        ["Fluss", "HTTP 429 Too Many Requests", ""],
        ["Dorf", "<span class='skeleton-loader'></span>", ""],
    ]
    selection = reword.RewordSelection(
        keys=tuple((i + 1, 0) for i in range(len(rows))),
        rows=tuple(range(len(rows))),
        visuals=(0, 2, 4),
        visual_keys=(((1, 0), 0), ((2, 0), 2), ((4, 0), 4)),
    )
    roles = {"word_translation": "WordDestination", "ipa": "WordSourceIPA"}

    filtered = enrichment.eligible_selection(rows, headers, selection, roles, prompt="fixture", reprocess=False)
    assert filtered.rows == (1, 2, 3, 4, 5, 6)
    assert filtered.keys == ((2, 0), (3, 0), (4, 0), (5, 0), (6, 0), (7, 0))
    assert filtered.visuals == (2, 4)
    assert filtered.visual_keys == (((2, 0), 2), ((4, 0), 4))

    explicit = enrichment.eligible_selection(rows, headers, selection, roles, prompt="fixture", reprocess=True)
    assert explicit == selection
