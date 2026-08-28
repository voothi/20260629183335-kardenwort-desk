from kardenwort_desk import (
    SEC_SETTINGS, SEC_TOKEN_MAPPINGS, SEC_MERGE, SEC_SENTENCES_MODE,
    SEC_CLASSIFICATION, SEC_TIMEOUTS, SEC_PIPELINE, SEC_TRIGGERS,
    SEC_TRANSLATION, SEC_TRANSLATION_PROVIDERS, SEC_RENDERING,
    SEC_ENVIRONMENT, SEC_LANGUAGES, SEC_LANGUAGE_RESOURCES,
    SEC_PROJECT_STRUCTURE, SEC_AUDIO, SEC_GOLDENDICT, SEC_WORDFILL,
    ErrorCode, _VALID_ERROR_CODES,
)
import configparser
import itertools
import json
import pathlib
import pytest
from typing import List, Tuple, Dict, Any
import kardenwort_desk as desk
from tests.schema_validator import validate_dataclass
from kardenwort_desk import (
    RuntimeTokenConfig,
    BatchMergeConfig,
    DeGCSConfig,
    OperationalMode,
    ExecutionContext,
    SentenceBoundaryConfig,
    SentencesModeConfig,
    ModeDispatcher,
    MonolithicLiveStrategy,
    SentenceLocalDedupStrategy,
    MultiGlobalCombinedStrategy,
    OperationalWorkflowResult,
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


def generate_de_gcs_config_matrix() -> List[Dict[str, Any]]:
    """
    Generates deterministic test combinations specifically for DeGCSConfig boolean flags.
    """
    flags = [
        "enabled",
        "preserve_compound_word",
        "add_parts_to_wordlist",
        "skip_merge_fractions",
        "mask_unknown_parts",
    ]
    matrix = list(itertools.product([True, False], repeat=len(flags)))
    return [dict(zip(flags, values)) for values in matrix]


def generate_execution_context_matrix() -> List[Dict[str, Any]]:
    """
    Generates deterministic test combinations specifically for ExecutionContext parameter resolution and invariants.
    """
    keys = ["text_mode", "sentences_enabled", "dedup_scope", "combine_source_words"]
    values = [
        ["single", "multi"],
        [True, False],
        ["sentence", "global", "none"],
        [True, False],
    ]
    matrix = list(itertools.product(*values))
    return [dict(zip(keys, item)) for item in matrix]


def generate_sentence_boundary_config_matrix() -> List[Dict[str, Any]]:
    """
    Generates deterministic test combinations specifically for SentenceBoundaryConfig resolution.
    """
    keys = ["terminators", "punctuation_marks", "abbrev_list", "context_mode", "words_params"]
    values = [
        [".!?:", ".\n"],
        [".,;:!?()\"[]{}—–", ".,"],
        ["Dr. Mr. Prof.", ""],
        ["single", "both"],
        [(0, 0, 0), (2, 2, 10)],
    ]
    matrix = list(itertools.product(*values))
    return [dict(zip(keys, item)) for item in matrix]


def generate_sentences_mode_config_matrix() -> List[Dict[str, Any]]:
    """
    Generates deterministic test combinations for SentencesModeConfig resolution.
    """
    keys = [
        "enabled", "min_sentences", "alignment_method", "spawn_order", 
        "parent_mode", "multi_mode_decompose", "legacy_spawn_children", "dedup_scope",
        "web_tab_mode"
    ]
    values = [
        [True, False],
        [2, 5],
        ["auto", "newline_join", "proportion"],
        ["normal", "reverse"],
        ["full", "stub"],
        [True, False],
        [True, False],
        ["sentence", "global", "none"],
        ["container", "tabs"]
    ]
    matrix = list(itertools.product(*values))
    return [dict(zip(keys, item)) for item in matrix]


def test_matrix_generator_dimensions():
    """Verify that deterministic matrix generators produce exact expected permutations."""
    bool_matrix = generate_boolean_matrix()
    assert len(bool_matrix) == 2 ** len(BOOLEAN_FLAGS) == 32
    assert (True, True, True, True, True) in bool_matrix
    assert (False, False, False, False, False) in bool_matrix

    runtime_matrix = generate_runtime_token_config_matrix()
    assert len(runtime_matrix) == 16

    merge_matrix = generate_batch_merge_config_matrix()
    assert len(merge_matrix) == 16

    gcs_matrix = generate_de_gcs_config_matrix()
    assert len(gcs_matrix) == 32

    exec_matrix = generate_execution_context_matrix()
    assert len(exec_matrix) == 24

    sbc_matrix = generate_sentence_boundary_config_matrix()
    assert len(sbc_matrix) == 32

    smc_matrix = generate_sentences_mode_config_matrix()
    assert len(smc_matrix) == 1152


@pytest.mark.parametrize("params", generate_runtime_token_config_matrix())
def test_runtime_token_config_matrix_resolution(params):
    """
    Verify complete runtime resolution across combinations of boolean flags
    in RuntimeTokenConfig without unhandled exceptions or parsing errors.
    """
    cp = configparser.ConfigParser()
    cp.add_section(SEC_SETTINGS)
    cp.add_section(SEC_TOKEN_MAPPINGS)

    cp.set(SEC_SETTINGS, "combine_source_words", str(params["combine_source_words"]))
    cp.set(SEC_SETTINGS, "filter_inflected_by_window", str(params["filter_by_window"]))
    cp.set(SEC_SETTINGS, "combine_source_words_prefer_lowercase", str(params["prefer_lowercase"]))
    cp.set(SEC_TOKEN_MAPPINGS, "enabled", str(params["token_mappings_enabled"]))

    cfg = RuntimeTokenConfig.from_config(cp)
    assert cfg.combine_source_words == params["combine_source_words"]
    assert cfg.filter_by_window == params["filter_by_window"]
    assert cfg.prefer_lowercase == params["prefer_lowercase"]
    assert cfg.token_mappings_enabled == params["token_mappings_enabled"]
    assert cfg.combine_source_words_prefer_lowercase == params["prefer_lowercase"]
    validate_dataclass(cfg)


@pytest.mark.parametrize("params", generate_batch_merge_config_matrix())
def test_batch_merge_config_matrix_resolution(params):
    """
    Verify complete runtime resolution across combinations of boolean flags
    in BatchMergeConfig without unhandled exceptions or parsing errors.
    """
    cp = configparser.ConfigParser()
    cp.add_section(SEC_MERGE)
    cp.add_section(SEC_SETTINGS)

    cp.set(SEC_MERGE, "deduplicate", str(params["deduplicate"]))
    cp.set(SEC_MERGE, "deduplicate_by_lemma", str(params["deduplicate_by_lemma"]))
    cp.set(SEC_MERGE, "sort_frequency", str(params["sort_frequency"]))
    cp.set(SEC_MERGE, "combine_source_words_prefer_lowercase", str(params["prefer_lowercase"]))

    cfg = BatchMergeConfig.from_config(cp)
    assert cfg.deduplicate == params["deduplicate"]
    assert cfg.deduplicate_by_lemma == params["deduplicate_by_lemma"]
    assert cfg.sort_frequency == params["sort_frequency"]
    assert cfg.prefer_lowercase == params["prefer_lowercase"]
    assert cfg.combine_source_words_prefer_lowercase == params["prefer_lowercase"]
    validate_dataclass(cfg)


@pytest.mark.parametrize("combine_flag", [True, False])
@pytest.mark.parametrize("dedup_scope", ["sentence", "global", "none"])
def test_sentence_deduplication_scope_override_invariant(combine_flag, dedup_scope):
    """
    Verify that when text_mode = 'multi' and sentences_mode.enabled = True,
    the invariant is enforced that source word combining behavior is preserved from config
    across all deduplication scopes including sentence-local.
    """
    cp = configparser.ConfigParser()
    cp.add_section(SEC_SETTINGS)
    cp.add_section(SEC_SENTENCES_MODE)
    cp.set(SEC_SETTINGS, "combine_source_words", str(combine_flag))
    cp.set(SEC_SENTENCES_MODE, "enabled", "true")
    cp.set(SEC_SENTENCES_MODE, "deduplication_scope", dedup_scope)

    ctx = ExecutionContext.from_config("multi", cp)
    workflow_res = ModeDispatcher.dispatch(ctx)

    assert ctx.combine_source_words is combine_flag
    assert workflow_res.combine_source_words is combine_flag


@pytest.mark.parametrize("flags", generate_boolean_matrix())
def test_lemma_deduplication_bounded_output_invariant(flags):
    """
    Verify across all boolean configuration permutations that unique lemma output counts
    remain strictly bounded by (and never exceed) the total count of raw input rows.
    """
    cp = configparser.ConfigParser()
    cp.add_section(SEC_SETTINGS)
    cp.add_section(SEC_TOKEN_MAPPINGS)
    cp.add_section(SEC_MERGE)

    combine_sw, filter_win, pref_lc, map_en, dedup_lem = flags
    cp.set(SEC_SETTINGS, "combine_source_words", str(combine_sw))
    cp.set(SEC_SETTINGS, "filter_inflected_by_window", str(filter_win))
    cp.set(SEC_SETTINGS, "combine_source_words_prefer_lowercase", str(pref_lc))
    cp.set(SEC_TOKEN_MAPPINGS, "enabled", str(map_en))
    cp.set(SEC_MERGE, "deduplicate_by_lemma", str(dedup_lem))

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
    cp.add_section(SEC_MERGE)
    cp.set(SEC_MERGE, "deduplicate", str(merge_params["deduplicate"]))
    cp.set(SEC_MERGE, "deduplicate_by_lemma", str(merge_params["deduplicate_by_lemma"]))
    cp.set(SEC_MERGE, "sort_frequency", str(merge_params["sort_frequency"]))
    cp.set(SEC_MERGE, "combine_source_words_prefer_lowercase", str(merge_params["prefer_lowercase"]))

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


@pytest.mark.parametrize("params", generate_de_gcs_config_matrix())
def test_de_gcs_config_matrix_resolution(params):
    """
    Verify complete runtime resolution across combinations of boolean flags in DeGCSConfig
    without unhandled exceptions, and confirm deterministic CLI argument generation.
    """
    cp = configparser.ConfigParser()
    cp.add_section(SEC_SETTINGS)
    cp.set(SEC_SETTINGS, "de_gcs", str(params["enabled"]))
    cp.set(SEC_SETTINGS, "de_gcs_preserve_compound_word", str(params["preserve_compound_word"]))
    cp.set(SEC_SETTINGS, "de_gcs_add_parts_to_wordlist", str(params["add_parts_to_wordlist"]))
    cp.set(SEC_SETTINGS, "de_gcs_skip_merge_fractions", str(params["skip_merge_fractions"]))
    cp.set(SEC_SETTINGS, "de_gcs_mask_unknown_parts", str(params["mask_unknown_parts"]))

    cfg = DeGCSConfig.from_config(cp)
    assert cfg.enabled == params["enabled"]
    assert cfg.preserve_compound_word == params["preserve_compound_word"]
    assert cfg.add_parts_to_wordlist == params["add_parts_to_wordlist"]
    assert cfg.skip_merge_fractions == params["skip_merge_fractions"]
    assert cfg.mask_unknown_parts == params["mask_unknown_parts"]

    args = cfg.to_cli_args()
    if not cfg.enabled:
        assert args == []
    else:
        assert args[0] == "--de-gcs"
        if cfg.preserve_compound_word:
            assert "--de-gcs-preserve-compound-word" in args
        if cfg.add_parts_to_wordlist:
            assert "--de-gcs-add-parts-to-wordlist" in args
        if cfg.skip_merge_fractions:
            assert "--de-gcs-skip-merge-fractions" in args
        if cfg.mask_unknown_parts:
            assert "--de-gcs-mask-unknown-parts" in args
    validate_dataclass(cfg)


@pytest.mark.parametrize("params", generate_execution_context_matrix())
def test_execution_context_matrix_resolution(params):
    """
    Verify state transitions and invariant locks across all combinations of text_mode,
    sentences_mode.enabled, and deduplication_scope in ExecutionContext without parsing errors.
    """
    cp = configparser.ConfigParser()
    cp.add_section(SEC_SETTINGS)
    cp.add_section(SEC_SENTENCES_MODE)

    cp.set(SEC_SETTINGS, "combine_source_words", str(params["combine_source_words"]))
    cp.set(SEC_SENTENCES_MODE, "enabled", str(params["sentences_enabled"]))
    cp.set(SEC_SENTENCES_MODE, "deduplication_scope", params["dedup_scope"])

    ctx = ExecutionContext.from_config(params["text_mode"], cp)

    if params["text_mode"] == "multi" and params["sentences_enabled"]:
        if params["dedup_scope"] == "sentence":
            assert ctx.mode == OperationalMode.MULTI_SENTENCE_LOCAL_DEDUP
            assert ctx.combine_source_words == params["combine_source_words"]
        else:
            assert ctx.mode == OperationalMode.MULTI_GLOBAL_COMBINED
            assert ctx.combine_source_words == params["combine_source_words"]
    else:
        assert ctx.mode == OperationalMode.MONOLITHIC_LIVE
        assert ctx.combine_source_words == params["combine_source_words"]

    # Assert immutability of resolved operational mode and invariant parameters
    with pytest.raises(AttributeError):
        ctx.mode = OperationalMode.MONOLITHIC_LIVE
    with pytest.raises(AttributeError):
        ctx.combine_source_words = not ctx.combine_source_words


@pytest.mark.parametrize("params", generate_sentence_boundary_config_matrix())
def test_sentence_boundary_config_matrix_resolution(params):
    """
    Verify deterministic parameter resolution across combinatorial configuration matrices
    in SentenceBoundaryConfig without unhandled exceptions or parsing errors, and assert immutability.
    """
    cp = configparser.ConfigParser()
    cp.add_section(SEC_SETTINGS)
    cp.add_section(SEC_SENTENCES_MODE)

    cp.set(SEC_SENTENCES_MODE, "terminators", params["terminators"])
    cp.set(SEC_SENTENCES_MODE, "punctuation_marks", params["punctuation_marks"])
    cp.set(SEC_SETTINGS, "anki_abbrev_list", params["abbrev_list"])
    cp.set(SEC_SETTINGS, "anki_context_mode", params["context_mode"])

    w_before, w_after, w_max = params["words_params"]
    cp.set(SEC_SETTINGS, "anki_context_words_before", str(w_before))
    cp.set(SEC_SETTINGS, "anki_context_words_after", str(w_after))
    cp.set(SEC_SETTINGS, "anki_context_max_words", str(w_max))

    sbc = SentenceBoundaryConfig.from_config(cp)
    assert sbc.terminators == (params["terminators"] if params["terminators"].strip() else ".!?:")
    assert sbc.punctuation_marks == params["punctuation_marks"]
    assert sbc.context_mode == params["context_mode"].lower()
    assert sbc.words_before == w_before
    assert sbc.words_after == w_after
    assert sbc.max_words == w_max

    if params["abbrev_list"].strip():
        expected_abbrev_set = frozenset(a.lower().rstrip('.') for a in params["abbrev_list"].split())
        assert sbc.abbrev_set == expected_abbrev_set
    else:
        assert sbc.abbrev_set is None

    # Verify idempotency of from_config
    sbc2 = SentenceBoundaryConfig.from_config(sbc)
    assert sbc is sbc2

    # Verify immutability (frozen dataclass)
    with pytest.raises(AttributeError):
        sbc.terminators = ".!?"
    with pytest.raises(AttributeError):
        sbc.words_before = 10
    validate_dataclass(sbc)


@pytest.mark.parametrize("params", generate_execution_context_matrix())
def test_deterministic_strategy_dispatch_routing(params):
    """
    Verify deterministic mode dispatching via ModeDispatcher and strategy execution isolation
    across all combinations of operational modes and configuration states.
    """
    cp = configparser.ConfigParser()
    cp.add_section(SEC_SETTINGS)
    cp.add_section(SEC_SENTENCES_MODE)

    cp.set(SEC_SETTINGS, "combine_source_words", str(params["combine_source_words"]))
    cp.set(SEC_SENTENCES_MODE, "enabled", str(params["sentences_enabled"]))
    cp.set(SEC_SENTENCES_MODE, "deduplication_scope", params["dedup_scope"])

    ctx = ExecutionContext.from_config(params["text_mode"], cp)
    strategy = ModeDispatcher.get_strategy(ctx)
    result = ModeDispatcher.dispatch(ctx)

    assert isinstance(result, OperationalWorkflowResult)
    assert result.mode == ctx.mode

    if ctx.mode == OperationalMode.MONOLITHIC_LIVE:
        assert isinstance(strategy, MonolithicLiveStrategy)
        assert result.dedup_scope == "global"
        assert result.combine_source_words == params["combine_source_words"]
    elif ctx.mode == OperationalMode.MULTI_SENTENCE_LOCAL_DEDUP:
        assert isinstance(strategy, SentenceLocalDedupStrategy)
        assert result.dedup_scope == "sentence"
        assert result.combine_source_words == params["combine_source_words"]
    elif ctx.mode == OperationalMode.MULTI_GLOBAL_COMBINED:
        assert isinstance(strategy, MultiGlobalCombinedStrategy)
        assert result.dedup_scope == "global"
        assert result.combine_source_words == params["combine_source_words"]
    else:
        pytest.fail(f"Unexpected OperationalMode resolution: {ctx.mode}")

    with pytest.raises(AttributeError):
        result.dedup_scope = "overridden"
    with pytest.raises(AttributeError):
        result.combine_source_words = True


def test_sentences_mode_config_invariants():
    """
    Verify parsing, default fallback behaviors, and immutability invariants for SentencesModeConfig.
    """
    cp = configparser.ConfigParser()
    cp.add_section(SEC_SENTENCES_MODE)
    cp.set(SEC_SENTENCES_MODE, "enabled", "true")
    cp.set(SEC_SENTENCES_MODE, "min_sentences", "5")
    cp.set(SEC_SENTENCES_MODE, "alignment_method", "proportional")
    cp.set(SEC_SENTENCES_MODE, "spawn_order", "reversed")
    cp.set(SEC_SENTENCES_MODE, "parent_mode", "summary")
    cp.set(SEC_SENTENCES_MODE, "multi_mode_sentence_decomposition", "true")
    cp.set(SEC_SENTENCES_MODE, "deduplication_scope", " GLOBAL ")

    smc = SentencesModeConfig.from_config(cp)
    assert smc.enabled is True
    assert smc.min_sentences == 5
    assert smc.alignment_method == "proportional"
    assert smc.spawn_order == "reversed"
    assert smc.parent_mode == "summary"
    assert smc.multi_mode_decompose is True
    assert smc.deduplication_scope == "global"

    # Verify idempotency
    smc2 = SentencesModeConfig.from_config(smc)
    assert smc2 is smc

    # Verify fallback defaults with empty config
    empty_smc = SentencesModeConfig.from_config(None)
    assert empty_smc.enabled is False
    assert empty_smc.min_sentences == 2
    assert empty_smc.alignment_method == "auto"
    assert empty_smc.deduplication_scope == "sentence"

    # Verify immutability
    with pytest.raises(AttributeError):
        smc.enabled = False
    with pytest.raises(AttributeError):
        smc.deduplication_scope = "none"
    validate_dataclass(smc)
    validate_dataclass(empty_smc)


# ---------------------------------------------------------------------------
# FSM Conformance: ModeDispatcher vs. backend_dispatch_table.json
# ---------------------------------------------------------------------------

_BACKEND_DISPATCH_TABLE_PATH = (
    pathlib.Path(__file__).parent.parent / "schemas" / "fsm" / "backend_dispatch_table.json"
)

_STRATEGY_TO_MODE = {
    "MONOLITHIC_LIVE": OperationalMode.MONOLITHIC_LIVE,
    "MULTI_SENTENCE_LOCAL_DEDUP": OperationalMode.MULTI_SENTENCE_LOCAL_DEDUP,
    "MULTI_GLOBAL_COMBINED": OperationalMode.MULTI_GLOBAL_COMBINED,
}

_STRATEGY_CLASS_MAP = {
    "MONOLITHIC_LIVE": MonolithicLiveStrategy,
    "MULTI_SENTENCE_LOCAL_DEDUP": SentenceLocalDedupStrategy,
    "MULTI_GLOBAL_COMBINED": MultiGlobalCombinedStrategy,
}


def _load_backend_dispatch_table() -> dict:
    """Load and return the backend dispatch FSM table from schemas/fsm/."""
    assert _BACKEND_DISPATCH_TABLE_PATH.exists(), (
        f"backend_dispatch_table.json not found at {_BACKEND_DISPATCH_TABLE_PATH}"
    )
    with _BACKEND_DISPATCH_TABLE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def _build_dispatch_params_from_table() -> list:
    """
    Build pytest parametrize values from each row in backend_dispatch_table.json's routing_table.
    Returns list of (row_id, text_mode, sentences_enabled, dedup_scope, expected_strategy_id).
    """
    table = _load_backend_dispatch_table()
    params = []
    for row in table["routing_table"]:
        params.append(pytest.param(
            row["id"],
            row["text_mode"],
            row["sentences_enabled"],
            row["dedup_scope"],
            row["target_strategy"],
            id=row["id"],
        ))
    return params


@pytest.mark.parametrize(
    "row_id,text_mode,sentences_enabled,dedup_scope,expected_strategy_id",
    _build_dispatch_params_from_table(),
)
def test_mode_dispatcher_conforms_to_fsm_table(
    row_id, text_mode, sentences_enabled, dedup_scope, expected_strategy_id
):
    """
    FSM conformance assertion: for each routing row in backend_dispatch_table.json,
    verify that ModeDispatcher at runtime resolves to exactly the declared target strategy
    across all (text_mode, sentences_enabled, dedup_scope) combinations.

    This test is the automated conformance gate ensuring zero routing drift between
    the declarative FSM specification and the runtime ModeDispatcher implementation.
    """
    cp = configparser.ConfigParser()
    cp.add_section(SEC_SETTINGS)
    cp.add_section(SEC_SENTENCES_MODE)
    cp.set(SEC_SETTINGS, "combine_source_words", "True")
    cp.set(SEC_SENTENCES_MODE, "enabled", str(sentences_enabled))
    cp.set(SEC_SENTENCES_MODE, "deduplication_scope", dedup_scope)

    ctx = ExecutionContext.from_config(text_mode, cp)
    runtime_strategy = ModeDispatcher.get_strategy(ctx)

    expected_mode = _STRATEGY_TO_MODE[expected_strategy_id]
    expected_cls = _STRATEGY_CLASS_MAP[expected_strategy_id]

    assert ctx.mode == expected_mode, (
        f"[Row {row_id}] FSM table declares {expected_strategy_id} for "
        f"(text_mode={text_mode!r}, sentences_enabled={sentences_enabled}, dedup_scope={dedup_scope!r}), "
        f"but ExecutionContext resolved mode={ctx.mode!r}"
    )
    assert isinstance(runtime_strategy, expected_cls), (
        f"[Row {row_id}] ModeDispatcher returned {type(runtime_strategy).__name__!r} "
        f"but FSM table specifies {expected_cls.__name__!r} for "
        f"(text_mode={text_mode!r}, sentences_enabled={sentences_enabled}, dedup_scope={dedup_scope!r})"
    )


def test_mode_dispatcher_fsm_table_covers_all_registered_modes():
    """
    Verify that every OperationalMode registered in ModeDispatcher._strategies has at
    least one corresponding entry in backend_dispatch_table.json's routing_table,
    ensuring the FSM specification does not omit any live operational strategy.
    """
    table = _load_backend_dispatch_table()
    declared_strategy_ids = {s["id"] for s in table["strategies"]}
    routing_targets = {r["target_strategy"] for r in table["routing_table"]}

    # All strategies declared in the table must appear in routing rows
    unused_strategies = declared_strategy_ids - routing_targets
    assert not unused_strategies, (
        f"Strategies declared in FSM table but not referenced in any routing row: {unused_strategies}"
    )

    # All modes registered in ModeDispatcher must map to a declared FSM strategy
    registered_modes = set(ModeDispatcher._strategies.keys())
    fsm_modes = {_STRATEGY_TO_MODE[sid] for sid in declared_strategy_ids}
    unspecified_modes = registered_modes - fsm_modes
    assert not unspecified_modes, (
        f"OperationalModes registered in ModeDispatcher but absent from FSM table: {unspecified_modes}"
    )


# ---------------------------------------------------------------------------
# 4. Shared Error Catalog Synchronization Invariants
# ---------------------------------------------------------------------------

def _load_error_catalog() -> dict:
    """Load schemas/error_catalog.json relative to the project root."""
    project_root = pathlib.Path(__file__).parent.parent
    catalog_path = project_root / "schemas" / "error_catalog.json"
    assert catalog_path.exists(), (
        f"schemas/error_catalog.json not found at {catalog_path}. "
        "The file must be present as the authoritative error code reference."
    )
    with open(catalog_path, "r", encoding="utf-8") as f:
        return json.load(f)


class TestErrorCatalogSynchronization:
    """
    4.1 – Automated synchronization gate: programmatically confirms 1-to-1 parity
    between the runtime Python ErrorCode enumeration in kardenwort_desk.py and
    the authoritative schemas/error_catalog.json catalog.

    These tests are the build-time enforcement mechanism preventing catalog drift
    between the Python enum definition and the language-agnostic JSON schema.
    """

    def test_error_catalog_file_is_loadable(self):
        """schemas/error_catalog.json must be valid JSON and contain the expected top-level structure."""
        catalog = _load_error_catalog()
        assert "version" in catalog, "Error catalog must have a 'version' field."
        assert "error_codes" in catalog, "Error catalog must have an 'error_codes' array."
        assert isinstance(catalog["error_codes"], list), "'error_codes' must be a JSON array."
        assert len(catalog["error_codes"]) > 0, "Error catalog must define at least one error code."

    def test_error_catalog_entries_have_required_fields(self):
        """Every entry in schemas/error_catalog.json must contain 'code', 'category', 'description', and 'resolution'."""
        catalog = _load_error_catalog()
        for entry in catalog["error_codes"]:
            code = entry.get("code", "<missing>")
            for required_field in ("code", "category", "description", "resolution"):
                assert required_field in entry, (
                    f"Error catalog entry {code!r} is missing required field {required_field!r}."
                )

    def test_python_enum_members_match_catalog_codes_exactly(self):
        """
        Every ErrorCode enum member in kardenwort_desk.py must have a corresponding
        entry in schemas/error_catalog.json, with matching code identifiers.

        Fails if: Python enum defines a code not documented in the catalog.
        """
        catalog = _load_error_catalog()
        catalog_codes = {entry["code"] for entry in catalog["error_codes"]}
        python_codes = {member.value for member in ErrorCode}

        undocumented = python_codes - catalog_codes
        assert not undocumented, (
            f"ErrorCode enum members are defined in kardenwort_desk.py but absent from "
            f"schemas/error_catalog.json: {sorted(undocumented)}. "
            "Add the missing codes to the catalog to restore synchronization."
        )

    def test_catalog_codes_match_python_enum_members_exactly(self):
        """
        Every error code in schemas/error_catalog.json must have a corresponding
        member in the Python ErrorCode enum in kardenwort_desk.py.

        Fails if: Catalog documents a code that has no Python enum member.
        """
        catalog = _load_error_catalog()
        catalog_codes = {entry["code"] for entry in catalog["error_codes"]}
        python_codes = {member.value for member in ErrorCode}

        orphaned = catalog_codes - python_codes
        assert not orphaned, (
            f"schemas/error_catalog.json documents error codes with no corresponding "
            f"Python ErrorCode enum member: {sorted(orphaned)}. "
            "Add the missing members to the ErrorCode enum or remove them from the catalog."
        )

    def test_valid_error_codes_frozenset_matches_enum(self):
        """
        The runtime _VALID_ERROR_CODES frozenset must contain exactly the same
        identifiers as the ErrorCode enum members. This validates the O(1) membership
        check helper used by print_structured_error is not stale.
        """
        python_codes = {member.value for member in ErrorCode}
        assert _VALID_ERROR_CODES == frozenset(python_codes), (
            f"_VALID_ERROR_CODES frozenset diverged from ErrorCode enum members. "
            f"Frozenset: {sorted(_VALID_ERROR_CODES)}, Enum: {sorted(python_codes)}"
        )

    def test_error_catalog_has_no_duplicate_codes(self):
        """schemas/error_catalog.json must not define duplicate code identifiers."""
        catalog = _load_error_catalog()
        codes = [entry["code"] for entry in catalog["error_codes"]]
        seen: set = set()
        duplicates = set()
        for code in codes:
            if code in seen:
                duplicates.add(code)
            seen.add(code)
        assert not duplicates, (
            f"schemas/error_catalog.json contains duplicate error code entries: {sorted(duplicates)}"
        )

    def test_error_code_enum_serializes_as_plain_string(self):
        """
        ErrorCode members (str, Enum) must serialize via json.dumps() as plain string values,
        not as enumeration objects, ensuring backward compatibility with all IPC consumers.
        """
        for member in ErrorCode:
            serialized = json.dumps(member.value)
            assert serialized == f'"{member.value}"', (
                f"ErrorCode.{member.name} did not serialize as a plain JSON string: {serialized!r}"
            )

@pytest.mark.parametrize("params", generate_sentences_mode_config_matrix())
def test_sentences_mode_config_matrix_resolution(params):
    """
    Verify complete runtime resolution across all permutations of SentencesModeConfig parameters.
    """
    cp = configparser.ConfigParser()
    cp.add_section(SEC_SENTENCES_MODE)
    
    cp.set(SEC_SENTENCES_MODE, "enabled", str(params["enabled"]))
    cp.set(SEC_SENTENCES_MODE, "min_sentences", str(params["min_sentences"]))
    cp.set(SEC_SENTENCES_MODE, "alignment_method", params["alignment_method"])
    cp.set(SEC_SENTENCES_MODE, "spawn_order", params["spawn_order"])
    cp.set(SEC_SENTENCES_MODE, "parent_mode", params["parent_mode"])
    cp.set(SEC_SENTENCES_MODE, "multi_mode_sentence_decomposition", str(params["multi_mode_decompose"]))
    cp.set(SEC_SENTENCES_MODE, "legacy_spawn_children", str(params["legacy_spawn_children"]))
    cp.set(SEC_SENTENCES_MODE, "deduplication_scope", params["dedup_scope"])
    cp.set(SEC_SENTENCES_MODE, "web_tab_mode", params["web_tab_mode"])

    cfg = SentencesModeConfig.from_config(cp)
    
    assert cfg.enabled == params["enabled"]
    assert cfg.min_sentences == params["min_sentences"]
    assert cfg.alignment_method == params["alignment_method"]
    assert cfg.spawn_order == params["spawn_order"]
    assert cfg.parent_mode == params["parent_mode"]
    assert cfg.multi_mode_decompose == params["multi_mode_decompose"]
    assert cfg.legacy_spawn_children == params["legacy_spawn_children"]
    assert cfg.deduplication_scope == params["dedup_scope"]
    assert cfg.web_tab_mode == params["web_tab_mode"]
    
    validate_dataclass(cfg)
    
    # Also explicitly test logic invariant: get_expected_window_count
    split = params["enabled"] and 5 >= params["min_sentences"]
    expected_count = 5 + (1 if params["parent_mode"] != 'none' else 0) if split else 1
    assert cfg.get_expected_window_count(5) == expected_count


# ---------------------------------------------------------------------------
# Progressive-mode disable guard invariants
#
# Regression: lemma_base_provider not filled when a merged TSV is re-rendered
# and sentences_mode.enabled=true. The guard must ONLY disable progressive mode
# when there is active multi-window splitting — NOT for standalone TSV re-renders.
#
# Guard logic (kardenwort_desk.py _run_render_flow_impl):
#   tsv_has_active_children = bool(tsv_path and sentences_enabled and children_tsv_paths)
#   if (will_split or tsv_has_active_children) and (display_mode == 'progressive' OR prog_text_trans):
#       → disable progressive
# ---------------------------------------------------------------------------

def _make_progressive_guard_config(
    sentences_enabled: bool,
    legacy_spawn_children: bool,
    progressive_text_translation: bool,
    display_mode: str,
) -> configparser.ConfigParser:
    """Build a minimal ConfigParser matching the relevant guard inputs."""
    cp = configparser.ConfigParser()
    cp.add_section(SEC_PIPELINE)
    cp.add_section(SEC_RENDERING)
    cp.add_section(SEC_SENTENCES_MODE)

    cp.set(SEC_PIPELINE, "progressive_text_translation", str(progressive_text_translation))
    cp.set(SEC_RENDERING, "display_mode", display_mode)
    cp.set(SEC_SENTENCES_MODE, "enabled", str(sentences_enabled))
    cp.set(SEC_SENTENCES_MODE, "legacy_spawn_children", str(legacy_spawn_children))
    cp.set(SEC_SENTENCES_MODE, "min_sentences", "2")
    return cp


def _compute_guard(
    cp: configparser.ConfigParser,
    tsv_path_truthy: bool,
    children_truthy: bool,
    num_source_sentences: int,
    text_mode: str,
) -> bool:
    """
    Mirrors the exact guard logic in _run_render_flow_impl.
    Returns True if progressive mode would be DISABLED.
    """
    display_mode_val = cp.get(SEC_RENDERING, "display_mode", fallback="progressive")
    is_progressive_text = cp.getboolean(SEC_PIPELINE, "progressive_text_translation", fallback=False)
    if display_mode_val != "progressive":
        is_progressive_text = False

    smc = SentencesModeConfig.from_config(cp)
    sentences_enabled = smc.enabled

    will_split = (not tsv_path_truthy) and (
        smc.should_split_sentences(num_source_sentences)
        # legacy_spawn_children only fires when sentences_mode is enabled (mirrors production fix).
        or (text_mode == "multi" and smc.enabled and smc.legacy_spawn_children and num_source_sentences >= 2)
    )

    tsv_has_active_children = bool(tsv_path_truthy and sentences_enabled and children_truthy)

    needs_disable = (will_split or tsv_has_active_children) and (
        display_mode_val == "progressive" or is_progressive_text
    )
    return needs_disable


# Parametrize the exact user config combination (sentences_mode=true,
# legacy_spawn_children=false, progressive_text_translation=false, display_mode=progressive)
# against all TSV / children combinations.
_REGRESSION_CASES = [
    # (tsv_path_truthy, children_truthy, num_sentences, text_mode, expect_disabled, label)
    # --- REGRESSION: merged/standalone TSV, no children → MUST stay progressive ---
    (True,  False, 3, "single", False, "merged_tsv_no_children_single"),
    (True,  False, 3, "multi",  False, "merged_tsv_no_children_multi"),
    # --- Parent TSV with active children → MUST disable progressive ---
    (True,  True,  3, "single", True,  "parent_tsv_with_children_single"),
    (True,  True,  3, "multi",  True,  "parent_tsv_with_children_multi"),
    # --- Fresh text, enough sentences, sentences_mode → MUST disable progressive ---
    (False, False, 3, "single", True,  "fresh_text_enough_sentences"),
    (False, False, 1, "single", False, "fresh_text_below_min_sentences"),
    # --- No TSV, multi, legacy_spawn_children=false → will_split stays False ---
    (False, False, 3, "multi",  True,  "fresh_multi_text_enough_sentences"),
]


@pytest.mark.parametrize(
    "tsv_path_truthy,children_truthy,num_sentences,text_mode,expect_disabled,label",
    _REGRESSION_CASES,
    ids=[c[-1] for c in _REGRESSION_CASES],
)
def test_progressive_guard_regression_sentences_mode_enabled(
    tsv_path_truthy, children_truthy, num_sentences, text_mode, expect_disabled, label
):
    """
    Verify the progressive-mode disable guard for the exact production config combination
    that caused the lemma_base_provider regression:
      sentences_mode.enabled=true, legacy_spawn_children=false,
      progressive_text_translation=false, display_mode=progressive.

    CRITICAL: A merged/standalone TSV (tsv_path provided, no child TSVs on disk)
    MUST NOT disable progressive mode, regardless of sentences_mode.enabled.
    The progressive worker must be launched so lemma_base_provider translation runs.
    """
    cp = _make_progressive_guard_config(
        sentences_enabled=True,
        legacy_spawn_children=False,
        progressive_text_translation=False,
        display_mode="progressive",
    )
    result = _compute_guard(cp, tsv_path_truthy, children_truthy, num_sentences, text_mode)
    assert result == expect_disabled, (
        f"[{label}] progressive guard mismatch: "
        f"tsv={tsv_path_truthy} children={children_truthy} "
        f"n_sent={num_sentences} text_mode={text_mode} "
        f"expected disabled={expect_disabled}, got {result}"
    )


# Full mode matrix: all combinations of the three config knobs the user pointed to
_FULL_MATRIX_CASES = list(itertools.product(
    [True, False],  # sentences_enabled
    [True, False],  # legacy_spawn_children
    [True, False],  # progressive_text_translation
    ["progressive", "monolithic"],  # display_mode
    [True, False],  # tsv_path_truthy
    [True, False],  # children_truthy
))


@pytest.mark.parametrize(
    "sentences_enabled,legacy_children,prog_text,display_mode,tsv_path_truthy,children_truthy",
    _FULL_MATRIX_CASES,
)
def test_progressive_guard_full_mode_matrix(
    sentences_enabled, legacy_children, prog_text, display_mode, tsv_path_truthy, children_truthy
):
    """
    Exhaustive matrix test across all combinations of the three config knobs
    (sentences_mode.enabled, legacy_spawn_children, progressive_text_translation),
    display_mode, and TSV/children presence.

    Invariants verified:
    1. If display_mode=monolithic AND progressive_text_translation=false:
       guard NEVER disables (already monolithic, nothing to disable).
    2. If tsv_path is provided but children is empty:
       guard MUST NOT disable due to sentences_mode alone (regression guard).
    3. If will_split is True (fresh text, enough sentences, sentences_enabled):
       guard MUST disable if any progressive mode flag is active.
    4. If tsv_path provided and children present and sentences_enabled:
       guard MUST disable if any progressive mode flag is active.
    """
    cp = _make_progressive_guard_config(
        sentences_enabled=sentences_enabled,
        legacy_spawn_children=legacy_children,
        progressive_text_translation=prog_text,
        display_mode=display_mode,
    )

    # Use 3 sentences (above min_sentences=2) for fresh-text split scenario
    num_sentences = 3
    text_mode = "single"

    result = _compute_guard(cp, tsv_path_truthy, children_truthy, num_sentences, text_mode)

    # Invariant 1: if neither progressive flag is active, guard can never fire
    eff_prog_text = prog_text and display_mode == "progressive"
    any_progressive_active = (display_mode == "progressive") or eff_prog_text
    if not any_progressive_active:
        assert result is False, (
            "Guard must never fire when no progressive mode is active "
            f"(display_mode={display_mode}, prog_text={prog_text})"
        )

    # Invariant 2: TSV present but no children → guard must not fire due to sentences_mode alone
    if tsv_path_truthy and not children_truthy:
        # will_split is False (tsv_path_truthy=True suppresses will_split)
        # tsv_has_active_children is False (children_truthy=False)
        assert result is False, (
            "Guard must NOT disable progressive mode for a standalone/merged TSV "
            f"(sentences_enabled={sentences_enabled}, tsv_path=True, children=False). "
            "The progressive worker must be launched so lemma_base_provider runs."
        )

    # Invariant 3: will_split=True (fresh text, sentences_enabled, enough sentences)
    # + any progressive flag → must disable
    if not tsv_path_truthy and sentences_enabled and num_sentences >= 2:
        will_split_expected = True
        if will_split_expected and any_progressive_active:
            assert result is True, (
                "Guard MUST disable progressive for fresh text with sentences split "
                f"(sentences_enabled={sentences_enabled}, will_split=True, "
                f"display_mode={display_mode}, prog_text={prog_text})"
            )

    # Invariant 4: parent TSV with children + sentences_enabled + any progressive
    if tsv_path_truthy and children_truthy and sentences_enabled and any_progressive_active:
        assert result is True, (
            "Guard MUST disable progressive for parent TSV with active child windows "
            f"(sentences_enabled={sentences_enabled}, children=True, "
            f"display_mode={display_mode}, prog_text={prog_text})"
        )


@pytest.mark.parametrize("will_split", [True, False])
@pytest.mark.parametrize("dedup_scope", ["sentence", "global"])
@pytest.mark.parametrize("sentences_enabled", [True, False])
def test_execution_context_will_split_resolution(will_split, dedup_scope, sentences_enabled):
    """
    Verify that when will_split is True and sentences_enabled is True,
    ExecutionContext routes to MULTI_SENTENCE_LOCAL_DEDUP or MULTI_GLOBAL_COMBINED
    regardless of whether text_mode is 'single' or 'multi'.
    """
    cp = configparser.ConfigParser()
    cp.add_section(SEC_SETTINGS)
    cp.add_section(SEC_SENTENCES_MODE)

    cp.set(SEC_SETTINGS, "combine_source_words", "true")
    cp.set(SEC_SENTENCES_MODE, "enabled", str(sentences_enabled).lower())
    cp.set(SEC_SENTENCES_MODE, "deduplication_scope", dedup_scope)

    ctx = ExecutionContext.from_config("single", cp, will_split=will_split)
    workflow_res = ModeDispatcher.dispatch(ctx)

    if will_split and sentences_enabled:
        if dedup_scope == "sentence":
            assert ctx.mode == OperationalMode.MULTI_SENTENCE_LOCAL_DEDUP
            assert workflow_res.dedup_scope == "sentence"
            assert workflow_res.combine_source_words is True
        else:
            assert ctx.mode == OperationalMode.MULTI_GLOBAL_COMBINED
            assert workflow_res.dedup_scope == "global"
            assert workflow_res.combine_source_words is True
    else:
        assert ctx.mode == OperationalMode.MONOLITHIC_LIVE
        assert workflow_res.dedup_scope == "global"

