"""Compare the restored article with the pinned snapshot using owned fixtures."""
import configparser
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results' / '20261028220325-dict-restoration-phase3' / 'matrix'
sys.path.insert(0, str(ROOT))
os.environ.pop('SSLKEYLOGFILE', None)

import kardenwort_desk as current
from playwright.sync_api import sync_playwright


def setting_pairs(row, positions):
    return {(i, row[i], j, row[j]) for i, j in positions}


def matrix():
    axes = dict(theme=['compact', 'dark', 'light', 'white'], width=[620, 1280],
                enabled=[False, True], javascript=[False, True], css=[False, True],
                columns=['reduced', 'reordered', 'full'], sections=['normal', 'reordered'])
    candidates = list(itertools.product(*axes.values()))
    positions = list(itertools.combinations(range(len(axes)), 2))
    pairs = lambda row: setting_pairs(row, positions)
    required = set().union(*(pairs(row) for row in candidates))
    uncovered, selected = set(required), []
    while uncovered:
        row = max(candidates, key=lambda item: len(pairs(item) & uncovered))
        selected.append(row)
        uncovered -= pairs(row)
    for theme in axes['theme']:
        anchor = (theme, 1280, True, True, True, 'reduced', 'normal')
        if anchor not in selected:
            selected.append(anchor)
    assert set().union(*(pairs(row) for row in selected)) == required
    return axes, len(required), [dict(zip(axes, row)) for row in selected]


def protected():
    paths = [ROOT / 'config.ini', ROOT / 'anki-mapping.ini', *(ROOT / 'data').glob('*.db*')]
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths if path.is_file()}


HOST = '''<!doctype html><html><head><style>
body{font:17px Georgia;background:rgb(238,240,242);margin:8px}
</style></head><body><div id="host-label">Host dictionary heading</div>ARTICLE</body></html>'''
PROBE = '''() => {
    const el=document.getElementById('host-label'),s=getComputedStyle(el),b=el.getBoundingClientRect();
    return {font:s.fontFamily,size:s.fontSize,color:s.color,background:getComputedStyle(document.body).backgroundColor,
        x:b.x,y:b.y,width:b.width,height:b.height};
}'''
METRICS = '''() => {
    const metric=el=>{const s=getComputedStyle(el),b=el.getBoundingClientRect();
        return {width:b.width,height:b.height,font:s.fontFamily,size:s.fontSize,padding:s.padding};};
    return {columns:Array.from(document.querySelectorAll('th'),metric),
        column_classes:Array.from(document.querySelectorAll('th'),el=>el.className),
        cells:Array.from(document.querySelectorAll('tbody tr:first-child td'),metric),
        empty_morphology:Array.from(document.querySelectorAll('td.col-morphology'),el=>el.textContent.trim()),
        rows:document.querySelectorAll('tbody tr').length,
        headings:Array.from(document.querySelectorAll('h3'),el=>el.textContent),
        buttons:Array.from(document.querySelectorAll('button'),el=>el.textContent),
        controls:document.querySelectorAll('button,input,select,textarea').length,
        enabled_controls:document.querySelectorAll('button:enabled,input:enabled,select:enabled,textarea:enabled').length,
        scripts:document.scripts.length, styles:document.querySelectorAll('style').length,
        desktop:document.querySelectorAll('#kw-action-toolbar,#kw-lang-modal,#kardenwort-bootstrap').length,
        source:document.querySelector('#source-container')?.textContent,
        translation:document.querySelector('#translation-container')?.textContent};
}'''


COLUMNS = dict(reduced=['inflected', 'lemma', 'translation'],
              reordered=['translation', 'lemma', 'ipa', 'pos', 'gender'],
              full=['inflected', 'lemma', 'translation', 'ipa', 'morphology', 'pos', 'gender'])


def article_options(case):
    return dict(theme=case['theme'], server_enabled=case['enabled'], server_host='127.0.0.1',
        server_port=59998, server_api_key='', disable_css=not case['css'],
        sections=['source', 'translation', 'lemmas'] if case['sections']=='normal' else ['lemmas', 'source'],
        lemma_columns=COLUMNS[case['columns']], heading_source='Source',
        heading_translation='Translation', heading_lemmas='Vocabulary')


def assert_configured_content(metrics, case):
    assert metrics['column_classes'] == (['kw-tag-header'] if case['enabled'] else []) + [
        'col-' + token for token in COLUMNS[case['columns']]]
    assert metrics['headings'] == (['Source', 'Translation', 'Vocabulary']
        if case['sections']=='normal' else ['Vocabulary', 'Source'])
    assert metrics['source'] == 'Das Haus. Der Baum.'
    assert metrics['translation'] == ('The house. The tree.' if case['sections']=='normal' else None)
    if case['columns']=='full':
        assert metrics['empty_morphology'] == ['', '']


def assert_current(observation, case, html):
    metrics = observation['metrics']
    assert not observation['errors'] and not observation['requests'], observation
    assert observation['host_unchanged'], case
    assert metrics['rows'] == 2 and metrics['desktop'] == 0
    assert metrics['buttons'] == (['Export to Anki'] if case['enabled'] else [])
    assert len(metrics['columns']) == len(COLUMNS[case['columns']]) + int(case['enabled'])
    assert_configured_content(metrics, case)
    if not case['enabled']:
        assert metrics['controls'] == metrics['scripts'] == 0
        assert 'matrix-disposable-token' not in html
    if not case['css']:
        assert metrics['styles'] == 1  # Only the deliberately supplied host stylesheet.
    if not case['javascript']:
        assert metrics['enabled_controls'] == 0


def representative(case):
    return case['width']==1280 and case['css'] and case['enabled'] and case['javascript'] and case['columns']=='reduced'


def observe(browser, module, label, index, case, config, headers, rows):
    html = module.render_lookup_html('Das Haus. Der Baum.', 'de', 'en', config,
        {'results_dir': OUT, 'storage_backend':'tsv', 'sqlite_db_path':OUT/'unused.db'},
        '20261028220325', article_options(case), [], headers, [list(row) for row in rows], 'The house. The tree.',
        session_zid='20261028220325', api_token='matrix-disposable-token' if case['enabled'] else '',
        server_enabled=case['enabled'])
    context = browser.new_context(java_script_enabled=case['javascript'],
        viewport={'width':case['width'], 'height':900})
    requests, errors = [], []
    context.route('**/*', lambda route: (requests.append(route.request.url), route.abort()))
    page = context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.set_content(HOST.replace('ARTICLE', ''))
    host_before = page.evaluate(PROBE)
    page.set_content(HOST.replace('ARTICLE', html))
    observation = dict(metrics=page.evaluate(METRICS), host_unchanged=page.evaluate(PROBE)==host_before,
        html_bytes=len(html.encode('utf-8')), requests=requests, errors=errors)
    if representative(case):
        page.screenshot(path=str(OUT / f'{index}-{label}-{case["theme"]}.png'), full_page=True)
    if label == 'current':
        assert_current(observation, case, html)
    context.close()
    return observation


def compare_compact(case, observed):
    if case['theme']!='compact' or not case['css']:
        return
    for actual, reference in zip(observed['current']['metrics']['columns'], observed['snapshot']['metrics']['columns']):
        assert abs(actual['width']-reference['width']) <= 1, (case, actual, reference)
    assert observed['current']['metrics']['cells'] == observed['snapshot']['metrics']['cells']


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    before = protected()
    source = subprocess.check_output(['git', 'show', '56f8696:kardenwort_desk.py'], cwd=ROOT)
    baseline = types.ModuleType('restored_dict_matrix_reference')
    baseline.__file__ = str(ROOT / 'kardenwort_desk.py')
    sys.modules[baseline.__name__] = baseline
    exec(compile(source.decode('utf-8-sig'), baseline.__file__, 'exec'), baseline.__dict__)
    config = configparser.ConfigParser()
    config.read_string('[settings]\nenable_performance_tracing=false\n[storage]\nbackend=tsv\n')
    headers = ['Quotation', 'WordSourceInflectedForm', 'WordSource', 'WordDestination',
               'SentenceSourceIndex', 'TokenOrder', 'DeskSelected', 'WordSourceIPA',
               'WordSourceMorphologyAI', 'WordSourcePOS', 'WordSourceGender']
    rows = [['Haus', 'Haus', 'Haus', 'house', '1', '7', '0', 'haʊs', '', 'NOUN', 'n'],
            ['Baum', 'Baum', 'Baum', 'tree', '2', '0', '1', 'baʊm', '', 'NOUN', 'm']]
    axes, pairs, cases = matrix()
    evidence = dict(baseline='56f8696', axes=axes, pair_count=pairs, configurations=len(cases), cases=[])
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for index, case in enumerate(cases):
            observed = {}
            for label, module in [('snapshot', baseline), ('current', current)]:
                observed[label] = observe(browser, module, label, index, case, config, headers, rows)
            compare_compact(case, observed)
            evidence['cases'].append(dict(configuration=case, observations=observed))
        browser.close()
    after = protected()
    assert before == after
    evidence.update(protected_before=before, protected_after=after, protected_unchanged=True)
    (OUT / 'comparison.json').write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(dict(configurations=len(cases), renders=2*len(cases), pairs=pairs, protected_unchanged=True)))


if __name__ == '__main__':
    main()
