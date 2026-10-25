# Release boundary repairs: Phase 2

Action: 20261025114232. Change: `20261025101246-fix-release-boundaries-and-contract-duplication`.

**Phase 2 is complete; 11/36 tasks are checked. F3 and F4 are repaired on the exercised boundaries. Overall traffic light: RED** because Phase 3's selection-convergence and native-consumer failures remain, and the protected-data incident below remains unresolved. User approval recorded at `20261025122539` accepts SQLite internal WAL/SHM bookkeeping while preserving application data. Stop for review before Phase 3.

## Scope and changes

Desk started clean at `9310ebe7fd140ee011cff08ba714353c727ee316`, the user's Phase 1 commit. Native remains `373a595dfd57ef9ef068c111cf2494bea0985079`. The governing earlier release ranges remain Desk `1ac6727..24d47ab` and native `1e129c4..373a595`; this phase's implementation diff is Desk `9310ebe..working tree`. No native code, Git history, conversation, compiler artifact, or production session was intentionally edited.

| Boundary | Resulting behavior |
| --- | --- |
| Optional legacy workspace | `isLegacyOverviewContext` returns false when WorkspaceTabs or getActiveCard is absent; existing overview identity and historical cutoff remain intact |
| Storage query | StorageAdapter.get_shared_selection validates the session ID and delegates to an authoritative backend read; failures are structured, with no invented empty membership |
| SQLite | Reads session existence/deleted state and words in one read transaction; a read-only adapter skips migrations, sanitization and schema initialization |
| TSV | Restores the authoritative session TSV in the configured results directory and resolves configured lemma/selection roles; missing columns or invalid rows fail explicitly |
| Normalization | Shared projection trims/lowercases lemmas and recognizes `1`/`true` selection values; any selected occurrence makes its normalized lemma a member, without rewriting occurrence records |
| Polling HTTP | Authenticates first, preserves the ok/shared_selection/events envelope, and calls the storage interface instead of assuming `.db` |
| Invalid inputs | Invalid shapes/ZIDs return INVALID_PAYLOAD; unavailable sessions return NOT_FOUND; unreadable/invalid records return QUERY_FAILED; unsupported query backends return CONFIGURATION_ERROR |

The query-only construction flag reaches KardenwortDB so missing directories/databases are not initialized by a query. SQLite connections require an existing file (`mode=rw`), then enforce `PRAGMA query_only`; a query-only DB handle rejects requests for write connections. Ordinary writable construction remains the default. A `mode=ro` experiment left new empty WAL/SHM sidecars after a cold read, so it was not retained. Query-only handles do not create diagnostic directories or redirect the logger used by writable handles.

This is a logical read-only application boundary, not a claim that the SQLite pager performs zero filesystem housekeeping. No immutable database URI is used: the live-WAL test verifies visibility of committed WAL contents. It also verifies rejection of an attempted SQL UPDATE.

## Executed verification

Commands from the Desk root:

```powershell
python scripts/run_release_boundary_contracts.py membership --evidence-dir results/20261025114232-release-boundaries
python scripts/run_release_boundary_contracts.py storage --evidence-dir results/20261025114232-release-boundaries
python scripts/run_release_boundary_contracts.py controls --evidence-dir results/20261025114232-release-boundaries
python scripts/run_release_boundary_contracts.py contracts --evidence-dir results/20261025114232-release-boundaries
```

| Suite | Result | Coverage and limits |
| --- | --- | --- |
| Membership | 45 passed, exit 0, 33.60 s | Original F3/F4 cases, optional APIs, real SQLite/TSV round trips, authentication, invalid/unavailable sessions and records, cold query construction, configured roles, delivery policy, live WAL and SQL mutation rejection |
| Storage / export / assets | 80 passed, exit 0, 27.29 s | Existing database/router/restore and overview selection contracts; real favorites exports; browser card/occurrence export request contracts; 32 required-asset error cases |
| Controls | 5 passed, exit 0, 7.76 s | Actual AHK/WinHTTP bridge authorization, pending queue retention/disposal, sequential browser synchronization, Undo during a held save |
| Phase 1 contract inventory | 13 failures / 14 passes, exit 1, 23.51 s | Remaining failures belong to F1/F2; F3, F4 and invalid/unavailable-session cases pass |

Each suite has a log, JUnit XML and execution JSON under `results/20261025114232-release-boundaries/`. Final guarded runs require no external database opens, unchanged workspace database hashes during execution, and removal of their temporary checkout. These counts are executed suite totals, not a count of distinct scenarios across overlapping suites or certification of the full repository suite.

The original legacy-overview test is byte-identical to HEAD: SHA256 `9d769d369d106923aa7f8a7be9dccc1d11c1fb69165dd3596a5d5c41ef53d776`. The user explicitly approved adding a real SQLite session to `test_native_polling_retains_pending_events_and_disposes`. That test previously queried a nonexistent session, conflicting with the accepted rejection requirement. Its queue assertions are unchanged; only its setup now supplies an authoritative session.

No existing test assertion was weakened. New fixture corrections used the actual delivery module and TSV save arguments. The new live-WAL case distinguishes SQLite's transient SHM bookkeeping from application data; the user accepted this distinction for task 2.4.

## Request/response and data invariants

| Request | Observed response |
| --- | --- |
| Authenticated valid SQLite session, Haus selected | HTTP 200; data includes `ok: true`, `shared_selection: {haus: true, baum: false}`, `events: []` |
| Same authoritative TSV records | Same successful membership/event response shape |
| Nonexistent valid-looking session | HTTP 404 / NOT_FOUND on both adapters |
| Invalid session shape, path-like ID, zero ID | HTTP 400 / INVALID_PAYLOAD |
| Missing/unreadable storage or malformed TSV fields | Explicit NOT_FOUND or QUERY_FAILED; no shared_selection success payload |
| Invalid token | HTTP 403 before membership data is returned |
| Deleted SQLite session | HTTP 404; no TSV fallback |

All four delivery settings were executed for both backends: controller, desktop_file, mixed, and mixed with diskless_updates enabled. Polling preserves the configured sink policy and creates no application delivery files, markers, preferences or data directories. Ordinary query snapshots retain identical database/data bytes, directory entries and persistent sidecars, with diagnostic log appends excluded as in Phase 1.

The live-WAL case holds a real writer connection open after committing a selection. A cold polling connection sees the committed value. Database and WAL bytes, path sets and SHM size remain unchanged; existing `.db-shm` contents change when SQLite attaches the reader. The application issues no mutation SQL, and an attempted UPDATE through the query-only connection is rejected. A literal all-bytes zero-write assertion is therefore **not** established for a live-WAL read. The user accepted internal SQLite bookkeeping with application records and database/WAL bytes preserved, no save echoes or delivery artifacts, and SQL writes blocked. This approval is recorded at `20261025122539` in task 2.4 and the change's design and delta specification; no production code or test assertions changed for this clarification.

Per-occurrence token orders, sentence indices and translations remain unchanged after membership queries. Existing exports retain their selected/all/unselected behavior, and browser request assertions retain single-occurrence and child-card scope. Browser export transport tests are identified as request-contract tests; they do not certify an actual Anki import or physical native gestures.

## Isolation incident and corrected verification boundary

The initial broader storage run passed 77 assertions but **failed the runner's workspace-database hash guard**. It is not counted as clean verification. The phase-start database hash was `909f80522fa78afadc5c5cd49c4ee71f965c7ffd24244d4b671c4bcd8ccc13b2`; it changed to `a4299ae774d9e4592a499b783b3f56992d7f503c612a7ba13007d26eb81ea48d`. Read-only inspection found session `20260907043003` updated at `2026-10-25T10:55:03.280406+00:00`, matching the existing Ctrl+S fixture identity. Exact prior row contents were not captured, so no restoration was attempted.

The protected comparison also records appended diagnostic logs and three new generated chunks: `results/20260907043003.en.updates/000329.js`, `000330.js`, and `000331.js`. These are retained as incident evidence; no existing active-channel contents were deleted or rewound.

The runner's initial isolation redirected database and output paths but retained live controller defaults in rendered standalone HTML. The evidence is consistent with that HTML reaching the user's configured controller. Disabling the default controller and using port zero removed the mutation on subsequent runs. Explicit ephemeral-controller fixtures continue to work. This is an isolation correction, not a claimed application repair or database rollback.

The runner now also installs an interpreter audit hook in its disposable checkout that rejects SQLite opens outside that checkout before connection creation, records any attempt, and fails verification even if application code catches the exception. Final guarded runs record zero such attempts and equal before/after workspace database hashes. The runner retries only removal of an empty temporary root after a transient Windows directory-handle lock. One earlier empty probe directory was separately containment/reparse checked and removed non-recursively.

`protected-before.json` records 22,716 files / 481,586,316 bytes at the start of this phase. `protected-comparison.json` lists exact differences from that baseline, including the database and diagnostic changes from the initial unclean run. Subsequent successful isolation does not erase this incident or the earlier Phase 1 incident. Existing sessions, evidence, compiler candidates and exports were not deliberately cleaned up.

## Architecture and code review

New membership-path functions have measured cyclomatic complexity 1-5. The revised connection method measures 7; backend configuration selection and adapter factory measure 5 and 6. Canonical normalization/projection functions perform no I/O; adapter methods own storage access; the controller owns authentication and event draining. No second membership/history cache, storage schema or background worker was added. Existing large constructors in the monolithic modules were not certified wholesale against a new complexity ceiling.

The legacy host change is one optional-boundary guard. Required shared helper and asset failure handling remains in place, with the original asset error cases passing. The StorageRouter forwards the new query operation through its existing adapter.

All eight bundled JavaScript files must pass `node --check`; changed Python files must compile; Desk working/release-range and vault task whitespace checks are recorded in `checks.json`. `working.diff` and `deliverables.json` record exact modified paths, lines and hashes. No TypeScript compiler or package installation is part of verification.

## Twelve-dimension evaluation

| Dimension | Signal | Evidence / remaining limit |
| --- | --- | --- |
| Regression | WARN | F3/F4 pass; F1/F2 still fail by the Phase 3 inventory |
| Orthogonality | PASS | Adapter query separates reads from persistence and controller queues |
| Consistency | PASS | Shared normalization and compatible response envelope across both backends |
| Naming | PASS | get_shared_selection names the query; read_only construction has explicit limits |
| Production readiness | FAIL | Cross-window selection convergence/consumer ownership remain unresolved |
| Polishing | WARN | No geometry or styling changed; physical MSHTML unverified |
| Hardcoding | WARN | Existing native polling cadence remains for Phase 3 |
| Flexibility | PASS | Both configured backends and all tested delivery settings work |
| Fragility | WARN | Structured missing-data errors replace crashes; pending client races remain |
| Scalability / performance | WARN | Membership still scans session words; idle polling cost is a Phase 3 task |
| Edge cases | PASS | Cold reads, missing schema/data, malformed inputs and live WAL verified within the accepted SQLite bookkeeping boundary |
| Correctness | FAIL | Phase 3 membership divergence persists; overall protected-data invariant failed in the initial broader test run |

No full-suite, broad performance, physical MSHTML, compiler cleanup, archive or deployment claim is made. Review the Phase 2 changes and database incident before continuing. The accepted task 2.4 interpretation is recorded in this change's artifacts; canonical specification synchronization remains in Phase 4. Source rollback and commits remain user-managed; a hash-only baseline cannot support a trustworthy data rollback.

The phase review stop follows the user's global constraint and [test-and-apply-protocol](C:/Users/voothi/.gemini/config/skills/test-and-apply-protocol/SKILL.md): "Stop at designated phase boundaries to allow the user to review code and perform git commits."
