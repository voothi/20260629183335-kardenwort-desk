20261027221743

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
