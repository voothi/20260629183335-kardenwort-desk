import os
import sys
import subprocess
import importlib.util
from pathlib import Path

import time

def main():
    repo_root = Path(__file__).parent.parent.resolve()
    desk_script = repo_root / "kardenwort_desk.py"
    fixtures_dir = repo_root / "tests" / "fixtures"

    runs = [
        {
            "name": "English Golden Sample",
            "lang": "en",
            "zid": "20260807190100",
            "file": fixtures_dir / "en_golden.txt"
        },
        {
            "name": "German Golden Sample",
            "lang": "de",
            "zid": "20260807190200",
            "file": fixtures_dir / "de_golden.txt"
        }
    ]

    for i, run in enumerate(runs):
        print(f"==================================================")
        if i == 0:
            print(f"WARNING: Please ensure ALL desk windows are closed before testing so the AHK counter starts at 1.")
            time.sleep(2)
            print(f"Starting test for {run['lang']}...")
        else:
            print(f"\nWARNING: Please close the previous desk window now to reset the counter.")
            time.sleep(4)
            print(f"Starting test for {run['lang']}...")
            
        print(f"Running {run['name']} (ZID: {run['zid']})...")
        
        if not run['file'].exists():
            print(f"ERROR: Missing fixture {run['file']}")
            continue
            
        text_content = run['file'].read_text(encoding="utf-8")
        
        cmd = [
            sys.executable,
            str(desk_script),
            "render",
            "--language", run['lang'],
            "--zid", run['zid'],
            "--text", text_content,
            "--spawn-master"
        ]
        
        try:
            # We capture stdout so it doesn't flood the terminal with HTML Base64
            # We let stderr stream normally so any errors/logs are visible
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"ERROR: Backend failed for {run['lang']}:")
                print(result.stderr)
            else:
                print(f"SUCCESS: {run['name']} completed.")
                print(f"Rendered HTML length: {len(result.stdout)} characters.")
                    
        except Exception as e:
            print(f"ERROR: {e}")

    print(f"==================================================")
    print(f"Check results/speed_trace.jsonl to view the performance traces.")

if __name__ == "__main__":
    main()
