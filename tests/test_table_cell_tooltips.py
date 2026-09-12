import html
import pytest
from kardenwort_desk import (
    to_unicode_bold,
    format_inflected_sentence_tooltip,
    format_lemma_article_tooltip,
    format_pos_tooltip,
    format_gender_tooltip,
    format_classification_tooltip,
    run_render_flow,
    render_section,
    SEC_CLASSIFICATION,
)


def test_to_unicode_bold():
    # Test ASCII uppercase and lowercase conversion
    assert to_unicode_bold("Cat") == "\U0001D5D6\U0001D5EE\U0001D601"
    assert to_unicode_bold("Dog123") == "\U0001D5D7\U0001D5FC\U0001D5F4\U0001D7ED\U0001D7EE\U0001D7EF"

    # Test umlauts, Eszett and punctuation preservation
    bold_umlauts = to_unicode_bold("Äpfel, Köpfe & Über:groß!")
    # Ä, ö, Ü, ß, &, :, ! are preserved as-is, while ASCII letters are bolded
    assert "Ä" in bold_umlauts
    assert "ö" in bold_umlauts
    assert "Ü" in bold_umlauts
    assert "ß" in bold_umlauts
    assert ":" in bold_umlauts
    assert "!" in bold_umlauts
    assert "&" in bold_umlauts
    assert "," in bold_umlauts


def test_format_inflected_sentence_tooltip():
    # Single token match
    sent = "The brown fox jumps over the lazy dog."
    tooltip = format_inflected_sentence_tooltip(sent, "jumps", token_order="3")
    # 'jumps' should be replaced by its unicode bold version
    bold_jumps = to_unicode_bold("jumps")
    assert bold_jumps in tooltip
    assert "The brown fox " in tooltip
    assert " over the lazy dog." in tooltip

    # Separable verb pair (indicated by + or space in inflected token)
    sent_de = "Er steht jeden Tag früh auf."
    tooltip_de = format_inflected_sentence_tooltip(sent_de, "aufstehen + steht auf", token_order="0")
    bold_steht = to_unicode_bold("steht")
    bold_auf = to_unicode_bold("auf")
    assert bold_steht in tooltip_de
    assert bold_auf in tooltip_de

    # Fallback if sentence is empty
    assert format_inflected_sentence_tooltip("", "jumps") == "jumps"


def test_format_lemma_article_tooltip():
    # German masculine, feminine, neuter nouns
    assert format_lemma_article_tooltip("Hund", "m", lang="de") == "der Hund"
    assert format_lemma_article_tooltip("Katze", "f", lang="de") == "die Katze"
    assert format_lemma_article_tooltip("Haus", "n", lang="de") == "das Haus"
    assert format_lemma_article_tooltip("Mensch", "masc", lang="de") == "der Mensch"
    assert format_lemma_article_tooltip("Frau", "feminine", lang="de") == "die Frau"
    assert format_lemma_article_tooltip("Kind", "neuter", lang="de") == "das Kind"

    # Already has article
    assert format_lemma_article_tooltip("der Hund", "m", lang="de") == "der Hund"
    assert format_lemma_article_tooltip("die Katze", "f", lang="de") == "die Katze"

    # Non-noun or unknown gender
    assert format_lemma_article_tooltip("laufen", "", lang="de") == "laufen"
    assert format_lemma_article_tooltip("schnell", "", lang="de") == "schnell"

    # Non-German sessions (e.g. English, French)
    assert format_lemma_article_tooltip("dog", "m", lang="en") == "dog"
    assert format_lemma_article_tooltip("house", "n", lang="en") == "house"


def test_format_pos_tooltip():
    assert format_pos_tooltip("NOUN") == "Noun"
    assert format_pos_tooltip("PROPN") == "Noun"
    assert format_pos_tooltip("n.") == "Noun"
    assert format_pos_tooltip("VERB") == "Verb"
    assert format_pos_tooltip("AUX") == "Verb"
    assert format_pos_tooltip("v.") == "Verb"
    assert format_pos_tooltip("ADJ") == "Adjective"
    assert format_pos_tooltip("adj.") == "Adjective"
    assert format_pos_tooltip("ADV") == "Adverb"
    assert format_pos_tooltip("adv.") == "Adverb"
    assert format_pos_tooltip("ADP") == "Preposition"
    assert format_pos_tooltip("prep.") == "Preposition"
    assert format_pos_tooltip("PRON") == "Pronoun"
    assert format_pos_tooltip("pron.") == "Pronoun"
    assert format_pos_tooltip("CCONJ") == "Conjunction"
    assert format_pos_tooltip("conj.") == "Conjunction"
    assert format_pos_tooltip("DET") == "Article"
    assert format_pos_tooltip("art.") == "Article"
    assert format_pos_tooltip("NUM") == "Numeral"
    assert format_pos_tooltip("num.") == "Numeral"
    assert format_pos_tooltip("PART") == "Particle"
    assert format_pos_tooltip("part.") == "Particle"
    assert format_pos_tooltip("INTJ") == "Interjection"
    assert format_pos_tooltip("intj.") == "Interjection"
    assert format_pos_tooltip("") == ""
    assert format_pos_tooltip(None) == ""


def test_format_gender_tooltip():
    assert format_gender_tooltip("m") == "Masculine"
    assert format_gender_tooltip("MASC") == "Masculine"
    assert format_gender_tooltip("masculine") == "Masculine"
    assert format_gender_tooltip("f") == "Feminine"
    assert format_gender_tooltip("FEM") == "Feminine"
    assert format_gender_tooltip("feminine") == "Feminine"
    assert format_gender_tooltip("n") == "Neuter"
    assert format_gender_tooltip("NEUT") == "Neuter"
    assert format_gender_tooltip("neuter") == "Neuter"
    assert format_gender_tooltip("") == ""
    assert format_gender_tooltip(None) == ""
    assert format_gender_tooltip("other") == ""


def test_format_classification_tooltip():
    assert format_classification_tooltip("Goethe: A1", role="goethe") == "Goethe: A1"
    assert format_classification_tooltip("A1", role="goethe") == "Goethe: A1"
    assert format_classification_tooltip("Oxford: B2", role="oxford") == "Oxford: B2"
    assert format_classification_tooltip("B2", role="oxford") == "Oxford: B2"
    assert format_classification_tooltip("C1", role="cambridge") == "Cambridge: C1"
    assert format_classification_tooltip("", role="goethe") == ""
    assert format_classification_tooltip(None, role="goethe") == ""


def test_table_cell_tooltips_in_rendered_html(tmp_path):
    import configparser
    from tests.test_lookup_renderers import _create_render_test_env

    config, resolved_paths = _create_render_test_env(tmp_path)
    config.set(SEC_CLASSIFICATION, 'enabled', 'true')

    kw_config = configparser.ConfigParser()
    kw_config.add_section(SEC_CLASSIFICATION)
    kw_config.set(SEC_CLASSIFICATION, 'enabled', 'true')
    kw_config.set(SEC_CLASSIFICATION, 'dictionaries_de', 'goethe=path/to/goethe.tsv')
    with open(tmp_path / "config.ini", 'w', encoding='utf-8') as f:
        kw_config.write(f)

    mapping = configparser.ConfigParser()
    mapping.optionxform = str
    mapping.add_section('fields')
    mapping.add_section('fields_mapping.word')
    mapping.add_section('desk_columns')
    mapping.set('desk_columns', 'WordSource', 'lemma')
    mapping.set('desk_columns', 'WordSourceInflectedForm', 'inflected')
    mapping.set('desk_columns', 'WordDestination', 'word_translation')
    mapping.set('desk_columns', 'WordSourceIPA', 'ipa')
    mapping.set('desk_columns', 'WordSourceMorphologyAI', 'morphology')
    mapping.set('desk_columns', 'WordSourcePOS', 'pos')
    mapping.set('desk_columns', 'WordSourceGender', 'gender')
    mapping.set('desk_columns', 'goethe', 'goethe')

    mapping_file = tmp_path / "mapping.ini"
    with open(mapping_file, 'w', encoding='utf-8') as f:
        mapping.write(f)

    res_dir = tmp_path / "results"
    res_dir.mkdir(exist_ok=True)
    tsv_path = res_dir / "20260912223000-test-de.de.tsv"

    tsv_header = "WordSource\tWordSourceInflectedForm\tWordDestination\tWordSourceIPA\tWordSourceMorphologyAI\tWordSourcePOS\tWordSourceGender\tgoethe\n"
    tsv_rows = [
        "Hund\tHunde\tсобака\thʊnt\tSubstantiv, maskulin\tNOUN\tm\tA1\n",
        "Katze\tKatzen\tкошка\tˈkat͡sə\tSubstantiv, feminin\tNOUN\tf\tA1\n",
        "Haus\tHäuser\tдом\thaʊ̯s\tSubstantiv, neutral\tNOUN\tn\tA1\n"
    ]
    tsv_path.write_text(tsv_header + "".join(tsv_rows), encoding='utf-8')

    html_out = run_render_flow(
        text="Die Hunde schlafen im Haus.",
        language="de",
        zid="20260912223000",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=tsv_path
    )

    # 1. Verify inflected column title has sentence with bold token
    bold_hunde = to_unicode_bold("Hunde")
    assert f'Die {bold_hunde} schlafen im Haus.' in html_out
    assert f'title="{html.escape("Die " + bold_hunde + " schlafen im Haus.")}"' in html_out

    # 2. Verify lemma column title has definite article for German nouns
    assert 'title="der Hund"' in html_out
    assert 'title="die Katze"' in html_out
    assert 'title="das Haus"' in html_out

    # 3. Verify POS column title has unabbreviated POS name
    assert '<td class="col-pos" data-col="WordSourcePOS" title="Noun">' in html_out

    # 4. Verify Gender column title has unabbreviated Gender name
    assert '<td class="col-gender" data-col="WordSourceGender" title="Masculine">' in html_out
    assert '<td class="col-gender" data-col="WordSourceGender" title="Feminine">' in html_out
    assert '<td class="col-gender" data-col="WordSourceGender" title="Neuter">' in html_out

    # 5. Verify Classification column title has dictionary attribution
    assert '<td class="col-classification" data-col="goethe" title="Goethe: A1">' in html_out

    # 6. Verify IPA and Morphology column titles
    assert 'title="hʊnt"' in html_out
    assert 'title="Substantiv, maskulin"' in html_out


def test_client_side_js_helpers_parity(page, tmp_path):
    import configparser
    from tests.test_lookup_renderers import _create_render_test_env

    config, resolved_paths = _create_render_test_env(tmp_path)
    res_dir = tmp_path / "results"
    res_dir.mkdir(exist_ok=True)
    tsv_path = res_dir / "20260912224000-test-parity.de.tsv"
    tsv_path.write_text("WordSource\tWordSourceInflectedForm\tWordDestination\nKatze\tKatzen\tкошка\n", encoding='utf-8')

    html_out = run_render_flow(
        text="Die Katzen schlafen.",
        language="de",
        zid="20260912224000",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=tsv_path
    )

    page.set_content(html_out)

    # 1. Test window.toUnicodeBold
    js_bold = page.evaluate("window.toUnicodeBold('Katzen')")
    py_bold = to_unicode_bold("Katzen")
    assert js_bold == py_bold

    # 2. Test window.formatInflectedTooltip
    js_inf_tip = page.evaluate("window.formatInflectedTooltip('Die Katzen schlafen.', 'Katzen', '1')")
    py_inf_tip = format_inflected_sentence_tooltip("Die Katzen schlafen.", "Katzen", token_order="1")
    assert js_inf_tip == py_inf_tip

    # 3. Test window.formatLemmaTooltip
    js_lem_tip = page.evaluate("window.formatLemmaTooltip('Katze', 'f', 'de')")
    py_lem_tip = format_lemma_article_tooltip("Katze", "f", lang="de")
    assert js_lem_tip == py_lem_tip

    # 4. Test window.formatPosTooltip
    assert page.evaluate("window.formatPosTooltip('NOUN')") == "Noun"
    assert page.evaluate("window.formatPosTooltip('VERB')") == "Verb"
    assert page.evaluate("window.formatPosTooltip('ADJ')") == "Adjective"

    # 5. Test window.formatGenderTooltip
    assert page.evaluate("window.formatGenderTooltip('m')") == "Masculine"
    assert page.evaluate("window.formatGenderTooltip('f')") == "Feminine"
    assert page.evaluate("window.formatGenderTooltip('n')") == "Neuter"
    assert page.evaluate("window.formatGenderTooltip('other')") == ""

    # 6. Test window.formatClassificationTooltip
    assert page.evaluate("window.formatClassificationTooltip('A1', 'goethe')") == "Goethe: A1"
    assert page.evaluate("window.formatClassificationTooltip('Oxford: B2', 'oxford')") == "Oxford: B2"


def test_client_side_delta_updates_refresh_cell_tooltips(page, tmp_path):
    import configparser
    from tests.test_lookup_renderers import _create_render_test_env

    config, resolved_paths = _create_render_test_env(tmp_path)
    config.set(SEC_CLASSIFICATION, 'enabled', 'true')
    config.set('languages', 'de_prompt', 'de_prompt')

    kw_config = configparser.ConfigParser()
    kw_config.add_section(SEC_CLASSIFICATION)
    kw_config.set(SEC_CLASSIFICATION, 'enabled', 'true')
    kw_config.set(SEC_CLASSIFICATION, 'dictionaries_de', 'goethe=path/to/goethe.tsv')
    with open(tmp_path / "config.ini", 'w', encoding='utf-8') as f:
        kw_config.write(f)

    mapping = configparser.ConfigParser()
    mapping.optionxform = str
    mapping.add_section('fields')
    mapping.add_section('fields_mapping.word')
    mapping.add_section('desk_columns')
    mapping.set('desk_columns', 'WordSource', 'lemma')
    mapping.set('desk_columns', 'WordSourceInflectedForm', 'inflected')
    mapping.set('desk_columns', 'WordDestination', 'word_translation')
    mapping.set('desk_columns', 'WordSourceIPA', 'ipa')
    mapping.set('desk_columns', 'WordSourceMorphologyAI', 'morphology')
    mapping.set('desk_columns', 'WordSourcePOS', 'pos')
    mapping.set('desk_columns', 'WordSourceGender', 'gender')
    mapping.set('desk_columns', 'goethe', 'goethe')

    mapping_file = tmp_path / "mapping.ini"
    with open(mapping_file, 'w', encoding='utf-8') as f:
        mapping.write(f)

    res_dir = tmp_path / "results"
    res_dir.mkdir(exist_ok=True)
    tsv_path = res_dir / "20260912224500-test-delta.de.tsv"
    tsv_header = "WordSource\tWordSourceInflectedForm\tWordDestination\tWordSourceIPA\tWordSourceMorphologyAI\tWordSourcePOS\tWordSourceGender\tgoethe\n"
    tsv_rows = ["Hund\tHunde\t\t\t\t\t\t\n"]
    tsv_path.write_text(tsv_header + "".join(tsv_rows), encoding='utf-8')

    html_out = run_render_flow(
        text="Die Hunde schlafen.",
        language="de",
        zid="20260912224500",
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        tsv_path=tsv_path
    )

    page.set_content(html_out)

    # Initial state: POS and Gender are empty
    pos_cell = page.locator("#lemma-table tbody tr[data-row-id='0'] td.col-pos")
    gender_cell = page.locator("#lemma-table tbody tr[data-row-id='0'] td.col-gender")
    assert pos_cell.get_attribute("title") is None or pos_cell.get_attribute("title") == ""

    # Apply delta update
    page.evaluate("""
        window.receiveUpdate({
            stage: 'translated_words',
            rows: {
                "0": {
                    pos: "NOUN",
                    gender: "m",
                    morph: "Substantiv, maskulin",
                    ipa: "hʊnt",
                    classifications: {
                        goethe: "A1"
                    }
                }
            }
        });
    """)

    # Verify updated tooltips
    assert pos_cell.get_attribute("title") == "Noun"
    assert gender_cell.get_attribute("title") == "Masculine"

    morph_cell = page.locator("#lemma-table tbody tr[data-row-id='0'] td.col-morphology")
    assert morph_cell.get_attribute("title") == "Substantiv, maskulin"

    ipa_cell = page.locator("#lemma-table tbody tr[data-row-id='0'] td.col-ipa")
    assert ipa_cell.get_attribute("title") == "hʊnt"

    cls_cell = page.locator("#lemma-table tbody tr[data-row-id='0'] td.col-classification")
    assert cls_cell.get_attribute("title") == "Goethe: A1"


def test_inflected_column_gray_color_styling(page, tmp_path):
    """Verifies that INFLECTED column text is rendered in text_muted gray matching IPA and Morphology, while Lemma and Translation use main text color."""
    import kardenwort_desk
    config, resolved_paths, goldendict, wordfill = kardenwort_desk.load_config()
    zid = "20260913001500"
    tsv_file = tmp_path / f"{zid}-colors.de.tsv"
    tsv_content = (
        "# comment\n"
        "Quotation\tWordSource\tWordSourceInflectedForm\tWordDestination\tWordSourceIPA\tWordSourceMorphologyAI\tSentenceSourceIndex\tDeskSelected\n"
        "Häuser\tHaus\tHäuser\tдома\t[ˈhɔɪ̯zɐ]\tSubstantiv, Neutrum\t1\t1\n"
    )
    tsv_file.write_text(tsv_content, encoding="utf-8")

    html_code = kardenwort_desk.run_render_flow(
        text="Häuser",
        language="de",
        zid=zid,
        text_mode="single",
        config=config,
        resolved_paths=resolved_paths,
        theme="dark",
        tsv_path=str(tsv_file),
        spawn_children=False
    )
    assert "#lemma-table th.col-inflected, #lemma-table td.col-inflected" in html_code

    page.set_content(html_code)

    inflected_cell = page.locator("#lemma-table tbody tr[data-row-id='0'] td.col-inflected")
    ipa_cell = page.locator("#lemma-table tbody tr[data-row-id='0'] td.col-ipa")
    lemma_cell = page.locator("#lemma-table tbody tr[data-row-id='0'] td.col-lemma")
    trans_cell = page.locator("#lemma-table tbody tr[data-row-id='0'] td.col-translation")

    assert inflected_cell.count() == 1
    assert ipa_cell.count() == 1
    assert lemma_cell.count() == 1
    assert trans_cell.count() == 1

    inf_color = inflected_cell.evaluate("el => window.getComputedStyle(el).color")
    ipa_color = ipa_cell.evaluate("el => window.getComputedStyle(el).color")
    lemma_color = lemma_cell.evaluate("el => window.getComputedStyle(el).color")
    trans_color = trans_cell.evaluate("el => window.getComputedStyle(el).color")

    # INFLECTED and IPA must have identical gray text color
    assert inf_color == ipa_color

    # LEMMA and TRANSLATE must have bright/main color distinct from gray
    assert lemma_color == trans_color
    assert inf_color != lemma_color

