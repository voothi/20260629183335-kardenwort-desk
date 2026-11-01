"""Comparison failure evidence and tooling boundaries remain reviewable."""
import ast
import configparser
from pathlib import Path

import pytest

from scripts.release_audit_sources import scrub_credentials
from tests.test_intent_architecture import complexity, functions
from tests.test_release_comparison_identity import comparison_cli, execution


def test_comparison_redaction_preserves_linguistic_token_options():
    config = configparser.ConfigParser()
    config.read_dict({'provider': {'api_key': 'owned-fixture-secret'},
                      'token_mappings': {'lemmatize_mapped_tokens': 'true'}})
    scrub_credentials(config)
    assert config.get('provider', 'api_key') == ''
    assert config.getboolean('token_mappings', 'lemmatize_mapped_tokens') is True


def test_failed_renderer_retains_non_success_execution_and_original_ref(comparison_cli):
    repository, output, run = comparison_cli
    with (repository / 'kardenwort_reword.py').open('a', encoding='utf-8') as source:
        source.write('\nthis is invalid python syntax\n')
    result = run('--working-tree')
    assert result.returncode != 0
    record = execution(output)
    assert record['exit_code'] != 0
    assert record['target']['mode'] == 'working-tree'
    assert record['target']['source_hashes']['kardenwort_reword.py']
    assert record['target']['exit_code'] != 0
    assert (output / 'target/worker.log').is_file()


def test_unknown_baseline_is_not_substituted(comparison_cli):
    _, output, run = comparison_cli
    result = run('--baseline-ref', 'missing-baseline-reference', '--target-ref', 'bf498c3')
    assert result.returncode != 0
    assert 'missing-baseline-reference' in result.stdout + result.stderr
    assert not list(output.glob('*.png'))


def test_new_comparison_and_queue_helpers_keep_complexity_bounded():
    root = Path(__file__).resolve().parents[1]
    for file in ('scripts/release_audit_sources.py', 'kardenwort_events.py'):
        measured = {node.name: complexity(node) for node in functions(ast.parse((root / file).read_text(encoding='utf-8')))}
        assert max(measured.values()) <= 7, measured
