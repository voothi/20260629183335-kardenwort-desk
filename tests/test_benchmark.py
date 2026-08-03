from kardenwort_desk import (
    SEC_SETTINGS, SEC_TOKEN_MAPPINGS, SEC_MERGE, SEC_SENTENCES_MODE,
    SEC_CLASSIFICATION, SEC_TIMEOUTS, SEC_PIPELINE, SEC_TRIGGERS,
    SEC_TRANSLATION, SEC_TRANSLATION_PROVIDERS, SEC_RENDERING,
    SEC_ENVIRONMENT, SEC_LANGUAGES, SEC_LANGUAGE_RESOURCES,
    SEC_PROJECT_STRUCTURE, SEC_AUDIO, SEC_GOLDENDICT, SEC_WORDFILL
)
import time
import pytest
import configparser
from pathlib import Path
import tempfile
import kardenwort_desk

def test_benchmark_surfacing_latency(tmp_path):
    # Setup bench environment
    config = configparser.ConfigParser()
    config.add_section(SEC_SETTINGS)
    config.set(SEC_SETTINGS, 'default_target_language', 'ru')
    config.set(SEC_SETTINGS, 'anki_mapping_file', './anki-mapping.ini')
    config.add_section(SEC_RENDERING)
    config.set(SEC_RENDERING, 'display_mode', 'progressive')
    config.add_section(SEC_TRIGGERS)
    config.set(SEC_TRIGGERS, 'run_base_translation', 'auto')
    config.set(SEC_TRIGGERS, 'run_enrichment', 'auto')
    config.add_section(SEC_ENVIRONMENT)
    config.set(SEC_ENVIRONMENT, 'kardenwort_workspace', str(tmp_path))
    config.add_section(SEC_LANGUAGES)
    config.set(SEC_LANGUAGES, 'en_prompt', 'English prompt')
    config.set(SEC_LANGUAGES, 'en_lemma_index', 'en_idx')
    config.set(SEC_LANGUAGES, 'en_lemma_override', 'en_over')
    
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
        
    tsv_path = tmp_path / "results" / "test_bench.tsv"
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    tsv_path.write_text("WordSource\tWordSourceMorphologyAI\tWordSourceIPA\tWordDestination\nword1\t\t\t\n", encoding='utf-8')
    
    # We measure time to write update.js
    headers = ['WordSource', 'WordSourceMorphologyAI', 'WordSourceIPA', 'WordDestination']
    data_rows = [['word1', '', '', '']]
    role_fields = {'lemma': 'WordSource', 'word_translation': 'WordDestination', 'morphology': 'WordSourceMorphologyAI', 'ipa': 'WordSourceIPA'}
    
    start_time = time.perf_counter()
    kardenwort_desk.write_update_js(tsv_path, data_rows, headers, role_fields, stage="source")
    end_time = time.perf_counter()
    
    latency_ms = (end_time - start_time) * 1000
    print(f"\nProgressive write_update_js latency: {latency_ms:.2f}ms")
    
    # Perceived time-to-render of each stage from data readiness is basically the write latency
    # plus the poll interval (200ms). The sum should be well below 500ms.
    # We assert that the write latency itself is under 50ms (well under the 500ms target).
    assert latency_ms < 50.0
