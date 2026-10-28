20261028212349

TRAFFIC LIGHT SIGNAL: YELLOW. The dedicated Dict article is implemented and the focused checks pass. This phase does not certify the complete configuration matrix, the full suite or physical GoldenDict/MSHTML.

The reviewed starting point is Desk `c8ba8fe` (the user's phase-1 commit). The worktree was clean. The reference remains `56f8696`, the peeled `20261013101000-snapshot-desk` tag. Phase-2 preflight hashes and the final verification manifest are under `results/20261028212349-dict-restoration-phase2/`. No Git mutation, package installation, schema change, provider-policy change or AutoHotkey modification was performed.

## Implemented behavior

`_render_lookup_html_impl` now builds a dedicated article using `web/dict/article.html`, `article.css` and the optional `article.js`. It no longer reads or assembles the desktop template, styles, bootstrap, components, toolbar, modal, WorkspaceTabs or application runtime. The existing frequency sort, field-role mapping, occurrence manifest, formatting/tooltips, safe serialization and required-asset loader remain the owners of those contracts. Obsolete scrollable desktop-cell branches and inline global tagging handlers were removed from article section composition.

The stylesheet comes from the reference's compact and dark presentation with every selector confined to the article root. Light/white defaults and validated configured color variables are confined to that root as well. Compact typography inherits the host; selected/pending/failure changes retain box metrics. Source, headings, values and metadata are safely escaped in their own contexts. Configured known empty columns survive. Server-disabled output contains no action controls, runtime, credential or hydration island; CSS-disabled output contains no application styling.

The article client owns only its root's checkbox/export controls and status. Initial controls stay disabled when JavaScript, authenticated transport or complete identity is unavailable. There is no application-global client state, SSE, status polling, implicit server/native launch, word editor or desktop shortcut lifecycle. It projects normalized-lemma membership locally, sends complete occurrence targets through authenticated `/session/save`, preserves newer reversals across held acknowledgments and waits for preceding saves before export. Errors stop the command without positional guesses, silent retries or fabricated success. DOM row references are resolved once instead of repeatedly scanning for each projection.

Export indices come from the original occurrence manifest rather than frequency-sorted display positions; merged descriptors are retained. Combined output now forwards its authoritative session/fingerprint/credential identity into the HTML renderer, and coordinate aliases share one column-resolution owner.

Two small controller repairs were necessary for the article command boundary:

- The active `/session/export` handler previously discarded the supplied fingerprint. It now passes it to the existing core validator. A real-storage regression proves stale export returns `ROW_STALE` before creating a favorites file. The unreachable duplicate `/session/export` alias was removed from the later `/api/v1/export` condition.
- Article callers can request `include_article_state` in an export acknowledgment. The response then includes current normalized-lemma membership and fingerprint from a read-only storage projection. This accounts for existing SQLite export side effects in `all`/`unselected` mode and permits another tag using current identity. TSV projection resolves the requested language through the existing working-file resolver. Existing callers that omit the option retain their response contract; CORS/authentication and export policy are unchanged.

The restored preview is [available as a screenshot](results/20261028212349-dict-restoration-phase2/restored-compact.png). It was visually compared with the recorded reference screenshot. At 1280px it has a **40px selection column**, inherited compact table presentation and only **Export to Anki**. The final two-row preview measures **19,278 bytes**, versus approximately 752KB in the original audit. `final-preview.json` records its hash/size; the earlier preview measurement is retained separately. It is a payload measurement, not a native-host performance claim.

## Verification

```powershell
venv\Scripts\python.exe scripts\run_dict_transport_audit.py restoration-phase2-complete-20261028212349 tests\test_dict_article_assets.py tests\test_dict_article_actions.py tests\test_dict_snapshot_article_contracts.py
```

Exit 0: **56 passed in 38.46s**. This includes 16 unchanged snapshot/article contracts, 14 dedicated asset/combined-identity cases and 26 action cases against real isolated SQLite/TSV and separate actual HTTP article/controller origins. Covered effects include tag/deselect/export/reopen, repeated token zero, normalized duplicate lemmas, configured `Marked` selection fields, merged occurrence sets, held acknowledgments/reversals/export barriers, independent embedded articles, CSS-disabled actions, hostile headings, incomplete identity, real unauthorized/stale responses, network aborts and export-policy acknowledgment followed by another tag. A disposable favorites file is inspected for its complete exported target set; Anki import is explicitly disabled.

```powershell
venv\Scripts\python.exe scripts\run_dict_transport_audit.py restoration-phase2-protected-final-20261028212349 tests\test_dict_transport_audit_regressions.py::test_invalid_second_sqlite_target_leaves_entire_save_unchanged tests\test_dict_transport_audit_regressions.py::test_missing_both_sqlite_coordinates_cannot_use_display_position tests\test_dict_transport_audit_regressions.py::test_unknown_tsv_occurrence_cannot_fall_back_to_display_row tests\test_dict_completion_contracts.py::test_article_reads_require_authentication tests\test_dict_completion_contracts.py::test_arbitrary_web_origin_cannot_read_article_credentials tests\test_suite_regression_boundaries.py tests\test_ui_interactions.py::test_cell_edit_and_undo tests\test_ui_interactions.py::test_cancel_edit tests\test_ui_interactions.py::test_f2_shortcut_editing tests\test_static_web_shell.py::test_playwright_table_inline_editing_history_and_navigation tests\test_lookup_renderers.py::test_render_section_lemmas tests\test_lookup_renderers.py::test_render_lookup_text tests\test_lookup_renderers.py::test_render_lookup_html tests\test_lookup_renderers.py::test_render_lookup_html_pos_and_gender_integration tests\test_desk_core.py::test_cmd_export_selection_modes_and_favorites
```

Exit 0: **26 passed in 11.22s**. Existing assertions were not changed. This retains exact storage/no-write/transaction/authentication/origin/draft-history/recovery checks and desktop editing, Undo, cancel, F2, navigation, mapped lookup formatting and export-mode behavior. Neither count represents full-suite success.

The guarded runner records source hashes unchanged during each final run and protected config/mapping/database hashes unchanged. Its exact command arguments, exit codes, loaded modules and comparisons are in the corresponding `*-execution.json` files under `results/20261027221743-dict-transport-audit/`. Initial failing/debug runs are retained too. An intercepted HTTP document initially failed Chromium's loopback-address-space policy; the action fixture was corrected to serve an actual disposable loopback document, retaining browser/backend security. No production compatibility workaround was introduced for that test-fixture problem.

Python compilation, `node --check` for the article client, strict OpenSpec validation and `git diff --check` pass. Git reports a CRLF normalization advisory without whitespace errors. New Python article helpers and the rewritten assembly have measured branch complexity at or below 7; the retained pre-existing formatter is smaller after removal of desktop-only branches. The final manifest records the exact tracked diff, added-file hashes, protected comparisons and unchanged desktop asset/backend-domain hashes. The original transaction, selection mutation, authentication, CORS and provider-policy implementations were not replaced.

## Review boundary and remaining work

Phase 2 completes tasks 2.1-2.6; overall change progress is **14/24**. Task 2.6 and the test-and-apply protocol require stopping here for review and a user-managed commit. Phase 3 owns the broader theme/viewport/configuration/action matrix, interaction geometry/performance checks, reviewed rehoming/replacement of legacy tests and broader regression runs. The phase-1 test inventory remains the disposition authority: tests requiring desktop controls/runtime inside Dict still conflict with the explicit new product contract and have not been silently edited, skipped or counted as successful certification. Their valid backend and desktop assertions must survive their migration.

Physical GoldenDict engine/version/origin/authenticated transport and native MSHTML behavior remain unchecked in phase 4. Current Chromium, HTTP and storage evidence does not establish those host capabilities. Canonical restoration-spec synchronization remains pending; predecessor archives and the reference tag/release history were not changed in this phase.

| Evaluation dimension | Result | Evidence / remaining scope |
| --- | --- | --- |
| Regression | PASS focused / WARN broad | 82 focused checks pass; full suite and legacy test migration remain open. |
| Orthogonality | PASS focused | Dedicated assets/client; no desktop initialization in Dict. |
| Consistency | PASS focused | Display and persistence identities remain separate; shared aliases/mappings retained. |
| Naming | PASS focused | Article assets and acknowledgment state name their supported scope. |
| Production readiness | WARN | Physical hosts and full matrix remain unverified. |
| Polishing | PASS compact / WARN matrix | Reference-like compact preview; complete interaction geometry matrix remains. |
| Hardcoding | PASS focused | Only reference presentation constants; configured identity, mappings, backend and colors remain explicit. |
| Flexibility | PASS focused / WARN matrix | CSS-disabled, configured/reordered/empty columns, two storage backends; broader configurations remain. |
| Fragility | PASS focused | Invalid identity/authentication/stale state/transport fail explicitly without guessed writes. |
| Scalability/performance | PASS payload / WARN host | Small self-contained payload and cached row references; physical-host time/memory unmeasured. |
| Edge cases | PASS focused / WARN matrix | Reversal/barrier/merged/mapped/multi-article/export-policy cases pass; broader matrix and physical busy behavior remain. |
| Correctness | PASS focused | Actual persisted and exported targets verified; no full-release certification claimed. |
