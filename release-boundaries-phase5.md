# Release boundary repairs: Phase 5

Change: `20261025101246-fix-release-boundaries-and-contract-duplication`. Action: `20261025165138`. Evidence: `results/20261025165138-release-boundaries/`.

**Tooling retirement and protected-data verification are complete; 30/36 tasks are checked. Overall release signal remains RED.** The main post-removal runs report 405 passes and one failure across overlapping suites. The Update command failure repeats in a separate four-case run and remains unresolved for Phase 6. This is not a clean full-suite result. Stop for user review and a user-managed commit before Phase 6.

## Scope and exact baseline

Release `20261025164507` in `U:/voothi.vault/multi-repo-sync.md` anchors Desk `70feb63b73a386ce35d9f5975837a5f69dd2eb7d` and native `d27700d39e6f589081f8ca4c994f4051fece65c0`. Both started clean. Review Desk `70feb63..working tree`; native has no Phase 5 changes. The governing audit ranges remain Desk `1ac6727..24d47ab` and native `1e129c4..373a595`.

The only maintained executable change is the isolated verification runner's compiler-access guard and result fields. No production JavaScript, backend or native implementation changed. All eight JavaScript files match HEAD, with successful syntax checks recorded in `no-runtime-change.json`. No package was downloaded or installed; no compiler, archive, deployment or Git mutation was performed. Canonical specs and conversations were not edited.

## Exact retirement manifest

All 17 targets were positively identified in Phase 1 and re-inspected before removal. The cache contains TypeScript 5.6.3; the nine-file baseline is a generated analysis copy; the Python/CJS files are one-off analysis/source-rewrite runners; remaining files are compiler diagnostic outputs. These are unrelated to saved sessions, exports, databases or active updates.

| Removed path | Files | Bytes | Purpose |
| --- | ---: | ---: | --- |
| `results/20261024225159-type-analysis.py` | 1 | 1581 | one-off diagnostic comparison runner |
| `results/20261024225159-type-baseline` | 9 | 647960 | generated release JavaScript/declaration analysis copy |
| `results/20261024225159-typescript-baseline-with-host.txt` | 1 | 350566 | retired compiler diagnostic output |
| `results/20261024225159-typescript-baseline.txt` | 1 | 424598 | retired compiler diagnostic output |
| `results/20261024225159-typescript-cache` | 137 | 51217922 | temporary TypeScript 5.6.3 package/cache |
| `results/20261024225159-typescript-changed-lines.txt` | 1 | 694 | retired compiler diagnostic output |
| `results/20261024225159-typescript-comparison.txt` | 1 | 160 | retired compiler diagnostic output |
| `results/20261024225159-typescript-current.txt` | 1 | 260526 | retired compiler diagnostic output |
| `results/20261025005231-components-types-after.txt` | 1 | 0 | retired compiler diagnostic output |
| `results/20261025005231-lemma-types-after.txt` | 1 | 604 | retired compiler diagnostic output |
| `results/20261025005231-source-types-after.txt` | 1 | 570 | retired compiler diagnostic output |
| `results/20261025005231-translation-types-after.txt` | 1 | 212 | retired compiler diagnostic output |
| `results/20261025005231-translation-types-before.txt` | 1 | 4728 | retired compiler diagnostic output |
| `results/20261025005752-source-types.txt` | 1 | 0 | retired compiler diagnostic output |
| `results/20261025010043-close-types.cjs` | 1 | 4629 | one-off source rewrite runner |
| `results/20261025094114-editor-types.json` | 1 | 58 | retired compiler diagnostic output |
| `results/20261025094114-editor-types.txt` | 1 | 0 | retired compiler diagnostic output |

Total: **161 files, 52,914,808 bytes (50.46 MiB)**. Every path is absent. `removal-manifest.json` retains every removed file's original path, byte count and SHA-256, ownership, purpose and replacement evidence. `validated-targets.json` and `removal-execution.json` record execution. All absolute targets were direct children of this workspace's `results` directory. Native PowerShell validated every target and descendant for containment, reparse points, exact file counts, sizes and hashes before deleting any target. Removal used `Remove-Item -LiteralPath` within that same shell, with recursion only for the two directory targets.

Historical diagnostic provenance survives in `compiler-history.json`, the immutable Phase 1 inventory and the existing review reports. The old baseline outputs reported 1,400 diagnostics without host declarations, 1,159 with the same host declarations, and 1,140 current diagnostics; the retained comparison recorded zero net-new occurrences and zero changed-line diagnostics, while its separate changed-lines listing contained six lines. These were distinct historical outputs and are not reconciled or presented as current verification. The later editor record reported compiler 6.0.3, zero diagnostics and exit 0. Empty diagnostic outputs are recorded as empty outputs, not independent proof of a successful command. The old rewrite runners are removed, not retained as executable provenance.

## Retained and uncertain content

`retained-files.json` names all 22,766 surviving files in the comparison, with byte counts, hashes and preservation categories: 113 protected data/configuration files, 207 review-evidence files, and 22,446 uncertain pre-existing outputs left untouched. Classification as uncertain is a reason to preserve a file, never a reason to remove it. The historical phase reports and evidence directories remain intact. The exact manifest does not authorize blanket cleanup of `results` or `scratch`.

Saved sessions, favorites/exports, configuration, database/sidecars, active channels and old user/probe files remain protected. Read-only conversations were not written. This phase retains its manifest, compiler-history summary, dependency scan, guard proof, suite logs/XML/execution metadata, file comparisons and this report as review evidence. Temporary isolated checkouts from the five main runs were automatically removed. One diagnostic run failed before cleanup metadata was written; its leftover directory was later verified empty and removed with native PowerShell. The wrapper cleanup traceback was not retained; its complete pytest failure log is retained. No test or assertion was changed to obtain a pass.

## Verification without compiler tooling

The tracked setup/runtime/configuration/test/script scan in `dependency-scan.json` found no unclassified compiler dependency. Eleven remaining references are maintenance inventory/rejection patterns or architecture assertions requiring absence of compiler coupling. A separate scan found no compiler invocation references in existing scratch Python/JavaScript/CJS/config files. Generated TypeScript declarations/config/cache/runner paths are absent after retirement.

The isolated runner now rejects attempted reads/enumeration of the retired cache/baseline/runners or a `node_modules/typescript` directory, and attempted compiler/package-runner subprocess launches. It records violations and makes any such attempt fail verification. Three negative controls prove rejection of project-cache reads, editor-compiler reads and an `npx` launch before it executes. The first process-control probe exposed Windows audit events with an omitted executable field; the guard now derives it from the command line in that case. The corrected guard passes all three controls and has cyclomatic complexity 7. This audit covers Python verification processes and their Python children; it is not claimed as a system-wide filesystem trace of every native/browser process.

`install.py --list` and `kardenwort_desk.py --help` pass in isolated checkouts both before and after removal, under the guard, with no external SQLite access and automatic checkout removal. This verifies setup listing and CLI startup, not actual SendTo shortcut installation. The installer code and dependency manifests were inspected; no compiler dependency is prescribed. No user-wide editor installation or settings were altered.

Each main suite used:

```powershell
python scripts/run_release_boundary_contracts.py <suite> --run-label retired --evidence-dir results/20261025165138-release-boundaries
```

| Suite | Result | Exit | Duration |
| --- | --- | ---: | ---: |
| ownership/layout | 154 passed | 0 | 173.55 s |
| convergence | 94 passed, 1 failed | 1 | 149.39 s |
| watchdog/toolbar | 50 passed | 0 | 91.63 s |
| storage/export | 80 passed | 0 | 73.11 s |
| original boundary contracts | 27 passed | 0 | 60.51 s |

All five execution records report zero compiler-tooling attempts, zero external SQLite attempts, unchanged workspace database SHA-256 `957b9fa00364949c0af0c0e13ff919505c2ce7adea1652ac7623d9fa23e2628e`, unchanged original legacy-test copy hashes, current production/test hashes and successful isolated-checkout removal. The full records, commands and XML are retained under `*-retired-*`. These are focused runs, not the complete Phase 6 suite. Native/controller cases execute their existing real boundaries; no new physical MSHTML verification is claimed.

Additional successful checks: eight `node --check` executions; standard Python parsing of the runner and generated audit guard; guard complexity <= 7; `git diff --check`; strict OpenSpec validation. Runtime hashes and exact working diff are retained separately. There is no new production import edge or runtime cache.

## Repeated failure requiring Phase 6 investigation

`test_command_waits_for_save_and_reconciliation[chromium-onUpdateClick-/session/status]` observed zero status commands at its existing five-second deadline after the held save and membership read were released. The full convergence run failed this assertion. An isolated four-command diagnostic repeated the same failure: 1 failed, 3 passed, 91 deselected. It is not classified as a concurrency/load flake and an isolated pass is not substituted for the failed run.

The retained trace shows DeepL authentication failure, fallback to Google and a Windows connection-aborted trace. The fixture seeds word translations but no completed sentence translation and intercepts `/session/status`, not the automatic `/session/retext` request. Inspection shows Update issues Re-text when sentence translation is missing and waits for its response before status. This establishes an uncontrolled provider dependency in that test path; it does not prove the complete cause of the five-second failure. Compiler-tooling attempts are zero, and all production JavaScript content matches HEAD, allowing checkout line endings. No provider policy, timeout, fixture or functional assertion was changed in this cleanup phase. Phase 6 must distinguish the production Update behavior from fixture/transport assumptions and preserve the save/reconciliation barrier.

## Protected-data comparison

The before snapshot covers 22,927 files / 505,098,562 bytes across data, favorites, results, scratch, configuration and the root debug log, without following junctions. Only this phase's evidence directory is excluded from its own snapshot. The after snapshot covers **22,766 files**. Exactly **161 manifest files / 52,914,808 bytes** were removed. There are **zero unexpected removals, missing removals, changed files or additions**. All remaining hashes match. `protected-before.json`, `protected-after.json`, `protected-comparison.json` and the retained check script provide the full comparison.

This comparison does not restore or recertify the earlier data incidents described in Phases 1/2. Their historical status is unchanged.

## Twelve-dimension review

| Dimension | Signal | Finding |
| --- | --- | --- |
| Regression | WARN | Four focused suites pass; the repeated Update/status assertion remains unresolved. |
| Orthogonality | PASS | Retired analysis tools are separate from application state/persistence; guard changes are confined to isolated verification. |
| Consistency | PASS | Exact manifest paths and hashes govern removal; runtime coordinates and API shapes are unchanged. |
| Naming | PASS | Guard, manifest and preservation categories describe their purpose. |
| Production readiness | FAIL for release | Repeated functional failure, prior data incidents and Phase 6 verification prevent release readiness. |
| Polishing | PASS within executed cases | Ownership/layout/toolbar contracts pass; no UI/CSS source changed. |
| Hardcoding | PASS | Literal paths specify the exact retirement/rejection policy, not a new runtime language/provider default. |
| Flexibility | PASS | Setup/CLI and focused browser/storage behavior require no maintained compiler toolchain. |
| Fragility | WARN | Guard rejection is proven; one diagnostic cleanup trace is unavailable and the Update path depends on a provider response. |
| Scalability/performance | WARN | Removed 50.46 MiB; broad performance is unmeasured and concurrent run timings are not benchmarks. |
| Edge cases | WARN | Compiler-access and containment controls pass; the repeated five-second command failure remains an explicit negative result. |
| Correctness | PASS for retirement; WARN for application closure | Exact deletion and preserved-data invariants pass; the application has not received complete release sign-off. |

Rollback remains user-managed. Reverting an eventual Phase 5 commit can undo runner/report changes; these ignored temporary compiler artifacts were never Git-tracked and are intentionally retired. No automatic download or restoration is performed. Keep application data and retained evidence intact. Canonical synchronization application, archival, deployment and Phase 6 implementation remain separate actions.
