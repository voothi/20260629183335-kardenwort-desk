# Release boundary contracts: Phase 1

Action: 20261025104755. Change: `20261025101246-fix-release-boundaries-and-contract-duplication`.

**Traffic light: RED.** This phase establishes failures and inventories removal candidates. It implements no production repair. Stop here for user review and a user-managed commit before Phase 2.

## Release and ownership anchors

The latest release entry is `20261025102552` in `U:/voothi.vault/multi-repo-sync.md`. Its Desk/native heads match the proposal's targets. Both working trees were initially clean.

| Repository | Inspected release range | Current HEAD | Range size |
| --- | --- | --- | --- |
| Desk | `1ac6727..24d47ab` | `24d47ab34e19a77de9f3dbd33aa26d757c283c1b` | 32 files; 2,918 insertions, 1,742 deletions |
| AutoHotkey | `1e129c4..373a595` | `373a595dfd57ef9ef068c111cf2494bea0985079` | 2 files; 20 insertions, 4 deletions |

Native inspection uses the command-scoped `git -c safe.directory=U:/voothi/20240411110510-autohotkey -C U:/voothi/20240411110510-autohotkey ...`. No persistent Git configuration was changed.

The earlier audit and user decision `20261025101603` were read directly from the vault. That decision supersedes the earlier recommendation to retain compiler checking. Historical reports and conversations remain unchanged. The previous change's continuation waiver does not apply here.

| Finding / decision | Current production owners | Governing delta and repair phase |
| --- | --- | --- |
| F1: dropped disjoint membership during Save | `app.js:4328` applySharedSelection; `8085` acknowledgeValues; `8716` save coordinator; controller `save_session` | web-session-action-bar / Selection Convergence; Phase 3 |
| F2: independent native windows share client ID | `app.js:2249` remount generation, `4353` bridge; `kardenwort_events.py:50` queues; native ActionRenderDoneIO / ActionReloadDoneIO / OnAhkCall | sse-event-dispatcher / Independent Native Polling Consumers; Phase 3 |
| F3: optional WorkspaceTabs null throws | `app.js:4621` isLegacyOverviewContext | unified-web-workspace-tabs / Optional Legacy Workspace Host; Phase 2 |
| F4: backend-neutral polling accesses `.db` | `kardenwort_controller.py:3335`; StorageAdapter, TsvStorageAdapter, SqliteStorageAdapter | unified-web-workspace-tabs / Backend-Neutral Shared Membership Read; Phase 2 |
| Remove persistent compiler coupling and duplicate descriptions | `types.js`, WorkspaceTabs typedefs, `app-contracts.d.ts`, `host.d.ts`, `jsconfig.json`, annotated JS | typed-vanilla-components and Modular JavaScript WorkspaceTabs; Phase 4 |
| Selective artifact retirement | Exact candidates below; protected snapshot | workspace-artifact-maintenance; Phase 5 |

## Execution and failing contracts

Final reproducible commands, from the Desk root:

```powershell
python scripts/run_release_boundary_contracts.py contracts
python scripts/run_release_boundary_contracts.py controls
python scripts/release_boundaries_inventory.py verify
```

The runner copies actual working source and tests into a temporary checkout under `scratch`, redirects configuration database/results paths, and runs Python/Chromium/HTTP/SQLite/TSV there. It sets `SSLKEYLOGFILE` empty and `KARDENWORT_NATIVE_REPO` explicitly. Its JSON records exact subprocess arguments, exit codes, test hashes, database hashes, and automatic temporary-checkout removal. No package installation or compiler execution occurs.

| Executed contract | Result on audited production |
| --- | --- |
| Remote select/deselect, event before acknowledgment, no reversal | 2 failures: committed membership differs from a clean browser |
| Same remote changes, acknowledgment before event, no reversal | 2 passes, including no extra save after advancing the debounce clock |
| New local reversal, both event/ack orderings, remote select/deselect | 4 failures: early event can erase the local reversal; otherwise remote membership remains stale after the reversal saves |
| Failed local save, retained intent, remote update, retry | Failure: retry persists local intent but clean browser still omits remote Haus |
| Two independent native script Windows, actual authenticated dispatcher queues | Failure: generations `[1,1]`, IDs `["1","1"]`, stages `[["translated_text","finished"],[]]` |
| Repeated stop, pagehide, remount, reload, and close | 5 failures: old subscription remains; independent same-session and other-session queues remain present |
| Unchanged original legacy overview test | Failure: null WorkspaceTabs throws; original assertions and fixture source unchanged |
| Real SQLite select/deselect, restore, membership polling and no-write snapshots | Pass |
| Real TSV save/restore followed by authenticated membership polling | Failure: HTTP 500 / SERVER_ERROR / missing `TsvStorageAdapter.db` |
| Empty session, both adapters | 2 passes: structured client errors, no data writes |
| Invalid/nonexistent nonempty session, both adapters | 4 failures: SQLite fabricates successful empty membership; TSV throws HTTP 500 |
| Authentication before storage read, both adapters | 2 passes |
| Distinct dispatcher consumers: pending work, repeated close/reopen, cross-session isolation | Pass |

Final contract suite: **27 cases, 19 expected failures, 8 passes in 23.33 s; pytest exit 1**. These are ordinary failing assertions, not xfails or weakened expectations. The separate evidence verifier requires exactly this finding set, no collection errors or browser timeouts, and the successful controls; its exit 0 means the reproduction inventory is verified, not that the application is repaired.

Positive controls: **5 passed in 9.53 s, exit 0**: authorized and unauthorized production AHK/WinHTTP save bridge, existing pending native queue retention/disposal endpoint test, two browser windows synchronizing sequentially without echoes, and Undo during an in-flight cell save retaining its reversal/history. Physical MSHTML interaction is not exercised by these checks.

SQLite/TSV query snapshots include file contents, directory entries, database and WAL/SHM sidecars. Diagnostic `.log` appends are excluded from that query data invariant. The TSV case verifies that its failing query also leaves these snapshots unchanged. The new fixture's deselection assertion uses the existing browser wire value `''`; persisted membership is asserted as false. Early fixture mistakes (configured results path precedence, expected deselection representation, already queued legitimate save events) were corrected only in new tests. Existing tests were not edited.

The SSE harness uses actual EventSource/controller queues. It holds delivery of received membership events and real HTTP save responses. It maintains one active test transport and rejects callbacks from a replaced test stream, so bootstrap timing cannot duplicate fixture events. Native transport requests are captured from actual shared JavaScript, sent through the real authenticated HTTP endpoint, and delivered back to production receiveUpdate/applySharedSelection. This is a bridge boundary emulation, not an actual MSHTML Window.

Assertions after an earlier failure are not credited as executed: for example, convergence failures prevent later no-echo checks in those cases. The passing acknowledgment-first cases exercise that check. Full repaired journeys, stale-read generations, physical native disposal, pairwise layout/provider/audio combinations, and broad performance remain for later phases.

## Protected-data incident and isolation correction

The initial read-only inventory captured **22,694 files / 481,485,535 bytes** under data, favorites, results and scratch, plus configuration, excluding this change's retained evidence directory. It records SHA256 and size for each file. Compiler candidates and prior evidence were included, not deleted.

The initial direct test runs exposed an isolation failure in the existing legacy test fixture: `get_desk_page_html` loads the workspace config and `run_render_flow` writes its fixture session `20261002101427` into `data/kardenwort.db`. The legacy test ran against that database before the isolated runner was introduced. This changed a protected file and is a failed data-preservation invariant for the overall phase.

- Original database hash: `56f26fcdf16bc0b140b53dd46d63d50812be75630646aa5aeddca6a2d8fd1e1b`.
- Post-incident database hash: `909f80522fa78afadc5c5cd49c4ee71f965c7ffd24244d4b671c4bcd8ccc13b2`.
- Read-only inspection found only session `20261002101427` with `updated_at` at or after the phase start; its last update was `2026-10-25T09:59:27.038648+00:00`. No new session with this change's browser/controller test identities was present.
- The original snapshot contains hashes, not row contents. A complete logical comparison or exact restoration cannot be established from it. No restoration, deletion, or production-data rewriting was attempted.
- Root diagnostic logs were added/appended during the initial direct runs; the exact differences are retained in `protected-comparison.json`. They are not reported as unchanged data.

Subsequent isolated contract and control runs compare the workspace database hash before and after and require equality. They run an unchanged copy of the original legacy test, whose SHA256 is `9d769d369d106923aa7f8a7be9dccc1d11c1fb69165dd3596a5d5c41ef53d776`. Temporary source checkouts and their test data are automatically removed. Use the isolated runner for this phase, including the original legacy test. The incident remains an explicit review item; later successful isolation does not erase it.

## Source and caller inventory

`inventory.json` records **459 matching source lines** and **193 caller/helper-reference lines** with path, line number and text. Categories overlap: 11 compiler-directive lines, 3 declaration references, 26 import annotations, 282 compiler assertion sites, 123 Desk namespace annotations, and 29 domain-definition matches. These are lexical counts, not a semantic parser's totals. The two tracked `.ts` files are declarations (348 + 26 lines); `jsconfig.json` is 11 lines with strict/checkJs/noEmit. All eight bundled JavaScript files remain executable JavaScript.

| Domain description | Overlapping owners | Phase 4 disposition |
| --- | --- | --- |
| Token | `types.js` Token; `app-contracts.d.ts:45` Desk.Token widens required word and coordinate fields | Keep one descriptive canonical shape; validate optional runtime boundaries |
| Vocabulary / word | `types.js` VocabularyRow; WorkspaceTabs WordItem; Desk.Word and RowUpdate | Consolidate descriptions without losing occurrence metadata |
| Session state | `types.js` SessionState; Desk.AppState Omit/intersection; store selectedLemmas intersection | One state description; preserve acknowledged/current/in-flight ownership |
| Workspace card | WorkspaceTabs WorkspaceCard; Desk.Card | One documented card contract with public workspace API preserved |
| Update/provenance/config | `types.js` plus SSE event unions and Desk Config/Grammar/host additions | Retain useful descriptive documentation; remove compiler-only coupling |

| Rendering API / field | Production callers and competing path | Supported test callers / removal constraint |
| --- | --- | --- |
| SourceViewer.project | Its store subscription owns live source projection; app mounts it at line 2253 | Retain source identity/token/highlight behavior |
| SourceViewer.render | No tracked production call; alternate token-building renderer | static shell test at line 1154 asserts token output; preserve via canonical owner/delegation |
| TranslationView.render | No tracked production call; alternate renderer; production AppView.renderTranslatedText delegates card translation to WorkspaceTabs.updateActiveTabTranslation | static shell test at line 1159 asserts token output; preserve observable contract |
| LemmaTable.render / renderRows | No tracked production calls; renderRows already delegates to render; structural card rows use WorkspaceTabs.bindCardWordsToTbody; live cell updates use AppView.renderRow | static shell lines 1507 and 1543 cover selected-row filtering and sentinel sanitization; preserve those assertions |
| AppView | applyDeltas calls renderTranslatedText and renderRow; direct source/translation cleanup writers also remain | Phase 4 must resolve field ownership across bootstrap, terminal cleanup and card switching |
| WorkspaceTabs | init, switchToTab, updateSentences, updateActiveTabTranslation and bindCardWordsToTbody have actual app/internal callers | Public method behavior remains supported; structural row creation must not compete with live field projection |
| splitTextTokens | Identical private implementations in SourceViewer/TranslationView; equivalent regex in app tokenizeText | Consolidate at an existing canonical owner; no extra utility module just for cleanup |

No named SourceViewer/TranslationView/LemmaTable/AppView/WorkspaceTabs references were found under native `kardenwort-window`; native uses the bundled shell and generic public commands. This does not certify unknown external callers. A method used only by tests is not approved for deletion without retaining its behavior. The source/caller manifest identifies candidates; Phase 4 chooses the final ownership implementation.

`tests/test_intent_architecture.py::test_bundled_javascript_retains_check_annotations` explicitly asserts the superseded compiler directive. It must be reconciled traceably against the accepted plain-JavaScript requirement in Phase 4; this is distinct from changing behavioral assertions. It was not edited or represented as a compiler-independent check here.

## Runtime edits to retain or inspect separately

The full release diff is retained as `desk-release.diff`. `runtime-review.diff` compares `c481c9b..24d47ab` with JSDoc/full-line comments removed to expose executable candidates. This 4,727-line lexical review aid retains annotation parentheses and does not prove equivalence.

| Runtime edit family | Existing behavior / phase-4 constraint |
| --- | --- |
| required helper delegation, requireElement, nullable text/abort/parent checks, child argument validation | Preserve meaningful boundary checks; restore the incorrectly removed optional WorkspaceTabs guard separately |
| component mount/dispose handlers and SourceViewer store subscription/project | Preserve idempotence and unchanged-node projection |
| centralized SSEBridge and explicit parse/application diagnostics | Preserve one canonical stream path and observable failures |
| SET_LEMMA_SELECTION, shared membership, save snapshots/acknowledgment/retry | Repair convergence in the existing owner; do not revert selection semantics with annotations |
| undo/redo/playAudio scope, targetVal declaration, cleanupRowId and highlight loop variable names | Preserve executable scope/event fixes; do not perform a blanket commit revert |
| String/boolean coercions and null-to-empty/undefined adjustments | Inspect individually for actual boundary need before subtracting compiler-only changes |
| latest commit's duplicate fallback branches and event callback annotations | Keep useful removal of duplicate dispatch/sync calls; remove compiler-only callback annotations |

## Exact removal candidates and retained evidence

`removal-candidates.json` names **17 paths, 161 files, 52,914,808 bytes**. Nothing was removed. Phase 5 must revalidate dependency freedom, exact containment, nested reparse points and protected-file snapshots before any deletion.

| Exact path(s), relative to Desk | Ownership / proposed disposition |
| --- | --- |
| `results/20261024225159-typescript-cache` | Temporary compiler dependencies: 137 files / 51,217,922 bytes. Candidate disposal after no-toolchain verification |
| `results/20261024225159-type-baseline` | Nine copied frontend files / 647,960 bytes; generated comparison baseline. Candidate disposal after historical summary retained |
| `results/20261024225159-type-analysis.py` | Diagnostic comparison runner; candidate disposal |
| `results/20261025010043-close-types.cjs` | Compiler-based source mutation runner; candidate disposal; never executed in this phase |
| `results/20261024225159-typescript-baseline-with-host.txt`, `-baseline.txt`, `-changed-lines.txt`, `-comparison.txt`, `-current.txt` | Exact five compiler diagnostic outputs; full path list in JSON. Candidate disposal with concise provenance retained |
| `results/20261025005231-components-types-after.txt`, `-lemma-types-after.txt`, `-source-types-after.txt`, `-translation-types-after.txt`, `-translation-types-before.txt` | Exact five generated compiler reports; candidate disposal |
| `results/20261025005752-source-types.txt` | Empty generated compiler report; candidate disposal |
| `results/20261025094114-editor-types.json`, `results/20261025094114-editor-types.txt` | Editor compiler result metadata/empty output; candidate disposal after provenance summary |

Retain the earlier three-release audit, this report, source/tests/runners, inventories, execution logs/XML/JSON, whitespace/syntax evidence, protected baseline/comparison and the isolation incident record. Prior functional/layout reports, screenshots and spec history remain retained. Other results/scratch content, sessions/exports/SQLite and sidecars, active update directories and user files remain protected or uncertain; ignored/old/large is not a deletion rule. Phase 4 handles the three tracked compiler-integration files separately from ignored artifact cleanup.

No temporary compiler is invoked by these new runners. This is not yet the Phase 5 proof that all maintained setup/runtime/tests are independent of it. No canonical spec synchronization, archival, deployment or Git mutation was performed.

## Twelve-dimension evaluation

| Dimension | Signal | Evidence / limit |
| --- | --- | --- |
| Regression | FAIL | F1-F4 reproduced; original legacy assertion preserved |
| Orthogonality | WARN | Storage query assumes SQLite; domain and render owners overlap |
| Consistency | WARN | Optional host differs from declaration; descriptions widen coordinate contracts |
| Naming | PASS | Finding/owner/phase mapping explicit; identity and generation need separation |
| Production readiness | FAIL | TSV HTTP 500; clean browser can disagree with committed membership |
| Polishing | WARN | No UI styling changed; broad layout and physical-native verification deferred |
| Hardcoding | WARN | Native cadence is 200 ms; remount count used as window identity |
| Flexibility | FAIL | Supported TSV cannot participate in shared membership reads |
| Fragility | FAIL | Null host, missing backend attribute, teardown leaks |
| Scalability / performance | WARN | No broad workload measurement; polling expiry is demand-pruned at 60 s |
| Edge cases | FAIL | Save/event/reversal/retry and repeated lifecycle interleavings fail |
| Correctness | FAIL | Membership divergence and invalid-session empty success; initial test isolation changed protected DB |

## Verification limits and review boundary

All eight bundled JS `node --check` commands passed (exit 0). New Python files compile. Desk release/working-tree and native release whitespace checks passed (exit 0). Production files and the original legacy test have no working-tree diff against HEAD. This phase adds tests, read-only inventory/evidence tools, an isolated test runner and this report; it changes only Phase 1 OpenSpec task checkboxes in the vault.

Exact validation commands also included `python -m py_compile scripts/release_boundaries_inventory.py scripts/run_release_boundary_contracts.py tests/test_release_boundary_contracts.py`, `git diff --check 1ac6727..24d47ab`, `git diff --check`, and native `git diff --check 1e129c4..373a595` plus its working-tree check. `deliverables.json` records the four added Desk files, line counts and hashes. The vault task diff is eight insertions / six deletions (six checkbox replacements and the evidence/incident note); apply status is 6/36 complete. No later-phase task is checked.

The complete applicable suite, broad configuration/layout matrix, final architecture/complexity audit, cleanup and physical MSHTML are not certified. Rollback of these source additions is user-managed; no database rollback is offered from a hash-only baseline. Review the protected-data incident explicitly before proceeding. Phase 2 must implement the optional-host and backend membership boundaries and retain the failing assertions.

The phase stop is required both by the user's global instructions and by [test-and-apply-protocol](C:/Users/voothi/.gemini/config/skills/test-and-apply-protocol/SKILL.md): "Stop at designated phase boundaries to allow the user to review code and perform git commits."
