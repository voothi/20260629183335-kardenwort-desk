"""Capture Phase 1 evidence without modifying production sessions or Git.

Run from the repository root: python scripts/release_audit_baseline.py
Baseline Python is loaded directly from the requested read-only Git revision;
both versions render an explicit scratch TSV fixture with SQLite disabled.
"""

import itertools
import json
from pathlib import Path
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright
from tests.test_release_audit_regressions import audit_html, TRANSPORT
import kardenwort_desk


def pairwise_matrix():
    axes = {
        "theme": ["dark", "light", "white", "compact"],
        "host": ["browser", "native", "lookup"],
        "cards": ["single", "container"],
        "filter": ["off", "on"],
        "transport": ["status", "sse", "overlap", "reload"],
    }
    candidates = list(itertools.product(*axes.values()))
    positions = list(itertools.combinations(range(len(axes)), 2))

    def pairs(row):
        return {(i, row[i], j, row[j]) for i, j in positions}

    uncovered = set().union(*(pairs(row) for row in candidates))
    required = set(uncovered)
    chosen = []
    while uncovered:
        row = max(candidates, key=lambda value: len(pairs(value) & uncovered))
        chosen.append(row)
        uncovered -= pairs(row)
    covered = set().union(*(pairs(row) for row in chosen))
    assert covered == required
    return {"axes": axes, "pair_count": len(required), "rows": [
        dict(zip(axes, row)) for row in chosen
    ], "execution_status": "defined; not executed"}


METRICS = """() => {
    const metric = selector => {
        const el = document.querySelector(selector);
        if (!el) return null;
        const s = getComputedStyle(el), b = el.getBoundingClientRect();
        return {x: b.x, y: b.y, width: b.width, height: b.height,
            fontSize: s.fontSize, fontWeight: s.fontWeight,
            color: s.color, background: s.backgroundColor};
    };
    return {
        selected: metric('#kw-btn-filter-selected'),
        lemma: metric('#kw-btn-same-lemma'),
        delete: metric('#kw-btn-delete'),
        source: metric('#source-container'),
        translation: metric('#translation-container'),
        textIndicator: metric('#translation-container .skeleton-loader'),
        cellIndicator: metric('td .skeleton-loader'),
        toolbar: metric('.kw-action-toolbar'), table: metric('#lemma-table')
    };
}"""


def main():
    output = ROOT / "results" / (sys.argv[1] if len(sys.argv) > 1 else "20261023221555-release-audit")
    output.mkdir(parents=True, exist_ok=True)
    fixture = ROOT / "scratch" / "20261023221555-style-fixture"
    fixture.mkdir(parents=True, exist_ok=True)
    source = subprocess.check_output(
        ["git", "show", "56f8696:kardenwort_desk.py"], cwd=ROOT
    ).decode("utf-8")
    baseline = types.ModuleType("release_audit_baseline")
    baseline.__file__ = str(ROOT / "kardenwort_desk.py")
    sys.modules[baseline.__name__] = baseline
    exec(compile(source, baseline.__file__, "exec"), baseline.__dict__)
    results = {"baseline": "56f8696", "target": "2f6839e", "versions": {}}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for label, module in [("baseline", baseline), ("current", kardenwort_desk)]:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            html = audit_html(fixture, module=module)
            page.set_content(html.replace("<head>", "<head>" + TRANSPORT))
            page.wait_for_function("typeof setFilterSelectedOnly === 'function'")
            idle = page.evaluate(METRICS)
            page.evaluate("setFilterSelectedOnly(true); setHighlightSameLemma(true)")
            active = page.evaluate(METRICS)
            page.locator("#kw-btn-delete").hover()
            hover = page.evaluate(METRICS)
            # Diagnostic visual fixture uses the exact server text-indicator
            # markup; this is not a pending-to-resolved geometry proof.
            page.evaluate("""baseline => {
                const dimensions = baseline ? ' style="width:100%;max-width:500px;min-height:1.6em;height:1.6em"' : '';
                document.querySelector('#translation-container').innerHTML =
                    '<div class="skeleton-loader" data-pending="true"' + dimensions + '>Google</div>';
                document.querySelector('td[data-col="WordDestination"]').innerHTML =
                    '<span class="skeleton-loader kw-skeleton-cell">Google</span>';
            }""", label == 'baseline')
            indicators = page.evaluate(METRICS)
            page.screenshot(path=str(output / f"{label}.png"), full_page=True)
            results["versions"][label] = {
                "idle": idle, "active": active, "delete_hover": hover["delete"],
                "indicator_fixture": indicators, "page_errors": errors,
            }
            assert errors == []
            page.close()
        browser.close()
    (output / "styles.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    matrix = pairwise_matrix()
    (output / "matrix.json").write_text(json.dumps(matrix, indent=2), encoding="utf-8")
    print(f"2 versions captured; {len(matrix['rows'])} matrix rows; {matrix['pair_count']} pairs covered by definition")


if __name__ == "__main__":
    main()
