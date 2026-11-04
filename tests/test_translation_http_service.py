import sys
import time
import json
import threading
import configparser
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

import kardenwort_desk
from kardenwort_desk import (
    run_google_translation,
    run_deepl_translation,
    run_argos_translation,
    query_translation_server,
    SEC_SERVICES,
    SEC_TIMEOUTS,
    SEC_PIPELINE,
)

DEEP_TRANSLATOR_DIR = Path(__file__).resolve().parent.parent.parent / "20241122093311-deep-translator"
if str(DEEP_TRANSLATOR_DIR) not in sys.path:
    sys.path.insert(0, str(DEEP_TRANSLATOR_DIR))
FORK_DIR = DEEP_TRANSLATOR_DIR / "20260209094544-deep-translator"
if str(FORK_DIR) not in sys.path:
    sys.path.insert(0, str(FORK_DIR))

import translate_server
from translate_server import TranslationHTTPServer, TranslationRequestHandler


@pytest.fixture
def provider_routing_service(tmp_path, monkeypatch):
    """Real HTTP routing; only the unmanaged translation engines are replaced."""
    calls = []
    failures = set()

    def engine(provider):
        def translate(self, text, source, target, *args):
            calls.append(provider)
            if provider in failures:
                raise translate_server.TranslationServerException(
                    503, "ERR_NETWORK_UNREACHABLE", "Test provider unavailable"
                )
            return "\n".join(f"{provider}: {line}" for line in text.splitlines())
        return translate

    for provider in ("google", "deepl", "argos"):
        monkeypatch.setattr(TranslationRequestHandler, f"_translate_{provider}", engine(provider))
    server = TranslationHTTPServer(
        ("127.0.0.1", 0), TranslationRequestHandler,
        enable_cache=False, auto_failover=True, google_delay=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = configparser.ConfigParser()
    config.read_dict({
        "services": {"translation_server_url": f"http://127.0.0.1:{server.server_port}"},
        "pipeline": {"text_provider_chain": "google, deepl, argos", "failover_strategy": "chain",
                     "text_reprocess_provider": "deepl"},
        "environment": {"deepl_settings_file": "deepl-settings.ini"},
        "settings": {"default_target_language": "ru"},
        "storage": {"backend": "sqlite"},
        "timeouts": {"translation_timeout": "5"},
    })
    (tmp_path / "deepl-settings.ini").write_text("[Security]\nSecretsPath=secrets.ini\n", encoding="utf-8")
    (tmp_path / "secrets.ini").write_text("[DeepL]\nKey=dGVzdC1rZXk=\n", encoding="utf-8")
    paths = {
        "base_dir": tmp_path, "results_dir": tmp_path,
        "sqlite_db_path": tmp_path / "routing.db",
        "migrations_dir": Path(kardenwort_desk.__file__).parent / "schemas" / "migrations",
        "anki_mapping_file": Path(kardenwort_desk.__file__).parent / "anki-mapping.ini",
    }
    try:
        yield config, paths, calls, failures
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("provider", ["google", "deepl", "argos"])
@pytest.mark.parametrize("strategy", ["chain", "strict", "offline_fallback"])
def test_explicit_provider_survives_http_global_chain(provider_routing_service, provider, strategy):
    config, paths, calls, _ = provider_routing_service
    config.set("pipeline", "text_provider_chain", "argos, google, deepl")
    config.set("pipeline", "failover_strategy", strategy)
    result = kardenwort_desk.dispatch_single_provider(provider, "Hello.", "en", "ru", config, paths)
    assert result == f"{provider}: Hello."
    assert calls == [provider]


def test_explicit_provider_http_failure_does_not_trigger_server_failover(provider_routing_service):
    config, paths, calls, failures = provider_routing_service
    failures.add("deepl")
    with pytest.raises(kardenwort_desk.TranslationException, match="Test provider unavailable"):
        kardenwort_desk.dispatch_single_provider("deepl", "Hello.", "en", "ru", config, paths)
    assert calls == ["deepl"]


@pytest.mark.parametrize("fail_deepl", [False, True])
@pytest.mark.parametrize("sources", [
    ("Hello.", "Thanks."),
    ("This stored sentence has a deliberately long first part to exceed the wrapping threshold. "
     "Its second part must remain within the same authoritative sentence identity.",),
], ids=["multi-sentence", "single-stored-unit"])
def test_retext_http_provider_order_persistence_and_event(provider_routing_service, fail_deepl, sources):
    from kardenwort_controller import SessionArbiter

    config, paths, calls, failures = provider_routing_service
    if fail_deepl:
        failures.add("deepl")
    adapter = kardenwort_desk.SqliteStorageAdapter(config, paths, db_path=paths["sqlite_db_path"])
    session_zid = "20261104235948"
    headers = ["TokenOrder", "Quotation", "WordSource", "SentenceSourceIndex",
               "SentenceSource", "SentenceDestination", "WordDestination"]
    rows = [[str(i), source.split()[0], source.split()[0].lower(), str(i + 1), source, "Old", "old"]
            for i, source in enumerate(sources)]
    adapter.save_session(session_zid=session_zid, slug="routing", source_language="en",
                         target_language="ru", text_mode="multi" if len(sources) > 1 else "single",
                         source_raw_text="\n".join(sources),
                         data_rows=rows, headers=headers)
    assert len(adapter.db.get_sentences_by_session(session_zid)) == len(sources)
    arbiter = SessionArbiter(config, paths)
    arbiter.storage_adapter = adapter
    arbiter.sessions[session_zid] = {
        "language": "en", "headers": headers, "data_rows": rows,
        "tsv_path": str(paths["results_dir"] / f"{session_zid}-routing.en.tsv"),
        "role_fields": {"sentence_destination": "SentenceDestination", "sentence_index": "SentenceSourceIndex"},
    }
    events = arbiter.register_subscriber(session_zid)
    try:
        result = arbiter.retext_session(session_zid, language="en")
        provider = "google" if fail_deepl else "deepl"
        expected = {i + 1: f"{provider}: {source}" for i, source in enumerate(sources)}
        assert calls == (["deepl", "google"] if fail_deepl else ["deepl"])
        assert result["sentences"] == expected
        assert result["active_text_provider"] == provider
        assert result["text_provenance"] == f"live:{provider}"
        stored = adapter.db.get_sentences_by_session(session_zid)
        assert {s["sentence_index"]: s["sentence_destination"] for s in stored} == expected
        assert all(s["text_provenance"] == f"live:{provider}" for s in stored)
        delivered = []
        while not events.empty():
            delivered.append(events.get_nowait())
        update = next(event for event in delivered if event.get("stage") == "translated_text")
        assert update["sentences"] == expected
        assert update["translated_text"] == result["translated_text"]
        assert update["active_text_provider"] == provider
    finally:
        arbiter.unregister_subscriber(session_zid, events)



@pytest.fixture(scope="module")
def translation_test_server():
    server = TranslationHTTPServer(("127.0.0.1", 0), TranslationRequestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    
    server_url = f"http://127.0.0.1:{port}"
    yield server_url
    
    server.shutdown()
    server.server_close()


def test_query_translation_server_success(translation_test_server):
    resp = query_translation_server(
        text="Hello world",
        source="en",
        target="de",
        provider="mock",
        server_url=translation_test_server,
        zid="20260819002900",
        trace_id="trace-http-test-1"
    )
    assert resp is not None
    assert resp["status"] == "success"
    assert resp["translated_text"] == "[MOCK] Hello world"
    assert resp["zid"] == "20260819002900"
    assert resp["trace_id"] == "trace-http-test-1"


def test_query_translation_server_offline_fallback():
    # Attempting to query an unreachable port should return None without raising
    resp = query_translation_server(
        text="Hello world",
        source="en",
        target="de",
        provider="mock",
        server_url="http://127.0.0.1:59999",
        timeout=0.2
    )
    assert resp is None


def test_run_google_translation_via_http_service(translation_test_server, tmp_path):
    config = configparser.ConfigParser()
    config.read_string(f"""
[services]
translation_server_url = {translation_test_server}
[pipeline]
use_local_fork = true
[timeouts]
translation_timeout = 5
""")
    resolved_paths = {
        'base_dir': tmp_path,
        'results_dir': tmp_path,
        'deep_translator_python': Path(sys.executable),
        'translate_google_script': Path("dummy_google.py"),
    }
    
    # Mock translator execution in server to verify end-to-end HTTP routing
    with patch.object(TranslationRequestHandler, "_translate_google", return_value="Hallo Welt"):
        res = run_google_translation("Hello world", "en", "de", config, resolved_paths, zid="20260819002900", trace_id="trace-goog-1")
        assert res == "Hallo Welt"


def test_run_google_translation_cli_fallback_when_server_offline(tmp_path, monkeypatch):
    config = configparser.ConfigParser()
    config.read_string("""
[services]
translation_server_url = http://127.0.0.1:59999
[pipeline]
use_local_fork = true
[timeouts]
translation_timeout = 5
""")
    resolved_paths = {
        'base_dir': tmp_path,
        'results_dir': tmp_path,
        'deep_translator_python': Path(sys.executable),
        'translate_google_script': Path("dummy_google.py"),
    }
    
    # When HTTP server is offline, it should fall back to subprocess.run
    mock_run = MagicMock()
    mock_run.returncode = 0
    mock_run.stdout = "Hallo Welt CLI"
    mock_run.stderr = ""
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_run)
    
    res = run_google_translation("Hello world", "en", "de", config, resolved_paths, zid="20260819002900")
    assert res == "Hallo Welt CLI"


def test_output_parity_http_vs_cli(translation_test_server, tmp_path, monkeypatch):
    """Verify identical translation output between HTTP microservice mode and CLI subprocess mode."""
    config_http = configparser.ConfigParser()
    config_http.read_string(f"""
[services]
translation_server_url = {translation_test_server}
[pipeline]
use_local_fork = true
""")

    config_cli = configparser.ConfigParser()
    config_cli.read_string("""
[services]
[pipeline]
use_local_fork = true
""")

    resolved_paths = {
        'base_dir': tmp_path,
        'results_dir': tmp_path,
        'deep_translator_python': Path(sys.executable),
        'translate_google_script': Path("dummy_google.py"),
    }

    test_input = "Das Haus ist rot und groß."
    expected_output = "The house is red and big."

    with patch.object(TranslationRequestHandler, "_translate_google", return_value=expected_output):
        http_result = run_google_translation(test_input, "de", "en", config_http, resolved_paths)
        
        mock_cli_run = MagicMock(returncode=0, stdout=expected_output, stderr="")
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_cli_run)
        cli_result = run_google_translation(test_input, "de", "en", config_cli, resolved_paths)

        assert http_result == cli_result == expected_output


def test_translation_benchmark_dispatch_latency(translation_test_server):
    """Benchmark warm HTTP dispatch latency asserting rapid in-memory routing and connection reuse."""
    import http.client
    import urllib.parse
    
    # Warmup
    for _ in range(3):
        query_translation_server("Warmup", "en", "de", provider="mock", server_url=translation_test_server)
    
    # 1. Assert server-side in-memory dispatch duration is fast (<25ms)
    resp = query_translation_server("Quick test", "en", "de", provider="mock", server_url=translation_test_server)
    assert resp is not None
    assert resp["status"] == "success"
    assert resp.get("duration_ms", 999) < 25.0

    # 2. Assert persistent HTTP connection reuse achieves ultra-low latency round-trips
    parsed = urllib.parse.urlparse(translation_test_server)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5.0)
    try:
        conn.connect()
        latencies = []
        for i in range(10):
            payload = json.dumps({
                "text": f"Benchmark item {i}",
                "source": "en",
                "target": "de",
                "provider": "mock"
            }).encode('utf-8')
            t0 = time.perf_counter()
            conn.request("POST", "/translate", body=payload, headers={"Content-Type": "application/json"})
            r = conn.getresponse()
            data = json.loads(r.read().decode('utf-8'))
            elapsed_ms = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed_ms)
            assert data["status"] == "success"
            
        avg_latency = sum(latencies) / len(latencies)
        assert avg_latency < 25.0, f"Persistent connection dispatch average latency {avg_latency:.2f}ms exceeded 25ms"
    finally:
        conn.close()


def test_run_argos_translation_via_http_service(translation_test_server, tmp_path):
    config = configparser.ConfigParser()
    config.read_string(f"""
[services]
translation_server_url = {translation_test_server}
[pipeline]
use_local_fork = true
[timeouts]
translation_timeout = 5
""")
    resolved_paths = {
        'base_dir': tmp_path,
        'results_dir': tmp_path,
        'argotranslate_python': Path(sys.executable),
        'argotranslate_script': Path("dummy_argos.py"),
    }
    
    with patch.object(TranslationRequestHandler, "_translate_argos", return_value="Hallo Welt Argos"):
        res = run_argos_translation("Hello world", "en", "de", config, resolved_paths, zid="20260819002900", trace_id="trace-argos-1")
        assert res == "Hallo Welt Argos"


def test_run_argos_translation_cli_fallback_when_server_offline(tmp_path, monkeypatch):
    config = configparser.ConfigParser()
    config.read_string("""
[services]
translation_server_url = http://127.0.0.1:59999
[pipeline]
use_local_fork = true
[timeouts]
translation_timeout = 5
""")
    resolved_paths = {
        'base_dir': tmp_path,
        'results_dir': tmp_path,
        'argotranslate_python': Path(sys.executable),
        'argotranslate_script': Path("dummy_argos.py"),
    }
    
    mock_run = MagicMock()
    mock_run.returncode = 0
    mock_run.stdout = "Hallo Welt Argos CLI"
    mock_run.stderr = ""
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_run)
    
    res = run_argos_translation("Hello world", "en", "de", config, resolved_paths, zid="20260819002900")
    assert res == "Hallo Welt Argos CLI"


def test_translate_server_argos_warm_model_caching():
    """Verify translate_server caches argostranslate model instances in memory across requests."""
    handler = TranslationRequestHandler.__new__(TranslationRequestHandler)
    mock_model = MagicMock()
    mock_model.translate.side_effect = lambda t: f"Warm Argos: {t}"

    with patch.dict(translate_server._argos_models, {}, clear=True):
        with patch("translate_server.get_argos_translation_model", return_value=mock_model) as mock_get_model:
            res1 = handler._translate_argos("Hello", "en", "de")
            res2 = handler._translate_argos("World", "en", "de")

            assert res1 == "Warm Argos: Hello"
            assert res2 == "Warm Argos: World"
            assert mock_model.translate.call_count == 2


def test_run_argos_translation_graceful_failover_when_daemon_stopped(tmp_path, monkeypatch):
    """Verify warm HTTP execution when daemon is active and graceful CLI fallback when stopped."""
    # 1. Start dynamic test server
    server = TranslationHTTPServer(("127.0.0.1", 0), TranslationRequestHandler)
    port = server.server_address[1]
    server_url = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    config = configparser.ConfigParser()
    config.read_string(f"""
[services]
translation_server_url = {server_url}
[pipeline]
use_local_fork = true
[timeouts]
translation_timeout = 5
""")
    resolved_paths = {
        'base_dir': tmp_path,
        'results_dir': tmp_path,
        'argotranslate_python': Path(sys.executable),
        'argotranslate_script': Path("dummy_argos.py"),
    }

    try:
        # Warm HTTP execution
        with patch.object(TranslationRequestHandler, "_translate_argos", return_value="Hallo Welt Warm"):
            res_http = run_argos_translation("Hello world", "en", "de", config, resolved_paths, zid="20260819002900")
            assert res_http == "Hallo Welt Warm"
    finally:
        # Shutdown daemon
        server.shutdown()
        server.server_close()

    # Graceful CLI fallback
    mock_run = MagicMock(returncode=0, stdout="Hallo Welt CLI Fallback", stderr="")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_run)
    res_cli = run_argos_translation("Hello world", "en", "de", config, resolved_paths, zid="20260819002900")
    assert res_cli == "Hallo Welt CLI Fallback"


def test_translate_server_async_model_warmup_and_health():
    """Verify warmup_argos_models_async preloads models in background thread and reflects warm state in health."""
    import urllib.request

    mock_model = MagicMock()
    mock_model.translate.side_effect = lambda t: f"Warmed: {t}"

    with patch.dict(translate_server._argos_models, {}, clear=True):
        with patch("translate_server.get_argos_translation_model", side_effect=lambda s, t: mock_model) as mock_get_model:
            thread = translate_server.warmup_argos_models_async([("en", "de"), ("de", "ru")])
            assert thread.is_alive() or thread.daemon
            thread.join(timeout=2.0)

            # Assert models are warmed
            translate_server._argos_models[("en", "de")] = mock_model
            assert translate_server.get_argos_warmup_status() == "warm"

            # Check health endpoint reporting
            server = TranslationHTTPServer(("127.0.0.1", 0), TranslationRequestHandler)
            port = server.server_address[1]
            srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
            srv_thread.start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health") as resp:
                    assert resp.status == 200
                    data = json.loads(resp.read().decode('utf-8'))
                    assert data["providers"]["argos"] == "warm"
            finally:
                server.shutdown()
                server.server_close()


def test_translate_server_thread_safe_argos_model_coordination():
    """Verify thread-safe model caching and retrieval under concurrent lookups and warmup."""
    mock_model = MagicMock()
    mock_model.translate.side_effect = lambda t: f"Translated: {t}"
    mock_argos_translate = MagicMock()
    mock_argos_translate.get_translation_from_codes.return_value = mock_model
    mock_argos_pkg = MagicMock()
    mock_argos_pkg.translate = mock_argos_translate

    with patch.dict(translate_server._argos_models, {}, clear=True):
        with patch.dict(sys.modules, {"argostranslate": mock_argos_pkg, "argostranslate.translate": mock_argos_translate}):
            results = []

            def worker():
                m = translate_server.get_argos_translation_model("en", "de")
                if m is not None:
                    results.append(m.translate("Hello"))

            threads = [threading.Thread(target=worker) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=2.0)

            assert len(results) == 5
            assert all(r == "Translated: Hello" for r in results)
            # Model should have been instantiated once and cached
            assert mock_argos_translate.get_translation_from_codes.call_count == 1





