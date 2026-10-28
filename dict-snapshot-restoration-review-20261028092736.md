20261028092736

TRAFFIC LIGHT SIGNAL: RED. The current Dict article does not preserve the requested snapshot's appearance or functional scope. The previous repair verified the new desktop-style article instead of detecting that this article was itself the regression. This review establishes the restoration boundary; production rendering has not yet been changed.

The reference is the actual Git tag `20261013101000-snapshot-desk`, resolving to Desk `56f8696`, with AutoHotkey `e0f4158` in release history. Current release 20261028091417 contains Desk `c2e11f3` and AutoHotkey `6bdbce0`. The empirical comparison is `56f8696..c2e11f3`: 75 files, 29,676 insertions and 13,514 deletions. The user's separate pending `config.ini` change is retained. No Git mutation was performed.

| Reproduced difference | Snapshot reference | Current release |
| --- | --- | --- |
| Article composition | Dedicated Dict document, configured sections and columns | Desktop `web/index.html`, desktop components, application runtime and global styles |
| Compact three-column article, enabled server, 1280px | Selection 40px; text columns approximately 387/342/491px | Selection 843px; each text column approximately 120px |
| Visible article actions, enabled server | Checkboxes and one Export to Anki button | Nine desktop toolbar buttons; the original article export area is discarded during slot extraction |
| Offline article | No buttons, application script or token | Nine-button desktop toolbar, including enabled local controls, and bundled application runtime |
| Compact typography | Inherits host font and size; cells use 2px/4px padding | Application font; 11px headings, 13px table text, 8px heading padding |
| Two-row HTML payload | 5,108 bytes offline; 11,213 bytes enabled | 751,991 bytes offline; 752,162 bytes enabled |
| Embedding into an existing styled document | Host remains Georgia 17px with its original background | Host changes to application font 13px with a white background |

These are measured fixture results, not estimates for every document. The 843px failure predates the latest repair and remains present. The surrounding-host style change reproduces independently of JavaScript. The embedding fixture is a browser demonstration of CSS leakage, not a physical GoldenDict certification.

Snapshot reference:

![Snapshot Dict reference](results/20261028092736-dict-snapshot-audit/snapshot-compact-1-3.png)

Current regression:

![Current Dict regression](results/20261028092736-dict-snapshot-audit/current-compact-1-3.png)

| History point | Effect |
| --- | --- |
| `56f8696` | Dedicated article renderer with compact/scoped CSS; optional small tagging/export script. It already uses fetch, so fetch alone is not evidence of a new host incompatibility. |
| `b135e02` | Removes the old injected article script during HTML-shell extraction; the shell at this point still has the article export button rather than the later desktop toolbar. |
| `cf7885f` | Dict consumes the common desktop template; its shell includes the nine-action desktop toolbar and language modal. |
| `f87272e` | Embeds `app.js` unconditionally for offline articles as an asset-closure repair. This addresses missing handlers while retaining the wrong article UI/runtime boundary. |
| `30510bb..c2e11f3` | Adds genuine target, transaction, authentication, mapping, recovery and compatibility repairs. Reverting these commits does not remove the earlier template/panel substitution. |

Recommended implementation: restore the dedicated Dict article surface from `56f8696`, while keeping the current persistence and authentication boundaries. A whole-release rollback would remove substantially more than the offending article surface, including later desktop and data-integrity repairs. A rollback of only the latest commits would leave the panel and remove valid protections. Neither is the preferred repair.

The concrete restoration scope is:

1. Replace Dict's use of the desktop shell in `_render_lookup_html_impl`. Restore the snapshot's section composition, inherited compact typography, table density, widths, tooltip presentation and conditional article export area. Use the snapshot as the visual reference, not the current Desk window.
2. Stop loading Desk's `app.js`, WorkspaceTabs, action toolbar, language modal and global desktop CSS into Dict. Do not hide that toolbar with CSS while continuing to initialize its application runtime.
3. Preserve the snapshot's supported functional scope: readable configured source/translation/lemma output; optional tagging/export when the server and host transport support them. Offline output has no interactive controls, application runtime or credentials. Do not add Save, Update, Re-text, Re-word, Delete, Hand Tool or desktop shortcuts to Dict.
4. Keep current authoritative session/sentence/token descriptors, valid token zero, configured field mappings, complete merged occurrence manifests, whole-request validation and transactions. The snapshot's old positional/token shortcuts must not be copied back into mutation handlers. Use the current validated command boundary for the small article actions, retaining shared selection semantics and exact export targets.
5. Keep loopback/token/origin policy, script-safe serialization and failure reporting. An unavailable host transport must not trigger unauthenticated requests or fictitious success. The snapshot's optional script is a behavioral reference, not a reason to copy old credential interpolation or identity mistakes.
6. Test dedicated Dict and desktop independently. Existing assertions about exact targets, authentication, transactional saves and desktop behavior remain contracts. Tests that require a desktop toolbar inside Dict conflict with the user's explicit restoration request and must be replaced with article-specific behavior coverage, rather than counted as proof of parity.
7. Verify the restored article against the reference in compact/dark/light themes, enabled/disabled server modes, reduced/reordered/full columns, CSS-disabled mode and host-font inheritance. Exercise actual tagging, deselection and export through isolated SQLite/TSV boundaries, including differing display/token identities and repeated token zero. Confirm that desktop layout and action/recovery behavior are unchanged. Physical GoldenDict remains a separate acceptance check.

This scope deliberately changes article presentation and client composition, not the storage schema, translation providers, package dependencies or AutoHotkey host. No AutoHotkey rollback is currently justified by the reproduced article defects. Cross-repository rollback would need its own compatibility evidence.

The archive decision must distinguish the reference release from OpenSpec changes. `20261013101000-snapshot-desk` is an existing Git tag and release-history entry, not an active OpenSpec change; it has no delta specs to synchronize or archive. Keep that reference intact. Do not create a fictitious OpenSpec archive for it or replace canonical specs wholesale from the old vault revision.

| Current OpenSpec object | Verified state | Recommended disposition |
| --- | --- | --- |
| `20261026093717-unify-dict-and-desk-view-contracts` | Artifacts complete, tasks 0/13. Its design explicitly says restoring the old Dict appearance is a non-goal and requires Desk as the reference. | Superseded by the current user request. Archive as unimplemented/superseded **without syncing its existing deltas**. Its useful bounded-column requirement belongs in the restoration's article contract, without the desktop-shell requirement. |
| `20261026093717-fix-dict-row-identity-and-save-targets` | Tasks 13/13; deltas cover occurrence identity, atomic mutations and canonical selection. Canonical identity text still differs. | Retain implementation protections. Reconcile article/bootstrap wording and applicable selection scenarios against the restored article before selectively synchronizing and archiving. Do not undo these fixes to obtain the old appearance. |
| `20261026093717-fix-goldendict-article-transport-and-offline-actions` | Automated tasks 13/17; physical-host tasks 4/17 remain open. Deltas assume broad article actions and the shared toolbar. | Do not sync these deltas wholesale. Retain authentication/routing/failure invariants; replace broad article-action/toolbar assumptions with the restored tagging/export surface. Carry physical-host work forward explicitly; any later archival must preserve incomplete status and must not imply host certification. |
| Canonical `static-web-shell` | Currently requires a universal shell including GoldenDict. | Amend through the restoration change: shared desktop shell remains, Dict has a dedicated host-contained article boundary. Preserve serialization and other valid requirements. Merely skipping the pending shared-view delta would not correct this existing canonical contradiction. |

Thus the answer is not a blanket sync or blanket rollback: archive the superseded shared-view proposal without resyncing, reconcile valid identity/transport requirements with the restored article, and sync only that reviewed contract after implementation verification. No archive or canonical synchronization was performed during this audit.

| Evaluation dimension | Result | Evidence or remaining limit |
| --- | --- | --- |
| Regression | FAIL | Baseline/current article renders demonstrate the toolbar, density and layout changes. |
| Orthogonality | FAIL | An article embeds the full desktop runtime and leaks desktop CSS into its surrounding document. |
| Consistency | FAIL | Current implementation/pending proposal use Desk as reference; the user requires the snapshot Dict surface. |
| Naming | WARN | A lookup renderer currently assembles a desktop application rather than a confined article. |
| Production readiness | FAIL for requested Dict contract | Offline output still exposes a desktop toolbar. Physical host is unverified. |
| Polishing | FAIL | 843px selection width and changed compact typography are reproduced. |
| Hardcoding | WARN | Full-desktop percentage allocation is applied to a reduced article table. |
| Flexibility | WARN | Configured columns exist, but the reduced interactive layout absorbs unused allocation incorrectly. |
| Fragility | FAIL | Article readability/control scope depends on unrelated desktop initialization and styling. |
| Scalability/performance | WARN | Two-row payload grows from 5-11KB to roughly 752KB; actual host time/memory was not measured. |
| Edge cases | WARN | Themes, columns, server modes and JavaScript availability were exercised; physical engine limits remain open. |
| Correctness | Mixed | Current article contract fails; 14 targeted storage/security boundary checks pass and should be retained. |

Evidence:

```powershell
venv\Scripts\python.exe scripts\audit_dict_snapshot_20261028092736.py
```

Exit 0: 48 browser render cases across two revisions, three themes, two server modes, two column sets and enabled/disabled JavaScript, plus four embedding style probes. Network requests were blocked. Both revisions rendered the explicit owned fixture; no live session or provider was used. `comparison.json` records geometry, controls, payload sizes, errors and before/after protected hashes. No Chromium runtime errors were recorded; that is not evidence of physical-host support.

```powershell
venv\Scripts\python.exe scripts\run_dict_transport_audit.py snapshot-retain-boundaries-202610280940 tests\test_dict_transport_audit_regressions.py::test_invalid_second_sqlite_target_leaves_entire_save_unchanged tests\test_dict_transport_audit_regressions.py::test_missing_both_sqlite_coordinates_cannot_use_display_position tests\test_dict_transport_audit_regressions.py::test_unknown_tsv_occurrence_cannot_fall_back_to_display_row tests\test_dict_completion_contracts.py::test_article_reads_require_authentication tests\test_dict_completion_contracts.py::test_arbitrary_web_origin_cannot_read_article_credentials tests\test_suite_regression_boundaries.py -k "not watchdog and not recovery and not draft"
```

Exit 0: 14 passed, 3 deselected in 3.56 seconds, with protected/source files unchanged. Python compilation and `git diff --check` pass. OpenSpec metadata reports the shared-view artifacts complete, while inspection confirms its 13 tasks remain unchecked; artifact completeness is not implementation completion.

This phase adds only the audit script and this review, plus ignored evidence under `results/20261028092736-dict-snapshot-audit/`. Runtime files, existing tests, canonical specs, change tasks, release history and Git history remain unchanged by this phase. The user's pending configuration change is preserved. Production restoration is the next phase, subject to the user's AGENTS.md instruction: "Stop at each phase and wait for user review and git commits."
