import os
import shutil
import sys
import subprocess
from pathlib import Path

def main():
    repo_root = Path(__file__).parent.parent.resolve()
    sys.path.insert(0, str(repo_root))
    import kardenwort_desk
    
    fixtures_dir = repo_root / "tests" / "fixtures"

    runs = []
    # Find directory-based fixtures
    for d in sorted(fixtures_dir.glob("*-golden.*")):
        if not d.is_dir():
            continue
        name_parts = d.name.split('-')
        if len(name_parts) >= 2 and len(d.suffixes) >= 1:
            zid = name_parts[0]
            lang = d.suffixes[-1].strip('.')
            
            source_file = d / "source.txt"
            config_file = d / "config.ini"
            
            if not source_file.exists():
                print(f"Skipping {d.name} - missing source.txt")
                continue
                
            runs.append({
                "name": f"{lang.upper()} Golden Sample",
                "lang": lang,
                "zid": zid,
                "dir": d,
                "file": source_file,
                "config": config_file
            })

    config_path = repo_root / "config.ini"
    config_backup = repo_root / "config.ini.backup"
    
    if config_path.exists():
        shutil.copy2(config_path, config_backup)
        print(f"Backed up live config.ini to {config_backup.name}")
    
    try:
        for i, run in enumerate(runs):
            print(f"==================================================")
            if i == 0:
                print(f"WARNING: Please ensure ALL desk windows are closed before testing.\nReady to test {run['lang']}?")
            else:
                print(f"\nWARNING: Please CLOSE the current desk windows.\nReady to test {run['lang']}?")
                
            print(f"Running {run['name']} (Native AHK Flow)...")
            
            # Swap in the directory-specific config.ini
            if run['config'].exists():
                shutil.copy2(run['config'], config_path)
                print(f"Swapped in test-specific config.ini from {run['dir'].name}")
            else:
                print(f"WARNING: No test-specific config.ini found for {run['dir'].name}. Proceeding with previous config.")
            
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
                import time
                import json
                start_time = time.time()
                success = kardenwort_desk.spawn_ahk([
                    "--zid", run['zid'], 
                    "--language", run['lang'],
                    "--desk", str(run['file'])
                ], repo_root)
                if success:
                    print(f"SUCCESS: {run['name']} initiated via AHK.")
                    
                    # 1. Wait for all expected TSV files to appear in results/
                    expected_zids = set()
                    last_count = -1
                    stable_count = 0
                    print("Polling for TSV generation...")
                    while True:
                        current_tsvs = list(results_dir.glob(f"{run['zid']}*.tsv"))
                        if len(current_tsvs) == last_count and len(current_tsvs) > 0:
                            stable_count += 1
                        else:
                            stable_count = 0
                            last_count = len(current_tsvs)
                            
                        if stable_count >= 3:
                            break
                            
                        if time.time() - start_time > 15:
                            print("TIMEOUT waiting for TSVs to stabilize.")
                            break
                        time.sleep(0.25)
                        
                    for t in current_tsvs:
                        expected_zids.add(t.name[:14])
                        
                    print(f"Expected {len(expected_zids)} windows based on TSV generation.")
                    
                    # 2. Poll speed_trace.jsonl for all html_generation events
                    speed_trace = results_dir / "speed_trace.jsonl"
                    completed_zids = set()
                    print("Waiting for backend html_generation traces...")
                    while len(completed_zids) < len(expected_zids):
                        if speed_trace.exists():
                            try:
                                with open(speed_trace, 'r', encoding='utf-8') as f:
                                    for line in f:
                                        try:
                                            data = json.loads(line)
                                            if data.get('phase') == 'html_generation' and data.get('zid') in expected_zids:
                                                completed_zids.add(data['zid'])
                                        except:
                                            pass
                            except Exception as e:
                                pass
                        
                        if time.time() - start_time > 90:
                            print(f"TIMEOUT waiting for backend traces. Found {len(completed_zids)}/{len(expected_zids)}")
                            break
                        time.sleep(0.5)
                        
                    backend_duration = time.time() - start_time
                    print(f"Backend E2E Duration: {backend_duration:.2f} seconds.")
                    
                    # 3. Call native AHK script to wait for physical UI
                    print(f"Waiting for {len(expected_zids)} physical AHK windows to render...")
                    ahk_repo = os.environ.get("AHK_REPO_PATH")
                    if ahk_repo:
                        ahk_repo = Path(ahk_repo)
                    else:
                        ahk_repo = next(repo_root.parent.glob("*-autohotkey"), None)
                        
                    if not ahk_repo:
                        print("Could not find autohotkey repository to run wait script. Set AHK_REPO_PATH.")
                        continue
                    ahk_script = ahk_repo / "kardenwort-window" / "tests" / "wait_for_windows.ahk"
                    if not ahk_script.exists():
                        print(f"Wait script not found at {ahk_script}")
                        continue
                    
                    ahk_exe = kardenwort_desk.get_ahk_executable()
                            
                    if ahk_exe:
                        try:
                            res = subprocess.run([ahk_exe, str(ahk_script), str(len(expected_zids)), "90"], capture_output=True, text=True)
                            if res.returncode == 0:
                                total_duration = time.time() - start_time
                                print(f"UI E2E Duration: {total_duration:.2f} seconds.")
                                print(res.stdout.strip())
                            else:
                                print(f"UI Wait FAILED: {res.stdout.strip()}")
                        except Exception as e:
                            print(f"Failed to run AHK wait script: {e}")
                    else:
                        print("Could not find AutoHotkey executable to run wait_for_windows.ahk")

                else:
                    print(f"FAILED: {run['name']} could not be initiated.")
                        
            except Exception as e:
                print(f"ERROR: {e}")

        print(f"==================================================")
        print(f"Check results/speed_trace.jsonl to view the authentic performance traces.")

    finally:
        if config_backup.exists():
            shutil.copy2(config_backup, config_path)
            config_backup.unlink()
            print("\nRestored original config.ini")

if __name__ == "__main__":
    main()
