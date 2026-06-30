# text_tokenizer.py — Pure Python port of Lua tokenizer text_utils.lua
import re

CYRILLIC_UPPER = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯÄÖÜẞ"
CYRILLIC_LOWER = "абвгдеёжзийклмнопрстуфхцчшщъыьэюяäöüß"

CYRILLIC_MAP = {CYRILLIC_UPPER[i]: CYRILLIC_LOWER[i] for i in range(len(CYRILLIC_UPPER))}
WORD_CHARS = set(CYRILLIC_UPPER + CYRILLIC_LOWER)

def utf8_to_lower(s: str) -> str:
    """Lowercase string mapping custom Cyrillic and German umlauts."""
    return "".join(CYRILLIC_MAP.get(c, c.lower()) for c in s)

def is_word_char(c: str) -> bool:
    """Check if character is a word character (ASCII alnum + apostrophe, or Russian/German mapping)."""
    if not c:
        return False
    if len(c) == 1:
        if ('a' <= c <= 'z') or ('A' <= c <= 'Z') or ('0' <= c <= '9') or c == "'":
            return True
    return c in WORD_CHARS

def build_word_list_internal(text: str, keep_spaces: bool) -> list:
    """Scanner-parser: tokenizes text into atoms matching Lua text_utils.lua build_word_list_internal."""
    tokens = []
    if not text:
        return tokens

    chars = list(text)
    i = 0
    n = len(chars)
    curr_logical_idx = 1
    curr_sub_idx = 0.1
    curr_visual_idx = 1

    while i < n:
        c = chars[i]
        token = {
            "text": "",
            "is_word": False,
            "logical_idx": None,
            "visual_idx": curr_visual_idx
        }

        # 1. Handle ASS Tags (Atomize)
        if c == "{":
            start = i
            while i < n and chars[i] != "}":
                i += 1
            if i < n:
                i += 1
            token["text"] = "".join(chars[start:i])

        # 3. Handle Whitespace
        elif c.isspace() or c == "\u00a0":
            start = i
            while i < n and (chars[i].isspace() or chars[i] == "\u00a0"):
                i += 1
            if keep_spaces:
                token["text"] = "".join(chars[start:i])
                token["logical_idx"] = float(f"{curr_logical_idx - 1 + curr_sub_idx:.4f}")
                curr_sub_idx += 0.1
            else:
                token = None

        # 4. Handle Word Characters (Scanning contiguous blocks)
        elif is_word_char(c):
            start = i
            while i < n and is_word_char(chars[i]):
                i += 1
            token["text"] = "".join(chars[start:i])
            token["is_word"] = True
            
            # Clean non-alphanumeric chars (excluding apostrophe) and lowercase
            cleaned_text = "".join(ch for ch in token["text"] if ch.isalnum() or ch == "'")
            token["lower_clean"] = utf8_to_lower(cleaned_text)
            token["logical_idx"] = curr_logical_idx
            curr_logical_idx += 1
            curr_sub_idx = 0.1

        # 5. Handle Line Breaks (Atomize \N, \n, \h)
        elif c == "\\" and i + 1 < n and chars[i + 1] in ("N", "n", "h"):
            token["text"] = c + chars[i + 1]
            token["logical_idx"] = float(f"{curr_logical_idx - 1 + curr_sub_idx:.4f}")
            curr_sub_idx += 0.1
            i += 2

        # 6. Handle Punctuation/Misc (Atomic Separator)
        else:
            token["text"] = c
            token["logical_idx"] = float(f"{curr_logical_idx - 1 + curr_sub_idx:.4f}")
            curr_sub_idx += 0.1
            i += 1

        if token:
            tokens.append(token)
            curr_visual_idx += 1

    return tokens

def build_word_list(text: str) -> list:
    """Returns list of plain word strings extracted from text."""
    tokens = build_word_list_internal(text, False)
    return [t["text"] for t in tokens if t["is_word"]]
