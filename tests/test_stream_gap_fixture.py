"""The active gap fixture supports the real save and reload contract."""
import pytest

from tests.release_projection_support import checked_controller as running_controller
from tests.test_release_boundary_contracts import ZID, save_selection, restored_membership, use_backend
from tests.test_stream_gap_recovery import seed_session


@pytest.mark.parametrize('backend', ['sqlite', 'tsv'])
def test_active_gap_fixture_saves_and_reloads_real_membership(running_controller, backend):
    url, server, _ = running_controller
    adapter = use_backend(server, backend)
    seed_session(server, adapter)
    save_selection(url, 1, True)
    assert restored_membership(adapter) == {'haus': False, 'baum': True}
    memo = server.arbiter.sessions[ZID]
    selected_column = memo['headers'].index('DeskSelected')
    assert [row[selected_column] for row in memo['data_rows']] == ['0', '1']
