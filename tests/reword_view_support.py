"""Stored occurrence facts and real rendering checks shared by legacy regressions."""
import html
import re

import kardenwort_desk as desk


def article_occurrence(text):
    first = next(token for token in desk.tok.build_word_list_internal(text, keep_spaces=True)
                 if token.get('lower_clean') == 'der')
    return {'occurrence_data': {str(first['visual_idx']): {
        'trans': 'тот', 'pos': 'art.',
        'morph': 'der (определенный артикль мужского рода)',
    }}}


def assert_readonly_aggregate(adapter, zid, config, paths, text):
    before = adapter.db.get_session_bundle(zid)
    rendered = desk.run_render_flow(text=text, language='de', zid=zid, text_mode='single',
        config=config, resolved_paths=paths, tsv_path=str(desk.resolve_results_dir(paths, config) / (zid + '.de.tsv')),
        spawn_children=False, return_children=False)
    translations = [html.unescape(re.sub('<[^>]+>', '', cell)).strip()
                    for cell in re.findall(r'<td\b[^>]*data-col="WordDestination"[^>]*>(.*?)</td>', rendered, re.S)]
    assert 'тот, который' in translations
    assert adapter.db.get_session_bundle(zid) == before
