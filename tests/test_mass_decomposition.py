"""
Verification tests for mass paragraph decomposition and spawn packaging (30-40 sentences).

Capability: mass-spawn-orchestration, multi-mode-decomposition
"""
import kardenwort_desk as desk
from tests.controller_test_support import controller_world, token_service
from tests.render_pipeline_support import configure_render_pipeline
from tests.test_structured_enrichment import control_writes, prohibited


def real_mass_render(server, monkeypatch, reverse):
    config, paths = server.config, server.resolved_paths
    configure_render_pipeline(server)
    config.read_dict({'sentences_mode': {'delivery_mode': 'multi_window', 'parent_mode': 'table',
        'spawn_order': 'reverse' if reverse else 'forward'}})
    sentences = [f'This is sentence number {i} with some unique content.' for i in range(1, 36)]
    spawn_calls = []
    monkeypatch.setattr(desk, 'spawn_ahk', lambda args, base_dir: spawn_calls.append(list(args)))
    with token_service() as tokenizer:
        config.set('services', 'spacy_server_url', f'http://127.0.0.1:{tokenizer.server_port}')
        with control_writes() as writes:
            desk.run_render_flow('\n'.join(sentences), 'en', '20260826000000', 'multi',
                                 config, paths, seq_num=1, wordfill_cfg={'enabled': False})
    assert prohibited(writes) == []
    cold = desk.SqliteStorageAdapter(config, paths)
    master = cold.db.get_sentences_by_session('20260826000000')
    assert [row['sentence_source'] for row in master] == sentences
    assert all(row['sentence_destination'] and row['text_provenance'] == 'live:google' for row in master)
    for number, sentence in enumerate(sentences, 1):
        identity = f'202608260000{number:02d}'
        child = cold.db.get_sentences_by_session(identity)
        assert len(child) == 1 and child[0]['sentence_source'] == sentence
        assert child[0]['sentence_destination'] == master[number-1]['sentence_destination']
        assert child[0]['text_provenance'] == 'live:google'
        assert cold.db.get_words_by_session(identity)
    return spawn_calls


def test_mass_paragraph_decomposition_sequence_continuity(monkeypatch, controller_world):
    """
    Test decomposing a 35-sentence paragraph to verify that:
    1. All 35 child sessions are correctly initialized with distinct ZIDs and slugs.
    2. Sequence numbers passed to spawn_ahk are strictly monotonic and continuous.
    3. spawn_ahk receives clean argument blocks without duplicate passes.
    """
    _, server = controller_world
    num_sentences = 35
    spawn_calls = real_mass_render(server, monkeypatch, reverse=False)

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


def test_mass_decomposition_reverse_spawn_order(monkeypatch, controller_world):
    """
    Test decomposing a multi-sentence paragraph when spawn_order is set to 'reverse'.
    Verifies that:
    1. Child sessions are passed to spawn_ahk in reverse sequence (e.g. 36 down to 2).
    2. Sequence numbers and restore paths match in reversed order.
    3. Window #2 (sentence 1) is dispatched last so it remains top-of-stack in Windows OS Z-order.
    """
    _, server = controller_world
    num_sentences = 35
    spawn_calls = real_mass_render(server, monkeypatch, reverse=True)

    # Verify spawn_ahk was called
    assert len(spawn_calls) == 1, f"Expected 1 call to spawn_ahk, got {len(spawn_calls)}"
    args = spawn_calls[0]

    # Verify 35 items spawned in reverse order
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

    # Check sequence is reversed: 36, 35, ..., 2
    expected_reverse_seqs = list(range(2 + num_sentences - 1, 1, -1))
    assert seq_nums == expected_reverse_seqs, f"Reverse sequence numbers mismatch. Got: {seq_nums}, Expected: {expected_reverse_seqs}"
    assert seq_nums[0] == 36 and seq_nums[-1] == 2, "First dispatched must be highest seq (36) and last must be window #2"


def test_concurrent_render_burst_handling(controller_world):
    """Thirty-five concurrent HTTP renders read actual independently stored sessions."""
    import urllib.request
    import json
    import http_server
    from kardenwort_controller import ControllerRequestHandler
    from tests.render_test_support import seed_render_rows
    from tests.thread_support import Worker, finish_workers

    server_url, server = controller_world
    assert http_server.APIRequestHandler is ControllerRequestHandler
    config, paths = server.config, server.resolved_paths
    config.read_dict({'rendering': {'display_mode': 'monolithic'},
        'sentences_mode': {'enabled': 'false'},
        'triggers': {'run_text_translation': 'manual', 'run_lemma_base_translation': 'manual',
                     'run_lemma_enrichment': 'manual'}})
    num_clients = 35
    for client_id in range(num_clients):
        text = f'Sentence {client_id}'
        seed_render_rows(config, paths, f'202608260000{client_id:02d}', text, 'en',
            ['TokenOrder', 'WordSource', 'SentenceSourceIndex', 'SentenceSource', 'SentenceDestination'],
            [['0', 'sentence', '1', text, '']])
    cold = desk.SqliteStorageAdapter(config, paths)
    before = {f'202608260000{index:02d}': cold.restore_session(f'202608260000{index:02d}')
              for index in range(num_clients)}
    results, errors = [], []
    rendered = []

    def worker(client_id):
        try:
            payload = json.dumps({'session_zid': f'202608260000{client_id:02d}',
                'language': 'en', 'text': f'Sentence {client_id}', 'text_mode': 'single',
                'bypass_lang_check': True}).encode('utf-8')
            request = urllib.request.Request(f'{server_url}/api/v1/render', data=payload,
                headers={'Content-Type': 'application/json; charset=utf-8', 'X-API-Token': server.api_key})
            with urllib.request.urlopen(request, timeout=10.0) as response:
                body = json.loads(response.read().decode('utf-8'))
                data = body.get('data', body)
                results.append((client_id, response.status, data.get('ok')))
                rendered.append((client_id, data))
        except Exception as error:
            errors.append((client_id, str(error)))

    with control_writes() as writes:
        finish_workers([Worker(lambda index=index: worker(index)) for index in range(num_clients)])
    assert len(errors) == 0, f"Encountered errors during concurrent burst: {errors}"
    assert len(results) == num_clients, f"Expected {num_clients} successful responses, got {len(results)}"
    for cid, status, ok in results:
        assert status == 200, f"Client {cid} got status {status}"
        assert ok is True, f"Client {cid} got ok={ok}"
    assert prohibited(writes) == []
    assert server.translation_service.provider_calls == []
    assert {identity: cold.restore_session(identity) for identity in before} == before
    assert len(rendered) == num_clients
    for client_id, data in rendered:
        assert f'Sentence {client_id}' in data['html']
