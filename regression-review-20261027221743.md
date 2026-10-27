20261027221743

TRAFFIC LIGHT SIGNAL: RED for release acceptance. The reproduced defects below are repaired, but three unchanged legacy cases require inputs forbidden by the reviewed coordinate contract. Physical GoldenDict/MSHTML verification and complete transport-security certification remain outstanding.

The release ledger's latest anchor is `f87272e` (Release 20261027220940). The implementation audit covers `956178e..f87272e`: 9 files, 1428 insertions and 276 deletions. `d2ee083..f87272e` isolates the latest transport commit. Repairs are the uncommitted diff against `f87272e`; no Git history was changed. The authoritative vault files were read only.

| Finding | Evidence at the reviewed release | Repair |
| --- | --- | --- |
| Desktop membership recovery fabricates success | Saved `{haus: true}` becomes `{selected_row_ids: true}` and the indicator reports synced. The corresponding probe passes at `d2ee083` and fails at `f87272e`. | Read real membership for ordinary Desk pages; reject unavailable static-article reads instead of manufacturing another payload schema. Restore the previous desktop stream startup contract. |
| Rejected SQLite saves partially commit | A valid first word edit persists even when the second target is nonexistent. | Validate all word targets under one transaction and share that connection with word, selection and deletion writes. Errors roll the request back. |
| Missing SQLite coordinates are guessed | A delta containing only display row 1 succeeds and changes the second stored word. | Require explicit positive sentence index and nonnegative token order for word edits. |
| Unmatched TSV descriptor falls through to display position | `(sentence=99, token=99, row_id=0)` changes row 0 and reports success. | Reject missing manifest columns and unmatched/ambiguous explicit descriptors. Retain the separate preexisting row-addressed TSV path when no descriptor is supplied. |
| Lookup invents or shadows identity | Absent sentence metadata becomes 1; an existing lowercase `token_order=9` is shadowed by an appended `TokenOrder=0`. | Remove synthetic lookup coordinates; preserve the existing token-order column; reject incomplete edit/selection commands and escape coordinate attributes. |
| Re-word loses occurrence scope | Selecting sentence 2/token 0 sends token order alone. Even an explicit HTTP descriptor is ignored and both sentence-1 and sentence-2 words are translated. | Carry lookup sentence/token descriptors, resolve them before provider work in the controller and SQLite enrichment adapter, and suppress sibling propagation for explicit occurrence operations. Real SQLite and TSV probes retain sentence 1 unchanged. |
| Server-disabled articles still call the backend | Audio, single-row retry and session retry issue HTTP requests after the new standalone runtime is embedded. | Guard all three entry points and provide explicit feedback. |
| Malformed metadata silently nests | Invalid JSON, arrays and recursively named `extra_fields` are accepted and persisted. | Require an object without recursive metadata keys and reject malformed values without writes. |
| Transport broadens permissions and adds a native incompatibility | The latest commit unconditionally adds private-network permission alongside wildcard CORS, and uses `String.startsWith` without a matching compatibility shim. | Remove the blanket private-network header and use the existing string-index operation. Physical host compatibility is not inferred from this source repair. |

These findings include new regressions and incomplete repairs of earlier defects. The report does not attribute every preexisting failure to the latest commit. In particular, Re-word occurrence targeting was already incomplete before the transport change; the identity change's checked tasks did not establish its promised behavior.

| Evaluation dimension | Result | Basis |
| --- | --- | --- |
| Regression | WARN | Reproduced failures repaired; three legacy fixture conflicts remain visible. |
| Orthogonality | PASS | One transaction owns each SQLite save; descriptor resolution is shared by the command and enrichment boundaries. |
| Consistency | PASS | Explicit sentence/token descriptors survive the repaired lookup journey. |
| Naming | PASS | Coordinate parsing and occurrence-row resolution have explicit names. |
| Production readiness | WARN | Physical hosts and complete transport-security policy are not certified. |
| Polishing | PASS for measured browser cases | Existing layout checks cover four themes and two viewport/zoom combinations. |
| Hardcoding | PASS for this repair | No new provider, local runtime path, schema or dependency was introduced. The existing default-language configuration was not reverted. |
| Flexibility | WARN | Legacy coordinate-free callers must migrate to the explicit contract. |
| Fragility | PASS for reproduced boundaries | Missing/unknown coordinates and malformed metadata reject instead of guessing. |
| Scalability/performance | PASS for measured cases | Existing 256-row pending-selection tests retain linear DOM work and unchanged geometry. |
| Edge cases | WARN | Zero token order, repeated sentence-local orders, lowercase metadata, invalid JSON and delayed saves are covered; all merged/custom-mapping shapes were not exhaustively certified. |
| Correctness | WARN | Exact target and no-write rejection probes pass; unchanged legacy tests still demand the superseded guessing behavior. |

Verification artifacts are in `results/20261027221743-dict-transport-audit/`. Each execution JSON records arguments, exit status, protected-file hashes, and, for final runs, loaded module paths and source hashes. SQLite access outside that run's owned directory is rejected by the harness. External translation providers are controlled; the persistence used by the new save and Re-word probes is real. The different-origin Save control uses two actual HTTP servers without security-disabling browser flags.

| Execution | Result |
| --- | --- |
| `existing` | The two supplied regression files pass all 13 cases on the original reviewed code; this was insufficient coverage. |
| `before-transport-confirmed` | Three selected membership/offline/recovery probes pass at `d2ee083`. |
| `final-baseline` | The 16 new cases produce 15 failures and one passing different-origin Save control at `f87272e`. |
| `final-implementation` | 62 passed, 3 failed; source hashes stable throughout, protected database/configuration hashes unchanged. All 16 new cases and the original 13 targeted cases pass. |
| `final-stable-convergence` | 73 passed, 11 deselected, exit 0; source hashes stable throughout and protected database/configuration hashes unchanged. |
| `proposed-fixtures-verified` | All three conflicting cases pass in isolated copies after only the two fixture lines described below are changed. |
| Syntax and whitespace | Python compilation, both JavaScript syntax checks and `git diff --check` exit 0. |
| OpenSpec | Both named changes pass strict validation. Validation does not establish implementation or physical-host completeness. |

Reproduce the affected suite:

```powershell
venv\Scripts\python.exe scripts\run_dict_transport_audit.py final-implementation tests\test_dict_row_identity_regressions.py tests\test_goldendict_article_transport_regressions.py tests\test_dict_transport_audit_regressions.py tests\test_release_audit_regressions.py tests\test_sqlite_mutations.py
```

Reproduce the baseline with the same new tests:

```powershell
venv\Scripts\python.exe scripts\run_dict_transport_audit.py baseline-review --source-ref f87272e tests\test_dict_transport_audit_regressions.py
```

The remaining unchanged failures are:

- `test_f1_non_german_lookup_language_metadata`, English/French and Spanish/Italian: its one-row fixture has neither sentence nor token coordinates but expects a Re-word mutation to be sent.
- `TestCoreEditSaveSqlite.test_core_edit_save_atomic_sqlite`: its word delta supplies only `row_id` but expects persistence.

Those inputs conflict with the identity change's `row-id-token-order-manifest` and `sqlite-atomic-mutations` requirements, which prohibit using display position to supply missing persistence coordinates. The concrete proposal is `results/20261027221743-dict-transport-audit/legacy-fixture-proposal.patch`: add `(sentence=1, token=0)` to the language fixture and SQLite word delta. Every existing assertion is preserved. The patch has been tested in isolated copies and has not been applied to the original tests.

This review does not certify actual GoldenDict or MSHTML interaction, every merged-row/custom-field shape, production-data recovery, or the full security requirements of the article transport change. SSE/status authentication and the inherited wildcard-origin policy were not comprehensively redesigned here. The new blanket private-network permission was removed. Existing OpenSpec completion checkboxes were not rewritten, synchronized or archived. Stop for user review and a user-managed commit before the physical-host phase or changes to existing test fixtures.
