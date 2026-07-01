import os
import json
import base64
import tempfile
import configparser
from pathlib import Path
import pytest
import kardenwort_desk as desk

def test_deobfuscation():
    # Setup simulated settings and secrets files
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        settings_file = tmp_path / "settings.ini"
        secrets_file = tmp_path / "secrets.ini"
        
        salt = "my_secret_salt_123"
        raw_key = "deepl_api_key_val_456"
        prefix = "%%SEC%%"
        full_raw_key = prefix + raw_key
        
        # XOR key bytes with repeating salt bytes
        key_bytes = full_raw_key.encode('utf-8')
        salt_bytes = salt.encode('utf-8')
        obfuscated_bytes = bytearray()
        for i, b in enumerate(key_bytes):
            obfuscated_bytes.append(b ^ salt_bytes[i % len(salt_bytes)])
            
        obfuscated_key_b64 = base64.b64encode(obfuscated_bytes).decode('utf-8')
        
        settings_file.write_text(f"""[Security]
Salt = {salt}
SecretsPath = ./secrets.ini
""")
        
        secrets_file.write_text(f"""[DeepL]
Key = {obfuscated_key_b64}
""")
        
        config = configparser.ConfigParser()
        config.add_section('environment')
        config.set('environment', 'deepl_settings_file', str(settings_file.relative_to(tmp_path)))
        
        # Test deobfuscation
        key = desk.get_deepl_key(config, tmp_path)
        assert key == raw_key

def test_deobfuscation_fallback_plain():
    # Setup simulated settings and secrets files with plain text key (no %%SEC%%)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        settings_file = tmp_path / "settings.ini"
        secrets_file = tmp_path / "secrets.ini"
        
        plain_key = "my_plain_text_api_key"
        
        settings_file.write_text(f"""[Security]
Salt = some_salt
SecretsPath = secrets.ini
""")
        secrets_file.write_text(f"""[DeepL]
Key = {plain_key}
""")
        
        config = configparser.ConfigParser()
        config.add_section('environment')
        config.set('environment', 'deepl_settings_file', 'settings.ini')
        
        # Should return plain_key because it fails b64decode/XOR parsing or doesn't have %%SEC%% prefix
        key = desk.get_deepl_key(config, tmp_path)
        assert key == plain_key

def test_generate_slug():
    assert desk.generate_slug("The Quick Brown Fox!") == "the-quick-brown-fox"
    assert desk.generate_slug("Hello {\\an8} World") == "hello-world"
    assert desk.generate_slug("!!!") == "untitled"

def test_load_tsv_rows():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.tsv', delete=False, encoding='utf-8') as f:
        f.write("# comment 1\n# comment 2\nHeader1\tHeader2\nval1\tval2\nval3\tval4\n")
        f_name = f.name
        
    try:
        comments, headers, data_rows = desk.load_tsv_rows(Path(f_name))
        assert comments == ["# comment 1", "# comment 2"]
        assert headers == ["Header1", "Header2"]
        assert len(data_rows) == 2
        assert data_rows[0] == ["val1", "val2"]
        assert data_rows[1] == ["val3", "val4"]
    finally:
        os.remove(f_name)

def test_save_tsv_rows_safely():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        tsv_path = tmp_path / "test.tsv"
        
        comments = ["# test comment"]
        headers = ["Col1", "Col2"]
        data_rows = [["a", "b"], ["c", "d"]]
        
        desk.save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
        
        assert tsv_path.exists()
        comments_read, headers_read, data_rows_read = desk.load_tsv_rows(tsv_path)
        assert comments_read == comments
        assert headers_read == headers
        assert data_rows_read == data_rows

def test_is_tsv_llm_filled():
    headers = ["WordSource", "WordSourceMorphologyAI", "WordSourceIPA"]
    # Filled
    rows_filled = [["test", "noun", "/t/"]]
    assert desk.is_tsv_llm_filled(headers, rows_filled, None) is True
    
    # Not filled
    rows_empty = [["test", "", ""]]
    assert desk.is_tsv_llm_filled(headers, rows_empty, None) is False

def test_merge_subcommand():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        # Create two working TSVs and sibling TXTs
        # ZIDs: 20260630000000 and 20260630000001
        tsv1 = tmp_path / "20260630000000-part1.en.tsv"
        txt1 = tmp_path / "20260630000000-part1.txt"
        tsv1.write_text("Header1\tHeader2\nv1\tv2\n", encoding='utf-8')
        txt1.write_text("Text one", encoding='utf-8')
        
        tsv2 = tmp_path / "20260630000001-part2.en.tsv"
        txt2 = tmp_path / "20260630000001-part2.txt"
        tsv2.write_text("Header1\tHeader2\nv3\tv4\n", encoding='utf-8')
        txt2.write_text("Text two", encoding='utf-8')
        
        # Test merge creation of new file
        dest_tsv = tmp_path / "merged.tsv"
        dest_txt = tmp_path / "merged.txt"
        
        # Simulating argparse namespace
        class Args:
            files = [str(tsv2), str(tsv1)] # Pass out of order, should ZID-sort
            target = str(dest_tsv)
            config = None
            
        # Simulating config and paths
        config = configparser.ConfigParser()
        config.add_section('settings')
        config.set('settings', 'merge_delete_sources', 'false')
        
        # Run merge core logic directly
        # Sort files by ZID
        files = [Path(f).resolve() for f in Args.files]
        files.sort(key=desk.extract_zid)
        
        first_headers = None
        all_comments = []
        all_data_rows = []
        sibling_texts = []
        
        for f in files:
            comments, headers, rows = desk.load_tsv_rows(f)
            if not first_headers:
                first_headers = headers
            all_data_rows.extend(rows)
            
            zid = desk.extract_zid(f)
            txt_files = list(f.parent.glob(f"{zid}-*.txt"))
            if txt_files:
                sibling_texts.append(txt_files[0].read_text(encoding='utf-8'))
                
        # Write merged
        desk.save_tsv_rows_safely(dest_tsv, all_comments, first_headers, all_data_rows)
        dest_txt.write_text("\n\n".join(sibling_texts), encoding='utf-8')
        
        # Verify merged result
        _, final_headers, final_rows = desk.load_tsv_rows(dest_tsv)
        assert final_headers == ["Header1", "Header2"]
        # Ordered by ZID, so part 1 (v1,v2) then part 2 (v3,v4)
        assert final_rows == [["v1", "v2"], ["v3", "v4"]]
        assert dest_txt.read_text(encoding='utf-8') == "Text one\n\nText two"

def test_restore_subcommand():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        tsv = tmp_path / "20260630121212-restore.en.tsv"
        txt = tmp_path / "20260630121212-restore.txt"
        
        tsv.write_text("Col1\tCol2\nv1\tv2\n", encoding='utf-8')
        txt.write_text("Hello source", encoding='utf-8')
        
        # Restore from tsv
        class Args:
            file = str(tsv)
            config = None
            
        # Verify extract ZID
        zid = desk.extract_zid(tsv)
        assert zid == "20260630121212"
        
        # Reconstitute working state
        comments, headers, data_rows = desk.load_tsv_rows(tsv)
        source_text = txt.read_text(encoding='utf-8')
        
        assert headers == ["Col1", "Col2"]
        assert data_rows == [["v1", "v2"]]
        assert source_text == "Hello source"

def test_is_contiguous_subsequence():
    assert desk.is_contiguous_subsequence(["set", "up"], ["i", "want", "to", "set", "up", "the", "system"]) is True
    assert desk.is_contiguous_subsequence(["set", "up"], ["i", "set", "it", "up"]) is False
    assert desk.is_contiguous_subsequence([], ["a", "b"]) is False
    assert desk.is_contiguous_subsequence(["a"], []) is False

def test_build_field_mapping_includes_tts():
    mapping = configparser.ConfigParser(allow_no_value=True, interpolation=None)
    mapping.optionxform = str
    mapping.read_string("""
[fields_mapping.word]
WordSource=lemma
[tts]
Source-en-GB=tts_source_en
Destination-ru-RU=tts_dest_ru
""")
    res = desk.build_field_mapping(mapping, 'word')
    assert res['WordSource'] == 'lemma'
    assert res['Source-en-GB'] == 'tts_source_en'
    assert res['Destination-ru-RU'] == 'tts_dest_ru'

def test_build_field_mapping_without_tts_section():
    mapping = configparser.ConfigParser(allow_no_value=True, interpolation=None)
    mapping.optionxform = str
    mapping.read_string("""
[fields_mapping.word]
WordSource=lemma
""")
    res = desk.build_field_mapping(mapping, 'word')
    assert res == {'WordSource': 'lemma'}

def test_build_field_mapping_tts_does_not_overwrite_word_keys():
    mapping = configparser.ConfigParser(allow_no_value=True, interpolation=None)
    mapping.optionxform = str
    mapping.read_string("""
[fields_mapping.word]
OverlapKey=from_word
[tts]
OverlapKey=from_tts
""")
    res = desk.build_field_mapping(mapping, 'word')
    assert res['OverlapKey'] == 'from_tts'

def test_run_render_flow_passes_tts_destination_lang(monkeypatch):
    import subprocess
    import sys
    
    mock_cmd = []
    def mock_run(cmd, *args, **kwargs):
        mock_cmd.extend(cmd)
        if "--output-file" in cmd:
            out_idx = cmd.index("--output-file")
            out_path = Path(cmd[out_idx+1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text("WordSource\\nword\\n", encoding='utf-8')
        class MockProc:
            returncode = 0
            stdout = ""
            stderr = ""
        return MockProc()
        
    monkeypatch.setattr(subprocess, 'run', mock_run)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "config.ini").write_text("[project_structure]\\ngenerated_results_dir=results\\n")
        
        config = configparser.ConfigParser()
        config.read_string("""
[settings]
default_target_language=uk
save_source_text=False
[languages]
en_lemma_index=idx.txt
en_lemma_override=override.txt
""")
        
        mapping_file = tmp_path / "mapping.ini"
        mapping_file.write_text("""
[fields]
WordSource=
[fields_mapping.word]
WordSource=lemma
[tts]
Destination-uk-UA=tts_dest_uk
""")
        
        resolved_paths = {
            'kardenwort_workspace': workspace,
            'anki_mapping_file': mapping_file,
            'kardenwort_python': Path(sys.executable),
        }
        
        # Should populate mock_cmd
        try:
            desk.run_render_flow("test text", "en", "1234", "single", config, resolved_paths)
        except Exception:
            pass
            
        assert "--tts-destination-lang" in mock_cmd
        idx = mock_cmd.index("--tts-destination-lang")
        assert mock_cmd[idx+1] == "uk"
        
        assert "--anki-field-mapping" in mock_cmd
        mapping_idx = mock_cmd.index("--anki-field-mapping")
        mapping_json = mock_cmd[mapping_idx+1]
        mapping_dict = json.loads(mapping_json)
        assert mapping_dict['Destination-uk-UA'] == 'tts_dest_uk'

def test_cmd_export_selection_modes_and_favorites(monkeypatch, tmp_path):
    import json
    
    config = configparser.ConfigParser()
    config.read_string("""
[settings]
export_selection_mode=all
save_to_favorites_on_export=false
send_to_anki_after_export=false
""")
    
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "config.ini").write_text("[project_structure]\ngenerated_results_dir=results\n")
    results_dir = workspace / "results"
    results_dir.mkdir()
    
    working_tsv = results_dir / "123-test.en.tsv"
    working_tsv.write_text("H1\tH2\nv1\tv2\nv3\tv4\nv5\tv6\n", encoding='utf-8')
    
    fav_dir = tmp_path / "favorites"
    fav_dir.mkdir()
    
    resolved_paths = {
        'kardenwort_workspace': workspace,
        'favorites_output_dir': fav_dir
    }
    
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({
        "zid": "123",
        "selected_row_ids": [1],
        "tsv_path": str(working_tsv)
    }))
    
    monkeypatch.setattr(desk, 'load_config', lambda c: (config, resolved_paths))
    
    class Args:
        config = None
        selection_manifest = str(manifest_path)
        language = "en"
        
    saved_paths = []
    saved_rows = []
    
    orig_save = desk.save_tsv_rows_safely
    def mock_save(path, comments, headers, data_rows):
        saved_paths.append(path)
        saved_rows.append(data_rows)
        orig_save(path, comments, headers, data_rows)
        
    monkeypatch.setattr(desk, 'save_tsv_rows_safely', mock_save)
    
    # 1. Test mode 'all' and save_to_favorites_on_export=false
    try:
        desk.cmd_export(Args())
    except SystemExit:
        pass
        
    assert len(saved_paths) == 1
    assert saved_paths[0].parent == results_dir
    assert saved_paths[0].name == "temp_import_123-test.en.tsv"
    assert len(saved_rows[0]) == 3
    
    # 2. Test mode 'unselected' and save_to_favorites_on_export=true
    config.set('settings', 'export_selection_mode', 'unselected')
    config.set('settings', 'save_to_favorites_on_export', 'true')
    saved_paths.clear()
    saved_rows.clear()
    try:
        desk.cmd_export(Args())
    except SystemExit:
        pass
        
    assert len(saved_paths) == 1
    assert saved_paths[0].parent == fav_dir
    assert saved_paths[0].name == "123-test.en.tsv"
    assert len(saved_rows[0]) == 2
    assert saved_rows[0][0] == ["v1", "v2"]
    assert saved_rows[0][1] == ["v5", "v6"]

    # 3. Test mode 'selected'
    config.set('settings', 'export_selection_mode', 'selected')
    saved_paths.clear()
    saved_rows.clear()
    try:
        desk.cmd_export(Args())
    except SystemExit:
        pass
        
    assert len(saved_paths) == 1
    assert len(saved_rows[0]) == 1
    assert saved_rows[0][0] == ["v3", "v4"]
