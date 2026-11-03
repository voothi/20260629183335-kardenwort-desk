"""Source selection preserves the viewport while Selected changes table height."""
import json
import re

import pytest

import kardenwort_desk as desk
from scripts.benchmark_release_interactions import TRANSPORT, render_fixture


def settle(page):
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")


def load_page(page, theme="dark", zoom=100):
    html = render_fixture(desk, 300, "container")
    bootstrap = json.loads(re.search(
        r'<script[^>]*id="kardenwort-bootstrap"[^>]*>(.*?)</script>', html, re.S
    ).group(1))
    page.set_viewport_size({"width": 800, "height": 600})
    html = desk.render_declarative_web_shell(bootstrap, theme=theme)
    page.set_content(html.replace("<head>", "<head>" + TRANSPORT))
    page.wait_for_function('document.querySelectorAll("#source-container span.word").length === 300')
    page.evaluate("""z => {
        document.documentElement.style.setProperty('--zoom-level', z + '%');
        document.documentElement.style.setProperty('--inverse-zoom-width', 10000 / z + '%');
    }""", zoom)
    settle(page)


def snapshot(page):
    return page.evaluate("""() => {
        const r = document.getElementById('source-container').getBoundingClientRect();
        return {x: scrollX, y: scrollY, source: [r.x, r.y, r.width, r.height]};
    }""")


def center(page, index):
    return page.locator('#source-container span.word').nth(index).evaluate("""e => {
        const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2];
    }""")


def assert_stationary(page, before):
    after = snapshot(page)
    assert after["x"] == pytest.approx(before["x"], abs=0.01)
    assert after["y"] == pytest.approx(before["y"], abs=0.01)
    assert after["source"] == pytest.approx(before["source"], abs=0.01)


@pytest.mark.parametrize("theme", ["dark", "light", "white", "compact"])
@pytest.mark.parametrize("zoom", [100, 125])
@pytest.mark.parametrize("gesture", ["first", "drag", "deselect"])
@pytest.mark.parametrize("filtered", [True, False])
def test_selected_source_gesture_preserves_viewport(page, tmp_path, theme, zoom, gesture, filtered):
    load_page(page, theme, zoom)
    page.evaluate("""({gesture, filtered}) => {
        if (gesture === 'deselect') { toggleRowSelection(298, true); toggleRowSelection(299, true); }
        setFilterSelectedOnly(filtered);
        const r = document.getElementById('source-container').getBoundingClientRect();
        scrollTo(0, r.bottom + scrollY - 150);
    }""", {"gesture": gesture, "filtered": filtered})
    settle(page)
    before = snapshot(page)
    start_index = 298 if gesture == "deselect" else 297
    if gesture == "drag":
        # A straight pointer path across three adjacent words on one line.
        start_index = page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('#source-container span.word'));
            for (let i = 297; i >= 270; i--) {
                if (els[i].getBoundingClientRect().y === els[i+2].getBoundingClientRect().y) return i;
            }
            throw new Error('No visible three-word line');
        }""")
    start = center(page, start_index)
    assert 0 < start[1] < 600
    page.mouse.move(*start)
    page.mouse.down()
    settle(page)
    during = snapshot(page)
    assert_stationary(page, before)
    if gesture == "drag":
        page.mouse.move(*center(page, start_index + 2), steps=5)
        settle(page)
        assert_stationary(page, before)
    page.mouse.up()
    settle(page)
    after = snapshot(page)
    (tmp_path / "viewport.json").write_text(json.dumps(
        {"before": before, "during": during, "after": after}, indent=2
    ), encoding="utf-8")
    assert_stationary(page, before)
    selected = page.locator('#lemma-table tbody tr[data-selected="1"]').evaluate_all(
        "rows => rows.map(r => Number(r.getAttribute('data-row-id'))).sort((a,b) => a-b)"
    )
    expected = [299] if gesture == "deselect" else list(range(
        start_index, start_index + (3 if gesture == "drag" else 1)
    ))
    assert selected == expected
    page.evaluate("scrollTo(0, 0)")
    settle(page)
    released_height = page.evaluate("document.scrollingElement.scrollHeight")
    page.evaluate("filtered => {setFilterSelectedOnly(false); setFilterSelectedOnly(filtered)}", filtered)
    settle(page)
    assert page.evaluate("document.scrollingElement.scrollHeight") == released_height


def test_source_selection_preserves_retained_keyboard_focus_without_revealing_it(page):
    load_page(page)
    page.evaluate("toggleRowSelection(0, true); setFilterSelectedOnly(true)")
    page.keyboard.press("ArrowDown")
    assert page.evaluate("getFocusedRowId()") == 0
    page.evaluate("scrollTo(0, 300)")
    settle(page)
    before = snapshot(page)
    index = page.evaluate("""() => Array.from(document.querySelectorAll('#source-container span.word'))
        .findIndex(e => {const r=e.getBoundingClientRect(); return r.y > 100 && r.bottom < 350;})""")
    assert index > 0
    page.mouse.click(*center(page, index))
    settle(page)
    assert_stationary(page, before)
    assert page.evaluate("getFocusedRowId()") == 0
    assert page.locator('#lemma-table tbody tr[data-selected="1"]').count() == 2


def test_keyboard_navigation_still_reveals_focused_row(page):
    load_page(page)
    page.keyboard.press("ArrowDown")
    settle(page)
    assert page.evaluate("getFocusedRowId()") == 0
    box = page.locator('#lemma-table tr[data-row-id="0"]').bounding_box()
    assert box["y"] >= 0
    assert box["y"] + box["height"] <= 600
