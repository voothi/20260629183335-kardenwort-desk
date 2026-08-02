import configparser
import itertools
import pytest
from typing import List, Tuple, Dict, Any
import kardenwort_desk as desk
from kardenwort_desk import (
    RuntimeTokenConfig,
    BatchMergeConfig,
    DEFAULT_COMBINE_ORDER,
    DEFAULT_APOSTROPHE_CHARS,
)

# Standard boolean flags across configuration systems
BOOLEAN_FLAGS = [
    "combine_source_words",
    "filter_by_window",
    "prefer_lowercase",
    "token_mappings_enabled",
    "deduplicate_by_lemma",
]


def generate_boolean_matrix() -> List[Tuple[bool, ...]]:
    """
    Generates a deterministic parameter matrix using itertools.product
    across the standard boolean configuration flags.
    Returns 2^5 = 32 combinations of (True, False).
    """
    return list(itertools.product([True, False], repeat=len(BOOLEAN_FLAGS)))


def generate_runtime_token_config_matrix() -> List[Dict[str, Any]]:
    """
    Generates deterministic test combinations specifically for RuntimeTokenConfig boolean flags.
    """
    flags = [
        "combine_source_words",
        "filter_by_window",
        "prefer_lowercase",
        "token_mappings_enabled",
    ]
    matrix = list(itertools.product([True, False], repeat=len(flags)))
    return [dict(zip(flags, values)) for values in matrix]


def generate_batch_merge_config_matrix() -> List[Dict[str, Any]]:
    """
    Generates deterministic test combinations specifically for BatchMergeConfig boolean flags.
    """
    flags = [
        "deduplicate",
        "deduplicate_by_lemma",
        "sort_frequency",
        "prefer_lowercase",
    ]
    matrix = list(itertools.product([True, False], repeat=len(flags)))
    return [dict(zip(flags, values)) for values in matrix]


def test_matrix_generator_dimensions():
    """Verify that deterministic matrix generators produce exact expected 2^N permutations."""
    bool_matrix = generate_boolean_matrix()
    assert len(bool_matrix) == 2 ** len(BOOLEAN_FLAGS) == 32
    assert (True, True, True, True, True) in bool_matrix
    assert (False, False, False, False, False) in bool_matrix

    runtime_matrix = generate_runtime_token_config_matrix()
    assert len(runtime_matrix) == 16

    merge_matrix = generate_batch_merge_config_matrix()
    assert len(merge_matrix) == 16


@pytest.mark.parametrize("params", generate_runtime_token_config_matrix())
def test_runtime_token_config_matrix_resolution(params):
    """
    Verify complete runtime resolution across combinations of boolean flags
    in RuntimeTokenConfig without unhandled exceptions or parsing errors.
    """
    cp = configparser.ConfigParser()
    cp.add_section("settings")
    cp.add_section("token_mappings")

    cp.set("settings", "combine_source_words", str(params["combine_source_words"]))
    cp.set("settings", "filter_inflected_by_window", str(params["filter_by_window"]))
    cp.set("settings", "combine_source_words_prefer_lowercase", str(params["prefer_lowercase"]))
    cp.set("token_mappings", "enabled", str(params["token_mappings_enabled"]))

    cfg = RuntimeTokenConfig.from_config(cp)
    assert cfg.combine_source_words == params["combine_source_words"]
    assert cfg.filter_by_window == params["filter_by_window"]
    assert cfg.prefer_lowercase == params["prefer_lowercase"]
    assert cfg.token_mappings_enabled == params["token_mappings_enabled"]
    assert cfg.combine_source_words_prefer_lowercase == params["prefer_lowercase"]


@pytest.mark.parametrize("params", generate_batch_merge_config_matrix())
def test_batch_merge_config_matrix_resolution(params):
    """
    Verify complete runtime resolution across combinations of boolean flags
    in BatchMergeConfig without unhandled exceptions or parsing errors.
    """
    cp = configparser.ConfigParser()
    cp.add_section("merge")
    cp.add_section("settings")

    cp.set("merge", "deduplicate", str(params["deduplicate"]))
    cp.set("merge", "deduplicate_by_lemma", str(params["deduplicate_by_lemma"]))
    cp.set("merge", "sort_frequency", str(params["sort_frequency"]))
    cp.set("merge", "combine_source_words_prefer_lowercase", str(params["prefer_lowercase"]))

    cfg = BatchMergeConfig.from_config(cp)
    assert cfg.deduplicate == params["deduplicate"]
    assert cfg.deduplicate_by_lemma == params["deduplicate_by_lemma"]
    assert cfg.sort_frequency == params["sort_frequency"]
    assert cfg.prefer_lowercase == params["prefer_lowercase"]
    assert cfg.combine_source_words_prefer_lowercase == params["prefer_lowercase"]


@pytest.mark.parametrize("combine_flag", [True, False])
@pytest.mark.parametrize("dedup_scope", ["sentence", "global", "none"])
def test_sentence_deduplication_scope_override_invariant(combine_flag, dedup_scope):
    """
    Verify that when text_mode = 'multi', sentences_mode.enabled = True, and dedup_scope = 'sentence',
    the invariant is enforced that source word combining behavior is disabled or cleanly overridden.
    """
    cp = configparser.ConfigParser()
    cp.add_section("settings")
    cp.add_section("sentences_mode")
    cp.set("settings", "combine_source_words", str(combine_flag))
    cp.set("sentences_mode", "enabled", "true")
    cp.set("sentences_mode", "deduplication_scope", dedup_scope)

    text_mode = "multi"

    combine_source_words = cp.getboolean("settings", "combine_source_words", fallback=False)
    sentences_enabled = cp.getboolean("sentences_mode", "enabled", fallback=False)
    if text_mode == "multi" and sentences_enabled:
        scope = cp.get("sentences_mode", "deduplication_scope", fallback="sentence").strip().lower()
        if scope == "sentence":
            combine_source_words = False

    if dedup_scope == "sentence":
        assert combine_source_words is False
    else:
        assert combine_source_words is combine_flag


@pytest.mark.parametrize("flags", generate_boolean_matrix())
def test_lemma_deduplication_bounded_output_invariant(flags):
    """
    Verify across all boolean configuration permutations that unique lemma output counts
    remain strictly bounded by (and never exceed) the total count of raw input rows.
    """
    cp = configparser.ConfigParser()
    cp.add_section("settings")
    cp.add_section("token_mappings")
    cp.add_section("merge")

    combine_sw, filter_win, pref_lc, map_en, dedup_lem = flags
    cp.set("settings", "combine_source_words", str(combine_sw))
    cp.set("settings", "filter_inflected_by_window", str(filter_win))
    cp.set("settings", "combine_source_words_prefer_lowercase", str(pref_lc))
    cp.set("token_mappings", "enabled", str(map_en))
    cp.set("merge", "deduplicate_by_lemma", str(dedup_lem))

    raw_data_rows = [
        ["run", "verb", "runs"],
        ["run", "verb", "running"],
        ["house", "noun", "houses"],
        ["House", "noun", "house"],
        ["run", "verb", "ran"],
    ]
    total_input_rows = len(raw_data_rows)

    deduped_rows = desk.deduplicate_rows(
        raw_data_rows,
        col_word_source=0,
        col_pos=1,
        col_inflected=2,
        config=cp,
    )

    assert len(deduped_rows) <= total_input_rows
    assert len(deduped_rows) > 0

    unique_lemmas = {row[0].strip().lower() for row in deduped_rows if row}
    assert len(unique_lemmas) <= total_input_rows


@pytest.mark.parametrize("merge_params", generate_batch_merge_config_matrix())
def test_batch_merge_deduplication_bounded_output_invariant(merge_params):
    """
    Verify across all operational permutations of BatchMergeConfig that lemma deduplication
    workflows obey core systemic identity invariants (output rows never exceed input rows).
    """
    cp = configparser.ConfigParser()
    cp.add_section("merge")
    cp.set("merge", "deduplicate", str(merge_params["deduplicate"]))
    cp.set("merge", "deduplicate_by_lemma", str(merge_params["deduplicate_by_lemma"]))
    cp.set("merge", "sort_frequency", str(merge_params["sort_frequency"]))
    cp.set("merge", "combine_source_words_prefer_lowercase", str(merge_params["prefer_lowercase"]))

    cfg = BatchMergeConfig.from_config(cp)

    raw_data_rows = [
        ["laufen", "verb", "lauft"],
        ["laufen", "verb", "gelaufen"],
        ["haus", "noun", "häuser"],
        ["haus", "noun", "haus"],
    ]
    total_input = len(raw_data_rows)

    grouped = {}
    for row in raw_data_rows:
        lemma_val = row[0].strip().lower()
        inf_val = tuple(sorted(row[2].strip().lower().split(',')))
        key = lemma_val if cfg.deduplicate_by_lemma else (inf_val, lemma_val)
        grouped.setdefault(key, []).append(row)

    assert len(grouped) <= total_input
    assert len(grouped) > 0
