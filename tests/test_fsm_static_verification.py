"""
Static FSM Reachability and Deadlock Verification Suite

Parses `schemas/fsm/gui_lifecycle_table.json` and `schemas/fsm/backend_dispatch_table.json`
and performs algorithmic graph reachability analysis to assert:
  - Every declared state is reachable from the initial state.
  - No state forms an unresolvable deadlock (i.e., every non-terminal state has at
    least one outgoing transition that leads to a different state).
  - Every transition references only declared states, events, and guards.
  - No orphaned events exist (every declared event is referenced in at least one transition).
  - The routing table in backend_dispatch_table.json is exhaustive over the combinatorial
    space of (text_mode x sentences_enabled x dedup_scope).
"""

import json
import pathlib
import itertools
import pytest
from collections import defaultdict, deque

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).parent.parent
_SCHEMAS_FSM = _REPO_ROOT / "schemas" / "fsm"


def _load_json(filename: str) -> dict:
    path = _SCHEMAS_FSM / filename
    assert path.exists(), f"Schema file not found: {path}"
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _build_reachability_graph(transitions: list[dict]) -> dict[str, set[str]]:
    """
    Builds an adjacency map { from_state -> {to_state, ...} } from a transition list.
    """
    graph: dict[str, set[str]] = defaultdict(set)
    for t in transitions:
        graph[t["from_state"]].add(t["to_state"])
    return graph


def _bfs_reachable(graph: dict[str, set[str]], initial: str) -> set[str]:
    """
    Returns the set of all state IDs reachable from *initial* via BFS.
    """
    visited: set[str] = set()
    queue: deque[str] = deque([initial])
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        for neighbour in graph.get(node, set()):
            if neighbour not in visited:
                queue.append(neighbour)
    return visited


# ---------------------------------------------------------------------------
# GUI Lifecycle FSM Tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def gui_fsm() -> dict:
    return _load_json("gui_lifecycle_table.json")


class TestGuiFsmStructure:
    """Structural integrity checks for gui_lifecycle_table.json."""

    def test_required_top_level_keys(self, gui_fsm):
        for key in ("states", "events", "guards", "transitions", "initial_state", "domain"):
            assert key in gui_fsm, f"Missing required top-level key: '{key}'"

    def test_initial_state_declared(self, gui_fsm):
        state_ids = {s["id"] for s in gui_fsm["states"]}
        assert gui_fsm["initial_state"] in state_ids, (
            f"initial_state '{gui_fsm['initial_state']}' not present in declared states"
        )

    def test_transition_states_declared(self, gui_fsm):
        state_ids = {s["id"] for s in gui_fsm["states"]}
        for t in gui_fsm["transitions"]:
            assert t["from_state"] in state_ids, (
                f"Transition {t['id']}: from_state '{t['from_state']}' not declared"
            )
            assert t["to_state"] in state_ids, (
                f"Transition {t['id']}: to_state '{t['to_state']}' not declared"
            )

    def test_transition_events_declared(self, gui_fsm):
        event_ids = {e["id"] for e in gui_fsm["events"]}
        for t in gui_fsm["transitions"]:
            assert t["event"] in event_ids, (
                f"Transition {t['id']}: event '{t['event']}' not declared in events list"
            )

    def test_transition_guards_declared(self, gui_fsm):
        guard_ids = {g["id"] for g in gui_fsm["guards"]}
        for t in gui_fsm["transitions"]:
            assert t["guard"] in guard_ids, (
                f"Transition {t['id']}: guard '{t['guard']}' not declared in guards list"
            )

    def test_no_orphaned_events(self, gui_fsm):
        """Every declared event must appear in at least one transition."""
        used_events = {t["event"] for t in gui_fsm["transitions"]}
        declared_events = {e["id"] for e in gui_fsm["events"]}
        orphans = declared_events - used_events
        assert not orphans, f"Orphaned events (declared but never triggered): {orphans}"

    def test_transition_ids_unique(self, gui_fsm):
        ids = [t["id"] for t in gui_fsm["transitions"]]
        assert len(ids) == len(set(ids)), "Duplicate transition IDs found in gui_lifecycle_table.json"


class TestGuiFsmReachability:
    """Graph-traversal reachability checks for gui_lifecycle_table.json."""

    def test_all_states_reachable(self, gui_fsm):
        """
        Every state must be reachable from initial_state following outgoing transitions.
        A state that is not reachable is an orphan and constitutes a specification error.
        """
        initial = gui_fsm["initial_state"]
        all_state_ids = {s["id"] for s in gui_fsm["states"]}
        graph = _build_reachability_graph(gui_fsm["transitions"])
        reachable = _bfs_reachable(graph, initial)
        unreachable = all_state_ids - reachable
        assert not unreachable, (
            f"Unreachable states detected from initial state '{initial}': {unreachable}"
        )

    def test_no_strict_deadlock_states(self, gui_fsm):
        """
        A strict deadlock is a non-initial state with zero outgoing transitions to a
        *different* state (a pure sink), from which the system cannot escape.
        IDLE is the designated terminal/rest state and is exempt from this check since
        it is the intentional neutral rest point (it has outgoing transitions anyway).
        """
        initial = gui_fsm["initial_state"]
        all_state_ids = {s["id"] for s in gui_fsm["states"]}

        # Build outgoing transition map excluding self-loops
        non_self_outgoing: dict[str, set[str]] = defaultdict(set)
        for t in gui_fsm["transitions"]:
            if t["from_state"] != t["to_state"]:
                non_self_outgoing[t["from_state"]].add(t["to_state"])

        # Identify pure sink states (no escape transitions at all)
        sink_states = {
            s for s in all_state_ids
            if not non_self_outgoing.get(s)
        }
        # IDLE is the designated rest state; if it has outgoing transitions it is fine.
        # If it ends up as a sink it is still valid semantically, so we exclude it.
        problematic_sinks = sink_states - {initial}
        assert not problematic_sinks, (
            f"Deadlock sink states detected (no outgoing escape transition): {problematic_sinks}"
        )

    def test_initial_state_has_outgoing_transitions(self, gui_fsm):
        """The initial state must have at least one outgoing transition."""
        initial = gui_fsm["initial_state"]
        outgoing = [t for t in gui_fsm["transitions"] if t["from_state"] == initial]
        assert outgoing, f"Initial state '{initial}' has no outgoing transitions"


# ---------------------------------------------------------------------------
# Backend Dispatch Table Tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def backend_fsm() -> dict:
    return _load_json("backend_dispatch_table.json")


class TestBackendDispatchStructure:
    """Structural integrity checks for backend_dispatch_table.json."""

    def test_required_top_level_keys(self, backend_fsm):
        for key in ("strategies", "routing_table", "routing_logic", "domain"):
            assert key in backend_fsm, f"Missing required top-level key: '{key}'"

    def test_routing_table_references_declared_strategies(self, backend_fsm):
        strategy_ids = {s["id"] for s in backend_fsm["strategies"]}
        for row in backend_fsm["routing_table"]:
            assert row["target_strategy"] in strategy_ids, (
                f"Routing row {row['id']}: target_strategy '{row['target_strategy']}' "
                f"not declared in strategies list"
            )

    def test_routing_table_ids_unique(self, backend_fsm):
        ids = [r["id"] for r in backend_fsm["routing_table"]]
        assert len(ids) == len(set(ids)), "Duplicate routing row IDs in backend_dispatch_table.json"

    def test_all_three_strategies_present(self, backend_fsm):
        strategy_ids = {s["id"] for s in backend_fsm["strategies"]}
        expected = {"MONOLITHIC_LIVE", "MULTI_SENTENCE_LOCAL_DEDUP", "MULTI_GLOBAL_COMBINED"}
        assert expected == strategy_ids, (
            f"Expected exactly strategies {expected}, found {strategy_ids}"
        )


class TestBackendDispatchRoutingCompleteness:
    """
    Exhaustive combinatorial coverage: the routing_table must cover every combination of
    (text_mode, sentences_enabled, dedup_scope) that the codebase can produce.
    """

    _TEXT_MODES = ["single", "multi"]
    _SENTENCES_ENABLED = [True, False]
    _DEDUP_SCOPES = ["sentence", "global"]

    def _routing_key(self, row: dict) -> tuple:
        return (row["text_mode"], row["sentences_enabled"], row["dedup_scope"])

    def test_routing_table_covers_all_combinations(self, backend_fsm):
        """Every (text_mode x sentences_enabled x dedup_scope) combination has a routing entry."""
        all_combinations = set(
            itertools.product(self._TEXT_MODES, self._SENTENCES_ENABLED, self._DEDUP_SCOPES)
        )
        covered = {self._routing_key(r) for r in backend_fsm["routing_table"]}
        missing = all_combinations - covered
        assert not missing, (
            f"Routing table is missing entries for combinations: {missing}"
        )

    def test_routing_table_has_no_duplicate_combinations(self, backend_fsm):
        """No two routing rows should share the same (text_mode, sentences_enabled, dedup_scope) key."""
        keys = [self._routing_key(r) for r in backend_fsm["routing_table"]]
        assert len(keys) == len(set(keys)), (
            "Duplicate routing keys detected in backend_dispatch_table.json"
        )

    @pytest.mark.parametrize("text_mode,sentences_enabled,dedup_scope", [
        ("single", True, "sentence"),
        ("single", True, "global"),
        ("single", False, "sentence"),
        ("single", False, "global"),
        ("multi", False, "sentence"),
        ("multi", False, "global"),
    ])
    def test_non_multi_sentences_routes_to_monolithic(
        self, backend_fsm, text_mode, sentences_enabled, dedup_scope
    ):
        """
        All combinations where sentences_enabled=False or text_mode='single'
        MUST map to MONOLITHIC_LIVE.
        """
        covered = {self._routing_key(r): r["target_strategy"] for r in backend_fsm["routing_table"]}
        key = (text_mode, sentences_enabled, dedup_scope)
        strategy = covered.get(key)
        assert strategy == "MONOLITHIC_LIVE", (
            f"Combination {key} expected MONOLITHIC_LIVE but got '{strategy}'"
        )

    def test_multi_sentences_sentence_scope_routes_to_local_dedup(self, backend_fsm):
        covered = {self._routing_key(r): r["target_strategy"] for r in backend_fsm["routing_table"]}
        key = ("multi", True, "sentence")
        assert covered.get(key) == "MULTI_SENTENCE_LOCAL_DEDUP", (
            f"Combination {key} expected MULTI_SENTENCE_LOCAL_DEDUP but got '{covered.get(key)}'"
        )

    def test_multi_sentences_global_scope_routes_to_global_combined(self, backend_fsm):
        covered = {self._routing_key(r): r["target_strategy"] for r in backend_fsm["routing_table"]}
        key = ("multi", True, "global")
        assert covered.get(key) == "MULTI_GLOBAL_COMBINED", (
            f"Combination {key} expected MULTI_GLOBAL_COMBINED but got '{covered.get(key)}'"
        )
