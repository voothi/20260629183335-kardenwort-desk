import json
import collections
from pathlib import Path
from datetime import datetime, timezone
import subprocess
import bisect

def get_git_commits():
    try:
        # Get all commits: Hash | ISO_Date | Subject
        output = subprocess.check_output(
            ['git', 'log', '--format=%h|%cI|%s'],
            universal_newlines=True
        )
    except Exception as e:
        print("Could not get git history:", e)
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
    
    # Sort chronological (oldest first) for bisect
    commits.sort(key=lambda x: x['ts'])
    return commits

def get_commit_for_time(commits, timestamp):
    if not commits:
        return "unknown"
    # Find the last commit that happened *before* the trace timestamp
    # Extract timestamps
    timestamps = [c['ts'] for c in commits]
    idx = bisect.bisect_right(timestamps, timestamp)
    if idx == 0:
        return commits[0]['hash'] # Trace is before first commit
    return commits[idx-1]['hash']

def analyze():
    trace_file = Path("results/speed_trace.jsonl")
    if not trace_file.exists():
        print(f"Trace file not found at {trace_file}")
        return

    commits = get_git_commits()
    commit_lookup = {c['hash']: c for c in commits}

    # Group runs by git commit -> ZID -> events
    runs_by_commit = collections.defaultdict(lambda: collections.defaultdict(list))

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
                    if zid in ("000", "00000000000000"): continue
                    
                    event_ts = datetime.fromisoformat(ts_str).timestamp()
                    c_hash = get_commit_for_time(commits, event_ts)
                    runs_by_commit[c_hash][zid].append(data)
            except json.JSONDecodeError:
                pass

    print("=" * 80)
    print(f"{'PERFORMANCE DYNAMICS OVER TIME (BY GIT COMMIT)':^80}")
    print("=" * 80)

    # Sort commits chronological
    active_commits = sorted(runs_by_commit.keys(), key=lambda h: commit_lookup[h]['ts'] if h in commit_lookup else 0)

    for h in active_commits:
        subj = commit_lookup[h]['subj'] if h in commit_lookup else "Unknown"
        print(f"\n[Commit: {h}] {subj}")
        print("-" * 80)
        
        zids = runs_by_commit[h]
        valid_zids = [z for z in zids.keys() if z != 'unknown']
        valid_zids.sort(reverse=True)
        
        if not valid_zids:
            print("  No full pipeline runs in this commit.")
            continue
            
        for latest_zid in valid_zids[:3]: # top 3 runs per commit
            events = zids[latest_zid]
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
                    'start_t': start_t,
                    'end_t': end_t,
                    'dur': e['duration']
                })
                
            total_time = max_end_ts - min_start_ts
            if total_time <= 0: total_time = 1
            
            print(f"  ZID: {latest_zid} (Total E2E Pipeline Duration: {total_time:.3f}s)")
            
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
                print(f"    {p['phase']:<30} | {bar} | {p['dur']:.3f}s")

if __name__ == '__main__':
    analyze()
