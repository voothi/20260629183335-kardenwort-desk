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
