"""Resolve declared static asset roots without repository discovery."""
from pathlib import Path


def contained_asset(root, relative):
    root = Path(root).resolve()
    target = (root / relative).resolve()
    target.relative_to(root)
    return target


def asset_candidates(roots, relative):
    candidates = [contained_asset(root, relative) for root in roots]
    if relative.startswith('numbers/'):
        candidates.extend(contained_asset(root, 'numbers/1.ico') for root in roots)
    return candidates


def resolve_static_asset(local_root, relative, native_root=None):
    contained_asset(local_root, relative)
    roots = [Path(local_root)]
    if relative.startswith('numbers/') and native_root:
        roots.append(Path(native_root) / 'assets')
    candidates = asset_candidates(roots, relative)
    target = next((candidate for candidate in candidates if candidate.is_file()), None)
    if target is None:
        raise FileNotFoundError(relative)
    return target
