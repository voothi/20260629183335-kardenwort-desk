20261027221743

Latest follow-up: 20261028090942. The full-suite failure report exposed compatibility regressions missed by the earlier selected runs. The repair baseline is now user-managed commit `a11354e`, recorded in release 20261027234153. The reviewed range is `a11354e` to the working tree, including the new `tests/test_suite_regression_boundaries.py`: seven files, 409 insertions and 31 deletions. The production portion is three files, 49 insertions and 24 deletions. Git remains read-only.

TRAFFIC LIGHT SIGNAL: YELLOW. All 12 reported cases and all 11 added boundary cases pass. The final broad run has one external-provider lookup timeout, which passes independently on both repaired and baseline source. Two warnings and physical GoldenDict/MSHTML acceptance remain open. This is not a zero-failure or release-certification claim.

| Reported failures | Cause and repair |
| --- | --- |
| Six desktop inline-edit/history/navigation cases | The lookup occurrence gate had been applied to ordinary desktop drafts. Desktop drafts can now edit locally while retaining missing coordinates as missing; their histories use distinct display-row keys. Lookup editing stays strict, and SQLite rejects these unresolved drafts before writing. |
| Two skeleton polling cases | Watchdog discovery recognized body `data-zid`, but the canonical session resolver used by stale-response rejection did not. The resolver now recognizes this existing session source; saves and session changes still invalidate obsolete reads. |
| Two language-modal child-tab cases | The raw server flag defaults to false on ordinary desktop documents. Child spawning now uses the existing lookup-aware disabled-capability predicate, preserving desktop tab order and lookup offline restrictions. |
| Legacy visual target propagation case | Legacy `visual_idx` context was treated as a sentence/token persistence descriptor. Both command and SQLite adapter boundaries now distinguish validated visual context from occurrence targets; mixed or incomplete occurrence descriptions still reject. Visual context does not change the explicit master/child propagation scope. |
| Sidecar status case | The positive unknown-session request omitted its required token. It now supplies the same configured credentials already used for shutdown. All 66 assertions in this file are unchanged. Authentication requirements were retained. |
| Additional isolated tooltip case | Restoring the base occurrence view reapplied a cached tooltip even after its provenance changed. The restore path now formats translation tooltips from the current provenance while retaining the original cell value. Both duplicate restore branches share this operation. |
| Explicit legacy SQLite path | `SqliteStorageAdapter` ignored a caller's resolved `database_file` and opened the workspace default. It now honors that declared path after the existing explicit/canonical path options. A real database probe verifies the chosen file. |
| Additional stream-recovery race | Watchdog reads could dispatch during membership recovery, and pre-recovery status responses remained eligible for projection afterward. Polling now waits during recovery; starting recovery invalidates an earlier status read. Manual Update then reads after its recovery barrier. |

| Follow-up evidence | Result |
| --- | --- |
| `suite12-before` / `suite12-after` | All 12 reported failures reproduced at `a11354e`, then all 12 passed after repair. Protected hashes unchanged in both runs. |
| `suite-boundaries-baseline` / `suite-boundaries-fixed` | Eight additional cases cover independent draft histories, rejection of unresolved SQLite saves, invalid/mixed coordinate formats at both boundaries, and a real SQLite visual-context enrichment. At `a11354e`, two fail and six negative controls pass; after repair all eight pass. Only the external provider is controlled in the positive enrichment probe. |
| `suite12-assertion-integrity.json` | AST comparison with `a11354e` confirms all 66 existing sidecar assertions unchanged. Other preexisting test files are untouched in this follow-up. |
| `full-suite-20261028000603` | Interrupted after an additional tooltip failure and a protected-database hash change. The original Python adapter guard did not cover detached worker processes. This run is not passing or data-preservation evidence. |
| `worker-isolated-tooltip` | Confirmed that blocking ambient-config worker launches prevents the protected database write; the tooltip failure still reproduced independently. |
| `tooltip-and-worker-isolation-20261028080904` | Ten cases passed after repairing the tooltip, including all eight new boundary probes. Protected files unchanged. |
| `full-suite-isolated-202610280811` | 3,051 passed, four failed, two skipped, one deselected, two warnings in 754.72 seconds. Protected/source hashes unchanged. The failures concerned native repository discovery under restricted parent-directory enumeration, an owned worker blocked by the guard, the legacy SQLite path, and the harness creating its config files inside a diskless assertion's directory. All four also reproduce at `a11354e` (`full-harness-diagnostics-a11354e`). |
| `harness-and-boundaries-202610280831` | 17 passed in 4.62 seconds, including all four cases and nine new boundary cases. The harness now supplies the override-preserving native sibling path, keeps its default config files beside the fixture directory, and allows workers only with an explicit fixture-owned config/database. Owned processes are waited for or terminated during teardown. Protected files unchanged. |
| `final-assertion-integrity.json` | Reconfirmed all 66 sidecar assertions unchanged against `a11354e`. |
| `full-suite-final-202610280833` | Stopped at 72% after one additional intermittent `test_stream_recovery_blocks_clean_commands` failure. No complete-suite result is claimed for this execution. The failure also reproduced in `stream-recovery-repeat-202610280838`; post-failure state capture demonstrated polling while recovery was still pending. |
| `recovery-poll-before-202610280847` / `recovery-stale-before-202610280848` | Both controlled race probes fail before the repair: a poll dispatches during held recovery, and a tagged earlier status payload reaches projection after recovery. Protected files unchanged in each probe. |
| `recovery-fix-202610280849` | 15 passed, 46 deselected in 21.33 seconds: all 11 added boundary cases and the four existing recovery-command cases. Protected files unchanged. |
| `live-snapshot-202610280855` | One passed in 2.76 seconds. The historical live-session test ran separately from a byte-identical copy of its test module against the verified pre-run database copy, with disposable fixture configuration. `live-snapshot-inputs-202610280855.json` records both input hashes. Workspace protected files unchanged. |
| `full-suite-recovery-fixed-202610280851` | 3,059 passed, one failed, one deselected, zero skipped, two warnings in 1,134.06 seconds. The only failure is `test_controller_lookup_endpoint` exceeding its unchanged 30-second HTTP timeout, accompanied by Google provider network/rate-limit errors. All reported regressions, new cases, geometry/performance contracts, and the two previously skipped headless native bridge cases pass. Source/protected hashes unchanged. `final-suite-summary.json` records parsed XML counts and assertion integrity. |
| `lookup-timeout-check-202610280859` / `lookup-baseline-check-202610280900` | The unchanged lookup case passes independently: repaired source in 26.73 seconds and `a11354e` in 29.82 seconds. Each run exits 0 with protected files unchanged. These retries do not erase the broad-run timeout or establish a reliable provider latency bound. |
| Syntax and specs | Python compilation, JavaScript syntax and whitespace checks pass. Both named OpenSpec changes pass strict validation. |

Full-run command:

```powershell
venv\Scripts\python.exe scripts\run_dict_transport_audit.py full-suite-review-repeat tests -k "not live_session_20261019235609"
```

The full-run exclusion is the historical test hardcoded to open the production database through a writable adapter. It passes separately against a verified database copy with byte-identical test source. All runs retain the pre-collection storage guard. Progressive/batch workers without an explicit owned config/database are rejected. Physical native-window launches are blocked; headless bridge fixtures remain available. Protected local-service requests are blocked in Python and browser contexts; explicit fixture routes and ephemeral controllers remain usable. Sanitized blocked-operation records contain test IDs and ports/worker kinds, without credentials.

| Follow-up evaluation dimension | Result | Basis |
| --- | --- | --- |
| Regression | PASS for reproduced cases | The original 12 cases pass, with added rejection and history probes. Full-suite verification is recorded separately. |
| Orthogonality | PASS | Local draft history, visual context, and persistence coordinates retain separate meanings. |
| Consistency | PASS | Both Re-word boundaries use the same descriptor classification and existing strict resolver. |
| Naming | PASS | Helpers describe target classification and occurrence-cell restoration. |
| Production readiness | WARN | Physical GoldenDict/MSHTML acceptance remains open. Earlier full-run warnings are retained in the evidence. |
| Polishing | PASS for exercised cases | Tooltips use current provenance; existing skeleton and interaction contracts pass. No CSS metrics changed. |
| Hardcoding | PASS for production delta | No new runtime path, provider, schema, or dependency. The local sibling path is confined to the audit runner. |
| Flexibility | PASS for exercised contracts | Desktop draft compatibility coexists with strict Lookup persistence; declared SQLite paths are honored. |
| Fragility | PASS for exercised boundaries | Invalid/mixed occurrence targets reject without database changes. |
| Scalability/performance | PASS by inspection for this delta | No added render traversal, persistent cache, or database scan. Existing measured performance contracts remain in the full run. |
| Edge cases | PASS for targeted cases | Missing coordinates, distinct draft rows, token zero, visual hints, stale provenance and authenticated unknown-session status. |
| Correctness | PASS for persisted effects | SQLite contents verify unchanged invalid targets and the exact successful enrichment target. |

The database hash at the start of this follow-up was `2a6fd1a53900a336143c9bc4f4d6f31c775372e58ba4392a95656fcab444e0a4`. The interrupted overnight run and diagnostic fallback workers changed fixture-session records; the following isolated run compared `49f78dd61f08d76aaf5f52e29bfa672de43cccdcece9b27aa00f7f3568587c4f` before and after. The later morning reruns start from their own recorded snapshot, `5908be979c42b61171ea6dabb94c7e180dce934a813588a14dba267fac21dba6`. An intermediate database was preserved, and the revised harness creates a verified pre-run database copy before each run. No exact initial copy was found in the local backups. On continuation, the user explicitly excluded current-data recovery: "I don't care about current data." No speculative rollback was attempted. Data preservation claims apply only to executions whose protected-file comparisons pass, not to interrupted runs.

This does not certify physical GoldenDict or MSHTML interaction.

The final broad run still reports two warnings, including the existing performance fixture's background attempt to write to Windows `\\tmp`. That warning is not a successful filesystem-write verification. The automated repair phase is ready for review; no archival or physical-host phase was performed.

---

Historical implementation review included in `a11354e`:

Updated implementation review: 20261027225852, following the instruction to fix all identified defects.

TRAFFIC LIGHT SIGNAL: YELLOW. The reproduced automated defects are repaired. Actual GoldenDict and physical MSHTML interaction remain unverified because the available computer-use interface disables native computer APIs. Browser, HTTP, storage and native-bridge results do not certify those physical hosts.

The original audit covers `956178e..f87272e`; `d2ee083..f87272e` isolates the transport release. The first repairs are now in user-managed commit `30510bb` (Release 20261027225757). This follow-up is the working-tree diff against `30510bb`, including the new `tests/test_dict_completion_contracts.py`. The complete review diff contains 12 files, 575 insertions and 151 deletions, including this report and the new test file. Git history was not modified. Vault changes, checkboxes and conversations were not edited, synchronized or archived.

| Reproduced defect | Resulting behavior |
| --- | --- |
| Desktop membership recovery fabricated a successful but incorrect payload. | Membership comes from storage; unavailable reads remain failures. |
| An invalid second SQLite edit left the first edit committed. | Whole-request validation and a shared transaction prevent partial saves. |
| Missing SQLite or unmatched TSV coordinates fell through to display position. | Explicit word descriptors reject missing, unmatched and ambiguous targets. |
| Lookup invented sentence 1 or shadowed lowercase token order. | Authoritative coordinates, including token zero, survive rendering; unresolved mutations reject. |
| Re-word lost sentence scope and changed sibling occurrences. | Complete descriptors reach the controller and enrichment adapter; explicit targets retain other occurrences. |
| Disabled articles dispatched audio and retry requests. | Those entry points reject with explicit feedback and send no request. |
| Malformed or recursive metadata was persisted. | Metadata must be an object without recursive extra_fields nesting. |
| Custom translation columns were emitted as WordDestination. | Editable cells carry configured names; real SQLite and TSV save/reload probes cover Gloss. |
| Merged rows omitted descriptors or edited an arbitrary occurrence. | Rows expose the complete descriptor set. Re-word carries the set; inline editing requires one explicitly selected occurrence. |
| Custom selection columns displayed a checkbox but produced no save delta. | Rendering recognizes the mapped field; the canonical command maps to it, and SQLite restores canonical membership. Selection/deselection round trips pass for both backends. |
| Status and event reads lacked header authentication; wildcard CORS admitted arbitrary web origins. | Status/SSE require the existing token header. CORS admits supported loopback or authenticated opaque article origins; foreign origins reject. Streams send no URL credentials. |
| In-flight status results remained applicable after a save invalidated their read. | Superseded reads reject before projection. Watchdog and Update discard that cancellation without reload or false success. |
| Failed status reads requested a full render whose hydration could overwrite newer selections. | The render fallback is removed. Failures remain explicit, and native recovery retains previously committed selections. |
| Positive legacy fixtures omitted required coordinates or credentials. | Fixture inputs now provide them. All 604 preexisting assertions in the four amended test files are unchanged, checked by AST comparison with 30510bb. |

Production changes in this follow-up are confined to `kardenwort_desk.py`, `kardenwort_controller.py`, `web/js/app.js`, `web/js/services/sse.js` and canonical `web/js/types.js`. No package, schema migration or provider was added. Authenticated SSE uses a fetch stream because EventSource cannot attach the token header; unavailable authenticated streaming fails without an unauthenticated retry.

| Evaluation dimension | Result | Basis |
| --- | --- | --- |
| Regression | PASS for exercised contracts | Exact targets, pending saves, recovery and multi-window bridge paths. |
| Orthogonality | PASS | Atomic commands retain identity; failed reads no longer invoke a potentially mutating render. |
| Consistency | PASS | Complete descriptors and configured names cross rendering, commands and storage. |
| Naming | PASS | Occurrence-target and status-cancellation helpers name their contracts. |
| Production readiness | WARN | Physical hosts remain open; the wider suite reports a test-listener timeout warning. |
| Polishing | PASS for measured browser cases | Existing theme/viewport geometry assertions pass; physical appearance is uncertified. |
| Hardcoding | PASS for this repair | No new runtime path, provider, schema or dependency. Test credentials are disposable. |
| Flexibility | PASS for exercised mappings | Custom translation and selection fields save/reload under both backends. |
| Fragility | PASS for reproduced boundaries | Invalid descriptors, metadata, origins and credentials reject. |
| Scalability/performance | PASS for measured cases | Pending-selection work and invariant box metrics remain covered. |
| Edge cases | PASS for exercised matrix | Token zero, repeated local orders, sorting, merged targets, reversals and late responses. |
| Correctness | PASS for exercised contracts | Persisted rows and restored membership are checked, beyond request construction. |

Evidence is in `results/20261027221743-dict-transport-audit/`. Execution JSON records exact arguments, loaded modules, source hashes, exit status and protected-file comparisons. The harness isolates default workspace storage before collection/module fixtures, preserves explicit fixture configurations and rejects database access outside its owned directory.

| Execution | Outcome |
| --- | --- |
| Original existing / final-baseline | All 13 supplied cases passed at the reviewed release; 15 of 16 added probes failed there. |
| approved-fixtures | All 65 earlier affected cases pass with required fixture coordinates. |
| completion-before / custom-selection-before | Reproduced configured-field, merged-target and missing selection-delta failures. |
| read-security-before | Three authentication/origin boundary failures reproduced. |
| stale-status-before-observed / stale-status-after-observed | The held-response probe fails at 30510bb because the obsolete payload reaches projection; it passes with cancellation enforced. |
| native-retry-db-diagnostic | Before removing the render fallback, real SQLite changes from {haus:false, baum:true} to {haus:true, baum:false} across disjoint saves. The unchanged recovery assertion fails. |
| native-read-recovery-after | 11 passed, 65 deselected, exit 0; source/protected files unchanged. Includes real HTTP queues and headless AHK lifecycle execution. |
| verified-all-repair-contracts | 160 passed, exit 0, in 168.98 seconds; final source hashes and protected files unchanged. Includes all selected native-bridge cases without a native-name filter. |
| final-controller-lookup | 127 passed, 1 deselected, exit 0; source/protected files unchanged. One existing SSE fixture thread times out after its stop flag while blocked in a socket read; assertions pass. This is not a zero-warning claim. |
| Assertion integrity | 604 preexisting assertions unchanged across the four amended legacy test files. |
| Syntax/whitespace | Python compilation, JavaScript syntax checks and git diff --check exit 0. |
| OpenSpec | Both named changes pass strict validation, separately from physical-host acceptance. |

Reproduce the affected suite:

```powershell
venv\Scripts\python.exe scripts\run_dict_transport_audit.py review-repeat tests\test_dict_completion_contracts.py tests\test_dict_row_identity_regressions.py tests\test_goldendict_article_transport_regressions.py tests\test_dict_transport_audit_regressions.py tests\test_release_audit_regressions.py tests\test_sqlite_mutations.py tests\test_release_boundary_contracts.py tests\test_release_save_convergence.py tests\test_intent_contract_integrity.py::test_layout_evidence_and_scoped_update_cost
```

Protected database SHA256 remains `fefb05cc1e54745d078502047a46cb8a9a470d252d0f6143825376c0497c1654`; protected configuration hashes are also unchanged.

The wider run excludes `test_live_session_20261019235609_endpoint_verification`, which hardcodes the production database and constructs a writable adapter. It was not run against protected data. Historical production-data recovery is outside these changes' authorization.

Actual GoldenDict engine/version, article origin, input/visual behavior and physical MSHTML two-window/save/reopen behavior still need their separate host phase. No physical task was marked complete. This automated implementation phase stops for user review and a user-managed commit under the supplied phase constraints.
