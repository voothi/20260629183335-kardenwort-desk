"""
kardenwort_grammar.py
Canonical German grammar metadata and tooltip resolution contracts.
Provides normative dictionaries, suffix matching, POS mappings, and JSON-serializable payloads
for unified server-side and client-side table cell tooltip rendering.
"""

from typing import Optional, Dict, Any, Set, Tuple

# Normative part-of-speech tag mappings to human-readable labels
POS_FULL_NAME_MAP: Dict[str, str] = {
    "n.": "Noun",
    "n": "Noun",
    "NOUN": "Noun",
    "PROPN": "Noun",
    "noun": "Noun",
    "v.": "Verb",
    "v": "Verb",
    "VERB": "Verb",
    "AUX": "Verb",
    "verb": "Verb",
    "adj.": "Adjective",
    "adj": "Adjective",
    "ADJ": "Adjective",
    "adjective": "Adjective",
    "adv.": "Adverb",
    "adv": "Adverb",
    "ADV": "Adverb",
    "adverb": "Adverb",
    "prep.": "Preposition",
    "prep": "Preposition",
    "ADP": "Preposition",
    "PREP": "Preposition",
    "preposition": "Preposition",
    "art.": "Article",
    "art": "Article",
    "DET": "Article",
    "ART": "Article",
    "article": "Article",
    "pron.": "Pronoun",
    "pron": "Pronoun",
    "PRON": "Pronoun",
    "pronoun": "Pronoun",
    "conj.": "Conjunction",
    "conj": "Conjunction",
    "CCONJ": "Conjunction",
    "SCONJ": "Conjunction",
    "CONJ": "Conjunction",
    "conjunction": "Conjunction",
    "num.": "Numeral",
    "num": "Numeral",
    "NUM": "Numeral",
    "numeral": "Numeral",
    "part.": "Particle",
    "part": "Particle",
    "PART": "Particle",
    "particle": "Particle",
    "intj.": "Interjection",
    "intj": "Interjection",
    "INTJ": "Interjection",
    "interjection": "Interjection",
}

# Normative gender code / label mappings to canonical single-character codes
GENDER_CODE_MAP: Dict[str, str] = {
    "m": "m",
    "masc": "m",
    "masculine": "m",
    "der": "m",
    "f": "f",
    "fem": "f",
    "feminine": "f",
    "die": "f",
    "n": "n",
    "neut": "n",
    "neuter": "n",
    "das": "n",
}


def normalize_gender_code(val: Optional[str]) -> Optional[str]:
    """
    Normalizes gender strings and aliases (m, masc, masculine, der -> m;
    f, fem, feminine, die -> f; n, neut, neuter, das -> n) to canonical single-character
    gender codes ('m', 'f', 'n') or None if unrecognized.
    """
    if val is None:
        return None
    cleaned = str(val).strip().rstrip('.').lower()
    return GENDER_CODE_MAP.get(cleaned)


# Normative gender code / label mappings to human-readable names
GENDER_FULL_NAME_MAP: Dict[str, str] = {
    "m": "Masculine",
    "masc": "Masculine",
    "masculine": "Masculine",
    "der": "Masculine",
    "f": "Feminine",
    "fem": "Feminine",
    "feminine": "Feminine",
    "die": "Feminine",
    "n": "Neuter",
    "neut": "Neuter",
    "neuter": "Neuter",
    "das": "Neuter",
}

# Normative German definite/indefinite/negative articles
GERMAN_ARTICLES: Tuple[str, ...] = (
    "der", "die", "das",
    "ein", "eine", "einer", "eines", "einem", "einen",
    "des", "dem", "den",
    "kein", "keine", "keiner", "keines", "keinem", "keinen",
)

# Normative German personal/possessive/reflexive pronouns
GERMAN_PRONOUNS: Tuple[str, ...] = (
    "er", "sie", "es", "wir", "ihr", "du", "ich",
    "mich", "dich", "ihn", "ihm", "ihnen", "uns", "euch",
    "mir", "dir", "sich", "mein", "dein", "sein", "unser", "euer",
)

# POS tags identifying nominal headwords
GERMAN_NOUN_TAGS: Set[str] = {"n", "noun", "propn", "subst"}

# POS tags identifying non-nominal headwords that suppress article prepending
GERMAN_NON_NOUN_TAGS: Set[str] = {
    "art", "det", "pron", "v", "verb", "aux",
    "adv", "prep", "adp", "conj", "cconj", "sconj",
    "intj", "num", "part", "adj",
}

# German gender derivational and compound noun suffixes for lightweight morphological derivation:
# Suffix lists sorted by length descending so longer suffixes match first
GERMAN_GENDER_SUFFIXES_MASCULINE: Tuple[str, ...] = (
    "partner", "ismus", "ling", "mann", "ant", "ent", "ist", "or",
)
GERMAN_GENDER_SUFFIXES_FEMININE: Tuple[str, ...] = (
    "schaft", "heit", "keit", "ung", "ion", "tät", "enz", "anz", "ik", "ei", "in",
)
GERMAN_GENDER_SUFFIXES_NEUTER: Tuple[str, ...] = (
    "chen", "lein", "ment", "tum", "um",
)

# Common words ending in -in that are NOT feminine derived agent nouns
GERMAN_SUFFIX_EXCLUDED_IN: Set[str] = {
    "kinn", "sinn", "benzin", "ruin", "urin", "rubin", "cousin", "magazin", "berlin", "dublin",
}

# Common non-derived nominal stems ending in -ent that are NOT derived agent nouns
GERMAN_SUFFIX_EXCLUDED_ENT: Set[str] = {
    "moment", "element", "dokument", "talent", "argument", "patent", "zement",
}

_SUFFIX_MIN_LENGTHS: Dict[str, int] = {
    "partner": 7, "ismus": 6, "schaft": 6,
    "mann": 4, "or": 4, "ik": 4, "ei": 4, "tum": 4, "um": 4,
}
_GENDER_SUFFIX_TABLE: Dict[str, Tuple[str, ...]] = {
    "m": GERMAN_GENDER_SUFFIXES_MASCULINE,
    "f": GERMAN_GENDER_SUFFIXES_FEMININE,
    "n": GERMAN_GENDER_SUFFIXES_NEUTER,
}
_GERMAN_ARTICLE_PREFIXES: Tuple[str, ...] = tuple(a + " " for a in GERMAN_ARTICLES)
_GERMAN_ARTICLE_BY_GENDER: Dict[str, str] = {"m": "der", "f": "die", "n": "das"}


def _is_excluded_suffix(word: str, suffix: str) -> bool:
    if suffix == "in":
        return word in GERMAN_SUFFIX_EXCLUDED_IN
    if suffix == "ent":
        return word.endswith(tuple(GERMAN_SUFFIX_EXCLUDED_ENT))
    return False


def _matches_gender_suffix(word: str, suffix: str) -> bool:
    if not word.endswith(suffix) or _is_excluded_suffix(word, suffix):
        return False
    return len(word) >= _SUFFIX_MIN_LENGTHS.get(suffix, 5)


def detect_german_gender_suffix(lemma: Optional[str], gender_code: Optional[str]) -> Optional[str]:
    """
    Detects recognizable German derivational or compound gender suffixes.
    Returns format '-suffix' or None if no recognized suffix applies.
    """
    w = str(lemma or "").strip().lower()
    code = normalize_gender_code(gender_code) if w else None
    for suf in _GENDER_SUFFIX_TABLE.get(code or "", ()):
        if _matches_gender_suffix(w, suf):
            return f"-{suf}"
    return None


def is_ai_provenance(provenance: Optional[str]) -> bool:
    """
    Checks if cell provenance denotes AI/IntelliFiller generation.
    """
    if not provenance:
        return False
    p_str = str(provenance).strip().lower()
    return "intellifiller" in p_str or p_str in ("ai", "live:ai")


def _pos_is_nominal(pos: str) -> bool:
    parts = {p.strip().rstrip('.').lower() for p in pos.split(',') if p.strip()}
    return bool(parts & GERMAN_NOUN_TAGS) or not bool(parts & GERMAN_NON_NOUN_TAGS)


def _allows_german_article(clean_lemma: str, pos: Optional[str]) -> bool:
    lower_lemma = clean_lemma.lower()
    if lower_lemma in GERMAN_ARTICLES or lower_lemma.startswith(_GERMAN_ARTICLE_PREFIXES):
        return False
    if pos:
        return _pos_is_nominal(str(pos))
    return not clean_lemma[0].islower() and lower_lemma not in GERMAN_PRONOUNS


def format_lemma_article_tooltip(
    lemma: Optional[str],
    gender: Optional[str] = None,
    lang: str = "de",
    pos: Optional[str] = None,
) -> str:
    """
    Formats hover tooltip for table lemma cell.
    Prepends definite German article (der, die, das) if language is German and gender is recognized.
    """
    clean_lemma = str(lemma or "").strip()
    if not clean_lemma or str(lang or "de").strip().lower() != "de":
        return clean_lemma
    if not _allows_german_article(clean_lemma, pos):
        return clean_lemma
    article = _GERMAN_ARTICLE_BY_GENDER.get(normalize_gender_code(gender))
    return f"{article} {clean_lemma}" if article else clean_lemma


def _resolve_single_pos_tag(tag: str) -> str:
    for candidate in (tag, tag.upper(), tag.lower(), tag.rstrip('.').lower()):
        if candidate in POS_FULL_NAME_MAP:
            return POS_FULL_NAME_MAP[candidate]
    return tag


def format_pos_tooltip(pos_val: Optional[str]) -> str:
    """
    Expands abbreviated POS tags (e.g. 'n.' -> 'Noun', 'VERB' -> 'Verb') into readable title strings.
    """
    p_clean = str(pos_val or "").strip()
    if not p_clean:
        return ""
    if "," in p_clean:
        return ", ".join(_resolve_single_pos_tag(p.strip()) for p in p_clean.split(',') if p.strip())
    return _resolve_single_pos_tag(p_clean)


def format_gender_tooltip(
    gender_val: Optional[str],
    lemma: Optional[str] = None,
    provenance: Optional[str] = None,
) -> str:
    """
    Formats unabbreviated grammatical gender name with optional AI source and suffix derivation annotation.
    """
    g_code = normalize_gender_code(gender_val)
    if not g_code:
        return ""
    gender_name = {"m": "Masculine", "f": "Feminine", "n": "Neuter"}[g_code]

    ai_flag = is_ai_provenance(provenance)
    suffix = detect_german_gender_suffix(lemma, g_code)

    if ai_flag and suffix:
        return f"{gender_name} (AI: IntelliFiller · Suffix: {suffix})"
    elif suffix:
        return f"{gender_name} (Suffix: {suffix})"
    elif ai_flag:
        return f"{gender_name} (AI: IntelliFiller)"
    return gender_name


def format_gender_badge(gender_val: Optional[str]) -> str:
    """
    Renders styled HTML badge for grammatical gender (e.g. m, f, n).
    """
    g = normalize_gender_code(gender_val)
    if g == "m":
        return '<span class="kw-gender kw-gender-m">m</span>'
    elif g == "f":
        return '<span class="kw-gender kw-gender-f">f</span>'
    elif g == "n":
        return '<span class="kw-gender kw-gender-n">n</span>'
    return ""


def get_grammar_payload() -> Dict[str, Any]:
    """
    Returns a clean, JSON-serializable dictionary containing canonical German grammar contracts
    for client injection via window.__CONFIG__.grammar.
    """
    return {
        "pos_full_name_map": dict(POS_FULL_NAME_MAP),
        "gender_full_name_map": dict(GENDER_FULL_NAME_MAP),
        "german_articles": list(GERMAN_ARTICLES),
        "german_pronouns": list(GERMAN_PRONOUNS),
        "noun_tags": sorted(list(GERMAN_NOUN_TAGS)),
        "non_noun_tags": sorted(list(GERMAN_NON_NOUN_TAGS)),
        "suffixes_masculine": list(GERMAN_GENDER_SUFFIXES_MASCULINE),
        "suffixes_feminine": list(GERMAN_GENDER_SUFFIXES_FEMININE),
        "suffixes_neuter": list(GERMAN_GENDER_SUFFIXES_NEUTER),
        "suffix_excluded_in": sorted(list(GERMAN_SUFFIX_EXCLUDED_IN)),
        "suffix_excluded_ent": sorted(list(GERMAN_SUFFIX_EXCLUDED_ENT)),
    }
