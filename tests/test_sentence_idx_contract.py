from pathlib import Path
import pytest
import kardenwort_desk
import kardenwort_controller
from kardenwort_controller import SessionArbiter
from kardenwort_desk import StructuredError


def _make_reword_arbiter(tmp_path, monkeypatch):
    config_path = tmp_path / "config.ini"
    mapping_path = tmp_path / "mapping.ini"
    mapping_path.write_text(
        "[roles]\nlemma=WordSource\nword_translation=WordDestination\nipa=WordSourceIPA\nmorphology=WordSourceMorphologyAI\nsentence_index=SentenceSourceIndex\n[fields]\nTokenOrder=\nWordSource=\nWordDestination=\nWordSourceIPA=\nWordSourceMorphologyAI=\nSentenceSourceIndex=\n",
        encoding="utf-8"
    )

    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)

    # Put a full-quality match in WordFill corpus
    corpus_tsv = corpus_dir / "20260901120000-corpus.de.tsv"
    corpus_tsv.write_text(
        "TokenOrder\tWordSource\tWordDestination\tWordSourceIPA\tWordSourceMorphologyAI\tSentenceSourceIndex\n"
        "0\tLieferpartner\tстарый_перевод\t/ˈliːfɐˌpaʁtnɐ/\tSubstantiv, Maskulinum\t1\n",
        encoding="utf-8"
    )

    config_path.write_text(f"""[pipeline]
text_base_provider=google
[storage]
backend=tsv
[wordfill]
enabled=true
corpus_dir={corpus_dir.as_posix()}
target_quality=full
[settings]
default_language=de
default_target_language=ru
anki_mapping_file={mapping_path.as_posix()}
[environment]
kardenwort_workspace={tmp_path.as_posix()}
[languages]
de_prompt=test
""", encoding="utf-8")

    config, resolved_paths, _, wordfill_cfg = kardenwort_desk.load_config(config_path)
    sess_zid = "20260913000001"
    tsv_file = results_dir / f"{sess_zid}.de.tsv"
    tsv_file.write_text(
        "# comment\n"
        "TokenOrder\tWordSource\tWordDestination\tWordSourceIPA\tWordSourceMorphologyAI\tSentenceSourceIndex\n"
        "0\tLieferpartner\t\t\t\t1\n"
        "1\tKunde\t\t\t\t1\n",
        encoding="utf-8"
    )

    arbiter = SessionArbiter(config=config, resolved_paths=resolved_paths, wordfill_cfg=wordfill_cfg)
    arbiter.sessions[sess_zid] = {
        "session_zid": sess_zid,
        "language": "de",
        "data_rows": [
            ["0", "Lieferpartner", "", "", "", "1"],
            ["1", "Kunde", "", "", "", "1"],
        ],
        "headers": ["TokenOrder", "WordSource", "WordDestination", "WordSourceIPA", "WordSourceMorphologyAI", "SentenceSourceIndex"],
        "role_fields": {"lemma": "WordSource", "word_translation": "WordDestination", "ipa": "WordSourceIPA", "morphology": "WordSourceMorphologyAI", "sentence_index": "SentenceSourceIndex"},
        "row_provenances": {0: "live:google", "0": "live:google", 1: "live:google", "1": "live:google"},
    }

    # Also pre-populate enrichment queue cache to verify cache is bypassed during Re-word
    arbiter.enrichment_queue.set_cached("Lieferpartner", "de", {
        "WordDestination": "кэшированный_партнер",
        "WordSourceIPA": "/ipa/",
        "WordSourceMorphologyAI": "morph",
        "word_provenance": "live:intellifiller"
    })

    def fake_headless(tsv_path, prompt_name, config, resolved_paths, selected_rows=None, reprocess=False, zid=None, trace_id=None):
        lines = tsv_path.read_text(encoding="utf-8").splitlines()
        new_lines = [
            lines[0], lines[1],
            "0\tLieferpartner\tсвежий_ai_партнер\t/ˈliːfɐˌpaʁtnɐ/\tSubstantiv, m\t1",
            "1\tKunde\tсвежий_ai_клиент\t/ˈkʊndə/\tSubstantiv, m\t1",
        ]
        tsv_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return True

    monkeypatch.setattr(kardenwort_controller, "run_headless_intellifiller", fake_headless)
    monkeypatch.setattr(kardenwort_desk, "run_headless_intellifiller", fake_headless)

    calls = []
    monkeypatch.setattr(arbiter, "propagate_enrichment_to_siblings", lambda *a, **k: calls.append("enrich"))
    monkeypatch.setattr(arbiter, "propagate_translations_to_siblings", lambda *a, **k: calls.append("trans"))

    return arbiter, sess_zid, calls


def test_explicit_master_propagates(tmp_path, monkeypatch):
    arbiter, sess_zid, calls = _make_reword_arbiter(tmp_path, monkeypatch)
    arbiter.reword_session(session_zid=sess_zid, selected_rows=[0, 1], language="de", sentence_idx=0)
    assert len(calls) == 1


def test_omitted_sentence_idx_propagates(tmp_path, monkeypatch):
    arbiter, sess_zid, calls = _make_reword_arbiter(tmp_path, monkeypatch)
    arbiter.reword_session(session_zid=sess_zid, selected_rows=[0, 1], language="de")
    assert len(calls) == 1


def test_child_sentence_idx_isolated(tmp_path, monkeypatch):
    arbiter, sess_zid, calls = _make_reword_arbiter(tmp_path, monkeypatch)
    arbiter.reword_session(session_zid=sess_zid, selected_rows=[0, 1], language="de", sentence_idx=2)
    assert calls == []


def test_target_coordinates_do_not_override(tmp_path, monkeypatch):
    arbiter, sess_zid, calls = _make_reword_arbiter(tmp_path, monkeypatch)
    arbiter.reword_session(
        session_zid=sess_zid,
        selected_rows=[0, 1],
        language="de",
        target_coordinates=[{"visual_idx": 0, "sentence_idx": 3}],
    )
    assert len(calls) == 1


@pytest.mark.parametrize("invalid_sent", [-1, "abc", True])
def test_invalid_sentence_idx_rejected(tmp_path, monkeypatch, invalid_sent):
    arbiter, sess_zid, calls = _make_reword_arbiter(tmp_path, monkeypatch)
    with pytest.raises(StructuredError) as ei:
        arbiter.reword_session(session_zid=sess_zid, selected_rows=[0, 1], language="de", sentence_idx=invalid_sent)
    assert ei.value.error_code == "INVALID_PAYLOAD"
    assert calls == []


# --- Phase 3: Retext persistence tests ---

def test_to_one_based_sentence_map_zero_based_shift():
    main, padded = kardenwort_controller.to_one_based_sentence_map(
        {0: "A", 1: "B", "PADDED": {0: "pA", 1: "pB"}, "FULL_TEXT": "A B"}
    )
    assert main == {1: "A", 2: "B"}
    assert padded == {1: "pA", 2: "pB"}


def test_to_one_based_sentence_map_string():
    main, padded = kardenwort_controller.to_one_based_sentence_map("Hallo")
    assert main == {1: "Hallo"}
    assert padded == {}


def test_to_one_based_sentence_map_full_text_only():
    main, padded = kardenwort_controller.to_one_based_sentence_map({"FULL_TEXT": "X"})
    assert main == {1: "X"}
    assert padded == {}


@pytest.mark.parametrize("empty_input", ["", "   ", {}, {"PADDED": {}}])
def test_to_one_based_sentence_map_empty_fails_fast(empty_input):
    with pytest.raises(StructuredError):
        kardenwort_controller.to_one_based_sentence_map(empty_input)


def _make_persist_fixture(tmp_path, headers=None, initial_rows=None, save_sqlite=True):
    from kardenwort_desk import SqliteStorageAdapter
    db_file = tmp_path / "test_persist.db"
    config, resolved_paths, _, _ = kardenwort_desk.load_config()
    resolved_paths["sqlite_db_path"] = str(db_file)
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    resolved_paths["results_dir"] = str(results_dir)

    adapter = SqliteStorageAdapter(config=config, resolved_paths=resolved_paths, db_path=str(db_file))
    arbiter = SessionArbiter(config=config, resolved_paths=resolved_paths)
    arbiter.storage_adapter = adapter

    session_zid = "20261014095500"
    if headers is None:
        headers = [
            "TokenOrder", "Quotation", "WordSource", "SentenceSourceIndex",
            "SentenceSource", "SentenceDestination", "SentenceDestination2", "WordDestination"
        ]
    if initial_rows is None:
        initial_rows = [
            ["0", "Eins", "eins", "1", "Satz eins.", "Alter Satz 1", "", "один"],
            ["1", "Zwei", "zwei", "2", "Satz zwei.", "Alter Satz 2", "", "два"]
        ]

    if save_sqlite:
        adapter.save_session(
            session_zid=session_zid,
            slug="test-isolated-retext",
            source_language="de",
            target_language="ru",
            text_mode="multi",
            source_raw_text="Satz eins.\nSatz zwei.",
            data_rows=initial_rows,
            headers=headers,
        )

    tsv_file = results_dir / f"{session_zid}.de.tsv"
    tsv_file.write_text("\t".join(headers) + "\n" + "\n".join("\t".join(r) for r in initial_rows), encoding="utf-8")

    role_fields = {"sentence_destination": "SentenceDestination"}
    if "SentenceSourceIndex" in headers:
        role_fields["sentence_index"] = "SentenceSourceIndex"

    arbiter.sessions[session_zid] = {
        "session_zid": session_zid,
        "language": "de",
        "target_lang": "ru",
        "text": "Satz eins.\nSatz zwei.",
        "tsv_path": str(tsv_file),
        "comments": [],
        "headers": headers,
        "data_rows": [list(r) for r in initial_rows],
        "role_fields": role_fields
    }
    return arbiter, adapter, session_zid, tsv_file


def test_persist_retext_key_zero_rejected(tmp_path):
    arbiter, adapter, session_zid, tsv_file = _make_persist_fixture(tmp_path)
    with pytest.raises(StructuredError) as ei:
        arbiter._persist_retext_updates(
            session_zid=session_zid,
            sentence_trans={0: "A"},
            active_text_prov="test",
            req_zid="20261014095501",
            tsv_path=tsv_file
        )
    assert ei.value.error_code == "INVALID_PAYLOAD"


def test_persist_retext_missing_sentence_keeps_old_value(tmp_path):
    arbiter, adapter, session_zid, tsv_file = _make_persist_fixture(tmp_path)
    res = arbiter._persist_retext_updates(
        session_zid=session_zid,
        sentence_trans={1: "Neu 1"},
        active_text_prov="test",
        req_zid="20261014095501",
        tsv_path=tsv_file
    )
    col_dst = res["headers"].index("SentenceDestination")
    assert res["data_rows"][0][col_dst] == "Neu 1"
    assert res["data_rows"][1][col_dst] == "Alter Satz 2"


def test_persist_retext_empty_translation_keeps_old_value(tmp_path):
    arbiter, adapter, session_zid, tsv_file = _make_persist_fixture(tmp_path)
    res = arbiter._persist_retext_updates(
        session_zid=session_zid,
        sentence_trans={1: "Neu 1", 2: "  "},
        active_text_prov="test",
        req_zid="20261014095501",
        tsv_path=tsv_file
    )
    col_dst = res["headers"].index("SentenceDestination")
    assert res["data_rows"][0][col_dst] == "Neu 1"
    assert res["data_rows"][1][col_dst] == "Alter Satz 2"


def test_persist_retext_multi_sentence_without_index_col_raises(tmp_path):
    headers_no_sent = [
        "TokenOrder", "Quotation", "WordSource",
        "SentenceSource", "SentenceDestination", "SentenceDestination2", "WordDestination"
    ]
    initial_rows = [
        ["0", "Eins", "eins", "Satz eins.", "Alter Satz 1", "", "один"],
        ["1", "Zwei", "zwei", "Satz zwei.", "Alter Satz 2", "", "два"]
    ]
    arbiter, adapter, session_zid, tsv_file = _make_persist_fixture(
        tmp_path, headers=headers_no_sent, initial_rows=initial_rows, save_sqlite=False
    )
    with pytest.raises(StructuredError) as ei:
        arbiter._persist_retext_updates(
            session_zid=session_zid,
            sentence_trans={1: "A", 2: "B"},
            active_text_prov="test",
            req_zid="20261014095501",
            tsv_path=tsv_file
        )
    assert ei.value.error_code == "INVALID_STATE"


# --- Phase 5: Paired assembly tests ---

def test_pair_sentence_records_drops_untranslated_and_preserves_alignment():
    records = [
        {"sentence_index": 1, "sentence_source": "S1", "sentence_destination": "T1"},
        {"sentence_index": 2, "sentence_source": "S2", "sentence_destination": ""},
        {"sentence_index": 3, "sentence_source": "S3", "sentence_destination": "T3"},
    ]
    srcs, dests = kardenwort_controller.pair_sentence_records(records)
    assert srcs == ["S1", "S3"]
    assert dests == ["T1", "T3"]


def test_pair_sentence_records_sorts_by_sentence_index():
    records = [
        {"sentence_index": 3, "sentence_source": "S3", "sentence_destination": "T3"},
        {"sentence_index": 1, "sentence_source": "S1", "sentence_destination": "T1"},
    ]
    srcs, dests = kardenwort_controller.pair_sentence_records(records)
    assert srcs == ["S1", "S3"]
    assert dests == ["T1", "T3"]


# --- Phase 6: Client findTokenData tests ---

def test_find_token_data_has_no_seq_num():
    js_path = Path("web/js/app.js")
    content = js_path.read_text(encoding="utf-8")
    start = content.index("function findTokenData(span)")
    end = content.index("function findTokenDataByVisualIdx(")
    body = content[start:end]
    assert "seq_num" not in body


def test_find_token_data_sentence_coordinate(page):
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    headers = ["WordSource", "WordDestination", "WordSourceIPA", "WordSourceMorphologyAI"]
    data_rows = [["Haus", "дом", "", ""]]
    html = kardenwort_desk.render_lookup_html(
        text="Das Haus",
        language="de",
        target_lang="ru",
        config=config,
        resolved_paths=resolved_paths,
        zid="000",
        goldendict=goldendict,
        comments=[],
        headers=headers,
        data_rows=data_rows,
        sentence_translation="The house",
        server_enabled=True,
        session_zid="20261014095500"
    )
    page.set_content(html)

    eval_script = """
    () => {
        window.AppState = window.AppState || {};
        window.AppState.sentenceCards = [
            {
                seq_num: 2,
                sentence_idx: 1,
                words: [
                    {word: "Haus", row_id: 7, token_order: 7, sentence_idx: "1"},
                    {word: "der", row_id: 8, token_order: 8, sentence_idx: "1"},
                    {word: "der", row_id: 9, token_order: 9, sentence_idx: "1"}
                ]
            }
        ];

        // Scenario 1: Child card word resolves to its sentence, not its tab number (seq_num: 2)
        const span1 = document.createElement('span');
        span1.setAttribute('data-word-idx', '7');
        const res1 = window.findTokenData(span1);

        // Scenario 2: data-sentence-idx="0" is honored
        const span2 = document.createElement('span');
        span2.setAttribute('data-word-idx', '7');
        span2.setAttribute('data-sentence-idx', '0');
        const res2 = window.findTokenData(span2);

        // Scenario 3: Ambiguous text match returns null
        const span3 = document.createElement('span');
        span3.textContent = 'der';
        span3.setAttribute('data-word-idx', '999');
        span3.setAttribute('data-sentence-idx', '1');
        const res3 = window.findTokenData(span3);

        return {
            res1_sentence_idx: res1 ? res1.sentence_idx : null,
            res2_sentence_idx: res2 ? res2.sentence_idx : null,
            res3: res3
        };
    }
    """
    res = page.evaluate(eval_script)
    assert res["res1_sentence_idx"] == 1
    assert res["res2_sentence_idx"] == 0
    assert res["res3"] is None
