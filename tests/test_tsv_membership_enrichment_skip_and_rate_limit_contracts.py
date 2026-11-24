"""Phase 1 reproduction and contract tests for Task 1.1:
Optional/custom TSV membership, corrupt/missing-session errors, enrichment skip-path
lang initialization, and exact HTTP 429 positive/negative classification.
"""
import configparser
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import kardenwort_alignment as alignment
import kardenwort_desk as desk
import kardenwort_progressive_completion as progressive
import kardenwort_translation_validation as tv
from kardenwort_db import KardenwortDB
from tests.controller_test_support import (
    DeterministicTranslationHandler,
    controller_lifetime,
    owned_configuration,
    translation_service,
)


TSV_ZID = "20261124060001"
NEIGHBOR_ZID = "20261124060002"
MISSING_ZID = "20261124060099"
SQLITE_ZID = "20261124060003"
SQLITE_NEIGHBOR_ZID = "20261124060004"

POSITIVE_FAILURE_MARKERS = [
    pytest.param("HTTP 429 Too Many Requests", id="exact-429"),
    pytest.param("  HTTP 429 Too Many Requests  ", id="whitespace-429"),
    pytest.param("Error calling Gemini API: HTTP 429 Too Many Requests", id="provider-429-full"),
    pytest.param("Error calling provider: HTTP 429", id="provider-429-short"),
    pytest.param("[FAILED]", id="failed-marker"),
    pytest.param("[Error: timeout]", id="error-marker"),
]

NEGATIVE_ORDINARY_TEXTS = [
    pytest.param("HTTP 429 error code", id="http-429-phrase"),
    pytest.param("See HTTP 429 Too Many Requests in the manual", id="manual-mention"),
    pytest.param("Error calling a friend", id="error-calling-phrase"),
    pytest.param("429", id="bare-429"),
    pytest.param("HTTP", id="bare-http"),
    pytest.param("HTTP 429", id="short-http-429"),
    pytest.param("HTTP 404 Not Found", id="http-404"),
    pytest.param("Google rate limit is HTTP 429.", id="sentence-mention"),
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tsv_adapter(tmp_path: Path, *, mapping_text: str | None = None):
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    mapping_file = tmp_path / "mapping.ini"
    mapping_file.write_text(
        mapping_text
        or (
            "[desk_columns]\n"
            "WordSource=lemma\n"
            "WordDestination=word_translation\n"
            "SentenceSourceIndex=sentence_index\n"
            "SentenceSource=sentence\n"
            "DeskSelected=selected\n"
        ),
        encoding="utf-8",
    )
    cfg = configparser.ConfigParser()
    cfg.read_dict({
        "storage": {"backend": "tsv"},
        "settings": {
            "default_language": "de",
            "default_target_language": "ru",
            "anki_mapping_file": str(mapping_file),
        },
        "project_structure": {"generated_results_dir": str(results_dir)},
    })
    paths = {
        "storage_backend": "tsv",
        "results_dir": results_dir,
        "generated_results_dir": results_dir,
        "anki_mapping_file": mapping_file,
        "base_dir": tmp_path,
    }
    return desk.TsvStorageAdapter(cfg, paths), cfg, paths, results_dir, mapping_file


def _http_get_json(url: str, token: str):
    req = Request(url, headers={"X-API-Token": token}, method="GET")
    try:
        resp = urlopen(req, timeout=10)
    except HTTPError as err:
        resp = err
    with resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return resp.status, json.loads(raw)


def _read_update_payloads(tsv_path: Path):
    updates_dir = tsv_path.parent / f"{tsv_path.stem}.updates"
    if not updates_dir.exists():
        return []
    payloads = []
    for path in sorted(updates_dir.glob("*.js")):
        raw = path.read_text(encoding="utf-8")
        if "window.receiveUpdate(" in raw:
            body = raw.split("window.receiveUpdate(", 1)[1].rsplit(");", 1)[0]
            payloads.append(json.loads(body))
    return payloads


# ---------------------------------------------------------------------------
# 1. Optional and custom-column TSV membership contracts
# ---------------------------------------------------------------------------


def test_tsv_missing_default_selected_column_returns_empty_membership_without_modifying_files(tmp_path):
    """Unannotated TSV without DeskSelected returns {} directly and via HTTP without altering bytes."""
    adapter, _cfg, _paths, results_dir, mapping_file = _tsv_adapter(tmp_path)
    target = results_dir / f"{TSV_ZID}-haus.de.tsv"
    neighbor = results_dir / f"{NEIGHBOR_ZID}-baum.de.tsv"
    target.write_text(
        "TokenOrder\tQuotation\tWordSource\tWordDestination\tSentenceSourceIndex\tSentenceSource\n"
        "0\tHaus\tHaus\thouse\t1\tHaus.\n",
        encoding="utf-8",
    )
    neighbor.write_text(
        "TokenOrder\tQuotation\tWordSource\tWordDestination\tDeskSelected\n"
        "0\tBaum\tBaum\ttree\t1\n",
        encoding="utf-8",
    )
    target_hash = _sha256(target)
    neighbor_hash = _sha256(neighbor)
    mapping_hash = _sha256(mapping_file)

    assert adapter.get_shared_selection(TSV_ZID) == {}
    assert _sha256(target) == target_hash
    assert _sha256(neighbor) == neighbor_hash
    assert _sha256(mapping_file) == mapping_hash

    with translation_service(DeterministicTranslationHandler) as translator:
        with controller_lifetime(tmp_path / "http", translator, backend="tsv") as (url, server):
            http_results = Path(server.resolved_paths["results_dir"])
            http_tsv = http_results / f"{TSV_ZID}-haus.de.tsv"
            http_neighbor = http_results / f"{NEIGHBOR_ZID}-baum.de.tsv"
            http_tsv.write_text(
                "TokenOrder\tQuotation\tWordSource\tWordDestination\tSentenceSourceIndex\tSentenceSource\n"
                "0\tHaus\tHaus\thouse\t1\tHaus.\n",
                encoding="utf-8",
            )
            http_neighbor.write_text(
                "TokenOrder\tQuotation\tWordSource\tWordDestination\tDeskSelected\n"
                "0\tBaum\tBaum\ttree\t1\n",
                encoding="utf-8",
            )
            http_tsv_hash = _sha256(http_tsv)
            http_neighbor_hash = _sha256(http_neighbor)

            status, body = _http_get_json(f"{url}/internal/selection?session_zid={TSV_ZID}", server.api_key)
            assert status == 200, body
            assert body["status"] == "success"
            assert body["data"]["selection"] == {}
            assert _sha256(http_tsv) == http_tsv_hash
            assert _sha256(http_neighbor) == http_neighbor_hash


def test_tsv_missing_custom_selected_column_returns_empty_membership_and_keeps_bytes(tmp_path):
    """Unannotated TSV missing a configured custom selected role returns {} without mutating TSV."""
    custom_mapping = (
        "[desk_columns]\n"
        "WordSource=lemma\n"
        "WordDestination=word_translation\n"
        "CustomPick=selected\n"
    )
    adapter, _cfg, _paths, results_dir, _mapping = _tsv_adapter(tmp_path, mapping_text=custom_mapping)
    target = results_dir / f"{TSV_ZID}-custom.de.tsv"
    target.write_text(
        "TokenOrder\tQuotation\tWordSource\tWordDestination\tDeskSelected\n"
        "0\tHaus\tHaus\thouse\t1\n",
        encoding="utf-8",
    )
    before_hash = _sha256(target)

    # Configured selected role is CustomPick (absent); unrelated DeskSelected must not be misread.
    assert adapter.get_shared_selection(TSV_ZID) == {}
    assert _sha256(target) == before_hash


def test_tsv_custom_selected_column_projects_real_selection_and_selection_columns_returns_pair(tmp_path):
    """When the configured custom selected role is present, real membership is projected."""
    custom_mapping = (
        "[desk_columns]\n"
        "WordSource=lemma\n"
        "WordDestination=word_translation\n"
        "CustomPick=selected\n"
    )
    adapter, _cfg, _paths, results_dir, _mapping = _tsv_adapter(tmp_path, mapping_text=custom_mapping)
    target = results_dir / f"{TSV_ZID}-custom-real.de.tsv"
    target.write_text(
        "TokenOrder\tWordSource\tWordDestination\tCustomPick\n"
        "0\tHaus\thouse\t1\n"
        "1\tBaum\ttree\t0\n",
        encoding="utf-8",
    )
    before_hash = _sha256(target)

    pair = adapter._selection_columns(["TokenOrder", "WordSource", "WordDestination", "CustomPick"])
    assert pair == (1, 3)
    assert adapter.get_shared_selection(TSV_ZID) == {"haus": True, "baum": False}
    assert _sha256(target) == before_hash


@pytest.mark.parametrize("include_selected", [False, True], ids=["without-selected", "with-selected"])
def test_tsv_empty_rows_returns_empty_membership(tmp_path, include_selected):
    """Header-only TSV with zero data rows returns {} cleanly."""
    adapter, _cfg, _paths, results_dir, _mapping = _tsv_adapter(tmp_path)
    target = results_dir / f"{TSV_ZID}-empty.de.tsv"
    header = "TokenOrder\tWordSource\tWordDestination" + ("\tDeskSelected" if include_selected else "")
    target.write_text(header + "\n", encoding="utf-8")

    assert adapter.get_shared_selection(TSV_ZID) == {}


def test_tsv_missing_and_corrupt_session_membership_fails_explicitly(tmp_path):
    """Missing session, invalid ZID, missing lemma role, empty headers, and truncated rows fail explicitly."""
    adapter, _cfg, _paths, results_dir, _mapping = _tsv_adapter(tmp_path)

    with pytest.raises(desk.StructuredError) as exc_invalid:
        adapter.get_shared_selection("not-a-zid")
    assert exc_invalid.value.error_code == desk.ErrorCode.INVALID_PAYLOAD

    with pytest.raises(desk.StructuredError) as exc_missing:
        adapter.get_shared_selection(MISSING_ZID)
    assert exc_missing.value.error_code == desk.ErrorCode.NOT_FOUND

    # Selected column exists, but required lemma column is missing -> explicit failure, not {}
    missing_lemma = results_dir / "20261124060011-nolemma.de.tsv"
    missing_lemma.write_text("TokenOrder\tDeskSelected\n0\t1\n", encoding="utf-8")
    with pytest.raises(desk.StructuredError) as exc_nolemma:
        adapter.get_shared_selection("20261124060011")
    assert exc_nolemma.value.error_code == desk.ErrorCode.QUERY_FAILED

    # Selected column exists, but row is truncated before selected column -> explicit failure
    truncated = results_dir / "20261124060012-trunc.de.tsv"
    truncated.write_text("TokenOrder\tWordSource\tDeskSelected\n0\tHaus\n", encoding="utf-8")
    with pytest.raises(desk.StructuredError) as exc_trunc:
        adapter.get_shared_selection("20261124060012")
    assert exc_trunc.value.error_code == desk.ErrorCode.QUERY_FAILED

    # Empty/headerless corrupt TSV -> explicit failure
    empty_file = results_dir / "20261124060013-corrupt.de.tsv"
    empty_file.write_text("", encoding="utf-8")
    with pytest.raises(desk.StructuredError) as exc_empty:
        adapter.get_shared_selection("20261124060013")
    assert exc_empty.value.error_code in (desk.ErrorCode.QUERY_FAILED, desk.ErrorCode.INVALID_STATE, desk.ErrorCode.NOT_FOUND)


# ---------------------------------------------------------------------------
# 2. Enrichment zero-row skip-path `lang` initialization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend", ["sqlite", "tsv"])
def test_enrichment_skip_path_initializes_lang_before_zero_row_branch(tmp_path, monkeypatch, backend):
    """When all rows are already enriched, _progressive_worker_stage_enrichment skips cleanly without UnboundLocalError."""
    root = tmp_path / backend
    root.mkdir(parents=True, exist_ok=True)
    config, resolved_paths, _ = owned_configuration(root, "", backend)
    config.read_dict({"server": {"update_delivery": "desktop_file"}})

    headers = [
        "TokenOrder",
        "Quotation",
        "WordSource",
        "WordDestination",
        "WordSourceIPA",
        "WordSourceMorphologyAI",
        "SentenceSourceIndex",
        "SentenceSource",
        "SentenceDestination",
    ]
    role_fields = {
        "lemma": "WordSource",
        "word_translation": "WordDestination",
        "ipa": "WordSourceIPA",
        "morphology": "WordSourceMorphologyAI",
        "sentence_index": "SentenceSourceIndex",
        "sentence_source": "SentenceSource",
        "sentence_destination": "SentenceDestination",
    }
    rows = [
        ["0", "Haus", "Haus", "дом", "/haʊ̯s/", "Subst.Neut", "1", "Das Haus.", "Дом."],
    ]

    tsv_path = Path(resolved_paths["results_dir"]) / f"{SQLITE_ZID}-skip.de.tsv"
    if backend == "sqlite":
        db = KardenwortDB(db_path=resolved_paths["sqlite_db_path"])
        db.run_migrations()
        adapter = desk.SqliteStorageAdapter(config, resolved_paths, db_path=resolved_paths["sqlite_db_path"])
        adapter.save_session(
            session_zid=SQLITE_ZID,
            slug="skip",
            source_language="de",
            target_language="ru",
            text_mode="single",
            source_raw_text="Das Haus.",
            headers=headers,
            data_rows=rows,
        )
        before_bundle = db.get_session_bundle(SQLITE_ZID)
    else:
        desk.save_tsv_rows_safely(tsv_path, [], headers, rows)
        before_hash = _sha256(tsv_path)

    provider_calls = []
    monkeypatch.setattr(
        desk,
        "query_intellifiller_server",
        lambda **kwargs: provider_calls.append(kwargs),
    )

    args = SimpleNamespace(language="de", target_lang="ru", prompt="fixture", zid=SQLITE_ZID, trace_id=f"{SQLITE_ZID}:skip")
    result_rows = desk._progressive_worker_stage_enrichment(
        tsv_path, args, config, resolved_paths, [list(r) for r in rows], list(headers), dict(role_fields), stage_name="enrichment"
    )

    assert result_rows == rows
    assert provider_calls == []
    payloads = _read_update_payloads(tsv_path)
    assert payloads, "Expected a stage='enrichment' update payload on skip path"
    assert payloads[-1]["stage"] == "enrichment"
    assert payloads[-1]["status"] == "success", payloads[-1]
    assert payloads[-1].get("error") is None
    if backend == "sqlite":
        assert db.get_session_bundle(SQLITE_ZID) == before_bundle
    else:
        assert _sha256(tsv_path) == before_hash


# ---------------------------------------------------------------------------
# 3. Exact HTTP 429 positive/negative classification across pipeline consumers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("marker", POSITIVE_FAILURE_MARKERS)
def test_exact_failure_predicates_classify_positive_failures_across_alignment_and_completion(marker):
    """Recognized whole-field failures (including HTTP 429) are unfinished across alignment and wordfill."""
    assert tv.is_stored_translation_failure(marker) is True
    assert tv.is_unfinished_translation(marker) is True
    assert alignment.failed_sentence_text(marker) is True
    assert alignment.unresolved_sentences([{"sentence_index": 1, "sentence_source": "Haus.", "sentence_destination": marker}]) == [
        {"sentence_index": 1, "sentence_source": "Haus.", "sentence_destination": marker}
    ]
    assert alignment.display_sentence_text({"0": marker, "1": "Tree."}) == "Tree."

    headers = ["WordSource", "WordDestination"]
    roles = {"lemma": "WordSource", "word_translation": "WordDestination"}
    # Stored failure in target cell is replaceable by valid corpus match
    patch = progressive.wordfill_patch(
        ["Haus", marker], headers, roles, {"WordDestination": "house"}, provenance="corpus:wordfill"
    )
    assert ("word_destination", 1, "house") in patch
    # Recognized failure in incoming corpus match is rejected
    incoming_bad = progressive.wordfill_patch(
        ["Haus", ""], headers, roles, {"WordDestination": marker}, provenance="corpus:wordfill"
    )
    assert incoming_bad == []


@pytest.mark.parametrize("ordinary", NEGATIVE_ORDINARY_TEXTS)
def test_exact_failure_predicates_preserve_ordinary_text_mentioning_http_429(ordinary):
    """Ordinary text discussing HTTP 429 or provider errors must remain valid everywhere."""
    assert tv.is_stored_translation_failure(ordinary) is False
    assert tv.is_unfinished_translation(ordinary) is False
    assert alignment.failed_sentence_text(ordinary) is False
    assert alignment.unresolved_sentences([{"sentence_index": 1, "sentence_source": "Haus.", "sentence_destination": ordinary}]) == []
    assert alignment.display_sentence_text({"0": ordinary}) == ordinary

    headers = ["WordSource", "WordDestination"]
    roles = {"lemma": "WordSource", "word_translation": "WordDestination"}
    # Existing legitimate text must not be overwritten by wordfill
    assert progressive.wordfill_patch(
        ["Haus", ordinary], headers, roles, {"WordDestination": "replacement"}, provenance="corpus:wordfill"
    ) == []
    # Incoming legitimate text must be accepted by wordfill
    accepted = progressive.wordfill_patch(
        ["Haus", ""], headers, roles, {"WordDestination": ordinary}, provenance="corpus:wordfill"
    )
    assert ("word_destination", 1, ordinary) in accepted


@pytest.mark.parametrize("stored_failure", [
    "HTTP 429 Too Many Requests",
    "Error calling Gemini API: HTTP 429 Too Many Requests",
    "[FAILED]",
])
def test_sqlite_save_session_does_not_resurrect_stale_translation_on_explicit_failure(tmp_path, stored_failure):
    """Explicitly saving a recognized failure must not silently resurrect the previous valid translation."""
    db_path = tmp_path / "no_resurrect.db"
    adapter = desk.SqliteStorageAdapter(db_path=db_path)
    headers = ["TokenOrder", "Quotation", "WordSource", "WordDestination", "SentenceSourceIndex", "SentenceSource", "SentenceDestination"]

    adapter.save_session(
        session_zid=SQLITE_ZID,
        slug="resurrect",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Haus.",
        headers=headers,
        data_rows=[["0", "Haus", "Haus", "old-house", "1", "Haus.", "Old house."]],
        row_provenances={(1, 0): "live:google"},
        text_provenance="live:google",
    )
    adapter.save_session(
        session_zid=SQLITE_NEIGHBOR_ZID,
        slug="neighbor",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Baum.",
        headers=headers,
        data_rows=[["0", "Baum", "Baum", "tree", "1", "Baum.", "Tree."]],
        row_provenances={(1, 0): "live:deepl"},
        text_provenance="live:deepl",
    )
    neighbor_before = KardenwortDB(db_path=db_path).get_session_bundle(SQLITE_NEIGHBOR_ZID)

    # Save with explicit recognized failure in WordDestination and SentenceDestination
    adapter.save_session(
        session_zid=SQLITE_ZID,
        slug="resurrect",
        source_language="de",
        target_language="en",
        text_mode="single",
        source_raw_text="Haus.",
        headers=headers,
        data_rows=[["0", "Haus", "Haus", stored_failure, "1", "Haus.", stored_failure]],
    )

    cold = KardenwortDB(db_path=db_path)
    bundle = cold.get_session_bundle(SQLITE_ZID)
    assert bundle["words"][0]["word_destination"] != "old-house", (
        f"save_session resurrected stale 'old-house' when saving {stored_failure!r}"
    )
    assert bundle["sentences"][0]["sentence_destination"] != "Old house.", (
        f"save_session resurrected stale sentence translation when saving {stored_failure!r}"
    )
    assert cold.get_session_bundle(SQLITE_NEIGHBOR_ZID) == neighbor_before
