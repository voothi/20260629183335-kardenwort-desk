# Release boundary repairs: Phase 3

Action: `20261025143359`. Change: `20261025101246-fix-release-boundaries-and-contract-duplication`.

**Phase 3 is complete; 18/36 tasks are checked. F1/F2 pass on the exercised boundaries. Overall release signal: RED.** The earlier protected-data incidents remain unresolved, and Phases 4-6 remain incomplete. This report certifies the listed repairs and executions, not the whole release. Stop for user review and paired user-managed commits before Phase 4.

## Scope and ownership

The current release anchor in `U:/voothi.vault/multi-repo-sync.md` is release `20261025130332`: Desk `d77ca3a532b35419702d117bd943a9de480a50bf`, native `373a595dfd57ef9ef068c111cf2494bea0985079`. Both started clean. Review this phase as Desk `d77ca3a..working tree` and native `373a595..working tree`. The original audit ranges remain Desk `1ac6727..24d47ab` and native `1e129c4..373a595`.

| Owner | Change |
| --- | --- |
| `web/js/app.js` selection projection | Apply disjoint remote membership while preserving local per-lemma intent from existing deltas and in-flight snapshots. Pending lemmas retain their acknowledgment baseline so an older queued event cannot erase a reversal. |
| Existing save coordinator | Reconcile authoritative membership after an acknowledgment when membership arrived during the save. Keep `reconciling` / `reconcile_failed` dirty until success; retry a failed read without repeating a committed save. Reject stale session/mount responses. |
| Dirty-state query | Delegate to existing delta calculation instead of maintaining a duplicate selection scan. Native snapshots and unresolved reconciliation remain dirty. |
| Native JavaScript bridge | Require an explicit host consumer identity; register immediately, then poll at the supplied cadence. Stop is idempotent and closes only its captured session/consumer. Reject stale callbacks; skip table rendering for unchanged effective membership. |
| `web/js/services/sse.js` | Keep the persistent stream under its existing private owner. Remove its writes to the watchdog's `_kwEvtSource` alias; reject callbacks from disconnected streams. |
| Native `kardenwort-window.ahk` | Allocate identity once per actual window from process ID, HWND, tick count and process-local window sequence. Supply existing `FileWatcherIntervalMs`; explicitly dispose before navigation and destruction, with authenticated HTTP disposal if the document is unavailable. |
| Controller / dispatcher | Dispose unavailable-session consumers without opening storage. Run the existing 60-second expiry from `serve_forever` maintenance, including when no other consumer polls. |
| Isolated runner | Add convergence/watchdog suites, unique run labels and hashes of tested production files/native host. Preserve the existing external-SQLite guard and disabled live-controller defaults. |

The projection overlay is ephemeral. There is no second full membership/history cache, schema migration, new package, compiler cleanup, archive, deployment or Git mutation. Existing tracked tests are unchanged in this phase.

## Executed results

Commands run from the Desk root; each named run retains `.log`, `.xml` and `-execution.json` under `results/20261025143359-release-boundaries/`:

```powershell
python scripts/run_release_boundary_contracts.py convergence --run-label reviewed --evidence-dir results/20261025143359-release-boundaries
python scripts/run_release_boundary_contracts.py watchdog --run-label final --evidence-dir results/20261025143359-release-boundaries
python scripts/run_release_boundary_contracts.py contracts --run-label fixed-load-a --evidence-dir results/20261025143359-release-boundaries
python scripts/run_release_boundary_contracts.py contracts --run-label fixed-load-b --evidence-dir results/20261025143359-release-boundaries
python scripts/run_release_boundary_contracts.py membership --run-label final --evidence-dir results/20261025143359-release-boundaries
python scripts/run_release_boundary_contracts.py storage --run-label final --evidence-dir results/20261025143359-release-boundaries
python scripts/run_release_boundary_contracts.py controls --run-label final --evidence-dir results/20261025143359-release-boundaries
```

| Run | Result | Coverage |
| --- | --- | --- |
| Convergence, reviewed | 95 passed, exit 0, 59.82 s | Same/disjoint lemmas; both event/ack and commit orderings; newer reversals; save/read failure and retry; stale save/read after session change/remount; native queue lifecycle; focus/filter/empty action; command sequencing; prior intent/layout contracts |
| Watchdog, final | 50 passed, exit 0, 33.92 s | Existing container watchdog, resilience and toolbar cases; forced watchdog completion preserving membership stream; rejected disconnected-stream callbacks |
| Boundary fixed-load-a | 27 passed, exit 0, 27.82 s | Original Phase 1 contracts, including unchanged legacy-host regression |
| Boundary fixed-load-b | 27 passed, exit 0, 26.27 s | Independent concurrent execution of the same contracts |
| Membership, final | 45 passed, exit 0, 33.76 s | Real SQLite/TSV, invalid/unavailable sessions, authentication, normalization, delivery matrix and accepted live-WAL bookkeeping boundary |
| Storage/export/assets, final | 80 passed, exit 0, 27.87 s | Restore/persistence, overview identity, actual favorites exports, occurrence/card-scoped browser export requests and required-asset failures |
| Controls, final | 5 passed, exit 0, 8.43 s | Actual AHK/WinHTTP authorization, real SQLite, native pending queues, browser synchronization without echoes, Undo during save |
| Native headless FSM | 66/66 assertions, exit 0 | Production AHK FSM handlers; retained harness changes only include paths, output location and unreachable loopback test port |

Counts overlap between suites and are not distinct-case totals or a full-suite claim. The reviewed convergence run includes the final different-session native HTTP-window case. Native HTTP-window cases execute production shared JavaScript against actual controller queues through synchronous XMLHttpRequest; actual AHK/WinHTTP is verified separately. Neither is physical MSHTML verification.

The native command was `C:/AHK/AutoHotkey_2.0.18/AutoHotkey64.exe /ErrorStdOut results/20261025143359-release-boundaries/native-fsm.ahk`. Results are in `test_fsm_headless_results.txt`. Eight bundled JavaScript files pass `node --check`; four changed Python files compile without cache writes. Desk release/worktree and native worktree whitespace checks pass. OpenSpec strict validation and artifact diff checks are recorded at finalization.

## Durable and visible timelines

These rows summarize assertions executed against actual controller/storage and shared JavaScript, rather than inferred success from HTTP status alone.

| Ordering | Durable state and observed browser result |
| --- | --- |
| Remote Haus select/deselect during held Baum save | Both event-before-ack and ack-before-event end with visible membership equal to restored SQLite membership. |
| New Baum reversal while first save is held | Acknowledgment exposes the newer false delta; it remains pending until its own save. Final visible/durable membership agrees. |
| Same lemma, remote commit before/after local commit | Without a reversal, the final committed value wins. A newer local reversal remains pending and its own successful save establishes the final value. Eight commit/event/reversal combinations pass. |
| Save fails, then retry succeeds | The local selection remains dirty; disjoint remote selection survives recovery. The existing regression observes two submitted saves and no read/event-triggered autosave echo. |
| Save commits, reconciliation read fails | Dirty state and error feedback remain. Retry performs reconciliation without another committed save; both HTTP failure and invalid-success payload are exercised. |
| New reversal and cell edit during held read | Both remain pending after the read, remote disjoint membership is visible, and Undo/Redo retains the edit. |
| Save/read response arrives after remount/session change | Current membership and deltas remain unchanged by the stale response. Disconnected EventSource callbacks cannot update the current projection/status. |
| F5 / Re-text / Re-word / export during save/read | The command makes no request until both persistence and reconciliation complete; each command is then issued once. Non-Latin Save/F5 shortcuts and selection-only autosave cases pass unchanged. |

The disjoint regression asserts one held save without a reversal and two with a reversal, after advancing the debounce clock by one second. Membership reads carry no consumer ID and do not drain subscriptions. Selection reads and delivered events do not schedule save echoes. Export tests verify request/occurrence/card scope and real favorites files, not a physical Anki import.

## Native lifecycle and measured idle behavior

Two actual controller consumers receive independent `translated_text` and `finished` stages. One window with a captured native save retains its two queued stages while the other drains its own queue. After acknowledgment/reconciliation the first drains its retained stages. Repeated stop removes only its queue. Separate windows in different sessions receive only their own stages and membership; closing one leaves the other session registered.

The real AHK probe executes production identity allocation for two HWNDs and a reused HWND; all three identities differ. It also executes production disposal with an unavailable document against the authenticated controller and leaves the independent consumer registered. The production host calls disposal before both navigation paths and before destruction. Browser tests independently exercise stop, pagehide, remount, reload and close. Server maintenance is tested with the nondeterministic clock controlled: an abandoned consumer expires after 60 seconds while a recently active one remains.

The controlled idle measurement supplies a 750 ms cadence and observes four periodic requests across 3,000 ms, zero table mutations, and unchanged row-node identity. Initial registration occurs outside that measurement. The actual native host supplies its existing `FileWatcherIntervalMs` setting (default 1,000 ms; the disabled watcher sentinel uses that default). The shared bridge retains its 200 ms default for explicit callers that omit cadence. This is a small-fixture count/DOM measurement, not broad performance certification; membership projection and database reads still scan session words.

An active translation editor retains focus, value and selection range during disjoint remote membership. Selected filtering remains active; the exact `show all words` action still restores all rows without saving the unrelated cell edit. Existing four-theme, two-width/zoom layout assertions retain less than 0.01 px region displacement and unchanged source nodes.

## Additional stream failure found during verification

The starting boundary run reproduced 13 F1/F2 failures / 14 passes. Early repair runs passed under ordinary timing, but a concurrent run returned one failure whose log was overwritten by a later invocation using the same filenames. Its exact traceback is unavailable; it is not classified as a clean run. The runner now supports unique labels, and subsequent evidence is retained separately.

Two of three labeled load runs reproduced a missing second membership event at `wait_for_membership(page, 2)`: `contracts-load-a.log` and `contracts-load-c.log` each show 1 failure / 26 passes, while load-b passes 27. The old persistent stream and temporary skeleton watchdog shared `_kwEvtSource`; watchdog completion could close the persistent subscription while its owner still considered it active. An old stream could also apply callbacks after replacement.

The deterministic replay `sse-ownership-before` restores only the disposable copy of `web/js/services/sse.js` from Desk `d77ca3a`. Both new regressions fail: the persistent source has CLOSED state after watchdog cleanup, and a disconnected callback changes current selection. With alias writes removed and current-source guards added, those regressions pass in the 50-case watchdog suite, both parallel 27-case load runs pass, and the reviewed 95-case convergence run passes. Existing assertions were not changed. This diagnoses the reproduced stream defect; it does not reconstruct the overwritten traceback.

## Data and architecture invariants

The Phase 3 baseline contains 22,741 protected files / 486,008,783 bytes. The full comparison records the same 22,741 files and **zero changes**, excluding only the two declared Phase 3 evidence directories. Every isolated verification records an unchanged workspace database hash, no outside-checkout SQLite attempts and automatic temporary-checkout removal. The Phase 3 database hash is `957b9fa00364949c0af0c0e13ff919505c2ce7adea1652ac7623d9fa23e2628e`. No restoration or production-data rewriting was attempted. This baseline does not undo or certify restoration of the earlier incidents described in `release-boundaries-phase1.md` and `release-boundaries-phase2.md`.

All existing tracked tests are unchanged. The legacy test SHA256 remains `9d769d369d106923aa7f8a7be9dccc1d11c1fb69165dd3596a5d5c41ef53d776`. Final execution metadata includes tested source/native hashes; `deliverables.json` and the retained Desk/native diffs identify the review files and exact working changes.

`python results/20261025143359-release-boundaries/architecture-check.py` parses the changed JavaScript functions and nested callbacks using the already installed parser. Forty-seven functions/callbacks have maximum complexity 7. Python AST checks establish no new production import edges and no dispatcher dependency on the controller. Large pre-existing init/watchdog/controller dispatcher functions are not certified wholesale. No package was installed, and this retained diagnostic is not a setup/runtime compiler dependency.

## Twelve-dimension review

| Dimension | Signal | Evidence or remaining limit |
| --- | --- | --- |
| Regression | PASS | Original assertions, save/filter/export contracts and watchdog suites pass |
| Orthogonality | PASS | Read reconciliation stays separate from persistence; no second full membership cache |
| Consistency | PASS | Existing normalization, session and occurrence/export coordinates retained |
| Naming | PASS | Explicit save phases; consumer identity separate from remount generation |
| Production readiness | WARN | Physical MSHTML and complete release validation remain; historical data incidents unresolved |
| Polishing | PASS | Active editor/focus preserved; existing theme/width/zoom geometry cases pass |
| Hardcoding | WARN | Host cadence uses existing INI; existing 60-second expiry and compatibility default retained |
| Flexibility | PASS | Both storage backends and delivery policies retained; session-isolated consumers verified |
| Fragility | PASS | Read failures remain dirty; stale responses and cleanup ownership are explicit |
| Scalability/performance | WARN | Idle table rebuild avoided; full membership scans remain, broad costs unmeasured |
| Edge cases | PASS | Same/disjoint reversals, failure/retry, remount, unavailable-session disposal and HWND reuse covered |
| Correctness | WARN | Repaired boundaries pass; earlier data incidents and remaining release phases prevent overall clearance |

Phase 4 compiler/duplicate-owner cleanup and canonical spec synchronization have not started. Source rollback and commits remain user-managed; hash-only historical baselines cannot establish a trustworthy row-level data rollback.

The review stop follows the user constraint and [test-and-apply-protocol](C:/Users/voothi/.gemini/config/skills/test-and-apply-protocol/SKILL.md): "Stop at designated phase boundaries to allow the user to review code and perform git commits."
