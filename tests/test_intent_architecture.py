"""Architectural contracts for the repaired translation and delivery boundaries."""
import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def functions(tree):
    return [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]


def complexity(node):
    count = 1
    for child in ast.walk(node):
        count += isinstance(child, (ast.If, ast.For, ast.While, ast.ExceptHandler, ast.IfExp))
        if isinstance(child, ast.BoolOp):
            count += len(child.values) - 1
        if isinstance(child, ast.comprehension):
            count += 1 + len(child.ifs)
    return count


@pytest.mark.parametrize('name', ['kardenwort_alignment', 'kardenwort_delivery', 'kardenwort_events', 'kardenwort_configuration'])
def test_new_boundaries_have_no_backwards_shell_imports(name):
    tree = ast.parse((ROOT / (name + '.py')).read_text(encoding='utf-8'))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module.split('.')[0])
    assert not imports.intersection({'kardenwort_desk', 'kardenwort_controller', 'http_server'})
    if name == 'kardenwort_alignment':
        assert not imports


@pytest.mark.parametrize('name', ['kardenwort_alignment', 'kardenwort_delivery', 'kardenwort_events', 'kardenwort_configuration'])
def test_new_boundary_complexity_ceiling(name):
    tree = ast.parse((ROOT / (name + '.py')).read_text(encoding='utf-8'))
    violations = {node.name: complexity(node) for node in functions(tree) if complexity(node) > 7}
    assert violations == {}


def test_reworked_retext_functions_meet_complexity_ceiling():
    tree = ast.parse((ROOT / 'kardenwort_controller.py').read_text(encoding='utf-8'))
    names = {'retext_session', '_persist_retext_updates', '_retext_memo', '_retext_path',
             '_prepare_legacy_retext', '_retext_maps', '_persist_retext_rows', '_cache_retext_result',
             '_read_retext_sentences', '_read_retext_source', '_retext_providers', '_translate_retext',
             '_align_retext', '_publish_retext_result'}
    measured = {node.name: complexity(node) for node in functions(tree) if node.name in names}
    assert set(measured) == names
    assert max(measured.values()) <= 7, measured


def test_bundled_javascript_has_no_compiler_coupling():
    # The accepted plain-JavaScript requirement supersedes the old @ts-check gate.
    # Behavioral tests remain unchanged; this checks the revised architecture contract.
    root = ROOT / 'web/js'
    assert not list(root.rglob('*.d.ts'))
    assert not list(root.rglob('jsconfig.json'))
    for path in (ROOT / 'web/js').rglob('*.js'):
        source = path.read_text(encoding='utf-8')
        assert '@ts-check' not in source and '@ts-ignore' not in source and '@ts-nocheck' not in source, path
        assert '<reference path=' not in source and '@type ' not in source, path
        assert "import('" not in source and 'import("' not in source, path
