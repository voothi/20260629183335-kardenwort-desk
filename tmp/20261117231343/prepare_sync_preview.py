from pathlib import Path
import re
import json
import difflib
import hashlib

root = Path('U:/voothi.vault/kardenwort-desk/openspec')
name = '20261117192122-restore-same-lemma-table-highlighting'
source = root/'specs/same-lemma-highlight-toggle/spec.md'
delta = root/'changes'/name/'specs/same-lemma-highlight-toggle/spec.md'
original = source.read_text(encoding='utf-8-sig')
proposed = delta.read_text(encoding='utf-8-sig')
pattern = re.compile(r'^### Requirement: ([^\n]+)\n.*?(?=^### Requirement: |\Z)', re.M | re.S)
current = {match[1]: match[0] for match in pattern.finditer(original)}
modified = {match[1]: match[0] for match in pattern.finditer(proposed)}
assert set(modified) <= set(current)
merged = pattern.sub(lambda match: modified.get(match[1], match[0]), original)
retained = {match[1]: match[0] for match in pattern.finditer(merged)}
assert set(retained) == set(current)
assert all(retained[key] == value for key, value in current.items() if key not in modified)
scenarios = lambda value: re.findall(r'^#### Scenario: ([^\n]+)$', value, re.M)
old_scenarios = scenarios(original)
new_scenarios = scenarios(merged)
assert set(old_scenarios) <= set(new_scenarios)
output = Path(__file__).resolve().parent
target = output/'sync-preview/openspec/specs/same-lemma-highlight-toggle/spec.md'
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(merged, encoding='utf-8', newline='\n')
(output/'spec-sync.diff').write_text(''.join(difflib.unified_diff(
    original.splitlines(True), merged.splitlines(True), fromfile=str(source),
    tofile='proposed/same-lemma-highlight-toggle/spec.md')), encoding='utf-8')
summary = dict(change=name, capability='same-lemma-highlight-toggle',
    modified_requirements=list(modified),
    unaffected_requirements=[key for key in current if key not in modified],
    added_scenarios=[item for item in new_scenarios if item not in old_scenarios],
    original_scenario_count=len(old_scenarios), proposed_scenario_count=len(new_scenarios),
    canonical_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    archive_target_exists=(root/'changes/archive'/name).exists())
(output/'sync-preview.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary, indent=2))
