import sys
import time
from pathlib import Path
import pytest
import kardenwort_desk
from tests.controller_test_support import controller_world
from tests.render_pipeline_support import configure_render_pipeline
from kardenwort_desk import run_render_flow

DEEP_TRANSLATOR_DIR = Path(__file__).resolve().parent.parent.parent / "20241122093311-deep-translator"
if str(DEEP_TRANSLATOR_DIR) not in sys.path:
    sys.path.insert(0, str(DEEP_TRANSLATOR_DIR))

def test_google_session_pooling_contract():
    """Verify that translate_google uses persistent requests.Session with connection pooling."""
    import translate_google
    session = translate_google.get_global_session()
    assert session is not None
    assert "https://" in session.adapters
    adapter = session.adapters["https://"]
    assert adapter._pool_connections >= 10
    assert adapter._pool_maxsize >= 10

def test_deepl_session_pooling_contract():
    """Verify that translate_deepl uses persistent requests.Session with connection pooling."""
    import translate_deepl
    session = translate_deepl.get_global_session()
    assert session is not None
    assert "https://" in session.adapters
    adapter = session.adapters["https://"]
    assert adapter._pool_connections >= 10
    assert adapter._pool_maxsize >= 10

def test_concurrent_execution_overlap(controller_world):
    """Keep the subsecond budget on real local extraction and provider transport."""
    _, server = controller_world
    config, resolved_paths = server.config, server.resolved_paths
    configure_render_pipeline(server)
    start_time = time.perf_counter()
    html = run_render_flow('Hello world. Another sentence.', 'und', '20260819000000', 'single',
                           config, resolved_paths, spawn_children=False, wordfill_cfg={'enabled': False})
    elapsed = time.perf_counter() - start_time
    assert elapsed < 0.55, f"Execution was too slow ({elapsed:.3f}s), expected concurrent overlap (<0.55s)"
    assert 'Hello' in html and 'Another' in html
    cold = kardenwort_desk.SqliteStorageAdapter(config, resolved_paths)
    assert len(cold.db.get_sentences_by_session('20260819000000')) == 2
    assert all(row['sentence_destination'] for row in cold.db.get_sentences_by_session('20260819000000'))
    assert server.translation_service.provider_calls


def test_parallel_thread_exception_safety_and_zid(caplog):
    from concurrent.futures import ThreadPoolExecutor
    from kardenwort_render_preparation import collect_preparation
    test_zid = "20260819099999"

    def core():
        raise RuntimeError("Synthetic core worker crash")

    with ThreadPoolExecutor(max_workers=2) as executor:
        core_task = executor.submit(core)
        translation_task = executor.submit(lambda: 'Translated')
        with pytest.raises(RuntimeError, match="Synthetic core worker crash"):
            collect_preparation(core_task, translation_task, 1, test_zid, kardenwort_desk.logger)
    assert any(test_zid in record.message for record in caplog.records if record.levelname == "ERROR")


def test_partial_translation_persistence_under_concurrency(tmp_path):
    """Retained explicit file-host edge uses real input, transport, and text export."""
    from tests.controller_test_support import owned_configuration, translation_service, DeterministicTranslationHandler

    class ExportEngine(DeterministicTranslationHandler):
        def translate(self, provider, text, source, target):
            self.server.provider_calls.append((text, source, target))
            return "Я ем яблоко."

    with translation_service(ExportEngine) as engine:
        config, resolved_paths, _ = owned_configuration(tmp_path, f'http://127.0.0.1:{engine.server_port}', 'tsv')
        config.read_dict({'rendering': {'display_mode': 'monolithic'},
                          'settings': {'save_translation_text': 'true'},
                          'project_structure': {'generated_results_dir': str(tmp_path)},
                          'triggers': {'run_text_translation': 'auto', 'run_lemma_base_translation': 'manual',
                                       'run_lemma_enrichment': 'manual'}})
        resolved_paths.update(results_dir=tmp_path, generated_results_dir=tmp_path)
        test_zid = "20260819055555"
        tsv_file = tmp_path / f"{test_zid}-sample.en.tsv"
        tsv_file.write_text("# comments\nWordSource\tWordDestination\tSentenceSource\tSentenceDestination\tSentenceSourceIndex\napple\t\tI eat an apple.\t\t1\n", encoding='utf-8')
        run_render_flow("I eat an apple.", "en", test_zid, "single", config, resolved_paths,
                        tsv_path=tsv_file, spawn_children=False, wordfill_cfg={'enabled': False})
        trans_files = list(tmp_path.glob(f"{test_zid}*.ru.txt"))
        assert len(trans_files) >= 1
        assert "Я ем яблоко." in trans_files[0].read_text(encoding='utf-8')
        assert engine.provider_calls
