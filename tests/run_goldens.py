import os
import sys
import subprocess
from pathlib import Path

def main():
    repo_root = Path(__file__).parent.parent.resolve()
    fixtures_dir = repo_root / "tests" / "fixtures"
    ahk_script = Path("U:/voothi/20240411110510-autohotkey/kardenwort-window/kardenwort-window.ahk")

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
            
        # Instead of calling Python directly, we trigger the resident AHK script exactly
        # as if the user triggered it via hotkey or tray menu.
        AHK_PATH = r"C:\AHK\AutoHotkey_2.0.18\AutoHotkey64.exe"
        
        if os.path.exists(AHK_PATH):
            cmd = [AHK_PATH, str(ahk_script), "--desk", str(run['file']), "--zid", run['zid']]
            cmd_kwargs = {"capture_output": True, "text": True}
        else:
            # Fall back to using cmd.exe start to natively delegate to the OS's .ahk 
            # file association, which avoids WinError 2 if AutoHotkey.exe is not in PATH.
            cmd = f'start "" "{ahk_script}" --desk "{run["file"]}" --zid "{run["zid"]}"'
            cmd_kwargs = {"shell": True, "capture_output": True, "text": True}
        
        try:
            result = subprocess.run(cmd, **cmd_kwargs)
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
