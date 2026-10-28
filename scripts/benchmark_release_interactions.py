"""Measure real frontend and authenticated storage boundaries in an owned checkout."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summary(samples):
    ordered = sorted(samples)
    return dict(samples_ms=samples, p50_ms=ordered[len(ordered) // 2],
                p95_ms=ordered[math.ceil(len(ordered) * .95) - 1])


def measure(operation, samples):
    values = []
    for _ in range(samples):
        start = time.perf_counter()
        operation()
        values.append((time.perf_counter() - start) * 1000)
    return summary(values)


def words(count):
    return ['wort' + ''.join(chr(97 + (i // 26**j) % 26) for j in range(4)) for i in range(count)]


HEADERS = ['Quotation', 'WordSource', 'WordDestination', 'SentenceSourceIndex',
           'SentenceSource', 'DeskSelected', 'TokenOrder']
ZID = '20261026055331'


def payload(count):
    lemmas = words(count)
    rows = [[lemma, lemma, 'translation', str(i + 1), lemma + '.', '0', str(i)] for i, lemma in enumerate(lemmas)]
    return lemmas, rows


def render_fixture(desk, count, mode):
    lemmas, rows = payload(count)
    config, paths, gd, _ = desk.load_config()
    config.set('server', 'enabled', 'true')
    config.set('rendering', 'filter_selected_only', 'false')
    config.set('rendering', 'auto_save_on_edit', 'false')
    gd.update(server_enabled=True, filter_selected_only=False, auto_save_on_edit=False,
              lemma_columns=['lemma', 'translation'], sections=['source', 'translation', 'lemmas'])
    card_words = [dict(row_id=i, token_order=i, sentence_idx=i+1, word=lemma, inflected=lemma,
                       lemma=lemma, sentence_text=lemma+'.', translation='translation', selected='0')
                  for i, lemma in enumerate(lemmas)]
    bootstrap = dict(delivery_mode='container', sentence_cards=[dict(seq_num=1, sentence_idx=0,
        zid=ZID, words=card_words, translated_text='A complete translation.', selected_ids=[])],
        ui_config=dict(filter_selected_only=False, highlight_same_lemma=True, auto_save_on_edit=False),
        session_data=dict(session_zid=ZID, zid=ZID, language='de', target_lang='en', server_enabled=True,
                          source_text=' '.join(lemmas), sentence_translation='A complete translation.',
                          headers=HEADERS, data_rows=rows))
    if mode == 'desktop':
        bootstrap.update(delivery_mode='single', sentence_cards=[])
    return desk.render_declarative_web_shell(bootstrap, theme='dark')


TRANSPORT = '''<script>
window.EventSource=undefined;
window.__requests=[];
window.fetch=function(url,options){
  __requests.push(String(url));
  return Promise.resolve({ok:true,status:200,json:function(){return Promise.resolve({ok:true,stage:'finished',
    shared_selection:Object.assign({},window.AppState.selectedLemmas)});}});
};
</script>'''


def browser_case(browser, desk, count, mode, samples, profile_path=None):
    start = time.perf_counter()
    html = render_fixture(desk, count, mode)
    render_ms = (time.perf_counter() - start) * 1000
    page = browser.new_page(viewport=dict(width=1280, height=900))
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    if profile_path:
        profiler = page.context.new_cdp_session(page)
        profiler.send('Profiler.enable')
        profiler.send('Profiler.start')
    start = time.perf_counter()
    page.set_content(html.replace('<head>', '<head>' + TRANSPORT), timeout=120000)
    try:
        page.wait_for_function('typeof applySharedSelection === "function"', timeout=5000)
    except Exception:
        print('Initialization errors:', errors, flush=True)
        raise
    page.wait_for_function('n => document.querySelectorAll("#lemma-table tbody tr[data-row-id]").length === n', arg=count)
    initialize_ms = (time.perf_counter() - start) * 1000
    if profile_path:
        profile_path.write_text(json.dumps(profiler.send('Profiler.stop')['profile']))
        profiler.detach()
    operations = page.evaluate('''async ({count,samples}) => {
      const measure = fn => {const start=performance.now();fn();document.body.getBoundingClientRect();return performance.now()-start;};
      const keys=Array.from(document.querySelectorAll('#lemma-table tbody tr[data-row-id]')).map(r=>r.querySelector('[data-col="WordSource"]').textContent.trim().toLowerCase());
      const membership=selected=>Object.fromEntries(keys.map(k=>[k,selected]));
      const result={membership:[],idle:[],filter:[],selection:[],translation:[],bulk:[],pending_membership:[]};
      const text='A complete translated sentence. '.repeat(Math.max(20,Math.floor(count/4)));
      for(let i=0;i<samples;i++){
        result.membership.push(measure(()=>applySharedSelection(membership(i%2===0))));
        if(isDirty()) throw new Error('Membership projection echoed local intent');
        result.idle.push(measure(()=>applySharedSelection(membership(i%2===0))));
        result.filter.push(measure(()=>{setFilterSelectedOnly(true);setFilterSelectedOnly(false);}));
        result.selection.push(measure(()=>{toggleRowSelection(0,i%2!==0);toggleRowSelection(0,i%2===0);}));
        result.translation.push(measure(()=>receiveUpdate({stage:'translated_text',translatedText:text+(i%2?'Next.':'End.')})));
        result.bulk.push(measure(()=>{selectAllRows();clearAllSelections();}));
        await flushPendingSaves();
        selectAllRows();
        result.pending_membership.push(measure(()=>applySharedSelection(membership(false))));
        if(Object.keys(AppState.selectedLemmas).length!==count || !isDirty())
          throw new Error('Authoritative read discarded pending selection');
        await flushPendingSaves();
      }
      const element=document.getElementById('translation-container');
      return {times:result,rows:keys.length,scrollbar:element.scrollHeight>element.clientHeight,
              dirty:isDirty(),selection:AppState.selectedLemmas,requests:__requests};
    }''', dict(count=count, samples=samples))
    assert not errors, errors
    assert not operations['scrollbar'] and not operations['dirty'], operations
    timings = {key: summary(value) for key, value in operations.pop('times').items()}
    page.close()
    return dict(rows=count, mode=mode, python_render_ms=render_ms, page_initialize_ms=initialize_ms,
                operations=timings, invariants=dict(rows=operations['rows'], no_inner_scroll=True,
                clean_after_save=True, no_page_errors=True))


def storage_cases(desk, directory, sizes, samples):
    from http.server import ThreadingHTTPServer
    from tests.test_controller_integration import running_controller
    from tests.test_release_boundary_contracts import post, data, use_backend
    directory.mkdir()
    # Match the production entry point's listen backlog; the fixture bypasses main().
    ThreadingHTTPServer.request_queue_size = 64
    fixture = running_controller.__wrapped__(directory)
    url, server, _ = next(fixture)
    cases = []
    try:
        for backend in ('sqlite', 'tsv'):
            adapter = use_backend(server, backend)
            for count in sizes:
                _, rows = payload(count)
                adapter.save_session(session_zid=ZID, slug='performance', source_language='de', target_language='en',
                    source_raw_text='Words.', text_mode='single', headers=HEADERS, data_rows=rows,
                    working_tsv_path=Path(server.resolved_paths['results_dir']) / (ZID+'-performance.de.tsv'))
                def read(client=None):
                    start = time.perf_counter()
                    body = dict(session_zid=ZID, accept_updates=False)
                    if client: body['client_id'] = client
                    status, response = post(url, '/internal/selection', body)
                    assert status == 200, response
                    assert len(data(response)['shared_selection']) == count
                    return (time.perf_counter() - start) * 1000
                cold = measure(read, 1)
                warm = measure(read, samples)
                concurrent = {}
                for clients in (2, 8):
                    requests = []
                    with ThreadPoolExecutor(max_workers=clients) as pool:
                        batch = measure(lambda: requests.extend(pool.map(read,
                            ['performance-'+str(i) for i in range(clients)])), samples)
                    concurrent[clients] = dict(batch, requests=summary(requests))
                cases.append(dict(backend=backend, rows=count, cold=cold, warm=warm,
                                  two_clients=concurrent[2], eight_clients=concurrent[8]))
        return cases
    finally:
        try:
            next(fixture)
        except StopIteration:
            pass


def worker(args):
    import kardenwort_desk as desk
    from playwright.sync_api import sync_playwright
    output = Path(args.output)
    result = dict(baseline=args.baseline, samples=args.samples, sizes=args.sizes, browser=[], storage=[])
    assets = [ROOT/'web/js/app.js', ROOT/'web/js/services/sse.js']
    current = {path: path.read_bytes() for path in assets}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for version in ('baseline', 'current'):
                for path in assets:
                    path.write_bytes((ROOT/'baseline'/path.relative_to(ROOT)).read_bytes() if version=='baseline' else current[path])
                for size in args.sizes:
                    for mode in ('desktop', 'container'):
                        profile = output/f'{version}-{size}-{mode}-init.cpuprofile' if args.profile_init else None
                        case = browser_case(browser, desk, size, mode, args.samples, profile)
                        case['version'] = version
                        result['browser'].append(case)
                        (output/'measurements.json').write_text(json.dumps(result, indent=2))
                        print(version, size, mode, json.dumps({k:round(v['p95_ms'],1) for k,v in case['operations'].items()}), flush=True)
            browser.close()
        result['storage'] = storage_cases(desk, ROOT/'storage-performance', args.sizes, args.samples)
        (output/'measurements.json').write_text(json.dumps(result, indent=2))
    finally:
        for path, content in current.items(): path.write_bytes(content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', default='4a7de8c')
    parser.add_argument('--sizes', type=int, nargs='+', default=[100,1000,5000])
    parser.add_argument('--samples', type=int, default=7)
    parser.add_argument('--output', default='results/20261026055331-performance')
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--profile-init', action='store_true')
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    from scripts.run_release_boundary_contracts import temporary_checkout, copy_checkout, guard_external_databases
    output = (ROOT/args.output).resolve()
    assert output.parent == ROOT/'results'
    output.mkdir(exist_ok=True)
    protected_before = digest(ROOT/'data/kardenwort.db')
    with temporary_checkout(ROOT/'scratch') as checkout:
        copy_checkout(checkout)
        guard_external_databases(checkout)
        for asset in ('web/js/app.js','web/js/services/sse.js'):
            target = checkout/'baseline'/asset
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.run(['git','show',args.baseline+':'+asset],cwd=ROOT,capture_output=True,check=True).stdout)
        env = dict(os.environ, SSLKEYLOGFILE='', PYTHONPATH=str(checkout))
        command = [sys.executable, str(checkout/'scripts'/Path(__file__).name), '--worker', '--baseline', args.baseline,
                   '--samples', str(args.samples), '--sizes', *map(str,args.sizes), '--output', str(output)]
        if args.profile_init:
            command.append('--profile-init')
        with (output/'execution.log').open('w') as log:
            run = subprocess.run(command, cwd=checkout, env=env, stdout=log, stderr=subprocess.STDOUT)
        guard = checkout/'database-guard.jsonl'
        tooling_guard = checkout/'tooling-guard.jsonl'
        metadata = dict(command=command, exit_code=run.returncode, checkout=str(checkout),
            db_before=protected_before, db_after=digest(ROOT/'data/kardenwort.db'),
            external_database_attempts=guard.read_text() if guard.exists() else '',
            compiler_tooling_attempts=tooling_guard.read_text() if tooling_guard.exists() else '',
            head=subprocess.run(['git','rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip(),
            source_sha256={name:digest(checkout/name) for name in
                           ('web/js/app.js','web/js/services/sse.js','scripts/benchmark_release_interactions.py')})
    metadata['checkout_removed'] = not checkout.exists()
    (output/'execution.json').write_text(json.dumps(metadata,indent=2))
    print((output/'execution.log').read_text())
    assert metadata['db_before']==metadata['db_after'] and not metadata['external_database_attempts']
    assert not metadata['compiler_tooling_attempts']
    assert metadata['checkout_removed']
    raise SystemExit(run.returncode)


if __name__ == '__main__':
    main()
