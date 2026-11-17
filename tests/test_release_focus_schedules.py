"""Exercise focus clearing after real shared-selection persistence settles."""
from tests.test_same_lemma_toggle import test_table_row_click_clears_storage_and_prevents_reload_resurrection as original
from tests.test_preference_commands import preference_world


def test_cleared_focus_survives_reload_after_selection_commit(page, tmp_path, preference_world, monkeypatch):
    evaluate = page.evaluate
    waited = []
    frames = []
    geometry = "() => Array.from(document.querySelectorAll('#source-container span.word')).map(e => {const r=e.getBoundingClientRect();return [r.x,r.y,r.width,r.height];})"

    def after_selection_commit(expression, *args, **kwargs):
        if expression == 'saveClientView()' and not waited:
            waited.append(True)
            evaluate('flushPendingSaves()')
            frames.append(evaluate(geometry))
        return evaluate(expression, *args, **kwargs)

    monkeypatch.setattr(page, 'evaluate', after_selection_commit)
    original(page, tmp_path, preference_world)
    assert page.evaluate(geometry) == frames[0]
