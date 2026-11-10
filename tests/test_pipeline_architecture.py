"""Canonical structured boundaries remain inward, acyclic and bounded."""
import ast
from pathlib import Path

from tests.test_intent_architecture import complexity, functions
from tests.test_preference_architecture import imports


def test_pipeline_boundaries_have_no_reverse_or_file_protocol_dependencies():
    root = Path(__file__).resolve().parents[1]
    allowed = {'kardenwort_enrichment': {'kardenwort_reword'},
               'kardenwort_extraction': {'json'}, 'kardenwort_provider_state': set()}
    for name, dependencies in allowed.items():
        tree = ast.parse((root/(name+'.py')).read_text(encoding='utf-8'))
        assert imports(tree) == dependencies
        assert all(complexity(node) <= 7 for node in functions(tree))
        calls = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name)}
        assert not calls.intersection({'open', 'exec', 'eval', '__import__'})


def test_canonical_enrichment_has_no_headless_or_file_calls():
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root/'kardenwort_desk.py').read_text(encoding='utf-8'))
    method = next(node for node in functions(tree) if node.name == 'enrich_session_intellifiller')
    calls = {node.func.id for node in ast.walk(method) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)}
    assert not calls.intersection({'Path', 'run_headless_intellifiller', 'save_tsv_rows_safely', 'load_tsv_rows'})
    assert 'enrich_rows' in calls and 'commit_reword_sqlite' in calls


def test_new_shell_functions_keep_bounded_branch_complexity():
    root = Path(__file__).resolve().parents[1]
    slices = {
        'kardenwort_desk.py': {'enrich_session_intellifiller', 'sqlite_provider_database', 'storage_backend_setting'},
        'kardenwort_controller.py': {'committed_delivery', '_execute_enrich_lemma'},
    }
    for filename, names in slices.items():
        tree = ast.parse((root/filename).read_text(encoding='utf-8'))
        measured = {node.name: complexity(node) for node in functions(tree) if node.name in names}
        assert set(measured) == names
        assert max(measured.values()) <= 7, measured
