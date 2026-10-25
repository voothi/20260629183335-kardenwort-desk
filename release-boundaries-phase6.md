# Release boundary repairs: Phase 6 closure review

Change: `20261025101246-fix-release-boundaries-and-contract-duplication`. Review action: `20261025200714`. Retained evidence: `results/20261025173244-release-boundaries/`.

**Implementation and verification are complete: 36/36 tasks. Overall release signal remains RED.** The complete isolated suite records **2,969 passed / 3 skipped**, exit 0, followed by **13 supplemental passes**, including all three previously skipped cases. F1-F4 and the Update recovery regression pass. Earlier protected-data incidents remain unresolved; physical MSHTML, broad performance and complete visual usability are not certified. Stop for final user review and user-managed commits. No archival, deployment or Git mutation is performed.

## Exact review ranges and inventory

Phase 6 started at Desk `ce04949073508b6fa833ffcd211c48d707a04fa3`. During verification, the user committed the Update fix, new regression and initial runner changes as `64a3cf78875d4f3ee76e0fa7c0104fc70bf54437`. Latest release anchor `20261025183718` in `U:/voothi.vault/multi-repo-sync.md` records that Desk head and native `d27700d39e6f589081f8ca4c994f4051fece65c0`; both heads were revalidated. Native has no Phase 6 changes.

Review Phase 6 as **Desk `ce04949..64a3cf7` plus `64a3cf7..working tree`**, including this report; the vault change updates only this change's task status. The governing historical audit ranges remain Desk `1ac6727..24d47ab` and native `1e129c4..373a595`. The entire subsequent implementation can be inspected as Desk `24d47ab..working tree` and native `373a595..d27700d`. Exact executable diffs and the cumulative Desk stat are retained as `phase6-committed.diff`, `phase6-working.diff`, and `repair-range-stat.txt`.

| File | Phase 6 result |
| --- | --- |
| `web/js/app.js` | Update starts required provider recovery after the existing save/reconciliation barrier, then requests status immediately. It no longer blocks status and button recovery on provider completion. Request payloads and provider policy are unchanged. |
| `web/js/components/TranslationView.js` | One shared pending-state predicate preserves the original pending-selector contract and equivalent skeleton detection in projection and compatibility rendering. No geometry or style change. |
| `tests/test_release_update_recovery.py` | Two added cases hold Re-text through save/reconciliation and verify usable status/button, durable selection, eventual real SSE delivery or network failure. |
| `scripts/run_release_boundary_contracts.py` | Complete-suite selection, source hashes, retained failure metadata, existing translation-service import paths, owned temporary-directory settings, read-only source DB backup and copied native icon fixtures. Backup connections close explicitly. |
| This report and authoritative `tasks.md` | Closure evidence and final review boundary. |

All **123 test files present at `ce04949` are unchanged**, allowing checkout line endings; `test-integrity.json` lists hashes. No existing assertion was weakened. No package or compiler was installed or invoked. No conversation or canonical spec was rewritten. Phase 4's canonical synchronization remains a prepared review artifact, not an applied sync. Phase 5's 17-target / 161-file / 52,914,808-byte tooling retirement remains the exact removal inventory; this phase performs no further artifact sweep.

## Executed verification and negative evidence

Commands run from the Desk root:

```powershell
python scripts/run_release_boundary_contracts.py update --run-label before --evidence-dir results/20261025173244-release-boundaries
python scripts/run_release_boundary_contracts.py update --run-label after --evidence-dir results/20261025173244-release-boundaries
python scripts/run_release_boundary_contracts.py all --run-label verified --evidence-dir results/20261025173244-release-boundaries
python results/20261025173244-release-boundaries/run-optional-fixtures.py
python results/20261025173244-release-boundaries/architecture-check.py
python results/20261025173244-release-boundaries/summarize-final.py
node U:/voothi/20260406202446-openspec/bin/openspec.js validate 20261025101246-fix-release-boundaries-and-contract-duplication --strict
& 'C:/AHK/AutoHotkey_2.0.18/AutoHotkey64.exe' /ErrorStdOut 'U:/voothi/20260629183335-kardenwort-desk/results/20261025173244-release-boundaries/native-fsm.ahk'
```

| Execution | Result | Exit / qualification |
| --- | --- | --- |
| Update before repair | 2 failed, 4 passed | 1; held-provider cases reproduce disabled Update despite completed save/reconciliation. |
| Update after repair | 6 passed, 9.08 s | 0; original four command-barrier cases remain unchanged. |
| First complete-suite attempt (`all-final`) | 2 collection errors | 2; isolated sibling translation-service imports unavailable. |
| Configured complete suite (`all-configured`) | 12 failed, 2,957 passed, 3 skipped; 602.64 s | 1; preserved negative result, not replaced by a selective pass. |
| Failed-case/fixture diagnosis | 47 passed, 2,925 deselected; 32.05 s | Pytest 0, wrapper 1 because copied DB remained open during cleanup. Corrected with `contextlib.closing`; exact owned leftover was containment/reparse checked and removed in native PowerShell. |
| Final complete suite (`all-verified`) | **2,969 passed, 3 skipped; 620.60 s** | **0**, source hashes match final executable worktree, owned checkout removed. |
| Available optional fixtures | **13 passed, 2,959 deselected; 16.29 s** | **0**, including all three main-run skips; separate execution, not a zero-skip full run. |
| Native production FSM headless harness | 66/66 assertions | 0; real AHK/FSM, separate from physical MSHTML. |
| Architecture and syntax | 8 JavaScript syntax checks, 45 targeted functions/callbacks, max complexity 7, 22 unique documented shapes | 0; shared renderer ownership and no new module import edges. |
| Whitespace | Phase 6 committed range, latest-release range and worktree; native latest-release range and worktree | All five exit 0; exact commands in `whitespace.json`. |
| Strict OpenSpec validation | Active change valid | 0. |

The configured run's seven blocked SQLite attempts were legacy tests' own system-temp databases, not user databases. `TEMP`/`TMP`/`TMPDIR` now point inside the owned checkout; the database guard was not relaxed. Other setup failures required enabling the isolated server with ephemeral ports, supplying the actual historical session via read-only SQLite backup, copying native icons, and making existing translation-service modules importable. The pending-selector failure was repaired in production. The earlier lookup timeout involved unavailable network providers; the diagnostic and subsequent full run pass without a provider-policy or timeout change. Its failed result remains in the record; its exact timing cause is not established.

The full run skipped the legacy AHK MVP extractor, historical AI sample and real frequency-index integration because their expected paths were absent in the isolated tree. All inputs exist locally. The supplemental runner copies the exact AHK source, sample pair, core source and frequency CSV into one owned disposable tree, preserving test logic. It runs those three plus ten module-name matches, all passing. `optional-fixture-inputs.json` records source hashes; no copied user sample is retained after cleanup. The first supplemental wrapper attempt stopped at the existing direct-parent safety assertion before pytest; the replacement uses its own nested owned context without relaxing the maintained runner's containment check.

Both final runs record zero **test-process** external SQLite attempts and zero compiler-tooling attempts. The parent runner intentionally opens the source DB read-only to copy its fixture; this is distinct from test-process access. Python audit guards do not constitute a system-wide native/browser trace. The final full run includes all six filesystem-free SQLite cases and the actual native identity/disposal probe with explicit `KARDENWORT_NATIVE_REPO`. Existing translation service source is identified by hash `e929e2d8f14955af68ba82d941784e66f4b4e1af9534bb15af8dc468f064981e`.

## Finding-to-result closure register

The machine-readable register in `finding-contract-map.json` is generated from final JUnit case identities and statuses; it rejects missing named contracts. These groups overlap the complete suite and must not be added to its total.

| Finding | Executed result | Covered boundaries |
| --- | ---: | --- |
| F1: remote selection lost during save | 23 passed | Eight disjoint select/deselect/event/ack/reversal cases; eight same-lemma orderings; failed save/retry; two failed authoritative-read cases; four stale save/read remount/session cases. |
| F2: native consumers share a draining queue | 9 passed | Independent windows, five stop/pagehide/remount/reload/close cases, same-session and different-session HTTP consumers, actual AHK identity/disposal. Dispatcher and expiry tests also pass. |
| F3: optional host null crash | 5 passed | Original unchanged legacy-overview regression plus absent/null/missing-api/null-card cases. |
| F4: TSV polling assumes SQLite | 11 passed | Two backend read-only roundtrips; eight SQLite/TSV delivery/diskless cases; committed-WAL visibility and rejected SQL mutation. All 34 membership-module cases pass. |
| Update waits for provider completion | 6 passed | Held Re-text completion/failure plus four unchanged save-before-command barriers. Automatic held Re-word recovery is not a separately measured case. |

Additional production journeys pass within the complete suite: selection-only persistence and reload; local reversals, Undo/Redo and cell edits; Selected and exact `show all words` behavior; F5/Re-text/Re-word/export barriers; stale stream callbacks and watchdog ownership; provenance/metadata/tooltips/audio; card-scoped, targeted and merged-occurrence exports. Actual Anki import and live external provider availability are not certified by export fixtures.

## Matrix, layout and measured costs

| Surface | Executed evidence | Limit |
| --- | --- | --- |
| Shell/preferences | All 17 defined rows pass; 88 defined value pairs across four themes, three host labels, two card modes, Selected on/off and status/SSE/overlap/reload | Chromium HTML projections with controlled transport. Native and lookup labels do not prove their physical engines; the host label is not a full independent engine implementation in this test. |
| Legacy configuration matrix | 12 rendering rows plus one orthogonality case pass | Actual definition covers 61/73 possible value pairs, missing 12. Existing worker fixture is mocked; no complete provider/audio pairwise or live-service claim. |
| Membership/backend/delivery | Eight SQLite/TSV x controller/desktop-file/mixed/diskless combinations pass, plus invalid/auth/unavailable/WAL cases | Read policy and actual records/files, not every possible configuration Cartesian product. |
| Save/event lifecycle | Same/disjoint lemma, reversal, failure/read retry, remount/session/reload/close tests pass | Controlled transport completions; external network timing is not deterministic evidence. |
| Layout | Eight long-translation cases plus eight retained layout/cost cases; four themes at 1280/100% and 620/125% | Reserved source/translation/table/toolbar rectangle shift <0.01 px; retained eight records show 0 px. This measures those boxes, not all text readability. |
| Native | Real AHK identity/WinHTTP probe and 66 headless FSM assertions; Chromium shared bridge cases | **Physical MSHTML blocked/unverified**: native computer-control APIs unavailable. |

Eight retained layout JSON/PNG pairs are under `layout/`. The measured 100 simple `AppStore.applyDeltas({stage:'translating'})` calls take approximately **0.6–1.1 ms** per case in this one loaded Chromium run, retaining the source node. These are scoped measurements, not a benchmark. Visual inspection of the wide light and narrow dark screenshots finds crowded narrow-table labels and clipped toolbar content at 620/125%; stable rectangles do not establish complete visual usability. That observation remains a review limit, not a repaired or baseline-attributed layout finding.

The idle native-bridge test configures **750 ms**, observes **3 requests at 2,999 ms / 4 at 3,000 ms**, **zero table DOM mutations**, and the same row node, using a simulated clock. No polling cadence was tightened. Broad CPU/memory/network/provider-cost performance remains unmeasured.

The targeted architecture set has complexity <=7. The inherited Update coordinator decreases from **47 to 46**; its whole body is not certified at <=7. Earlier large initialization/card/row coordinators retain their documented scope limits. This phase does not introduce a second membership/history cache or new production dependency.

## Protected-data result and preservation

`protected-before.json` covers **22,807 files / 466,592,791 bytes**. Final `protected-after.json` covers **22,811 files**. The strict phase comparison exits **1**: four files changed, four were added, none removed. This is an explicit negative preservation result for the whole phase interval, not a zero-change claim.

Changed: `data/kardenwort.db`, `results/db.log`, `results/provider_cooldowns.json`, `results/speed_trace.jsonl`. Added: `data/kardenwort.db-shm`, `data/kardenwort.db-wal`, `results/20261025181433.active_provider.json`, `results/20261025181433.log`. All remain preserved. `protected-change-times.json` records timestamps. Application file modifications cluster around **18:15**, between the earlier run ending 17:39 and the later run beginning about 19:38. The process responsible is not established. No restoration or deletion is attempted.

The early Update runs preserve source DB hash `957b9fa00364949c0af0c0e13ff919505c2ce7adea1652ac7623d9fa23e2628e`. The later configured/diagnostic/full/supplemental runs each preserve their source hash `3be135d137dcdb47cba9ba5ab0bf511832161d7026a335c3ace260477aa484c9`. These are different observed states; per-run equality does not erase the phase-wide change.

The read-only fixture backup creates SQLite bookkeeping sidecars at 19:52: an **empty WAL** and **32,768-byte SHM**. This is permitted by the user's accepted SQLite bookkeeping boundary, not an application record write. Final source database bytes remain equal across the full and supplemental executions. Temporary checkouts are removed; the explicitly retained evidence directory is excluded only from its own protected snapshot. A preliminary snapshot attempted during testing encountered a changing temporary file and failed; the final comparison runs after checkout cleanup.

The earlier Phase 1 alteration of session `20261002101427` and Phase 2 alteration of session `20260907043003` plus three update chunks remain unresolved. Hashes alone do not provide row-level restoration. This report neither restores nor recertifies those data states.

## Twelve-dimension release review

| Dimension | Signal | Assessment |
| --- | --- | --- |
| Regression | PASS within executed boundaries | Full suite and supplemental missing-fixture cases pass; all baseline tests preserved. Negative runs remain retained. |
| Orthogonality | PASS | Authoritative reads, local write intent, native identity and renderer ownership retain separate boundaries. Provider completion no longer blocks status after saves. |
| Consistency | PASS | Existing coordinates, request shapes, storage schemas and export identities remain compatible. |
| Naming | PASS | Shared pending predicate and existing save/lifecycle states describe their actual responsibilities. |
| Production readiness | FAIL for release | Earlier data incidents and unexplained interval changes prevent a clean data sign-off; physical host remains unverified. |
| Polishing | WARN | Measured box invariants pass; narrow fixture clipping/crowding prevents blanket visual approval. |
| Hardcoding | PASS within change | Fixture repository paths are isolated verification inputs; no new product language/provider/cadence defaults. |
| Flexibility | PASS within tested modes | Both storage backends and delivery modes pass without a compiler dependency or runtime migration. |
| Fragility | WARN | Deterministic held-provider case is fixed; legacy fixture path assumptions required explicit preparation, and inherited high-complexity coordinators remain. |
| Scalability/performance | WARN | Scoped idle/DOM/delta costs measured; broad performance and provider costs unverified. |
| Edge cases | PASS within executed cases | Null hosts, unavailable storage, malformed inputs, stale generations, reversal/retry, independent disposal and network-failed recovery pass. |
| Correctness | PASS for repaired boundaries; FAIL for release data certification | F1-F4/Update repaired on measured paths; data restoration and physical engine certification remain outside this completion. |

## Final review and user-managed rollback

Review the paired Desk/native implementation ranges above, the exact Phase 6 diffs, negative evidence, data differences and prepared canonical-sync preview. If rollback is chosen, the user must preserve current sessions/exports/evidence and review coordinated Desk/native changes; reverting application code does not restore database contents. Phase 6 can be reviewed independently as `ce04949..64a3cf7` plus the current runner/pending-predicate worktree. Retired ignored compiler artifacts have no automatic Git restoration or replacement download. Do not blindly revert the whole historical audit range.

Task 6.6 is the required final review stop. Archival, applied canonical synchronization, deployment, Git operations, production-data repair and any replacement-runtime migration need separate authorization. The retained evidence inventory lists paths, sizes and hashes; temporary fixture trees and copied user samples are not retained deliverables.
