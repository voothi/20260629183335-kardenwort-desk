import itertools
import pytest
from typing import List, Tuple, Dict, Any
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
