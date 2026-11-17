from pathlib import Path
import hashlib
import json
import os
import re

root = Path('U:/voothi.vault/kardenwort-desk/openspec').resolve()
name = '20261117192122-restore-same-lemma-table-highlighting'
source = (root/'changes'/name).resolve()
target = (root/'changes/archive'/name).resolve()
assert source.is_relative_to(root/'changes') and target.is_relative_to(root/'changes/archive')
assert source.is_dir() and not target.exists()

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
change = files(source)
tasks = (source/'tasks.md').read_text(encoding='utf-8-sig')
complete = len(re.findall(r'^- \[x\]', tasks, re.M))
incomplete = len(re.findall(r'^- \[ \]', tasks, re.M))
assert (complete, incomplete) == (9, 0)
assert '.openspec.yaml' in change
preview = Path('U:/voothi/20260629183335-kardenwort-desk/tmp/20261117231343/sync-preview')
assert canonical['same-lemma-highlight-toggle/spec.md'] == 'f670940e7b6d4f3204572641f85e739c1ce227d98a4fde73704ff0af7acad06f'
summary = dict(change=name, root=str(root), source=str(source), target=str(target),
    approved_disposition='Sync reviewed specs, then archive; user continued after recommended disposition and exact preview',
    canonical_before=canonical, change_before=change, tasks_complete=complete, tasks_incomplete=incomplete,
    preview_sha256=hashlib.sha256((preview/'openspec/specs/same-lemma-highlight-toggle/spec.md').read_bytes()).hexdigest())
output = Path(__file__).resolve().parent
(output/'archive-preflight.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(dict(canonical_specs=sum(key.endswith('/spec.md') for key in canonical),
    archived_files_expected=len(change), tasks_complete=complete, tasks_incomplete=incomplete,
    source=str(source), target=str(target), preview_sha256=summary['preview_sha256']), indent=2))
