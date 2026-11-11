import os
import json
import urllib.request
import urllib.error
import subprocess
import unittest
from contextlib import ExitStack
import tempfile
from tests.controller_test_support import owned_configuration, translation_service, token_service, DeterministicTranslationHandler
from kardenwort_controller_lifetime import stop_owned_process
from tests.thread_support import wait_for_http
from pathlib import Path

DESK_DIR = Path(__file__).resolve().parent.parent
PYTHON_EXE = os.sys.executable
TEST_PORT = 18399
TEST_TOKEN = "test-secret-token-12345"


class TestHTTPServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        resources = ExitStack()
        cls.addClassCleanup(resources.close)
        owner = Path(resources.enter_context(tempfile.TemporaryDirectory()))
        config, paths, _ = owned_configuration(owner, '', 'sqlite')
        config.set('settings', 'anki_mapping_file', str(paths['anki_mapping_file']))
        config.set('server', 'enabled', 'true')
        cls.enabled_config = owner / 'enabled.ini'
        with cls.enabled_config.open('w', encoding='utf-8') as stream:
            config.write(stream)
        config.set('server', 'enabled', 'false')
        cls.disabled_config = owner / 'disabled.ini'
        with cls.disabled_config.open('w', encoding='utf-8') as stream:
            config.write(stream)

    def test_01_server_disabled_exit(self):
        """Verify server subcommand exits with non-zero code when enabled = false."""
        proc = subprocess.run(
            [PYTHON_EXE, str(DESK_DIR / "kardenwort_desk.py"), "--config", str(self.disabled_config), "server"],
            cwd=str(DESK_DIR),
            capture_output=True,
            text=True
        )
        self.assertNotEqual(proc.returncode, 0, "Server must refuse to start when disabled in config.ini")
        self.assertIn("CONFIGURATION_ERROR", proc.stderr + proc.stdout)

    def test_02_server_non_loopback_rejected(self):
        """Verify server refuses to bind to non-loopback host."""
        proc = subprocess.run(
            [PYTHON_EXE, str(DESK_DIR / "kardenwort_desk.py"), "--config", str(self.enabled_config), "server", "--host", "192.168.1.1"],
            cwd=str(DESK_DIR),
            capture_output=True,
            text=True
        )
        self.assertNotEqual(proc.returncode, 0, "Server must reject non-loopback host binding")
        self.assertIn("CONFIGURATION_ERROR", proc.stderr + proc.stdout)


class TestHTTPServerRunning(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        resources = ExitStack()
        cls.addClassCleanup(resources.close)
        cls.test_dir = tempfile.TemporaryDirectory()
        owner = Path(resources.enter_context(cls.test_dir))
        translator = resources.enter_context(translation_service(DeterministicTranslationHandler))
        tokenizer = resources.enter_context(token_service())
        config, paths, _ = owned_configuration(owner, f'http://127.0.0.1:{translator.server_port}', 'sqlite')
        config.read_dict({
            'settings': {'anki_mapping_file': str(paths['anki_mapping_file'])},
            'server': {'enabled': 'true', 'api_key': TEST_TOKEN, 'port': str(TEST_PORT), 'controller_port': str(TEST_PORT)},
            'storage': {'sqlite_db_path': str(owner / 'test_kardenwort.db')},
            'services': {'spacy_server_url': f'http://127.0.0.1:{tokenizer.server_port}'},
            'goldendict': {'run_intellifiller': 'false'},
            'wordfill': {'enabled': 'false'},
            'triggers': {'run_lemma_enrichment': 'manual'}})
        cls.running_config = owner / 'running.ini'
        with cls.running_config.open('w', encoding='utf-8') as stream:
            config.write(stream)
        cls.server_proc = subprocess.Popen(
            [PYTHON_EXE, str(DESK_DIR / "kardenwort_desk.py"), "--config", str(cls.running_config),
             "server", "--port", str(TEST_PORT), "--no-sidecars"], cwd=str(DESK_DIR),
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        resources.callback(stop_owned_process, cls.server_proc)
        wait_for_http(f'http://127.0.0.1:{TEST_PORT}/api/v1/health', lambda: cls.server_proc.poll() is None)

    def test_03_health_endpoint(self):
        url = f"http://127.0.0.1:{TEST_PORT}/api/v1/health"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertEqual(data["status"], "success")
            self.assertTrue(data["data"]["ok"])

    def test_04_unauthorized_post(self):
        url = f"http://127.0.0.1:{TEST_PORT}/api/v1/tag"
        payload = json.dumps({"session_zid": "123", "language": "en", "row_id": 0, "status": True, "fingerprint": "abc"}).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as resp:
                self.fail("Expected HTTP 403 Forbidden without API token")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 403)

    def test_05_lookup_json_endpoint(self):
        url = f"http://127.0.0.1:{TEST_PORT}/api/v1/lookup?text=apple&language=en"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertEqual(data["status"], "success")
            self.assertIn("fingerprint", data["data"])
            self.assertIn("session_zid", data["data"])

    def test_06_tag_endpoint(self):
        url_lk = f"http://127.0.0.1:{TEST_PORT}/api/v1/lookup?text=apple&language=en"
        with urllib.request.urlopen(url_lk) as resp:
            lk_data = json.loads(resp.read().decode('utf-8'))["data"]

        session_zid = lk_data["session_zid"]
        fingerprint = lk_data["fingerprint"]

        url_tag = f"http://127.0.0.1:{TEST_PORT}/api/v1/tag"
        payload = json.dumps({
            "session_zid": session_zid,
            "language": "en",
            "row_id": 0,
            "status": True,
            "fingerprint": fingerprint
        }).encode('utf-8')

        req = urllib.request.Request(url_tag, data=payload, headers={
            "Content-Type": "application/json",
            "X-API-Token": TEST_TOKEN
        })
        try:
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                tag_res = json.loads(resp.read().decode('utf-8'))
                self.assertEqual(tag_res["status"], "success")
                self.assertIn("fingerprint", tag_res["data"])
        except urllib.error.HTTPError as e:
            err_body = e.read().decode('utf-8')
            self.fail(f"HTTPError {e.code}: {err_body}")

    def test_07_stale_fingerprint_rejected(self):
        url_lk = f"http://127.0.0.1:{TEST_PORT}/api/v1/lookup?text=banana&language=en"
        with urllib.request.urlopen(url_lk) as resp:
            lk_data = json.loads(resp.read().decode('utf-8'))["data"]

        url_tag = f"http://127.0.0.1:{TEST_PORT}/api/v1/tag"
        payload = json.dumps({
            "session_zid": lk_data["session_zid"],
            "language": "en",
            "row_id": 0,
            "status": True,
            "fingerprint": "invalid_stale_hash_12345"
        }).encode('utf-8')

        req = urllib.request.Request(url_tag, data=payload, headers={
            "Content-Type": "application/json",
            "X-API-Token": TEST_TOKEN
        })
        try:
            with urllib.request.urlopen(req) as resp:
                self.fail("Expected HTTP 409 ROW_STALE for mismatched fingerprint")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 409)
            err_data = json.loads(e.read().decode('utf-8'))
            self.assertEqual(err_data["error_code"], "ROW_STALE")

    def test_07b_render_endpoint(self):
        url = f"http://127.0.0.1:{TEST_PORT}/api/v1/render"
        payload = json.dumps({
            "text": "Hello world from http render test.",
            "language": "en",
            "text_mode": "single",
        }).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json",
            "X-API-Token": TEST_TOKEN,
        })
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            res = json.loads(resp.read().decode('utf-8'))
            self.assertEqual(res["status"], "success")
            self.assertTrue(res["data"]["ok"])
            self.assertIn("html_b64", res["data"])
            self.assertTrue(len(res["data"]["html_b64"]) > 0)

    def test_07c_render_language_mismatch_returns_422(self):
        url = f"http://127.0.0.1:{TEST_PORT}/api/v1/render"
        payload = json.dumps({
            "text": "Das ist ein schönes deutsches Haus für den HTTP Sprachtest.",
            "language": "en",
            "text_mode": "single",
            "bypass_lang_check": False,
        }).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json",
            "X-API-Token": TEST_TOKEN,
        })
        try:
            with urllib.request.urlopen(req) as resp:
                self.fail("Expected HTTP 422 LANGUAGE_MISMATCH for mismatched language")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 422)
            err_data = json.loads(e.read().decode('utf-8'))
            self.assertEqual(err_data["status"], "error")
            self.assertEqual(err_data["error_code"], "LANGUAGE_MISMATCH")
            self.assertEqual(err_data["details"]["detected_language"], "de")
            self.assertEqual(err_data["details"]["expected_language"], "en")
            self.assertIn(err_data["details"]["action"], ("prompt", "block"))

    def test_07d_render_language_mismatch_with_bypass(self):
        url = f"http://127.0.0.1:{TEST_PORT}/api/v1/render"
        payload = json.dumps({
            "text": "Das ist ein schönes deutsches Haus für den HTTP Sprachtest.",
            "language": "en",
            "text_mode": "single",
            "bypass_lang_check": True,
        }).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json",
            "X-API-Token": TEST_TOKEN,
        })
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            res = json.loads(resp.read().decode('utf-8'))
            self.assertEqual(res["status"], "success")
            self.assertTrue(res["data"]["ok"])
            self.assertIn("html_b64", res["data"])
            self.assertTrue(len(res["data"]["html_b64"]) > 0)

    def test_07e_worker_status_endpoint(self):
        # 1. Non-existent session returns 200 with all-None status
        url_none = f"http://127.0.0.1:{TEST_PORT}/session/non_existent_zid_9999/worker_status?token={TEST_TOKEN}"
        req_none = urllib.request.Request(url_none)
        with urllib.request.urlopen(req_none) as resp:
            self.assertEqual(resp.status, 200)
            res = json.loads(resp.read().decode('utf-8'))
            self.assertEqual(res["status"], "success")
            self.assertIsNone(res["data"]["worker_status"])
            self.assertIsNone(res["data"]["worker_started_at"])

        # 2. Unauthorized without token returns 403
        url_unauth = f"http://127.0.0.1:{TEST_PORT}/session/non_existent_zid_9999/worker_status"
        try:
            with urllib.request.urlopen(url_unauth):
                self.fail("Expected HTTP 403 Forbidden without token")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 403)

        # 3. Create a session, update worker lifecycle, and assert status transitions via endpoint
        from kardenwort_db import KardenwortDB
        test_db_p = Path(self.test_dir.name) / "test_kardenwort.db"
        db = KardenwortDB(db_path=test_db_p)
        db.run_migrations()
        sess_zid = "20260830235901"
        db.insert_session({
            "zid": sess_zid,
            "slug": "http-worker-status-test",
            "source_language": "en",
            "source_raw_text": "HTTP worker status lifecycle test",
        })

        # Set running
        t_start = "2026-08-30T23:00:00.000000+00:00"
        db.set_worker_status(sess_zid, "running", started_at=t_start)
        db.update_worker_heartbeat(sess_zid, heartbeat_at=t_start)

        url_running = f"http://127.0.0.1:{TEST_PORT}/session/{sess_zid}/worker_status"
        req_running = urllib.request.Request(url_running, headers={"X-API-Token": TEST_TOKEN})
        with urllib.request.urlopen(req_running) as resp:
            self.assertEqual(resp.status, 200)
            res = json.loads(resp.read().decode('utf-8'))
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["data"]["worker_status"], "running")
            self.assertEqual(res["data"]["worker_started_at"], t_start)
            self.assertEqual(res["data"]["worker_heartbeat_at"], t_start)

        # Set finished
        t_fin = "2026-08-30T23:00:05.000000+00:00"
        db.set_worker_status(sess_zid, "finished", finished_at=t_fin)
        with urllib.request.urlopen(req_running) as resp:
            self.assertEqual(resp.status, 200)
            res = json.loads(resp.read().decode('utf-8'))
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["data"]["worker_status"], "finished")
            self.assertEqual(res["data"]["worker_finished_at"], t_fin)

    def test_08_shutdown_endpoint(self):
        url = f"http://127.0.0.1:{TEST_PORT}/api/v1/shutdown"
        req = urllib.request.Request(url, data=b"{}", headers={
            "Content-Type": "application/json",
            "X-API-Token": TEST_TOKEN
        })
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            res = json.loads(resp.read().decode('utf-8'))
            self.assertEqual(res["status"], "success")


if __name__ == "__main__":
    unittest.main()
