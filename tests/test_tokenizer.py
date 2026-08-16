import pytest
import text_tokenizer as tok

def test_is_word_char():
    assert tok.is_word_char("a") is True
    assert tok.is_word_char("Z") is True
    assert tok.is_word_char("9") is True
    assert tok.is_word_char("'") is False
    assert tok.is_word_char("’") is False
    assert tok.is_word_char("‘") is False
    assert tok.is_word_char("`") is False
    assert tok.is_word_char("´") is False
    assert tok.is_word_char("ʼ") is False
    assert tok.is_word_char("ä") is True
    assert tok.is_word_char("Ö") is True
    assert tok.is_word_char("ß") is True
    assert tok.is_word_char("ж") is True
    assert tok.is_word_char("Я") is True
    assert tok.is_word_char("!") is False
    assert tok.is_word_char(" ") is False
    assert tok.is_word_char(None) is False
    assert tok.is_word_char("") is False

def test_utf8_to_lower():
    assert tok.utf8_to_lower("HELLO") == "hello"
    assert tok.utf8_to_lower("ÄÖÜẞ") == "äöüß"
    assert tok.utf8_to_lower("АБВГ") == "абвг"

def test_build_word_list():
    text = "The quick brown fox jumps over the lazy dog."
    words = tok.build_word_list(text)
    assert words == ["The", "quick", "brown", "fox", "jumps", "over", "the", "lazy", "dog"]

def test_build_word_list_internal_keep_spaces():
    text = "Hello, world! \\N New line."
    tokens = tok.build_word_list_internal(text, keep_spaces=True)
    
    # Verify we got words, spaces, punctuation, line breaks
    texts = [t["text"] for t in tokens]
    assert "Hello" in texts
    assert ", " not in texts # it gets split as ',' and ' ' because they are separate atomic categories
    assert "," in texts
    assert " " in texts
    assert "\\N" in texts
    assert "world" in texts
    
    # Verify visual and logical indices
    word_tokens = [t for t in tokens if t["is_word"]]
    assert word_tokens[0]["logical_idx"] == 1
    assert word_tokens[1]["logical_idx"] == 2
    assert word_tokens[2]["logical_idx"] == 3
    assert word_tokens[3]["logical_idx"] == 4

def test_build_word_list_internal_ass_tags():
    text = "Word {\\an8} tag"
    tokens = tok.build_word_list_internal(text, keep_spaces=True)
    texts = [t["text"] for t in tokens]
    assert "Word" in texts
    assert "{\\an8}" in texts
    assert "tag" in texts

def test_build_word_list_typographic_apostrophes():
    text = "Let’s hope they don’t split when using ` or ´ or ʼ apostrophes in words like can‘t."
    words = tok.build_word_list(text)
    assert "Let’s" in words
    assert "don’t" in words
    assert "can‘t" in words

def test_build_word_list_internal_typographic_contractions():
    text = "Let’s see."
    tokens = tok.build_word_list_internal(text, keep_spaces=False)
    word_tokens = [t for t in tokens if t["is_word"]]
    assert len(word_tokens) == 2
    assert word_tokens[0]["text"] == "Let’s"
    assert word_tokens[0]["lower_clean"] == "let’s"

def test_split_camel_case():
    assert tok.split_camel_case("") == []
    assert tok.split_camel_case(None) == []
    assert tok.split_camel_case("simple") == ["simple"]
    assert tok.split_camel_case("camelCase") == ["camel", "Case"]
    assert tok.split_camel_case("PascalCase") == ["Pascal", "Case"]
    assert tok.split_camel_case("HTMLParser") == ["HTML", "Parser"]
    assert tok.split_camel_case("getHTTPResponse") == ["get", "HTTP", "Response"]
    assert tok.split_camel_case("MULTI") == ["MULTI"]

def test_decompose_identifier():
    assert tok.decompose_identifier("") == []
    assert tok.decompose_identifier(None) == []
    assert tok.decompose_identifier("_") == []
    assert tok.decompose_identifier("__init__") == ["init"]
    assert tok.decompose_identifier("simple") == ["simple"]
    
    # snake_case
    assert tok.decompose_identifier("prepare_lookup_tsv") == ["prepare", "lookup", "tsv"]
    
    # kebab-case
    assert tok.decompose_identifier("per-window") == ["per", "window"]
    assert tok.decompose_identifier("open-sourced") == ["open", "sourced"]
    
    # dotted and compound identifiers
    assert tok.decompose_identifier("ExecutionContext.from_config") == [
        "ExecutionContext", "Execution", "Context", "from", "config"
    ]
    assert tok.decompose_identifier("OperationalMode.MULTI_SENTENCE_LOCAL_DEDUP") == [
        "OperationalMode", "Operational", "Mode", "MULTI", "SENTENCE", "LOCAL", "DEDUP"
    ]
    
    # Mixed cases & abbreviations
    assert tok.decompose_identifier("Hunde-Haus") == ["Hunde", "Haus"]
    assert tok.decompose_identifier("e.g.") == ["e", "g"]
    assert tok.decompose_identifier("word1_word2-word3.word4") == ["word1", "word2", "word3", "word4"]
    assert tok.decompose_identifier("UTF8String") == ["UTF8String", "UTF8", "String"]
    assert tok.decompose_identifier("HTML5Parser") == ["HTML5Parser", "HTML5", "Parser"]
    # Dot-separated filenames and path decomposition
    assert tok.decompose_identifier("spec.md") == ["spec", "md"]
    assert tok.decompose_identifier("openspec/changes/specs/spec.md") == ["openspec", "changes", "specs", "spec", "md"]
    assert tok.decompose_identifier(r"openspec\specs\spec.md") == ["openspec", "specs", "spec", "md"]
    assert tok.decompose_identifier("20260815131120-token-mapping") == ["20260815131120", "token", "mapping"]

def test_extract_identifier_subtokens():
    assert tok.extract_identifier_subtokens("") == []
    assert tok.extract_identifier_subtokens(None) == []
    assert tok.extract_identifier_subtokens("_") == []
    assert tok.extract_identifier_subtokens("__init__") == ["init"]
    assert tok.extract_identifier_subtokens("simple") == ["simple"]
    
    # snake_case left-to-right appearance order
    assert tok.extract_identifier_subtokens("split_camel_case") == ["split", "camel", "case"]
    assert tok.extract_identifier_subtokens("prepare_lookup_tsv") == ["prepare", "lookup", "tsv"]
    
    # kebab-case and dotted
    assert tok.extract_identifier_subtokens("per-window") == ["per", "window"]
    assert tok.extract_identifier_subtokens("text_tokenizer.py") == ["text", "tokenizer", "py"]
    assert tok.extract_identifier_subtokens("spec.md") == ["spec", "md"]
    assert tok.extract_identifier_subtokens("openspec/changes/specs/spec.md") == ["openspec", "changes", "specs", "spec", "md"]
    assert tok.extract_identifier_subtokens(r"openspec\specs\spec.md") == ["openspec", "specs", "spec", "md"]
    assert tok.extract_identifier_subtokens("20260815131120-token-mapping") == ["20260815131120", "token", "mapping"]
    assert tok.extract_identifier_subtokens("ExecutionContext.from_config") == [
        "Execution", "Context", "from", "config"
    ]
    assert tok.extract_identifier_subtokens("OperationalMode.MULTI_SENTENCE_LOCAL_DEDUP") == [
        "Operational", "Mode", "MULTI", "SENTENCE", "LOCAL", "DEDUP"
    ]
    assert tok.extract_identifier_subtokens("word1_word2-word3.word4") == ["word1", "word2", "word3", "word4"]
    assert tok.extract_identifier_subtokens("UTF8String") == ["UTF8", "String"]
    assert tok.extract_identifier_subtokens("БыстрыйПоиск") == ["Быстрый", "Поиск"]


def test_build_word_list_quoted_words():
    text = "display = 'none'"
    tokens = tok.build_word_list_internal(text, keep_spaces=True)
    word_tokens = [t for t in tokens if t["is_word"]]
    assert len(word_tokens) == 2
    assert word_tokens[0]["text"] == "display"
    assert word_tokens[0]["lower_clean"] == "display"
    assert word_tokens[1]["text"] == "none"
    assert word_tokens[1]["lower_clean"] == "none"
    
    # Check punctuation tokens for single quotes
    punct_tokens = [t for t in tokens if not t["is_word"] and t["text"] == "'"]
    assert len(punct_tokens) == 2


def test_build_word_list_camel_case_splitting():
    text = "flipWord and PascalCase"
    tokens = tok.build_word_list_internal(text, keep_spaces=True)
    word_tokens = [t for t in tokens if t["is_word"]]
    assert len(word_tokens) == 5
    assert [t["text"] for t in word_tokens] == ["flip", "Word", "and", "Pascal", "Case"]
    assert [t["lower_clean"] for t in word_tokens] == ["flip", "word", "and", "pascal", "case"]


def test_build_word_list_decimal_numbers():
    text = "Version 2.0 released"
    tokens = tok.build_word_list_internal(text, keep_spaces=True)
    word_tokens = [t for t in tokens if t["is_word"]]
    assert [t["text"] for t in word_tokens] == ["Version", "2", "0", "released"]
    dot_tokens = [t for t in tokens if not t["is_word"] and t["text"] == "."]
    assert len(dot_tokens) == 1




