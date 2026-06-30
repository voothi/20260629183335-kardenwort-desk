import base64

def encode(payload: str) -> str:
    """Encode string payload to a Base64 ASCII string."""
    if payload is None:
        payload = ""
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")

def decode(b64: str) -> str:
    """Decode a Base64 string back to a UTF-8 string."""
    if b64 is None:
        return ""
    return base64.b64decode(b64.encode("ascii")).decode("utf-8")
