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
