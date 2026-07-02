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
    assert "laufen |  | to run" in text_out
    assert "schnell | adj | fast" in text_out

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
