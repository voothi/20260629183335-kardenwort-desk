"""Real SQLite saves only project the pre-edit view when it is consumed."""

import pytest

import kardenwort_desk as desk
from tests.test_sqlite_mutations import temp_env, _seed_sample_session


@pytest.mark.parametrize("column", ["DeskSelected", "selected"])
@pytest.mark.parametrize("with_fingerprint", [False, True])
def test_selection_save_projection_budget(temp_env, monkeypatch, column, with_fingerprint):
    adapter = desk.SqliteStorageAdapter(
        config=temp_env["config"],
        resolved_paths=temp_env["resolved_paths"],
        db_path=temp_env["db_path"],
    )
    session = "20261113184701"
    _seed_sample_session(adapter, session)
    before = adapter.restore_session(session)
    fingerprint = desk.compute_content_fingerprint(before["data_rows"]) if with_fingerprint else None
    projections = []
    original = desk.SqliteStorageAdapter.restore_session

    def observed_restore(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        projections.append(result)
        return result

    with monkeypatch.context() as observation:
        observation.setattr(desk.SqliteStorageAdapter, "restore_session", observed_restore)
        result = desk.core_edit_save(
            session,
            [{"row_id": 0, "sentence_idx": 1, "token_order": 0, "column": column, "value": "1"}],
            temp_env["config"],
            temp_env["resolved_paths"],
            fingerprint=fingerprint,
        )

    assert result["status"] == "success"
    assert adapter.db.get_words_by_session(session)[0]["selected"] == 1
    reloaded = adapter.restore_session(session)
    assert result["fingerprint"] == desk.compute_content_fingerprint(reloaded["data_rows"])
    assert len(projections) == (2 if with_fingerprint else 1)
