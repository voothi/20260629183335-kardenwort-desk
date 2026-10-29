20261029091603

TRAFFIC LIGHT SIGNAL: YELLOW. All 13 supplied failures pass, and the complete suite exits 0: **3,062 passed, two warnings in 773.53s (12:53)**. The warnings and an earlier initial SSE connection timeout remain explicitly recorded below; this is not a zero-warning or physical-host certification claim. This report concerns desktop behavior; the abandoned GoldenDict restoration remains archived without synchronization.

Baseline: the user's clean Desk checkout at Release `20261027234153`, commit `a11354e`. Review range: `a11354e` to this working tree. The rollback also removed valid desktop compatibility repairs from `c2e11f3`; the relevant behavior was reviewed and reapplied. The wider run additionally reproduced its occurrence-tooltip defect, so that repair was included too. The final `web/js/app.js` matches that reviewed desktop runtime; the Python coordinate classifier is split into small helpers. No Git mutation, package installation, CSS change, storage schema change or provider-policy change was performed.

## Root causes and repairs

| Boundary | Reproduced defect and repaired behavior |
| --- | --- |
| Desktop editing and history | A persistence-coordinate check prevented opening editors on unresolved desktop draft rows. Desktop editing retains blank coordinates, keeps draft history distinct by row/column, and supports edit/cancel/F2/Undo. Persistence still rejects unresolved word targets; it never substitutes display position. Existing lookup strictness is preserved. |
| Skeleton/status resolution | Session identity omitted the document's explicit `data-zid` when no other session element/card supplied it. The session resolver now reads that authoritative attribute, allowing the watchdog to resolve the correct desktop session. |
| Language-modal child tabs | Child-tab spawning used the render-time server constant rather than the current canonical disabled-state predicate. It now honors the effective runtime capability, preserving ordered/reverse-ordered child opening. |
| Re-word coordinates | Legacy visual context was incorrectly treated as complete word-persistence identity. The controller and SQLite enrichment adapter now classify and validate visual-only descriptors separately; selected rows retain ownership. Explicit occurrence descriptors remain exact and mixed/incomplete/invalid formats reject without writes. The three pure helpers have measured complexity 2/4/7. |
| Membership recovery | The watchdog issued status reads while authoritative membership recovery was pending, and earlier status responses remained eligible for projection. Polling now waits during recovery and the preceding status read is invalidated when recovery starts. Held-response tests verify that stale status never reaches projection under both real SQLite and TSV. |
| Explicit SQLite configuration | The adapter ignored the existing `database_file` path alias. It now honors that explicit path instead of silently selecting ambient configuration. |
| Occurrence provenance tooltip | Restoring a row's cached cell restored an obsolete provenance title. Restoration now formats the tooltip from current translation provenance, without changing layout. The existing metadata/inline-edit tooltip assertion passes. |
| Sidecar endpoint fixture | The unknown-session probe omitted authentication, so the correct security response was 403 before route/session validation. The fixture now supplies its configured token; the existing 404 assertion is unchanged. Production authentication is not weakened. |

`scripts/run_dict_transport_audit.py` restores the existing storage-isolated runner protections: owned databases/results, pre-run database copies, per-test protected-file checks, blocked ambient worker/native launches and protected local-service access, and source/protected comparison records. The existing integration endpoint fixture that hardcoded `data/kardenwort.db` now seeds the same two-sentence/provenance contract in disposable real SQLite. Both amended legacy test files retain all **101 original AST assertions** (66 sidecar, 35 controller integration). Other pre-existing test files are unchanged.

New desktop boundary coverage in `tests/test_suite_regression_boundaries.py` retains draft history/no-guessed-persistence, explicit database ownership, malformed Re-word no-write cases, visual-context selection, recovery barriers and obsolete status-response rejection. The tests exercise real storage/controllers and gate only response delivery. An initial extension of the recovery fixture accidentally seeded SQLite while selecting TSV; the setup was corrected to seed the adapter returned by `use_backend`. That failed setup is preserved in the diagnostic evidence, not claimed as a product defect. With the corrected fixture, historical `a11354e` deterministically applies the stale TSV response and fails the unchanged zero-application assertion.

## Executed evidence

The commands use this existing runner prefix:

```powershell
venv\Scripts\python.exe scripts\run_dict_transport_audit.py <label> <pytest arguments>
```

Exact argument arrays, loaded modules, before/after source and protected hashes, database copies, exit codes and JUnit XML are under `results/20261027221743-dict-transport-audit/`. The new work's preflight and final review evidence are under `results/20261029091603-desktop-regressions/`.

| Label | Result |
| --- | --- |
| `desktop-before-20261029091603` | Exit 1: two failures, two passes. Reproduced the absent desktop editor and visual-context rejection; both existing reconnect backend cases passed independently, so their intermittent symptom was not falsely claimed as directly reproduced. |
| `desktop-status-race-before-20261029091603` | Exit 1: three failures. The watchdog issued a held status request during recovery and the SQLite stale response reached projection. The first TSV extension had the fixture-seeding error described above. |
| `desktop-tsv-baseline-proof-20261029091603` | `--source-ref a11354e tests/test_suite_regression_boundaries.py::test_status_started_before_recovery_is_discarded -k tsv --maxfail=1`: exit 1, one failure, one deselected. Correctly seeded real TSV; stale-status applications are 1 instead of 0. |
| `desktop-affected-after-20261029091603` | Exit 1: 146 passed and the new TSV fixture initialization failed. All 13 supplied cases passed; the new fixture problem was then diagnosed from actual 404 membership responses and corrected. |
| `desktop-affected-final-20261029091603` | Exit 0: **150 passed in 56.41s**. Includes all supplied cases, both reconnect backends, complete UI-interaction/language-modal/sentence-coordinate modules, the new boundary coverage and controller integration. Source/protected comparisons are unchanged. |
| `desktop-full-20261029091603` | `tests --maxfail=6`: interrupted by ENOSPC during test-artifact/JUnit writes; no complete functional summary. Workspace protected hashes remain unchanged. |
| `desktop-full-retry-20261029091603` | Stopped after two findings before editing monitored source: a new SQLite probe's initial SSE connection timeout and the existing stale-provenance tooltip assertion. Failure log and stop disposition are retained. |
| `desktop-tooltip-recovery-final-20261029091603` | Exit 0: **15 passed in 10.59s**; existing tooltip contract, all new boundary checks and both reconnect backends. |
| `desktop-full-final-20261029091603` | `tests -p results.desktop_recovery_diagnostics --maxfail=6`: exit 0, **3,062 passed, two warnings in 773.53s (12:53)**. No deselection or test skip; protected and monitored source files are unchanged. Browser diagnostics cover the new recovery probe and supplied reconnect case. |

Reproduce the final affected run:

```powershell
venv\Scripts\python.exe scripts\run_dict_transport_audit.py desktop-affected-final-20261029091603 tests/test_static_web_shell.py::test_playwright_table_inline_editing_history_and_navigation tests/test_ui_interactions.py tests/test_web_language_modal.py tests/test_sentence_idx_contract.py tests/test_server_sidecars.py::test_cmd_server_launches_supervisor_and_serves_session_endpoints tests/test_release_audit_regressions.py::test_f2_browser_reconnect_recovers_durable_membership tests/test_suite_regression_boundaries.py tests/test_controller_integration.py --maxfail=3
```

Labels own their output/backup directories; use a new label when repeating a completed command. Python compilation, `node --check web/js/app.js` and `git diff --check` pass. Baseline diagnostic runs may log Windows connection-abort/cancelled-route messages when a failed held-response test closes its browser; final-run warnings are reported separately rather than hidden.

Two fresh paired reconnect repetitions also pass (four cases); a third repetition could not start because its pre-run backup ran out of space. The 50GB test filesystem had reached zero free bytes. Cleanup consolidated twelve redundant/incomplete backup directories created by this task. Complete copies were hash-compared with the retained task preflight database before removal; the failed third-repeat copy was incomplete. The preflight copy and the final affected-run backup remain, and the final broad run creates its own backup. `backup-consolidation.json` maps removed copies to the retained backup, and `disk-exhaustion.json` records the interruption and unchanged protected hashes. Original execution records retain their original backup paths; the consolidation record explains those now-removed duplicates. No workspace data or prior task's artifacts were deleted.

## Review scope

`results/20261029091603-desktop-regressions/verification.json` records final static checks, exact source diff, file hashes, protected comparisons, legacy assertion integrity and per-run results. All 13 supplied test names are verified as passing again in the final full-suite JUnit output. New production helpers meet the complexity ceiling; all CSS/component asset hashes remain unchanged from baseline. The final run's 3,062 cases include the original 3,050 plus 12 added desktop boundary cases.

Two warnings remain: `test_controller_render_multi_sentence_returns_children_and_suppresses_spawn` reports its background SSE listener's socket-read timeout, and `test_run_render_flow_debounced` reports a background fixture thread attempting to write `\\tmp\\20260807205400-test.en.txt` without permission. Both warning-producing tests and all assertions pass, but the warnings are not suppressed or presented as clean thread execution. The terminal warning tail is retained as `full-terminal-tail.txt`. These warning-producing test sources were not modified.

The earlier stopped broad run also had an initial SSE connection timeout in the new SQLite recovery probe, before its race steps. Its cause was not established. The subsequent instrumented complete run passes that probe and all supplied reconnect checks. This report preserves the earlier failure rather than claiming it was a reproduced product defect or concealing it with a timeout increase.

No claim of physical native UI or complete-release certification is made from Chromium/controller/storage tests. GoldenDict support was not resumed; its archived proposal, canonical specs, abandoned article assets and earlier task history are untouched. This repair phase stops for user review and a user-managed commit after verification, as required by AGENTS.md and the test-and-apply protocol.

| Evaluation dimension | Disposition |
| --- | --- |
| Regression | PASS automated: all 13 supplied cases, 150 focused checks and 3,062 complete-suite passes. |
| Orthogonality | PASS scoped: desktop drafts, persistence identity and recovery ownership remain separate. |
| Consistency | PASS scoped: exact occurrences and explicit database aliases keep their existing meaning. |
| Naming | PASS scoped: visual-context and persistence-target helpers name distinct contracts. |
| Production readiness | WARN: two unsuppressed test-thread warnings, one earlier initial SSE timeout and no physical native UI certification; authentication remains enforced. |
| Polishing | PASS scoped: editing/Undo/navigation/skeleton behavior verified; CSS and box metrics are not changed by this patch. |
| Hardcoding | PASS scoped: no new runtime port/path/config constant; fixture credentials/data are disposable. |
| Flexibility | PASS scoped: SQLite/TSV, both child-tab orders and explicit path resolution. |
| Fragility | PASS scoped: invalid/mixed target descriptors reject and stale read completions cannot project. |
| Scalability/performance | PASS change inspection: no new scans, storage reads or persistent caches in the repaired paths; no new timing claim. |
| Edge cases | PASS scoped: blank draft coordinates, independent histories, malformed targets and held-response interleavings. |
| Correctness | PASS automated: actual unchanged/persisted storage and canonical membership are asserted; full suite exits 0. |
