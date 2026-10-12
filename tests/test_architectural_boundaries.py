"""
test_architectural_boundaries.py
Verifies automated architectural boundary verification and pure functional core isolation via AST inspection.
Codifies the protocol defined in architectural-fitness-testing/SKILL.md.
"""

import ast
from pathlib import Path
import pytest


BANNED_CORE_IMPORTS = {
    "sqlite3",
    "PyQt6",
    "PySide6",
    "tkinter",
    "http.server",
    "socket",
    "urllib",
    "requests",
    "subprocess",
    "aiohttp",
}

ROOT_DIR = Path(__file__).resolve().parent.parent


def _collect_module_imports(filepath: Path):
    with open(filepath, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=str(filepath))

    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_names.add(node.module.split('.')[0])
    return tree, imported_names


def test_text_tokenizer_is_pure_functional_core():
    """
    Verifies that text_tokenizer.py is an isolated, pure functional core with zero I/O imports.
    """
    tokenizer_path = ROOT_DIR / "text_tokenizer.py"
    assert tokenizer_path.exists(), f"text_tokenizer.py not found at {tokenizer_path}"

    tree, imports = _collect_module_imports(tokenizer_path)

    # 1. Assert zero banned I/O, UI, network, or DB imports
    violations = imports.intersection(BANNED_CORE_IMPORTS)
    assert not violations, f"Illegal I/O or UI imports detected in pure text_tokenizer core: {violations}"

    # 2. Assert zero backward dependencies on outer imperative shell modules
    banned_shell_modules = {"kardenwort_desk", "kardenwort_controller", "kardenwort_db", "http_server"}
    shell_violations = imports.intersection(banned_shell_modules)
    assert not shell_violations, f"text_tokenizer imports outer shell modules: {shell_violations}"


def test_b64util_is_pure_utility():
    """
    Verifies that b64util.py contains zero database, UI, or networking imports.
    """
    b64_path = ROOT_DIR / "b64util.py"
    if b64_path.exists():
        _, imports = _collect_module_imports(b64_path)
        violations = imports.intersection(BANNED_CORE_IMPORTS)
        assert not violations, f"Illegal imports in b64util: {violations}"


def test_pure_linguistic_functions_have_no_db_or_io_nodes():
    """
    Verifies via AST inspection that pure linguistic helper functions in kardenwort_desk.py
    contain zero database executions, cursor queries, or socket I/O.
    """
    desk_path = ROOT_DIR / "kardenwort_desk.py"
    with open(desk_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=str(desk_path))

    pure_func_names = {
        "format_lemma_article_tooltip",
        "format_pos_tooltip",
        "format_gender_tooltip",
        "format_gender_badge",
        "format_classification_tooltip",
        "aggregate_sequential_distinct",
        "sort_pos_tags",
        "normalize_pos_tag",
        "sanitize_bracketed_field"
    }

    found_funcs = set()
    banned_call_patterns = {"execute", "cursor", "connect", "urlopen", "get_connection"}

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in pure_func_names:
                found_funcs.add(node.name)
                # Walk statements inside the function body
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        if isinstance(child.func, ast.Attribute):
                            assert child.func.attr not in banned_call_patterns, (
                                f"Pure function '{node.name}' calls forbidden I/O method '{child.func.attr}'"
                            )

    assert pure_func_names.issubset(found_funcs), (
        f"Missing expected pure linguistic functions: {pure_func_names - found_funcs}"
    )


def test_dependency_acyclicity():
    """
    Verifies acyclic dependency flow between database, controller, and tokenizer layers.
    text_tokenizer must never import kardenwort_db, and kardenwort_db must not import kardenwort_controller.
    """
    db_path = ROOT_DIR / "kardenwort_db.py"
    _, db_imports = _collect_module_imports(db_path)
    assert "kardenwort_controller" not in db_imports, "Circular dependency: kardenwort_db imports kardenwort_controller"
    assert "kardenwort_desk" not in db_imports, "Circular dependency: kardenwort_db imports kardenwort_desk"
