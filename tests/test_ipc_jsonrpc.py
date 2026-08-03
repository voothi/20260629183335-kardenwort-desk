"""
tests/test_ipc_jsonrpc.py

Mock streaming socket server and JSON-RPC 2.0 protocol verification utilities
for Kardenwort-Desk IPC conformance testing.

Verifies bidirectional newline-delimited stream frame decoding and response
wrapping without any process re-spawns, using in-process socket pairs and
threading-based simulated daemons.

Coverage:
- JSON-RPC 2.0 request frame parsing from newline-delimited byte streams
- JSON-RPC 2.0 response and error frame encoding
- Bidirectional round-trip over a real TCP loopback socket pair
- Notification frame (server-push, id=null) framing
- Malformed / partial frame recovery (parse error -32700)
- Protocol-level error codes (-32600, -32601, -32602, -32603)
- Application-level error code mapping (-32000 range)
"""
import json
import socket
import threading
import time
import pytest


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 framing helpers (utilities under test)
# ---------------------------------------------------------------------------

JSONRPC_VERSION = "2.0"

KNOWN_METHODS = {
    "render", "edit-save", "retext", "reprocess",
    "lookup", "export", "merge", "shutdown",
}

# Standard error codes (JSON-RPC 2.0 spec)
PARSE_ERROR      = -32700
INVALID_REQUEST  = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS   = -32602
INTERNAL_ERROR   = -32603

# Application error codes (-32000 range)
APP_PROCESSING_ERROR   = -32000
APP_CONFIG_LOAD_FAILED = -32001
APP_COMMAND_FAILED     = -32002


def encode_frame(obj: dict) -> bytes:
    """Serialize a dict to a newline-terminated UTF-8 JSON frame."""
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def decode_frame(raw_line: bytes) -> dict:
    """
    Decode a single newline-terminated UTF-8 JSON frame.
    Raises json.JSONDecodeError on malformed frames.
    """
    return json.loads(raw_line.decode("utf-8").rstrip("\n"))


def make_request(method: str, params: dict | None = None,
                 request_id: str | int | None = "req-1") -> dict:
    """Build a valid JSON-RPC 2.0 request frame dict."""
    frame: dict = {"jsonrpc": JSONRPC_VERSION, "id": request_id, "method": method}
    if params is not None:
        frame["params"] = params
    return frame


def make_response(request_id: str | int | None, result: dict) -> dict:
    """Build a JSON-RPC 2.0 success response frame dict."""
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def make_error_response(request_id: str | int | None,
                        code: int, message: str,
                        error_code: str | None = None,
                        details: str | None = None) -> dict:
    """Build a JSON-RPC 2.0 error response frame dict."""
    error_obj: dict = {"code": code, "message": message}
    if error_code is not None:
        data: dict = {"error_code": error_code, "message": message}
        if details is not None:
            data["details"] = details
        error_obj["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error_obj}


def make_notification(event: str, stage: str | None = None,
                      status: str | None = None,
                      message: str | None = None,
                      rows: dict | None = None) -> dict:
    """
    Build a server-push JSON-RPC notification frame (id is always omitted /
    null per spec — notifications have no correlation identifier).
    """
    result: dict = {"notification": event}
    if stage is not None:
        result["stage"] = stage
    if status is not None:
        result["status"] = status
    if message is not None:
        result["message"] = message
    if rows is not None:
        result["rows"] = rows
    return {"jsonrpc": JSONRPC_VERSION, "id": None, "result": result}


# ---------------------------------------------------------------------------
# Minimal mock streaming IPC server
# ---------------------------------------------------------------------------

class MockIpcServer:
    """
    Minimal in-process TCP loopback mock server that speaks JSON-RPC 2.0
    over a newline-delimited streaming socket.

    Handles one client connection per invocation in a background thread.
    Demonstrates:
    - Bidirectional frame exchange
    - Method dispatch
    - Malformed frame recovery (parse error)
    - Graceful shutdown via 'shutdown' method
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((host, port))
        self._server_sock.listen(1)
        self.address = self._server_sock.getsockname()
        self._thread: threading.Thread | None = None
        self._received_frames: list[dict] = []
        self._sent_frames: list[dict] = []
        self._error: Exception | None = None

    # ------------------------------------------------------------------
    # Internal request dispatcher (simulates backend command handlers)
    # ------------------------------------------------------------------

    def _dispatch(self, frame: dict) -> dict:
        """Dispatch a parsed request frame to a result or error response."""
        req_id = frame.get("id")
        method = frame.get("method")

        if method not in KNOWN_METHODS:
            return make_error_response(
                req_id, METHOD_NOT_FOUND, "Method not found",
                error_code="COMMAND_NOT_FOUND",
            )

        if method == "render":
            params = frame.get("params", {})
            if "tsv_path" not in params or "config_path" not in params:
                return make_error_response(
                    req_id, INVALID_PARAMS, "Invalid params",
                    error_code="INVALID_PARAMS",
                )
            return make_response(req_id, {
                "status": "success",
                "output": "<html>mock render</html>",
                "row_count": 3,
            })

        if method == "lookup":
            params = frame.get("params", {})
            return make_response(req_id, {"match": {"word": params.get("query", "")}})

        if method == "export":
            return make_response(req_id, {
                "import_complete": True,
                "status": "success",
                "output": "SUCCESS: Exported to mock_path",
                "show_window": True,
                "message": None,
            })

        if method == "merge":
            return make_response(req_id, {
                "status": "success",
                "merged_count": 5,
                "duplicate_count": 1,
            })

        if method == "retext":
            return make_response(req_id, {"retext_started": True, "status": None})

        if method == "reprocess":
            return make_response(req_id, {"reprocess_started": True, "rows": 2})

        if method == "edit-save":
            return make_response(req_id, {"status": "success"})

        if method == "shutdown":
            return make_response(req_id, {"status": "shutting_down"})

        # Fallback internal error
        return make_error_response(
            req_id, INTERNAL_ERROR, "Internal error",
            error_code="PROCESSING_ERROR",
        )

    # ------------------------------------------------------------------
    # Connection handler (runs in thread)
    # ------------------------------------------------------------------

    def _handle_connection(self, conn: socket.socket) -> None:
        """Process one client connection: read frames, dispatch, respond."""
        buf = b""
        try:
            with conn:
                conn.settimeout(5.0)
                while True:
                    try:
                        chunk = conn.recv(4096)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        # Attempt to decode; respond with parse error on failure
                        try:
                            frame = decode_frame(line + b"\n")
                        except json.JSONDecodeError:
                            err = make_error_response(
                                None, PARSE_ERROR, "Parse error",
                                error_code="PARSE_ERROR",
                            )
                            self._sent_frames.append(err)
                            conn.sendall(encode_frame(err))
                            continue

                        self._received_frames.append(frame)

                        # Validate JSON-RPC envelope basics
                        if frame.get("jsonrpc") != JSONRPC_VERSION or "method" not in frame:
                            err = make_error_response(
                                frame.get("id"), INVALID_REQUEST, "Invalid Request",
                                error_code="INVALID_REQUEST",
                            )
                            self._sent_frames.append(err)
                            conn.sendall(encode_frame(err))
                            continue

                        response = self._dispatch(frame)
                        self._sent_frames.append(response)
                        conn.sendall(encode_frame(response))

                        # Shutdown closes the connection cleanly
                        if frame.get("method") == "shutdown":
                            return

        except OSError:
            pass
        except Exception as exc:
            self._error = exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Accept one client connection in a background thread."""
        def _serve():
            try:
                self._server_sock.settimeout(5.0)
                conn, _ = self._server_sock.accept()
                self._handle_connection(conn)
            except socket.timeout:
                pass
            except Exception as exc:
                self._error = exc
            finally:
                self._server_sock.close()

        self._thread = threading.Thread(target=_serve, daemon=True)
        self._thread.start()

    def join(self, timeout: float = 5.0) -> None:
        if self._thread:
            self._thread.join(timeout=timeout)

    @property
    def received_frames(self) -> list[dict]:
        return list(self._received_frames)

    @property
    def sent_frames(self) -> list[dict]:
        return list(self._sent_frames)

    @property
    def server_error(self) -> Exception | None:
        return self._error


# ---------------------------------------------------------------------------
# Client helper (used by tests)
# ---------------------------------------------------------------------------

class MockIpcClient:
    """Simple synchronous client for the mock server."""

    def __init__(self, address: tuple[str, int]):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.connect(address)
        self._sock.settimeout(5.0)
        self._buf = b""

    def send(self, frame: dict) -> None:
        self._sock.sendall(encode_frame(frame))

    def receive(self) -> dict:
        """Block until a complete newline-terminated frame is received."""
        while b"\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("Server closed connection before sending response")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return decode_frame(line + b"\n")

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def ipc_server_client():
    """Spin up a mock server and connect a client. Yields (server, client)."""
    server = MockIpcServer()
    server.start()
    with MockIpcClient(server.address) as client:
        yield server, client
    server.join()
    assert server.server_error is None, f"Server error: {server.server_error}"


# ---------------------------------------------------------------------------
# Tests: Frame encoding / decoding utilities
# ---------------------------------------------------------------------------

class TestFrameCodec:
    """Unit tests for encode_frame / decode_frame utilities."""

    def test_encode_frame_adds_newline(self):
        raw = encode_frame({"jsonrpc": "2.0", "id": 1, "method": "lookup"})
        assert raw.endswith(b"\n")

    def test_encode_frame_is_valid_json(self):
        obj = {"jsonrpc": "2.0", "id": "abc", "method": "render"}
        raw = encode_frame(obj)
        decoded = json.loads(raw.decode("utf-8").strip())
        assert decoded == obj

    def test_decode_frame_strips_newline(self):
        data = b'{"jsonrpc": "2.0", "id": 1, "method": "lookup"}\n'
        frame = decode_frame(data)
        assert frame["method"] == "lookup"

    def test_decode_frame_raises_on_malformed(self):
        with pytest.raises(json.JSONDecodeError):
            decode_frame(b"not valid json\n")

    def test_roundtrip_preserves_unicode(self):
        obj = {"jsonrpc": "2.0", "id": 1, "result": {"word": "Übersetzer"}}
        assert decode_frame(encode_frame(obj)) == obj


# ---------------------------------------------------------------------------
# Tests: Frame factory helpers
# ---------------------------------------------------------------------------

class TestFrameFactories:
    """Unit tests for make_request / make_response / make_error_response / make_notification."""

    def test_make_request_minimal(self):
        req = make_request("lookup")
        assert req["jsonrpc"] == "2.0"
        assert req["method"] == "lookup"
        assert req["id"] == "req-1"
        assert "params" not in req

    def test_make_request_with_params(self):
        req = make_request("render", {"tsv_path": "/a.tsv", "config_path": "/c.ini"}, request_id=42)
        assert req["id"] == 42
        assert req["params"]["tsv_path"] == "/a.tsv"

    def test_make_response_structure(self):
        resp = make_response("req-5", {"status": "success"})
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == "req-5"
        assert resp["result"]["status"] == "success"
        assert "error" not in resp

    def test_make_error_response_structure(self):
        err = make_error_response("req-7", INTERNAL_ERROR, "Internal error",
                                  error_code="PROCESSING_ERROR", details="trace")
        assert err["jsonrpc"] == "2.0"
        assert err["id"] == "req-7"
        assert err["error"]["code"] == INTERNAL_ERROR
        assert err["error"]["data"]["error_code"] == "PROCESSING_ERROR"
        assert err["error"]["data"]["details"] == "trace"
        assert "result" not in err

    def test_make_notification_has_null_id(self):
        notif = make_notification("progress", stage="translating", status="running",
                                  message="Processing row 3/10")
        assert notif["id"] is None
        assert notif["result"]["notification"] == "progress"
        assert notif["result"]["stage"] == "translating"

    def test_make_notification_minimal(self):
        notif = make_notification("stage_complete")
        assert notif["result"]["notification"] == "stage_complete"


# ---------------------------------------------------------------------------
# Tests: Bidirectional round-trip over mock server
# ---------------------------------------------------------------------------

class TestBidirectionalRoundTrip:
    """End-to-end streaming round-trip tests using MockIpcServer + MockIpcClient."""

    def test_lookup_round_trip(self, ipc_server_client):
        """lookup: request sent → server dispatches → response received."""
        server, client = ipc_server_client
        req = make_request("lookup", {"config_path": "/c.ini", "query": "Haus"}, request_id="r-1")
        client.send(req)
        resp = client.receive()
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == "r-1"
        assert "result" in resp
        assert resp["result"]["match"]["word"] == "Haus"

    def test_render_round_trip(self, ipc_server_client):
        """render: valid params → successful response with status='success'."""
        server, client = ipc_server_client
        req = make_request("render", {
            "config_path": "/c.ini",
            "tsv_path": "/data.tsv",
        }, request_id="r-2")
        client.send(req)
        resp = client.receive()
        assert resp["id"] == "r-2"
        assert resp["result"]["status"] == "success"
        assert resp["result"]["row_count"] == 3

    def test_render_missing_params_returns_invalid_params(self, ipc_server_client):
        """render with missing required params → -32602 Invalid params."""
        _, client = ipc_server_client
        req = make_request("render", {"config_path": "/c.ini"}, request_id="r-3")
        client.send(req)
        resp = client.receive()
        assert resp["id"] == "r-3"
        assert "error" in resp
        assert resp["error"]["code"] == INVALID_PARAMS

    def test_unknown_method_returns_method_not_found(self, ipc_server_client):
        """Calling an unknown method → -32601 Method not found."""
        _, client = ipc_server_client
        req = make_request("nonexistent-command", request_id="r-4")
        client.send(req)
        resp = client.receive()
        assert resp["id"] == "r-4"
        assert resp["error"]["code"] == METHOD_NOT_FOUND

    def test_malformed_frame_returns_parse_error(self, ipc_server_client):
        """Sending a non-JSON line → server responds with -32700 Parse error."""
        _, client = ipc_server_client
        client._sock.sendall(b"this is not json at all\n")
        resp = client.receive()
        assert resp["id"] is None
        assert resp["error"]["code"] == PARSE_ERROR

    def test_invalid_jsonrpc_envelope_returns_invalid_request(self, ipc_server_client):
        """JSON frame missing 'method' key → -32600 Invalid Request."""
        _, client = ipc_server_client
        bad_frame = {"jsonrpc": "2.0", "id": "r-5"}   # no method
        client.send(bad_frame)
        resp = client.receive()
        assert resp["id"] == "r-5"
        assert resp["error"]["code"] == INVALID_REQUEST

    def test_shutdown_closes_connection(self, ipc_server_client):
        """'shutdown' method → server responds shutting_down and terminates stream."""
        server, client = ipc_server_client
        req = make_request("shutdown", request_id="r-99")
        client.send(req)
        resp = client.receive()
        assert resp["result"]["status"] == "shutting_down"
        # After shutdown, server should close the connection
        server.join(timeout=3.0)
        assert server.server_error is None

    def test_multiple_sequential_requests(self, ipc_server_client):
        """Multiple sequential requests over same connection maintain correlation IDs."""
        _, client = ipc_server_client
        methods = [
            ("lookup",  {"config_path": "/c.ini", "query": "Wort"}, "m-1"),
            ("export",  {"config_path": "/c.ini", "tsv_path": "/d.tsv"}, "m-2"),
            ("merge",   {"config_path": "/c.ini", "tsv_path": "/d.tsv", "source_path": "/s.tsv"}, "m-3"),
        ]
        for method, params, req_id in methods:
            client.send(make_request(method, params, request_id=req_id))
            resp = client.receive()
            assert resp["id"] == req_id, f"Correlation mismatch for {method}"
            assert "result" in resp, f"Expected result for {method}"


# ---------------------------------------------------------------------------
# Tests: Notification frame framing
# ---------------------------------------------------------------------------

class TestNotificationFrames:
    """Verify server-push notification frame structure and encoding."""

    def test_notification_frame_encodes_correctly(self):
        notif = make_notification("progress", stage="tokenizing", status="running",
                                  message="3/10 rows processed")
        raw = encode_frame(notif)
        decoded = decode_frame(raw)
        assert decoded["id"] is None
        assert decoded["result"]["notification"] == "progress"
        assert decoded["result"]["stage"] == "tokenizing"

    def test_notification_frame_has_no_error_key(self):
        notif = make_notification("stage_complete", stage="translating", status="done")
        assert "error" not in notif

    def test_notification_roundtrip_with_rows_payload(self):
        rows = {"0": {"lemma": "Haus", "trans": "house"}}
        notif = make_notification("stage_complete", stage="translated", status="success", rows=rows)
        decoded = decode_frame(encode_frame(notif))
        assert decoded["result"]["rows"]["0"]["lemma"] == "Haus"


# ---------------------------------------------------------------------------
# Tests: Application error code mapping
# ---------------------------------------------------------------------------

class TestApplicationErrorCodes:
    """Verify application-domain error code frames (-32000 range)."""

    def test_app_processing_error_code(self):
        err = make_error_response("req-e1", APP_PROCESSING_ERROR, "Internal error",
                                  error_code="PROCESSING_ERROR", details="traceback here")
        assert err["error"]["code"] == APP_PROCESSING_ERROR
        assert err["error"]["data"]["error_code"] == "PROCESSING_ERROR"
        assert err["error"]["data"]["details"] == "traceback here"

    def test_app_config_load_failed_code(self):
        err = make_error_response("req-e2", APP_CONFIG_LOAD_FAILED, "Internal error",
                                  error_code="CONFIG_LOAD_FAILED")
        assert err["error"]["code"] == APP_CONFIG_LOAD_FAILED

    def test_error_frame_has_no_result_key(self):
        err = make_error_response(None, PARSE_ERROR, "Parse error",
                                  error_code="PARSE_ERROR")
        assert "result" not in err
        assert err["id"] is None
