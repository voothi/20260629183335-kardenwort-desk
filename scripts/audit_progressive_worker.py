"""Audit-only preflight for the selected worker's actual stdin transport."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys


def require_path(path, owner):
    if not Path(path).resolve().is_relative_to(owner):
        raise ValueError('Audit worker destination is outside its owner')


def file_effects(owner, event, args):
    if event == 'open':
        path, _, flags = args
        if isinstance(path, (str, bytes, os.PathLike)) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
            require_path(os.fsdecode(path), owner)
    elif event in ('os.mkdir', 'os.remove', 'os.rmdir', 'os.utime'):
        require_path(os.fsdecode(args[0]), owner)
    elif event == 'os.rename':
        require_path(os.fsdecode(args[0]), owner)
        require_path(os.fsdecode(args[1]), owner)


def argument(args, name):
    prefix = name + '='
    for idx in range(len(args) - 1, -1, -1):
        if args[idx] == name:
            if idx + 1 >= len(args):
                raise ValueError('Missing worker argument')
            return args[idx + 1]
        if args[idx].startswith(prefix):
            return args[idx][len(prefix):]
    raise ValueError('Missing worker argument')


def has_argument(args, name):
    prefix = name + '='
    return any(item == name or item.startswith(prefix) for item in args)


def database_log_directory(config, paths, source):
    if 'results_dir' in paths:
        return paths['results_dir'] or source.parent/'results'
    if 'generated_results_dir' in paths:
        return paths['generated_results_dir'] or source.parent/'results'
    return config.get('settings', 'results_dir', fallback=None) or source.parent/'results'


def validate_runtime(desk, config, paths, args, owner, source):
    target = Path(argument(args, '--tsv'))
    require_path(target, owner)
    for suffix in ('.log', '.base_translation_done', '.enrichment_done', '.the_cut_done', '.updates'):
        require_path(target.with_suffix(suffix), owner)
    require_path(str(target)+'.worker.lock', owner)
    results = desk.resolve_results_dir(paths, config)
    require_path(results, owner)
    zid = argument(args, '--zid') if has_argument(args, '--zid') else target.name[:14]
    require_path(results/(zid+'.log'), owner)
    validate_database(desk, config, paths, owner, source)


def validate_database(desk, config, paths, owner, source):
    if desk.storage_backend_setting(config, paths) != 'sqlite' and not desk.has_projection_database(config, paths):
        return
    database = desk.SqliteStorageAdapter(config=config, resolved_paths=paths, read_only=True).db.db_path
    require_path(database, owner)
    require_path(Path(database_log_directory(config, paths, source))/'db.log', owner)


def record(owner, source, phase, **fields):
    row = dict(phase=phase, pid=os.getpid(), source=str(source), **fields)
    with (owner/'audit-workers.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row)+'\n')


def no_configuration(*args, **kwargs):
    raise AssertionError('Audit stdin worker cannot load ambient configuration')


def prepare(owner, source, args):
    sys.dont_write_bytecode = True
    sys.addaudithook(lambda event, values: file_effects(owner, event, values))
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(source.parent))
    from kardenwort_progressive_context import decode_runtime
    raw = sys.stdin.buffer.read()
    config, paths = decode_runtime(raw)
    import kardenwort_desk as desk
    if Path(desk.__file__).resolve() != source:
        raise ValueError('Unexpected worker runtime source')
    validate_runtime(desk, config, paths, args, owner, source)
    record(owner, source, 'admitted', decoder=str(Path(sys.modules[decode_runtime.__module__].__file__).resolve()),
           source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    return desk, raw


def dispatch(desk, raw, owner, source, ports, args):
    from scripts import run_dict_transport_audit as audit
    from kardenwort_delivery import controller_producer
    audit.OUT = owner
    audit.PROTECTED_PORTS.update(int(port) for port in ports.split(','))
    blocked, children = [], []
    sys.argv = [str(source), *args]
    sys.stdin = io.TextIOWrapper(io.BytesIO(raw), encoding='utf-8')
    sys.stdout = sys.stderr
    with audit.pytest.MonkeyPatch.context() as patches:
        audit.install_socket_guard(patches)
        audit.install_url_guard(patches)
        audit.install_subprocess_guard(patches, blocked, children)
        patches.setattr(desk, 'load_config', no_configuration)
        patches.setattr(audit.kardenwort_controller, 'load_config', no_configuration)
        patches.setattr(audit.kardenwort_db.KardenwortDB, 'get_connection', audit.guarded_connection)
        try:
            with controller_producer():
                desk.main()
        finally:
            audit.finish_workers(children)


def main(argv):
    owner, source = Path(argv[0]).resolve(), Path(argv[1]).resolve()
    try:
        desk, raw = prepare(owner, source, argv[3:])
    except Exception as exc:
        record(owner, source, 'rejected', error=f'{type(exc).__name__}: {exc}')
        print(f'Audit rejected worker runtime before dispatch: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2
    try:
        dispatch(desk, raw, owner, source, argv[2], argv[3:])
    except SystemExit as error:
        record(owner, source, 'finished', exit_code=error.code)
        return error.code
    except Exception as exc:
        record(owner, source, 'failed', error=f'{type(exc).__name__}: {exc}')
        raise
    record(owner, source, 'finished', exit_code=0)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
