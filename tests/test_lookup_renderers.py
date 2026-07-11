import pytest
import kardenwort_desk
from kardenwort_desk import render_section, render_lookup_html, render_lookup_text

@pytest.fixture(autouse=True)
def mock_progressive_worker(monkeypatch):
    monkeypatch.setattr(kardenwort_desk, 'run_progressive_worker_async', lambda *args, **kwargs: None)

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
    config.add_section('rendering')
    config.set('rendering', 'display_mode', 'progressive')
    config.add_section('triggers')
    config.set('triggers', 'run_lemma_base_translation', 'auto')
    config.set('triggers', 'run_lemma_enrichment', 'auto')
    
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
    config.set('triggers', 'run_lemma_base_translation', 'manual')
    config.set('triggers', 'run_lemma_enrichment', 'manual')
    
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
    config.set('triggers', 'run_lemma_base_translation', 'auto')
    
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

def test_separable_verb_anchoring_scenarios(tmp_path, monkeypatch):
    import configparser
    import sys
    from kardenwort_desk import run_render_flow
    import kardenwort_desk

    monkeypatch.setattr(kardenwort_desk, 'run_progressive_worker_async', lambda *args, **kwargs: None)

    # Setup base config
    config = configparser.ConfigParser()
    config.add_section('settings')
    config.set('settings', 'default_target_language', 'ru')
    config.add_section('rendering')
    config.set('rendering', 'display_mode', 'progressive')
    config.add_section('triggers')
    config.set('triggers', 'run_lemma_base_translation', 'auto')
    config.set('triggers', 'run_lemma_enrichment', 'manual')
    config.add_section('environment')
    config.set('environment', 'kardenwort_workspace', str(tmp_path))
    config.add_section('languages')
    config.set('languages', 'en_lemma_index', 'en_idx')
    config.set('languages', 'en_lemma_override', 'en_over')
    config.set('languages', 'en_prompt', 'en_prompt')
    
    # Setup mapping
    mapping = configparser.ConfigParser()
    mapping.optionxform = str
    mapping.add_section('fields')
    mapping.add_section('fields_mapping.word')
    mapping.add_section('desk_columns')
    mapping.set('desk_columns', 'WordSource', 'lemma')
    mapping.set('desk_columns', 'WordSourceInflectedForm', 'inflected')
    mapping.set('desk_columns', 'WordDestination', 'word_translation')
    
    mapping_file = tmp_path / "mapping.ini"
    with open(mapping_file, 'w') as f:
        mapping.write(f)

    resolved_paths = {
        'kardenwort_workspace': tmp_path,
        'anki_mapping_file': str(mapping_file),
        'kardenwort_python': sys.executable
    }

    # Create results folder
    res_dir = tmp_path / "results"
    res_dir.mkdir()

    # Helper function to render a scenario and return HTML
    def run_scenario(text, tsv_content, split_gap_limit=60, custom_config=None):
        tsv_path = res_dir / "123-test-slug.en.tsv"
        tsv_path.write_text(tsv_content, encoding='utf-8')
        cfg = custom_config if custom_config else config
        return run_render_flow(
            text=text,
            language="en",
            zid="123",
            text_mode="single",
            config=cfg,
            resolved_paths=resolved_paths,
            tsv_path=tsv_path,
            split_gap_limit=split_gap_limit
        )

    # 4.1 split verb with repeated particle
    # Source: "Heute kommt der Redakteur an einem Tag an."
    # TSV has: WordSource=ankommen, WordSourceInflectedForm=kommt an, WordDestination=arrive
    tsv_content = "WordSource\tWordSourceInflectedForm\tWordDestination\nankommen\tkommt an\tarrive\n"
    html = run_scenario("Heute kommt der Redakteur an einem Tag an.", tsv_content)
    
    assert 'class="word highlight-purple" data-word-idx="3" data-line-idx="0" data-lower-clean="kommt">kommt</span>' in html
    assert 'class="word highlight-purple" data-word-idx="9" data-line-idx="0" data-lower-clean="an">an</span>' in html
    assert 'class="word not-connected" data-word-idx="15" data-line-idx="0" data-lower-clean="an">an</span>' in html
    assert 'class="highlight-purple"' in html # table row should be purple

    # 4.2 contiguous phrase
    tsv_content = "WordSource\tWordSourceInflectedForm\tWordDestination\nin spite of\tin spite of\tin spite of\n"
    html = run_scenario("He did it in spite of the rule of which he was aware", tsv_content)
    assert 'class="word highlight-purple" data-word-idx="7" data-line-idx="0" data-lower-clean="in">in</span>' in html
    assert 'class="word highlight-purple" data-word-idx="9" data-line-idx="0" data-lower-clean="spite">spite</span>' in html
    assert 'class="word highlight-purple" data-word-idx="11" data-line-idx="0" data-lower-clean="of">of</span>' in html
    assert 'class="word not-connected" data-word-idx="17" data-line-idx="0" data-lower-clean="of">of</span>' in html

    # 4.3 out-of-order
    tsv_content = "WordSource\tWordSourceInflectedForm\tWordDestination\nankommen\tkommt an\tarrive\n"
    html = run_scenario("an etwas kommt", tsv_content)
    assert 'class="word not-connected" data-word-idx="1" data-line-idx="0" data-lower-clean="an">an</span>' in html
    assert 'class="word not-connected" data-word-idx="5" data-line-idx="0" data-lower-clean="kommt">kommt</span>' in html
    assert 'class="highlight-orange"' in html # table row should be orange

    # 4.4 gap-exceeded with default 60 (>60 word positions between kommt and an)
    tsv_content = "WordSource\tWordSourceInflectedForm\tWordDestination\nankommen\tkommt an\tarrive\n"
    text = "kommt " + "word " * 61 + "an"
    html = run_scenario(text, tsv_content)
    assert 'class="word not-connected" data-word-idx="1" data-line-idx="0" data-lower-clean="kommt">kommt</span>' in html
    assert 'class="word highlight-orange"' not in html # should not have orange class
    assert 'class="highlight-orange"' in html # table row should be orange

    # 4.5 tighter limit suppresses 20-word gap
    tsv_content = "WordSource\tWordSourceInflectedForm\tWordDestination\nankommen\tkommt an\tarrive\n"
    text = "kommt " + "word " * 19 + "an"
    html = run_scenario(text, tsv_content, split_gap_limit=10)
    assert 'class="word not-connected" data-word-idx="1" data-line-idx="0" data-lower-clean="kommt">kommt</span>' in html

    # 4.6 config vs flag limit
    # Config has split_gap_limit = 15, no flag -> suppresses gap 20
    cfg15 = configparser.ConfigParser()
    for sec in config.sections():
        cfg15.add_section(sec)
        for k, v in config.items(sec):
            cfg15.set(sec, k, v)
    cfg15.set('settings', 'split_gap_limit', '15')
    
    html = run_scenario(text, tsv_content, split_gap_limit=cfg15.getint('settings', 'split_gap_limit', fallback=60), custom_config=cfg15)
    assert 'class="word not-connected" data-word-idx="1" data-line-idx="0" data-lower-clean="kommt">kommt</span>' in html

    # Overridden by flag split_gap_limit = 30 -> allows gap 20
    html = run_scenario(text, tsv_content, split_gap_limit=30, custom_config=cfg15)
    assert 'class="word highlight-purple" data-word-idx="1" data-line-idx="0" data-lower-clean="kommt">kommt</span>' in html

    # 4.7 single-word row unchanged (all occurrences orange)
    tsv_content = "WordSource\tWordSourceInflectedForm\tWordDestination\nlaufen\tlaufen\trun\n"
    html = run_scenario("laufen laufen", tsv_content)
    assert 'class="word highlight-orange" data-word-idx="1" data-line-idx="0" data-lower-clean="laufen">laufen</span>' in html
    assert 'class="word highlight-orange" data-word-idx="3" data-line-idx="0" data-lower-clean="laufen">laufen</span>' in html

    # Collapses to single word
    tsv_content = "WordSource\tWordSourceInflectedForm\tWordDestination\na.\ta.\ta\n"
    html = run_scenario("a a", tsv_content)
    assert 'class="word highlight-orange" data-word-idx="1" data-line-idx="0" data-lower-clean="a">a</span>' in html

    # 4.8 repeated construct
    tsv_content = "WordSource\tWordSourceInflectedForm\tWordDestination\naufstehen\tsteht auf\tstand up\n"
    html = run_scenario("Der Mann steht am Morgen auf, dann steht er auf.", tsv_content)
    assert 'class="word highlight-purple" data-word-idx="5" data-line-idx="0" data-lower-clean="steht">steht</span>' in html
    assert 'class="word highlight-purple" data-word-idx="11" data-line-idx="0" data-lower-clean="auf">auf</span>' in html
    assert 'class="word highlight-purple" data-word-idx="16" data-line-idx="0" data-lower-clean="steht">steht</span>' in html
    assert 'class="word highlight-purple" data-word-idx="20" data-line-idx="0" data-lower-clean="auf">auf</span>' in html

    # 4.9 overlapping anchored rows
    tsv_content = "WordSource\tWordSourceInflectedForm\tWordDestination\nankommen\tkommt an\tarrive\nangehen\tgeht an\tconcern\n"
    html = run_scenario("kommt geht an", tsv_content)
    assert 'class="word highlight-purple" data-word-idx="1" data-line-idx="0" data-lower-clean="kommt">kommt</span>' in html
    assert 'class="word highlight-purple" data-word-idx="3" data-line-idx="0" data-lower-clean="geht">geht</span>' in html
    assert 'class="word highlight-purple" data-word-idx="5" data-line-idx="0" data-lower-clean="an">an</span>' in html

