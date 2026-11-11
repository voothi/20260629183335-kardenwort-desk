"""Explicit relocated icon roots, deterministic fallback, and traversal rejection."""
import pytest
from pathlib import Path

from kardenwort_assets import resolve_static_asset


def test_shipped_numbered_icons_are_available_without_a_native_repository():
    root = Path(__file__).resolve().parents[1] / 'assets'
    for number in range(1, 100):
        icon = resolve_static_asset(root, f'numbers/{number}.ico')
        assert icon.name == f'{number}.ico'
        assert icon.read_bytes()[:4] == b'\x00\x00\x01\x00'
    assert resolve_static_asset(root, 'numbers/999.ico') == root / 'numbers/1.ico'


def test_numbered_assets_follow_declared_root_without_sibling_discovery(tmp_path):
    local, native = tmp_path / 'local', tmp_path / 'relocated-host'
    numbers = native / 'assets' / 'numbers'
    numbers.mkdir(parents=True)
    first, second = numbers / '1.ico', numbers / '2.ico'
    first.write_bytes(b'first')
    second.write_bytes(b'second')
    assert resolve_static_asset(local, 'numbers/2.ico', native) == second
    assert resolve_static_asset(local, 'numbers/999.ico', native) == first
    with pytest.raises(FileNotFoundError):
        resolve_static_asset(local, 'numbers/1.ico')


def test_local_assets_win_and_native_root_is_limited_to_numbered_icons(tmp_path):
    local, native = tmp_path / 'local', tmp_path / 'host'
    (local / 'numbers').mkdir(parents=True)
    (native / 'assets').mkdir(parents=True)
    first = local / 'numbers' / '1.ico'
    first.write_bytes(b'local')
    (native / 'assets' / 'admin.js').write_text('external', encoding='utf-8')
    assert resolve_static_asset(local, 'numbers/1.ico', native) == first
    with pytest.raises(FileNotFoundError):
        resolve_static_asset(local, 'admin.js', native)


@pytest.mark.parametrize('relative', ['../secret', 'numbers/../../secret'])
def test_static_asset_traversal_is_rejected_before_icon_fallback(tmp_path, relative):
    local, native = tmp_path / 'local', tmp_path / 'host'
    (native / 'assets' / 'numbers').mkdir(parents=True)
    (native / 'assets' / 'numbers' / '1.ico').write_bytes(b'icon')
    with pytest.raises(ValueError):
        resolve_static_asset(local, relative, native)
