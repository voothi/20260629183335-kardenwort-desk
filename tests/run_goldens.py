import os
import sys
import subprocess
from pathlib import Path

def main():
    repo_root = Path(__file__).parent.parent.resolve()
    sys.path.insert(0, str(repo_root))
    import kardenwort_desk
    
    fixtures_dir = repo_root / "tests" / "fixtures"

    runs = []
    for f in sorted(fixtures_dir.glob("*-golden.*.txt")):
        name_parts = f.name.split('-')
        if len(name_parts) >= 2 and len(f.suffixes) >= 2:
            zid = name_parts[0]
            lang = f.suffixes[-2].strip('.')
            runs.append({
                "name": f"{lang.upper()} Golden Sample",
                "lang": lang,
                "zid": zid,
                "file": f
            })

    for i, run in enumerate(runs):
        print(f"==================================================")
        if i == 0:
            input(f"WARNING: Please ensure ALL desk windows are closed before testing.\nReady to test {run['lang']}? Press Enter to start...")
        else:
            input(f"\nWARNING: Please CLOSE the current desk windows.\nOnce they are closed, press Enter to test {run['lang']}...")
            
        print(f"Running {run['name']} (Native AHK Flow)...")
        
        if not run['file'].exists():
            print(f"ERROR: Missing fixture {run['file']}")
            continue
            
        # Clean up any existing generated files for this ZID to force a real performance test
        results_dir = repo_root / "results"
        for existing_file in results_dir.rglob(f"{run['zid']}*"):
            if existing_file.is_file() and existing_file.suffix in ['.tsv', '.txt']:
                try:
                    existing_file.unlink()
                    print(f"Cleaned up existing file: {existing_file.name}")
                except Exception as e:
                    print(f"Warning: Could not delete {existing_file.name}: {e}")
            
        # Instead of calling Python directly, we trigger the resident AHK script exactly
        # as if the user triggered it via hotkey or tray menu.
        try:
            success = kardenwort_desk.spawn_ahk(["--zid", run['zid'], "--desk", str(run['file'])], repo_root)
            if success:
                print(f"SUCCESS: {run['name']} initiated via AHK.")
                print(f"The actual speed trace will be asynchronously appended to results/speed_trace.jsonl")
            else:
                print(f"FAILED: {run['name']} could not be initiated.")
                    
        except Exception as e:
            print(f"ERROR: {e}")

    print(f"==================================================")
    print(f"Check results/speed_trace.jsonl to view the authentic performance traces.")

if __name__ == "__main__":
    main()
