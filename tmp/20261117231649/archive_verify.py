from pathlib import Path
import hashlib
import json
import os
import re
import sys

output = Path(__file__).resolve().parent
before = json.loads((output/'archive-preflight.json').read_text(encoding='utf-8'))
root = Path(before['root']).resolve()
source = Path(before['source']).resolve()
target = Path(before['target']).resolve()
assert source.is_relative_to(root/'changes') and target.is_relative_to(root/'changes/archive')

def files(directory):
    result = {}
    for current, directories, names in os.walk(directory, followlinks=False):
        for child in directories + names:
            path = Path(current)/child
            assert not path.is_symlink() and not path.is_junction(), str(path)
        for child in names:
            path = Path(current)/child
            result[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result

canonical = files(root/'specs')
changed = sorted(key for key in set(canonical) | set(before['canonical_before'])
    if canonical.get(key) != before['canonical_before'].get(key))
assert changed == ['same-lemma-highlight-toggle/spec.md'], changed
assert canonical[changed[0]] == before['preview_sha256']
phase = sys.argv[1]
if phase == 'before-archive':
    assert source.is_dir() and not target.exists()
    assert files(source) == before['change_before']
    print('Synced canonical matches approved preview; 160 unrelated specs and all 9 source files unchanged.')
elif phase == 'after-archive':
    assert not source.exists() and target.is_dir()
    archived = files(target)
    assert archived == before['change_before']
    tasks = (target/'tasks.md').read_text(encoding='utf-8-sig')
    assert len(re.findall(r'^- \[x\]', tasks, re.M)) == 9
    assert not re.findall(r'^- \[ \]', tasks, re.M)
    assert '.openspec.yaml' in archived
    result = dict(change=before['change'], source_absent=True, archive=str(target),
        archived_file_count=len(archived), all_archive_hashes_match=True,
        metadata_preserved=True, tasks_complete=9, tasks_incomplete=0,
        canonical_changed=changed, canonical_preview_hash_matches=True,
        unrelated_specs_unchanged=160)
    (output/'archive-verification.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
else:
    raise ValueError(phase)
