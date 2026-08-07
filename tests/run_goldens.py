import os
import sys
import subprocess
from pathlib import Path

def main():
    repo_root = Path(__file__).parent.parent.resolve()
    desk_script = repo_root / "kardenwort_desk.py"
    fixtures_dir = repo_root / "tests" / "fixtures"

    runs = [
        {
            "name": "English Golden Sample",
            "lang": "en",
            "zid": "20260807190001",
            "file": fixtures_dir / "en_golden.txt"
        },
        {
            "name": "German Golden Sample",
            "lang": "de",
            "zid": "20260807190002",
            "file": fixtures_dir / "de_golden.txt"
        }
    ]

    for i, run in enumerate(runs):
        print(f"==================================================")
        if i == 0:
            input(f"Ready to test {run['lang']}? Press Enter to start...")
        else:
            input(f"Please close any open desk windows now.\nReady to test {run['lang']}? Press Enter to start...")
            
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
            "--text", text_content
        ]
        
        try:
            # We capture stdout so it doesn't flood the terminal with HTML Base64
            # We let stderr stream normally so any errors/logs are visible
            result = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, check=True)
            print(f"SUCCESS: {run['name']} completed.")
            print(f"Rendered HTML length: {len(result.stdout)} characters.")
        except subprocess.CalledProcessError as e:
            print(f"FAILED: {run['name']} crashed with exit code {e.returncode}")
            print(f"Stderr:\n{e.stderr}")

    print(f"==================================================")
    print(f"Check results/speed_trace.jsonl to view the performance traces.")

if __name__ == "__main__":
    main()
