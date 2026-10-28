"""Snapshot article contracts; the current desktop substitution must fail these.

The reference renderer is read from the pinned release, never checked out. Both
versions receive the same explicit disposable fixture without workspace config,
providers or storage. Browser network access is blocked in every case.
"""
import configparser
from contextlib import contextmanager
from functools import partial
from pathlib import Path
import subprocess
import sys
import types

import pytest

import kardenwort_desk as desk


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = "56f86968e794cbdcc947b6223b3138510695df98"
TOKEN = "snapshot-contract-disposable-token"
HEADERS = [
    "Quotation", "WordSourceInflectedForm", "WordSource", "WordDestination",
    "SentenceSourceIndex", "TokenOrder", "DeskSelected", "WordSourceIPA",
    "WordSourceMorphologyAI", "WordSourcePOS", "WordSourceGender",
]
ROWS = [
    ["Haus", "Haus", "Haus", "house", "1", "0", "0", "haʊs", "", "NOUN", "n"],
    ["Baum", "Baum", "Baum", "tree", "2", "0", "1", "baʊm", "", "NOUN", "m"],
]
HOST = """<!doctype html><html><head><style>
body { font: 17px Georgia; background: rgb(238, 240, 242); margin: 8px; }
</style></head><body><div id="host-heading">Host dictionary heading</div>
ARTICLE</body></html>"""


@pytest.fixture(scope="module")
def snapshot_renderer():
    source = subprocess.check_output(
        ["git", "show", SNAPSHOT + ":kardenwort_desk.py"], cwd=ROOT,
    )
    module = types.ModuleType("dict_snapshot_contract_reference")
    module.__file__ = str(ROOT / "kardenwort_desk.py")
    sys.modules[module.__name__] = module
    try:
        exec(compile(source.decode("utf-8-sig"), module.__file__, "exec"), module.__dict__)
        yield module
    finally:
        del sys.modules[module.__name__]


@pytest.fixture
def render_article(snapshot_renderer, tmp_path):
    config = configparser.ConfigParser()
    config.read_string("[storage]\nbackend=tsv\n[settings]\ndefaultzoom=100\n"
                       "enable_performance_tracing=false\n")
    paths = dict(storage_backend="tsv", sqlite_db_path=tmp_path / "unused.db",
                 results_dir=tmp_path, generated_results_dir=tmp_path)

    def render(*, version="current", enabled=True, theme="compact", disable_css=False):
        module = snapshot_renderer if version == "snapshot" else desk
        options = dict(
            theme=theme, sections=["translation", "lemmas"],
            lemma_columns=["inflected", "lemma", "translation"],
            heading_source="", heading_translation="", heading_lemmas="",
            server_enabled=enabled, server_api_key=TOKEN,
            server_host="127.0.0.1", server_port=59998, disable_css=disable_css,
        )
        return module.render_lookup_html(
            "Das Haus. Der Baum.", "de", "en", config, paths, "20261028102008",
            options, [], list(HEADERS), [list(row) for row in ROWS],
            "The house. The tree.", session_zid="20261028102008",
            api_token=TOKEN, server_enabled=enabled,
        )

    return render


@pytest.fixture(params=["snapshot", "current"])
def article(request, render_article):
    return partial(render_article, version=request.param)


@contextmanager
def isolated_page(browser, *, javascript=False):
    context = browser.new_context(java_script_enabled=javascript,
                                  viewport={"width": 1280, "height": 900})
    context.route("**/*", lambda route: route.abort())
    try:
        yield context.new_page()
    finally:
        context.close()


def table_metrics(page):
    return page.get_by_role("table").evaluate("""table => {
        const metric = element => {
            const css = getComputedStyle(element);
            const box = element.getBoundingClientRect();
            return {width: box.width, height: box.height, font: css.fontFamily,
                    size: css.fontSize, padding: css.padding};
        };
        return {headers: Array.from(table.querySelectorAll('th'), metric),
                cells: Array.from(table.querySelectorAll('tbody tr:first-child td'), metric)};
    }""")


def test_enabled_article_has_only_snapshot_tagging_and_export(browser, article):
    with isolated_page(browser) as page:
        page.set_content(article())
        assert page.get_by_role("checkbox").count() == 2
        buttons = page.get_by_role("button").all_text_contents()
        assert [text.strip() for text in buttons] == ["Export to Anki"]
        assert page.locator("#kw-action-toolbar, #kw-lang-modal, #workspace-tab-bar").count() == 0


def test_enabled_article_does_not_initialize_desktop_runtime(browser, article):
    with isolated_page(browser, javascript=True) as page:
        page.set_content(article())
        globals_present = page.evaluate("""() => [
            'AppState', 'WorkspaceTabs', 'SSEBridge', 'onSaveClick',
            'onRetextClick', 'onRewordClick', 'onUpdateClick'
        ].filter(name => typeof window[name] !== 'undefined')""")
        assert globals_present == []
        assert page.locator("#kardenwort-bootstrap").count() == 0


@pytest.mark.parametrize("disable_css", [False, True])
def test_offline_article_is_readable_without_controls_scripts_or_credentials(browser, article, disable_css):
    html = article(enabled=False, disable_css=disable_css)
    assert TOKEN not in html
    with isolated_page(browser) as page:
        page.set_content(html)
        assert "The house. The tree." in page.locator(".kw-lookup-container").inner_text()
        assert page.get_by_role("table").locator("tbody tr").count() == 2
        assert page.get_by_role("table").locator("tbody").inner_text().split() == [
            "Haus", "Haus", "house", "Baum", "Baum", "tree",
        ]
        assert page.locator("button, input, select, textarea, [contenteditable=true]").count() == 0
        assert page.locator("script, [onclick], [onchange]").count() == 0
        assert page.locator('link[rel="stylesheet"], script[src]').count() == 0
        if disable_css:
            assert page.locator("style").count() == 0


def test_compact_selection_and_content_widths_match_snapshot(browser, article, render_article):
    # Compare actual renders in the same host; 1 CSS px permits rounding only.
    reference = render_article(version="snapshot")
    with isolated_page(browser) as page:
        page.set_content(HOST.replace("ARTICLE", reference))
        expected = table_metrics(page)
        page.set_content(HOST.replace("ARTICLE", article()))
        actual = table_metrics(page)
        assert len(actual["headers"]) == len(expected["headers"]) == 4
        for actual_column, expected_column in zip(actual["headers"], expected["headers"]):
            assert actual_column["width"] == pytest.approx(expected_column["width"], abs=1)


def test_compact_typography_and_density_inherit_host(browser, article):
    with isolated_page(browser) as page:
        page.set_content(HOST.replace("ARTICLE", article()))
        metrics = table_metrics(page)
        for cell in metrics["cells"]:
            assert cell["font"] == "Georgia"
            assert cell["size"] == "17px"
            assert cell["padding"] == "2px 4px"


@pytest.mark.parametrize("version,theme", [
    ("snapshot", "compact"), ("current", "compact"),
    ("current", "dark"), ("current", "light"),
])
def test_article_styles_leave_host_typography_background_and_geometry_unchanged(browser, render_article, version, theme):
    # Snapshot dark/light already restyle the host. Only its compact mode is an
    # isolation reference; the restoration requires containment in every theme.
    probe = """() => {
        const element = document.getElementById('host-heading');
        const css = getComputedStyle(element), body = getComputedStyle(document.body);
        const box = element.getBoundingClientRect();
        return {font: css.fontFamily, size: css.fontSize, color: css.color,
                background: body.backgroundColor, margin: body.margin,
                x: box.x, y: box.y, width: box.width, height: box.height};
    }"""
    with isolated_page(browser) as page:
        page.set_content(HOST.replace("ARTICLE", ""))
        before = page.evaluate(probe)
        page.set_content(HOST.replace("ARTICLE", render_article(version=version, theme=theme)))
        assert page.evaluate(probe) == before
