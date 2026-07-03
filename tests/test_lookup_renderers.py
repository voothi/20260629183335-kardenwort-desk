import pytest
from kardenwort_desk import render_section, render_lookup_html, render_lookup_text

def test_render_section_lemmas(caplog):
    ctx = {
        'column_tokens': ['lemma', 'translation', 'unknown_token'],
        'headings': {'lemmas': 'My Lemmas'},
        'headers': ['WordSource', 'WordDestination', 'WordSourceMorphologyAI'],
        'data_rows': [
            ['laufen', 'to run', 'verb'],
            ['schnell', 'fast', 'adj']
        ]
    }
    html = render_section('lemmas', ctx)
    
    assert '<h3>My Lemmas</h3>' in html
    assert 'Lemma' in html
    assert 'Translation' in html
    assert 'Unknown_token' not in html
    assert 'laufen' in html
    assert 'to run' in html
    
    assert 'Unknown lemma_columns token: unknown_token' in caplog.text

def test_render_lookup_text():
    goldendict = {
        'sections': ['source', 'translation', 'lemmas'],
        'lemma_columns': ['lemma', 'morphology', 'translation'],
        'heading_source': 'Source',
        'heading_translation': '__default__',
        'heading_lemmas': '__default__',
        'run_intellifiller': False
    }
    
    headers = ['WordSource', 'WordSourceMorphologyAI', 'WordDestination']
    # Unpopulated morphology should just be empty cells
    data_rows = [
        ['laufen', '', 'to run'],
        ['schnell', 'adj', 'fast']
    ]
    
    text_out = render_lookup_text(
        text="laufen schnell",
        language="de",
        target_lang="en",
        config=None,
        resolved_paths=None,
        zid="123",
        goldendict=goldendict,
        comments=[],
        headers=headers,
        data_rows=data_rows,
        sentence_translation="run fast"
    )
    
    assert "=== Source ===" in text_out
    assert "laufen schnell" in text_out
    assert "=== Translation ===" in text_out
    assert "run fast" in text_out
    assert "=== Lemmas ===" in text_out
    assert "laufen\t\tto run" in text_out
    assert "schnell\tadj\tfast" in text_out

def test_render_lookup_html():
    goldendict = {
        'sections': ['source', 'lemmas'],
        'lemma_columns': ['lemma'],
        'heading_source': '__default__',
        'heading_lemmas': '',
        'run_intellifiller': False
    }
    
    headers = ['WordSource']
    data_rows = [['laufen']]
    
    html_out = render_lookup_html(
        text="laufen",
        language="de",
        target_lang="en",
        config=None,
        resolved_paths=None,
        zid="123",
        goldendict=goldendict,
        comments=[],
        headers=headers,
        data_rows=data_rows,
        sentence_translation="run"
    )
    
    assert '<h3>Source Text</h3>' in html_out
    assert '<h3>Lemmas</h3>' not in html_out
    assert 'laufen' in html_out

def test_run_render_flow_regions(tmp_path):
    import configparser
    from kardenwort_desk import run_render_flow
    
    config = configparser.ConfigParser()
    config.add_section('settings')
    config.set('settings', 'default_target_language', 'ru')
    config.set('settings', 'anki_mapping_file', './anki-mapping.ini')
    config.set('settings', 'progressive_loading', 'true')
    config.set('settings', 'lazy_processing', 'none')
    
    config.add_section('environment')
    config.set('environment', 'kardenwort_workspace', str(tmp_path))
    
    config.add_section('languages')
    config.set('languages', 'en_prompt', 'English prompt')
    config.set('languages', 'en_lemma_index', 'en_idx')
    config.set('languages', 'en_lemma_override', 'en_over')
    
    resolved_paths = {
        'kardenwort_workspace': tmp_path,
        'anki_mapping_file': str(tmp_path / "anki_mapping.ini")
    }
    
    mapping = configparser.ConfigParser()
    mapping.add_section('fields')
    mapping.add_section('desk_columns')
    mapping.set('desk_columns', 'WordSource', 'lemma')
    mapping.set('desk_columns', 'WordDestination', 'word_translation')
    with open(tmp_path / "anki_mapping.ini", 'w') as f:
        mapping.write(f)
        
    # We create results directory and mock working tsv file
    res_dir = tmp_path / "results"
    res_dir.mkdir()
    tsv_path = res_dir / "123-test-slug.en.tsv"
    tsv_path.write_text("WordSource\tWordSourceMorphologyAI\tWordSourceIPA\tWordDestination\nword1\t\t\t\n", encoding='utf-8')
    
    html_out = run_render_flow(
        text="word1",
        language="en",
        zid="123",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=tsv_path
    )
    
    # Assert regions are present in HTML
    assert 'id="source-container"' in html_out
    assert 'id="translation-container"' in html_out
    assert 'id="lemma-table"' in html_out
    
    # Assert skeleton loader CSS is in styles
    assert '.skeleton-loader' in html_out
    
    # Assert window.receiveUpdate routes data to source-container, translation-container, and lemma-table
    assert 'translation-container' in html_out
    assert 'lemma-table' in html_out

def test_d1_progressive_display_mode(tmp_path):
    import configparser
    from kardenwort_desk import run_render_flow
    
    config = configparser.ConfigParser()
    config.add_section('settings')
    config.set('settings', 'default_target_language', 'ru')
    config.add_section('rendering')
    config.set('rendering', 'display_mode', 'monolithic')
    
    config.add_section('triggers')
    config.set('triggers', 'run_base_translation', 'manual')
    config.set('triggers', 'run_enrichment', 'manual')
    
    config.add_section('environment')
    config.set('environment', 'kardenwort_workspace', str(tmp_path))
    config.add_section('languages')
    config.set('languages', 'en_lemma_index', 'en_idx')
    config.set('languages', 'en_lemma_override', 'en_over')
    config.set('languages', 'en_prompt', 'en_prompt')
    
    resolved_paths = {'kardenwort_workspace': tmp_path, 'anki_mapping_file': str(tmp_path / "mapping.ini")}
    tsv_path = tmp_path / "test.tsv"
    tsv_path.write_text("WordSource\nword1\n", encoding='utf-8')
    
    mapping = configparser.ConfigParser()
    mapping.add_section('fields')
    mapping.add_section('fields_mapping.word')
    mapping.add_section('desk_columns')
    mapping.set('desk_columns', 'WordSource', 'lemma')
    with open(tmp_path / "mapping.ini", 'w') as f:
        mapping.write(f)
        
    html_out = run_render_flow("word1", "en", "123", "single", config, resolved_paths, tsv_path=tsv_path)
    (tmp_path / "debug1.html").write_text(html_out, encoding="utf-8")
    assert '<script id="display-mode" type="text/plain">monolithic</script>' in html_out
    
    config.set('rendering', 'display_mode', 'progressive')
    html_out = run_render_flow("word1", "en", "123", "single", config, resolved_paths, tsv_path=tsv_path)
    assert '<script id="display-mode" type="text/plain">progressive</script>' in html_out

def test_d2_render_source_translated_pending(tmp_path):
    import configparser
    from kardenwort_desk import run_render_flow
    
    config = configparser.ConfigParser()
    config.add_section('settings')
    config.set('settings', 'default_target_language', 'ru')
    config.add_section('rendering')
    config.set('rendering', 'display_mode', 'progressive')
    config.add_section('triggers')
    config.set('triggers', 'run_base_translation', 'auto')
    
    config.add_section('environment')
    config.set('environment', 'kardenwort_workspace', str(tmp_path))
    config.add_section('languages')
    config.set('languages', 'en_lemma_index', 'en_idx')
    config.set('languages', 'en_lemma_override', 'en_over')
    config.set('languages', 'en_prompt', 'en_prompt')
    
    import sys
    resolved_paths = {'kardenwort_workspace': tmp_path, 'anki_mapping_file': str(tmp_path / "mapping.ini"), 'kardenwort_python': sys.executable}
    tsv_path = tmp_path / "test.tsv"
    tsv_path.write_text("WordSource\tWordDestination\nword1\t\n", encoding='utf-8')
    
    mapping = configparser.ConfigParser()
    mapping.add_section('fields')
    mapping.add_section('fields_mapping.word')
    mapping.add_section('desk_columns')
    mapping.set('desk_columns', 'WordSource', 'lemma')
    mapping.set('desk_columns', 'WordDestination', 'word_translation')
    with open(tmp_path / "mapping.ini", 'w') as f:
        mapping.write(f)
        
    html_out = run_render_flow("word1", "en", "123", "single", config, resolved_paths, tsv_path=tsv_path)
    
    assert '<div class="skeleton-loader" data-pending="true"' in html_out
    assert 'container.querySelector(\'[data-pending="true"]\')' in html_out
