"""Capture Phase 1 evidence without modifying production sessions or Git.

Select two sources and a new owned output directory with --baseline-ref and
--target-ref (or --working-tree). Each renderer runs in its own source tree.
"""

import itertools
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))



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
    from scripts.release_audit_sources import run_comparison
    return run_comparison(ROOT, Path(__file__).resolve(), METRICS, pairwise_matrix())


if __name__ == "__main__":
    sys.exit(main())
