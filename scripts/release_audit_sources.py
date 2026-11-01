"""Owned release materialization and process isolation for comparison evidence."""
import argparse
import configparser
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2), encoding='utf-8')


def git(root, *args):
    return subprocess.check_output(['git', '-c', 'safe.directory=' + root.as_posix(),
        '-C', str(root), *args], stderr=subprocess.PIPE)


def resolve(root, reference):
    try:
        return git(root, 'rev-parse', '--verify', reference + '^{commit}').decode().strip()
    except subprocess.CalledProcessError as error:
        raise ValueError('Cannot resolve reference ' + reference) from error


def runtime_path(name):
    path = Path(name)
    return (len(path.parts) == 1 and path.suffix == '.py') or path.parts[0] in ('web', 'schemas')


def tracked_runtime(root, commit):
    names = git(root, 'ls-tree', '-r', '--name-only', commit).decode().splitlines()
    return [name for name in names if runtime_path(name)]


def copy_working_tree(root, destination, names):
    names = working_runtime_names(root, names)
    for name in names:
        source = root / name
        if not source.resolve().is_relative_to(root.resolve()):
            raise ValueError('Foreign runtime path: ' + name)
        if not source.is_file():
            raise ValueError('Missing working-tree runtime asset: ' + name)
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def working_runtime_names(root, tracked):
    files = list(root.glob('*.py'))
    for directory in ('web', 'schemas'):
        files.extend(p for p in (root / directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    return sorted(set(tracked) | {p.relative_to(root).as_posix() for p in files})


def workspace_runtime_hashes(root):
    names = working_runtime_names(root, tracked_runtime(root, resolve(root, 'HEAD')))
    return {name: digest(root / name) if (root / name).is_file() else None for name in names}


def copy_revision(root, destination, commit, names):
    # Archive bytes are read-only Git output; extraction accepts only enumerated
    # regular runtime files and never trusts archive paths or link targets.
    content = git(root, 'archive', commit)
    with tarfile.open(fileobj=io.BytesIO(content)) as archive:
        for name in names:
            member = archive.getmember(name)
            target = (destination / name).resolve()
            if not member.isfile() or not target.is_relative_to(destination.resolve()):
                raise ValueError('Invalid historical runtime path: ' + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())


def source_hashes(source):
    return {p.relative_to(source).as_posix(): digest(p) for p in sorted(source.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts}


def materialize(root, output, label, reference, working=False):
    commit = resolve(root, reference)
    source = output / label / 'source'
    source.mkdir(parents=True)
    names = tracked_runtime(root, commit)
    copier = copy_working_tree if working else copy_revision
    if working:
        copier(root, source, names)
    else:
        copier(root, source, commit, names)
    status = git(root, 'status', '--porcelain', '--', '*.py', 'web', 'assets', 'schemas').decode().strip() if working else ''
    return dict(mode='working-tree' if working else 'revision', requested_ref=reference,
                commit=commit, source=str(source), dirty=bool(status), dirty_status=status,
                source_hashes=source_hashes(source))


def protected_snapshot(root):
    files = [root / 'config.ini', root / 'anki-mapping.ini', *(root / 'data').glob('*.db*')]
    return {str(p.relative_to(root)): digest(p) for p in files if p.is_file()}


def parse_arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-ref', default='56f8696')
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument('--target-ref')
    targets.add_argument('--working-tree', action='store_true')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--reserve-mb', type=float, default=128)
    return parser.parse_args()


def render_worker(root, output, label, identity, metrics):
    job = output / label / 'job.json'
    write_json(job, dict(source=identity['source'], owner=str(output / label / 'fixture'),
        root=str(root), output=str(output), label=label, metrics=metrics,
        expected_hashes=identity['source_hashes']))
    command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--worker', str(job)]
    process = subprocess.run(command, capture_output=True, text=True, timeout=90)
    (output / label / 'worker.log').write_text(process.stdout + process.stderr, encoding='utf-8')
    identity.update(command=command, exit_code=process.returncode)
    if process.returncode:
        raise RuntimeError(label + ' renderer failed: ' + process.stdout + process.stderr)
    result = json.loads((output / label / 'render.json').read_text(encoding='utf-8'))
    identity.update(runtime=result.pop('runtime'))
    return result


def run_comparison(root, tool, metrics, matrix):
    # Tool guards are shared with the test runner; renderer processes import
    # domain code only after selecting their isolated source directory.
    from scripts import run_dict_transport_audit as audit
    options = parse_arguments()
    output = audit.require_owned(options.output_dir, root)
    if output.exists():
        print('Output already exists: ' + str(output), file=sys.stderr)
        return 2
    output.mkdir(parents=True)
    before = protected_snapshot(root)
    source_before = workspace_runtime_hashes(root)
    record = dict(command=sys.argv, output=str(output), protected_before=before,
                  source_before=source_before, runtime_scope='top-level Python, desktop web assets and schemas',
                  measurement_scope='dark desktop idle, active and hover metrics; pairwise matrix defined only',
                  tool_hashes={str(tool): digest(tool), str(Path(__file__).resolve()): digest(__file__)})
    code = 2
    try:
        record.update(audit.prepare_backup(output, before, int(options.reserve_mb * 1024 * 1024), root=root))
        record['baseline'] = materialize(root, output, 'baseline', options.baseline_ref)
        record['target'] = materialize(root, output, 'target', options.target_ref or 'HEAD', options.working_tree)
        versions = {label: render_worker(root, output, label, record[label], metrics) for label in ('baseline', 'target')}
        write_json(output / 'styles.json', dict(baseline=record['baseline']['commit'],
            target=record['target']['commit'], versions=versions))
        write_json(output / 'matrix.json', matrix)
        code = 0
    except Exception as error:
        record['error'] = str(error)
        print(str(error), file=sys.stderr)
    after = protected_snapshot(root)
    source_after = workspace_runtime_hashes(root)
    record.update(exit_code=code if before == after and source_before == source_after else 99,
                  source_after=source_after, source_unchanged=source_before == source_after,
                  protected_after=after, protected_unchanged=before == after)
    write_json(output / 'execution.json', record)
    return record['exit_code']


def fixture_config(root, owner):
    config = configparser.ConfigParser(interpolation=None)
    config.read(root / 'config.ini', encoding='utf-8-sig')
    scrub_credentials(config)
    for key, value in config['environment'].items():
        if value:
            path = Path(value)
            config.set('environment', key, str(path if path.is_absolute() else (root / path).resolve()))
    values = {'storage': {'backend': 'tsv', 'sqlite_db_path': str(owner / 'fixture.db')},
        'settings': {'anki_mapping_file': str(owner / 'anki-mapping.ini')},
        'project_structure': {'generated_results_dir': str(owner / 'results')},
        'wordfill': {'enabled': 'false'}, 'server': {'enabled': 'false', 'api_key': 'fixture-token'},
        'sentences_mode': {'enabled': 'false', 'delivery_mode': 'container'},
        'rendering': {'filter_selected_only': 'false', 'highlight_same_lemma': 'false', 'auto_save_on_edit': 'false'},
        'triggers': {key: 'manual' for key in ('run_text_translation', 'run_lemma_base_translation', 'run_lemma_enrichment')}}
    config.read_dict(values)
    shutil.copyfile(root / 'anki-mapping.ini', owner / 'anki-mapping.ini')
    path = owner / 'config.ini'
    with path.open('w', encoding='utf-8') as destination:
        config.write(destination)
    return path


def scrub_credentials(config):
    for section in config.sections():
        for key in config[section]:
            if sensitive_option(key.lower()):
                config.set(section, key, '')


def sensitive_option(key):
    return key in ('token', 'password', 'secret', 'authorization') or key.endswith(('_token', '_key', '_password', '_secret'))


def load_guard():
    path = Path(__file__).with_name('run_dict_transport_audit.py')
    spec = importlib.util.spec_from_file_location('comparison_guard', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runtime_identity(source):
    domain = {'b64util', 'text_tokenizer', 'http_server'}
    modules = {name: Path(module.__file__).resolve() for name, module in sys.modules.items()
               if getattr(module, '__file__', None) and (name.startswith('kardenwort_') or name in domain)}
    validate_module_paths(source, modules)
    return {name: dict(path=str(path), sha256=digest(path)) for name, path in modules.items()}


def validate_module_paths(source, modules):
    for name, path in modules.items():
        if not path.is_relative_to(source.resolve()):
            raise ValueError('Foreign runtime import: ' + name + ' at ' + str(path))


def render_fixture(module, owner, config_path):
    config, paths, _, _ = module.load_config(config_path)
    paths['storage_backend'] = 'tsv'
    tsv = owner / '20261023221555-audit.de.tsv'
    tsv.write_text('Quotation\tWordSource\tWordDestination\tSentenceSourceIndex\tSentenceSource\tSentenceDestination\tDeskSelected\n'
        'Haus\tHaus\thouse\t1\tDas Haus.\tThe house.\t1\n'
        'Baum\tBaum\ttree\t2\tDer Baum.\tThe tree.\t0\n', encoding='utf-8')
    return module.run_render_flow(text='Das Haus. Der Baum.', language='de', zid='20261023221555',
        text_mode='single', config=config, resolved_paths=paths, theme='dark', tsv_path=str(tsv), spawn_children=False)


TRANSPORT = """<script>
window.EventSource=undefined;
window.fetch=function(){return Promise.resolve({ok:true,status:200,json:function(){return Promise.resolve({ok:true,stage:'finished'});}});};
</script>"""


def measure(page, html, metrics, output):
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.set_content(html.replace('<head>', '<head>' + TRANSPORT))
    page.wait_for_function("typeof setFilterSelectedOnly === 'function'")
    idle = page.evaluate(metrics)
    page.evaluate('setFilterSelectedOnly(true); setHighlightSameLemma(true)')
    active = page.evaluate(metrics)
    page.locator('#kw-btn-delete').hover()
    hover = page.evaluate(metrics)
    page.screenshot(path=str(output), full_page=True)
    if errors:
        raise ValueError('Generated page errors: ' + repr(errors))
    return dict(idle=idle, active=active, delete_hover=hover['delete'], page_errors=errors)


def worker(job_path):
    job = json.loads(job_path.read_text(encoding='utf-8'))
    source, owner = Path(job['source']), Path(job['owner'])
    owner.mkdir(parents=True)
    sys.path.insert(0, str(source))
    import kardenwort_desk as desk
    import kardenwort_controller
    import kardenwort_db
    audit = load_guard()
    sys.path = [str(source), *(p for p in sys.path if Path(p).resolve() != audit.ROOT.resolve())]
    audit.OUT = owner
    before = audit.protected_snapshot()
    config = fixture_config(Path(job['root']), owner)
    runtime_identity(source)
    from playwright.sync_api import sync_playwright
    with audit.owned_runtime(owner, audit.Isolation(before, 'comparison')):
        html = render_fixture(desk, owner, config)
        (owner / 'rendered.html').write_text(html, encoding='utf-8')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                result = measure(page, html, job['metrics'], Path(job['output']) / (job['label'] + '.png'))
                result['runtime'] = dict(python=sys.version, platform=platform.platform(), browser=browser.version,
                    modules=runtime_identity(source), fixture_hashes=source_hashes(owner),
                    renderer='external-assets' if (source / 'web').exists() else 'embedded-assets')
            finally:
                browser.close()
    if audit.protected_snapshot() != before or source_hashes(source) != job['expected_hashes']:
        raise ValueError('Comparison changed protected files or selected runtime sources')
    write_json(Path(job['output']) / job['label'] / 'render.json', result)


if __name__ == '__main__':
    worker(Path(sys.argv[2]))
