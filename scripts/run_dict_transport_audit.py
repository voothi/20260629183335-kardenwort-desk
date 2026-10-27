"""Run review probes with owned storage and a protected-file comparison."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "results" / "20261027221743-dict-transport-audit"
OUT.mkdir(parents=True, exist_ok=True)
os.environ.pop("SSLKEYLOGFILE", None)
REVISION = None
if "--source-ref" in sys.argv:
    position = sys.argv.index("--source-ref")
    REVISION = sys.argv[position + 1]
    del sys.argv[position:position + 2]
    source = OUT / ("source-" + REVISION)
    source.mkdir(exist_ok=True)
    shutil.copytree(ROOT / "web", source / "web", dirs_exist_ok=True)
    for relative in ("kardenwort_desk.py", "kardenwort_controller.py", "web/js/app.js",
                     "web/js/types.js", "web/js/services/sse.js", "web/js/components/WorkspaceTabs.js"):
        content = subprocess.check_output(["git", "show", REVISION + ":" + relative], cwd=ROOT)
        (source / relative).write_bytes(content)
    sys.path.insert(0, str(source))

import pytest
import kardenwort_desk as desk
import kardenwort_db
import kardenwort_controller


def protected_snapshot():
    files = [ROOT / "config.ini", ROOT / "anki-mapping.ini"]
    files += list((ROOT / "data").glob("*.db*"))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files if p.is_file()}


def source_snapshot():
    source_root = Path(desk.__file__).parent
    files = [source_root / relative for relative in (
        "kardenwort_desk.py", "kardenwort_controller.py", "web/js/app.js", "web/js/services/sse.js")]
    files += [ROOT / "tests/test_dict_transport_audit_regressions.py", Path(__file__)]
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


class Isolation:
    @pytest.fixture(autouse=True)
    def audit_owned_config(self, tmp_path, monkeypatch):
        original_load = desk.load_config

        def owned_load(*args, **kwargs):
            if not args and not kwargs:
                args = (ROOT / "config.ini",)
            cfg, paths, gd, wordfill = original_load(*args, **kwargs)
            database = tmp_path / "config-default.db"
            owned_results = tmp_path / "results"
            owned_results.mkdir(exist_ok=True)
            cfg.set("storage", "sqlite_db_path", str(database))
            cfg.set("project_structure", "generated_results_dir", str(owned_results))
            paths.update(sqlite_db_path=database, results_dir=owned_results,
                         generated_results_dir=owned_results)
            if isinstance(wordfill, dict):
                wordfill["sqlite_db_path"] = database
                wordfill["scan_roots"] = [owned_results]
            return cfg, paths, gd, wordfill

        original_connection = kardenwort_db.KardenwortDB.get_connection

        def guarded_connection(instance, *args, **kwargs):
            if not Path(instance.db_path).resolve().is_relative_to(OUT):
                raise AssertionError("Audit attempted database access outside its owned fixture directory")
            return original_connection(instance, *args, **kwargs)

        monkeypatch.setattr(desk, "load_config", owned_load)
        monkeypatch.setattr(kardenwort_db.KardenwortDB, "get_connection", guarded_connection)


if __name__ == "__main__":
    label = sys.argv[1]
    before = protected_snapshot()
    source_before = source_snapshot()
    args = sys.argv[2:] + ["-q", "-p", "no:cacheprovider", "--tb=short", "--basetemp", str(OUT / (label + "-tmp")),
                           "--junitxml", str(OUT / (label + ".xml"))]
    result = pytest.main(args, plugins=[Isolation()])
    after = protected_snapshot()
    source_after = source_snapshot()
    evidence = {"pytest_args": args, "source_ref": REVISION or "working-tree", "exit_code": int(result),
                "loaded_modules": [desk.__file__, kardenwort_controller.__file__],
                "source_before": source_before, "source_after": source_after,
                "source_unchanged": source_before == source_after,
                "protected_before": before, "protected_after": after,
                "protected_unchanged": before == after}
    (OUT / (label + "-execution.json")).write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print("Protected files unchanged:", before == after)
    sys.exit(int(result) if before == after else 99)
