import os
import json
import time
import urllib.request
import urllib.error
import subprocess
import unittest
from pathlib import Path

DESK_DIR = Path(__file__).resolve().parent.parent
PYTHON_EXE = os.sys.executable
TEST_PORT = 18399
TEST_TOKEN = "test-secret-token-12345"


class TestHTTPServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.disabled_config = DESK_DIR / ".test_disabled_config.ini"
        with open(DESK_DIR / "config.ini", "r", encoding="utf-8") as f:
            base_cfg = f.read()
        import re
        disabled_content = re.sub(r'enabled\s*=\s*true', 'enabled = false', base_cfg, flags=re.IGNORECASE)
        with open(cls.disabled_config, "w", encoding="utf-8") as f:
            f.write(disabled_content)

    @classmethod
    def tearDownClass(cls):
        if cls.disabled_config.exists():
            try:
                os.remove(cls.disabled_config)
            except OSError:
                pass

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
            [PYTHON_EXE, str(DESK_DIR / "kardenwort_desk.py"), "server", "--host", "192.168.1.1"],
            cwd=str(DESK_DIR),
            capture_output=True,
            text=True
        )
        self.assertNotEqual(proc.returncode, 0, "Server must reject non-loopback host binding")
        self.assertIn("CONFIGURATION_ERROR", proc.stderr + proc.stdout)


class TestHTTPServerRunning(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.test_dir = tempfile.TemporaryDirectory()
        test_dir_p = Path(cls.test_dir.name)
        test_db_p = (test_dir_p / "test_kardenwort.db").resolve()
        test_res_p = (test_dir_p / "results").resolve()
        test_res_p.mkdir(parents=True, exist_ok=True)

        cls.running_config = DESK_DIR / ".test_running_config.ini"
        with open(DESK_DIR / "config.ini", "r", encoding="utf-8") as f:
            base_cfg = f.read()
        import re
        running_content = re.sub(r'enabled\s*=\s*false', 'enabled = true', base_cfg, flags=re.IGNORECASE)
        running_content = re.sub(r'api_key\s*=.*', f'api_key = {TEST_TOKEN}', running_content)
        running_content = re.sub(r'port\s*=.*', f'port = {TEST_PORT}', running_content)
        running_content = re.sub(r'sqlite_db_path\s*=.*', f'sqlite_db_path = {test_db_p.as_posix()}', running_content)
        running_content = re.sub(r'results_dir\s*=.*', f'results_dir = {test_res_p.as_posix()}', running_content)
        with open(cls.running_config, "w", encoding="utf-8") as f:
            f.write(running_content)

        cls.server_proc = subprocess.Popen(
            [PYTHON_EXE, str(DESK_DIR / "kardenwort_desk.py"), "--config", str(cls.running_config), "server", "--port", str(TEST_PORT)],
            cwd=str(DESK_DIR)
        )
        time.sleep(1.2)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.server_proc.terminate()
            cls.server_proc.wait(timeout=2)
        except Exception:
            pass
        if cls.running_config.exists():
            try:
                os.remove(cls.running_config)
            except OSError:
                pass
        if hasattr(cls, 'test_dir'):
            cls.test_dir.cleanup()

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
