"""Keep the identity core pure and its branch complexity bounded."""
import ast
from pathlib import Path

from tests.test_intent_architecture import complexity, functions


def test_reword_core_has_only_pure_dependencies():
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'kardenwort_reword.py').read_text(encoding='utf-8'))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imports == {'dataclasses'}
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
    forbidden = {'open', 'exec', 'eval', '__import__', 'print', 'input'}
    calls = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert not calls.intersection(forbidden)


def test_reword_core_complexity_ceiling():
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'kardenwort_reword.py').read_text(encoding='utf-8'))
    measured = {node.name: complexity(node) for node in functions(tree)}
    assert max(measured.values()) <= 7, measured
