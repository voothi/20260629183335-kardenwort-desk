from kardenwort_desk import run_render_flow
import pytest
import kardenwort_desk
from tests.render_pipeline_support import overlapping_pipeline

def test_parallel_execution_overlaps_real_engines(overlapping_pipeline):
    server, observed = overlapping_pipeline
    config, resolved_paths = server.config, server.resolved_paths
    html = run_render_flow('Hello world. Another sentence.', 'en', '20261111135900', 'single',
                           config, resolved_paths, spawn_children=False, wordfill_cfg={'enabled': False})
    assert not server.render_preparation_rendezvous.broken, 'Extraction and translation did not overlap'
    assert observed == {'extraction', 'translation'}
    assert 'Hello' in html and 'Another' in html
    cold = kardenwort_desk.SqliteStorageAdapter(config, resolved_paths)
    assert len(cold.db.get_sentences_by_session('20261111135900')) == 2
    assert all(row['sentence_destination'] for row in cold.db.get_sentences_by_session('20261111135900'))


def test_parallel_execution_exception_handling():
    from concurrent.futures import ThreadPoolExecutor
    from kardenwort_render_preparation import collect_preparation, TranslationStatus

    def translation():
        raise ValueError('Network error simulated')

    with ThreadPoolExecutor(max_workers=2) as executor:
        result = collect_preparation(executor.submit(lambda: 'source'), executor.submit(translation),
                                     1, '20261111140901', kardenwort_desk.logger)
    assert result.source == 'source'
    assert result.status is TranslationStatus.FAILED
    assert isinstance(result.error, ValueError)


def test_parallel_core_failure():
    from concurrent.futures import ThreadPoolExecutor
    from kardenwort_render_preparation import collect_preparation

    def core():
        raise RuntimeError("Fatal TSV error")

    with ThreadPoolExecutor(max_workers=2) as executor:
        core_task = executor.submit(core)
        translation_task = executor.submit(lambda: 'Translated')
        with pytest.raises(RuntimeError, match="Fatal TSV error"):
            collect_preparation(core_task, translation_task, 1, '20261111140902', kardenwort_desk.logger)


def test_parallel_exception_priority():
    from concurrent.futures import ThreadPoolExecutor
    from kardenwort_render_preparation import collect_preparation
    import threading

    translation_failed = threading.Event()

    def core():
        assert translation_failed.wait(5)
        raise RuntimeError("Fatal TSV error")

    def translation():
        raise ValueError('Network error')

    with ThreadPoolExecutor(max_workers=2) as executor:
        core_task = executor.submit(core)
        translation_task = executor.submit(translation)
        translation_task.add_done_callback(lambda completed: translation_failed.set())
        with pytest.raises(RuntimeError, match="Fatal TSV error"):
            collect_preparation(core_task, translation_task, 1, '20261111140903', kardenwort_desk.logger)
        assert translation_task.done()
        assert isinstance(translation_task.exception(), ValueError)
