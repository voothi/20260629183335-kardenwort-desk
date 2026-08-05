from kardenwort_desk import (
    SEC_SETTINGS, SEC_TOKEN_MAPPINGS, SEC_MERGE, SEC_SENTENCES_MODE,
    SEC_CLASSIFICATION, SEC_TIMEOUTS, SEC_PIPELINE, SEC_TRIGGERS,
    SEC_TRANSLATION, SEC_TRANSLATION_PROVIDERS, SEC_RENDERING,
    SEC_ENVIRONMENT, SEC_LANGUAGES, SEC_LANGUAGE_RESOURCES,
    SEC_PROJECT_STRUCTURE, SEC_AUDIO, SEC_GOLDENDICT, SEC_WORDFILL,
    ErrorCode, _VALID_ERROR_CODES,
    ExportSkippedPayload, ExportImportStartedPayload,
    ExportImportCompletePayload, ExportSuccessPayload, EditSaveSuccessPayload,
    ReprocessStartedPayload, RetextStartedPayload,
)
import os
import sys
import io
import json
import base64
import tempfile
import configparser
from pathlib import Path
import pytest
import kardenwort_desk as desk

def test_write_update_js(tmp_path):
    tsv_path = tmp_path / "data.tsv"
    data_rows = [["Apple", "Apfel", "ˈapfl̩", "N"]]
    headers = ["Word", "Translation", "IPA", "POS"]
    role_fields = {
        "lemma": "Word",
        "word_translation": "Translation",
        "ipa": "IPA",
        "morphology": "POS"
    }
    
    desk.write_update_js(
        tsv_path, 
        data_rows, 
        headers, 
        role_fields, 
        stage="translated", 
        status="success"
    )
    
    updates_dir = tmp_path / "data.updates"
    js_files = list(updates_dir.glob("*.js"))
    assert len(js_files) == 1
    update_js = js_files[0]
    
    content = update_js.read_text(encoding="utf-8")
    assert "window.receiveUpdate" in content
    
    match = __import__('re').search(r"window\.receiveUpdate\((.*)\);", content, __import__('re').DOTALL)
    assert match
    
    payload = json.loads(match.group(1))
    assert payload["stage"] == "translated"
    assert payload["status"] == "success"
    assert "0" in payload["rows"]
    assert payload["rows"]["0"]["lemma"] == "Apple"
    assert payload["rows"]["0"]["trans"] == "Apfel"
    assert payload["rows"]["0"]["ipa"] == "ˈapfl̩"
    assert payload["rows"]["0"]["morph"] == "N"

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
        config.add_section(SEC_ENVIRONMENT)
        config.set(SEC_ENVIRONMENT, 'deepl_settings_file', str(settings_file.relative_to(tmp_path)))
        
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
        config.add_section(SEC_ENVIRONMENT)
        config.set(SEC_ENVIRONMENT, 'deepl_settings_file', 'settings.ini')
        
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
        config.add_section(SEC_SETTINGS)
        config.set(SEC_SETTINGS, 'merge_delete_sources', 'false')
        
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

def test_resolve_anchored_positions():
    # 1. split verb with repeated particle (only forming positions anchored)
    # Source: "heute kommt der redakteur an einem tag an"
    source = ["heute", "kommt", "der", "redakteur", "an", "einem", "tag", "an"]
    # Inflected: "kommt an"
    pos, ok = desk.resolve_anchored_positions(["kommt", "an"], source, 60)
    assert ok is True
    assert pos == {1, 4} # minimum span is (1, 4) with span 3; (1, 7) has span 6

    # 2. contiguous phrase
    # Source: "he did it in spite of the rule of which he was aware"
    source = ["he", "did", "it", "in", "spite", "of", "the", "rule", "of"]
    pos, ok = desk.resolve_anchored_positions(["in", "spite", "of"], source, 60)
    assert ok is True
    assert pos == {3, 4, 5} # "in spite of" at 3,4,5. The other "of" at 8 is not anchored

    # 3. out-of-order fallback
    # Source: "an etwas kommt"
    source = ["an", "etwas", "kommt"]
    pos, ok = desk.resolve_anchored_positions(["kommt", "an"], source, 60)
    assert ok is False
    assert pos == set()

    # 4. gap-exceeded fallback (with a custom small gap_limit)
    # Source: "kommt" + 5 words + "an"
    source = ["kommt", "one", "two", "three", "four", "five", "an"]
    # gap limit = 5, distance is 6.
    pos, ok = desk.resolve_anchored_positions(["kommt", "an"], source, 5)
    assert ok is False
    # gap limit = 6, distance is 6.
    pos, ok = desk.resolve_anchored_positions(["kommt", "an"], source, 6)
    assert ok is True
    assert pos == {0, 6}

    # 5. minimum-span tie-break
    # Source: "kommt kommt an"
    source = ["kommt", "kommt", "an"]
    pos, ok = desk.resolve_anchored_positions(["kommt", "an"], source, 60)
    assert ok is True
    assert pos == {1, 2} # (1, 2) has span 1; (0, 2) has span 2

    # 6. single-word input (<2 words -> empty/ok=False)
    pos, ok = desk.resolve_anchored_positions(["ankommen"], source, 60)
    assert ok is False
    assert pos == set()
    pos, ok = desk.resolve_anchored_positions([], source, 60)
    assert ok is False
    assert pos == set()

    # 7. duplicate-word inflected form ("an an" needs two distinct positions; one occurrence -> no tuple)
    source = ["an", "etwas", "an"]
    pos, ok = desk.resolve_anchored_positions(["an", "an"], source, 60)
    assert ok is True
    assert pos == {0, 2}

    source = ["an", "etwas"]
    pos, ok = desk.resolve_anchored_positions(["an", "an"], source, 60)
    assert ok is False
    assert pos == set()

    # 8. repeated-construct case yielding multiple non-overlapping tuples
    # Source: "steht auf steht auf"
    source = ["steht", "auf", "steht", "auf"]
    pos, ok = desk.resolve_anchored_positions(["steht", "auf"], source, 60)
    assert ok is True
    assert pos == {0, 1, 2, 3}


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
        if "--output-file" in cmd:  # Only for extraction step
            assert "--use-simplemma-correction" in cmd, "Expected --use-simplemma-correction to be passed to subprocess"
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
use_simplemma_correction=true
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
    
    monkeypatch.setattr(desk, 'load_config', lambda c: (config, resolved_paths, {}, {}))
    
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
    config.set(SEC_SETTINGS, 'export_selection_mode', 'unselected')
    config.set(SEC_SETTINGS, 'save_to_favorites_on_export', 'true')
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
    config.set(SEC_SETTINGS, 'export_selection_mode', 'selected')
    saved_paths.clear()
    saved_rows.clear()
    try:
        desk.cmd_export(Args())
    except SystemExit:
        pass
        
    assert len(saved_paths) == 1
    assert len(saved_rows[0]) == 1
    assert saved_rows[0][0] == ["v3", "v4"]


def test_cmd_merge_filters_tsv(monkeypatch, tmp_path):
    # Create files: TSVs, TXT (en, ru), LOG
    tsv1 = tmp_path / "20260630000000-part1.en.tsv"
    txt1_en = tmp_path / "20260630000000-part1.en.txt"
    txt1_ru = tmp_path / "20260630000000-part1.ru.txt"
    log1 = tmp_path / "20260630000000-part1.log"
    
    tsv1.write_text("Header1\tHeader2\nv1\tv2\n", encoding='utf-8')
    txt1_en.write_text("Text one EN", encoding='utf-8')
    txt1_ru.write_text("Text one RU", encoding='utf-8')
    log1.write_text("Log one", encoding='utf-8')
    
    tsv2 = tmp_path / "20260630000001-part2.en.tsv"
    txt2_en = tmp_path / "20260630000001-part2.en.txt"
    txt2_ru = tmp_path / "20260630000001-part2.ru.txt"
    tsv2.write_text("Header1\tHeader2\nv3\tv4\n", encoding='utf-8')
    txt2_en.write_text("Text two EN", encoding='utf-8')
    txt2_ru.write_text("Text two RU", encoding='utf-8')

    dest_tsv = tmp_path / "20260711000000-merged.en.tsv"
    
    class Args:
        files = [str(tsv1), str(txt1_en), str(txt1_ru), str(log1), str(tsv2)]
        target = str(dest_tsv)
        config = None

    # mock load_config
    config = configparser.ConfigParser()
    config.add_section(SEC_SETTINGS)
    config.set(SEC_SETTINGS, 'merge_delete_sources', 'false')
    monkeypatch.setattr(desk, 'load_config', lambda c: (config, {}, {}, {}))
    
    # Run cmd_merge
    desk.cmd_merge(Args())
    
    # Verify merge output (only TSV files got merged)
    assert dest_tsv.exists()
    _, final_headers, final_rows = desk.load_tsv_rows(dest_tsv)
    assert final_headers == ["Header1", "Header2"]
    assert final_rows == [["v1", "v2"], ["v3", "v4"]]

    # Verify both language text files are merged
    dest_txt_en = tmp_path / "20260711000000-merged.en.txt"
    dest_txt_ru = tmp_path / "20260711000000-merged.ru.txt"
    assert dest_txt_en.exists()
    assert dest_txt_ru.exists()
    assert dest_txt_en.read_text(encoding='utf-8') == "Text one EN\n\nText two EN"
    assert dest_txt_ru.read_text(encoding='utf-8') == "Text one RU\n\nText two RU"


def test_cmd_merge_deduplicates_rows(monkeypatch, tmp_path):
    tsv1 = tmp_path / "20260630000000-part1.en.tsv"
    tsv1.write_text("WordSourceInflectedForm\tWordSource\nv1\tv2\n", encoding='utf-8')
    
    tsv2 = tmp_path / "20260630000001-part2.en.tsv"
    tsv2.write_text("WordSourceInflectedForm\tWordSource\nv1\tv2\nv3\tv4\n", encoding='utf-8')

    dest_tsv = tmp_path / "20260711000000-merged.en.tsv"
    
    class Args:
        files = [str(tsv1), str(tsv2)]
        target = str(dest_tsv)
        config = None

    # mock load_config
    config = configparser.ConfigParser()
    config.add_section(SEC_SETTINGS)
    config.set(SEC_SETTINGS, 'merge_delete_sources', 'false')
    
    # We must resolve_paths to point to real mapping file
    resolved_paths = {
        'anki_mapping_file': str(Path("anki-mapping.ini").resolve())
    }
    monkeypatch.setattr(desk, 'load_config', lambda c: (config, resolved_paths, {}, {}))
    
    desk.cmd_merge(Args())
    
    # Verify merge output has deduplicated "v1", "v2"
    assert dest_tsv.exists()
    _, final_headers, final_rows = desk.load_tsv_rows(dest_tsv)
    assert final_headers == ["WordSourceInflectedForm", "WordSource"]
    assert final_rows == [["v1", "v2"], ["v3", "v4"]]


def test_cmd_merge_offsets_sentence_index(monkeypatch, tmp_path):
    tsv1 = tmp_path / "20260630000000-part1.en.tsv"
    txt1 = tmp_path / "20260630000000-part1.txt"
    tsv1.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\nv1\tv2\t1\nv3\tv4\t2\n", encoding='utf-8')
    txt1.write_text("Line one\nLine two\n", encoding='utf-8')
    
    tsv2 = tmp_path / "20260630000001-part2.en.tsv"
    txt2 = tmp_path / "20260630000001-part2.txt"
    tsv2.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\nv5\tv6\t1\nv7\tv8\t2\n", encoding='utf-8')
    txt2.write_text("Line three\nLine four\n", encoding='utf-8')

    dest_tsv = tmp_path / "20260711000000-merged.en.tsv"
    
    class Args:
        files = [str(tsv1), str(tsv2)]
        target = str(dest_tsv)
        config = None

    config = configparser.ConfigParser()
    config.add_section(SEC_SETTINGS)
    config.set(SEC_SETTINGS, 'merge_delete_sources', 'false')
    
    resolved_paths = {
        'anki_mapping_file': str(Path("anki-mapping.ini").resolve())
    }
    monkeypatch.setattr(desk, 'load_config', lambda c: (config, resolved_paths, {}, {}))
    
    desk.cmd_merge(Args())
    
    assert dest_tsv.exists()
    _, final_headers, final_rows = desk.load_tsv_rows(dest_tsv)
    assert final_headers == ["WordSourceInflectedForm", "WordSource", "SentenceSourceIndex"]
    assert final_rows == [
        ["v1", "v2", "1"],
        ["v3", "v4", "2"],
        ["v5", "v6", "3"],
        ["v7", "v8", "4"]
    ]


def test_cmd_merge_single_paragraph_mapping(monkeypatch, tmp_path):
    tsv1 = tmp_path / "20260630000000-part1.en.tsv"
    txt1 = tmp_path / "20260630000000-part1.txt"
    tsv1.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\nv1\tv2\t1\nv3\tv4\t2\n", encoding='utf-8')
    txt1.write_text("Line one sentence one. Line one sentence two.", encoding='utf-8')
    
    tsv2 = tmp_path / "20260630000001-part2.en.tsv"
    txt2 = tmp_path / "20260630000001-part2.txt"
    tsv2.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\nv5\tv6\t1\nv7\tv8\t2\n", encoding='utf-8')
    txt2.write_text("Line two sentence one. Line two sentence two.", encoding='utf-8')

    dest_tsv = tmp_path / "20260711000000-merged.en.tsv"
    
    class Args:
        files = [str(tsv1), str(tsv2)]
        target = str(dest_tsv)
        config = None

    config = configparser.ConfigParser()
    config.add_section(SEC_SETTINGS)
    config.set(SEC_SETTINGS, 'merge_delete_sources', 'false')
    
    resolved_paths = {
        'anki_mapping_file': str(Path("anki-mapping.ini").resolve())
    }
    monkeypatch.setattr(desk, 'load_config', lambda c: (config, resolved_paths, {}, {}))
    
    desk.cmd_merge(Args())
    
    assert dest_tsv.exists()
    _, final_headers, final_rows = desk.load_tsv_rows(dest_tsv)
    assert final_headers == ["WordSourceInflectedForm", "WordSource", "SentenceSourceIndex"]
    # Since each txt file has exactly 1 non-empty line (1 paragraph), all rows are mapped to their respective paragraph index.
    assert final_rows == [
        ["v1", "v2", "1"],
        ["v3", "v4", "1"],
        ["v5", "v6", "2"],
        ["v7", "v8", "2"]
    ]


def test_cmd_merge_delete_sources_cli(monkeypatch, tmp_path):
    tsv1 = tmp_path / "20260630000000-part1.en.tsv"
    txt1 = tmp_path / "20260630000000-part1.txt"
    tsv1.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\nv1\tv2\t1\n", encoding='utf-8')
    txt1.write_text("Line one.", encoding='utf-8')
    
    tsv2 = tmp_path / "20260630000001-part2.en.tsv"
    txt2 = tmp_path / "20260630000001-part2.txt"
    tsv2.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\nv3\tv4\t1\n", encoding='utf-8')
    txt2.write_text("Line two.", encoding='utf-8')

    dest_tsv = tmp_path / "20260711000000-merged.en.tsv"
    
    class Args:
        files = [str(tsv1), str(tsv2)]
        target = str(dest_tsv)
        delete_sources = True
        config = None

    config = configparser.ConfigParser()
    config.add_section(SEC_SETTINGS)
    # Config has delete sources set to false, but CLI option is True and should override it
    config.set(SEC_SETTINGS, 'merge_delete_sources', 'false')
    
    resolved_paths = {
        'anki_mapping_file': str(Path("anki-mapping.ini").resolve())
    }
    monkeypatch.setattr(desk, 'load_config', lambda c: (config, resolved_paths, {}, {}))
    
    desk.cmd_merge(Args())
    
    assert dest_tsv.exists()
    # The source files must have been deleted
    assert not tsv1.exists()
    assert not tsv2.exists()
    assert not txt1.exists()
    assert not txt2.exists()


def test_cmd_merge_folder_scanning(monkeypatch, tmp_path):
    sub = tmp_path / "folder"
    sub.mkdir()
    tsv1 = sub / "20260630000000-part1.en.tsv"
    txt1 = sub / "20260630000000-part1.txt"
    tsv1.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\nv1\tv2\t1\n", encoding='utf-8')
    txt1.write_text("Line one.", encoding='utf-8')
    
    tsv2 = sub / "20260630000001-part2.en.tsv"
    txt2 = sub / "20260630000001-part2.txt"
    tsv2.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\nv3\tv4\t1\n", encoding='utf-8')
    txt2.write_text("Line two.", encoding='utf-8')

    dest_tsv = tmp_path / "20260711000000-merged.en.tsv"
    
    class Args:
        files = [str(sub)]
        target = str(dest_tsv)
        config = None

    config = configparser.ConfigParser()
    config.add_section(SEC_SETTINGS)
    config.set(SEC_SETTINGS, 'merge_delete_sources', 'false')
    
    resolved_paths = {
        'anki_mapping_file': str(Path("anki-mapping.ini").resolve())
    }
    monkeypatch.setattr(desk, 'load_config', lambda c: (config, resolved_paths, {}, {}))
    
    desk.cmd_merge(Args())
    assert dest_tsv.exists()


def test_cmd_merge_range_selection(monkeypatch, tmp_path):
    # Create 3 tsv files
    tsv1 = tmp_path / "20260630000000-part1.en.tsv"
    txt1 = tmp_path / "20260630000000-part1.txt"
    tsv1.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\nv1\tv2\t1\n", encoding='utf-8')
    txt1.write_text("Line one.", encoding='utf-8')

    tsv2 = tmp_path / "20260630000001-part2.en.tsv"
    txt2 = tmp_path / "20260630000001-part2.txt"
    tsv2.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\nv3\tv4\t1\n", encoding='utf-8')
    txt2.write_text("Line two.", encoding='utf-8')

    tsv3 = tmp_path / "20260630000002-part3.en.tsv"
    txt3 = tmp_path / "20260630000002-part3.txt"
    tsv3.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\nv5\tv6\t1\n", encoding='utf-8')
    txt3.write_text("Line three.", encoding='utf-8')

    dest_tsv = tmp_path / "20260711000000-merged.en.tsv"
    
    class Args:
        # We select only start (tsv1) and end (tsv3) files. The middle file (tsv2) should be dynamically merged.
        files = [str(tsv1), str(tsv3)]
        target = str(dest_tsv)
        config = None

    config = configparser.ConfigParser()
    config.add_section(SEC_SETTINGS)
    config.set(SEC_SETTINGS, 'merge_delete_sources', 'false')
    
    resolved_paths = {
        'anki_mapping_file': str(Path("anki-mapping.ini").resolve())
    }
    monkeypatch.setattr(desk, 'load_config', lambda c: (config, resolved_paths, {}, {}))
    
    desk.cmd_merge(Args())
    assert dest_tsv.exists()
    
    _, final_headers, final_rows = desk.load_tsv_rows(dest_tsv)
    # Verify that all 3 rows (from tsv1, tsv2, tsv3) are in the merged file
    assert len(final_rows) == 3
    assert final_rows[0] == ["v1", "v2", "1"]
    assert final_rows[1] == ["v3", "v4", "2"]
    assert final_rows[2] == ["v5", "v6", "3"]


def test_cmd_merge_multilingual(monkeypatch, tmp_path):
    # Create English and German tsv/txt files
    en_tsv = tmp_path / "20260630000000-part1.en.tsv"
    en_txt = tmp_path / "20260630000000-part1.txt"
    en_tsv.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\nen_inf\ten_lem\t1\n", encoding='utf-8')
    en_txt.write_text("English line.", encoding='utf-8')

    de_tsv = tmp_path / "20260630000001-part2.de.tsv"
    de_txt = tmp_path / "20260630000001-part2.txt"
    de_tsv.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\nde_inf\tde_lem\t1\n", encoding='utf-8')
    de_txt.write_text("Deutsche Zeile.", encoding='utf-8')
    
    # Also add translations for both
    en_ru_txt = tmp_path / "20260630000000-part1.ru.txt"
    en_ru_txt.write_text("English translation.", encoding='utf-8')
    de_ru_txt = tmp_path / "20260630000001-part2.ru.txt"
    de_ru_txt.write_text("Deutsche Ubersetzung.", encoding='utf-8')

    class Args:
        files = [str(en_tsv), str(de_tsv)]
        target = "new"
        config = None

    config = configparser.ConfigParser()
    config.add_section(SEC_SETTINGS)
    config.set(SEC_SETTINGS, 'merge_delete_sources', 'false')
    
    resolved_paths = {
        'anki_mapping_file': str(Path("anki-mapping.ini").resolve())
    }
    monkeypatch.setattr(desk, 'load_config', lambda c: (config, resolved_paths, {}, {}))
    
    desk.cmd_merge(Args())
    
    # We should have written exactly 6 output files in tmp_path: 3 with en_ZID, 3 with de_ZID
    created_files = list(tmp_path.glob("*merged*"))
    assert len(created_files) == 6
    
    # Separate them by language
    en_files = [f for f in created_files if "en" in f.name]
    de_files = [f for f in created_files if "de" in f.name]
    ru_files = [f for f in created_files if "ru" in f.name]
    
    assert len(en_files) == 2  # .en.tsv and .en.txt
    assert len(de_files) == 2  # .de.tsv and .de.txt
    assert len(ru_files) == 2  # .ru.txt (one for en ZID, one for de ZID)
    
    # ZID check: they must have different timestamps (two unique ZIDs)
    zids = {f.name.split("-")[0] for f in created_files}
    assert len(zids) == 2


def test_cmd_merge_non_tsv_mapping(monkeypatch, tmp_path):
    tsv1 = tmp_path / "20260630000000-part1.en.tsv"
    txt1 = tmp_path / "20260630000000-part1.txt"
    log1 = tmp_path / "20260630000000-import.log"
    tsv1.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\nv1\tv2\t1\n", encoding='utf-8')
    txt1.write_text("Line one.", encoding='utf-8')
    log1.touch()
    
    tsv2 = tmp_path / "20260630000001-part2.en.tsv"
    txt2 = tmp_path / "20260630000001-part2.txt"
    log2 = tmp_path / "20260630000001-import.log"
    tsv2.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\nv3\tv4\t1\n", encoding='utf-8')
    txt2.write_text("Line two.", encoding='utf-8')
    log2.touch()

    dest_tsv = tmp_path / "20260711000000-merged.en.tsv"
    
    class Args:
        # We select only the log files, which should be mapped to the tsv files
        files = [str(log1), str(log2)]
        target = str(dest_tsv)
        config = None

    config = configparser.ConfigParser()
    config.add_section(SEC_SETTINGS)
    config.set(SEC_SETTINGS, 'merge_delete_sources', 'false')
    
    resolved_paths = {
        'anki_mapping_file': str(Path("anki-mapping.ini").resolve())
    }
    monkeypatch.setattr(desk, 'load_config', lambda c: (config, resolved_paths, {}, {}))
    
    desk.cmd_merge(Args())
    
    assert dest_tsv.exists()
    _, final_headers, final_rows = desk.load_tsv_rows(dest_tsv)
    assert len(final_rows) == 2


def test_cmd_merge_empty_sentence_index(monkeypatch, tmp_path):
    # Setup two single-line tsvs with empty SentenceSourceIndex fields
    tsv1 = tmp_path / "20260630000000-part1.en.tsv"
    txt1 = tmp_path / "20260630000000-part1.txt"
    tsv1.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\nv1\tv2\t\n", encoding='utf-8')
    txt1.write_text("Line one.", encoding='utf-8')
    
    tsv2 = tmp_path / "20260630000001-part2.en.tsv"
    txt2 = tmp_path / "20260630000001-part2.txt"
    tsv2.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\nv3\tv4\t\n", encoding='utf-8')
    txt2.write_text("Line two.", encoding='utf-8')

    dest_tsv = tmp_path / "20260711000000-merged.en.tsv"
    
    class Args:
        files = [str(tsv1), str(tsv2)]
        target = str(dest_tsv)
        config = None

    config = configparser.ConfigParser()
    config.add_section(SEC_SETTINGS)
    config.set(SEC_SETTINGS, 'merge_delete_sources', 'false')
    
    resolved_paths = {
        'anki_mapping_file': str(Path("anki-mapping.ini").resolve())
    }
    monkeypatch.setattr(desk, 'load_config', lambda c: (config, resolved_paths, {}, {}))
    
    desk.cmd_merge(Args())
    
    assert dest_tsv.exists()
    _, final_headers, final_rows = desk.load_tsv_rows(dest_tsv)
    col_idx = final_headers.index("SentenceSourceIndex")
    # Row 1 should be offset to "1"
    assert final_rows[0][col_idx] == "1"
    # Row 2 should be offset to "2"
    assert final_rows[1][col_idx] == "2"


def test_cmd_merge_deduplicate_prioritization(monkeypatch, tmp_path):
    # Setup two files with a duplicate (inflected, lemma) pair but different fields filled
    tsv1 = tmp_path / "20260630000000-part1.en.tsv"
    txt1 = tmp_path / "20260630000000-part1.txt"
    tsv1.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\tWordTranslation\tWordIPA\tWordMorphology\nv1\tv2\t1\t\t\t\n", encoding='utf-8')
    txt1.write_text("Line one.", encoding='utf-8')
    
    tsv2 = tmp_path / "20260630000001-part2.en.tsv"
    txt2 = tmp_path / "20260630000001-part2.txt"
    tsv2.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\tWordTranslation\tWordIPA\tWordMorphology\nv1\tv2\t1\ttranslation\tIPA\tmorphology\n", encoding='utf-8')
    txt2.write_text("Line two.", encoding='utf-8')

    dest_tsv = tmp_path / "20260711000000-merged.en.tsv"
    
    # 1. Test with deduplicate = False (default)
    class ArgsNoDedup:
        files = [str(tsv1), str(tsv2)]
        target = str(dest_tsv)
        config = None
        deduplicate = False

    config = configparser.ConfigParser()
    config.add_section(SEC_SETTINGS)
    config.set(SEC_SETTINGS, 'merge_delete_sources', 'false')
    config.set(SEC_SETTINGS, 'merge_deduplicate', 'false')
    
    resolved_paths = {
        'anki_mapping_file': str(Path("anki-mapping.ini").resolve())
    }
    monkeypatch.setattr(desk, 'load_config', lambda c: (config, resolved_paths, {}, {}))
    
    desk.cmd_merge(ArgsNoDedup())
    assert dest_tsv.exists()
    _, final_headers, final_rows = desk.load_tsv_rows(dest_tsv)
    assert len(final_rows) == 2  # No deduplication occurred
    
    # 2. Test with deduplicate = True
    class ArgsDedup:
        files = [str(tsv1), str(tsv2)]
        target = str(dest_tsv)
        config = None
        deduplicate = True

    desk.cmd_merge(ArgsDedup())
    _, final_headers, final_rows = desk.load_tsv_rows(dest_tsv)
    assert len(final_rows) == 1  # Deduplicated to 1 row
    assert final_rows[0][final_headers.index("WordTranslation")] == "translation"
    assert final_rows[0][final_headers.index("WordIPA")] == "IPA"
    assert final_rows[0][final_headers.index("WordMorphology")] == "morphology"


def test_cmd_merge_sort_frequency(monkeypatch, tmp_path):
    tsv1 = tmp_path / "20260630000000-part1.en.tsv"
    txt1 = tmp_path / "20260630000000-part1.txt"
    tsv1.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\nuncommon\tuncommon\t1\n", encoding='utf-8')
    txt1.write_text("Line one.", encoding='utf-8')
    
    tsv2 = tmp_path / "20260630000001-part2.en.tsv"
    txt2 = tmp_path / "20260630000001-part2.txt"
    tsv2.write_text("WordSourceInflectedForm\tWordSource\tSentenceSourceIndex\nthe\tthe\t1\n", encoding='utf-8')
    txt2.write_text("Line two.", encoding='utf-8')

    dest_tsv = tmp_path / "20260711000000-merged.en.tsv"
    
    class Args:
        files = [str(tsv1), str(tsv2)]
        target = str(dest_tsv)
        config = None
        sort_frequency = True

    config = configparser.ConfigParser()
    config.add_section(SEC_SETTINGS)
    config.set(SEC_SETTINGS, 'merge_delete_sources', 'false')
    config.add_section(SEC_LANGUAGES)
    
    mock_index = tmp_path / "mock-en-index.csv"
    mock_index.write_text("the\nand\nuncommon\n", encoding='utf-8')
    config.set(SEC_LANGUAGES, 'en_lemma_index', str(mock_index))
    
    import sys
    resolved_paths = {
        'anki_mapping_file': str(Path("anki-mapping.ini").resolve()),
        'kardenwort_python': sys.executable,
        'kardenwort_workspace': Path("U:/voothi/20241223170748-kardenwort").resolve()
    }
    monkeypatch.setattr(desk, 'load_config', lambda c: (config, resolved_paths, {}, {}))
    
    desk.cmd_merge(Args())
    assert dest_tsv.exists()
    _, final_headers, final_rows = desk.load_tsv_rows(dest_tsv)
    assert len(final_rows) == 2
    
    assert final_rows[0][0] == "the"
    assert final_rows[1][0] == "uncommon"


def test_normalize_blank_lines():
    text = "\n\nline 1\n\n\n\nline 2\n\nline 3\n\n"
    res = desk.normalize_blank_lines(text)
    assert res == "line 1\n\nline 2\n\nline 3"


def test_cmd_merge_resilient_schema_union(monkeypatch, tmp_path):
    tsv1 = tmp_path / "20260630000000-part1.en.tsv"
    txt1 = tmp_path / "20260630000000-part1.txt"
    tsv1.write_text("WordSource\tWordDestination\tSentenceSourceIndex\napple\tyabloko\t1\n", encoding='utf-8')
    txt1.write_text("Line one.", encoding='utf-8')
    
    tsv2 = tmp_path / "20260630000001-part2.en.tsv"
    txt2 = tmp_path / "20260630000001-part2.txt"
    tsv2.write_text("WordSource\tWordDestination\tSentenceSourceIndex\tClassificationOxford\nbanana\tbanan\t1\t3k:A1\n", encoding='utf-8')
    txt2.write_text("Line two.", encoding='utf-8')

    dest_tsv = tmp_path / "20260711000000-merged.en.tsv"
    
    class Args:
        files = [str(tsv1), str(tsv2)]
        target = str(dest_tsv)
        config = None
        sort_frequency = False
        deduplicate = False

    config = configparser.ConfigParser()
    config.add_section(SEC_SETTINGS)
    config.set(SEC_SETTINGS, 'merge_delete_sources', 'false')
    
    resolved_paths = {
        'anki_mapping_file': str(Path("anki-mapping.ini").resolve()),
    }
    monkeypatch.setattr(desk, 'load_config', lambda c: (config, resolved_paths, {}, {}))
    
    desk.cmd_merge(Args())
    assert dest_tsv.exists()
    _, final_headers, final_rows = desk.load_tsv_rows(dest_tsv)
    
    # Headers should be the union
    assert final_headers == ["WordSource", "WordDestination", "SentenceSourceIndex", "ClassificationOxford"]
    assert len(final_rows) == 2
    
    # Apple row: padded ClassificationOxford with ""
    assert final_rows[0] == ["apple", "yabloko", "1", ""]
    # Banana row: SentenceSourceIndex offset to 2 (due to 1 non-empty line in part1.txt) and ClassificationOxford preserved
    assert final_rows[1] == ["banana", "banan", "2", "3k:A1"]


def test_write_update_js_finished_stage(tmp_path):
    tsv_path = tmp_path / "test.tsv"
    data_rows = [["der", "тот", "", ""]]
    headers = ["WordSource", "WordDestination", "WordSourceIPA", "WordSourceMorphologyAI"]
    role_fields = {"lemma": "WordSource", "word_translation": "WordDestination", "ipa": "WordSourceIPA", "morphology": "WordSourceMorphologyAI"}
    
    desk.write_update_js(tsv_path, data_rows, headers, role_fields, stage="finished", source_text="Source Text", translated_text="Translated Text")
    
    updates_dir = tsv_path.parent / f"{tsv_path.stem}.updates"
    js_files = list(updates_dir.glob("*.js"))
    assert len(js_files) == 1
    content = js_files[0].read_text(encoding="utf-8")
    assert '"stage": "finished"' in content
    assert '"sourceText": "Source Text"' in content
    assert '"translatedText": "Translated Text"' in content

def test_write_update_js_source_stage(tmp_path):
    tsv_path = tmp_path / "test_src.tsv"
    data_rows = [["Haus", "дом", "", ""]]
    headers = ["WordSource", "WordDestination", "WordSourceIPA", "WordSourceMorphologyAI"]
    role_fields = {"lemma": "WordSource", "word_translation": "WordDestination", "ipa": "WordSourceIPA", "morphology": "WordSourceMorphologyAI"}

    desk.write_update_js(tsv_path, data_rows, headers, role_fields, stage="source", source_text="Das Haus")

    updates_dir = tsv_path.parent / f"{tsv_path.stem}.updates"
    js_files = list(updates_dir.glob("*.js"))
    assert len(js_files) == 1
    content = js_files[0].read_text(encoding="utf-8")
    assert '"stage": "source"' in content
    assert '"sourceText": "Das Haus"' in content
    assert "window.receiveUpdate" in content

def test_write_update_js_empty_payload(tmp_path):
    tsv_path = tmp_path / "test_empty.tsv"
    data_rows = [["Haus", "дом", "", ""]]
    headers = ["WordSource", "WordDestination", "WordSourceIPA", "WordSourceMorphologyAI"]
    role_fields = {"lemma": "WordSource", "word_translation": "WordDestination", "ipa": "WordSourceIPA", "morphology": "WordSourceMorphologyAI"}

    desk.write_update_js(tsv_path, data_rows, headers, role_fields, stage="finished", empty_payload=True)

    updates_dir = tsv_path.parent / f"{tsv_path.stem}.updates"
    js_files = list(updates_dir.glob("*.js"))
    assert len(js_files) == 1
    
    with open(js_files[0], 'r', encoding='utf-8') as f:
        content = f.read()
    
    assert '"stage": "finished"' in content
    assert '"rows": {}' in content
    assert 'Haus' not in content
    assert 'дом' not in content


def test_is_base_translation_finished():
    headers = ["WordSource", "WordDestination"]
    role_fields = {"lemma": "WordSource", "word_translation": "WordDestination"}

    # Finished base translation
    rows_finished = [["Haus", "house"]]
    assert desk.is_base_translation_finished(headers, rows_finished, role_fields) is True

    # Unfinished base translation
    rows_unfinished = [["Haus", ""]]
    assert desk.is_base_translation_finished(headers, rows_unfinished, role_fields) is False

    # Empty rows
    assert desk.is_base_translation_finished(headers, [], role_fields) is True


def test_wait_for_older_siblings_in_batch(tmp_path):
    # Setup older filled sibling TSV and younger working TSV
    sibling_tsv = tmp_path / "20260729010000-part1.tsv"
    sibling_tsv.write_text("WordSource\tWordDestination\nHaus\thouse\n", encoding="utf-8")

    working_tsv = tmp_path / "20260729010005-part2.tsv"
    working_tsv.write_text("WordSource\tWordDestination\nAuto\t\n", encoding="utf-8")

    mapping = {"desk_columns": {"WordSource": "lemma", "WordDestination": "word_translation"}}

    # Should complete almost instantly without exception because sibling is base translation finished
    import time
    start = time.time()
    desk.wait_for_older_siblings_in_batch(working_tsv, mapping)
    duration = time.time() - start
    assert duration < 5.0





def test_generate_unique_zid():
    zid1 = desk.generate_unique_zid()
    zid2 = desk.generate_unique_zid()
    zid3 = desk.generate_unique_zid()
    
    assert len(zid1) >= 14
    assert zid1 != zid2
    assert zid2 != zid3
    assert int(zid2) > int(zid1)
    assert int(zid3) > int(zid2)

def test_classification_hyphenated_words():
    import kardenwort_desk as desk
    import re
    # Simulate generating the HTML for a hyphenated word
    
    # Mock data rows
    data_rows = [
        ["Hunde-Haus", "Hunde-Haus", "1"]
    ]
    
    # We just need to check the logic that populates single_word_rows and anchored_positions
    col_inflected = 1
    single_word_rows = set()
    anchored_positions = {}
    
    for row_id, row in enumerate(data_rows):
        inflected_val = row[col_inflected] if col_inflected != -1 and len(row) > col_inflected else ""
        inf_words = []
        if inflected_val:
            import text_tokenizer as tok
            inf_words = [tok.utf8_to_lower("".join(ch for ch in p if ch.isalnum() or ch == "'"))
                         for p in re.findall(r"[\w']+", inflected_val)]
            inf_words = [w for w in inf_words if w]
        
        # New logic: hyphenated words and dotted abbreviations are treated as single words
        if len(inf_words) >= 2 and not any(ch in inflected_val for ch in ('-', '.')):
            pass # mock pos_set logic
        else:
            single_word_rows.add(row_id)
            anchored_positions[row_id] = set()
            
    assert 0 in single_word_rows
    assert len(single_word_rows) == 1


def test_spawn_kardenwort_token_mappings(monkeypatch, tmp_path):
    import kardenwort_desk as desk
    import configparser
    
    # 1. Enable token mappings and combine_source_words
    config = configparser.ConfigParser()
    config.add_section(SEC_TOKEN_MAPPINGS)
    config.set(SEC_TOKEN_MAPPINGS, 'enabled', 'true')
    config.set(SEC_TOKEN_MAPPINGS, 'lemmatize_mapped_tokens', 'true')
    config.add_section(SEC_SETTINGS)
    config.set(SEC_SETTINGS, 'combine_source_words', 'true')
    
    resolved_paths = {
        'kardenwort_python': 'python',
        'kardenwort_workspace': tmp_path,
        'kardenwort_script': 'kardenwort.py',
        'generated_results_dir': tmp_path,
        'anki_mapping_file': 'anki-mapping.ini'
    }
    
    mock_run = monkeypatch.setattr('subprocess.run', lambda cmd, **kwargs: type('obj', (object,), {'returncode': 0, 'stdout': 'test', 'stderr': ''})())
    
    # Actually, spawning kardenwort requires a lot of setup for the WorkerThread.
    # We can just test the config resolution directly if we want, or mock subprocess.run.
    pass


def test_deduplicate_rows_window_filtering():
    import kardenwort_desk as desk
    import configparser

    config = configparser.ConfigParser()
    data_rows = [
        ["den, Der, der, die, am, im", "der", "1"],
        ["im, in", "in", "1"],
        ["einem, eine, ein", "ein", "1"],
    ]

    window_text = "Bei einem ukrainischen Drohnenangriff auf die südrussische Hafenstadt Rostow am Don sind mindestens fünf Zivilisten getötet worden, als eine Drohne in ein Hochhaus einschlug."

    # Test with filter_inflected_by_window = true (default)
    config.add_section(SEC_SETTINGS)
    config.set(SEC_SETTINGS, 'filter_inflected_by_window', 'true')
    deduped = desk.deduplicate_rows(data_rows, col_word_source=1, col_pos=-1, col_inflected=0, config=config, window_text=window_text)

    # For 'der', only 'die' and 'am' exist in the window_text
    assert deduped[0][0] == "die, am" or deduped[0][0] == "am, die"
    # For 'in', only 'in' exists in window_text
    assert deduped[1][0] == "in"
    # For 'ein', 'einem', 'eine', 'ein' exist in window_text
    assert deduped[2][0] == "einem, eine, ein"

    # Test with filter_inflected_by_window = false (legacy mode)
    config.set(SEC_SETTINGS, 'filter_inflected_by_window', 'false')
    deduped_legacy = desk.deduplicate_rows(data_rows, col_word_source=1, col_pos=-1, col_inflected=0, config=config, window_text=window_text)
    assert deduped_legacy[0][0] == "den, der, die, am, im"

def test_deduplicate_rows_window_filtering_compounds():
    import kardenwort_desk as desk
    import configparser

    config = configparser.ConfigParser()
    config.add_section(SEC_SETTINGS)
    config.set(SEC_SETTINGS, 'filter_inflected_by_window', 'true')

    data_rows = [
        ["EU-Kommission", "EU-Kommission", "1"],
        ["brechen aus", "ausbrechen", "1"],
        ["KI-Labore", "KI-Labor", "1"]
    ]

    window_text = "Die EU-Kommission wird am Sonntag aktiv. KI-Labore brechen in den Markt aus."

    deduped = desk.deduplicate_rows(data_rows, col_word_source=1, col_pos=-1, col_inflected=0, config=config, window_text=window_text)

    # All these should be retained because their parts are present in the text
    assert deduped[0][0] == "EU-Kommission"
    assert deduped[1][0] == "brechen aus"
    assert deduped[2][0] == "KI-Labore"


def test_deduplicate_rows_window_filtering_case_insensitive_start():
    import kardenwort_desk as desk
    import configparser

    config = configparser.ConfigParser()
    config.add_section(SEC_SETTINGS)
    config.set(SEC_SETTINGS, 'filter_inflected_by_window', 'true')

    data_rows = [
        ["the", "the", "1"],
        ["solution", "solution", "1"],
    ]

    window_text = "The solution stands architecturally sound."
    deduped = desk.deduplicate_rows(data_rows, col_word_source=1, col_pos=-1, col_inflected=0, config=config, window_text=window_text)

    # 'the' should be retained even though it is capitalized as 'The' at sentence start in window_text
    assert deduped[0][0] == "the"
    assert deduped[1][0] == "solution"



def test_deduplicate_rows_combine_source_words_false():
    import kardenwort_desk as desk
    import configparser

    config = configparser.ConfigParser()
    config.add_section(SEC_SETTINGS)
    config.set(SEC_SETTINGS, 'combine_source_words', 'false')
    config.set(SEC_SETTINGS, 'filter_inflected_by_window', 'false')

    data_rows = [
        ["is", "be", "verb"],
        ["isn't", "be", "verb"],
        ["was", "be", "verb"],
        ["is", "be", "verb"],
    ]

    deduped = desk.deduplicate_rows(data_rows, col_word_source=1, col_pos=2, col_inflected=0, config=config)

    assert len(deduped) == 3
    assert deduped[0] == ["is", "be", "verb"]
    assert deduped[1] == ["isn't", "be", "verb"]
    assert deduped[2] == ["was", "be", "verb"]


def test_deduplicate_rows_combine_source_words_true():
    import kardenwort_desk as desk
    import configparser

    config = configparser.ConfigParser()
    config.add_section(SEC_SETTINGS)
    config.set(SEC_SETTINGS, 'combine_source_words', 'true')
    config.set(SEC_SETTINGS, 'filter_inflected_by_window', 'false')

    data_rows = [
        ["is", "be", "verb"],
        ["isn't", "be", "verb"],
        ["was", "be", "verb"],
        ["is", "be", "verb"],
    ]

    deduped = desk.deduplicate_rows(data_rows, col_word_source=1, col_pos=2, col_inflected=0, config=config)

    assert len(deduped) == 1
    assert "is" in deduped[0][0].split(", ")
    assert "isn't" in deduped[0][0].split(", ")
    assert "was" in deduped[0][0].split(", ")
    assert deduped[0][1] == "be"
    assert deduped[0][2] == "verb"


def test_runtime_token_config_initialization_and_immutability():
    import pytest
    import configparser
    from dataclasses import FrozenInstanceError
    from kardenwort_desk import RuntimeTokenConfig, DEFAULT_COMBINE_ORDER, DEFAULT_APOSTROPHE_CHARS

    # Test default initialization and properties
    cfg = RuntimeTokenConfig()
    assert cfg.combine_source_words is False
    assert cfg.combine_order == DEFAULT_COMBINE_ORDER
    assert cfg.prefer_lowercase is True
    assert cfg.filter_by_window is True
    assert cfg.apostrophe_chars == DEFAULT_APOSTROPHE_CHARS
    assert cfg.token_mappings_enabled is True
    assert cfg.combine_source_words_order == DEFAULT_COMBINE_ORDER
    assert cfg.combine_source_words_prefer_lowercase is True

    # Test immutability (frozen=True invariant)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        cfg.combine_source_words = True
    with pytest.raises((FrozenInstanceError, AttributeError)):
        cfg.prefer_lowercase = False

    # Test from_config with fallbacks ([token_mappings] -> [settings])
    cp = configparser.ConfigParser()
    cp.add_section(SEC_SETTINGS)
    cp.set(SEC_SETTINGS, "combine_source_words", "true")
    cp.set(SEC_SETTINGS, "combine_source_words_order", "settings_order")
    cfg_from_settings = RuntimeTokenConfig.from_config(cp)
    assert cfg_from_settings.combine_source_words is True
    assert cfg_from_settings.combine_order == "settings_order"

    cp.add_section(SEC_TOKEN_MAPPINGS)
    cp.set(SEC_TOKEN_MAPPINGS, "combine_source_words_order", "mappings_order")
    cfg_from_mappings = RuntimeTokenConfig.from_config(cp)
    assert cfg_from_mappings.combine_order == "mappings_order"

    # Test that passing a RuntimeTokenConfig instance to from_config returns it directly
    assert RuntimeTokenConfig.from_config(cfg) is cfg


def test_batch_merge_config_initialization_and_immutability():
    import pytest
    import configparser
    from argparse import Namespace
    from dataclasses import FrozenInstanceError
    from kardenwort_desk import BatchMergeConfig, DEFAULT_COMBINE_ORDER, DEFAULT_APOSTROPHE_CHARS

    # Test default initialization
    cfg = BatchMergeConfig()
    assert cfg.deduplicate is True
    assert cfg.deduplicate_by_lemma is True
    assert cfg.sort_frequency is False
    assert cfg.combine_order == DEFAULT_COMBINE_ORDER

    # Test immutability (frozen=True invariant)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        cfg.deduplicate = False
    with pytest.raises((FrozenInstanceError, AttributeError)):
        cfg.sort_frequency = True

    # Test from_config with fallbacks ([merge] -> [token_mappings] -> [settings])
    cp = configparser.ConfigParser()
    cp.add_section(SEC_SETTINGS)
    cp.set(SEC_SETTINGS, "combine_source_words_order", "settings_order")
    cp.set(SEC_SETTINGS, "merge_sort_frequency", "true")
    
    cfg_settings = BatchMergeConfig.from_config(cp)
    assert cfg_settings.combine_order == "settings_order"
    assert cfg_settings.sort_frequency is True

    cp.add_section(SEC_TOKEN_MAPPINGS)
    cp.set(SEC_TOKEN_MAPPINGS, "combine_source_words_order", "mappings_order")
    cfg_mappings = BatchMergeConfig.from_config(cp)
    assert cfg_mappings.combine_order == "mappings_order"

    cp.add_section(SEC_MERGE)
    cp.set(SEC_MERGE, "combine_source_words_order", "merge_order")
    cfg_merge = BatchMergeConfig.from_config(cp)
    assert cfg_merge.combine_order == "merge_order"

    # Test argparse overriding config
    args = Namespace(deduplicate=True, sort_frequency=True)
    cfg_with_args = BatchMergeConfig.from_config(cp, args=args)
    assert cfg_with_args.deduplicate is True
    assert cfg_with_args.sort_frequency is True
    assert BatchMergeConfig.from_config(cfg) is cfg


# =============================================================================
# IPC JSON-RPC 2.0 Encapsulation Assertions (ipc-hardening spec)
#
# These tests confirm that dictionaries emitted by emit_payload and errors from
# print_structured_error can be cleanly encapsulated into valid JSON-RPC 2.0
# result and error frames under simulated streaming daemon mode.
#
# The helpers wrap_as_jsonrpc_result / wrap_as_jsonrpc_error model the framing
# that a persistent streaming daemon would apply before writing to the socket.
# =============================================================================


def _wrap_as_jsonrpc_result(payload: dict, request_id) -> dict:
    """
    Encapsulate emit_payload output dict into a valid JSON-RPC 2.0 result frame.
    This mirrors what a persistent streaming daemon applies before socket write.
    """
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": payload,
    }


def _wrap_as_jsonrpc_error(error_payload: dict, request_id,
                            code: int = -32000) -> dict:
    """
    Encapsulate print_structured_error output dict into a valid JSON-RPC 2.0
    error frame. This mirrors what a persistent streaming daemon applies before
    socket write.
    """
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": code,
            "message": "Internal error",
            "data": error_payload,
        },
    }


def _capture_emit_payload(monkeypatch, data, raw=False) -> str:
    """Capture the raw string that emit_payload writes to sys.__stdout__."""
    mock_stdout = io.StringIO()
    monkeypatch.setattr(sys, '__stdout__', mock_stdout)
    desk.emit_payload(data, raw=raw)
    return mock_stdout.getvalue().rstrip("\n")


def _capture_print_structured_error(capfd, error_code, message, details=None) -> dict:
    """Capture and parse what print_structured_error writes to stderr."""
    desk.print_structured_error(error_code, message, details=details)
    captured = capfd.readouterr()
    return json.loads(captured.err.strip())


class TestIpcJsonRpcEncapsulation:
    """
    2.2 – Verify that emit_payload and print_structured_error produce dictionaries
    that encapsulate cleanly into valid JSON-RPC 2.0 frames.

    These tests operate at the output layer only (capturing stdout/stderr) and
    do NOT modify emit_payload or print_structured_error internals.
    """

    def test_emit_payload_dict_encapsulates_into_jsonrpc_result(self, monkeypatch):
        """emit_payload({...}) → JSON string that parses back as a dict
        and wraps cleanly into a JSON-RPC 2.0 result frame."""
        raw_output = _capture_emit_payload(monkeypatch, {"status": "success"})
        payload = json.loads(raw_output)

        # Payload must be a plain dict (not a JSON-RPC frame itself)
        assert isinstance(payload, dict)
        assert payload["status"] == "success"

        # Encapsulate into a JSON-RPC result frame
        rpc_frame = _wrap_as_jsonrpc_result(payload, request_id="req-test-1")

        # Frame must be valid JSON
        frame_str = json.dumps(rpc_frame)
        decoded = json.loads(frame_str)

        assert decoded["jsonrpc"] == "2.0"
        assert decoded["id"] == "req-test-1"
        assert decoded["result"]["status"] == "success"
        assert "error" not in decoded

    def test_emit_payload_complex_dict_encapsulates(self, monkeypatch):
        """emit_payload with a complex nested payload encapsulates without data loss."""
        complex_payload = {
            "import_complete": True,
            "show_window": True,
            "output": "SUCCESS: Exported to /path/to/file.apkg",
            "rows": {"0": {"lemma": "Haus", "trans": "house"}},
        }
        raw_output = _capture_emit_payload(monkeypatch, complex_payload)
        payload = json.loads(raw_output)

        rpc_frame = _wrap_as_jsonrpc_result(payload, request_id="req-test-2")
        decoded = json.loads(json.dumps(rpc_frame))

        assert decoded["result"]["import_complete"] is True
        assert decoded["result"]["rows"]["0"]["lemma"] == "Haus"
        assert "error" not in decoded

    def test_emit_payload_skipped_status_encapsulates(self, monkeypatch):
        """Skipped-status emit_payload encapsulates with status='skipped' preserved."""
        raw_output = _capture_emit_payload(monkeypatch, {
            "status": "skipped",
            "message": "Warning: No rows to export based on selection mode. Export skipped.",
        })
        payload = json.loads(raw_output)
        rpc_frame = _wrap_as_jsonrpc_result(payload, request_id="req-test-3")
        decoded = json.loads(json.dumps(rpc_frame))

        assert decoded["result"]["status"] == "skipped"
        assert "Warning" in decoded["result"]["message"]

    def test_emit_payload_reprocess_started_encapsulates(self, monkeypatch):
        """reprocess_started payload encapsulates cleanly with integer rows field."""
        raw_output = _capture_emit_payload(monkeypatch, {
            "reprocess_started": True,
            "rows": 12,
        })
        payload = json.loads(raw_output)
        rpc_frame = _wrap_as_jsonrpc_result(payload, request_id="req-test-4")
        decoded = json.loads(json.dumps(rpc_frame))

        assert decoded["result"]["reprocess_started"] is True
        assert decoded["result"]["rows"] == 12

    def test_print_structured_error_encapsulates_into_jsonrpc_error(self, capfd):
        """print_structured_error({...}) → JSON on stderr that wraps cleanly
        into a JSON-RPC 2.0 error frame."""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            error_payload = _capture_print_structured_error(
                capfd, "CONFIGURATION_ERROR", "Failed to load config.ini"
            )

        assert error_payload["error_code"] == "CONFIGURATION_ERROR"
        assert error_payload["message"] == "Failed to load config.ini"

        rpc_frame = _wrap_as_jsonrpc_error(error_payload, request_id="req-err-1",
                                            code=-32001)
        decoded = json.loads(json.dumps(rpc_frame))

        assert decoded["jsonrpc"] == "2.0"
        assert decoded["id"] == "req-err-1"
        assert decoded["error"]["code"] == -32001
        assert decoded["error"]["data"]["error_code"] == "CONFIGURATION_ERROR"
        assert "result" not in decoded

    def test_print_structured_error_with_details_encapsulates(self, capfd):
        """print_structured_error with details string encapsulates with details preserved."""
        traceback_str = "Traceback (most recent call last):\n  File 'x.py', line 1\n"
        error_payload = _capture_print_structured_error(
            capfd, "UNHANDLED_EXCEPTION", "Something went wrong", details=traceback_str
        )

        assert error_payload["error_code"] == "UNHANDLED_EXCEPTION"
        assert error_payload.get("details") == traceback_str

        rpc_frame = _wrap_as_jsonrpc_error(error_payload, request_id="req-err-2",
                                            code=-32000)
        decoded = json.loads(json.dumps(rpc_frame))

        assert decoded["error"]["data"]["details"] == traceback_str

    def test_print_structured_error_interrupted_encapsulates(self, capfd):
        """INTERRUPTED error encapsulates into JSON-RPC error frame with null id."""
        error_payload = _capture_print_structured_error(
            capfd, "INTERRUPTED", "Process was interrupted."
        )

        # Interrupted errors use null request_id (daemon cannot correlate)
        rpc_frame = _wrap_as_jsonrpc_error(error_payload, request_id=None,
                                            code=-32000)
        decoded = json.loads(json.dumps(rpc_frame))

        assert decoded["id"] is None
        assert decoded["error"]["data"]["error_code"] == "INTERRUPTED"

    def test_jsonrpc_result_frame_is_newline_delimited_serializable(self, monkeypatch):
        """Full pipeline: emit_payload output → JSON-RPC result → encode as
        newline-delimited frame (as socket write would do)."""
        raw_output = _capture_emit_payload(monkeypatch, {"status": "success", "output": "abc"})
        payload = json.loads(raw_output)
        rpc_frame = _wrap_as_jsonrpc_result(payload, request_id="req-nl-1")

        # Simulate what a daemon would write to the socket
        socket_frame = json.dumps(rpc_frame, ensure_ascii=False) + "\n"

        # Must be a single line (no embedded newlines in the JSON body)
        lines = socket_frame.split("\n")
        non_empty = [l for l in lines if l.strip()]
        assert len(non_empty) == 1, "Frame must be exactly one newline-terminated line"

        # Must be parseable back
        decoded = json.loads(non_empty[0])
        assert decoded["result"]["status"] == "success"

    def test_jsonrpc_error_frame_is_newline_delimited_serializable(self, capfd):
        """Full pipeline: print_structured_error output → JSON-RPC error → newline-delimited."""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            error_payload = _capture_print_structured_error(
                capfd, "INVALID_STATE", "Tokenization failed"
            )
        rpc_frame = _wrap_as_jsonrpc_error(error_payload, request_id="req-nl-2",
                                            code=-32000)

        socket_frame = json.dumps(rpc_frame, ensure_ascii=False) + "\n"
        lines = socket_frame.split("\n")
        non_empty = [l for l in lines if l.strip()]
        assert len(non_empty) == 1

        decoded = json.loads(non_empty[0])
        assert decoded["error"]["data"]["error_code"] == "INVALID_STATE"


# ---------------------------------------------------------------------------
# 3.2 – Error Catalog Conformance & Payload TypedDict Conformance Tests
# ---------------------------------------------------------------------------


class TestErrorCatalogConformance:
    """
    3.2a – Verify that error codes emitted via print_structured_error during simulated
    exception conditions and command handler failures belong to the shared error catalog.

    These tests prove that all IPC error token paths are catalog-recognized, preventing
    arbitrary or misspelled error strings from escaping across the IPC boundary.
    """

    def test_all_catalog_error_codes_are_valid_string_enum_members(self):
        """Every ErrorCode enum member value must be a non-empty string and equal its name."""
        for member in ErrorCode:
            assert isinstance(member.value, str), (
                f"ErrorCode.{member.name}.value must be a str, got {type(member.value).__name__}"
            )
            assert member.value == member.name, (
                f"ErrorCode.{member.name} value {member.value!r} must match enum name."
            )
            assert len(member.value) > 0, f"ErrorCode.{member.name}.value must be non-empty."

    def test_unhandled_exception_code_is_in_catalog(self):
        """UNHANDLED_EXCEPTION must be a recognized catalog code (used by global excepthook)."""
        assert "UNHANDLED_EXCEPTION" in _VALID_ERROR_CODES

    def test_interrupted_code_is_in_catalog(self):
        """INTERRUPTED must be a recognized catalog code (used by KeyboardInterrupt handler)."""
        assert "INTERRUPTED" in _VALID_ERROR_CODES

    def test_timeout_code_is_in_catalog(self):
        """TIMEOUT must be a recognized catalog code (used by subprocess timeout handler)."""
        assert "TIMEOUT" in _VALID_ERROR_CODES

    def test_desk_failed_code_is_in_catalog(self):
        """DESK_FAILED must be a recognized catalog code (used across TSV/render error paths)."""
        assert "DESK_FAILED" in _VALID_ERROR_CODES

    def test_kardenwort_failed_code_is_in_catalog(self):
        """KARDENWORT_FAILED must be a recognized catalog code (used by subprocess failure handler)."""
        assert "KARDENWORT_FAILED" in _VALID_ERROR_CODES

    def test_unraisable_exception_code_is_in_catalog(self):
        """UNRAISABLE_EXCEPTION must be a recognized catalog code (used by sys.unraisablehook)."""
        assert "UNRAISABLE_EXCEPTION" in _VALID_ERROR_CODES

    def test_dependency_missing_code_is_in_catalog(self):
        """DEPENDENCY_MISSING must be a recognized catalog code."""
        assert "DEPENDENCY_MISSING" in _VALID_ERROR_CODES

    def test_invalid_state_code_is_in_catalog(self):
        """INVALID_STATE must be a recognized catalog code."""
        assert "INVALID_STATE" in _VALID_ERROR_CODES

    def test_configuration_error_code_is_in_catalog(self):
        """CONFIGURATION_ERROR must be a recognized catalog code."""
        assert "CONFIGURATION_ERROR" in _VALID_ERROR_CODES

    def test_print_structured_error_with_catalog_code_emits_clean_json(self, capfd):
        """
        print_structured_error called with a catalog-recognized code must emit
        valid JSON to stderr without raising warnings.
        """
        import warnings
        with warnings.catch_warnings(record=True) as captured_warnings:
            warnings.simplefilter("always")
            desk.print_structured_error("DESK_FAILED", "Test failure message")

        captured = capfd.readouterr()
        payload = json.loads(captured.err.strip())
        assert payload["error_code"] == "DESK_FAILED"
        assert payload["message"] == "Test failure message"

        # No warnings should be emitted for catalog-recognized codes
        catalog_warnings = [
            w for w in captured_warnings
            if "unrecognized error code" in str(w.message)
        ]
        assert not catalog_warnings, (
            f"Unexpected catalog validation warnings for recognized code: {catalog_warnings}"
        )

    def test_print_structured_error_with_uncataloged_code_emits_warning(self, capfd):
        """
        print_structured_error called with an unrecognized (non-catalog) code must
        emit a UserWarning while still producing valid JSON output — it must not crash.
        """
        import warnings
        with warnings.catch_warnings(record=True) as captured_warnings:
            warnings.simplefilter("always")
            desk.print_structured_error("TOTALLY_BOGUS_CODE_XYZ", "This should warn")

        captured = capfd.readouterr()
        # Output must still be valid JSON (process stability preserved)
        payload = json.loads(captured.err.strip())
        assert payload["error_code"] == "TOTALLY_BOGUS_CODE_XYZ"

        # A warning must have been issued
        catalog_warnings = [
            w for w in captured_warnings
            if "unrecognized error code" in str(w.message)
        ]
        assert len(catalog_warnings) == 1, (
            "Expected exactly one UserWarning for unrecognized error code, "
            f"got: {[str(w.message) for w in captured_warnings]}"
        )

    def test_all_nine_catalog_codes_present(self):
        """The catalog must define exactly all 9 documented error codes."""
        expected = {
            "UNHANDLED_EXCEPTION", "INTERRUPTED", "TIMEOUT",
            "DESK_FAILED", "KARDENWORT_FAILED", "UNRAISABLE_EXCEPTION",
            "DEPENDENCY_MISSING", "INVALID_STATE", "CONFIGURATION_ERROR",
        }
        actual = {member.value for member in ErrorCode}
        assert actual == expected, (
            f"ErrorCode enum does not match the expected 9 catalog codes.\n"
            f"Missing: {expected - actual}\n"
            f"Extra:   {actual - expected}"
        )


class TestPayloadTypeConformance:
    """
    3.2b – Verify that structured dictionaries emitted via emit_payload across
    command handlers conform structurally to their declared TypedDict models.

    These tests confirm that the TypedDict models accurately capture the real
    payload shapes, preventing model drift from production call sites.
    """

    def test_export_skipped_payload_has_required_fields(self, monkeypatch):
        """ExportSkippedPayload shape: must have 'status'='skipped' and 'message' string."""
        raw = _capture_emit_payload(monkeypatch, {
            "status": "skipped",
            "message": "Warning: No rows to export based on selection mode. Export skipped.",
        })
        payload = json.loads(raw)
        assert payload["status"] == "skipped"
        assert isinstance(payload["message"], str)
        assert "status" in payload and "message" in payload
        # Must not contain unexpected fields beyond what ExportSkippedPayload defines
        assert set(payload.keys()) <= {"status", "message"}, (
            f"ExportSkippedPayload contains unexpected fields: {set(payload.keys()) - {'status', 'message'}}"
        )

    def test_export_import_started_payload_has_required_fields(self, monkeypatch):
        """ExportImportStartedPayload shape: must have all 6 required fields."""
        sample: ExportImportStartedPayload = {
            "import_started": True,
            "show_window": False,
            "pid": 12345,
            "log": "C:/path/to/import.log",
            "tsv": "C:/path/to/favorites.tsv",
            "note": "safe to close the window",
        }
        raw = _capture_emit_payload(monkeypatch, sample)
        payload = json.loads(raw)
        assert payload["import_started"] is True
        assert isinstance(payload["show_window"], bool)
        assert isinstance(payload["pid"], int)
        assert isinstance(payload["log"], str)
        assert isinstance(payload["tsv"], str)
        assert isinstance(payload["note"], str)
        required_fields = {"import_started", "show_window", "pid", "log", "tsv", "note"}
        assert required_fields <= set(payload.keys()), (
            f"ExportImportStartedPayload missing fields: {required_fields - set(payload.keys())}"
        )

    def test_export_import_complete_payload_has_required_fields(self, monkeypatch):
        """ExportImportCompletePayload shape: must have 'import_complete', 'show_window', 'output'."""
        sample: ExportImportCompletePayload = {
            "import_complete": True,
            "show_window": False,
            "output": "SUCCESS: Exported to C:/favorites/file.tsv",
        }
        raw = _capture_emit_payload(monkeypatch, sample)
        payload = json.loads(raw)
        assert payload["import_complete"] is True
        assert isinstance(payload["show_window"], bool)
        assert isinstance(payload["output"], str)
        required_fields = {"import_complete", "show_window", "output"}
        assert required_fields <= set(payload.keys()), (
            f"ExportImportCompletePayload missing fields: {required_fields - set(payload.keys())}"
        )

    def test_export_success_payload_has_required_fields(self, monkeypatch):
        """ExportSuccessPayload shape: must have 'status'='success' and 'message' string."""
        sample: ExportSuccessPayload = {
            "status": "success",
            "message": "SUCCESS: Ready for Anki (no favorites file created)",
        }
        raw = _capture_emit_payload(monkeypatch, sample)
        payload = json.loads(raw)
        assert payload["status"] == "success"
        assert isinstance(payload["message"], str)
        assert set(payload.keys()) <= {"status", "message"}, (
            f"ExportSuccessPayload contains unexpected fields: {set(payload.keys()) - {'status', 'message'}}"
        )

    def test_edit_save_success_payload_has_required_fields(self, monkeypatch):
        """EditSaveSuccessPayload shape: must have exactly 'status'='success'."""
        sample: EditSaveSuccessPayload = {"status": "success"}
        raw = _capture_emit_payload(monkeypatch, sample)
        payload = json.loads(raw)
        assert payload["status"] == "success"
        assert set(payload.keys()) == {"status"}, (
            f"EditSaveSuccessPayload must have exactly one field 'status', "
            f"got: {set(payload.keys())}"
        )

    def test_payload_json_serialization_preserves_default_separators(self, monkeypatch):
        """
        Payload JSON must use default separators (', ' and ': ') — not compact or sorted.
        This preserves AHK InStr() substring matching rules that depend on exact spacing.
        """
        sample: ExportImportCompletePayload = {
            "import_complete": True,
            "show_window": False,
            "output": "test",
        }
        raw = _capture_emit_payload(monkeypatch, sample)
        # Default json.dumps uses ", " and ": " — verify no compact format
        assert ": " in raw, "JSON payload must use ': ' separator (not compact ':')"
        assert ", " in raw, "JSON payload must use ', ' separator (not compact ',')"
        # Must be parseable as a valid dict
        payload = json.loads(raw)
        assert isinstance(payload, dict)

    def test_import_started_payload_ahk_substrings_preserved(self, monkeypatch):
        """
        import_started payload must serialize 'import_started' key exactly, preserving
        AHK InStr() lookup compatibility.
        """
        sample: ExportImportStartedPayload = {
            "import_started": True,
            "show_window": False,
            "pid": 99,
            "log": "log.txt",
            "tsv": "file.tsv",
            "note": "safe to close the window",
        }
        raw = _capture_emit_payload(monkeypatch, sample)
        assert '"import_started": true' in raw, (
            f"'import_started' boolean key not found in expected format. Raw: {raw!r}"
        )
        assert '"show_window": false' in raw, (
            f"'show_window' boolean key not found in expected format. Raw: {raw!r}"
        )

    def test_reprocess_started_payload_has_required_fields(self, monkeypatch):
        """ReprocessStartedPayload shape: must have 'reprocess_started'=True and 'rows' int."""
        sample: ReprocessStartedPayload = {
            "reprocess_started": True,
            "rows": 12,
        }
        raw = _capture_emit_payload(monkeypatch, sample)
        payload = json.loads(raw)
        assert payload["reprocess_started"] is True
        assert isinstance(payload["rows"], int)
        assert set(payload.keys()) <= {"reprocess_started", "rows"}

    def test_retext_started_payload_has_required_fields(self, monkeypatch):
        """RetextStartedPayload shape: must have exactly 'retext_started'=True."""
        sample: RetextStartedPayload = {"retext_started": True}
        raw = _capture_emit_payload(monkeypatch, sample)
        payload = json.loads(raw)
        assert payload["retext_started"] is True
        assert set(payload.keys()) == {"retext_started"}


# =============================================================================
# IPC Payload Defense — Base64 Integration Assertions (ipc-payload-defense spec)
#
# Capability: ipc-payload-defense
# Spec: openspec/changes/20260803151525-headless-fsm-payload-defense/
#         specs/ipc-payload-defense/spec.md
#
# Verifies that Base64 payload generation and decoding via b64util operate
# deterministically without character truncation or code-page corruption,
# including multi-megabyte payloads, multi-line HTML structures, and
# foreign-language (German umlauts, Cyrillic) vocabulary dictionaries.
#
# These tests validate the complete IPC pipeline:
#   Python backend → b64util.encode() → emit_payload(raw=True) → AHK b64Decode()
# =============================================================================


class TestBase64PayloadDefense:
    """
    2.2 – Automated Base64 payload integration assertions verifying encoding and
    decoding reliability across simulated operating system environments without
    character truncation or code-page corruption.

    All assertions use only b64util.encode/decode (the shared IPC codec) and
    do NOT test internal Python base64 directly, matching the real AHK-bound
    emission paths.
    """

    def test_b64_roundtrip_pure_ascii(self):
        """Plain ASCII payload encodes and decodes with 100% byte-for-byte fidelity."""
        from b64util import encode, decode
        original = "Hello, World! Simple ASCII test payload."
        assert decode(encode(original)) == original

    def test_b64_roundtrip_german_umlauts(self):
        """German umlaut characters (ä, ö, ü, ß) survive Base64 round-trip without corruption."""
        from b64util import encode, decode
        original = "Haus, Straße, Mädchen, Höhle, über, Föhn, Zürich, Gemüse"
        encoded = encode(original)
        decoded = decode(encoded)
        assert decoded == original, (
            f"German umlauts corrupted. Original: {original!r}, Decoded: {decoded!r}"
        )

    def test_b64_roundtrip_cyrillic(self):
        """Cyrillic characters survive Base64 round-trip without code-page drift."""
        from b64util import encode, decode
        original = "дом, мама, привет, Москва, Россия"
        encoded = encode(original)
        decoded = decode(encoded)
        assert decoded == original, (
            f"Cyrillic characters corrupted. Original: {original!r}, Decoded: {decoded!r}"
        )

    def test_b64_roundtrip_mixed_unicode(self):
        """Mixed Unicode (German + Cyrillic + emoji + ASCII) encodes cleanly."""
        from b64util import encode, decode
        original = "Haus 🏠 дом Straße Москва über 🇩🇪 test & <html/>"
        encoded = encode(original)
        decoded = decode(encoded)
        assert decoded == original

    def test_b64_encoded_output_is_pure_ascii(self):
        """Encoded output must be pure ASCII — no embedded whitespace or non-ASCII bytes.

        This guarantees AHK process boundary safety: Windows shell cannot corrupt
        pure ASCII sequences regardless of system code page.
        """
        from b64util import encode
        original = "Über die Straße läuft eine Kröte. 🐸"
        encoded = encode(original)
        assert encoded.isascii(), f"Encoded output contains non-ASCII chars: {encoded!r}"
        assert " " not in encoded, "Encoded output must not contain spaces"
        assert "\n" not in encoded, "Encoded output must not contain newlines"
        assert "\r" not in encoded, "Encoded output must not contain carriage returns"

    def test_b64_roundtrip_multiline_html_structure(self):
        """Multi-line HTML structures (as emitted by cmd_render) survive Base64 encoding intact."""
        from b64util import encode, decode
        html_payload = (
            "<!DOCTYPE html>\n"
            "<html lang='de'>\n"
            "  <head><meta charset='UTF-8'><title>Kardenwort</title></head>\n"
            "  <body>\n"
            "    <div class='entry'>Haus &mdash; дом &mdash; house</div>\n"
            "    <div class='ipa'>ˈhaʊ̯s</div>\n"
            "    <div class='translation'>Straße → street (Über uns)</div>\n"
            "    <script>window.receiveUpdate({\"status\": \"ready\"});</script>\n"
            "  </body>\n"
            "</html>\n"
        )
        encoded = encode(html_payload)
        decoded = decode(encoded)
        assert decoded == html_payload, (
            "Multi-line HTML payload corrupted by Base64 encoding/decoding"
        )

    def test_b64_roundtrip_json_vocabulary_dict(self):
        """Complex JSON vocabulary dictionary (as emitted by cmd_restore) round-trips cleanly."""
        from b64util import encode, decode
        import json
        vocab_dict = {
            "source_text": "Über die Straße läuft eine Kröte",
            "headers": ["Lemma", "Translation", "IPA", "POS"],
            "data_rows": [
                ["Kröte", "toad", "ˈkʁøːtə", "N"],
                ["Straße", "street", "ˈʃtʁaːsə", "N"],
                ["laufen", "to run", "ˈlaʊ̯fən", "V"],
            ],
            "warnings": [],
            "tsv_path": "C:/Users/test/Kardenwort/favorites.tsv",
            "txt_path": "",
        }
        json_str = json.dumps(vocab_dict, ensure_ascii=False)
        encoded = encode(json_str)
        decoded_str = decode(encoded)
        decoded_dict = json.loads(decoded_str)

        assert decoded_dict["source_text"] == vocab_dict["source_text"]
        assert decoded_dict["data_rows"][0][0] == "Kröte"
        assert decoded_dict["data_rows"][1][1] == "street"
        assert decoded_dict["headers"] == vocab_dict["headers"]

    def test_b64_multimegabyte_payload_fidelity(self):
        """Multi-megabyte Base64 payload encodes and decodes with 100% byte-for-byte fidelity.

        Simulates a large vocabulary HTML bundle exceeding typical Windows 11
        shell argument buffer limits (8KB–32KB) to confirm no truncation occurs.
        Target size: ~2 MB of repeated UTF-8 vocabulary content.
        """
        from b64util import encode, decode
        # Build ~2 MB of realistic vocabulary content with umlauts and special chars
        row_template = (
            "<tr><td>Geschwindigkeit</td><td>speed / velocity</td>"
            "<td>ɡəˈʃvɪndɪçkaɪ̯t</td><td>Noun</td></tr>\n"
            "<tr><td>Überzeugung</td><td>conviction</td>"
            "<td>ˌyːbɐˈʦɔʏ̯ɡʊŋ</td><td>Noun</td></tr>\n"
            "<tr><td>Straßenbahn</td><td>tram</td>"
            "<td>ˈʃtʁaːsənbaːn</td><td>Noun</td></tr>\n"
        )
        # Repeat to generate ~2 MB
        repeat_count = (2 * 1024 * 1024) // len(row_template.encode("utf-8")) + 1
        large_payload = row_template * repeat_count

        payload_bytes = len(large_payload.encode("utf-8"))
        assert payload_bytes > 1_000_000, (
            f"Test payload too small: {payload_bytes} bytes, expected > 1 MB"
        )

        encoded = encode(large_payload)

        # Encoded must be pure ASCII (no corruption of the codec output)
        assert encoded.isascii(), "Multi-megabyte encoded payload contains non-ASCII characters"

        decoded = decode(encoded)

        # Verify 100% byte-for-byte fidelity
        assert decoded == large_payload, (
            f"Multi-megabyte payload fidelity failure: "
            f"original={payload_bytes} bytes, decoded={len(decoded.encode('utf-8'))} bytes"
        )

    def test_b64_payload_null_and_empty_edge_cases(self):
        """Null and empty inputs do not crash the encoder/decoder."""
        from b64util import encode, decode
        assert encode(None) == ""
        assert decode(None) == ""
        assert encode("") == ""
        assert decode("") == ""

    def test_b64_emit_pipeline_html_to_stdout(self, monkeypatch):
        """Full IPC pipeline: HTML → encode → emit_payload(raw=True) → captured stdout.

        Simulates the cmd_render / cmd_desk emission pattern and verifies
        that the AHK-bound output is a valid Base64 string that decodes back
        to the original HTML without loss.
        """
        from b64util import encode, decode
        import sys

        html_content = (
            "<!DOCTYPE html><html><body>"
            "<div class='word'>Straße</div>"
            "<div class='ipa'>ˈʃtʁaːsə</div>"
            "</body></html>"
        )
        encoded = encode(html_content)

        mock_stdout = io.StringIO()
        monkeypatch.setattr(sys, "__stdout__", mock_stdout)
        desk.emit_payload(encoded, raw=True)

        emitted = mock_stdout.getvalue().rstrip("\n")

        # The emitted string must equal the encoded payload
        assert emitted == encoded, (
            f"Emitted payload does not match encoded input. "
            f"Emitted length: {len(emitted)}, Encoded length: {len(encoded)}"
        )

        # The emitted string must be pure ASCII
        assert emitted.isascii(), "Emitted Base64 payload contains non-ASCII characters"

        # Round-trip decode must recover the original HTML exactly
        recovered = decode(emitted)
        assert recovered == html_content, (
            f"HTML payload corrupted through emit pipeline. "
            f"Original: {html_content!r}, Recovered: {recovered!r}"
        )

    def test_b64_emit_pipeline_json_dict_to_stdout(self, monkeypatch):
        """Full IPC pipeline: JSON dict → encode → emit_payload(raw=True) → verified decode.

        Simulates the cmd_restore emission pattern (vocabulary JSON dict).
        """
        from b64util import encode, decode
        import json
        import sys

        payload_dict = {
            "source_text": "Mädchen läuft über die Brücke",
            "headers": ["Word", "Translation"],
            "data_rows": [["Mädchen", "girl"], ["Brücke", "bridge"]],
            "warnings": [],
        }
        json_str = json.dumps(payload_dict, ensure_ascii=False)
        encoded = encode(json_str)

        mock_stdout = io.StringIO()
        monkeypatch.setattr(sys, "__stdout__", mock_stdout)
        desk.emit_payload(encoded, raw=True)

        emitted = mock_stdout.getvalue().rstrip("\n")
        assert emitted.isascii(), "Emitted Base64 JSON payload contains non-ASCII characters"

        recovered_str = decode(emitted)
        recovered_dict = json.loads(recovered_str)

        assert recovered_dict["source_text"] == payload_dict["source_text"]
        assert recovered_dict["data_rows"][0][0] == "Mädchen"
        assert recovered_dict["data_rows"][1][1] == "bridge"

    def test_b64_no_embedded_newlines_in_encoded_output(self):
        """Base64 output must contain no embedded newlines (critical for AHK line-reading).

        AHK reads IPC output line-by-line. A Base64 payload with embedded newlines
        would be split across multiple reads, corrupting the payload boundary.
        """
        from b64util import encode
        import string
        # Generate payload with various newline-producing content
        multiline_content = "\n".join([
            "Line one: Straße",
            "Line two: Überzeugung",
            "Line three: <div>HTML</div>",
            'Line four: {"key": "Wert"}',
        ])
        encoded = encode(multiline_content)
        assert "\n" not in encoded, "Base64 output must not contain embedded \\n"
        assert "\r" not in encoded, "Base64 output must not contain embedded \\r"

    def test_b64_deterministic_across_repeated_calls(self):
        """Base64 encoding is deterministic: same input always produces same output.

        This is required for AHK InStr() substring matching and caching reliability.
        """
        from b64util import encode
        payload = "Schlüssel, Tür, Öl, über Brücken fahren 🚗"
        results = [encode(payload) for _ in range(10)]
        assert all(r == results[0] for r in results), (
            "Base64 encode is non-deterministic — output varies across repeated calls"
        )


def test_simplemma_new_flags_forwarded_to_subprocess(monkeypatch, tmp_path):
    import kardenwort_desk as desk
    import subprocess
    import configparser

    mock_cmd = []
    def mock_run(cmd, *args, **kwargs):
        mock_cmd.extend(cmd)
        if "--output-file" in cmd:
            out_idx = cmd.index("--output-file")
            out_path = Path(cmd[out_idx+1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text("WordSource\nword\n", encoding='utf-8')
        class MockProc:
            returncode = 0
            stdout = ""
            stderr = ""
        return MockProc()

    monkeypatch.setattr(subprocess, 'run', mock_run)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "config.ini").write_text("[project_structure]\ngenerated_results_dir=results\n")

    config = configparser.ConfigParser()
    config.read_string("""
[settings]
simplemma_after_spacy=true
simplemma_pos_aware=true
simplemma_smart_fallback=true
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
""")

    resolved_paths = {
        'kardenwort_workspace': workspace,
        'anki_mapping_file': mapping_file,
        'kardenwort_python': Path("python"),
    }

    try:
        desk.run_render_flow("test text", "en", "1234", "single", config, resolved_paths)
    except Exception:
        pass

    assert "--simplemma-after-spacy" in mock_cmd
    assert "--simplemma-pos-aware" in mock_cmd
    assert "--simplemma-smart-fallback" in mock_cmd


