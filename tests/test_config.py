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
        config, resolved_paths = kardenwort_desk.load_config(config_file)
        
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
