# Release boundary repairs: Phase 4

Change: `20261025101246-fix-release-boundaries-and-contract-duplication`. Evidence: `results/20261025154840-release-boundaries/`.

Phase 4 is complete; 25/36 tasks are checked. It removes maintained TypeScript integration and the alternate component renderers. The five final suites pass: 154 ownership/layout, 95 convergence, 50 watchdog/toolbar, 80 storage/export, and 27 boundary-contract executions. These 406 executions overlap; they are not 406 distinct tests. Phase 5 artifact retirement and Phase 6 complete-release verification remain pending. Stop for review and a user-managed commit before Phase 5.

Phase signal: **YELLOW**, because physical MSHTML, broad performance, and the complete release matrix are unverified. Overall release signal remains **RED** because the earlier protected-data incidents remain unresolved. Passing this phase does not establish data restoration or complete-release readiness.

## Exact scope

The latest empirical release anchor is `20261025154621` in `U:/voothi.vault/multi-repo-sync.md`: Desk `3cdf08fdb1e26cbc52c0d12deab39a9bcd6c279e`, native `d27700d39e6f589081f8ca4c994f4051fece65c0`. Both started clean. Review this phase as Desk `3cdf08f..working tree`; the native repository has no phase changes. The governing audit ranges remain Desk `1ac6727..24d47ab` and native `1e129c4..373a595`. Read-only diff evidence is retained in `working.diff`, `working-stat.txt`, and the two `governing-*-stat.txt` files.

No Git mutation, package installation, deployment, archive, conversation edit, compiler invocation, or Phase 5 compiler-cache deletion occurred. `release-boundaries-phase4-spec-sync.md` is a reviewable canonical-spec synchronization preview. Canonical specs are unchanged, verified against its before hashes.

## Removed inventory and retained behavior

| Item | Result |
| --- | --- |
| `web/js/app-contracts.d.ts` | Removed, 348 lines. Parallel Desk domain/host schemas are no longer maintained. |
| `web/js/host.d.ts` | Removed, 26 lines. No declaration reference remains in bundled scripts. |
| `web/js/jsconfig.json`, eight `@ts-check` opt-ins | Already removed in the user's preceding `3cdf08f` commit; not counted as deletions in this worktree. |
| Compiler-oriented comments | 489 comment edits across eight scripts, including assertions, imported annotations, declaration references and aliases. The initial removal stage preserved all eight runtime ASTs; `annotation-removal.json` records that stage separately from later runtime work. |
| Domain descriptions | `types.js` owns the single descriptions of Token, VocabularyRow, provenance, update/session state, workspace cards/navigation and save/history records. Workspace's WordItem duplicate was merged into VocabularyRow. Private event/store records stay local. No replacement schema generator was introduced. |
| Alternate SourceViewer/TranslationView renderers | Removed their HTML/token-splitting algorithms and private escaping helpers. Supported `render` methods remain thin adapters. |
| Alternate LemmaTable row builder | Removed. `render`/`renderRows` adapt the legacy matrix representation to the existing workspace row owner. Explicit token/sentence coordinates and selected IDs are preserved. |
| Repeated translation/provenance writers | Global projection, card activation, compatibility calls and terminal cleanup delegate to the same translation component and metadata helpers. Tokenization and first-load provenance use the shared metadata owner. |
| Runtime repairs from annotation-era work | Store/SSE/type-documentation runtime ASTs equal HEAD; optional-host guards, save convergence, command ordering, event handling and variable-scope behavior remain exercised by unchanged contracts. No blanket commit reversal was used. |

The final five-suite verification uses the source hashes recorded in `*-review-final-execution.json`. Earlier `canonical` runs precede the final compatibility-adapter correction and the two mode-documentation comments. Their original hashes and `post-test-documentation-only.json` remain historical evidence; they are not substituted for the final runs.

## Rendering and lifecycle ownership

| Field or boundary | Owner and callers |
| --- | --- |
| Source text replacement | `SourceViewer.project`, called by its authoritative store subscription and terminal projection. The public raw-text `render` method escapes text then delegates. Unchanged content retains token nodes. |
| Translation DOM replacement | `TranslationView.project`. AppView prepares global translation state; WorkspaceTabs prepares active-card content/context; both delegate. Repeated identical markup/context preserves nodes. A terminal update with no replacement preserves ready text; terminal pending text clears through the same owner. |
| Translation token construction | Existing `tokenizeTranslation` and canonical `tokenizeText`; no component splitter copy remains. Its existing index/event/highlight hooks are reused. |
| Translation provenance/title | `applyTextProvenance` / `projectTextProvenance`. Metadata updates do not require replacing unchanged token nodes. |
| Card table construction | Existing `WorkspaceTabs.bindCardWordsToTbody`. LemmaTable's public matrix adapter supplies records and explicit selection input. Existing AppView cell projections retain the live-update role, without an alternate component row algorithm. |
| Compatibility selection | Supplied selected IDs enter the existing lemma-selection setter before canonical rebinding, preventing stale shared membership from overriding the explicit input. Normal production rebinding supplies no override. No second persistence/history cache is added. |
| Mount/dispose/remount | Existing canonical component bindings remain idempotent. The new cases count one active context binding; unchanged source/translation nodes, focus and active edits are checked separately. |
| Workspace metadata | Bootstrap values take precedence. Legacy host metadata sources remain supported. Missing language logs and raises at resolution; missing text mode logs and stays unresolved instead of inventing `single`. Valid configured language/mode behavior is retained. |

The compatibility API remains supported because unchanged tests call it. No observable scenario was removed or redirected merely because there is no production caller. The only existing test changed is the structural architecture test that required first-line `@ts-check`: accepted instruction `20261025101603`, D1 and task 4.4 explicitly supersede that prescription. It now checks absence of compiler coupling. Existing behavioral assertions remain unchanged; `test-integrity.json` verifies the original legacy/static-shell/intent/save-convergence contracts against HEAD after normalizing checkout line endings. Isolated copies of the original legacy file are also byte-identical to the working file in every execution record.

## Executed proof

All suite commands used disposable filesystem checkouts, isolated database/results paths, disabled live-controller defaults, the external-SQLite audit guard, and `SSLKEYLOGFILE=''`. Real storage/controller/browser implementations run; only external transport completion and browser fixture setup are controlled where the existing contracts require them.

Common command prefix:

```powershell
python scripts/run_release_boundary_contracts.py <suite> --run-label review-final --evidence-dir results/20261025154840-release-boundaries
```

| Suite | Result | Duration from pytest | Main evidence |
| --- | --- | --- | --- |
| `ownership` | 154 passed, exit 0 | 116.13 s | Original public rendering contracts; 13 new compatibility/lifecycle/boundary cases; grammar parity; 17 pairwise preference rows; four themes at wide/100% and narrow/125% zoom. |
| `convergence` | 95 passed, exit 0 | 113.66 s | Selection/save/event/ack/retry/remount ordering, current focus/edit intent, command save barriers, independent native consumer boundaries. |
| `watchdog` | 50 passed, exit 0 | 68.54 s | First-load, terminal normalization, offline/reconnect, focus/status recovery, persistent selection stream and toolbar behavior. |
| `storage` | 80 passed, exit 0 | 57.13 s | SQLite/TSV restore, exact targeted/merged/card-scoped export identity, clean translation and required-asset failures. |
| `contracts` | 27 passed, exit 0 | 52.25 s | Original optional legacy-host regression and real membership/save/controller boundaries. |

Each `*-review-final-execution.json` includes the complete pytest command, exit code, hashes of all eight scripts, controller/dispatcher/native host/test hashes, unchanged root-database hash, zero external SQLite attempts, and successful temporary-checkout removal. JUnit XML and complete logs are retained under the same prefix. Layout assertions compare source, translation, table and toolbar rectangles within 0.01 pixels; long translation content scrolls within the reserved box. This measures those cases, not every possible layout or physical display.

Additional commands and results:

```powershell
python results/20261025154840-release-boundaries/architecture-check.py
node U:/voothi/20260406202446-openspec/bin/openspec.js validate 20261025101246-fix-release-boundaries-and-contract-duplication --strict
git diff --check
```

All exit 0. Architecture evidence reports eight successful `node --check` executions, 44 targeted functions/callbacks with maximum cyclomatic complexity 7, unique shared shape definitions, no private component token splitter, and no new module import edges. Existing large init/applyDeltas/tokenizer/table/card coordinators retain legacy complexity; their narrow delegation changes do not certify those entire bodies as <= 7. There is no claim of a wholesale architecture rewrite.

## Negative evidence and harness corrections

- The first compatibility run collected no tests because the new suite list included a nonexistent grammar test filename. The path was corrected; the failure log remains. No application or assertion change was made for that collection error.
- An early ownership run had two Chromium `ERR_UNSAFE_PORT` failures on Windows-assigned fixture ports 5060/5061. The isolated browser fixture now explicitly permits Chromium's blocked ephemeral candidates. Assertions and production launch settings are unchanged.
- An overbroad attempt to require session metadata during unrelated initialization failed watchdog tests. It was replaced with scoped workspace resolution/diagnostics, preserving supported partial-host initialization. Final watchdog and metadata cases pass.
- The new explicit-coordinate/selection case first failed because canonical rebinding restored stale membership. Production selection ingress was corrected; the expected values remain unchanged.
- One 113-pass run failed during Windows temporary-checkout removal, so it is not counted as a successful execution. The bounded runner cleanup now retries the owned temporary path after handle-release races. Its leftover checkout was resolved, checked for reparse points, and removed using native PowerShell. Final runs all report removal success.

These traces are retained. Earlier passing iterations are not substituted for the final source-hash-matched results.

## Protected data and retained artifacts

Every isolated run preserved the workspace database SHA-256 `957b9fa00364949c0af0c0e13ff919505c2ce7adea1652ac7623d9fa23e2628e`. The complete 22,814-file / 490,540,973-byte comparison has zero changed, removed or added files and is recorded in `protected-before.json`, `protected-after.json` and `protected-comparison.json`; the current phase evidence directory is excluded from its own snapshot. Compiler caches and older uncertain outputs remain for Phase 5's bounded manifest review.

Retained deliverables are this report, `release-boundaries-phase4-spec-sync.md`, the new browser regression file, runner improvements, updated source/documentation, OpenSpec task states, and the phase evidence directory. This phase's isolated test checkouts are removed. Historical reports and conversations remain intact. Earlier database/update-file incidents documented in Phases 1/2 are not restored or silently reclassified by this phase's unchanged hashes.

## Twelve-dimension review

| Dimension | Signal | Finding |
| --- | --- | --- |
| Regression | PASS | Unchanged observable contracts and new compatibility cases pass on the final runtime sources. |
| Orthogonality | PASS | Compatibility adapters delegate; reads, persistence and rendering keep existing boundaries. |
| Consistency | PASS | Shared descriptions have one owner; explicit occurrence coordinates and export scope remain tested. |
| Naming | PASS | Projection, compatibility adaptation, provenance and session-resolution helpers describe their roles. |
| Production readiness | WARN | Physical MSHTML and post-cleanup whole-release verification remain pending. |
| Polishing | PASS | Measured box invariants, pending states, focus, metadata and tooltips pass the executed browser cases. |
| Hardcoding | PASS | No new language/provider default or production port policy; browser port allowance is isolated harness setup. |
| Flexibility | PASS | Plain scripts and ordinary JSDoc need no build/compiler setup; documented public methods remain available. |
| Fragility | WARN | Required helpers and metadata diagnose failures; large inherited coordinators still carry legacy complexity. |
| Scalability/performance | WARN | Unchanged nodes avoid repeat reconstruction; broad performance and physical-host timings were not measured. |
| Edge cases | PASS | Escaped raw text, terminal no-replacement, pending preservation, explicit selection/coordinates, missing metadata and remount cases pass. |
| Correctness | PASS for this slice | Rendering, selection/save convergence and actual export boundaries pass; unresolved historical data incidents keep overall release RED. |

Rollback is user-managed: review/revert the scoped Desk worktree changes or the eventual phase commit while preserving session data and retained evidence. Native HEAD is unchanged in this phase. Phases 5/6, canonical synchronization application, archival and deployment are not implied by this report.
