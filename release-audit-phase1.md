# 20261023221555: Phase 1 release-audit evidence

Historical checkpoint. Implementation resumed on the user's instruction to continue to the end; see [repair results](release-audit-results.md) for current outcomes.

Change: `20261023214525-fix-release-audit-regressions-and-failfast-gaps`.
Scope: baseline `56f8696` to target/current HEAD `2f6839ef25c7c5643c73bbfbfb8cdaa6bc168ce5`.
The release ledger confirms these anchors at `U:/voothi.vault/multi-repo-sync.md:48126` and `:50961`.
The release comparison changes 37 files, adding 20,437 and deleting 11,943 lines.
This phase adds diagnostic tests, a reproducible capture script, and this evidence report. No production repairs are included.

## Task status and review boundary

- 1.1: All 15 inventory journeys mapped below. Mapping is not end-to-end verification.
- 1.2: Executable browser contracts cover preferences, empty actions, autosave, late acknowledgment, and F5/status/SSE in single/container pages. Actual native-host execution remains unverified.
- 1.3: Baseline/current dark-theme styles and indicator fixtures captured; conflicting requirements reconciled below. Other themes/zoom/overflow measurements remain for phase 4.
- 1.4: Pairwise configurations and mandatory event sequences defined below. Definition does not count as execution.
- 1.5: Review checkpoint reached; phase 2 has not started.

The vault is strictly read-only under the supplied operating constraints. Its `tasks.md` therefore remains unchanged at 0/41 checked tasks. No conversation files, Git metadata, or installed packages were changed.
The user phase rule requires a stop for review and user-managed commits. The matching [test-and-apply-protocol skill](C:/Users/voothi/.gemini/config/skills/test-and-apply-protocol/SKILL.md) says: "Stop at designated phase boundaries to allow the user to review code and perform git commits."

## Baseline/current journey inventory

Baseline references below are line numbers in `git show 56f8696:kardenwort_desk.py`, not current file positions. Current JavaScript lives under `web/js/`. Tests listed are existing contracts to preserve, not evidence that their entire associated journey has been executed here.

| Journey | Baseline owner/evidence | Current owner | Existing tests / prior contract | Disposition and remaining scope |
|---|---|---|---|---|
| Startup/hydration | `run_render_flow:10204`, inline initialization, lookup `:24593` | `kardenwort_desk.py:13583` shell; `app.js` bootstrap; `WorkspaceTabs.js` initial mount | `test_static_web_shell.py`, `test_lookup_renderers.py`, `test_workspace_tab_container.py` | New store/legacy ownership collision confirmed. Full native/lookup preference precedence remains unverified. |
| Toolbar | Inline toolbar CSS: danger hover and active weight 600 | `web/css/table.css:881-975`; `app.js:575-669` bindings | `test_web_action_toolbar.py`, `test_selected_rows_filter.py`, `test_same_lemma_toggle.py` | Neutral Delete/invariant 500 are requested corrections to inherited styling. Current adds ID-specific danger hover. |
| Selected | `setFilterSelectedOnly:23543`, empty projection `:19835` | `app.js:9841`, `:6924`; `store.js:77-81`; `WorkspaceTabs.js:374` | `test_selected_rows_filter.py`; selected-rows-table-filter | New background-store reset and action loss reproduced. Zero/one/many, drag and real reload closure remain for phase 2. |
| Lemma/occurrences | `setHighlightSameLemma:23490`, existing token/highlight helpers | `app.js:9785`, `:7186`, `:4348`; store notification | `test_same_lemma_toggle.py`, `test_hover_highlight_mvp.py`, `test_der_pronoun_occurrence.py` | New legacy/store reset reproduced. Full repeated/compound/subtoken/pin matrix remains unverified. |
| Selection persistence | `notifyAHKSelection:20595`; baseline omits edit-autosave trigger | `app.js:7702`, `:8115`, `:8761`; `/session/save` in `kardenwort_controller.py:3596` | `test_overview_tab_selection_persistence.py`, `test_sqlite_restore.py`, `test_sqlite_mutations.py` | Missing selection autosave inherited; automatic persistence is requested behavior. Real SQLite autosave round trips belong to phase 3. |
| Save lifecycle | `onSaveClick:21547`, `clearDirty:21081` | `app.js:8698`, `:8187`, `:8780` close-save path | `test_web_action_toolbar.py` edit/close autosave; overview Ctrl+S test | Unconditional acknowledgment clearing is inherited source behavior; delayed-response case reproduces newer input loss. Do not enable cell-edit autosave. |
| Update/F5 | `onUpdateClick:21673`, existing native FSM bridge | `app.js:8830`, `:6684`; `WorkspaceTabs.js:1065`; native references below | `test_web_action_toolbar.py` status/fallback tests; dual-button-save-update | Simple status-only F5 is a passing control. SSE overlap plus reprojection loses the action. Native execution and actual reload remain unverified. |
| Feedback | Existing toast/empty-state paths | `app.js:8435`, `:6924`, `:9910` show-all action | selected filter tests; action-bar feedback contract | Empty action loss confirmed; no claim that every success/failure toast has been audited dynamically. |
| Progressive loading | Baseline inline server placeholder max-width 500px; retry/provider helpers | `table.css:526-591`; SourceViewer/TranslationView; app provider/retry/watchdog paths | `test_progressive.py`, `test_watchdog_resilience.py`, `test_clean_translation_view.py` | Oversized width inherited; current text typography grows to 16px. Compact visual is requested correction. Retry/failover/terminal lifecycle remains for phase 4. |
| Geometry | Inline toolbar weight 500/active 600; text/table skeleton CSS | `table.css:551-591`, `:881-975`; source/translation components | `test_static_web_shell.py`, `test_ui_interactions.py` geometry cases | Selected width remains 110px in the capture despite weight change. No zero-reflow claim for other labels, zoom, viewport, or multiline transitions. |
| Editing/history/deletion | `makeEditable:20617`, history helpers, `onDeleteClick:22252` | `app.js:7724`, `:7975-8110`, `:9417` | `test_ui_interactions.py`, `test_web_action_toolbar.py` | No regression established here; commit/cancel/undo/redo and deletion during updates require phase 8 execution. |
| Re-text/Re-word | Existing controller routes and inline request handlers | `kardenwort_controller.py` Re-text sentence read `:2211`; `app.js:8962`, Re-word request path | `test_unslop_retext_and_sse.py`, `test_sqlite_sentence_isolation.py`, `test_sentence_idx_contract.py`, `test_translation_bias_tab_sync_retext.py` | Read failure converted to empty boundaries is a fail-fast defect; side-effect proof remains for phase 6. Existing provenance and coordinate contracts must remain. |
| Export | Inline selected-row/context assembly and export command | `app.js:9240-9404`; controller export route | `test_dynamic_export.py`, `test_same_lemma_toggle.py` targeted/unrolled export cases | No behavioral parity claim here. Preserve overview/child identities and persistence ordering; phase 8 closure required. |
| Audio/hand/text | Existing left/right-click token/row handlers; `onHandToolClick:22241` | `app.js` audio paths, `:9406`, row/source pointer handlers | `test_ui_interactions.py` audio/hand/range tests | No regression established. Remount duplicate actions, identity, and native parity remain unverified. |
| Security/delivery | Baseline render/lookup JSON and configured CSS; old theme resolution `:23961-23964` | shell `kardenwort_desk.py:13573-13583`; controller auth; `kardenwort_grammar.py`; HTTP static routes | `test_json_island_escaping.py`, `test_prompt_injection_safety.py`, `test_static_web_shell.py`, `test_lookup_renderers.py` | Removed theme application is a new regression. Mandatory asset fallback and grammar alias duplication require phases 5/7. German defaults/root route/GoldenDict order are intentional exclusions. |

Prior decision evidence was read from `conversations/20260907043842-the-reason-selections-were.md` and the overview persistence tests. Preserve separate `overview_selections`, restore master-card `selected_ids`, and execute Ctrl+S once including non-Latin layouts. The four overview contracts pass in this run; that does not establish selection autosave.

## Reproductions and commands

New cases are in `tests/test_release_audit_regressions.py`. They execute the real rendered shell, store, components, row clicks, save handler and acknowledgment processing. Only outgoing transport/EventSource is controlled. Fixtures force TSV storage into `scratch`, with no production session writes. Init status requests complete normally; only the explicit F5 status request is held. Tests use predicates and Playwright's virtual clock rather than fixed sleeps.

```powershell
python -m pytest tests/test_release_audit_regressions.py -q --tb=short -p no:cacheprovider --basetemp=scratch/20261023221555-pytest-verified
python -m pytest tests/test_overview_tab_selection_persistence.py -q --tb=short -p no:cacheprovider --basetemp=scratch/20261023221555-existing
python scripts/release_audit_baseline.py
git diff --check
git diff --check 56f8696
```

The overview suite exits 0: **4 passed in 2.80s**. Capture script exits 0: **2 versions captured; 17 matrix rows; 88 pairs covered by definition**. Working-tree tracked diff check exits 0. Baseline whitespace check exits 2, confirming pending phase 7 cleanup; details are in the evidence directory.

The regression command exits 1: **8 failed, 2 passed in 26.99s**. Both status-only F5 controls pass (single and container); both overlap cases fail at the missing empty action. Other failures reproduce Selected/Lemma state reset, absent selection autosave with cell autosave both off/on, and acknowledgment loss. Logs are in `results/20261023221555-release-audit/verified-regressions.txt`. Failures are deliberately unmarked regression contracts, not expected-failure decorators or completed repairs. Early harness runs exposed an incorrect link role, a non-isolated inherited fixture, and preexisting watchdog status requests; these were corrected before accepting the final evidence.

The `results` and `scratch` trees are already Git-ignored. Keep the three new source/report files for user-managed review/commit; rerunning the capture script regenerates the ignored screenshots and measurements. New-file whitespace was checked separately using `git diff --no-index --check -- NUL <file>` because ordinary `git diff` excludes untracked files. No-index reports exit 1 for the added-file differences with no whitespace diagnostics; ordinary tracked diff checks report exit 0 after the repair.

## Style reconciliation and captures

`scripts/release_audit_baseline.py` loads baseline Python directly from read-only `git show`, renders both versions against the same isolated fixture, and captures computed boxes/styles at 1280x900. It saves `baseline.png`, `current.png`, and `styles.json` under `results/20261023221555-release-audit/`.

| Metric | Baseline | Current | Required contract |
|---|---|---|---|
| Selected idle/active weight | 500 / 600 | 500 / 600 | 500 / 500; common toolbar weight, chip weight separate |
| Selected button box | 110 x 28px | 110 x 28px | Preserve dimensions; no displacement demonstrated in this viewport |
| Delete hover text | rgb(255,123,114) | rgb(255,123,114) | Shared neutral command tokens |
| Diagnostic text indicator | 500 x 20.796875px, 13px text | 500 x 25.59375px, 16px text | Shared compact indicator typography independent of content reservation |
| Diagnostic table indicator | 104.390625 x 17.59375px, 11px text | 104.4375 x 21px, 11px text | Compact table visual language |

Indicator captures deliberately insert the exact server-style pending markup into a rendered document. They measure CSS treatment, not a real provider lifecycle or pending-to-resolved reflow. Button color-transition samples are diagnostic; typography/boxes are the relevant comparison. No screenshot establishes full theme coverage or long-text accessibility.

Current requested contracts supersede older active Selected weight 600, static-shell toolbar weight 400, and skeleton examples prescribing a 500px indicator. Use invariant toolbar 500 and compact indicators while preserving separate chip typography and stable content-region bounds. Do not clip long results or impose arbitrary global fixed heights.

## Coverage definition and remaining native evidence

`matrix.json` defines 17 deterministic greedy pairwise rows over dark/light/white/compact, browser/native/lookup, single/container, filter off/on, and status/SSE/overlap/reload. An exhaustive standard-library set comparison verifies all 88 pairs are represented. Rows are explicitly **not executed**.

Every applicable row must verify initialization without page errors, semantic selection visibility, one empty action, preference preservation, request counts/auth, and final restored storage state. Future geometry runs add narrow viewports/zoom and short/long/multiline text; broader phase 8 tests retain the existing provider/highlight/audio matrix.

Mandatory deterministic race cases, in addition to pairwise rows:

1. Select in overview; capture submitted snapshot; switch child; select there; start Update; deliver SSE; acknowledge the original save; acknowledge latest queued selection; reload. Observe request boundaries and real SQLite overview/child sets at each step. Reload must follow required acknowledgments.
2. Edit a cell while a selection snapshot is pending. Acknowledge selection only; verify the text edit remains dirty and SQLite text stays unchanged until manual Save.
3. Fail the save; request F5; verify no destructive hydration/reload, recoverable dirty state and error feedback; retry and acknowledge before refresh.
4. Deselect last row; status-only F5; status/SSE in both orders; remount; reload with persisted filter. Exactly one keyboard/click show-all action must disable filtering without changing selection.
5. Hold close/pagehide save completion; verify keepalive/native non-blocking behavior without claiming unacknowledged delivery. Repeat Ctrl+S on Latin/non-Latin layouts and count one command.

Drive these through response promises, endpoint completion events, exact request counters, DOM predicates and SQLite reads. Do not substitute elapsed time for acknowledgment.

Native source inspection: `U:/voothi/20240411110510-autohotkey/kardenwort-window/kardenwort-window.ahk:3902` shares Ctrl+S/F5 routing: a clean window with PendingUpdate invokes Update; otherwise it invokes Save. `ActionUpdateClickIO:649` captures selected rows/scroll and calls `PerformReload` through the FSM. This differs from browser F5's direct Update path (`app.js:6684`). No native code was changed. Actual MSHTML/AutoHotkey F5, host close and SSE overlap are **unverified**; browser emulation is not represented as native evidence.

## Preliminary traffic light

**RED**: Reproduced state loss and acknowledgment loss prevent parity sign-off.

| Dimension | Signal | Evidence / limit |
|---|---|---|
| Regression | FAIL | New store delta resets user toggles |
| Orthogonality | FAIL | Shared notification copies initialization defaults over interaction state |
| Consistency | FAIL | Legacy state, store and DOM disagree |
| Naming | WARN | Multiple aliases remain; grammar migration pending |
| Production readiness | FAIL | Missing assets/read failures remain permissive |
| Polishing | FAIL | Empty action disappears; requested toolbar/loading corrections pending |
| Hardcoding | WARN | Oversized indicator/typography; theme mapping pending |
| Flexibility | WARN | Shared shell helps reuse; competing consumers remain |
| Fragility | FAIL | Delayed save acknowledgment clears newer intent |
| Scalability/performance | WARN | No performance benchmark executed in this phase |
| Edge cases | FAIL | Filtered-empty/SSE overlap and late input reproduce defects |
| Correctness | FAIL | Selection autosave contract unmet |

Full 12-dimension closure is phase 8 work. This report is the phase 1 review checkpoint, with native reproduction and broad end-to-end coverage explicitly outstanding.
