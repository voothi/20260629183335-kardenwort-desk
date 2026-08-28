from kardenwort_desk import (
    SEC_SETTINGS, SEC_TOKEN_MAPPINGS, SEC_MERGE, SEC_SENTENCES_MODE,
    SEC_CLASSIFICATION, SEC_TIMEOUTS, SEC_PIPELINE, SEC_TRIGGERS,
    SEC_TRANSLATION, SEC_TRANSLATION_PROVIDERS, SEC_RENDERING,
    SEC_ENVIRONMENT, SEC_LANGUAGES, SEC_LANGUAGE_RESOURCES,
    SEC_PROJECT_STRUCTURE, SEC_AUDIO, SEC_GOLDENDICT, SEC_WORDFILL
)
import os
import tempfile
import pytest
from pathlib import Path
import kardenwort_desk

def test_config_relative_paths():
    # Create a temporary directory structure to simulate sibling projects
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        # Create simulated sibling projects
        spacy_env = tmp_path / "spacy-env"
        spacy_env.mkdir()
        python_exe = spacy_env / "python.exe"
        python_exe.touch()
        
        kardenwort_dir = tmp_path / "kardenwort"
        kardenwort_dir.mkdir()
        
        # Create a directory for the desk app
        desk_dir = tmp_path / "kardenwort-desk"
        desk_dir.mkdir()
        
        # Create a test anki-mapping.ini next to config
        anki_mapping = desk_dir / "anki-mapping.ini"
        anki_mapping.write_text("[fields]\nQuotation\n[desk_columns]\nQuotation=quotation\n[desk_editable]\neditable_columns=Quotation")
        
        # Create a config.ini inside desk_dir pointing to simulated siblings via relative paths
        config_content = f"""[environment]
kardenwort_python = ../spacy-env/python.exe
kardenwort_workspace = ../kardenwort

[settings]
favorites_output_dir = ./favorites
anki_mapping_file = ./anki-mapping.ini
"""
        config_file = desk_dir / "config.ini"
        config_file.write_text(config_content)
        
        # Load the config using the desk core loader
        config, resolved_paths, gd, _wf = kardenwort_desk.load_config(config_file)
        
        # Verify resolution
        assert resolved_paths["kardenwort_python"] == python_exe.resolve()
        assert resolved_paths["kardenwort_workspace"] == kardenwort_dir.resolve()
        assert resolved_paths["anki_mapping_file"] == anki_mapping.resolve()
        assert resolved_paths["favorites_output_dir"] == (desk_dir / "favorites").resolve()

def test_config_missing_path_error():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        desk_dir = tmp_path / "kardenwort-desk"
        desk_dir.mkdir()
        
        config_content = """[environment]
kardenwort_python = ../non_existent_env/python.exe
"""
        config_file = desk_dir / "config.ini"
        config_file.write_text(config_content)
        
        with pytest.raises(Exception) as excinfo:
            kardenwort_desk.load_config(config_file)
        assert "kardenwort_python" in str(excinfo.value)

def test_parse_sections_list(capsys):
    valid = ['source', 'translation', 'lemmas']
    # defaults
    assert kardenwort_desk.parse_sections_list("translation,lemmas", valid) == ['translation', 'lemmas']
    # custom order
    assert kardenwort_desk.parse_sections_list("lemmas, source", valid) == ['lemmas', 'source']
    # empty list
    assert kardenwort_desk.parse_sections_list("", valid) == []
    # whitespace tolerance
    assert kardenwort_desk.parse_sections_list("  translation , lemmas  ", valid) == ['translation', 'lemmas']
    # unknown token warning + skip
    assert kardenwort_desk.parse_sections_list("source,unknown,lemmas", valid) == ['source', 'lemmas']
    captured = capsys.readouterr()
    assert "Unknown section token 'unknown'" in captured.err

def test_parse_columns_list(capsys):
    valid = ['inflected', 'lemma', 'translation']
    assert kardenwort_desk.parse_columns_list("inflected,unknown", valid) == ['inflected']
    captured = capsys.readouterr()
    assert "Unknown column token 'unknown'" in captured.err

def test_goldendict_config_defaults():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        desk_dir = tmp_path / "kardenwort-desk"
        desk_dir.mkdir()
        
        anki_mapping = desk_dir / "anki-mapping.ini"
        anki_mapping.write_text("")
        
        config_content = """[settings]
anki_mapping_file = ./anki-mapping.ini
default_target_language = uk
"""
        config_file = desk_dir / "config.ini"
        config_file.write_text(config_content)
        
        config, resolved_paths, gd, _wf = kardenwort_desk.load_config(config_file)
        
        assert gd['format'] == 'html'
        assert gd['target_language'] == 'uk'
        assert gd['run_intellifiller'] is False
        assert gd['lookup_ttl_seconds'] == 300
        assert gd['theme'] == 'dark'
        assert gd['emit_meta_comment'] is True
        assert gd['sections'] == ['translation', 'lemmas']
        assert gd['heading_source'] == ''
        assert gd['heading_translation'] == ''
        assert gd['heading_lemmas'] == ''
        assert gd['lemma_columns'] == ['inflected', 'lemma', 'translation']

def test_goldendict_config_overrides():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        desk_dir = tmp_path / "kardenwort-desk"
        desk_dir.mkdir()
        
        anki_mapping = desk_dir / "anki-mapping.ini"
        anki_mapping.write_text("")
        
        config_content = """[settings]
anki_mapping_file = ./anki-mapping.ini
default_target_language = uk

[goldendict]
format = text
target_language = en
run_intellifiller = true
lookup_ttl_seconds = 600
theme = light
emit_meta_comment = false
sections = source,translation
heading_source = __default__
heading_translation = Custom
heading_lemmas = None
lemma_columns = lemma,translation
"""
        config_file = desk_dir / "config.ini"
        config_file.write_text(config_content)
        
        config, resolved_paths, gd, _wf = kardenwort_desk.load_config(config_file)
        
        assert gd['format'] == 'text'
        assert gd['target_language'] == 'en'
        assert gd['run_intellifiller'] is True
        assert gd['lookup_ttl_seconds'] == 600
        assert gd['theme'] == 'light'
        assert gd['emit_meta_comment'] is False
        assert gd['sections'] == ['source', 'translation']
        assert gd['heading_source'] == '__default__'
        assert gd['heading_translation'] == 'Custom'
        assert gd['heading_lemmas'] == 'None'
        assert gd['lemma_columns'] == ['lemma', 'translation']

def test_wordfill_config_multiline_scan_roots():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        desk_dir = tmp_path / "kardenwort-desk"
        desk_dir.mkdir()
        
        anki_mapping = desk_dir / "anki-mapping.ini"
        anki_mapping.write_text("")
        
        config_content = """[settings]
anki_mapping_file = ./anki-mapping.ini

[wordfill]
enabled = true
scan_roots = 
    ../repo1
    ./repo2, ../repo3
"""
        config_file = desk_dir / "config.ini"
        config_file.write_text(config_content)
        
        config, resolved_paths, gd, wf = kardenwort_desk.load_config(config_file)
        
        assert wf['enabled'] is True
        assert len(wf['scan_roots']) == 3
        assert wf['scan_roots'][0] == (desk_dir / "../repo1").resolve()
        assert wf['scan_roots'][1] == (desk_dir / "./repo2").resolve()
        assert wf['scan_roots'][2] == (desk_dir / "../repo3").resolve()


def test_orthogonal_config_parsing():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        desk_dir = tmp_path / "kardenwort-desk"
        desk_dir.mkdir()
        anki_mapping = desk_dir / "anki-mapping.ini"
        anki_mapping.write_text("")
        
        config_content = """[settings]
anki_mapping_file = ./anki-mapping.ini
default_target_language = ru

[pipeline]
base_provider = deepl
enrichment_provider = none

[triggers]
run_base_translation = manual
run_enrichment = auto

[rendering]
display_mode = monolithic
"""
        config_file = desk_dir / "config.ini"
        config_file.write_text(config_content)
        
        config, resolved_paths, gd, _wf = kardenwort_desk.load_config(config_file)
        
        assert config.get(SEC_PIPELINE, 'lemma_base_provider') == 'deepl'
        assert config.get(SEC_PIPELINE, 'lemma_reprocess_provider') == 'none'
        assert config.get(SEC_TRIGGERS, 'run_lemma_base_translation') == 'manual'
        assert config.get(SEC_TRIGGERS, 'run_lemma_enrichment') == 'auto'
        assert config.get(SEC_RENDERING, 'display_mode') == 'monolithic'

def test_orthogonal_config_migration():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        desk_dir = tmp_path / "kardenwort-desk"
        desk_dir.mkdir()
        anki_mapping = desk_dir / "anki-mapping.ini"
        anki_mapping.write_text("")
        config_content = """[settings]
anki_mapping_file = ./anki-mapping.ini
default_target_language = ru
lazy_processing = llm_only
progressive_loading = true

[translation_providers]
main_text_translation = deepl
lemmas_translation = combined
"""
        config_file = desk_dir / "config.ini"
        config_file.write_text(config_content)
        
        config, resolved_paths, gd, _wf = kardenwort_desk.load_config(config_file)
        
        assert config.get(SEC_TRIGGERS, 'run_lemma_base_translation') == 'auto'
        assert config.get(SEC_TRIGGERS, 'run_lemma_enrichment') == 'manual'
        
        assert config.get(SEC_RENDERING, 'display_mode') == 'progressive'
        
        assert config.get(SEC_PIPELINE, 'lemma_base_provider') == 'deepl'
        assert config.get(SEC_PIPELINE, 'text_base_provider') == 'deepl'
        assert config.get(SEC_PIPELINE, 'lemma_reprocess_provider') == 'intellifiller'

def test_orthogonal_config_migration_d7():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        desk_dir = tmp_path / "kardenwort-desk"
        desk_dir.mkdir()
        anki_mapping = desk_dir / "anki-mapping.ini"
        anki_mapping.write_text("")
        
        config_content = """[settings]
anki_mapping_file = ./anki-mapping.ini
default_target_language = ru

[pipeline]
base_provider = deepl
enrichment_provider = intellifiller
"""
        config_file = desk_dir / "config.ini"
        config_file.write_text(config_content)
        
        config, resolved_paths, gd, _wf = kardenwort_desk.load_config(config_file)
        
        assert config.get(SEC_PIPELINE, 'lemma_base_provider') == 'deepl'
        assert config.get(SEC_PIPELINE, 'lemma_reprocess_provider') == 'intellifiller'

def test_default_rendering_and_triggers():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        desk_dir = tmp_path / "kardenwort-desk"
        desk_dir.mkdir()
        anki_mapping = desk_dir / "anki-mapping.ini"
        anki_mapping.write_text("")

        config_content = """[settings]
anki_mapping_file = ./anki-mapping.ini
default_target_language = ru

[rendering]
display_mode = monolithic

[pipeline]
base_provider = google
enrichment_provider = combined
"""
        config_file = desk_dir / "config.ini"
        config_file.write_text(config_content)

        config, resolved_paths, gd, _wf = kardenwort_desk.load_config(config_file)

        assert config.get(SEC_RENDERING, 'display_mode') == 'monolithic'
        assert config.get(SEC_TRIGGERS, 'run_lemma_base_translation') == 'auto'
        assert config.get(SEC_TRIGGERS, 'run_lemma_enrichment') == 'auto'
        assert config.get(SEC_PIPELINE, 'lemma_base_provider') == 'google'
        assert config.get(SEC_PIPELINE, 'lemma_reprocess_provider') == 'combined'

def test_split_gap_limit_migration():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        desk_dir = tmp_path / "kardenwort-desk"
        desk_dir.mkdir()
        anki_mapping = desk_dir / "anki-mapping.ini"
        anki_mapping.write_text("")

        # Scenario 1: split_gap_limit is not in config, should fall back to 60
        config_content = """[settings]
anki_mapping_file = ./anki-mapping.ini
"""
        config_file = desk_dir / "config.ini"
        config_file.write_text(config_content)
        config, resolved_paths, gd, _wf = kardenwort_desk.load_config(config_file)
        assert config.getint(SEC_SETTINGS, 'split_gap_limit') == 60

        # Scenario 2: split_gap_limit is integer (e.g. 15), should be parsed and returned
        config_content = """[settings]
anki_mapping_file = ./anki-mapping.ini
split_gap_limit = 15
"""
        config_file.write_text(config_content)
        config, resolved_paths, gd, _wf = kardenwort_desk.load_config(config_file)
        assert config.getint(SEC_SETTINGS, 'split_gap_limit') == 15

        # Scenario 3: split_gap_limit is non-integer, should fall back to 60 without raising error
        config_content = """[settings]
anki_mapping_file = ./anki-mapping.ini
split_gap_limit = abc
"""
        config_file.write_text(config_content)
        config, resolved_paths, gd, _wf = kardenwort_desk.load_config(config_file)
        assert config.getint(SEC_SETTINGS, 'split_gap_limit') == 60


def test_audio_config():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        desk_dir = tmp_path / "kardenwort-desk"
        desk_dir.mkdir()
        anki_mapping = desk_dir / "anki-mapping.ini"
        anki_mapping.write_text("")
        
        # Sibling project for anki-tts-cli
        cli_dir = tmp_path / "anki-tts-cli"
        cli_dir.mkdir()
        cli_script = cli_dir / "anki-tts-cli.py"
        cli_script.touch()

        config_content = f"""[environment]
anki_tts_cli = ../anki-tts-cli/anki-tts-cli.py

[settings]
anki_mapping_file = ./anki-mapping.ini

[audio]
lmb_play = true
lmb_source = inflection
lmb_chain_mode = separate
source_range_mode = all
table_range_mode = all
rmb_play = false
rmb_chain_mode = separate
"""
        config_file = desk_dir / "config.ini"
        config_file.write_text(config_content)

        config, resolved_paths, gd, _wf = kardenwort_desk.load_config(config_file)
        
        assert resolved_paths["anki_tts_cli"] == cli_script.resolve()
        assert config.getboolean(SEC_AUDIO, 'lmb_play') is True
        assert config.get(SEC_AUDIO, 'lmb_source') == 'inflection'
        assert config.get(SEC_AUDIO, 'lmb_chain_mode') == 'separate'
        assert config.get(SEC_AUDIO, 'source_range_mode') == 'all'
        assert config.get(SEC_AUDIO, 'table_range_mode') == 'all'
        assert config.getboolean(SEC_AUDIO, 'rmb_play') is False
        assert config.get(SEC_AUDIO, 'rmb_chain_mode') == 'separate'

        # Test default fallback when source_range_mode and table_range_mode are omitted
        config_content_default = f"""[environment]
anki_tts_cli = ../anki-tts-cli/anki-tts-cli.py

[settings]
anki_mapping_file = ./anki-mapping.ini

[audio]
lmb_play = true
"""
        config_file.write_text(config_content_default)
        config_def, _, _, _ = kardenwort_desk.load_config(config_file)
        assert config_def.get(SEC_AUDIO, 'source_range_mode', fallback='all') == 'all'
        assert config_def.get(SEC_AUDIO, 'table_range_mode', fallback='none') == 'none'




def test_sentences_mode_and_export_selection_combinations():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        desk_dir = tmp_path / "kardenwort-desk"
        desk_dir.mkdir()
        anki_mapping = desk_dir / "anki-mapping.ini"
        anki_mapping.write_text("")

        config_content = """[settings]
anki_mapping_file = ./anki-mapping.ini
export_selection_mode = unselected
anki_context_mode = both
save_to_favorites_on_export = true
copy_source_txt_to_favorites_on_export = false

[sentences_mode]
enabled = true
alignment_method = proportion
min_sentences = 3
spawn_order = reverse
parent_mode = stub
delivery_mode = container
"""
        config_file = desk_dir / "config.ini"
        config_file.write_text(config_content)

        config, resolved_paths, gd, _wf = kardenwort_desk.load_config(config_file)

        assert config.get(SEC_SETTINGS, 'export_selection_mode') == 'unselected'
        assert config.get(SEC_SETTINGS, 'anki_context_mode') == 'both'
        assert config.getboolean(SEC_SETTINGS, 'save_to_favorites_on_export') is True
        assert config.getboolean(SEC_SETTINGS, 'copy_source_txt_to_favorites_on_export') is False

        assert config.getboolean(SEC_SENTENCES_MODE, 'enabled') is True
        assert config.get(SEC_SENTENCES_MODE, 'alignment_method') == 'proportion'
        assert config.getint(SEC_SENTENCES_MODE, 'min_sentences') == 3
        assert config.get(SEC_SENTENCES_MODE, 'spawn_order') == 'reverse'
        assert config.get(SEC_SENTENCES_MODE, 'parent_mode') == 'stub'
        assert config.get(SEC_SENTENCES_MODE, 'delivery_mode') == 'container'

        smc = kardenwort_desk.SentencesModeConfig.from_config(config)
        assert smc.delivery_mode == 'container'
        assert smc.web_tab_mode == 'container'


def test_sentences_mode_delivery_mode_default_and_fallback(tmp_path):
    """Test delivery_mode default value, multi_window parsing, invalid fallback, and legacy web_tab_mode fallback."""
    from kardenwort_desk import SentencesModeConfig, SEC_SENTENCES_MODE
    import configparser

    # Default
    cp = configparser.ConfigParser()
    cp.add_section(SEC_SENTENCES_MODE)
    smc = SentencesModeConfig.from_config(cp)
    assert smc.delivery_mode == "container"
    assert smc.web_tab_mode == "container"

    # Direct delivery_mode = multi_window
    cp.set(SEC_SENTENCES_MODE, "delivery_mode", "multi_window")
    smc_mw = SentencesModeConfig.from_config(cp)
    assert smc_mw.delivery_mode == "multi_window"
    assert smc_mw.web_tab_mode == "tabs"

    # Invalid delivery_mode falls back to container
    cp.set(SEC_SENTENCES_MODE, "delivery_mode", "invalid_mode")
    smc_invalid = SentencesModeConfig.from_config(cp)
    assert smc_invalid.delivery_mode == "container"
    assert smc_invalid.web_tab_mode == "container"

    # Legacy fallback: delivery_mode not present, web_tab_mode = tabs -> delivery_mode = multi_window
    cp.remove_option(SEC_SENTENCES_MODE, "delivery_mode")
    cp.set(SEC_SENTENCES_MODE, "web_tab_mode", "tabs")
    smc_legacy_tabs = SentencesModeConfig.from_config(cp)
    assert smc_legacy_tabs.delivery_mode == "multi_window"
    assert smc_legacy_tabs.web_tab_mode == "tabs"

    # Legacy fallback: delivery_mode not present, web_tab_mode = container -> delivery_mode = container
    cp.set(SEC_SENTENCES_MODE, "web_tab_mode", "container")
    smc_legacy_cont = SentencesModeConfig.from_config(cp)
    assert smc_legacy_cont.delivery_mode == "container"
    assert smc_legacy_cont.web_tab_mode == "container"


def test_sentences_mode_tab_bar_position_default_and_fallback():
    """Test tab_bar_position default value, valid options, and invalid fallback."""
    from kardenwort_desk import SentencesModeConfig, SEC_SENTENCES_MODE
    import configparser

    # Default
    cp = configparser.ConfigParser()
    cp.add_section(SEC_SENTENCES_MODE)
    smc = SentencesModeConfig.from_config(cp)
    assert smc.tab_bar_position == "top"

    # None config
    empty_smc = SentencesModeConfig.from_config(None)
    assert empty_smc.tab_bar_position == "top"

    # Explicit top
    cp.set(SEC_SENTENCES_MODE, "tab_bar_position", "top")
    smc_top = SentencesModeConfig.from_config(cp)
    assert smc_top.tab_bar_position == "top"

    # Explicit bottom
    cp.set(SEC_SENTENCES_MODE, "tab_bar_position", "bottom")
    smc_bottom = SentencesModeConfig.from_config(cp)
    assert smc_bottom.tab_bar_position == "bottom"

    # Explicit inline
    cp.set(SEC_SENTENCES_MODE, "tab_bar_position", "inline")
    smc_inline = SentencesModeConfig.from_config(cp)
    assert smc_inline.tab_bar_position == "inline"

    # Invalid fallback to top
    cp.set(SEC_SENTENCES_MODE, "tab_bar_position", "invalid_position")
    smc_invalid = SentencesModeConfig.from_config(cp)
    assert smc_invalid.tab_bar_position == "top"



