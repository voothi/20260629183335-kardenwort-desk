import configparser
import io
import sys
from pathlib import Path

import pytest

import http_server
import kardenwort_desk


REPO_ROOT = Path(__file__).resolve().parent.parent
AHK_CONFIG_DIR = REPO_ROOT.parent / "20240411110510-autohotkey" / "kardenwort-window"

DEPRECATED_CLI_SUBCOMMANDS = (
    "render",
    "export",
    "export-selected",
    "import-selected",
    "reprocess",
    "retext",
    "batch-worker",
    "retext-worker",
    "progressive-worker",
    "edit-save",
    "merge",
    "restore",
    "migrate-tsvs-to-db",
    "server",
)

CANONICAL_CLI_SUBCOMMANDS = (
    "controller",
    "desk",
    "db-status",
)


def _load_ini(path: Path) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(path, encoding="utf-8")
    return cfg


def test_canonical_defaults_and_template_parity():
    for filename in ("config.ini", "config.ini.template"):
        cfg = _load_ini(REPO_ROOT / filename)
        assert cfg.get("storage", "backend") == "sqlite"
        assert cfg.get("sentences_mode", "delivery_mode") == "container"
        assert cfg.getboolean("ui", "launch_in_browser") is True
        assert cfg.get("rendering", "display_mode") == "progressive"
        assert cfg.getboolean("rendering", "auto_inject_updates") is True

    cfg_live = _load_ini(REPO_ROOT / "config.ini")
    cfg_tmpl = _load_ini(REPO_ROOT / "config.ini.template")
    assert set(cfg_live.sections()) == set(cfg_tmpl.sections())
    for section in cfg_live.sections():
        assert set(cfg_live[section].keys()) == set(cfg_tmpl[section].keys()), (
            f"Key mismatch in section [{section}] between config.ini and config.ini.template"
        )


def test_desk_config_carries_canonical_and_deprecation_markers():
    for filename in ("config.ini", "config.ini.template"):
        text = (REPO_ROOT / filename).read_text(encoding="utf-8").replace("\r\n", "\n")
        assert "[storage]\n; [CANONICAL] backend = sqlite is the canonical storage backend." in text
        assert "; [DEPRECATED] Soft fallback from SQLite to TSV is deprecated and frozen" in text
        assert "; [CANONICAL] delivery_mode = container is the canonical multi-sentence workspace mode." in text
        assert "; [CANONICAL] launch_in_browser = true is the canonical UI host mode" in text
        assert "; [CANONICAL] display_mode = progressive is the canonical rendering mode." in text
        assert "[merge]\n; [DEPRECATED] Offline flat-file TSV batch merging is deprecated and frozen" in text


@pytest.mark.skipif(not AHK_CONFIG_DIR.exists(), reason="Sibling AutoHotkey repository not present")
def test_ahk_window_config_carries_canonical_and_deprecation_markers():
    for filename in ("config.ini", "config.ini.template"):
        text = (AHK_CONFIG_DIR / filename).read_text(encoding="utf-8").replace("\r\n", "\n")
        assert "; [CANONICAL] AutoHotkey is restricted to OS tray lifecycle control" in text
        assert "[Window]\n; [DEPRECATED] Native AHK GUI window geometry is deprecated" in text
        assert "; [DEPRECATED] Legacy file-watcher interval for native AHK GUI" in text


def test_python_classes_and_cli_handlers_carry_deprecated_docstrings():
    assert kardenwort_desk.TsvStorageAdapter.__doc__ is not None
    assert "@deprecated" in kardenwort_desk.TsvStorageAdapter.__doc__

    assert http_server.__doc__ is not None
    assert "@deprecated" in http_server.__doc__
    assert http_server.cmd_server.__doc__ is not None
    assert "@deprecated" in http_server.cmd_server.__doc__

    deprecated_handlers = (
        kardenwort_desk.cmd_render,
        kardenwort_desk.cmd_export,
        kardenwort_desk.cmd_export_selected,
        kardenwort_desk.cmd_import_selected,
        kardenwort_desk.cmd_reprocess,
        kardenwort_desk.cmd_reprocess_worker,
        kardenwort_desk.cmd_retext,
        kardenwort_desk.cmd_retext_worker,
        kardenwort_desk.cmd_progressive_worker,
        kardenwort_desk.cmd_edit_save,
        kardenwort_desk.cmd_merge,
        kardenwort_desk.cmd_restore,
        kardenwort_desk.cmd_migrate_tsvs_to_db,
    )
    for fn in deprecated_handlers:
        assert fn.__doc__ is not None, f"{fn.__name__} missing docstring"
        assert "@deprecated" in fn.__doc__, f"{fn.__name__} docstring missing @deprecated"


@pytest.mark.parametrize("subcommand", DEPRECATED_CLI_SUBCOMMANDS)
def test_deprecated_cli_subcommands_display_deprecated_in_help(monkeypatch, subcommand):
    mock_out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", mock_out)
    monkeypatch.setattr(sys, "stderr", mock_out)
    monkeypatch.setattr(sys, "argv", ["kardenwort_desk.py", subcommand, "--help"])

    with pytest.raises(SystemExit) as excinfo:
        kardenwort_desk.main()

    assert excinfo.value.code == 0
    help_text = mock_out.getvalue()
    assert "[DEPRECATED]" in help_text, f"{subcommand} --help did not contain [DEPRECATED]"


@pytest.mark.parametrize("subcommand", CANONICAL_CLI_SUBCOMMANDS)
def test_canonical_cli_subcommands_are_not_marked_deprecated(monkeypatch, subcommand):
    mock_out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", mock_out)
    monkeypatch.setattr(sys, "stderr", mock_out)
    monkeypatch.setattr(sys, "argv", ["kardenwort_desk.py", subcommand, "--help"])

    with pytest.raises(SystemExit) as excinfo:
        kardenwort_desk.main()

    assert excinfo.value.code == 0
    help_text = mock_out.getvalue()
    assert "[DEPRECATED]" not in help_text, f"Canonical subcommand {subcommand} --help unexpectedly contained [DEPRECATED]"


def test_frontend_ahk_webview_bridges_carry_deprecated_jsdoc():
    app_js = (REPO_ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
    assert "@deprecated Native AHK GUI host detection is deprecated" in app_js
    assert "@deprecated Native AHK GUI class marker is deprecated" in app_js
    assert "@deprecated Native AHK selection bridge is deprecated" in app_js
