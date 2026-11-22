"""Acceptance tests for scoping supported CLI progressive word writes to the exact sentence.

Spec: specs/fs-free-sqlite-pipeline/spec.md
Requirement: Scope supported CLI progressive writes to the exact sentence
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import kardenwort_desk as desk
from kardenwort_db import KardenwortDB
from tests.controller_test_support import (
    DeterministicTranslationHandler,
    controller_lifetime,
    owned_configuration,
    translation_service,
)


MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "schemas" / "migrations"

BASE_HEADERS = [
    "TokenOrder",
    "Quotation",
    "WordSource",
    "SentenceSourceIndex",
    "SentenceSource",
    "SentenceDestination",
    "WordDestination",
]

BASE_ROLE_FIELDS = {
    "lemma": "WordSource",
    "word_translation": "WordDestination",
    "sentence_index": "SentenceSourceIndex",
    "sentence_source": "SentenceSource",
    "sentence_destination": "SentenceDestination",
}

WORDFILL_HEADERS = [
    "TokenOrder",
    "Quotation",
    "WordSource",
    "WordDestination",
    "WordSourceIPA",
    "MorphCustom",
    "POSCustom",
    "WordSourceGender",
    "SentenceSourceIndex",
    "SentenceSource",
    "SentenceDestination",
]

WORDFILL_ROLE_FIELDS = {
    "lemma": "WordSource",
    "word_translation": "WordDestination",
    "ipa": "WordSourceIPA",
    "morphology": "MorphCustom",
    "pos": "POSCustom",
    "gender": "WordSourceGender",
    "sentence_index": "SentenceSourceIndex",
    "sentence_source": "SentenceSource",
    "sentence_destination": "SentenceDestination",
}


class ItemAwareTranslationHandler(DeterministicTranslationHandler):
    """Owned HTTP translation handler that prefixes both newline- and semicolon-joined segments."""

    def translate(self, provider, text, source, target):
        self.server.provider_calls.append((text, source, target))
        lines = []
        for line in text.splitlines():
            if "; " in line:
                lines.append("; ".join(f"{provider}: {part}" for part in line.split("; ")))
            else:
                lines.append(f"{provider}: {line}")
        return "\n".join(lines)


def _setup_cli_sqlite_env(tmp_path, translator, *, wordfill_enabled=False, update_delivery="desktop_file", custom_roles=False):
    config, resolved_paths, _ = owned_configuration(
        tmp_path,
        f"http://127.0.0.1:{translator.server_port}",
        "sqlite",
    )
    if custom_roles:
        mapping_file = tmp_path / "anki-mapping-custom.ini"
        mapping_file.write_text(
            "[fields]\n"
            "TokenOrder\nQuotation\nWordSource\nWordDestination\n"
            "WordSourceIPA\nMorphCustom\nPOSCustom\nWordSourceGender\n"
            "SentenceSourceIndex\nSentenceSource\nSentenceDestination\n"
            "[roles]\n"
            "lemma=WordSource\n"
            "word_translation=WordDestination\n"
            "ipa=WordSourceIPA\n"
            "morphology=MorphCustom\n"
            "pos=POSCustom\n"
            "gender=WordSourceGender\n"
            "sentence_index=SentenceSourceIndex\n"
            "sentence_source=SentenceSource\n"
            "sentence_destination=SentenceDestination\n",
            encoding="utf-8",
        )
        config.set("settings", "anki_mapping_file", str(mapping_file))
        resolved_paths["anki_mapping_file"] = mapping_file

    config.read_dict({
        "triggers": {
            "run_text_translation": "manual",
            "run_lemma_base_translation": "auto",
            "run_lemma_enrichment": "manual",
        },
        "translation": {
            "translation_order": "top_to_bottom",
            "translation_chunk_size": "15",
            "translation_max_retries": "1",
            "translation_retry_backoff": "0",
        },
        "wordfill": {
            "enabled": "true" if wordfill_enabled else "false",
            "scan_roots": str(tmp_path / "empty-corpus"),
            "scan_depth": "1",
            "scan_scope": "all",
            "scan_sort_order": "chronological",
            "scan_match_language": "true",
            "target_quality": "full" if wordfill_enabled else "any",
            "target_fallback": "true",
        },
        "server": {
            "update_delivery": update_delivery,
        },
    })
    cfg_path = tmp_path / "config.ini"
    with cfg_path.open("w", encoding="utf-8") as stream:
        config.write(stream)

    db = KardenwortDB(db_path=resolved_paths["sqlite_db_path"], migrations_dir=MIGRATIONS_DIR)
    db.run_migrations()
    return config, resolved_paths, db, cfg_path


def _cold_words_by_coord(db_path, zid):
    reopened = KardenwortDB(db_path=db_path, migrations_dir=MIGRATIONS_DIR)
    return {
        (w["sentence_index"], w["token_order"]): dict(w)
        for w in reopened.get_words_by_session(zid)
    }


def _read_update_payloads(tsv_path):
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


def test_cli_progressive_repeated_local_token_numbers_scoped_to_sentence(tmp_path):
    """Scenario: Repeated local token numbers.

    Two sentences each contain local token 0 ('Haus' in sentence 1, 'Baum' in sentence 2).
    Real owned HTTP translation and real SQLite must scope each write to its exact sentence.
    """
    with translation_service(ItemAwareTranslationHandler) as translator:
        config, resolved_paths, db, _ = _setup_cli_sqlite_env(tmp_path, translator)
        zid = "20261119085311"
        unrelated_zid = "20261119085399"

        db.save_session_bundle(
            session={
                "zid": zid,
                "slug": "repeated-local-token",
                "source_language": "de",
                "target_language": "ru",
                "text_mode": "multi",
                "source_raw_text": "Das Haus.\nDer Baum.",
            },
            sentences=[
                {"session_zid": zid, "sentence_index": 1, "sentence_source": "Das Haus.", "sentence_destination": "Дом."},
                {"session_zid": zid, "sentence_index": 2, "sentence_source": "Der Baum.", "sentence_destination": "Дерево."},
            ],
            words=[
                {"session_zid": zid, "sentence_index": 1, "token_order": 0, "quotation": "Haus", "lemma": "Haus", "word_destination": ""},
                {"session_zid": zid, "sentence_index": 2, "token_order": 0, "quotation": "Baum", "lemma": "Baum", "word_destination": ""},
            ],
        )
        db.save_session_bundle(
            session={
                "zid": unrelated_zid,
                "slug": "unrelated",
                "source_language": "de",
                "target_language": "ru",
                "text_mode": "single",
                "source_raw_text": "Der Stein.",
            },
            sentences=[
                {"session_zid": unrelated_zid, "sentence_index": 1, "sentence_source": "Der Stein.", "sentence_destination": "Камень."},
            ],
            words=[
                {"session_zid": unrelated_zid, "sentence_index": 1, "token_order": 0, "quotation": "Stein", "lemma": "Stein",
                 "word_destination": "камень", "word_provenance": "cached:fixture"},
            ],
        )
        unrelated_before = db.get_session_bundle(unrelated_zid)

        tsv_path = resolved_paths["results_dir"] / f"{zid}-repeated-local-token.de.tsv"
        data_rows = [
            ["0", "Haus", "Haus", "1", "Das Haus.", "Дом.", ""],
            ["0", "Baum", "Baum", "2", "Der Baum.", "Дерево.", ""],
        ]
        args = SimpleNamespace(language="de", target_lang="ru", text_mode="multi", zid=zid, trace_id=f"{zid}:test")

        res = desk._progressive_worker_stage_translation_impl(
            tsv_path=tsv_path,
            args=args,
            config=config,
            resolved_paths=resolved_paths,
            data_rows=data_rows,
            headers=list(BASE_HEADERS),
            role_fields=dict(BASE_ROLE_FIELDS),
            zid=zid,
            trace_id=f"{zid}:test",
            row_provenances={},
        )

        assert res.error is None
        by_coord = _cold_words_by_coord(resolved_paths["sqlite_db_path"], zid)
        assert by_coord[(1, 0)]["word_destination"] == "google: Haus"
        assert by_coord[(1, 0)]["word_provenance"] == "live:google"
        assert by_coord[(2, 0)]["word_destination"] == "google: Baum"
        assert by_coord[(2, 0)]["word_provenance"] == "live:google"
        assert db.get_session_bundle(unrelated_zid) == unrelated_before


@pytest.mark.parametrize("second_lemma", ["Baum", "Haus"])
def test_cli_progressive_one_requested_occurrence_preserves_other_sentence(tmp_path, second_lemma):
    """Scenario: One requested occurrence.

    Sentence 1 token 0 already has committed translation/enrichment/provenance.
    Only sentence 2 token 0 is eligible. Sentence 1 MUST remain byte-for-byte unchanged in SQLite.
    """
    with translation_service(ItemAwareTranslationHandler) as translator:
        config, resolved_paths, db, _ = _setup_cli_sqlite_env(tmp_path, translator)
        zid = "20261119085312"

        db.save_session_bundle(
            session={
                "zid": zid,
                "slug": "one-requested",
                "source_language": "de",
                "target_language": "ru",
                "text_mode": "multi",
                "source_raw_text": f"Das Haus.\nDer {second_lemma}.",
            },
            sentences=[
                {"session_zid": zid, "sentence_index": 1, "sentence_source": "Das Haus.", "sentence_destination": "Дом."},
                {"session_zid": zid, "sentence_index": 2, "sentence_source": f"Der {second_lemma}.", "sentence_destination": "Второе."},
            ],
            words=[
                {
                    "session_zid": zid,
                    "sentence_index": 1,
                    "token_order": 0,
                    "quotation": "Haus",
                    "lemma": "Haus",
                    "word_destination": "altes Haus",
                    "word_provenance": "cached:sqlite",
                    "ipa": "haʊ̯s",
                    "morphology": "Subst.Neut",
                    "pos": "NOUN",
                    "gender": "n",
                },
                {
                    "session_zid": zid,
                    "sentence_index": 2,
                    "token_order": 0,
                    "quotation": second_lemma,
                    "lemma": second_lemma,
                    "word_destination": "",
                },
            ],
        )
        sent1_before = _cold_words_by_coord(resolved_paths["sqlite_db_path"], zid)[(1, 0)]

        tsv_path = resolved_paths["results_dir"] / f"{zid}-one-requested.de.tsv"
        data_rows = [
            ["0", "Haus", "Haus", "1", "Das Haus.", "Дом.", "altes Haus"],
            ["0", second_lemma, second_lemma, "2", f"Der {second_lemma}.", "Второе.", ""],
        ]
        args = SimpleNamespace(language="de", target_lang="ru", text_mode="multi", zid=zid, trace_id=f"{zid}:test")

        res = desk._progressive_worker_stage_translation_impl(
            tsv_path=tsv_path,
            args=args,
            config=config,
            resolved_paths=resolved_paths,
            data_rows=data_rows,
            headers=list(BASE_HEADERS),
            role_fields=dict(BASE_ROLE_FIELDS),
            zid=zid,
            trace_id=f"{zid}:test",
            row_provenances={},
        )

        assert res.error is None
        by_coord = _cold_words_by_coord(resolved_paths["sqlite_db_path"], zid)
        assert by_coord[(1, 0)] == sent1_before
        assert by_coord[(2, 0)]["word_destination"] == f"google: {second_lemma}"
        assert by_coord[(2, 0)]["word_provenance"] == "live:google"


@pytest.mark.parametrize("bad_manifest", ["missing_sentence_column", "blank_sentence_index", "duplicate_occurrence"])
def test_cli_progressive_ambiguous_manifest_rejects_before_changes(tmp_path, bad_manifest):
    """Scenario: Ambiguous manifest.

    When the CLI batch lacks a resolvable sentence identity, the batch MUST fail explicitly
    before calling the provider or changing any word in SQLite.
    """
    with translation_service(ItemAwareTranslationHandler) as translator:
        config, resolved_paths, db, _ = _setup_cli_sqlite_env(tmp_path, translator)
        zid = "20261119085313"

        db.save_session_bundle(
            session={
                "zid": zid,
                "slug": "ambiguous-manifest",
                "source_language": "de",
                "target_language": "ru",
                "text_mode": "multi",
                "source_raw_text": "Das Haus.\nDer Baum.",
            },
            sentences=[
                {"session_zid": zid, "sentence_index": 1, "sentence_source": "Das Haus.", "sentence_destination": "Дом."},
                {"session_zid": zid, "sentence_index": 2, "sentence_source": "Der Baum.", "sentence_destination": "Дерево."},
            ],
            words=[
                {"session_zid": zid, "sentence_index": 1, "token_order": 0, "quotation": "Haus", "lemma": "Haus", "word_destination": ""},
                {"session_zid": zid, "sentence_index": 2, "token_order": 0, "quotation": "Baum", "lemma": "Baum", "word_destination": ""},
            ],
        )
        words_before = _cold_words_by_coord(resolved_paths["sqlite_db_path"], zid)

        if bad_manifest == "missing_sentence_column":
            headers = ["TokenOrder", "Quotation", "WordSource", "SentenceSource", "SentenceDestination", "WordDestination"]
            data_rows = [
                ["0", "Haus", "Haus", "Das Haus.", "Дом.", ""],
                ["0", "Baum", "Baum", "Der Baum.", "Дерево.", ""],
            ]
        elif bad_manifest == "blank_sentence_index":
            headers = list(BASE_HEADERS)
            data_rows = [
                ["0", "Haus", "Haus", "1", "Das Haus.", "Дом.", ""],
                ["0", "Baum", "Baum", "", "Der Baum.", "Дерево.", ""],
            ]
        else:
            headers = list(BASE_HEADERS)
            data_rows = [
                ["0", "Haus", "Haus", "1", "Das Haus.", "Дом.", ""],
                ["0", "Baum", "Baum", "1", "Der Baum.", "Дерево.", ""],
            ]

        tsv_path = resolved_paths["results_dir"] / f"{zid}-ambiguous-manifest.de.tsv"
        args = SimpleNamespace(language="de", target_lang="ru", text_mode="multi", zid=zid, trace_id=f"{zid}:test")

        res = desk._progressive_worker_stage_translation_impl(
            tsv_path=tsv_path,
            args=args,
            config=config,
            resolved_paths=resolved_paths,
            data_rows=data_rows,
            headers=headers,
            role_fields=dict(BASE_ROLE_FIELDS),
            zid=zid,
            trace_id=f"{zid}:test",
            row_provenances={},
        )

        assert res.error is not None
        assert translator.provider_calls == []
        assert _cold_words_by_coord(resolved_paths["sqlite_db_path"], zid) == words_before


def test_cli_wordfill_retains_all_accepted_fields_for_exact_occurrence(tmp_path):
    """Task 4.1 / Scenario: CLI wordfill retains all accepted fields for the exact occurrence.

    Owned SQLite corpus supplies translation, provenance, IPA, morphology, POS, and gender.
    Two sentences share local token_order 0:
    - (1, 0) 'Haus' is already committed and MUST remain byte-for-byte unchanged after cold reload.
    - (2, 0) 'Apfel' is eligible for CLI wordfill and MUST commit all accepted fields + provenance
      while preserving non-target fact classification_oxford='B1'.
    - (2, 1) 'Apfel' already has ipa='existing-ipa' and MUST keep existing filled fact under the
      declared reprocess/field policy while filling empty word_destination/morphology/pos/gender.
    """
    with translation_service(ItemAwareTranslationHandler) as translator:
        config, resolved_paths, db, _ = _setup_cli_sqlite_env(
            tmp_path, translator, wordfill_enabled=True, custom_roles=True
        )
        corpus_zid = "20261119085321"
        target_zid = "20261119085322"

        db.save_session_bundle(
            session={
                "zid": corpus_zid,
                "slug": "wf-corpus",
                "source_language": "de",
                "target_language": "ru",
                "text_mode": "single",
                "source_raw_text": "Der Apfel.",
            },
            sentences=[
                {"session_zid": corpus_zid, "sentence_index": 1, "sentence_source": "Der Apfel.", "sentence_destination": "Яблоко."},
            ],
            words=[
                {
                    "session_zid": corpus_zid,
                    "sentence_index": 1,
                    "token_order": 0,
                    "quotation": "Apfel",
                    "lemma": "Apfel",
                    "word_destination": "яблоко",
                    "ipa": "ˈapfəl",
                    "morphology": "Subst.Masc.Nom.Sg",
                    "pos": "NOUN",
                    "gender": "m",
                    "selected": 1,
                }
            ],
        )

        db.save_session_bundle(
            session={
                "zid": target_zid,
                "slug": "wf-target",
                "source_language": "de",
                "target_language": "ru",
                "text_mode": "multi",
                "source_raw_text": "Das Haus.\nDer Apfel und noch ein Apfel.",
            },
            sentences=[
                {"session_zid": target_zid, "sentence_index": 1, "sentence_source": "Das Haus.", "sentence_destination": "Дом."},
                {"session_zid": target_zid, "sentence_index": 2, "sentence_source": "Der Apfel und noch ein Apfel.", "sentence_destination": "Яблоко."},
            ],
            words=[
                {
                    "session_zid": target_zid,
                    "sentence_index": 1,
                    "token_order": 0,
                    "quotation": "Haus",
                    "lemma": "Haus",
                    "word_destination": "altes Haus",
                    "ipa": "haʊ̯s",
                    "morphology": "Subst.Neut",
                    "pos": "NOUN",
                    "gender": "n",
                    "word_provenance": "live:google",
                    "classification_oxford": "A1",
                    "selected": 1,
                },
                {
                    "session_zid": target_zid,
                    "sentence_index": 2,
                    "token_order": 0,
                    "quotation": "Apfel",
                    "lemma": "Apfel",
                    "word_destination": "",
                    "ipa": None,
                    "morphology": None,
                    "pos": None,
                    "gender": None,
                    "classification_oxford": "B1",
                    "selected": 0,
                },
                {
                    "session_zid": target_zid,
                    "sentence_index": 2,
                    "token_order": 1,
                    "quotation": "Apfel",
                    "lemma": "Apfel",
                    "word_destination": "",
                    "ipa": "existing-ipa",
                    "morphology": None,
                    "pos": None,
                    "gender": None,
                    "classification_oxford": "B2",
                    "selected": 0,
                },
            ],
        )
        haus_before = _cold_words_by_coord(resolved_paths["sqlite_db_path"], target_zid)[(1, 0)]

        tsv_path = resolved_paths["results_dir"] / f"{target_zid}-wf-target.de.tsv"
        data_rows = [
            ["0", "Haus", "Haus", "altes Haus", "haʊ̯s", "Subst.Neut", "NOUN", "n", "1", "Das Haus.", "Дом."],
            ["0", "Apfel", "Apfel", "", "", "", "", "", "2", "Der Apfel und noch ein Apfel.", "Яблоко."],
            ["1", "Apfel", "Apfel", "", "existing-ipa", "", "", "", "2", "Der Apfel und noch ein Apfel.", "Яблоко."],
        ]
        args = SimpleNamespace(language="de", target_lang="ru", text_mode="multi", zid=target_zid, trace_id=f"{target_zid}:test")

        res = desk._progressive_worker_stage_translation_impl(
            tsv_path=tsv_path,
            args=args,
            config=config,
            resolved_paths=resolved_paths,
            data_rows=data_rows,
            headers=list(WORDFILL_HEADERS),
            role_fields=dict(WORDFILL_ROLE_FIELDS),
            zid=target_zid,
            trace_id=f"{target_zid}:test",
            row_provenances={},
        )

        by_coord = _cold_words_by_coord(resolved_paths["sqlite_db_path"], target_zid)

        # Exact target occurrence (2, 0) commits every accepted wordfill field + provenance
        apfel_0 = by_coord[(2, 0)]
        assert (
            apfel_0["word_destination"],
            apfel_0["word_provenance"],
            apfel_0["ipa"],
            apfel_0["morphology"],
            apfel_0["pos"],
            apfel_0["gender"],
            apfel_0["classification_oxford"],
        ) == ("яблоко", "corpus:wordfill", "ˈapfəl", "Subst.Masc.Nom.Sg", "NOUN", "m", "B1")

        # Adjacent occurrence (1, 0) remains byte-for-byte unchanged
        assert by_coord[(1, 0)] == haus_before

        # Existing filled fact retains declared reprocess policy on (2, 1)
        apfel_1 = by_coord[(2, 1)]
        assert (
            apfel_1["word_destination"],
            apfel_1["word_provenance"],
            apfel_1["ipa"],
            apfel_1["morphology"],
            apfel_1["pos"],
            apfel_1["gender"],
            apfel_1["classification_oxford"],
        ) == ("яблоко", "corpus:wordfill", "existing-ipa", "Subst.Masc.Nom.Sg", "NOUN", "m", "B2")

        assert res.error is None
        assert translator.provider_calls == []


@pytest.mark.parametrize("bad_manifest", ["missing_sentence_column", "blank_sentence_index", "duplicate_occurrence"])
def test_cli_wordfill_invalid_identity_rejects_before_commit_or_publish(tmp_path, bad_manifest):
    """Scenario: Invalid wordfill identity cannot partially publish fields.

    Validation MUST reject before any translation, enrichment, or provenance field is
    committed to SQLite or published to update payloads.
    """
    with translation_service(ItemAwareTranslationHandler) as translator:
        config, resolved_paths, db, _ = _setup_cli_sqlite_env(
            tmp_path, translator, wordfill_enabled=True, custom_roles=True
        )
        corpus_zid = "20261119085323"
        target_zid = "20261119085324"

        db.save_session_bundle(
            session={
                "zid": corpus_zid,
                "slug": "wf-corpus-invalid",
                "source_language": "de",
                "target_language": "ru",
                "text_mode": "single",
                "source_raw_text": "Der Apfel.",
            },
            sentences=[
                {"session_zid": corpus_zid, "sentence_index": 1, "sentence_source": "Der Apfel.", "sentence_destination": "Яблоко."},
            ],
            words=[
                {
                    "session_zid": corpus_zid,
                    "sentence_index": 1,
                    "token_order": 0,
                    "quotation": "Apfel",
                    "lemma": "Apfel",
                    "word_destination": "яблоко",
                    "ipa": "ˈapfəl",
                    "morphology": "Subst.Masc.Nom.Sg",
                    "pos": "NOUN",
                    "gender": "m",
                    "selected": 1,
                }
            ],
        )
        db.save_session_bundle(
            session={
                "zid": target_zid,
                "slug": "wf-target-invalid",
                "source_language": "de",
                "target_language": "ru",
                "text_mode": "multi",
                "source_raw_text": "Das Haus.\nDer Apfel.",
            },
            sentences=[
                {"session_zid": target_zid, "sentence_index": 1, "sentence_source": "Das Haus.", "sentence_destination": "Дом."},
                {"session_zid": target_zid, "sentence_index": 2, "sentence_source": "Der Apfel.", "sentence_destination": "Яблоко."},
            ],
            words=[
                {
                    "session_zid": target_zid,
                    "sentence_index": 1,
                    "token_order": 0,
                    "quotation": "Haus",
                    "lemma": "Haus",
                    "word_destination": "altes Haus",
                    "ipa": "haʊ̯s",
                    "word_provenance": "live:google",
                },
                {
                    "session_zid": target_zid,
                    "sentence_index": 2,
                    "token_order": 0,
                    "quotation": "Apfel",
                    "lemma": "Apfel",
                    "word_destination": "",
                },
            ],
        )
        words_before = _cold_words_by_coord(resolved_paths["sqlite_db_path"], target_zid)

        if bad_manifest == "missing_sentence_column":
            headers = [h for h in WORDFILL_HEADERS if h != "SentenceSourceIndex"]
            data_rows = [
                ["0", "Haus", "Haus", "altes Haus", "haʊ̯s", "", "", "", "Das Haus.", "Дом."],
                ["0", "Apfel", "Apfel", "", "", "", "", "", "Der Apfel.", "Яблоко."],
            ]
        elif bad_manifest == "blank_sentence_index":
            headers = list(WORDFILL_HEADERS)
            data_rows = [
                ["0", "Haus", "Haus", "altes Haus", "haʊ̯s", "", "", "", "1", "Das Haus.", "Дом."],
                ["0", "Apfel", "Apfel", "", "", "", "", "", "", "Der Apfel.", "Яблоко."],
            ]
        else:
            headers = list(WORDFILL_HEADERS)
            data_rows = [
                ["0", "Haus", "Haus", "altes Haus", "haʊ̯s", "", "", "", "1", "Das Haus.", "Дом."],
                ["0", "Apfel", "Apfel", "", "", "", "", "", "1", "Der Apfel.", "Яблоко."],
            ]

        tsv_path = resolved_paths["results_dir"] / f"{target_zid}-wf-target-invalid.de.tsv"
        args = SimpleNamespace(language="de", target_lang="ru", text_mode="multi", zid=target_zid, trace_id=f"{target_zid}:test")

        res = desk._progressive_worker_stage_translation_impl(
            tsv_path=tsv_path,
            args=args,
            config=config,
            resolved_paths=resolved_paths,
            data_rows=data_rows,
            headers=headers,
            role_fields=dict(WORDFILL_ROLE_FIELDS),
            zid=target_zid,
            trace_id=f"{target_zid}:test",
            row_provenances={},
        )

        assert res.error is not None
        assert translator.provider_calls == []
        assert _cold_words_by_coord(resolved_paths["sqlite_db_path"], target_zid) == words_before

        payloads = _read_update_payloads(tsv_path)
        for payload in payloads:
            serialized = json.dumps(payload, ensure_ascii=False)
            assert "яблоко" not in serialized
            assert "ˈapfəl" not in serialized
            assert "Subst.Masc.Nom.Sg" not in serialized
            assert "corpus:wordfill" not in serialized
            assert payload.get("status") == "failed"


def test_adjacent_control_single_sentence_cli_distinct_token_orders(tmp_path):
    """Valid adjacent control: single-sentence CLI progressive write with distinct token orders."""
    with translation_service(ItemAwareTranslationHandler) as translator:
        config, resolved_paths, db, _ = _setup_cli_sqlite_env(tmp_path, translator)
        zid = "20261119085331"
        text = "Das Haus und der Baum."

        db.save_session_bundle(
            session={
                "zid": zid,
                "slug": "single-sentence-control",
                "source_language": "de",
                "target_language": "ru",
                "text_mode": "single",
                "source_raw_text": text,
            },
            sentences=[
                {"session_zid": zid, "sentence_index": 1, "sentence_source": text, "sentence_destination": "Дом и дерево."},
            ],
            words=[
                {"session_zid": zid, "sentence_index": 1, "token_order": 0, "quotation": "Haus", "lemma": "Haus", "word_destination": ""},
                {"session_zid": zid, "sentence_index": 1, "token_order": 1, "quotation": "Baum", "lemma": "Baum", "word_destination": ""},
            ],
        )

        tsv_path = resolved_paths["results_dir"] / f"{zid}-single-sentence-control.de.tsv"
        data_rows = [
            ["0", "Haus", "Haus", "1", text, "Дом и дерево.", ""],
            ["1", "Baum", "Baum", "1", text, "Дом и дерево.", ""],
        ]
        args = SimpleNamespace(language="de", target_lang="ru", text_mode="single", zid=zid, trace_id=f"{zid}:test")

        res = desk._progressive_worker_stage_translation_impl(
            tsv_path=tsv_path,
            args=args,
            config=config,
            resolved_paths=resolved_paths,
            data_rows=data_rows,
            headers=list(BASE_HEADERS),
            role_fields=dict(BASE_ROLE_FIELDS),
            zid=zid,
            trace_id=f"{zid}:test",
            row_provenances={},
        )

        assert res.error is None
        by_coord = _cold_words_by_coord(resolved_paths["sqlite_db_path"], zid)
        assert by_coord[(1, 0)]["word_destination"] == "google: Haus"
        assert by_coord[(1, 0)]["word_provenance"] == "live:google"
        assert by_coord[(1, 1)]["word_destination"] == "google: Baum"
        assert by_coord[(1, 1)]["word_provenance"] == "live:google"


def test_adjacent_control_canonical_controller_repeated_local_token_zero(tmp_path):
    """Valid adjacent control: canonical controller progressive path scopes repeated token 0 by sentence."""
    with translation_service(ItemAwareTranslationHandler) as translator:
        with controller_lifetime(tmp_path, translator, backend="sqlite") as (_, server):
            server.config.read_dict({
                "triggers": {
                    "run_text_translation": "manual",
                    "run_lemma_base_translation": "auto",
                    "run_lemma_enrichment": "manual",
                },
                "translation": {
                    "translation_order": "top_to_bottom",
                    "translation_max_retries": "1",
                    "translation_retry_backoff": "0",
                },
            })
            zid = "20261119085332"
            rows = [
                ["0", "Haus", "Haus", "1", "Das Haus.", "Дом.", ""],
                ["0", "Baum", "Baum", "2", "Der Baum.", "Дерево.", ""],
            ]
            adapter = desk.get_storage_adapter(server.config, server.resolved_paths)
            adapter.save_session(
                session_zid=zid,
                slug="controller-repeated-zero",
                source_language="de",
                target_language="ru",
                text_mode="multi",
                source_raw_text="Das Haus.\nDer Baum.",
                headers=BASE_HEADERS,
                data_rows=rows,
            )
            server.arbiter.sessions[zid] = {
                "text": "Das Haus.\nDer Baum.",
                "comments": [],
                "headers": list(BASE_HEADERS),
                "data_rows": [list(r) for r in rows],
                "language": "de",
                "target_lang": "ru",
                "text_mode": "multi",
                "tsv_path": zid,
            }
            subscriber = server.arbiter.register_subscriber(zid)
            try:
                enqueued = server.arbiter.enrichment_queue.enqueue_progressive_task(
                    zid, server.arbiter, language="de", target_lang="ru", text_mode="multi", skip_intellifiller=True
                )
                assert enqueued["status"] == "queued"
                with server.arbiter.enrichment_queue._lock:
                    future = server.arbiter.enrichment_queue._active_progressive_sessions.get(zid)
                if future is not None:
                    future.result(timeout=15)
            finally:
                server.arbiter.unregister_subscriber(zid, subscriber)

            by_coord = _cold_words_by_coord(server.resolved_paths["sqlite_db_path"], zid)
            assert by_coord[(1, 0)]["word_destination"] == "google: Haus"
            assert by_coord[(1, 0)]["word_provenance"] == "live:google"
            assert by_coord[(2, 0)]["word_destination"] == "google: Baum"
            assert by_coord[(2, 0)]["word_provenance"] == "live:google"


def test_adjacent_control_controller_unavailable_does_not_fallback_to_file_delivery(tmp_path):
    """Valid adjacent control (Decision #2): controller update_delivery does not restore file fallback."""
    from kardenwort_delivery import delivery_sinks

    with translation_service(ItemAwareTranslationHandler) as translator:
        config, resolved_paths, db, _ = _setup_cli_sqlite_env(
            tmp_path, translator, update_delivery="controller"
        )
        assert delivery_sinks(config, diskless=False) == ("controller",)
        zid = "20261119085333"
        tsv_path = resolved_paths["results_dir"] / f"{zid}-no-fallback.de.tsv"
        updates_dir = tsv_path.parent / f"{tsv_path.stem}.updates"
        assert not updates_dir.exists()
