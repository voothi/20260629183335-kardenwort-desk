"""The preference contract remains pure and the I/O shell points inward."""
import ast
from pathlib import Path

from tests.test_intent_architecture import complexity, functions


ROOT = Path(__file__).resolve().parents[1]


def imports(tree):
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name.split('.')[0] for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            result.add(node.module.split('.')[0])
    return result


def test_preference_core_is_pure_and_shell_has_no_reverse_dependencies():
    core = ast.parse((ROOT/'kardenwort_preference_contract.py').read_text())
    assert imports(core) == {'dataclasses'}
    calls = {node.func.id for node in ast.walk(core) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert not calls.intersection({'open', 'exec', 'eval', '__import__', 'input', 'print'})
    shell = ast.parse((ROOT/'kardenwort_preferences.py').read_text())
    assert not imports(shell).intersection({'kardenwort_desk', 'kardenwort_controller', 'sqlite3', 'http'})
    configuration = ast.parse((ROOT/'kardenwort_configuration.py').read_text())
    assert not imports(configuration).intersection({'kardenwort_preferences', 'kardenwort_preference_contract'})


def test_preference_functions_stay_within_complexity_ceiling():
    for name in ('kardenwort_preference_contract.py', 'kardenwort_preferences.py'):
        tree = ast.parse((ROOT/name).read_text())
        measured = {node.name: complexity(node) for node in functions(tree)}
        assert max(measured.values()) <= 7, measured
    controller = ast.parse((ROOT/'kardenwort_controller.py').read_text())
    handler = next(node for node in functions(controller) if node.name == '_update_ui_preference')
    assert complexity(handler) <= 7


def test_preference_client_has_only_explicit_effect_ports():
    source = (ROOT/'web/js/features/preferences.js').read_text()
    assert not any(name in source for name in ('document.', 'localStorage', 'fetch(', 'AppState', 'ahkCall'))
    assert 'setTimeout' not in source and 'setInterval' not in source
    shell = (ROOT/'kardenwort_desk.py').read_text()
    assert shell.count('"features/preferences.js"') == 2
    assert '/js/features/preferences.js' in (ROOT/'web/index.html').read_text()
