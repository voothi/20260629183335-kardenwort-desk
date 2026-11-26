"""Temporary legacy persistence shell; SQLite ownership is a separate migration."""
import os
from pathlib import Path
import re
import tempfile
import threading

from kardenwort_configuration import patch_ini_value


WRITE_LOCK = threading.RLock()


def replace_configuration(path, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.config-', suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_legacy_value(section, key, value, base_dir):
    path = Path(base_dir)/'config.ini'
    with WRITE_LOCK:
        content = path.read_bytes().decode('utf-8')
        changed = patch_ini_value(content, section, key, str(value))
        replace_configuration(path, changed.encode('utf-8'))
    return True


def write_legacy_language(language, base_dir):
    path = Path(base_dir)/'config.ini'
    with WRITE_LOCK:
        content = path.read_bytes().decode('utf-8-sig')
        pattern = re.compile(r'(?im)^([ \t]*default_language)[ \t]*=[^\r\n]*(\r?\n|$)')
        if not pattern.search(content):
            return False
        changed = pattern.sub(lambda match: match.group(1)+' = '+language+match.group(2), content)
        replace_configuration(path, changed.encode('utf-8'))
    return True
