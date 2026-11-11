import pytest
import sys
import kardenwort_desk
from kardenwort_desk import main
from tests.controller_test_support import controller_world, DeterministicTranslationHandler

@pytest.fixture
def lookup_cli_world(controller_world, monkeypatch):
    """Owned CLI configuration; only unmanaged translation output is deterministic."""
    _, server = controller_world
    config, paths = server.config, server.resolved_paths
    config.read_dict({'settings': {'anki_mapping_file': str(paths['anki_mapping_file']),
                                   'default_language': 'en', 'default_target_language': 'ru'},
        'goldendict': {'run_intellifiller': 'false', 'lookup_ttl_seconds': '0',
                      'sentence_match_strategy': 'none', 'format': 'html'},
        'wordfill': {'enabled': 'false'}})
    configuration = paths['base_dir']/'lookup-config.ini'
    with configuration.open('w', encoding='utf-8') as stream:
        config.write(stream)

    def engine(handler, provider, text, source, target):
        handler.server.provider_calls.append((text, source, target))
        return 'working'

    monkeypatch.setattr(DeterministicTranslationHandler, 'translate', engine)
    return server, configuration


def assert_lookup_cli_persisted(server):
    cold = kardenwort_desk.SqliteStorageAdapter(server.config, server.resolved_paths)
    with cold.db.get_connection() as connection:
        sessions = connection.execute("SELECT zid FROM sessions WHERE source_raw_text = 'running'").fetchall()
    assert len(sessions) == 1
    zid = sessions[0]['zid']
    assert cold.db.get_sentences_by_session(zid)[0]['sentence_destination'] == 'working'
    assert cold.db.get_words_by_session(zid)
    assert server.translation_service.provider_calls


def test_lookup_cli_smoke(monkeypatch, lookup_cli_world):
    import io
    server, configuration = lookup_cli_world
    monkeypatch.setattr(sys, 'argv', ['kardenwort_desk.py', '--config', str(configuration),
        'lookup', '--text', 'running', '--language', 'en', '--format', 'text'])
    mock_out = io.StringIO()
    monkeypatch.setattr(sys, '__stdout__', mock_out)
    monkeypatch.setattr(sys, 'stdout', mock_out)
    monkeypatch.setattr(sys, 'stderr', mock_out)
    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 0
    out_str = mock_out.getvalue()
    assert "working" in out_str
    assert_lookup_cli_persisted(server)


def test_lookup_cli_overrides(monkeypatch, lookup_cli_world):
    import io
    server, configuration = lookup_cli_world
    monkeypatch.setattr(sys, 'argv', ['kardenwort_desk.py', '--config', str(configuration),
        'lookup', '--text', 'running', '--language', 'en', '--sections', 'lemmas',
        '--lemma-columns', 'lemma,translation', '--no-headings'])
    mock_out = io.StringIO()
    monkeypatch.setattr(sys, '__stdout__', mock_out)
    monkeypatch.setattr(sys, 'stdout', mock_out)
    monkeypatch.setattr(sys, 'stderr', mock_out)
    with pytest.raises(SystemExit):
        main()

    out_str = mock_out.getvalue()
    assert '<div class="kw-translation"' not in out_str
    assert '<div class="kw-source-text"' not in out_str
    assert "<h3>" not in out_str
    assert "Lemma" in out_str
    assert_lookup_cli_persisted(server)


def test_cmd_merge_cli(tmp_path, monkeypatch):
    tsv1 = tmp_path / "20260729000000-file1.en.tsv"
    tsv1.write_text("WordSource\tWordDestination\nHaus\thouse\nAuto\tcar\n", encoding="utf-8")
    
    tsv2 = tmp_path / "20260729000001-file2.en.tsv"
    tsv2.write_text("WordSource\tWordDestination\nAuto\tcar\nHund\tdog\n", encoding="utf-8")
    
    monkeypatch.setattr(sys, 'argv', ['kardenwort_desk.py', 'merge', '--files', str(tsv1), str(tsv2), '--deduplicate'])
    
    import io
    mock_out = io.StringIO()
    monkeypatch.setattr(sys, '__stdout__', mock_out)
    monkeypatch.setattr(sys, 'stdout', mock_out)
    
    main()
    out_str = mock_out.getvalue()
    assert "SUCCESS: Merged Files" in out_str
