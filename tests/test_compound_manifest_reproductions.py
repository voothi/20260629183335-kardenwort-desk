"""Phase 1 backend reproduction tests for compound decomposition, manifest parity, and shell hardening."""
from html.parser import HTMLParser
import re

import pytest

import kardenwort_desk as desk
import kardenwort_theme as theme_mod
import text_tokenizer as tok
from tests.render_test_support import seed_render_rows
from tests.sqlite_document_support import island


class _SpanCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.spans = []

    def handle_starttag(self, tag, attrs):
        if tag == "span":
            self.spans.append(dict(attrs))


def _source_word_spans(html_text: str):
    match = re.search(r'<div[^>]*id="source-container"[^>]*>(.*?)</div>', html_text, flags=re.DOTALL)
    assert match is not None, "Missing #source-container in rendered HTML"
    parser = _SpanCollector()
    parser.feed(match.group(1))
    return [
        attrs
        for attrs in parser.spans
        if "word" in (attrs.get("class") or "").split()
    ]


def _render_sqlite_session(tmp_path, text, language, headers, rows, *, zid, text_mode="single", extra_config=None):
    config, paths, _, _ = desk.load_config()
    config.read_dict({
        "triggers": {
            "run_text_translation": "manual",
            "run_lemma_base_translation": "manual",
            "run_lemma_enrichment": "manual",
        },
        "rendering": {"display_mode": "monolithic"},
        "pipeline": {"progressive_text_translation": "false"},
    })
    if extra_config:
        config.read_dict(extra_config)
    seed_render_rows(config, paths, zid, text, language, headers, rows, text_mode=text_mode)
    return desk.run_render_flow(
        text=text,
        language=language,
        zid=zid,
        text_mode=text_mode,
        config=config,
        resolved_paths=paths,
        theme="dark",
        tsv_path=zid,
        spawn_children=False,
        seq_num=1,
        wordfill_cfg={"enabled": False},
    )


@pytest.mark.parametrize(
    "identifier, expected_words",
    [
        (
            "test_tray_startup.py::test_startup_slice_has_bounded_branch_complexity",
            ["test", "tray", "startup", "py", "test", "startup", "slice", "has", "bounded", "branch", "complexity"],
        ),
        ("foo__bar", ["foo", "bar"]),
        ("a//b", ["a", "b"]),
        ("a--b", ["a", "b"]),
        ("DeskStartup.ahk", ["Desk", "Startup", "ahk"]),
        ("foo_BarBaz", ["foo", "Bar", "Baz"]),
    ],
)
def test_tag_server_compound_tokens_multi_char_delimiters_and_mixed_camelcase(identifier, expected_words):
    tokens = tok.build_word_list_internal(identifier, "en")
    desk.tag_server_compound_tokens(tokens)
    word_tokens = [t for t in tokens if t.get("is_word")]
    assert [t["text"] for t in word_tokens] == expected_words
    assert all(t.get("is_in_compound") is True for t in word_tokens)
    assert {t.get("compound_text") for t in word_tokens} == {identifier}
    compound_ids = {t.get("compound_id") for t in word_tokens}
    assert len(compound_ids) == 1
    assert None not in compound_ids


def _row_id_by_lemma(html_text: str, lemma: str) -> int:
    session = island(html_text, "session-data")
    headers = session["headers"]
    lemma_col = headers.index("WordSource")
    for idx, row in enumerate(session["data_rows"]):
        if row[lemma_col] == lemma:
            return idx
    raise AssertionError(f"Lemma {lemma!r} not found in session-data rows")


def test_irregular_compound_subtoken_attribution_en_and_de(tmp_path):
    en_ident = "test_tray_startup.py::test_startup_slice_has_bounded_branch_complexity"
    headers = [
        "Quotation",
        "WordSource",
        "WordSourceInflectedForm",
        "WordDestination",
        "SentenceSourceIndex",
        "SentenceSource",
        "DeskSelected",
    ]
    en_rows = [
        [en_ident, en_ident, en_ident, "тест-слоя", "1", en_ident, "0"],
        [en_ident, "have", en_ident, "иметь", "1", en_ident, "0"],
        [en_ident, "test", en_ident, "тест", "1", en_ident, "0"],
    ]
    en_html = _render_sqlite_session(tmp_path, en_ident, "en", headers, en_rows, zid="20261127060412")
    en_manifest = island(en_html, "token-map")
    have_row_id = _row_id_by_lemma(en_html, "have")
    comp_row_id = _row_id_by_lemma(en_html, en_ident)
    has_tok = next(t for t in en_manifest if t.get("lower_clean") == "has")
    assert has_tok.get("atomic_row_ids") == [have_row_id]
    assert has_tok.get("compound_row_ids") == [comp_row_id]
    assert has_tok.get("lemma") == "have"
    assert has_tok.get("compound_id") is not None

    de_text = "hat-alles"
    de_rows = [
        [de_text, de_text, de_text, "всё-есть", "1", de_text, "0"],
        [de_text, "haben", de_text, "иметь", "1", de_text, "0"],
    ]
    de_html = _render_sqlite_session(tmp_path, de_text, "de", headers, de_rows, zid="20261127060413")
    de_manifest = island(de_html, "token-map")
    haben_row_id = _row_id_by_lemma(de_html, "haben")
    de_comp_row_id = _row_id_by_lemma(de_html, de_text)
    hat_tok = next(t for t in de_manifest if t.get("lower_clean") == "hat")
    assert hat_tok.get("atomic_row_ids") == [haben_row_id]
    assert hat_tok.get("compound_row_ids") == [de_comp_row_id]
    assert hat_tok.get("lemma") == "haben"


def test_html_and_token_manifest_parity_on_20261127060412_shaped_fixture(tmp_path):
    en_ident = "test_tray_startup.py::test_startup_slice_has_bounded_branch_complexity"
    source_text = f"Run {en_ident} and test_other.py now."
    headers = [
        "Quotation",
        "WordSource",
        "WordSourceInflectedForm",
        "WordDestination",
        "SentenceSourceIndex",
        "SentenceSource",
        "DeskSelected",
    ]
    rows = [
        [en_ident, en_ident, en_ident, "полный-тест", "1", source_text, "0"],
        [en_ident, "have", en_ident, "иметь", "1", source_text, "0"],
        ["Run", "run", "Run", "запустить", "1", source_text, "0"],
    ]
    html_text = _render_sqlite_session(tmp_path, source_text, "en", headers, rows, zid="20261127060414")
    manifest = island(html_text, "token-map")
    comp_row_id = _row_id_by_lemma(html_text, en_ident)
    manifest_by_vidx = {str(t["visual_idx"]): t for t in manifest if t.get("is_word")}
    spans = _source_word_spans(html_text)
    assert len(spans) == len(manifest_by_vidx)

    # All subtokens in the pytest node-id share one non-null compound_id and reference composite row.
    ident_tokens = [
        t
        for t in manifest
        if t.get("is_word") and t.get("compound_id") is not None and comp_row_id in (t.get("compound_row_ids") or [])
    ]
    assert len(ident_tokens) == 11
    assert len({t["compound_id"] for t in ident_tokens}) == 1

    # Strict HTML <-> token-map parity: no span may carry highlight-orange/purple when manifest has no rows.
    for span in spans:
        vidx = span["data-word-idx"]
        tok_entry = manifest_by_vidx[vidx]
        classes = set((span.get("class") or "").split())
        has_manifest_rows = bool(tok_entry.get("row_ids"))
        if has_manifest_rows:
            assert ("highlight-orange" in classes) or ("highlight-purple" in classes), (
                f"Span {vidx} ({tok_entry.get('text')}) missing highlight class despite manifest rows {tok_entry.get('row_ids')}"
            )
            assert "not-connected" not in classes
        else:
            assert "not-connected" in classes, (
                f"Span {vidx} ({tok_entry.get('text')}) has orphan highlight classes {classes} with empty manifest row_ids"
            )
            assert "highlight-orange" not in classes
            assert "highlight-purple" not in classes
        if tok_entry.get("compound_id") is not None:
            assert span.get("data-compound-id") == str(tok_entry["compound_id"])


def test_filtered_empty_mapped_rows_does_not_resurrect_cross_sentence_stem(tmp_path):
    source_text = "Cat's-other hat.\nCat-house sleeps."
    headers = [
        "Quotation",
        "WordSource",
        "WordSourceInflectedForm",
        "WordDestination",
        "SentenceSourceIndex",
        "SentenceSource",
        "DeskSelected",
    ]
    # Only Sentence 2 has a compound row for "Cat-house"; Sentence 1's "Cat's" inside "Cat's-other"
    # must be filtered out by compound matching and must not resurrect Sentence 2's row via stem fallback.
    rows = [
        ["hat", "hat", "hat", "шляпа", "1", "Cat's-other hat.", "0"],
        ["Cat-house", "Cat-house", "Cat-house", "кошкин-дом", "2", "Cat-house sleeps.", "0"],
    ]
    html_text = _render_sqlite_session(
        tmp_path,
        source_text,
        "en",
        headers,
        rows,
        zid="20261127060415",
        text_mode="multi",
    )
    manifest = island(html_text, "token-map")
    sent1_cats = next(
        t for t in manifest
        if t.get("is_word") and t.get("source_sentence_idx") == 1 and t.get("text") == "Cat's"
    )
    assert not sent1_cats.get("row_ids"), (
        f"Filtered empty mapping resurrected row_ids={sent1_cats.get('row_ids')} on Sentence 1 token"
    )
    sent1_span = next(s for s in _source_word_spans(html_text) if s.get("data-word-idx") == str(sent1_cats["visual_idx"]))
    assert "not-connected" in (sent1_span.get("class") or "").split()


def test_data_lower_clean_attribute_escaping(tmp_path):
    source_text = "Cat's"
    headers = [
        "Quotation",
        "WordSource",
        "WordSourceInflectedForm",
        "WordDestination",
        "SentenceSourceIndex",
        "SentenceSource",
        "DeskSelected",
    ]
    rows = [
        [source_text, source_text, source_text, "значение", "1", source_text, "0"],
    ]
    html_text = _render_sqlite_session(
        tmp_path,
        source_text,
        "en",
        headers,
        rows,
        zid="20261127060416",
    )
    assert 'data-lower-clean="cat&#x27;s"' in html_text
    spans = _source_word_spans(html_text)
    assert len(spans) == 1
    assert spans[0].get("data-lower-clean") == "cat's"


def test_disable_css_suppresses_all_style_tags():
    payload = {
        "session_zid": "20261127060417",
        "language": "en",
        "theme": "dark",
        "source_html": '<span class="kw-sentence-chunk" data-sentence-idx="1"><span class="word">Hello</span></span>',
        "translated_text": '<div data-translation-sentence="1">Привет</div>',
        "active_sentence_idx": 1,
        "sentence_cards": [
            {"seq_num": 1, "sentence_idx": 0, "words": []},
            {"seq_num": 2, "sentence_idx": 1, "words": []},
        ],
        "ui_config": {
            "state_authority": "sqlite",
            "client_view_enabled": True,
        },
        "theme_colors": {"bg-color": "#101010"},
    }
    rendered = desk.render_declarative_web_shell(payload, theme="dark", disable_css=True)
    assert "<style" not in rendered.lower()


def test_theme_compact_accepts_theme_colors():
    style = theme_mod.theme_style({"bg-color": "#ffffff"}, "compact")
    assert style == '<style id="configured-theme">body.theme-compact{--bg-color:#ffffff;}</style>'

    payload = {
        "session_zid": "20261127060418",
        "language": "en",
        "theme": "compact",
        "source_html": '<span class="word">Hello</span>',
        "translated_text": "<div>Привет</div>",
        "active_sentence_idx": 0,
        "sentence_cards": [],
        "ui_config": {"state_authority": "static"},
        "theme_colors": {"bg-color": "#ffffff"},
    }
    rendered = desk.render_declarative_web_shell(payload, theme="compact", disable_css=False)
    assert 'body.theme-compact{--bg-color:#ffffff;}' in rendered
