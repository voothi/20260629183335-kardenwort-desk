import unittest
import os
import json
import threading
import time
from unittest.mock import patch, MagicMock
import configparser
from pathlib import Path

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

    @patch('kardenwort_desk._effective_text_mode', return_value='single')
    @patch('kardenwort_desk.split_single_mode_text', return_value=['sentence'])
    @patch('kardenwort_desk.prepare_lookup_tsv', return_value=Path('/tmp/20260807205400-test.en.tsv'))
    @patch('kardenwort_desk.load_tsv_rows', return_value=([], [], []))
    @patch('kardenwort_desk.load_anki_mapping', return_value=configparser.ConfigParser())
    @patch('kardenwort_desk.get_role_fields', return_value={})
    def test_run_render_flow_debounced(self, *mocks):
        # We simulate a slow run_render_flow and try to enter it again with the same ZID
        config = configparser.ConfigParser()
        config.add_section("Settings")
        config.set("Settings", "default_target_language", "en")
        resolved_paths = {
            'kardenwort_workspace': Path('/mock/workspace'), 
            'base_dir': Path('/mock/base_dir'),
            'anki_mapping_file': Path('/mock/mapping.json'),
            'anki_tts_cli': Path('/mock/anki_tts_cli.py'),
            'kardenwort_python': Path('/mock/python.exe'),
            'deep_translator_python': Path('/mock/deep_translator_python.exe'),
            'translate_google_script': Path('/mock/translate_google.py'),
            'translate_deepl_script': Path('/mock/translate_deepl.py'),
            'intellifiller_headless': Path('/mock/intellifiller.py'),
            'favorites_output_dir': Path('/mock/favorites'),
            'generated_results_dir': Path('/mock/results')
        }

        # We'll use an event to hold the first thread inside run_render_flow
        in_flow_event = threading.Event()
        resume_event = threading.Event()

        # Patch a function called inside run_render_flow to block
        original_eff = kardenwort_desk._effective_text_mode
        def blocking_eff_mode(*args, **kwargs):
            in_flow_event.set()
            resume_event.wait()
            return 'single'

        with patch('kardenwort_desk._effective_text_mode', side_effect=blocking_eff_mode):
            zid = "debounce_zid"
            
            def run_flow():
                # returns instantly if debounced
                return run_render_flow("text", "de", zid, "single", config, resolved_paths)

            t1 = threading.Thread(target=run_flow)
            t1.start()

            # wait for t1 to be inside the flow
            in_flow_event.wait()

            # now t2 tries with the same zid, it should raise RuntimeError
            with self.assertRaises(RuntimeError):
                run_flow()

            # finish t1
            resume_event.set()
            t1.join()

            # check if _ACTIVE_ZIDS is clean
            self.assertNotIn(zid, kardenwort_desk._ACTIVE_ZIDS)
