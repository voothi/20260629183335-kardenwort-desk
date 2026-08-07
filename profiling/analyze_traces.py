import json
import collections
from pathlib import Path
from datetime import datetime, timezone
import subprocess
import bisect
import re

REPO_ROOT = Path(__file__).parent.parent.resolve()

def get_golden_prefixes():
    fixtures_dir = REPO_ROOT / "tests" / "fixtures"
    prefixes = {}
    if fixtures_dir.exists():
        for f in fixtures_dir.glob("*-golden.*.txt"):
            match = re.match(r"^(\d{14})", f.name)
            if match:
                zid = match.group(1)
                lang = f.suffixes[-2].strip('.').upper()
                prefixes[zid[:12]] = f"Golden {lang}"
    return prefixes

def get_git_commits():
    try:
        output = subprocess.check_output(
            ['git', 'log', '--format=%h|%cI|%s'],
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
                dt = datetime.fromisoformat(dt_str).timestamp()
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
    def __init__(self, filename):
        self.filename = filename
        open(filename, 'w', encoding='utf-8').close()
    def __call__(self, *args, **kwargs):
        print(*args, **kwargs)
        with open(self.filename, 'a', encoding='utf-8') as f:
            print(*args, file=f, **kwargs)

out = TeeLogger(Path(__file__).parent / 'speed_analysis.md')

def analyze():
    trace_file = REPO_ROOT / "results" / "speed_trace.jsonl"
    if not trace_file.exists():
        out(f"Trace file not found at {trace_file}")
        return

    golden_prefixes = get_golden_prefixes()
    commits = get_git_commits()
    commit_lookup = {c['hash']: c for c in commits}

    runs_by_commit = collections.defaultdict(lambda: collections.defaultdict(list))
    phase_aggregates = collections.defaultdict(list)

    with open(trace_file, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip(): continue
            try:
                data = json.loads(line)
                phase = data.get("phase")
                duration = data.get("duration")
                zid = data.get("zid")
                ts_str = data.get("timestamp")
                if phase and duration is not None and ts_str:
                    if zid in ("000", "00000000000000", "unknown"): continue
                    
                    # Filter: Only include Golden Runs (and their children)
                    if golden_prefixes and zid[:12] not in golden_prefixes:
                        continue
                        
                    event_ts = datetime.fromisoformat(ts_str).timestamp()
                    c_hash = get_commit_for_time(commits, event_ts)
                    
                    # Group by the 12-digit run session prefix so children are merged with the master
                    run_session = zid[:12]
                    runs_by_commit[c_hash][run_session].append(data)
                    phase_aggregates[phase].append(duration)
            except json.JSONDecodeError:
                pass

    out("# Performance Dynamics Over Time (By Git Commit)")
    out()

    active_commits = sorted(runs_by_commit.keys(), key=lambda h: commit_lookup[h]['ts'] if h in commit_lookup else 0)

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
        subj = commit_lookup[h]['subj'] if h in commit_lookup else "Unknown"
        out(f"## [Commit: {h}] {subj}")
        out("```text")
        
        sessions = runs_by_commit[h]
        valid_sessions = [s for s in sessions.keys() if s != 'unknown']
        valid_sessions.sort(reverse=True)
        
        for latest_session in valid_sessions:
            events = sessions[latest_session]
            if not events: continue
            
            min_start_ts = float('inf')
            max_end_ts = 0
            parsed_events = []
            for e in events:
                end_t = datetime.fromisoformat(e['timestamp']).timestamp()
                start_t = end_t - e['duration']
                if start_t < min_start_ts: min_start_ts = start_t
                if end_t > max_end_ts: max_end_ts = end_t
                
                parsed_events.append({
                    'phase': e['phase'],
                    'zid': e.get('zid', 'unknown'),
                    'start_t': start_t,
                    'end_t': end_t,
                    'dur': e['duration']
                })
                
            total_time = max_end_ts - min_start_ts
            if total_time <= 0: total_time = 1
            
            label = golden_prefixes.get(latest_session, "Unknown")
            out(f"Run Session: {latest_session}** [{label}] (Total Batch E2E Duration: {total_time:.3f}s)")
            out("-" * 75)
            
            parsed_events.sort(key=lambda x: x['start_t'])
            chart_width = 40
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
                phase_label = f"{p['phase']} {suffix}".strip()
                
                out(f"{phase_label:<30} | {bar} | {p['dur']:.3f}s")
            out()
            
        out("```\n")
                
    out("## Golden Run Aggregates")
    out("| Phase | Cnt | Min (s) | Avg (s) | Max (s) |")
    out("| :--- | :---: | :---: | :---: | :---: |")
    sorted_phases = sorted(phase_aggregates.items(), key=lambda x: sum(x[1])/len(x[1]), reverse=True)
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

if __name__ == '__main__':
    analyze()
