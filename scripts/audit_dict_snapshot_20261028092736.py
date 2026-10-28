"""Compare snapshot/current Dict artifacts with owned fixtures and no network."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results' / '20261028092736-dict-snapshot-audit'
sys.path.insert(0, str(ROOT))


def protected_snapshot():
    files = [ROOT / 'config.ini', ROOT / 'anki-mapping.ini', *(ROOT / 'data').glob('*.db*')]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files if p.is_file()}


METRICS = """() => {
    const metric = element => {
        if (!element) return null;
        const box = element.getBoundingClientRect(), css = getComputedStyle(element);
        return {width:box.width,height:box.height,font:css.fontFamily,
            fontSize:css.fontSize,padding:css.padding,overflow:css.overflow};
    };
    return {table:metric(document.querySelector('#lemma-table')),
        columns:Array.from(document.querySelectorAll('#lemma-table th')).map(e=>({text:e.textContent,...metric(e)})),
        toolbar:metric(document.querySelector('#kw-action-toolbar')),
        buttons:Array.from(document.querySelectorAll('button')).filter(e=>e.getBoundingClientRect().height>0)
            .map(e=>({id:e.id,text:e.textContent,disabled:e.disabled})),
        rows:document.querySelectorAll('#lemma-table tbody tr').length,
        text:document.querySelector('.kw-lookup-container').textContent.trim()};
}"""


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    before = protected_snapshot()
    import kardenwort_desk as current
    from kardenwort_db import KardenwortDB
    from playwright.sync_api import sync_playwright

    original_connection = KardenwortDB.get_connection

    def owned_connection(instance, *args, **kwargs):
        if not Path(instance.db_path).resolve().is_relative_to(OUT):
            raise AssertionError('Snapshot audit attempted workspace database access')
        return original_connection(instance, *args, **kwargs)

    KardenwortDB.get_connection = owned_connection
    source = subprocess.check_output(['git', 'show', '56f8696:kardenwort_desk.py'], cwd=ROOT)
    baseline = types.ModuleType('dict_snapshot_reference')
    baseline.__file__ = str(ROOT / 'kardenwort_desk.py')
    sys.modules[baseline.__name__] = baseline
    exec(compile(source.decode('utf-8-sig'), baseline.__file__, 'exec'), baseline.__dict__)
    cfg, paths, gd, _ = current.load_config(ROOT / 'config.ini')
    cfg.set('storage', 'backend', 'tsv')
    cfg.set('storage', 'sqlite_db_path', str(OUT / 'fixture.db'))
    cfg.set('settings', 'enable_performance_tracing', 'false')
    paths.update(storage_backend='tsv', sqlite_db_path=OUT / 'fixture.db',
                 results_dir=OUT, generated_results_dir=OUT)
    headers = ['Quotation', 'WordSourceInflectedForm', 'WordSource', 'WordDestination',
               'SentenceSourceIndex', 'TokenOrder', 'DeskSelected', 'WordSourceIPA',
               'WordSourceMorphologyAI', 'WordSourcePOS', 'WordSourceGender']
    rows = [['Haus', 'Haus', 'Haus', 'house', '1', '0', '0', 'haʊs', '', 'NOUN', 'n'],
            ['Baum', 'Baum', 'Baum', 'tree', '2', '0', '1', 'baʊm', '', 'NOUN', 'm']]
    evidence = {'baseline':'56f8696', 'target':subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(), 'cases':[], 'host_style_probes':[]}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for theme in ('compact', 'dark', 'light'):
            for enabled in (False, True):
                for columns in (['inflected', 'lemma', 'translation'],
                                ['translation', 'lemma', 'ipa', 'pos', 'gender']):
                    fixture = dict(gd, theme=theme, sections=['translation', 'lemmas'],
                                   lemma_columns=columns, heading_translation='', heading_lemmas='',
                                   server_enabled=enabled, server_api_key='', server_host='127.0.0.1',
                                   server_port=59998, disable_css=False)
                    for label, module in (('snapshot', baseline), ('current', current)):
                        name = f'{label}-{theme}-{int(enabled)}-{len(columns)}'
                        rendered = module.render_lookup_html('Das Haus. Der Baum.', 'de', 'en',
                            cfg, paths, '20261028092736', copy.deepcopy(fixture), [], list(headers),
                            copy.deepcopy(rows), 'The house. The tree.', session_zid='20261028092736',
                            api_token='audit-disposable-token' if enabled else '', server_enabled=enabled)
                        (OUT / (name + '.html')).write_text(rendered, encoding='utf-8')
                        for js_enabled in (False, True):
                            context = browser.new_context(java_script_enabled=js_enabled,
                                                          viewport={'width':1280,'height':900})
                            context.route('**/*', lambda route: route.abort())
                            page = context.new_page()
                            errors = []
                            page.on('pageerror', lambda error: errors.append(str(error)))
                            page.set_content(rendered)
                            metrics = page.evaluate(METRICS)
                            evidence['cases'].append(dict(version=label, theme=theme,
                                server_enabled=enabled, columns=columns, javascript=js_enabled,
                                html_bytes=len(rendered.encode('utf-8')), metrics=metrics, errors=errors))
                            if theme == 'compact' and len(columns) == 3 and not js_enabled:
                                page.screenshot(path=str(OUT / (name + '.png')), full_page=True)
                                host = '<html><head><style>body{font:17px Georgia;background:rgb(238, 240, 242)}</style></head><body><div id="host-label">Host dictionary heading</div>ARTICLE</body></html>'
                                probe = """() => {
                                    const s=getComputedStyle(document.getElementById('host-label'));
                                    return {font:s.fontFamily,fontSize:s.fontSize,
                                        background:getComputedStyle(document.body).backgroundColor};
                                }"""
                                page.set_content(host.replace('ARTICLE', ''))
                                host_before = page.evaluate(probe)
                                page.set_content(host.replace('ARTICLE', rendered))
                                host_after = page.evaluate(probe)
                                evidence['host_style_probes'].append(dict(version=label,
                                    server_enabled=enabled, before=host_before, after=host_after,
                                    unchanged=host_before == host_after))
                            context.close()
        browser.close()
    after = protected_snapshot()
    evidence.update(protected_before=before, protected_after=after, protected_unchanged=before == after)
    (OUT / 'comparison.json').write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding='utf-8')
    assert before == after
    print(json.dumps({'cases':len(evidence['cases']), 'host_style_probes':evidence['host_style_probes'], 'protected_unchanged':before == after,
                      'output':str(OUT)}))


if __name__ == '__main__':
    main()
