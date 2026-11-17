"""Commands and tasks retain committed preferences across concurrent commits."""
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
import kardenwort_controller as controller
from kardenwort_state_runtime import preference_store, project_preferences
from tests.test_release_completion_regressions import completion


@pytest.mark.parametrize('background', [False, True])
def test_running_operation_keeps_snapshot_and_next_operation_refreshes(completion, monkeypatch, background):
    db, arbiter, zid, _ = completion
    store = preference_store(arbiter.config, arbiter.resolved_paths)
    project_preferences(arbiter.config, store.set('theme', 'dark')['snapshot'])
    entered, release = threading.Event(), threading.Event()
    calls = []
    queue = arbiter.enrichment_queue

    def engine(lemmas, *args, **kwargs):
        if not calls:
            calls.append(True)
            entered.set()
            assert release.wait(5), 'Owned engine was not released'
        owner = queue if background else arbiter
        return {lemma: owner.config.get('ui', 'theme') for lemma in lemmas}

    monkeypatch.setattr(controller, 'translate_lemmas_fast_path', engine)
    def operation():
        if background:
            return queue._execute_translate_lemma('Haus')
        arbiter.retry_session_rows(zid, target_coordinates=[dict(sentence_idx=1, token_order=0)])
        return db.get_words_by_session(zid)[0]['word_destination']

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(operation)
        try:
            assert entered.wait(5), 'Owned engine was not entered'
            project_preferences(arbiter.config, store.set('theme', 'light')['snapshot'])
        finally:
            release.set()
        assert pending.result(timeout=10) == 'dark'
    assert operation() == 'light'


def test_nested_capture_preserves_parser_defaults_interpolation_and_converters(completion):
    from kardenwort_state_runtime import preference_operation
    _, arbiter, _, _ = completion
    config = arbiter.config
    config['DEFAULT']['base'] = 'old'
    config.add_section('static_fixture')
    config.set('static_fixture', 'path', '%(base)s/path')
    config.set('static_fixture', 'items', 'one,two')
    config.converters['csv'] = lambda value: value.split(',')
    store = preference_store(config, arbiter.resolved_paths)
    with preference_operation(config, arbiter.resolved_paths) as captured:
        config['DEFAULT']['base'] = 'new'
        config.set('static_fixture', 'items', 'three')
        project_preferences(config, store.set('theme', 'light')['snapshot'])
        with preference_operation(config, arbiter.resolved_paths) as nested:
            assert nested is captured
            assert arbiter.config is captured
            assert arbiter.enrichment_queue.config is captured
            assert captured.get('static_fixture', 'path') == 'old/path'
            assert captured.getcsv('static_fixture', 'items') == ['one', 'two']
            assert captured.get('ui', 'theme') == 'dark'
    with preference_operation(config, arbiter.resolved_paths) as next_operation:
        assert next_operation.get('static_fixture', 'path') == 'new/path'
        assert next_operation.get('ui', 'theme') == 'light'


def test_capture_reads_an_independent_preference_owner_without_writes(completion):
    from kardenwort_sqlite_preferences import PreferencesAdapter
    from kardenwort_state_runtime import preference_operation
    db, arbiter, _, _ = completion
    config = arbiter.config
    config.read_dict({'ui': {'theme': 'dark'}})
    independent = PreferencesAdapter(db)
    independent.initialize(config)
    independent.set('theme', 'light')
    assert not hasattr(config, '_preference_store')
    with db.get_connection(read_only=True) as connection:
        before = list(connection.iterdump())
    with preference_operation(config, arbiter.resolved_paths) as captured:
        assert captured.get('ui', 'theme') == 'light'
    assert config.get('ui', 'theme') == 'dark'
    with db.get_connection(read_only=True) as connection:
        assert list(connection.iterdump()) == before
