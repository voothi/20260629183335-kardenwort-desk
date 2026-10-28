"""Canonical desktop component fixture for tests rehomed from Dict.

Article editing/Undo/Re-word expectations were superseded by restoration. This
fixture tests their desktop store/DOM contracts through the real shell/runtime,
with explicit row descriptors and mapped editable cells. Whole-window desktop
rendering and real article persistence have their own integration coverage.
"""
import html
import re

import kardenwort_desk as desk


def render_desktop_components(text, language, target_lang, config, resolved_paths, zid, goldendict,
                              comments, headers, data_rows, sentence_translation, session_zid=None,
                              api_token='', server_enabled=False, fingerprint=''):
    options, rows, translation = goldendict, data_rows, sentence_translation
    roles = desk.get_role_fields(desk.load_anki_mapping(resolved_paths['anki_mapping_file']), headers)
    context = dict(text=text, headers=headers, data_rows=rows, language=language,
        column_tokens=['inflected', 'lemma', 'translation', 'ipa', 'morphology', 'pos', 'gender'],
        role_fields=roles, server_enabled=True,
        occurrence_targets=desk.lookup_occurrence_targets(headers, rows, roles))
    section = desk.render_section('lemmas', context)
    # A checkbox is a projection probe for the real desktop selection store.
    section = re.sub(r'(<input\b[^>]*data-row-id="(\d+)"[^>]*) disabled',
                     lambda m: m[1] + f' onchange="kwToggleTag(this, {m[2]})"', section)
    for token, role, default in [('inflected', 'inflected', 'WordSourceInflectedForm'),
                                ('lemma', 'lemma', 'WordSource'),
                                ('translation', 'word_translation', 'WordDestination')]:
        field = html.escape(roles.get(role, default), quote=True)
        section = section.replace(f'<td class="col-{token}"', f'<td class="editable col-{token}" data-col="{field}"')
    ui = dict(options, grammar=desk.get_grammar_payload(), has_highlight_col=True)
    ui.pop('server_api_key', None)
    ui['selected_col_name'] = roles.get('selected', 'DeskSelected')
    session = dict(zid=zid, session_zid=session_zid or zid, language=language, target_lang=target_lang,
        source_text=text, sentence_translation=translation, headers=headers, data_rows=rows,
        server_enabled=server_enabled, api_token=api_token,
        server_url=f"http://{options.get('server_host', '127.0.0.1')}:{options.get('server_port', 18335)}",
        fingerprint=fingerprint)
    payload = dict(session_data=session, ui_config=ui, sentence_cards=[], delivery_mode='single',
        zid=zid, session_zid=session_zid or zid, language=language, target_language=target_lang)
    return desk.render_declarative_web_shell(payload, theme=options.get('theme', 'compact'),
        source_html=html.escape(text), sentence_html=html.escape(translation),
        table_header_html=re.search(r'<thead>(.*?)</thead>', section, re.S)[1],
        table_rows_html=re.search(r'<tbody>(.*?)</tbody>', section, re.S)[1])
