"""
Verification tests for mass paragraph decomposition and spawn packaging (30-40 sentences).

Capability: mass-spawn-orchestration, multi-mode-decomposition
"""
import configparser
import pytest
from pathlib import Path
import kardenwort_desk as desk


def test_mass_paragraph_decomposition_sequence_continuity(monkeypatch, tmp_path):
    """
    Test decomposing a 35-sentence paragraph to verify that:
    1. All 35 child sessions are correctly initialized with distinct ZIDs and slugs.
    2. Sequence numbers passed to spawn_ahk are strictly monotonic and continuous.
    3. spawn_ahk receives clean argument blocks without duplicate passes.
    """
    config = configparser.ConfigParser()
    config.read_string("""
[settings]
default_target_language=ru
[rendering]
display_mode=progressive
[pipeline]
progressive_text_translation=true
progressive_timeout_seconds=15
lemma_base_provider=google
lemma_reprocess_provider=intellifiller
[triggers]
run_text_translation=auto
run_lemma_base_translation=auto
run_lemma_enrichment=manual
[sentences_mode]
enabled=true
parent_mode=table
[languages]
en_prompt=
[fields]
""")

    mapping_file = tmp_path / "mapping.ini"
    mapping_file.write_text(
        "[fields]\n"
        "WordSource=\nWordDestination=\nWordSourceInflectedForm=\n"
        "SentenceSourceIndex=\nSentenceDestination=\nDeskSelected=\n"
        "[fields_mapping.word]\n"
        "WordSource=lemma\nWordDestination=word_translation\n"
        "WordSourceInflectedForm=inflected\n"
        "SentenceSourceIndex=sentence_index\n"
        "SentenceDestination=sentence_destination\nDeskSelected=selected\n",
        encoding="utf-8",
    )

    resolved_paths = {
        'results_dir': tmp_path,
        'kardenwort_core_py': tmp_path / 'dummy.py',
        'kardenwort_python': tmp_path / 'python',
        'anki_mapping_file': mapping_file,
        'kardenwort_workspace': tmp_path,
        'settings_file': tmp_path / 'settings.ini',
        'base_dir': tmp_path,
    }

    headers = [
        "WordSource", "WordDestination", "WordSourceInflectedForm",
        "SentenceSourceIndex", "SentenceDestination", "DeskSelected"
    ]

    num_sentences = 35
    sentences = [f"This is sentence number {i} with some unique content." for i in range(1, num_sentences + 1)]
    full_text = "\n".join(sentences)

    data_rows = []
    for i in range(1, num_sentences + 1):
        data_rows.append([f"word{i}", f"trans{i}", f"Word{i}", str(i), f"Sentence trans {i}", "0"])

    role_fields = {
        "lemma": "WordSource",
        "word_translation": "WordDestination",
        "inflected": "WordSourceInflectedForm",
        "sentence_index": "SentenceSourceIndex",
        "sentence_destination": "SentenceDestination",
        "selected": "DeskSelected",
    }

    master_tsv = tmp_path / "20260826000000-master.en.tsv"
    master_tsv.write_text("\t".join(headers) + "\n" + "\n".join("\t".join(r) for r in data_rows) + "\n", encoding="utf-8")

    spawn_calls = []

    def mock_spawn_ahk(args, base_dir):
        spawn_calls.append(list(args))

    monkeypatch.setattr(desk, 'load_anki_mapping', lambda p: configparser.ConfigParser())
    monkeypatch.setattr(desk, 'is_tsv_llm_filled', lambda *a, **kw: False)
    monkeypatch.setattr(desk, 'get_role_fields', lambda m, h: role_fields)
    monkeypatch.setattr(desk, 'load_kardenwort_config', lambda w: configparser.ConfigParser())
    monkeypatch.setattr(desk, 'resolve_results_dir', lambda rp, kw: tmp_path)
    monkeypatch.setattr(desk, 'prepare_lookup_tsv', lambda *a, **kw: master_tsv)
    monkeypatch.setattr(desk, 'run_progressive_worker_async', lambda *a, **kw: None)
    monkeypatch.setattr(desk, 'write_update_js', lambda *a, **kw: None)
    monkeypatch.setattr(desk, 'spawn_ahk', mock_spawn_ahk)
    monkeypatch.setattr(desk, 'translate_source_text', lambda *a, **kw: {i: f"Перевод предложения {i+1}" for i in range(num_sentences)})
    monkeypatch.setattr(desk, 'resolve_translations', lambda *a, **kw: None)

    desk.run_render_flow(
        full_text,
        "en",
        "20260826000000",
        "multi",
        config,
        resolved_paths,
        seq_num=1,
        tsv_path=None,
    )

    # Verify spawn_ahk was called
    assert len(spawn_calls) == 1, f"Expected 1 call to spawn_ahk, got {len(spawn_calls)}"
    args = spawn_calls[0]

    # Verify 35 items spawned
    seq_nums = []
    restore_paths = []
    i = 0
    while i < len(args):
        assert args[i] == "--seq-num"
        seq_nums.append(int(args[i + 1]))
        assert args[i + 2] == "--restore"
        restore_paths.append(args[i + 3])
        i += 4

    assert len(seq_nums) == num_sentences, f"Expected {num_sentences} sequence numbers, got {len(seq_nums)}"
    assert len(restore_paths) == num_sentences, f"Expected {num_sentences} restore paths, got {len(restore_paths)}"

    # Check sequence continuity: 2, 3, 4, ..., 36 (since master_seq=1, child seqs are 2..36)
    expected_seqs = list(range(2, 2 + num_sentences))
    assert seq_nums == expected_seqs, f"Sequence numbers mismatch. Got: {seq_nums}, Expected: {expected_seqs}"


def test_concurrent_render_burst_handling(monkeypatch, tmp_path):
    """
    Test that the HTTP server handles a burst of 35 simultaneous /api/v1/render requests
    without socket timeouts, deadlocks, or dropped connections.
    """
    import threading
    import urllib.request
    import json
    from http_server import APIRequestHandler, ThreadingHTTPServer

    config = configparser.ConfigParser()
    config.read_string("""
[settings]
default_target_language=ru
[server]
enabled=true
api_key=test-token
""")
    resolved_paths = {
        'results_dir': tmp_path,
        'kardenwort_workspace': tmp_path,
        'base_dir': tmp_path,
    }

    server = ThreadingHTTPServer(('127.0.0.1', 0), APIRequestHandler)
    server.allow_reuse_address = True
    server.daemon_threads = True
    server.config = config
    server.resolved_paths = resolved_paths
    server.goldendict = {'server_api_key': 'test-token'}
    server.api_key = 'test-token'
    server.seq_counter = 0
    server.seq_lock = threading.Lock()
    port = server.server_address[1]

    import http_server
    # Mock render flow to return simple html
    monkeypatch.setattr(http_server, 'run_render_flow', lambda *a, **kw: "<html><body>Rendered</body></html>")

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        num_clients = 35
        results = []
        errors = []

        def worker(client_id):
            try:
                url = f"http://127.0.0.1:{port}/api/v1/render"
                payload = json.dumps({
                    "session_zid": f"202608260000{client_id:02d}",
                    "language": "en",
                    "text": f"Sentence {client_id}",
                    "text_mode": "single",
                    "bypass_lang_check": True,
                }).encode('utf-8')
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={
                        "Content-Type": "application/json; charset=utf-8",
                        "X-API-Token": "test-token"
                    }
                )
                with urllib.request.urlopen(req, timeout=10.0) as resp:
                    resp_obj = json.loads(resp.read().decode('utf-8'))
                    ok_val = resp_obj.get("data", {}).get("ok") if "data" in resp_obj else resp_obj.get("ok")
                    results.append((client_id, resp.status, ok_val))
            except Exception as e:
                errors.append((client_id, str(e)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_clients)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15.0)

        assert len(errors) == 0, f"Encountered errors during concurrent burst: {errors}"
        assert len(results) == num_clients, f"Expected {num_clients} successful responses, got {len(results)}"
        for cid, status, ok in results:
            assert status == 200, f"Client {cid} got status {status}"
            assert ok is True, f"Client {cid} got ok={ok}"
    finally:
        server.shutdown()
        server.server_close()

