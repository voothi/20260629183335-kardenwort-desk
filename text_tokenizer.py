# text_tokenizer.py — Pure Python port of Lua tokenizer text_utils.lua
import re

CYRILLIC_UPPER = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯÄÖÜẞ"
CYRILLIC_LOWER = "абвгдеёжзийклмнопрстуфхцчшщъыьэюяäöüß"

CYRILLIC_MAP = {CYRILLIC_UPPER[i]: CYRILLIC_LOWER[i] for i in range(len(CYRILLIC_UPPER))}
WORD_CHARS = set(CYRILLIC_UPPER + CYRILLIC_LOWER)
APOSTROPHE_CHARS = {"'", "’", "‘", "`", "´", "ʼ"}

def utf8_to_lower(s: str) -> str:
    """Lowercase string mapping custom Cyrillic and German umlauts."""
    return "".join(CYRILLIC_MAP.get(c, c.lower()) for c in s)

def is_word_char(c: str) -> bool:
    """Check if character is a base word character (ASCII alnum or Russian/German mapping)."""
    if not c:
        return False
    if len(c) == 1:
        if ('a' <= c <= 'z') or ('A' <= c <= 'Z') or ('0' <= c <= '9'):
            return True
    return c in WORD_CHARS

# Informal apostrophe-less contractions recognized by spaCy
INFORMAL_CONTRACTIONS = {
    # Pronoun / Noun + 've
    "ive": 1,
    "youve": 3,
    "weve": 2,
    "theyve": 4,
    "couldve": 5,
    "shouldve": 6,
    "wouldve": 5,
    "mightve": 5,
    "mustve": 4,
    # Pronoun + 're (note: 'were' is omitted as it is past tense of 'be')
    "youre": 3,
    "theyre": 4,
    # Verb + n't (negation)
    "dont": 2,
    "didnt": 3,
    "doesnt": 4,
    "cant": 2,
    "wont": 2,
    "isnt": 2,
    "arent": 3,
    "wasnt": 3,
    "werent": 4,
    "hasnt": 3,
    "havent": 4,
    "hadnt": 3,
    "couldnt": 5,
    "shouldnt": 6,
    "wouldnt": 5,
    "mustnt": 4,
    "neednt": 4,
    "darent": 4,
    "aint": 2,
    # Pronoun + 'd (unambiguous)
    "youd": 3,
    "theyd": 4,
    # Pronoun + 'll (unambiguous; 'ill', 'well', 'shell' omitted)
    "youll": 3,
    "theyll": 4,
    "itll": 2,
    "thatll": 4,
}

# Explicit safeguards: English words or abbreviations that MUST NEVER be decomposed
SAFEGUARD_WORDS = {"id", "wed", "were", "ill", "well", "shell", "canton", "cantor"}

def split_informal_contraction(s: str) -> list:
    """
    Split informal English contractions without apostrophes (e.g. Ive -> ['I', 've'],
    dont -> ['do', 'nt'], didnt -> ['did', 'nt']) into constituent sub-tokens.
    Preserves casing of original slices.
    """
    if not s or any(ch in APOSTROPHE_CHARS for ch in s):
        return []
    lower_s = utf8_to_lower(s)
    if lower_s in SAFEGUARD_WORDS:
        return []
    if lower_s in INFORMAL_CONTRACTIONS:
        split_idx = INFORMAL_CONTRACTIONS[lower_s]
        return [s[:split_idx], s[split_idx:]]
    return []

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
    curr_compound_id = 1

    while i < n:
        c = chars[i]
        token = {
            "text": "",
            "is_word": False,
            "logical_idx": None,
            "visual_idx": curr_visual_idx
        }

        # 1. Handle ASS Tags (Atomize only if {\)
        if c == "{" and i + 1 < n and chars[i + 1] == "\\":
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

        # 4. Handle Word Characters (Scanning contiguous blocks with intra-word apostrophes and camelCase splitting)
        elif is_word_char(c):
            start = i
            while i < n:
                if is_word_char(chars[i]):
                    i += 1
                elif chars[i] in APOSTROPHE_CHARS and i + 1 < n and is_word_char(chars[i + 1]):
                    i += 1
                else:
                    break
            raw_word = "".join(chars[start:i])
            camel_parts = split_camel_case(raw_word)
            if len(camel_parts) > 1:
                sub_units = camel_parts
                comp_id = curr_compound_id
                curr_compound_id += 1
            else:
                contraction_parts = split_informal_contraction(raw_word)
                if len(contraction_parts) > 1:
                    sub_units = contraction_parts
                    comp_id = curr_compound_id
                    curr_compound_id += 1
                else:
                    sub_units = [raw_word]
                    comp_id = None

            for unit in sub_units:
                sub_tok = {
                    "text": unit,
                    "is_word": True,
                    "logical_idx": curr_logical_idx,
                    "visual_idx": curr_visual_idx
                }
                if comp_id is not None:
                    sub_tok["compound_id"] = comp_id
                cleaned_text = "".join(ch for ch in unit if ch.isalnum() or ch in APOSTROPHE_CHARS or ch in WORD_CHARS)
                sub_tok["lower_clean"] = utf8_to_lower(cleaned_text)
                tokens.append(sub_tok)
                curr_logical_idx += 1
                curr_visual_idx += 1
                curr_sub_idx = 0.1
            token = None

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

def build_orthographic_token_spans(text: str) -> list:
    """Tokenizes text into top-level orthographic words and delimiters without camelCase decomposition."""
    tokens = []
    if not text:
        return tokens

    chars = list(text)
    i = 0
    n = len(chars)
    curr_visual_idx = 1

    while i < n:
        c = chars[i]
        token = {
            "text": "",
            "is_word": False,
            "visual_idx": curr_visual_idx
        }

        # 1. Handle ASS Tags (Atomize only if {\)
        if c == "{" and i + 1 < n and chars[i + 1] == "\\":
            start = i
            while i < n and chars[i] != "}":
                i += 1
            if i < n:
                i += 1
            token["text"] = "".join(chars[start:i])

        # 2. Handle Whitespace
        elif c.isspace() or c == "\u00a0":
            start = i
            while i < n and (chars[i].isspace() or chars[i] == "\u00a0"):
                i += 1
            token["text"] = "".join(chars[start:i])

        # 3. Handle Word Characters (Scanning contiguous blocks with intra-word apostrophes/connectors without camelCase splitting)
        elif is_word_char(c):
            start = i
            while i < n:
                if is_word_char(chars[i]):
                    i += 1
                elif chars[i] in APOSTROPHE_CHARS and i + 1 < n and is_word_char(chars[i + 1]):
                    i += 1
                elif chars[i] in ('_', '.') and i + 1 < n and is_word_char(chars[i + 1]):
                    i += 1
                else:
                    break
            raw_word = "".join(chars[start:i])
            token["text"] = raw_word
            token["is_word"] = True

        # 4. Handle Line Breaks (\N, \n, \h)
        elif c == "\\" and i + 1 < n and chars[i + 1] in ("N", "n", "h"):
            token["text"] = c + chars[i + 1]
            i += 2

        # 5. Handle Punctuation/Misc
        else:
            token["text"] = c
            i += 1

        tokens.append(token)
        curr_visual_idx += 1

    return tokens

def build_orthographic_word_list(text: str) -> list:
    """Returns list of top-level orthographic word strings extracted from text."""
    tokens = build_orthographic_token_spans(text)
    return [t["text"] for t in tokens if t["is_word"]]

def split_camel_case(s: str) -> list:
    """Split camelCase, PascalCase, or uppercase acronym boundaries into constituent words."""
    if not s:
        return []
    if re.match(r'^[A-ZА-ЯÄÖÜ]{2,}(?:[\'’‘`´ʼ]s|s)?$', s):
        return [s]
    parts = re.findall(r'[A-ZА-ЯÄÖÜ0-9]+(?=[A-ZА-ЯÄÖÜ][a-zа-яäöüß])|[A-ZА-ЯÄÖÜ]?[a-zа-яäöüß\'’‘`´ʼ]+[0-9]*|[A-ZА-ЯÄÖÜ0-9]+', s)
    return [p for p in parts if p]

def decompose_identifier(s: str) -> list:
    """
    Decompose composite identifiers across snake_case (_), kebab-case (-), dot (.),
    syntax delimiters/brackets, slashes/symbols, and camelCase boundaries.
    """
    if not s:
        return []
    raw_parts = re.split(r'[_.\-/:#@\\()\[\]{}"\'<>]+', s)
    result = []
    for raw in raw_parts:
        if not raw:
            continue
        camel_parts = split_camel_case(raw)
        if len(camel_parts) > 1:
            result.append(raw)
            result.extend(camel_parts)
        else:
            contraction_parts = split_informal_contraction(raw)
            if len(contraction_parts) > 1:
                result.append(raw)
                result.extend(contraction_parts)
            else:
                result.append(raw)
    return result

def extract_identifier_subtokens(s: str) -> list:
    """
    Extract constituent subtokens from composite identifiers strictly preserving left-to-right appearance order.
    """
    if not s:
        return []
    raw_parts = re.split(r'[_.\-/:#@\\()\[\]{}"\'<>]+', s)
    result = []
    for raw in raw_parts:
        if not raw:
            continue
        camel_parts = split_camel_case(raw)
        if len(camel_parts) > 1:
            result.extend(camel_parts)
        else:
            contraction_parts = split_informal_contraction(raw)
            if contraction_parts:
                result.extend(contraction_parts)
            else:
                result.append(raw)
    return result


