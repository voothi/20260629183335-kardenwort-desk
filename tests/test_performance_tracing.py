import unittest
import itertools
import os
import json
import threading
import time
from unittest.mock import patch
import configparser

import kardenwort_desk
from kardenwort_desk import TraceTimer, run_render_flow

class TestPerformanceTracing(unittest.TestCase):
    def setUp(self):
        clock = self.enterContext(patch("kardenwort_desk.time", wraps=time))
        clock.perf_counter.side_effect = itertools.count(step=0.01).__next__
        # Reset the active ZIDs and boot state before each test
        kardenwort_desk._ACTIVE_ZIDS = set()
        kardenwort_desk._HAS_BOOTED = False

    def test_tracetimer_disabled(self):
        config = configparser.ConfigParser()
        config.add_section("Settings")
        config.set("Settings", "enable_performance_tracing", "False")

        resolved_paths = {"results_dir": "/tmp"}
        
        # Should not raise exception and should not write files
        with TraceTimer("test_phase", "123", config, resolved_paths):
            pass

    def test_tracetimer_enabled(self):
        config = configparser.ConfigParser()
        from kardenwort_desk import SEC_SETTINGS
        config.add_section(SEC_SETTINGS)
        config.set(SEC_SETTINGS, "enable_performance_tracing", "True")

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved_paths = {"results_dir": tmpdir}

            with TraceTimer("test_phase", "zid_456", config, resolved_paths):
                pass

            trace_file = os.path.join(tmpdir, "speed_trace.jsonl")
            self.assertTrue(os.path.exists(trace_file))
            
            with open(trace_file, "r") as f:
                lines = f.readlines()
                self.assertEqual(len(lines), 1)
                entry = json.loads(lines[0])
                self.assertEqual(entry["zid"], "zid_456")
                self.assertEqual(entry["phase"], "test_phase")
                self.assertIn("duration", entry)
                self.assertTrue(entry["duration"] > 0)
                self.assertTrue(entry["cold_start"])

            # Test second run for cold_start = False
            with TraceTimer("test_phase_2", "zid_789", config, resolved_paths):
                pass

            with open(trace_file, "r") as f:
                lines = f.readlines()
                self.assertEqual(len(lines), 2)
                entry = json.loads(lines[1])
                self.assertFalse(entry["cold_start"])

    def test_run_render_flow_debounced(self):
        # Exercise the existing concurrency boundary, not a fabricated render tree.
        from tests.thread_support import Worker
        entered = threading.Event()
        resume = threading.Event()

        def render(*args, **kwargs):
            entered.set()
            assert resume.wait(3), "First render was not released"
            return "rendered first request"

        with patch('kardenwort_desk._run_render_flow_impl', side_effect=render) as implementation:
            zid = "debounce_zid"

            def run_flow():
                return run_render_flow("text", "de", zid, "single", configparser.ConfigParser(), {})

            first = Worker(run_flow)
            try:
                self.assertTrue(entered.wait(3), "First render did not enter")
                debounced_result = run_flow()
                self.assertEqual(debounced_result, "", "Debounced concurrent render must return '' silently without raising")
            finally:
                resume.set()
                self.assertEqual(first.result(), "rendered first request")
            self.assertEqual(implementation.call_count, 1)
            self.assertNotIn(zid, kardenwort_desk._ACTIVE_ZIDS)

    def test_trace_timer_exception(self):
        config = configparser.ConfigParser()
        from kardenwort_desk import SEC_SETTINGS
        config.add_section(SEC_SETTINGS)
        config.set(SEC_SETTINGS, "enable_performance_tracing", "True")

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved_paths = {"results_dir": tmpdir}

            with self.assertRaises(ValueError):
                with TraceTimer("test_exception", "zid_err", config, resolved_paths):
                    raise ValueError("Simulated error")

            trace_file = os.path.join(tmpdir, "speed_trace.jsonl")
            self.assertTrue(os.path.exists(trace_file))
            
            with open(trace_file, "r", encoding='utf-8') as f:
                lines = f.readlines()
                self.assertEqual(len(lines), 1)
                entry = json.loads(lines[0])
                self.assertEqual(entry["status"], "error")

    def test_trace_timer_encoding(self):
        config = configparser.ConfigParser()
        from kardenwort_desk import SEC_SETTINGS
        config.add_section(SEC_SETTINGS)
        config.set(SEC_SETTINGS, "enable_performance_tracing", "True")

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved_paths = {"results_dir": tmpdir}

            with TraceTimer("mül_phase", "zid_enc", config, resolved_paths):
                pass

            trace_file = os.path.join(tmpdir, "speed_trace.jsonl")
            self.assertTrue(os.path.exists(trace_file))
            
            with open(trace_file, "r", encoding='utf-8') as f:
                lines = f.readlines()
                self.assertEqual(len(lines), 1)
                entry = json.loads(lines[0])
                self.assertEqual(entry["phase"], "mül_phase")


def test_sqlite_trace_uses_existing_logger_without_control_files(tmp_path, caplog):
    config = configparser.ConfigParser()
    config.read_dict({'storage': {'backend': 'sqlite'}, 'profiling': {'enable_performance_tracing': 'true'}})
    with caplog.at_level('INFO', logger='kardenwort.desk.performance'):
        with TraceTimer('canonical-phase', 'owned-zid', config, {'results_dir': tmp_path}):
            pass
    records = [json.loads(record.message) for record in caplog.records
               if record.name == 'kardenwort.desk.performance']
    assert len(records) == 1
    assert records[0]['phase'] == 'canonical-phase' and records[0]['zid'] == 'owned-zid'
    assert records[0]['status'] == 'success' and records[0]['duration'] >= 0
    assert list(tmp_path.iterdir()) == []
