import pytest
import configparser
import kardenwort_desk as desk
from kardenwort_desk import TranslationAlignmentError

def test_clean_sentence_splits():
    lines = ["First sentence", ", second sentence.", "Punctuation only", "...", "Third sentence"]
    res = desk.clean_sentence_splits(lines)
    assert len(res) == len(lines)
    assert res[0] == "First sentence,"
    assert res[1] == "second sentence."
    assert res[2] == "Punctuation only..."
    assert res[3] == ""
    assert res[4] == "Third sentence"

def test_split_long_line():
    line = "word1 word2 word3 word4 word5"
    res = desk._split_long_line(line, 12)
    assert res == ["word1 word2", "word3 word4", "word5"]

def test_validate_translated_line():
    config = configparser.ConfigParser()
    config.add_section('translation')
    config.set('translation', 'translation_word_count_check', 'true')
    config.set('translation', 'translation_word_count_abs_tolerance', '2')
    config.set('translation', 'translation_word_count_min_ratio', '0.5')
    config.set('translation', 'translation_word_count_max_ratio', '2.0')
    
    # Normal
    desk._validate_translated_line("one two three", "раз два три", 0, config)
    
    # Within absolute tolerance
    desk._validate_translated_line("one two", "раз два три четыре", 0, config)
    
    # Fails ratio check and absolute tolerance
    with pytest.raises(ValueError, match="Word count mismatch"):
        desk._validate_translated_line("one two", "раз два три четыре пять", 0, config)

def test_build_chunks():
    config = configparser.ConfigParser()
    config.add_section('translation')
    
    # Fixed size chunking
    lines = ["a", "b", "c", "", "d", "e"]
    chunks = desk._build_chunks(lines, 2, config)
    assert chunks == [
        (["a", "b"], [0, 1]),
        (["c", "d"], [2, 4]),
        (["e"], [5])
    ]
    
    # Adaptive chunking (size = 0)
    config.set('translation', 'translation_adaptive_max_lines', '2')
    config.set('translation', 'translation_adaptive_max_chars', '50')
    chunks_adaptive = desk._build_chunks(lines, 0, config)
    assert chunks_adaptive == [
        (["a", "b"], [0, 1]),
        (["c", "d"], [2, 4]),
        (["e"], [5])
    ]

def test_split_by_proportion():
    text = "one two three four five"
    res = desk.split_by_proportion(text, [6, 6, 12])
    assert len(res) == 3

def test_split_merged_text_by_markers():
    text = "one[[KWSPLIT0001]]two[[KWSPLIT0002]]three"
    res = desk.split_merged_text_by_markers(text, ["[[KWSPLIT0001]]", "[[KWSPLIT0002]]"])
    assert res == ["one", "two", "three"]

def test_validate_translation_config_guard():
    config = configparser.ConfigParser()
    config.add_section('translation')
    config.set('translation', 'translation_split_mode', 'proportional')
    config.set('translation', 'translation_word_count_check', 'true')
    
    desk._validate_translation_config(config)
    assert config.get('translation', 'translation_word_count_check') == 'false'

def test_translate_source_text_split_modes(monkeypatch):
    config = configparser.ConfigParser()
    config.add_section('translation')
    
    # 1. newline_join
    config.set('translation', 'translation_split_mode', 'newline_join')
    def mock_translate_text_newline(text, source_lang, target_lang, cfg, paths, provider):
        return "\n".join(f"Trans_{l}" for l in text.splitlines())
    monkeypatch.setattr(desk, "translate_text", mock_translate_text_newline)
    
    res = desk.translate_source_text("line1\nline2", "en", "ru", "multi", config, {}, "google")
    assert res == {0: "Trans_line1", 1: "Trans_line2"}
    
    # 2. line_by_line
    config.set('translation', 'translation_split_mode', 'line_by_line')
    res_lbl = desk.translate_source_text("line1\nline2", "en", "ru", "multi", config, {}, "google")
    assert res_lbl == {0: "Trans_line1", 1: "Trans_line2"}
    
    # 3. marker
    config.set('translation', 'translation_split_mode', 'marker')
    def mock_translate_marker(text, source_lang, target_lang, cfg, paths, provider):
        return text.replace("line1", "Trans_line1").replace("line2", "Trans_line2")
    monkeypatch.setattr(desk, "translate_text", mock_translate_marker)
    
    res_marker = desk.translate_source_text("line1\nline2", "en", "ru", "multi", config, {}, "google")
    assert res_marker == {0: "Trans_line1", 1: "Trans_line2"}
    
    # Escaping marker collision guard
    res_esc = desk.translate_source_text("line1 with [[KWSPLIT prefix\nline2", "en", "ru", "multi", config, {}, "google")
    assert res_esc[0] == "Trans_line1 with [[KWSPLIT prefix"

def test_single_mode_routing(monkeypatch):
    config = configparser.ConfigParser()
    config.add_section('translation')
    config.set('translation', 'translation_wrap_max_chars', '10')
    
    called_multi = False
    def mock_translate(text, source_lang, target_lang, cfg, paths, provider):
        nonlocal called_multi
        if "\n" in text:
            called_multi = True
        return text.upper()
    monkeypatch.setattr(desk, "translate_text", mock_translate)
    
    # Short single sentence
    res_short = desk.translate_source_text("short", "en", "ru", "single", config, {}, "google")
    assert res_short == {0: "SHORT"}
    assert not called_multi
    
    # Long single sentence wrapped
    res_long = desk.translate_source_text("this is a very long paragraph", "en", "ru", "single", config, {}, "google")
    assert res_long == {0: "THIS IS A", 1: "VERY LONG", 2: "PARAGRAPH"}
    assert called_multi

def test_translation_alignment_error_rescue(monkeypatch):
    config = configparser.ConfigParser()
    config.add_section('translation')
    config.set('translation', 'translation_split_mode', 'newline_join')
    config.set('translation', 'translation_max_retries', '1')
    config.set('translation', 'translation_chunk_size', '2')
    
    # Chunk 1: [line1, line2] -> translates fine
    # Chunk 2: [line3, line4] -> chunk fails (wrong line count), triggers rescue.
    # Rescue: line3 succeeds, line4 raises exception
    def mock_translate(text, source_lang, target_lang, cfg, paths, provider):
        if "line3" in text and "line4" in text:
            return "only_one_line"
        if text == "line3":
            return "Trans_line3"
        if text == "line4":
            raise ValueError("Rescue failed for line 4")
        return text.upper()
        
    monkeypatch.setattr(desk, "translate_text", mock_translate)
    
    with pytest.raises(TranslationAlignmentError) as excinfo:
        desk.translate_source_text("line1\nline2\nline3\nline4", "en", "ru", "multi", config, {}, "google")
    
    partial = excinfo.value.partial_dict
    assert partial[0] == "LINE1"
    assert partial[1] == "LINE2"
    assert partial[2] == "Trans_line3"
    assert partial[3] == "" # Failing line is blanked

def test_single_mode_invariant_check():
    data_rows = [["1", "a", "", "snippet1"], ["2", "b", "", "snippet2"]]
    headers = ["SentenceSourceIndex", "Lemma", "SentenceTranslation", "SentenceSource"]
    comments = []
    
    # In single mode, rows resolve to their corresponding segment translations
    # in sentence_translations_raw, and SentenceSource is preserved (not overwritten).
    res = desk.resolve_translations(
        text="hello", text_mode="single", data_rows=data_rows,
        col_index=0, col_sentence_dest=2, sentence_translations_raw={0: "hello", 1: "world"},
        tsv_path=None, comments=comments, headers=headers, persist=False
    )
    assert res is None
    assert data_rows[0][2] == "hello"
    assert data_rows[1][2] == "world"
    assert data_rows[0][3] == "snippet1"
    assert data_rows[1][3] == "snippet2"

def test_abbreviation_aware_splitting():
    # Abbreviations ca., usw., uzw., d.h., z.B. should not split the sentence.
    text = "Wir haben ca. 5 Äpfel usw. und auch Birnen d.h. Obst. Aber Kirschen nicht."
    res = desk.split_single_mode_text(text)
    assert res == [
        "Wir haben ca. 5 Äpfel usw. und auch Birnen d.h. Obst.",
        "Aber Kirschen nicht."
    ]

def test_pad_sentences():
    text = "This is sentence one. Here is sentence two. Finally, sentence three."
    sentences = ["This is sentence one.", "Here is sentence two.", "Finally, sentence three."]
    
    # 1. No padding
    res = desk.pad_sentences(sentences, text)
    assert res == sentences
    
    # 2. Word padding before and after
    res_words = desk.pad_sentences(sentences, text, words_before=2, words_after=2)
    assert res_words[1] == "sentence one. Here is sentence two. Finally, sentence"
    
    # 3. Out of bounds fallback/clipping
    res_clip = desk.pad_sentences(sentences, text, words_before=100, words_after=100)
    assert res_clip[1] == text
def test_custom_abbreviation_allowlist():
    text = "Wir trafen Prof. Müller. Er ging nach Hause."
    
    # Standard splitting (Prof. is in defaults)
    res_default = desk.split_single_mode_text(text)
    assert res_default == ["Wir trafen Prof. Müller.", "Er ging nach Hause."]
    
    # Custom allowlist (without Prof.)
    custom_abbrev_set = {"ca", "z.b"}
    res_custom = desk.split_single_mode_text(text, abbrevs=custom_abbrev_set)
    assert res_custom == ["Wir trafen Prof.", "Müller.", "Er ging nach Hause."]

def test_custom_terminators():
    text = "Sentence one: detail. Sentence two! Sentence three."
    
    # Default splitting (splits on colon too)
    res_default = desk.split_single_mode_text(text)
    assert res_default == ["Sentence one:", "detail.", "Sentence two!", "Sentence three."]
    
    # Custom terminators (only .!)
    res_custom = desk.split_single_mode_text(text, terminators=".!")
    assert res_custom == ["Sentence one: detail.", "Sentence two!", "Sentence three."]

def test_context_truncation():
    text = "word1 word2 word3 TargetSentence word4 word5 word6"
    sentences = ["TargetSentence"]
    
    # Pad by 3 words on both sides, limit to 5 total words
    res = desk.pad_sentences(sentences, text, words_before=3, words_after=3, max_words=5)
    # TargetSentence (1 word) + 2 words left + 2 words right = 5 words total
    assert res[0] == "word2 word3 TargetSentence word4 word5"

def test_punctuation_marks_prevent_wrapping():
    # Long text with a comma but no terminators (.!?)
    text = "Here is why this synchronization is only necessary for multi mode, whereas single mode handles it implicitly"
    
    # Under standard wrap limit of 90, if we do not check punctuation, it would wrap/split.
    # Since it contains a comma (,), and by default comma is in punctuation_marks, it should NOT wrap.
    res = desk.split_single_mode_text(text, max_chars=90)
    assert res == [text]

    # If we pass custom punctuation_marks that does NOT include comma, it should wrap.
    res_wrap = desk.split_single_mode_text(text, max_chars=90, punctuation_marks=".")
    assert len(res_wrap) > 1

