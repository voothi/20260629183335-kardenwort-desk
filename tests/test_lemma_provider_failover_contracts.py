"""Owned-HTTP-service contracts for single-owner lemma provider failover and pins."""
import configparser
from pathlib import Path
from unittest.mock import patch

import pytest

import kardenwort_desk as desk
from kardenwort_controller import SessionArbiter
from tests.controller_test_support import translation_service

ROOT = Path(__file__).resolve().parent.parent
TRANSLATOR = ROOT.parent / "20241122093311-deep-translator"
FORK = TRANSLATOR / "20260209094544-deep-translator"
import sys
for _p in (str(FORK), str(TRANSLATOR)):
    if _p not in sys.path:
        sys.path.append(_p)

import translate_server
from translate_server import TranslationRequestHandler


def _err(status, code, message):
    def _raise(*_args):
        raise translate_server.TranslationServerException(status, code, message)
    return _raise


@pytest.fixture
def lemma_http_world(tmp_path, monkeypatch):
    """Real TranslationHTTPServer + real SQLite storage; only unmanaged engines are scripted."""
    calls = []
    behavior = {}

    def _engine(prov):
        def _translate(self, text, source, target, *args):
            calls.append((prov, text))
            fn = behavior.get(prov)
            if callable(fn):
                return fn(text, source, target)
            return f"{prov}:{text}"
        return _translate

    for prov in ("google", "deepl", "argos"):
        monkeypatch.setattr(TranslationRequestHandler, f"_translate_{prov}", _engine(prov))

    results = tmp_path / "results"
    results.mkdir()
    db_path = tmp_path / "lemma_routing.db"
    settings = tmp_path / "deepl-settings.ini"
    secrets = tmp_path / "secrets.ini"
    settings.write_text("[Security]\nSecretsPath=secrets.ini\n", encoding="utf-8")
    secrets.write_text("[DeepL]\nKey=dGVzdC1rZXk=\n", encoding="utf-8")

    with translation_service(TranslationRequestHandler, auto_failover=False) as server:
        server_url = f"http://127.0.0.1:{server.server_port}"

        def configure(*, lemma_chain="google, deepl, argos", text_chain="google, deepl, argos",
                      strategy="chain", check_ips="8.8.8.8", lemma_reprocess="deepl", batch_size=15):
            cfg = configparser.ConfigParser()
            cfg.read_dict({
                "services": {"translation_server_url": server_url},
                "pipeline": {
                    "text_provider_chain": text_chain,
                    "lemma_provider_chain": lemma_chain,
                    "failover_strategy": strategy,
                    "fast_connectivity_check_ips": check_ips,
                    "lemma_reprocess_provider": lemma_reprocess,
                    "provider_call_timeout": "5.0",
                },
                "translation": {"lemma_batch_size": str(batch_size)},
                "environment": {"deepl_settings_file": str(settings)},
                "settings": {
                    "default_language": "en",
                    "default_target_language": "ru",
                    "anki_mapping_file": str(ROOT / "anki-mapping.ini"),
                },
                "storage": {"backend": "sqlite", "sqlite_db_path": str(db_path)},
                "project_structure": {"generated_results_dir": str(results)},
                "timeouts": {"translation_timeout": "5"},
            })
            paths = {
                "base_dir": tmp_path,
                "results_dir": results,
                "generated_results_dir": results,
                "sqlite_db_path": db_path,
                "storage_backend": "sqlite",
                "migrations_dir": ROOT / "schemas" / "migrations",
                "anki_mapping_file": ROOT / "anki-mapping.ini",
            }
            adapter = desk.SqliteStorageAdapter(cfg, paths, db_path=db_path)
            desk.clear_provider_cooldowns(config=cfg, resolved_paths=paths)
            return cfg, paths, adapter

        yield configure, behavior, calls


def _seed_session(adapter, cfg, paths, zid, lemmas, initial_trans=""):
    headers = [
        "TokenOrder", "Quotation", "WordSource", "SentenceSourceIndex",
        "SentenceSource", "SentenceDestination", "WordDestination",
    ]
    source_text = " ".join(lemmas)
    rows = [
        [str(i), lemma, lemma, "1", source_text, "Sentence", initial_trans]
        for i, lemma in enumerate(lemmas)
    ]
    adapter.save_session(
        session_zid=zid, slug="routing", source_language="en", target_language="de",
        text_mode="single", source_raw_text=source_text, headers=headers, data_rows=rows,
    )
    arbiter = SessionArbiter(cfg, paths, wordfill_cfg={"enabled": False})
    arbiter.storage_adapter = adapter
    arbiter.sessions[zid] = {
        "language": "en",
        "headers": headers,
        "data_rows": [list(r) for r in rows],
        "row_provenances": {},
    }
    role_fields = {
        "lemma": "WordSource",
        "word_translation": "WordDestination",
        "sentence_index": "SentenceSourceIndex",
    }
    occ_keys = [(1, i) for i in range(len(lemmas))]
    return arbiter, headers, rows, role_fields, occ_keys


def test_automatic_chain_single_owner_progression_without_nested_traversal_or_revisit(lemma_http_world):
    """Automatic chain google->deepl->argos traverses each provider once and never revisits Google."""
    configure, behavior, calls = lemma_http_world
    cfg, paths, adapter = configure(lemma_chain="google, deepl, argos", strategy="chain", batch_size=1)
    arbiter, headers, rows, role_fields, occ_keys = _seed_session(adapter, cfg, paths, "20261122200001", ["House", "Tree"])

    behavior["google"] = _err(500, "ERR_TRANSLATION_FAILED", "google 500")

    def _deepl(text, _src, _tgt):
        if "Tree" in text:
            raise translate_server.TranslationServerException(500, "ERR_TRANSLATION_FAILED", "deepl 500 on chunk 2")
        return f"deepl:{text}"

    behavior["deepl"] = _deepl

    with patch("kardenwort_desk.is_network_online_multi", return_value=True):
        err, superseded = arbiter.enrichment_queue._run_lemma_stage(
            arbiter, adapter, "20261122200001", "20261122200001", True,
            [list(r) for r in rows], headers, role_fields, occ_keys,
            2, 6, 0, "en", "de", "20261122200001", [], "trace-1", None, {}, "live:google",
        )

    assert err is None
    assert superseded is False
    assert calls == [("google", "House"), ("deepl", "House"), ("deepl", "Tree"), ("argos", "Tree")]
    words = [(w["lemma"], w["word_destination"], w["word_provenance"])
             for w in adapter.db.get_words_by_session("20261122200001")]
    assert words == [("House", "deepl:House", "live:deepl"), ("Tree", "argos:Tree", "live:argos")]


def test_automatic_chain_all_providers_fail_invokes_each_candidate_once(lemma_http_world):
    """Exhausted chain must not run nested inner+outer traversals."""
    configure, behavior, calls = lemma_http_world
    cfg, paths, _ = configure(lemma_chain="google, deepl, argos", strategy="chain")
    behavior["google"] = _err(500, "ERR_TRANSLATION_FAILED", "google 500")
    behavior["deepl"] = _err(500, "ERR_TRANSLATION_FAILED", "deepl 500")
    behavior["argos"] = _err(500, "ERR_TRANSLATION_FAILED", "argos 500")

    events = []
    with patch("kardenwort_desk.is_network_online_multi", return_value=True):
        res = desk.translate_lemmas_fast_path(
            ["House"], "en", "de", cfg, paths, provider="google",
            on_failover=lambda task, fr, to, zid=None: events.append((task, fr, to)),
        )

    assert dict(res) == {"House": ""}
    assert calls == [("google", "House"), ("deepl", "House"), ("argos", "House")]
    assert events == [("lemma", "google", "deepl"), ("lemma", "deepl", "argos")]


def test_confirmed_offline_permits_offline_fallback_and_chain_short_circuit(lemma_http_world):
    """Confirmed failed connectivity probes authorize Argos under offline_fallback and chain."""
    configure, behavior, calls = lemma_http_world
    cfg, paths, adapter = configure(lemma_chain="google", strategy="offline_fallback", check_ips="8.8.8.8")
    arbiter, headers, rows, role_fields, occ_keys = _seed_session(adapter, cfg, paths, "20261122200002", ["House"])

    with patch("kardenwort_desk.is_network_online_multi", return_value=False):
        err, superseded = arbiter.enrichment_queue._run_lemma_stage(
            arbiter, adapter, "20261122200002", "20261122200002", True,
            [list(r) for r in rows], headers, role_fields, occ_keys,
            2, 6, 0, "en", "de", "20261122200002", [], "trace-2", None, {}, "live:argos",
        )

    assert err is None and not superseded
    assert calls == [("argos", "House")]
    words = adapter.db.get_words_by_session("20261122200002")
    assert [(w["word_destination"], w["word_provenance"]) for w in words] == [("argos:House", "live:argos")]

    # Chain strategy with confirmed offline skips intermediate DeepL to configured Argos
    calls.clear()
    cfg2, paths2, _ = configure(lemma_chain="google, deepl, argos", strategy="chain", check_ips="8.8.8.8")
    behavior["google"] = _err(503, "ERR_NETWORK_UNREACHABLE", "google unreachable")
    with patch("kardenwort_desk.is_network_online_multi", return_value=False):
        res = desk.translate_lemmas_fast_path(["House"], "en", "de", cfg2, paths2, provider="google")
    assert dict(res) == {"House": "argos:House"}
    assert getattr(res, "provenance", None) == "live:argos"
    assert calls == [("google", "House"), ("argos", "House")]


@pytest.mark.parametrize("status,code", [
    (500, "ERR_TRANSLATION_FAILED"),
    (429, "ERR_GOOGLE_RATE_LIMIT"),
    (503, "ERR_NETWORK_UNREACHABLE"),
])
@pytest.mark.parametrize("check_ips,probe_online", [
    ("8.8.8.8", True),
    ("", True),
], ids=["online-probe", "unconfirmed-no-probe"])
def test_offline_fallback_online_or_unconfirmed_error_does_not_invoke_argos(
    lemma_http_world, status, code, check_ips, probe_online,
):
    """Online provider errors with online or unconfirmed probes must never jump to Argos."""
    configure, behavior, calls = lemma_http_world
    cfg, paths, adapter = configure(lemma_chain="google", strategy="offline_fallback", check_ips=check_ips)
    arbiter, headers, rows, role_fields, occ_keys = _seed_session(adapter, cfg, paths, "20261122200003", ["House"])
    behavior["google"] = _err(status, code, f"google {status}")

    with patch("kardenwort_desk.is_network_online_multi", return_value=probe_online):
        err, superseded = arbiter.enrichment_queue._run_lemma_stage(
            arbiter, adapter, "20261122200003", "20261122200003", True,
            [list(r) for r in rows], headers, role_fields, occ_keys,
            2, 6, 0, "en", "de", "20261122200003", [], "trace-3", None, {}, "live:google",
        )
        # Repeat while still online/cooled-down: must still not jump to Argos
        second = desk.translate_lemmas_fast_path(["House"], "en", "de", cfg, paths, provider="google")

    assert err is not None and not superseded
    assert dict(second) == {"House": ""}
    assert all(prov == "google" for prov, _ in calls)
    assert len(calls) == 1
    words = adapter.db.get_words_by_session("20261122200003")
    assert all(not w["word_destination"] for w in words)


def test_strict_automatic_and_pinned_requests_stay_single_provider(lemma_http_world):
    """Strict head failure and explicit strict single-provider failure must not invoke secondary providers."""
    configure, behavior, calls = lemma_http_world
    cfg, paths, adapter = configure(lemma_chain="google, deepl, argos", strategy="strict", check_ips="8.8.8.8")
    arbiter, headers, rows, role_fields, occ_keys = _seed_session(adapter, cfg, paths, "20261122200004", ["House"])
    behavior["google"] = _err(503, "ERR_NETWORK_UNREACHABLE", "google unreachable")

    with patch("kardenwort_desk.is_network_online_multi", return_value=False):
        err, superseded = arbiter.enrichment_queue._run_lemma_stage(
            arbiter, adapter, "20261122200004", "20261122200004", True,
            [list(r) for r in rows], headers, role_fields, occ_keys,
            2, 6, 0, "en", "de", "20261122200004", [], "trace-4", None, {}, "live:google",
        )

    assert err is not None and not superseded
    assert calls == [("google", "House")]
    words = adapter.db.get_words_by_session("20261122200004")
    assert all(not w["word_destination"] for w in words)

    # Explicit strict single-provider non-head pin
    calls.clear()
    behavior["deepl"] = _err(503, "ERR_NETWORK_UNREACHABLE", "deepl unreachable")
    with patch("kardenwort_desk.is_network_online_multi", return_value=False):
        res_pin = desk.translate_lemmas_fast_path(["House"], "en", "de", cfg, paths, provider="deepl")
    assert dict(res_pin) == {"House": ""}
    assert getattr(res_pin, "provenance", None) == "live:deepl"
    assert calls == [("deepl", "House")]


def test_configured_boundary_and_task_scoped_state_isolation(lemma_http_world):
    """Lemma chain without Argos never invokes Argos and stays isolated from text cooldowns."""
    configure, behavior, calls = lemma_http_world
    cfg, paths, _ = configure(
        lemma_chain="google, deepl",
        text_chain="google, deepl, argos",
        strategy="chain",
        check_ips="8.8.8.8",
    )
    # Cool down Google for text task only; lemma routing must still try Google
    desk.record_provider_cooldown("google", duration=60.0, config=cfg, resolved_paths=paths, task_type="text")

    with patch("kardenwort_desk.is_network_online_multi", return_value=True):
        ok = desk.translate_lemmas_fast_path(["House"], "en", "de", cfg, paths, provider="google")
    assert dict(ok) == {"House": "google:House"}
    assert getattr(ok, "provenance", None) == "live:google"
    assert calls == [("google", "House")]

    # Now exhaust both configured lemma providers: Argos must not be called and text:deepl stays unaffected
    calls.clear()
    behavior["google"] = _err(503, "ERR_NETWORK_UNREACHABLE", "google down")
    behavior["deepl"] = _err(429, "ERR_DEEPL_QUOTA", "deepl quota")
    events = []
    with patch("kardenwort_desk.is_network_online_multi", return_value=True):
        exhausted = desk.translate_lemmas_fast_path(
            ["House"], "en", "de", cfg, paths, provider="google",
            on_failover=lambda task, fr, to, zid=None: events.append((task, fr, to)),
        )

    assert dict(exhausted) == {"House": ""}
    assert calls == [("google", "House"), ("deepl", "House")]
    assert events == [("lemma", "google", "deepl")]
    assert desk.is_provider_cooled_down("google", config=cfg, resolved_paths=paths, task_type="text")
    assert desk.is_provider_cooled_down("deepl", config=cfg, resolved_paths=paths, task_type="lemma")
    assert not desk.is_provider_cooled_down("deepl", config=cfg, resolved_paths=paths, task_type="text")


def test_manual_reword_preserves_ambient_caller_strategy(lemma_http_world):
    """Manual Re-word follows ambient chain under chain and stays single-provider under strict."""
    configure, behavior, calls = lemma_http_world
    cfg, paths, adapter = configure(
        lemma_chain="google, deepl, argos",
        strategy="chain",
        lemma_reprocess="deepl",
    )
    arbiter, _, _, _, _ = _seed_session(adapter, cfg, paths, "20261122200005", ["House"], initial_trans="old")
    behavior["deepl"] = _err(503, "ERR_NETWORK_UNREACHABLE", "deepl unavailable")

    with patch("kardenwort_desk.is_network_online_multi", return_value=True):
        res = arbiter.reword_session(session_zid="20261122200005", selected_rows=[0], language="en")

    assert res["status"] == "success"
    assert calls == [("deepl", "House"), ("google", "House")]
    words = adapter.db.get_words_by_session("20261122200005")
    assert [(w["word_destination"], w["word_provenance"]) for w in words] == [("google:House", "live:google")]

    # Under strict strategy, manual Re-word remains single-provider
    calls.clear()
    cfg_s, paths_s, adapter_s = configure(
        lemma_chain="google, deepl, argos",
        strategy="strict",
        lemma_reprocess="deepl",
    )
    arbiter_s, _, _, _, _ = _seed_session(adapter_s, cfg_s, paths_s, "20261122200006", ["House"], initial_trans="old")
    with patch("kardenwort_desk.is_network_online_multi", return_value=True):
        with pytest.raises(desk.StructuredError, match="Re-word failed"):
            arbiter_s.reword_session(session_zid="20261122200006", selected_rows=[0], language="en")
    assert calls == [("deepl", "House")]
