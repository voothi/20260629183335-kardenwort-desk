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
        config, resolved_paths, gd = kardenwort_desk.load_config(config_file)
        
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
        
        config, resolved_paths, gd = kardenwort_desk.load_config(config_file)
        
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
        
        config, resolved_paths, gd = kardenwort_desk.load_config(config_file)
        
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
        
        config, resolved_paths, gd = kardenwort_desk.load_config(config_file)
        
        assert config.get('pipeline', 'base_provider') == 'deepl'
        assert config.get('pipeline', 'enrichment_provider') == 'none'
        assert config.get('triggers', 'run_base_translation') == 'manual'
        assert config.get('triggers', 'run_enrichment') == 'auto'
        assert config.get('rendering', 'display_mode') == 'monolithic'

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

[pipeline]
base_provider = deepl
enrichment_provider = combined
"""
        config_file = desk_dir / "config.ini"
        config_file.write_text(config_content)
        
        config, resolved_paths, gd = kardenwort_desk.load_config(config_file)
        
        # Mapped triggers: lazy_processing = llm_only -> base = auto, enrichment = manual
        assert config.get('triggers', 'run_base_translation') == 'auto'
        assert config.get('triggers', 'run_enrichment') == 'manual'
        
        # Mapped rendering: progressive_loading = true -> display_mode = progressive
        assert config.get('rendering', 'display_mode') == 'progressive'
        
        assert config.get('pipeline', 'base_provider') == 'deepl'
        assert config.get('pipeline', 'enrichment_provider') == 'combined'

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
        
        config, resolved_paths, gd = kardenwort_desk.load_config(config_file)
        
        assert config.get('pipeline', 'base_provider') == 'deepl'
        assert config.get('pipeline', 'enrichment_provider') == 'intellifiller'

def test_backward_compatibility_rendering():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        desk_dir = tmp_path / "kardenwort-desk"
        desk_dir.mkdir()
        anki_mapping = desk_dir / "anki-mapping.ini"
        anki_mapping.write_text("")
        
        config_content = """[settings]
anki_mapping_file = ./anki-mapping.ini
default_target_language = ru
lazy_processing = false
progressive_loading = false

[pipeline]
base_provider = google
enrichment_provider = combined
"""
        config_file = desk_dir / "config.ini"
        config_file.write_text(config_content)
        
        config, resolved_paths, gd = kardenwort_desk.load_config(config_file)
        
        assert config.get('rendering', 'display_mode') == 'monolithic'
        assert config.get('triggers', 'run_base_translation') == 'auto'
        assert config.get('triggers', 'run_enrichment') == 'auto'
        assert config.get('pipeline', 'base_provider') == 'google'
        assert config.get('pipeline', 'enrichment_provider') == 'combined'
