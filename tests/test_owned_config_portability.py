"""Configuration relocation and independent committed state use real IO."""
from pathlib import Path

import kardenwort_desk as desk
from kardenwort_state_runtime import preference_store
from tests.controller_test_support import owned_configuration
from tests.owned_config_support import DefaultConfiguration


def test_copied_controller_config_reloads_static_capabilities(tmp_path):
    config, paths, _ = owned_configuration(tmp_path, '', 'sqlite')
    path = tmp_path/'worker.ini'
    with path.open('w', encoding='utf-8') as stream:
        config.write(stream)
    restored, actual, _, _ = desk.load_config(path)
    assert actual['kardenwort_python'] == paths['kardenwort_python']
    assert actual['kardenwort_python'].is_file()
    assert actual['sqlite_db_path'] == paths['sqlite_db_path']
    assert desk.get_deepl_key(restored, actual['base_dir']) == 'fixture-only-deepl-key'


def test_default_config_owners_do_not_share_committed_preferences(tmp_path, pytestconfig):
    loader = pytestconfig._owned_default_config
    first = DefaultConfiguration(loader.root, tmp_path/'first', loader.loader)
    second = DefaultConfiguration(loader.root, tmp_path/'second', loader.loader)
    one, one_paths, _, _ = first()
    two, two_paths, _, _ = second()
    original = preference_store(two, two_paths).get('highlight_same_lemma')
    preference_store(one, one_paths).set('highlight_same_lemma', not original)
    reloaded, reloaded_paths, _, _ = first()
    assert preference_store(reloaded, reloaded_paths).get('highlight_same_lemma') is not original
    assert preference_store(two, two_paths).get('highlight_same_lemma') is original
    assert Path(one_paths['sqlite_db_path']) != Path(two_paths['sqlite_db_path'])
