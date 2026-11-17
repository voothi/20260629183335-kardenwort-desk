"""Every extracted acceptance helper participates in architectural governance."""
import ast
from pathlib import Path
from tests.test_intent_architecture import complexity, functions

ROOT = Path(__file__).resolve().parents[1]


def test_completion_core_is_pure_and_all_functions_are_bounded():
    tree = ast.parse((ROOT/'kardenwort_progressive_completion.py').read_text())
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imports == {'dataclasses'}
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
    measured = {node.name: complexity(node) for node in functions(tree)}
    assert measured and max(measured.values()) <= 7, measured
    calls = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert not calls.intersection({'open', 'exec', 'eval', '__import__', 'print'})


def test_all_repaired_completion_and_wordfill_shell_helpers_are_bounded():
    tree = ast.parse((ROOT/'kardenwort_controller.py').read_text(encoding='utf-8-sig'))
    prefixes = ('_completion_', '_complete_progressive_', '_accept_completion', '_publish_completion_',
        '_persist_completion_', '_finish_completion_', '_cache_completion_', '_deliver_completion',
        '_record_completion_', '_publish_current_completion', '_reject_unstable_completion', '_mark_unstable_completion_', '_write_completion_', '_restore_completion', '_deleted_completion_', '_sqlite_completion_',
        '_legacy_completion_', '_collect_wordfill_', '_apply_wordfill_', '_wordfill_',
        '_run_wordfill_', '_persist_wordfill_', '_publish_progressive_final_state')
    measured = {node.name: complexity(node) for node in functions(tree) if node.name.startswith(prefixes)}
    assert {'_collect_wordfill_row_updates', '_run_wordfill_stage', '_publish_progressive_final_state'} <= measured.keys()
    assert max(measured.values()) <= 7, measured
