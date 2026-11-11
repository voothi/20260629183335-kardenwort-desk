"""Real extraction subprocess, provider HTTP, renderer, and durable SQLite effects."""
import kardenwort_desk as desk
import pytest
from tests.controller_test_support import controller_world
from tests.render_pipeline_support import configure_render_pipeline
from tests.render_test_support import seed_render_rows
from tests.test_structured_enrichment import control_writes, prohibited


def test_real_split_render_persists_extraction_and_translation(controller_world):
    _, server = controller_world
    config, paths = server.config, server.resolved_paths
    configure_render_pipeline(server)
    zid, text = '20261111140000', 'Hello world. Another sentence.'
    html = desk.run_render_flow(text, 'en', zid, 'single', config, paths,
                                spawn_children=False, wordfill_cfg={'enabled': False})
    assert 'Hello' in html and 'Another' in html
    cold = desk.SqliteStorageAdapter(config, paths)
    assert cold.db.get_session(zid)['source_raw_text'] == text
    words = cold.db.get_words_by_session(zid)
    assert {word['lemma'].lower() for word in words} >= {'hello', 'world', 'another', 'sentence'}
    assert len(cold.db.get_sentences_by_session(zid)) == 2
    assert all(row['sentence_destination'] for row in cold.db.get_sentences_by_session(zid))
    assert server.translation_service.provider_calls


def test_real_extractor_launch_failure_creates_no_session_or_control_files(controller_world, tmp_path):
    _, server = controller_world
    config, paths = server.config, dict(server.resolved_paths)
    paths['kardenwort_python'] = tmp_path / 'missing-extractor.exe'
    zid = '20261111141000'
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob('*') if path.is_file()}
    with pytest.raises(FileNotFoundError):
        desk.prepare_lookup_tsv('Hello world.', 'en', 'ru', config, paths, zid,
                                ttl_seconds=0, cache_key=f'{zid}-hello.en.tsv')
    cold = desk.SqliteStorageAdapter(config, paths)
    assert cold.db.get_session(zid) is None
    created = {path.relative_to(tmp_path) for path in tmp_path.rglob('*') if path.is_file()} - before
    assert not [path for path in created if path.suffix in ('.tsv', '.txt', '.lock', '.js')]


@pytest.mark.parametrize('text_trigger,lemma_trigger', [('manual', 'manual'), ('auto', 'manual'),
                                                       ('manual', 'auto'), ('auto', 'auto')])
def test_monolithic_text_and_lemma_triggers_are_independent(controller_world, text_trigger, lemma_trigger):
    _, server = controller_world
    config, paths = server.config, server.resolved_paths
    configure_render_pipeline(server)
    config.set('triggers', 'run_text_translation', text_trigger)
    config.set('triggers', 'run_lemma_base_translation', lemma_trigger)
    zid = '20261111140800'
    seed_render_rows(config, paths, zid, 'Hello.', 'en',
        ['TokenOrder', 'Quotation', 'WordSource', 'WordDestination', 'SentenceSourceIndex',
         'SentenceSource', 'SentenceDestination'], [['0', 'Hello', 'hello', '', '1', 'Hello.', '']])
    original = desk.SqliteStorageAdapter(config, paths)
    unrelated = original.db.get_words_by_session('20261108113001')
    with control_writes() as writes:
        html = desk.run_render_flow('Hello.', 'en', zid, 'single', config, paths,
                                    spawn_children=False, wordfill_cfg={'enabled': False})
    cold = desk.SqliteStorageAdapter(config, paths)
    words, sentences = cold.db.get_words_by_session(zid), cold.db.get_sentences_by_session(zid)
    assert [row['sentence_destination'] or '' for row in sentences] == ['google: Hello.' if text_trigger == 'auto' else '']
    assert [row['word_destination'] or '' for row in words] == ['google: hello' if lemma_trigger == 'auto' else '']
    assert len(server.translation_service.provider_calls) == (text_trigger == 'auto') + (lemma_trigger == 'auto')
    assert 'Hello' in html
    assert ('google: Hello.' in html) == (text_trigger == 'auto')
    assert sentences[0]['text_provenance'] == ('live:google' if text_trigger == 'auto' else 'fixture:render-input')
    if lemma_trigger == 'auto':
        assert words[0]['word_provenance'] == 'live:google'
    assert prohibited(writes) == []
    calls = list(server.translation_service.provider_calls)
    with control_writes() as restored_writes:
        restored = desk.run_render_flow('Hello.', 'en', zid, 'single', config, paths,
                                        spawn_children=False, wordfill_cfg={'enabled': False})
    assert ('google: Hello.' in restored) == (text_trigger == 'auto')
    assert server.translation_service.provider_calls == calls
    assert cold.db.get_words_by_session(zid) == words
    assert cold.db.get_sentences_by_session(zid) == sentences
    assert cold.db.get_words_by_session('20261108113001') == unrelated
    assert prohibited(restored_writes) == []


def test_manual_text_trigger_prevents_parallel_and_alignment_requests(controller_world):
    _, server = controller_world
    configure_render_pipeline(server)
    server.config.set('triggers', 'run_text_translation', 'manual')
    zid = '20261111140801'
    desk.run_render_flow('Hello world. Another sentence.', 'und', zid, 'single',
                         server.config, server.resolved_paths, spawn_children=False, wordfill_cfg={'enabled': False})
    assert server.translation_service.provider_calls == []
    cold = desk.SqliteStorageAdapter(server.config, server.resolved_paths)
    assert len(cold.db.get_sentences_by_session(zid)) == 2
    assert all(not row['sentence_destination'] for row in cold.db.get_sentences_by_session(zid))
