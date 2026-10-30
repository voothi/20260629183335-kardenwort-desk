import unittest
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
        # Reset the active ZIDs and boot state before each test
        kardenwort_desk._ACTIVE_ZIDS = set()
        kardenwort_desk._HAS_BOOTED = False

    def test_tracetimer_disabled(self):
        config = configparser.ConfigParser()
        config.add_section("Settings")
        config.set("Settings", "enable_performance_tracing", "False")

        resolved_paths = {"results_dir": "/tmp"}
        
        # Should not raise exception and should not write files
        with patch('kardenwort_desk.resolve_results_dir', return_value='/tmp'):
            with TraceTimer("test_phase", "123", config, resolved_paths):
                time.sleep(0.01)

    @patch('kardenwort_desk.resolve_results_dir')
    def test_tracetimer_enabled(self, mock_resolve_dir):
        config = configparser.ConfigParser()
        from kardenwort_desk import SEC_SETTINGS
        config.add_section(SEC_SETTINGS)
        config.set(SEC_SETTINGS, "enable_performance_tracing", "True")

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_resolve_dir.return_value = tmpdir
            resolved_paths = {"results_dir": tmpdir}

            with TraceTimer("test_phase", "zid_456", config, resolved_paths):
                time.sleep(0.01)

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
                time.sleep(0.01)

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
                return run_render_flow("text", "de", zid, "single", None, {})

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

    @patch('kardenwort_desk.resolve_results_dir')
    def test_trace_timer_exception(self, mock_resolve_dir):
        config = configparser.ConfigParser()
        from kardenwort_desk import SEC_SETTINGS
        config.add_section(SEC_SETTINGS)
        config.set(SEC_SETTINGS, "enable_performance_tracing", "True")

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_resolve_dir.return_value = tmpdir
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

    @patch('kardenwort_desk.resolve_results_dir')
    def test_trace_timer_encoding(self, mock_resolve_dir):
        config = configparser.ConfigParser()
        from kardenwort_desk import SEC_SETTINGS
        config.add_section(SEC_SETTINGS)
        config.set(SEC_SETTINGS, "enable_performance_tracing", "True")

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_resolve_dir.return_value = tmpdir
            resolved_paths = {"results_dir": tmpdir}

            with TraceTimer("mül_phase", "zid_enc", config, resolved_paths):
                time.sleep(0.01)

            trace_file = os.path.join(tmpdir, "speed_trace.jsonl")
            self.assertTrue(os.path.exists(trace_file))
            
            with open(trace_file, "r", encoding='utf-8') as f:
                lines = f.readlines()
                self.assertEqual(len(lines), 1)
                entry = json.loads(lines[0])
                self.assertEqual(entry["phase"], "mül_phase")
