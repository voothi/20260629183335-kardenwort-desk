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
        cls.config_path = DESK_DIR / "config.ini"

    def test_01_server_disabled_exit(self):
        """Verify server subcommand exits with non-zero code when enabled = false."""
        proc = subprocess.run(
            [PYTHON_EXE, str(DESK_DIR / "kardenwort_desk.py"), "server"],
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
        cls.config_path = DESK_DIR / "config.ini"
        with open(cls.config_path, "r", encoding="utf-8") as f:
            cls.original_config = f.read()

        with open(cls.config_path, "w", encoding="utf-8") as f:
            f.write(cls.original_config.replace("enabled = false", "enabled = true").replace("api_key =", f"api_key = {TEST_TOKEN}"))

        cls.server_proc = subprocess.Popen(
            [PYTHON_EXE, str(DESK_DIR / "kardenwort_desk.py"), "server", "--port", str(TEST_PORT)],
            cwd=str(DESK_DIR)
        )
        time.sleep(1.0)

    @classmethod
    def tearDownClass(cls):
        with open(cls.config_path, "w", encoding="utf-8") as f:
            f.write(cls.original_config)
        try:
            cls.server_proc.terminate()
            cls.server_proc.wait(timeout=2)
        except Exception:
            pass

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
