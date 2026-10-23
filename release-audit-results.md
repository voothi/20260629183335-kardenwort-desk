# 20261023223509: Release audit repair results

Change: `20261023214525-fix-release-audit-regressions-and-failfast-gaps`.
Comparison: release baseline `56f8696` to release target/current HEAD `2f6839e`, including these working-tree repairs. The user authorized continuing through the remaining phases without intermediate review stops. Git remains read-only; no commits, archive, deployment, package installation, or vault edits were performed. Vault task checkboxes remain unchanged because the vault is strictly read-only.

## Implemented behavior

| Phase | Result |
|---|---|
| 1: Baseline and reproductions | [Historical phase 1 inventory](release-audit-phase1.md) maps every journey and separates inherited defects, new regressions, and requested corrections. |
| 2: State and filtering | Store and legacy consumers share authoritative state; preference commands dispatch through it. SSE applies each event through one canonical update command. Initialization no longer overwrites preferences or copies another card's selections. Selected/Lemma survive status/SSE updates and real reloads; active card restores from session storage. |
| 3: Persistence and lifecycle | Completed selection gestures automatically save selection-only deltas independently of text-edit autosave. Saves serialize and coalesce; acknowledgments apply only submitted selections/history. New changes remain pending. Update/Re-text/Re-word/export drain required saves before proceeding; failure retains dirty state and stops the dependent action. Native acknowledgments now use the submitted snapshot, and native hosts without fetch can submit selection changes through the controller bridge. |
| 4: Themes and loading | All 37 baseline theme keys map explicitly to CSS properties through one shared desktop/lookup resolver. Unsupported keys cannot create declarations; unsafe values fail. Selected/Lemma use invariant weight 500; Delete shares neutral command styling. Text indicators use compact 11px/21px CSS treatment. Active text boxes reserve their measured layout size, with internal scrolling for complete long/multiline content; viewport/card changes establish a new reservation. |
| 5: Required assets | Desktop and lookup reject missing HTML/CSS/JavaScript with file-specific errors, including warm renders. Required reads cannot return empty HTML, skip scripts, or assemble degraded lookup pages. Optional favicon/static missing-file behavior remains separate. |
| 6: Re-text boundaries | SQLite sentence records are acquired before provider calls, cache warming, TSV reconstruction, or persistence. A failed read becomes a chained safe DESK_FAILED error. A real broken SQLite table test proves zero provider calls, unchanged database/session state, no TSV creation, and no update event. |
| 7: Grammar and whitespace | Grammar serializes only the eleven canonical snake_case keys. Browser consumers use them, including the excluded `-ent` contract for Moment. Whitespace cleanup is confined to the audited/modified files. Both ordinary and release-baseline diff checks pass. |
| 8: Verification and audit | Existing suites, new integration/browser contracts, shared-shell matrix, native headless FSM, screenshots, measurements, exact diff ranges, and baseline reproduction of the inherited environment failure are recorded below. Physical native-window sign-off remains outstanding. |

## Proof and artifacts

The main verification commands are:

```powershell
$env:SSLKEYLOGFILE=''
python -m pytest tests -q --tb=short -p no:cacheprovider --basetemp=scratch/20261023223509-final-full
python scripts/release_audit_baseline.py 20261023223509-release-audit
& 'C:\AHK\AutoHotkey_2.0.18\AutoHotkey64.exe' /ErrorStdOut 'U:\voothi\20240411110510-autohotkey\kardenwort-window\tests\test_fsm_headless.ahk'
git diff --check
git diff --check 56f8696
```

SSL key logging is disabled only in the test process because the inherited environment points it at an inaccessible file. No persistent environment setting was changed.

- Focused suites: **293 passed in 160.70s**, exit 0, including state/filter, toolbar, grammar, asset, SQLite sentence/controller, architecture, and configuration checks. Log: `results/20261023221555-release-audit/final-focused.txt`.
- Final combined new/overview contracts: **75 passed in 57.37s**, exit 0. Log: `results/20261023223509-release-audit/contracts.txt`.
- Final full suite: **2,770 passed, 1 failed in 505.15s**, exit 1. The sole failure is the baseline-confirmed filesystem-free child-spawn fixture described below.
- Native headless FSM: **66/66 passed**, exit 0. This executes production FSM handlers in headless test mode; it is not physical MSHTML interaction evidence.
- Shared-shell pairwise matrix: **17 cases passed**, spanning all **88 factor pairs**. Native/lookup-labelled rows test the shared projection in Chromium. Actual lookup theme rendering has an additional **4 passing computed-color assertions**.
- Geometry: eight dark/light/white/compact and wide/narrow/zoom cases preserve measured source, translation, table, and toolbar boxes within **0.01px** through long multiline translation updates; complete text remains scrollable.
- Real browser/controller/SQLite round trips cover click, drag, select-all, supported checkbox commands, deselection, and isolation from an uncommitted cell edit. A deterministic select -> switch -> SSE -> F5 -> delayed acknowledgment -> reload case verifies separate overview/child records, acknowledgment ordering, and restored active card.
- Both `git diff --check` and `git diff --check 56f8696`: **exit 0**. New files also receive individual no-index whitespace checks.

Full-suite results are in `results/20261023221555-release-audit/final-full-suite.txt`. The initial broad run found 14 failures; targeted investigation distinguished obsolete contracts from defects and drove the subsequent repairs. Python compilation, JavaScript syntax checks, the pure theme module's import boundary (re only), and a custom light-theme translation hover probe also pass.

Reproducible visual artifacts:

- [Current capture](results/20261023223509-release-audit/current.png), [baseline capture](results/20261023223509-release-audit/baseline.png).
- [Computed metrics](results/20261023223509-release-audit/styles.json), [matrix definition](results/20261023223509-release-audit/matrix.json), [exact diff ranges](results/20261023223509-release-audit/diff-ranges.json).

The current diagnostic text indicator is **77 x 21px**, with **11px** label typography; the baseline fixture is **500 x 20.796875px**, with **13px** typography. Before repair, the target text indicator used 16px typography and measured 500 x 25.59375px. Current Selected active weight is 500; baseline is 600. Current Delete hover text follows the neutral command color instead of baseline red. These screenshots measure explicit pending fixtures, while separate browser cases prove update geometry.

`results` and `scratch` are already Git-ignored. The report and capture script make the evidence reviewable and reproducible without staging generated files.

## Contract migrations and scope

Existing tests were migrated where the requested contracts expressly replace earlier behavior: Selected 600 -> invariant 500; danger Delete -> neutral; oversized text indicators -> compact; camelCase grammar setup -> snake_case; command request counts -> a required save followed by one command; global cross-tab selection propagation -> independent overview/child intent. These changes retain meaningful value, identity, context, styling, request-order, and error assertions rather than removing failed checks.

The same-card and cross-card persistence checks also preserve unrelated dirty edits. A save acknowledgment cannot mark later input clean. Restore testing uncovered and fixed an additional initialization write that replaced the restored child's selected IDs with the server-rendered overview's IDs.

Behavioral changes are in the desktop/controller/grammar/theme module, shared JS/CSS, and the native bridge. Eleven other tracked files have only trailing-whitespace/EOF cleanup: `config.ini`, the progressive/sentence-index/SQLite-isolation/translation-bias/translation-chain/translation-fast-fail/UI-DOM/UI-interaction/wordfill-SQLite tests, and `web/css/variables.css`. No configuration value changed.

The native repository changes only `kardenwort-window/kardenwort-window.ahk` (31 added, 9 removed lines at inspection): snapshot capture/acknowledgment, retained dirty-state feedback, and the controller selection-persistence bridge. Inspect it with a per-command safe.directory option if the sandbox reports repository ownership; no Git configuration write is required.

## Remaining verification limits

The filesystem-free multi-window child-spawn test fails in this environment because its temporary workspace cannot locate a sibling AutoHotkey repository. Running that same test against baseline Python loaded directly from `git show 56f8696:kardenwort_desk.py` reproduces the same missing `--restore` launch assertion. Evidence: `results/20261023221555-release-audit/baseline-fs.txt`. Its storage assertions pass before the child-launch assertion; the failure is not attributed to this repair.

Physical native-window interaction, native provider delivery, and manual visual review on a real MSHTML window remain unverified. Native source/FSM tests and Chromium bridge/projection cases are explicitly distinguished from that coverage. The German defaults, root route, GoldenDict column order, provider configuration, database schema, and unrelated packages remain outside this repair.

For physical native-window verification only: "Code modified. Test execution deferred to user." The automated browser, SQLite, endpoint and headless-native checks above were executed directly.

## Traffic light: YELLOW

| Dimension | Result | Evidence / limitation |
|---|---|---|
| Regression | PASS with environment caveat | New reproductions repaired; inherited child-launch fixture failure reproduced at baseline. |
| Orthogonality | PASS | Selection-only writes preserve text-edit policy; overview and child contexts remain separate. |
| Consistency | PASS | Shared state and canonical grammar keys; one update entry point. |
| Naming | PASS | Explicit snapshot/acknowledgment commands and baseline-derived theme key map. |
| Production readiness | WARN | Required asset/read failures are explicit; physical native host still requires sign-off. |
| Polishing | PASS with native caveat | Compact indicators, neutral Delete, invariant weight, measured stable text regions. |
| Hardcoding | PASS | Theme keys are allowlisted; measured layout reservation avoids arbitrary global fixed text heights. |
| Flexibility | PASS | Shared theme resolver and shell; existing commands/storage/schema preserved. |
| Fragility | PASS within executed cases | Delayed acknowledgments, failed saves, reloads, warm missing assets and real SQLite read failure covered. |
| Scalability/performance | WARN | No new benchmark claim; required files are reread per render to detect deployment damage. |
| Edge cases | PASS within executed matrix | Empty filter, repeated preference hydration, late input, context switch, long text, der/m./Moment/Student. |
| Correctness | PASS within executed contracts | Real SQLite and endpoint/browser checks; native physical behavior remains outside measured proof. |

Ready for final user review. Archive, deployment, and Git writes have not been performed.
