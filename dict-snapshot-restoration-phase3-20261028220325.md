20261028220325

TRAFFIC LIGHT SIGNAL: YELLOW. Article appearance, supported actions and focused desktop/storage contracts pass, but the broad suite is not clean: the last run has 3,126 passes and one intermittent TSV article transport failure. Task 3.4 remains unchecked. Physical GoldenDict/MSHTML acceptance remains open.

The starting point is the user's phase-2 commit `bf92a8eff5afd80f10aaca3833d846cf0fe50a5a`; Desk was clean. The appearance reference remains `56f8696`, the existing `20261013101000-snapshot-desk` release. The exact review range is `bf92a8e` to this working tree. Preflight, screenshots, geometry, assertion disposition and performance evidence are under `results/20261028220325-dict-restoration-phase3/`. No Git mutation, package installation, schema change or native implementation change was performed.

## Changes and retained contracts

The dedicated article implementation from phase 2 remains the product surface. This phase adds actual action/failure/layout coverage and rehomes obsolete desktop-in-Dict fixtures using the phase-1 reviewed inventory. `tests/desktop_component_fixture.py` assembles the real desktop shell/runtime with explicit mapped editable cells and complete occurrence descriptors. Its checkboxes are desktop selection-projection probes; it is not presented as a whole-window renderer. Existing whole-window desktop tests and independent real article/controller/storage tests retain those integration boundaries. Fixture UI configuration drops the ambient server credential and uses the disposable session credential explicitly.

The migration exposed one production issue: exact occurrence handling in the desktop runtime was gated solely by lookup mode. Desktop rows carrying an explicit merged-occurrence manifest could omit Re-word targets or ignore edit ambiguity. `web/js/app.js` now applies those protections whenever an explicit manifest is present, and the three internal helpers use row-oriented names. Rows without a manifest retain their previous desktop behavior. The existing exact merged target-set and explicit-occurrence editing assertions pass unchanged. No article toolbar, desktop hydration or broad command surface was added.

The pending-selection performance fixture now exercises the desktop and container owners of `applySharedSelection`, instead of demanding that global inside a real Dict article. All count, dirty-delta, no-save-echo and unchanged-geometry assertions remain. The benchmark uses the same corrected surface names.

One pre-existing endpoint test opened `data/kardenwort.db` directly, bypassing its owned configuration. The guarded full run rejected that connection before access. Its setup now seeds the same two-sentence/provenance contract in a disposable real SQLite database; all existing endpoint assertions remain unchanged. This removes dependence on the contents of the user's current database.

`assertion-disposition-final.json` compares AST assertion sets against `bf92a8e`: **167 unchanged sets, six reviewed product replacements, no removed tests**. The six replacements cover offline article asset/control expectations, missing-identity edit absence, dedicated article shell/CSS ownership and the canonical desktop root's attributes. The full phase-1 disposition remains in `dict-snapshot-restoration-phase1-20261028102008.md`. Backend target, transaction, authentication, origin, storage, Undo and recovery assertions were retained.

## Appearance, isolation and complete actions

```powershell
venv\Scripts\python.exe scripts\audit_restored_dict_matrix.py
```

Exit 0: **16 configurations, 32 snapshot/current renders, 122 setting pairs**. The deterministic pairwise matrix covers compact/dark/light/white, 620/1280px, server enabled/disabled, JavaScript enabled/disabled, CSS enabled/disabled, reduced/reordered/full columns and normal/reordered sections. Both versions receive identical explicit data, host CSS and fonts. All current host typography/background/geometry checks pass, with zero initialization requests or page errors. Section/heading order, column classes/order, readable source/translation, retained empty morphology cells, static offline output and disabled controls without JavaScript are asserted.

Compact first-row cell metrics match the reference exactly; header widths differ by at most 1px. The reference's existing dark/light global-style leakage is recorded rather than adopted as intended behavior. Current styles remain confined to the article. Representative comparisons are retained as `matrix/12-*-compact.png`, `13-*-dark.png`, `14-*-light.png` and `15-*-white.png`; geometry and configuration details are in `matrix/comparison.json`.

The action tests use real isolated SQLite/TSV and disposable actual HTTP article/controller origins. They inspect complete persisted and exported target sets, including frequency-sorted display IDs different from storage IDs, repeated sentence-local token zero, mapped selection fields, merged occurrences, tag/deselect/reopen, held acknowledgments, newer reversals and an Export barrier. A frequency-sorted save also compares every non-selection field before/after. Authentication, stale identity, aborted transport and real TSV worker-lock failures remain explicit without false acknowledgment, guessed targets or automatic weak retries. Pending/error feedback preserves exact measured box metrics at narrow/wide compact and dark/light configurations. Hostile corpus values remain literal with no injected elements, execution or extra requests.

```powershell
venv\Scripts\python.exe results\20261028220325-dict-restoration-phase3\performance-probe.py
```

Exit 0: at 100/1,000/5,000 rows, synchronous pending selection performs exactly **200/2,000/10,000** element queries and takes approximately **1.5/3.2/13ms** in this Chromium sample. The measured first-row box is unchanged. Render times were approximately 5/25/710ms and browser load/initialization 70/121/471ms; no claim of linear Python rendering or physical-host timing follows from these samples. The probe holds and then aborts its only command request, performs no backend write and verifies protected hashes. An earlier exploratory probe closed pages before draining held routes and emitted cancellation diagnostics; the reproducible final probe drains those routes and exits cleanly.

## Test execution evidence

All pytest commands below use this prefix and an owned, unique label:

```powershell
venv\Scripts\python.exe scripts\run_dict_transport_audit.py <label> <pytest arguments>
```

Exact arguments, exit codes, loaded source modules, database backups and source/protected comparisons are retained in `results/20261027221743-dict-transport-audit/<label>-execution.json`, with JUnit XML beside each record. Counts overlap and must not be summed as unique coverage.

| Label suffix after `restoration-phase3-` | Pytest arguments / result |
| --- | --- |
| `migration-second-20261028220325` | `tests/test_dict_row_identity_regressions.py tests/test_dict_completion_contracts.py tests/test_dict_transport_audit_regressions.py --maxfail=1`: exit 0, **34 passed in 16.60s**. |
| `migrated-integration2-20261028220325` | GoldenDict transport, release audit regressions/boundaries, static shell, DOM validation and sentence-coordinate modules: exit 0, **163 passed in 120.07s**; complete selectors are in the execution record. |
| `actions-matrix-20261028220325` | `tests/test_dict_article_actions.py`: exit 0, **38 passed in 41.71s**. |
| `busy-20261028220325` | `tests/test_dict_article_busy_contract.py`: exit 0, **2 passed in 2.97s**. |
| `pending-performance-20261028220325` | `tests/test_release_save_convergence.py::test_membership_with_many_pending_selections_has_linear_dom_work`: exit 0, **4 passed in 3.55s**. |
| `native-module-recheck-20261028220325` | `tests/test_release_save_convergence.py --maxfail=3`: exit 0, **50 passed in 119.77s**. |
| `owned-endpoint-20261028220325` | Isolated provenance endpoint plus completion/row-identity modules: exit 0, **19 passed in 11.20s**. |
| `full-final-20261028220325` | `tests --maxfail=12`: exit 1, **3,125 passed, 2 failed in 906.29s**. The protected-DB fixture rejection is repaired above. The native cross-session event check failed once and passed independently and with its full surrounding module; its assertion is unchanged. |
| `full-recheck-20261028220325` | `tests -p results.native_isolation_diagnostics --maxfail=5`: exit 1, **3,126 passed, 1 failed in 843.52s**. The native and isolated provenance endpoint checks pass. The TSV frequency-sorted article action fails with an explicit transport error; its cause remains unproven. |

The native isolation case also passes five fresh uninstrumented processes. `native-isolation-trace.json` records correct independent queue delivery in the broad recheck, and `native-bridge-identity.json` verifies polling/disposal code is unchanged from `bf92a8e`. This does not explain away its first broad-run failure.

The sorted-action case passes independently against both backends, followed by eight fresh paired runs (**16 passes**) with browser HTTP/console/request-failure traces. All 16 traces are clean. The article had reported failure rather than fabricating a successful acknowledgment in the broad run. No retry, timeout increase, assertion relaxation or speculative production workaround was introduced. The original failure remains an open finding, not a claimed repair. Results-only browser instrumentation was added after that failure for the repeat probes; the broad run's earlier plugin version records native queues only.

After the broad run, fixture construction and the new non-selection comparison were extracted into small helpers without changing their data/assertion semantics. Production code is unchanged after the broad run. The final focused rerun of all article actions, controller integration and real worker-lock checks is recorded as `final-focused-20261028220325`: exit 0, **43 passed in 53.45s**, protected and monitored source files unchanged.

An earlier full run, `full-suite-20261028220325`, was interrupted on the legacy lookup performance fixture; its execution record intentionally retains `stage: running` because the process was interrupted before finalization. That process is no longer running. The unchanged performance assertion was then rehomed to desktop and passed. Initial migration failures and the corrected offline fixture's transient encoding error are retained as failed diagnostic runs rather than relabeled successful executions.

## Verification scope and review boundary

Python compilation, JavaScript syntax, strict OpenSpec and whitespace checks and exact source/addition/protected manifests are recorded in `verification.json`, including the nonzero broad-suite result. New matrix/fixture helpers meet the complexity ceiling. Git's CRLF normalization and global-ignore-file access advisories are diagnostic warnings, not test success evidence. All completed guarded runs report protected config/mapping/database hashes unchanged; the matrix and final performance probe independently compare the same files. This does not certify physical GoldenDict or MSHTML.

Tasks 3.1-3.3 and the evidence/report task 3.5 are complete: overall progress is **18/24**. Task 3.4 remains open because the broader run is not clean; the intermittent transport failure needs a captured reproduction and disposition. This is a review stop, not a completed implementation or release certification. Task 3.5, AGENTS.md and the test-and-apply protocol require presenting the evidence for user review and a user-managed commit. Phase 4 has not started; it owns actual GoldenDict engine/version/origin/transport, readability/actions/reopen persistence and the carried-forward native MSHTML lifecycle checks. Task 5.2 owns later reviewed canonical synchronization. Existing predecessor archives, the reference tag and release history are not modified here.

| Evaluation dimension | Result | Evidence / limit |
| --- | --- | --- |
| Regression | WARN | Broad runs are not clean. Owned fixture repaired; native and TSV transport failures pass repeated focused rechecks but remain unexplained. |
| Orthogonality | PASS automated | Dedicated article stays independent of the desktop runtime; fixtures use the owner of each contract. |
| Consistency | PASS automated | Complete occurrence descriptors survive sorting, merged actions, editing and export. |
| Naming | PASS scoped | Row-oriented runtime helpers and explicit desktop/container benchmark modes. Legacy test names are retained for traceability. |
| Production readiness | WARN | Physical GoldenDict/MSHTML acceptance remains unverified. |
| Polishing | PASS automated | Compact reference geometry, host containment and exact pending/error box invariance. |
| Hardcoding | PASS scoped | Existing presentation constants; fixtures explicitly own their identities, ports, data and credentials. |
| Flexibility | PASS automated | Pairwise configuration matrix, mapped fields, CSS/JS availability and both storage backends. |
| Fragility | PASS automated | Missing identity, unsafe corpus values, auth/stale/transport/busy failures reject without unrelated writes. |
| Scalability/performance | PASS projection / WARN host | Linear measured DOM work through 5,000 rows; render/load samples recorded, native time/memory unmeasured. |
| Edge cases | PASS automated / WARN host | Reversals, held acknowledgments, export policy, merged/mapped targets and real lock failures; physical behavior open. |
| Correctness | PASS focused / WARN broad | Actual persisted/exported effects inspected; last broad run has one explicit transport failure. |
