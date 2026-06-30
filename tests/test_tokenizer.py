import pytest
import text_tokenizer as tok

def test_is_word_char():
    assert tok.is_word_char("a") is True
    assert tok.is_word_char("Z") is True
    assert tok.is_word_char("9") is True
    assert tok.is_word_char("'") is True
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
