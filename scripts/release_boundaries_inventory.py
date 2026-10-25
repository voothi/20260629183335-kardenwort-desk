"""Read-only phase-1 inventory; no compiler invocation, cleanup, or Git mutation."""
import argparse
from collections import Counter
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'results' / '20261025104755-release-boundaries'
CANDIDATES = (
    'results/20261024225159-typescript-cache',
    'results/20261024225159-type-baseline',
)
PATTERNS = {
    'declaration_reference': r'///\s*<reference',
    'compiler_directive': r'@ts-(?:check|ignore|expect-error|nocheck)',
    'import_annotation': r'import\([\'"]',
    'type_assertion': r'/\*\*\s*@type\s*\{[^\n]+\}\s*\*/\s*\(',
    'desk_annotation': r'Desk\.',
    'domain_definition': r'@typedef|\b(?:interface|type)\s+(?:Token|Card|AppState|WorkspaceCard|SessionState)\b',
}
CALLERS = re.compile(r'(?:SourceViewer|TranslationView|LemmaTable)\.(?:renderRows|render|project|init|dispose)|splitTextTokens|AppView\.|WorkspaceTabs\.')


def files_under(directory, excluded=EVIDENCE):
    """Do not traverse junctions/symlinks, including nested output reparse points."""
    if not directory.exists() or directory.is_junction() or directory.is_symlink():
        return
    for folder, dirs, names in os.walk(directory, followlinks=False):
        base = Path(folder)
        dirs[:] = sorted(name for name in dirs
                         if base / name != excluded
                         and not (base / name).is_junction()
                         and not (base / name).is_symlink())
        for name in sorted(names):
            path = base / name
            if not path.is_symlink():
                yield path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected_snapshot():
    paths = [ROOT / 'config.ini', ROOT / 'anki-mapping.ini']
    for directory in ('data', 'favorites', 'results', 'scratch'):
        paths.extend(files_under(ROOT / directory))
    return {p.relative_to(ROOT).as_posix(): {'bytes': p.stat().st_size, 'sha256': digest(p)}
            for p in paths if p.is_file()}


def git(*args):
    result = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, check=True)
    return result.stdout


def source_inventory():
    annotations, callers = [], []
    for name in git('ls-files').splitlines():
        path = ROOT / name
        if path.suffix not in ('.js', '.ts', '.json', '.py', '.md', '.ahk'):
            continue
        for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
            kinds = [key for key, pattern in PATTERNS.items() if re.search(pattern, line)]
            if name.startswith('web/') and kinds:
                annotations.append(dict(path=name, line=number, kinds=kinds, text=line.strip()))
            if CALLERS.search(line):
                callers.append(dict(path=name, line=number, text=line.strip()))
    return dict(annotations=annotations, callers=callers)


def artifact_inventory():
    result = []
    for name in CANDIDATES:
        directory = ROOT / name
        files = list(files_under(directory))
        result.append(dict(path=name, resolved=str(directory.resolve()),
                           reparse=directory.is_junction() or directory.is_symlink(),
                           files=len(files), bytes=sum(p.stat().st_size for p in files),
                           contents=[p.relative_to(directory).as_posix() for p in files]))
    return result


def review_details():
    """Produce review aids, never execute historical compiler/mutation runners."""
    original = json.loads((EVIDENCE / 'inventory.json').read_text(encoding='utf-8'))
    counts = Counter(kind for row in original['annotations'] for kind in row['kinds'])
    paths = list(CANDIDATES)
    paths += ['results/20261024225159-type-analysis.py', 'results/20261025010043-close-types.cjs']
    for pattern in ('20261024225159-typescript-*.txt', '20261025005231-*-types-*.txt',
                    '20261025005752-source-types.txt', '20261025094114-editor-types.*'):
        paths.extend(p.relative_to(ROOT).as_posix() for p in (ROOT / 'results').glob(pattern))
    candidates = []
    for name in sorted(set(paths)):
        path = ROOT / name
        files = list(files_under(path)) if path.is_dir() else [path]
        candidates.append(dict(path=name, files=len(files), bytes=sum(p.stat().st_size for p in files),
                               decision='candidate only; phase 5 containment and dependency checks required'))
    (EVIDENCE / 'removal-candidates.json').write_text(json.dumps(candidates, indent=2), encoding='utf-8')
    # Removing JSDoc and full-line comments isolates executable review candidates.
    # This lexical aid is not a JavaScript parser or equivalence certification.
    def executable_lines(source):
        source = re.sub(r'/\*\*.*?\*/', '', source, flags=re.S)
        return [line.strip() + '\n' for line in source.splitlines()
                if line.strip() and not line.strip().startswith('//')]
    review = []
    for name in git('ls-files', 'web/js/*.js', 'web/js/**/*.js').splitlines():
        old = git('show', 'c481c9b:' + name)
        current = (ROOT / name).read_text(encoding='utf-8')
        review.extend(difflib.unified_diff(executable_lines(old), executable_lines(current),
                                          fromfile='c481c9b/' + name, tofile='24d47ab/' + name))
    (EVIDENCE / 'runtime-review.diff').write_text(''.join(review), encoding='utf-8')
    print(json.dumps(dict(annotation_counts=counts, candidate_files=sum(p['files'] for p in candidates),
                          candidate_bytes=sum(p['bytes'] for p in candidates), runtime_review_lines=len(review))))


def verify_evidence():
    cases = ET.parse(EVIDENCE / 'contracts.xml').findall('.//testcase')
    failures = []
    for case in cases:
        name = case.attrib['name']
        expected = (
            name.startswith('test_disjoint_remote_') and ('new-local-reversal' in name or 'event-before-ack' in name)
            or name.startswith(('test_failed_save_retry_', 'test_independent_native_', 'test_native_lifecycle_',
                                'test_frontend_is_legacy_', 'test_authenticated_membership_roundtrip_is_read_only[tsv]'))
            or name.startswith('test_invalid_or_unavailable_') and ('not-a-session' in name or '20990101000000' in name))
        failure = case.find('failure')
        assert case.find('error') is None, name
        assert (failure is not None) == expected, name
        if failure is not None:
            assert 'TimeoutError' not in failure.attrib.get('message', ''), name
            assert 'ReferenceError' not in failure.attrib.get('message', ''), name
            failures.append(name)
    assert len(cases) == 27 and len(failures) == 19
    controls = ET.parse(EVIDENCE / 'controls.xml').findall('.//testcase')
    assert len(controls) == 5
    assert all(case.find('failure') is None and case.find('error') is None for case in controls)
    for suite in ('contracts', 'controls'):
        run = json.loads((EVIDENCE / (suite + '-execution.json')).read_text(encoding='utf-8'))
        assert run['protected_db_before'] == run['protected_db_after']
        assert run['original_legacy_test_sha256'] == run['copied_legacy_test_sha256']
        assert run['temporary_checkout_removed']
    result = dict(contract_tests=27, expected_failures=19, passes=8, positive_controls=5,
                  failures=failures, original_database_unchanged_during_isolated_runs=True)
    (EVIDENCE / 'verified-results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('capture', 'compare', 'details', 'verify'))
    args = parser.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    if args.operation == 'verify':
        verify_evidence()
        return
    if args.operation == 'details':
        review_details()
        return
    if args.operation == 'capture' and (EVIDENCE / 'inventory.json').exists():
        raise FileExistsError('Refusing to overwrite the phase-1 protected-file baseline')
    snapshot = protected_snapshot()
    if args.operation == 'capture':
        payload = dict(head=git('rev-parse', 'HEAD').strip(), protected=snapshot,
                       artifacts=artifact_inventory(), **source_inventory())
        (EVIDENCE / 'inventory.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
        (EVIDENCE / 'desk-release.diff').write_text(git('diff', '1ac6727..24d47ab'), encoding='utf-8')
        print(json.dumps(dict(protected_files=len(snapshot), protected_bytes=sum(v['bytes'] for v in snapshot.values()),
                              annotations=len(payload['annotations']), callers=len(payload['callers']),
                              artifacts=[{k: v for k, v in a.items() if k != 'contents'} for a in payload['artifacts']])))
        return
    before = json.loads((EVIDENCE / 'inventory.json').read_text(encoding='utf-8'))['protected']
    changes = {key: ('added' if key not in before else 'removed' if key not in snapshot else 'changed')
               for key in before.keys() | snapshot.keys() if before.get(key) != snapshot.get(key)}
    (EVIDENCE / 'protected-comparison.json').write_text(json.dumps(changes, indent=2), encoding='utf-8')
    print(json.dumps(dict(protected_files=len(snapshot), changes=changes)))
    raise SystemExit(bool(changes))


if __name__ == '__main__':
    main()
