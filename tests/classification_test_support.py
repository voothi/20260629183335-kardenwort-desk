"""Owned dictionary inputs; classification parsing and persistence remain real."""
from pathlib import Path


def configure_classification(server, monkeypatch):
    config, paths = server.config, server.resolved_paths
    # Code is a static capability; dictionaries and configuration belong to this test.
    monkeypatch.syspath_prepend(str(Path(paths['kardenwort_workspace'])/'src'))
    workspace = Path(paths['base_dir'])/'classification-input'
    workspace.mkdir()
    (workspace/'levels.tsv').write_text(
        'lemma\tlevel\nword1\t3k:A1\nword2\t5k:B2\ninvest\t3k:B2\n'
        'abandon\tB2\nability\tA2\ndoctor\tA1\npatient\tB1\n', encoding='utf-8')
    (workspace/'config.ini').write_text(
        '[classification]\nenabled=true\ndictionaries_en=oxford=levels.tsv\n', encoding='utf-8')
    paths['kardenwort_workspace'] = workspace
    config.read_dict({'environment': {'kardenwort_workspace': str(workspace)},
        'settings': {'anki_mapping_file': str(paths['anki_mapping_file']), 'default_language': 'en'},
        'classification': {'enabled': 'true'}, 'wordfill': {'enabled': 'false'},
        'pipeline': {'lemma_reprocess_provider': 'intellifiller', 'run_lemmatizer': 'false'},
        'languages': {'en_lemma_index': ''},
        'triggers': {'run_lemma_enrichment': 'manual'}})
    return config, paths
