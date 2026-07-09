import sys
import json
import pytest
import io
import kardenwort_desk

def test_emit_payload_json(capfd):
    """1.10a: Test emit_payload with default JSON envelope."""
    kardenwort_desk.emit_payload({"status": "success"})
    captured = capfd.readouterr()
    out_str = captured.out.replace('\r\n', '\n') or captured.err.replace('\r\n', '\n')
    assert out_str.endswith('{"status": "success"}\n')

def test_emit_payload_raw(capfd):
    """1.10b: Test emit_payload with raw data."""
    kardenwort_desk.emit_payload("raw data", raw=True)
    captured = capfd.readouterr()
    out_str = captured.out.replace('\r\n', '\n') or captured.err.replace('\r\n', '\n')
    assert out_str.endswith("raw data\n")

def test_stdout_hijack_pollution(capfd):
    """1.10c: Test that a third-party print() to sys.stdout does NOT appear on sys.__stdout__."""
    original_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        print("pollution")
    finally:
        sys.stdout = original_stdout
        
    captured = capfd.readouterr()
    assert "pollution\n" in captured.err.replace('\r\n', '\n')
    assert captured.out == ""

def test_sys_excepthook(capfd):
    """1.10d: Test sys.excepthook emits a JSON error envelope."""
    try:
        raise ValueError("test exception")
    except ValueError as e:
        import traceback
        tb = e.__traceback__
        sys.excepthook(type(e), e, tb)
        
    captured = capfd.readouterr()
    assert '"error_code": "UNHANDLED_EXCEPTION"' in captured.err
    assert '"message": "test exception"' in captured.err
    assert "Traceback" in captured.err

def test_sys_excepthook_keyboardinterrupt(capfd):
    """1.10d: Test sys.excepthook suppresses traceback for KeyboardInterrupt."""
    try:
        raise KeyboardInterrupt()
    except KeyboardInterrupt as e:
        sys.excepthook(type(e), e, e.__traceback__)
        
    captured = capfd.readouterr()
    assert '"error_code": "INTERRUPTED"' in captured.err
    assert "Traceback" not in captured.err

def test_sys_excepthook_systemexit(capfd):
    """1.10d: Test that SystemExit(1) does NOT trigger the excepthook."""
    try:
        sys.exit(1)
    except SystemExit as e:
        sys.excepthook(type(e), e, e.__traceback__)
        
    captured = capfd.readouterr()
    assert captured.err == ""
    assert captured.out == ""

def test_emit_payload_base64(capfd):
    """1.10e: Test that base64 encode + emit_payload(raw=True) produces valid base64."""
    from b64util import encode
    html_data = "<div>test html</div>"
    encoded = encode(html_data)
    
    # Must have no embedded whitespace
    assert " " not in encoded
    assert "\n" not in encoded
    
    kardenwort_desk.emit_payload(encoded, raw=True)
    captured = capfd.readouterr()
    
    # Check output matches what AHK expects (base64 string + \n)
    out_str = captured.out.replace('\r\n', '\n') or captured.err.replace('\r\n', '\n')
    assert out_str.endswith(encoded + "\n")

def test_emit_payload_no_stdout_fallback(monkeypatch, capfd):
    """1.10f: Test the sys.__stdout__ is None fallback path."""
    monkeypatch.setattr(sys, '__stdout__', None)
    
    # We also need to monkeypatch sys.stderr so capfd can see it?
    # capfd captures fd 2. 
    kardenwort_desk.emit_payload({"fallback": True})
    captured = capfd.readouterr()
    
    assert captured.out == ""
    assert '{"fallback": true}\n' in captured.err
