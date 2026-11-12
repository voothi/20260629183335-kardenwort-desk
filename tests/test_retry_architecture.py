"""Keep the repaired retry planning, storage and UI boundaries explicit."""
import ast
from pathlib import Path
import re

from tests.test_intent_architecture import complexity, functions
from tests.test_preference_architecture import imports
from tests.test_preference_js_structure import function_body

ROOT = Path(__file__).resolve().parents[1]


def test_retry_alignment_core_stays_pure():
    tree = ast.parse((ROOT/'kardenwort_alignment.py').read_text(encoding='utf-8'))
    assert imports(tree) == set()
    calls = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert not calls.intersection({'open', 'exec', 'eval', '__import__', 'print', 'input'})


def test_repair_functions_stay_within_branch_ceiling():
    slices = {
        'kardenwort_alignment.py': {'unresolved_sentences', 'align_retry_sentences'},
        'kardenwort_controller.py': {'_prepare_sqlite_retry_text', '_commit_sqlite_retry'},
        'kardenwort_desk.py': {'require_import_lemma', 'persist_retext'},
        'kardenwort_db.py': {'_wal_size'},
    }
    for filename, names in slices.items():
        tree = ast.parse((ROOT/filename).read_text(encoding='utf-8'))
        measured = {node.name: complexity(node) for node in functions(tree) if node.name in names}
        assert set(measured) == names
        assert max(measured.values()) <= 7, measured


def test_clipboard_projection_stays_within_branch_ceiling():
    body = function_body((ROOT/'assets/admin.js').read_text(encoding='utf-8'), 'copyInspectorText')
    assert 1+len(re.findall(r'\b(?:if|for|while|case|catch)\b|&&|\|\||\?', body)) <= 7
