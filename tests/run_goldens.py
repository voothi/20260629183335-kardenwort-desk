import os
import sys
import subprocess
from pathlib import Path

def main():
    repo_root = Path(__file__).parent.parent.resolve()
    fixtures_dir = repo_root / "tests" / "fixtures"
    ahk_script = Path("U:/voothi/20240411110510-autohotkey/kardenwort-window/kardenwort-window.ahk")

    runs = [
        {
            "name": "English Golden Sample",
            "lang": "en",
            "file": fixtures_dir / "en_golden.en.txt"
        },
        {
            "name": "German Golden Sample",
            "lang": "de",
            "file": fixtures_dir / "de_golden.de.txt"
        }
    ]

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
            
        # Instead of calling Python directly, we trigger the resident AHK script exactly
        # as if the user triggered it via hotkey or tray menu.
        # This guarantees 100% End-to-End architectural authenticity.
        cmd = [
            "AutoHotkey.exe",
            str(ahk_script),
            "--desk",
            str(run['file'])
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"ERROR: AHK launcher failed for {run['lang']}:")
                print(result.stderr)
            else:
                print(f"SUCCESS: {run['name']} initiated via AHK.")
                print(f"The actual speed trace will be asynchronously appended to results/speed_trace.jsonl")
                    
        except Exception as e:
            print(f"ERROR: {e}")

    print(f"==================================================")
    print(f"Check results/speed_trace.jsonl to view the authentic performance traces.")

if __name__ == "__main__":
    main()
