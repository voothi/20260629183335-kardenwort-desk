import pytest
import b64util

def test_b64util_encode_decode_roundtrip():
    original = "Haus, дом, 🚀 & test"
    encoded = b64util.encode(original)
    assert isinstance(encoded, str)
    decoded = b64util.decode(encoded)
    assert decoded == original

def test_b64util_null_handling():
    assert b64util.encode(None) == ""
    assert b64util.decode(None) == ""

def test_b64util_empty_string():
    assert b64util.encode("") == ""
    assert b64util.decode("") == ""
