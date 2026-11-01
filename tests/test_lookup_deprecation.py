import io
import sys
from pathlib import Path

import pytest

import kardenwort_desk


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_lookup_help_displays_deprecation_status(monkeypatch):
    mock_out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", mock_out)
    monkeypatch.setattr(sys, "stderr", mock_out)
    monkeypatch.setattr(sys, "argv", ["kardenwort_desk.py", "lookup", "--help"])

    with pytest.raises(SystemExit) as excinfo:
        kardenwort_desk.main()

    assert excinfo.value.code == 0
    help_text = mock_out.getvalue()
    assert "[DEPRECATED]" in help_text
    assert "GoldenDict" in help_text


def test_cmd_lookup_logs_deprecation_without_polluting_stdout(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(
        kardenwort_desk,
        "core_lookup",
        lambda **kwargs: {"html": "<div>clean-lookup-output</div>"},
    )
    mock_stdout = io.StringIO()
    monkeypatch.setattr(sys, "__stdout__", mock_stdout)
    monkeypatch.setattr(sys, "stdout", mock_stdout)

    args = kardenwort_desk.argparse.Namespace(
        text="Haus",
        language="de",
        target_lang="en",
        config=None,
        format="html",
        text_mode="single",
        sections=None,
        lemma_columns=None,
        theme=None,
        no_headings=False,
        disable_css=False,
        zid="20261101153500",
        bypass_lang_check=True,
        storage=None,
        sentence_match_strategy=None,
        no_checksum_lookup=False,
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(SystemExit) as excinfo:
            kardenwort_desk.cmd_lookup(args)

    assert excinfo.value.code == 0
    assert mock_stdout.getvalue().strip() == "<div>clean-lookup-output</div>"
    assert "DEPRECATED: GoldenDict lookup subcommand is deprecated and frozen" in caplog.text


def test_goldendict_entrypoints_and_config_carry_deprecation_annotations():
    for fn in (
        kardenwort_desk.run_lookup_flow,
        kardenwort_desk.render_lookup_html,
        kardenwort_desk._render_lookup_html_impl,
        kardenwort_desk.core_lookup,
        kardenwort_desk.cmd_lookup,
    ):
        assert fn.__doc__ is not None, f"{fn.__name__} missing docstring"
        assert "@deprecated" in fn.__doc__, f"{fn.__name__} docstring missing @deprecated"

    app_js = (REPO_ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
    assert "@deprecated GoldenDict lookup mode is deprecated" in app_js

    controller_py = (REPO_ROOT / "kardenwort_controller.py").read_text(encoding="utf-8")
    assert "@deprecated GoldenDict HTML lookup rendering" in controller_py
    assert "@deprecated GoldenDict view mode is deprecated" in controller_py
    assert "@deprecated GoldenDict JSON lookup endpoint" in controller_py

    config_ini = (REPO_ROOT / "config.ini").read_text(encoding="utf-8")
    assert "[goldendict]\n; [DEPRECATED] GoldenDict lookup integration is deprecated" in config_ini.replace("\r\n", "\n")

