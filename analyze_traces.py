import json
import collections
from pathlib import Path
from datetime import datetime, timezone

def analyze():
    trace_file = Path("results/speed_trace.jsonl")
    if not trace_file.exists():
        print(f"Trace file not found at {trace_file}")
        return

    phases = collections.defaultdict(list)
    zids = collections.defaultdict(list)

    with open(trace_file, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip(): continue
            try:
                data = json.loads(line)
                phase = data.get("phase")
                duration = data.get("duration")
                zid = data.get("zid")
                if phase and duration is not None:
                    if zid in ("000", "00000000000000"): continue
                    phases[phase].append(duration)
                    zids[zid].append(data)
            except json.JSONDecodeError:
                pass

    print("=" * 70)
    print(f"{'PHASE DURATION AGGREGATES (SECONDS)':^70}")
    print("=" * 70)
    print(f"{'Phase':<30} | {'Cnt':<4} | {'Min':<7} | {'Avg':<7} | {'Max':<7}")
    print("-" * 70)
    sorted_phases = sorted(phases.items(), key=lambda x: sum(x[1])/len(x[1]), reverse=True)
    for phase, durations in sorted_phases:
        count = len(durations)
        avg = sum(durations) / count
        min_d = min(durations)
        max_d = max(durations)
        print(f"{phase:<30} | {count:<4} | {min_d:<7.3f} | {avg:<7.3f} | {max_d:<7.3f}")
        
    print("\n" + "=" * 70)
    print(f"{'WATERFALL TIMELINE (LATEST RUNS)':^70}")
    print("=" * 70)
    
    valid_zids = [z for z in zids.keys() if z != 'unknown']
    valid_zids.sort(reverse=True)
    
    for latest_zid in valid_zids[:5]:
        events = zids[latest_zid]
        if not events: continue
        
        # Calculate real absolute start time of the entire sequence
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
        
        print(f"\nZID: {latest_zid} (Total E2E Pipeline Duration: {total_time:.3f}s)")
        print("-" * 70)
        
        # Sort by start time for the waterfall
        parsed_events.sort(key=lambda x: x['start_t'])
        
        chart_width = 40
        for p in parsed_events:
            rel_start = p['start_t'] - min_start_ts
            
            start_idx = int((rel_start / total_time) * chart_width)
            dur_idx = max(1, int((p['dur'] / total_time) * chart_width))
            
            # Ensure it fits exactly in chart width
            if start_idx + dur_idx > chart_width:
                dur_idx = chart_width - start_idx
                if dur_idx < 1: dur_idx = 1
                
            bar = (" " * start_idx) + ("█" * dur_idx)
            bar = bar.ljust(chart_width)
            
            print(f"{p['phase']:<30} | {bar} | {p['dur']:.3f}s")

if __name__ == '__main__':
    analyze()
