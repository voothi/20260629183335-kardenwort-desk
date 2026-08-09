import os
import shutil
import sys
import subprocess
import time
import json
import re
import configparser
from pathlib import Path

def main():
    repo_root = Path(__file__).parent.parent.resolve()
    sys.path.insert(0, str(repo_root))
    import kardenwort_desk
    
    fixtures_dir = repo_root / "tests" / "fixtures"

    def close_all_kardenwort_windows():
        try:
            ahk_exe = kardenwort_desk.get_ahk_executable()
            if not ahk_exe: return
            print("Cleaning up existing Kardenwort desk windows...")
            script = 'SetTitleMatchMode(2)\nids := WinGetList("Kardenwort - ")\nfor id in ids\n    try WinClose(id)'
            subprocess.run([ahk_exe, "*"], input=script, text=True, timeout=15)
            import time
            time.sleep(1) # wait for close
        except Exception as e:
            print(f"Cleanup warning: {e}")

    import argparse
    parser = argparse.ArgumentParser(description="Run Kardenwort Golden Fixtures")
    parser.add_argument("--include", nargs="+", help="Only run fixtures matching these ZIDs or names")
    parser.add_argument("--exclude", nargs="+", help="Skip fixtures matching these ZIDs or names")
    parser.add_argument("--count", type=int, default=1, help="Number of times to repeat the suite")
    args = parser.parse_args()
    
    includes = args.include if args.include else []
    excludes = args.exclude if args.exclude else []
    
    runs = []
    # Find directory-based fixtures
    for d in sorted(fixtures_dir.glob("*-golden.*")):
        if not d.is_dir():
            continue
            
        if includes:
            if not any(inc in d.name for inc in includes):
                continue
                
        if excludes:
            if any(exc in d.name for exc in excludes):
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

    runs = runs * args.count

    config_path = repo_root / "config.ini"
    config_backup = repo_root / "config.ini.backup"
    
    live_config = configparser.ConfigParser()
    if config_path.exists():
        live_config.read(config_path)
    backend_timeout = live_config.getint("profiling", "backend_timeout", fallback=90)
    ui_timeout = live_config.getint("profiling", "ui_timeout", fallback=90)
    cleanup_delay = live_config.getint("profiling", "cleanup_delay", fallback=5)
    
    if config_backup.exists():
        print(f"Found existing {config_backup.name}. A previous run likely crashed. Restoring it first.")
        shutil.copy2(config_backup, config_path)
        config_backup.unlink()  # Delete immediately — prevents orphan backup from poisoning the next run if we crash before creating a new one
        
    if config_path.exists():
        shutil.copy2(config_path, config_backup)
        print(f"Backed up live config.ini to {config_backup.name}")
    
    try:
        for i, run in enumerate(runs):
            if i > 0 and cleanup_delay > 0:
                print(f"Waiting {cleanup_delay}s for visual inspection before next run...")
                time.sleep(cleanup_delay)
                
            print(f"==================================================")
            if i == 0:
                print(f"Starting test for {run['lang']}...")
            else:
                print(f"\nStarting test for {run['lang']}...")
                
            close_all_kardenwort_windows()
            
            print(f"Running {run['name']} (Native AHK Flow)...")
            
            # Swap in the directory-specific config.ini
            if run['config'].exists():
                shutil.copy2(run['config'], config_path)
                print(f"Swapped in test-specific config.ini from {run['dir'].name}")
            else:
                if config_backup.exists():
                    shutil.copy2(config_backup, config_path)
                print(f"WARNING: No test-specific config.ini found for {run['dir'].name}. Restored original config.")
            
            # Clean up any existing generated files for this ZID to force a real performance test
            test_config = kardenwort_desk.load_kardenwort_config(repo_root)
            results_dir_str = test_config.get('project_structure', 'generated_results_dir', fallback='./results')
            results_dir = Path(results_dir_str)
            if not results_dir.is_absolute():
                results_dir = (repo_root / results_dir).resolve()
                
            from datetime import datetime, timedelta, timezone
            master_dt = datetime.strptime(run['zid'], '%Y%m%d%H%M%S')
            end_dt = master_dt + timedelta(minutes=15)
            end_zid = end_dt.strftime('%Y%m%d%H%M%S')
            
            if results_dir.exists():
                for existing_file in results_dir.rglob("*"):
                    if existing_file.is_file() and len(existing_file.name) >= 14 and existing_file.name[:14].isdigit():
                        if run['zid'] <= existing_file.name[:14] <= end_zid:
                            try:
                                existing_file.unlink()
                            except Exception as e:
                                print(f"Warning: could not delete {existing_file}: {e}")

            # Instead of calling Python directly, we trigger the resident AHK script exactly
            # as if the user triggered it via hotkey or tray menu.
            try:
                start_time = time.time()
                speed_trace = results_dir / "speed_trace.jsonl"
                initial_size = speed_trace.stat().st_size if speed_trace.exists() else 0
                
                success = kardenwort_desk.spawn_ahk([
                    "--zid", run['zid'], 
                    "--language", run['lang'],
                    "--desk", str(run['file'])
                ], repo_root)
                if success:
                    print(f"SUCCESS: {run['name']} initiated via AHK.")
                    
                    # 1. Determine expected windows based on Golden text dynamically
                    try:
                        source_text = run['file'].read_text(encoding='utf-8')
                        test_config = kardenwort_desk.load_kardenwort_config(repo_root)
                        
                        sentences, _, smc = kardenwort_desk.parse_source_sentences(source_text, 'multi', test_config)
                        expected_count = smc.get_expected_window_count(len(sentences))
                    except Exception as e:
                        print(f"Warning: Could not dynamically count sentences: {e}")
                        expected_count = 4 if run['lang'] == 'en' else 9
                    print(f"Expected {expected_count} windows for golden run.")
                    
                    speed_trace = results_dir / "speed_trace.jsonl"
                    completed_zids = set()
                    print("Waiting for backend html_generation traces...")
                    
                    while len(completed_zids) < expected_count:
                        if speed_trace.exists():
                            try:
                                with open(speed_trace, 'r', encoding='utf-8') as f:
                                    f.seek(initial_size)
                                    for line in f:
                                        try:
                                            data = json.loads(line)
                                            if data.get('phase') == 'html_generation' and data.get('zid'):
                                                trace_zid = str(data.get('zid'))
                                                if run['zid'] <= trace_zid <= end_zid:
                                                    completed_zids.add(trace_zid)
                                        except:
                                            pass
                            except Exception as e:
                                pass
                        
                        if time.time() - start_time > backend_timeout:
                            print(f"TIMEOUT waiting for backend traces. Found {len(completed_zids)}/{expected_count}")
                            break
                        time.sleep(0.5)
                        
                    backend_duration = time.time() - start_time
                    print(f"Backend E2E Duration: {backend_duration:.2f} seconds.")
                    
                    # 3. Spawn AHK to poll UI windows appearance
                    print(f"Waiting for {expected_count} physical AHK windows to render...")
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
                            res = subprocess.run([ahk_exe, str(ahk_script), str(expected_count), str(ui_timeout)], capture_output=True, text=True)
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

                    print(f"Validating completeness of generated TSV files (waiting up to {backend_timeout}s for background enrichment)...")
                    val_start = time.time()
                    validation_failed = True
                    missing_details = []
                    
                    while time.time() - val_start < backend_timeout:
                        all_passed = True
                        missing_details = []
                        for existing_file in results_dir.rglob("*.tsv"):
                            if existing_file.is_file():
                                m = re.match(r'^(\d{14})', existing_file.name)
                                if m:
                                    file_zid = m.group(1)
                                    if run['zid'] <= file_zid <= end_zid:
                                        if expected_count > 1 and file_zid == run['zid']:
                                            continue # Master window is not expected to translate lemmas in sentences mode
                                        try:
                                            with kardenwort_desk.file_lock(existing_file):
                                                _, headers, data_rows = kardenwort_desk.load_tsv_rows(existing_file)
                                            role_fields = {"lemma": "WordSource", "word_translation": "WordDestination", "ipa": "WordSourceIPA", "morphology": "WordSourceMorphologyAI"}
                                            if not kardenwort_desk.is_base_translation_finished(headers, data_rows, role_fields, lemma_base_provider='intellifiller'):
                                                all_passed = False
                                                missing_details.append(f"    [VALIDATION PENDING] Missing or incomplete fields detected in {existing_file.name}")
                                        except Exception as e:
                                            all_passed = False
                                            missing_details.append(f"    [VALIDATION ERROR] Could not validate {existing_file.name}: {e}")
                        
                        if all_passed:
                            validation_failed = False
                            break
                        time.sleep(1.0)
                    
                    if validation_failed:
                        for msg in missing_details:
                            print(msg.replace("PENDING", "FAILED"))
                        print(f"    WARNING: Test run {run['zid']} completed but generated corrupted/incomplete data.")
                        with open(speed_trace, 'a', encoding='utf-8') as f:
                            f.write(json.dumps({
                                "zid": run['zid'],
                                "phase": "validation_failed",
                                "duration": 0.0,
                                "status": "failed",
                                "timestamp": datetime.now(timezone.utc).isoformat()
                            }) + '\n')
                    else:
                        print(f"    Validation PASSED. All expected fields populated.")

                else:
                    print(f"FAILED: {run['name']} could not be initiated.")
            except Exception as e:
                print(f"ERROR: {e}")

        print(f"==================================================")
        if cleanup_delay > 0:
            print(f"Waiting {cleanup_delay}s for visual inspection...")
            time.sleep(cleanup_delay)
            
        print("Generating performance trace analysis...")
        subprocess.run([sys.executable, str(repo_root / "profiling" / "analyze_traces.py")])
        print("Done! Check profiling/speed_analysis.md to view the authentic performance traces.\n")
        
        close_all_kardenwort_windows()
                
    finally:
        print("\nCleaning up remaining test windows...")
        close_all_kardenwort_windows()
        
        # Restore original config
        if config_backup.exists():
            shutil.copy2(config_backup, config_path)
            config_backup.unlink()
            print("Restored original config.ini\n")

if __name__ == "__main__":
    main()
