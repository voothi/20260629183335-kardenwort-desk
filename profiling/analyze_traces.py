import json
import collections
import configparser
import os
import sys
import shutil
from pathlib import Path
from datetime import datetime, timezone
import subprocess
import bisect
import re

REPO_ROOT = Path(__file__).parent.parent.resolve()
CHART_WIDTH = 40


def _get_cluster_gap_seconds():
    """Read the cluster gap threshold from config.ini.
    Uses max(backend_timeout, ui_timeout) so that even slow runs are not
    accidentally split across two clusters by the deduplication logic.
    Fallback: 90 seconds (matches the default backend/ui timeout values).
    """
    try:
        cfg = configparser.ConfigParser()
        cfg.read(REPO_ROOT / 'config.ini', encoding='utf-8')
        backend = cfg.getint('profiling', 'backend_timeout', fallback=90)
        ui = cfg.getint('profiling', 'ui_timeout', fallback=90)
        return max(backend, ui)
    except Exception:
        return 90

def get_golden_prefixes():
    fixtures_dir = REPO_ROOT / "tests" / "fixtures"
    prefixes = {}
    if fixtures_dir.exists():
        for d in fixtures_dir.glob("*-golden.*"):
            if d.is_dir():
                match = re.match(r"^(\d{14})-golden\.(.*)", d.name)
                if match:
                    zid, lang = match.groups()
                    prefixes[zid] = (zid, f"Golden {lang.upper()}")
    return prefixes

def get_git_commits():
    try:
        output = subprocess.check_output(
            ['git', 'log', '-n', '100', '--format=%h|%cI|%s'],
            universal_newlines=True
        )
    except Exception as e:
        out("Could not get git history:", e)
        return []

    commits = []
    for line in output.strip().split('\n'):
        if not line: continue
        parts = line.split('|', 2)
        if len(parts) == 3:
            h, dt_str, subj = parts
            try:
                dt_str_clean = dt_str.replace('Z', '+00:00')
                dt = datetime.fromisoformat(dt_str_clean).timestamp()
                commits.append({'hash': h, 'ts': dt, 'subj': subj})
            except ValueError:
                pass
    commits.sort(key=lambda x: x['ts'])
    return commits

def get_commit_for_time(commits, timestamp):
    if not commits: return "unknown"
    timestamps = [c['ts'] for c in commits]
    idx = bisect.bisect_right(timestamps, timestamp)
    if idx == 0: return commits[0]['hash']
    return commits[idx-1]['hash']


class TeeLogger:
    def __init__(self, filename, quiet=True):
        self.filename = filename
        self.file = open(filename, 'w', encoding='utf-8')
        self.quiet = quiet
        
    def __call__(self, *args, **kwargs):
        if not self.quiet:
            print(*args, **kwargs)
        print(*args, file=self.file, **kwargs)
        self.file.flush()
        
    def __del__(self):
        try:
            self.file.close()
        except:
            pass

out = TeeLogger(Path(__file__).parent / 'speed_analysis.md', quiet=True)

def analyze():
    trace_files = []
    
    # Check main results directory
    main_trace = REPO_ROOT / "results" / "speed_trace.jsonl"
    if main_trace.exists():
        trace_files.append(main_trace)
        
    # Check fixture-specific results directories
    fixtures_dir = REPO_ROOT / "tests" / "fixtures"
    if fixtures_dir.exists():
        for d in fixtures_dir.glob("*-golden.*"):
            if d.is_dir():
                fixture_trace = d / "results" / "speed_trace.jsonl"
                if fixture_trace.exists():
                    trace_files.append(fixture_trace)
                    
    if not trace_files:
        out("No trace files found in results/ or tests/fixtures/*/results/")
        return

    golden_prefixes = get_golden_prefixes()
    commits = get_git_commits()
    commit_lookup = {c['hash']: c for c in commits}

    runs_by_commit = collections.defaultdict(lambda: collections.defaultdict(list))
    phase_aggregates = collections.defaultdict(list)

    for trace_file in trace_files:
        with open(trace_file, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip(): continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                phase = data.get("phase")
                duration = data.get("duration")
                zid = data.get("zid")
                ts_str = data.get("timestamp")
                if phase and duration is not None and ts_str:
                    if zid in ("000", "00000000000000", "unknown"): continue
                    
                    matched_golden_session = None
                    if golden_prefixes:
                        try:
                            # Use the ZID from the trace to match against golden sessions, not the system timestamp
                            trace_zid_dt = datetime.strptime(zid, "%Y%m%d%H%M%S")
                            best_match = None
                            min_diff = 900
                            for g_zid in golden_prefixes:
                                g_dt = datetime.strptime(g_zid, "%Y%m%d%H%M%S")
                                diff = (trace_zid_dt - g_dt).total_seconds()
                                if 0 <= diff <= min_diff:
                                    min_diff = diff
                                    best_match = g_zid
                            matched_golden_session = best_match
                        except Exception:
                            pass
                            
                        if not matched_golden_session:
                            continue
                            
                    run_session = matched_golden_session if matched_golden_session else zid
                    
                    ts_str_clean = ts_str.replace('Z', '+00:00')
                    event_ts = datetime.fromisoformat(ts_str_clean).timestamp()
                    c_hash = get_commit_for_time(commits, event_ts)
                    
                    runs_by_commit[c_hash][run_session].append(data)

    out("# Performance Dynamics Over Time (By Git Commit)")
    out()

    active_commits = sorted(runs_by_commit.keys(), key=lambda h: commit_lookup[h]['ts'] if h in commit_lookup else 0)
    latest_session_failed = {}

    out("## Table of Contents")
    for h in active_commits:
        subj = commit_lookup[h]['subj'] if h in commit_lookup else "Unknown"
        anchor = f"commit-{h}-{subj}".lower()
        anchor = re.sub(r'[^a-z0-9\s\-]', '', anchor).strip().replace(' ', '-')
        out(f"- [[Commit: {h}] {subj}](#{anchor})")
    out("- [Golden Run Aggregates](#golden-run-aggregates)")
    out("- [Phase Glossary](#phase-glossary)")
    out()

    for h in active_commits:
        sessions = runs_by_commit[h]
        valid_sessions = [s for s in sessions.keys() if s != 'unknown']
        valid_sessions.sort(reverse=False)  # Chronological order
        
        session_stats = []
        for latest_session in valid_sessions:
            events = sessions[latest_session]
            if not events: continue
            
            # Sort events by timestamp
            events_sorted = []
            for e in events:
                ts_str_clean = e['timestamp'].replace('Z', '+00:00')
                end_t = datetime.fromisoformat(ts_str_clean).timestamp()
                events_sorted.append((end_t, e))
            events_sorted.sort(key=lambda x: x[0])
            
            # Cluster events that occur within the configured gap window.
            # Uses max(backend_timeout, ui_timeout) from config.ini so that a slow run
            # whose last phase lands >60s after the first is not split across two clusters.
            cluster_gap = _get_cluster_gap_seconds()
            clusters = []
            current_cluster = []
            for end_t, e in events_sorted:
                if not current_cluster:
                    current_cluster.append((end_t, e))
                else:
                    last_t = current_cluster[-1][0]
                    if end_t - last_t > cluster_gap:
                        clusters.append(current_cluster)
                        current_cluster = [(end_t, e)]
                    else:
                        current_cluster.append((end_t, e))
            if current_cluster:
                clusters.append(current_cluster)
                
            # Take only the latest execution cluster
            latest_cluster = clusters[-1] if clusters else []
            
            latest_events = {}
            for end_t, e in latest_cluster:
                start_t = end_t - e['duration']
                key = (e.get('zid', 'unknown'), e['phase'])
                # Within the same cluster, keep the latest if duplicates exist
                if key not in latest_events or end_t > latest_events[key]['end_t']:
                    latest_events[key] = {
                        'phase': e['phase'],
                        'zid': key[0],
                        'start_t': start_t,
                        'end_t': end_t,
                        'dur': e['duration'],
                        'status': e.get('status', 'success')
                    }
            
            parsed_events = list(latest_events.values())
            
            is_failed = any(p['phase'] == 'validation_failed' for p in parsed_events)
            
            min_start_ts = min((p['start_t'] for p in parsed_events), default=0)
            max_end_ts = max((p['end_t'] for p in parsed_events), default=0)
                
            total_time = max_end_ts - min_start_ts
            if total_time <= 0: total_time = 1
            
            session_stats.append({
                'session_id': latest_session,
                'min_start_ts': min_start_ts,
                'max_end_ts': max_end_ts,
                'total_time': total_time,
                'parsed_events': parsed_events,
                'is_failed': is_failed
            })
            
            if not is_failed:
                for p in parsed_events:
                    phase_aggregates[(latest_session, p['phase'])].append(p['dur'])
            
            latest_session_failed[latest_session] = is_failed
            
        gap_info = ""
        if len(session_stats) >= 2:
            gaps = []
            for i in range(1, len(session_stats)):
                gaps.append(session_stats[i]['min_start_ts'] - session_stats[i-1]['max_end_ts'])
            avg_gap = sum(gaps) / len(gaps)
            gap_info = f" (Avg wait between runs: {avg_gap:.2f}s)"
            
        subj = commit_lookup[h]['subj'] if h in commit_lookup else "Unknown"
        out(f"## [Commit: {h}] {subj}{gap_info}")
        out("```text")
            
        for stat in reversed(session_stats):
            latest_session = stat['session_id']
            parsed_events = stat['parsed_events']
            total_time = stat['total_time']
            min_start_ts = stat['min_start_ts']
            
            if latest_session in golden_prefixes:
                master_zid, label = golden_prefixes[latest_session]
            else:
                master_zid = latest_session + "00"
                label = "Unknown"
                
            if stat.get('is_failed'):
                label += " [FAILED - EXCLUDED FROM STATS]"
                
            out(f"Run Session: {master_zid} [{label}] (Total Batch E2E Duration: {total_time:.3f}s)")
            out("-" * 75)
            
            parsed_events.sort(key=lambda x: x['start_t'])
            chart_width = CHART_WIDTH
            for p in parsed_events:
                rel_start = p['start_t'] - min_start_ts
                start_idx = int((rel_start / total_time) * chart_width)
                dur_idx = max(1, int((p['dur'] / total_time) * chart_width))
                if start_idx + dur_idx > chart_width:
                    dur_idx = chart_width - start_idx
                    if dur_idx < 1: dur_idx = 1
                    
                bar = (" " * start_idx) + ("█" * dur_idx)
                bar = bar.ljust(chart_width)
                
                # Suffix to show if it's master (00) or child (01, 02)
                suffix = f"({p['zid'][-2:]})" if len(p['zid']) == 14 else ""
                if p.get('status') == 'error':
                    suffix += " [ERROR]"
                phase_label = f"{p['phase']} {suffix}".strip()
                
                out(f"{phase_label:<35} | {bar} | {p['dur']:.3f}s")
            out()
            
        out("```\n")
                
    out("## Golden Run Aggregates")
    
    session_aggregates = collections.defaultdict(lambda: collections.defaultdict(list))
    for (session, phase), durations in phase_aggregates.items():
        if latest_session_failed.get(session, False):
            continue
        session_aggregates[session][phase].extend(durations)
        
    for session in sorted(session_aggregates.keys()):
        aggregates = session_aggregates[session]
        if session in golden_prefixes:
            master_zid, label = golden_prefixes[session]
        else:
            master_zid = session + "00"
            label = "Unknown"
            
        out(f"\n### {master_zid} [{label}]")
        out("| Phase | Cnt | Min (s) | Avg (s) | Max (s) |")
        out("| :--- | :---: | :---: | :---: | :---: |")
        sorted_phases = sorted(aggregates.items(), key=lambda x: sum(x[1])/len(x[1]), reverse=True)
        for phase, durations in sorted_phases:
            count = len(durations)
            avg = sum(durations) / count
            min_d = min(durations)
            max_d = max(durations)
            out(f"| `{phase}` | {count} | {min_d:.3f} | {avg:.3f} | {max_d:.3f} |")
        
    out("\n## Phase Glossary")
    out("- **`translate_text`**: (Network IO-Bound) Holistically translating the source paragraph/sentence via external APIs (e.g. DeepL).")
    out("- **`lemmatization`**: (CPU-Bound) Tokenizing text and executing morphological/Anki lookups via Kardenwort Core to generate the data grid.")
    out("- **`the_cut`**: (CPU-Bound) Slicing the master TSV into individual child sentence files during Multi-mode runs.")
    out("- **`background_text_translation`**: The progressive worker updating BOTH the text translation AND the individual base lemma translations asynchronously without blocking the UI.")


def delete_trace_by_zid(target_zid):
    trace_files = []
    
    main_trace = REPO_ROOT / "results" / "speed_trace.jsonl"
    if main_trace.exists():
        trace_files.append(main_trace)
        
    fixtures_dir = REPO_ROOT / "tests" / "fixtures"
    if fixtures_dir.exists():
        for d in fixtures_dir.glob("*-golden.*"):
            if d.is_dir():
                fixture_trace = d / "results" / "speed_trace.jsonl"
                if fixture_trace.exists():
                    trace_files.append(fixture_trace)
                    
    if not trace_files:
        print("No trace files found to delete from.")
        return
        
    for trace_file in trace_files:
        temp_file = trace_file.with_name("speed_trace_tmp.jsonl")
        deleted_count = 0
        kept_count = 0
        
        try:
            with open(trace_file, 'r', encoding='utf-8') as f_in, open(temp_file, 'w', encoding='utf-8') as f_out:
                for line in f_in:
                    if not line.strip():
                        f_out.write(line)
                        continue
                    try:
                        data = json.loads(line)
                        zid_val = data.get("zid", "")
                        if zid_val == target_zid or zid_val.startswith(target_zid):
                            deleted_count += 1
                            continue
                    except Exception:
                        pass
                    f_out.write(line)
                    kept_count += 1
                    
            if deleted_count > 0:
                shutil.copy2(temp_file, trace_file)
                print(f"[{trace_file.parent.parent.name}] Deleted {deleted_count} records matching ZID '{target_zid}'. Kept {kept_count} records.")
        finally:
            if temp_file.exists():
                temp_file.unlink()

if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--delete':
        delete_trace_by_zid(sys.argv[2])
    else:
        analyze()
