20261028102008

TRAFFIC LIGHT SIGNAL: RED for current Dict. Phase 1 codifies the restoration contract; production restoration has not begun. The new tests deliberately fail against the substituted desktop article.

The active change is `20261028095231-restore-snapshot-dict-article`. Desk HEAD is `f35288aef0662030b2142ea2cba5c72a8782b9bf`; the annotated snapshot tag peels to `56f86968e794cbdcc947b6223b3138510695df98`. Release `20261028101953` records that same current Desk commit and AutoHotkey `5b5a016`. Since the earlier `c2e11f3` audit, Desk added only the audit/report and the user's `default_language=de` configuration change. That configuration remains intact. No Git mutation was performed.

Before test execution, `results/20261028102008-dict-restoration-phase1/preflight.json` recorded the clean worktree, both commit identities, 214 tracked-file hashes and three protected-file hashes. The protected set covers `config.ini`, `anki-mapping.ini` and the workspace database. The existing guarded runner additionally creates pre-run database copies, redirects ambient storage/results into owned fixtures, blocks protected service ports/native launches and compares protected files before/after each test. Evidence uses disposable credentials only.

The fixture, comparison results and screenshots from `results/20261028092736-dict-snapshot-audit/` were reused and visually inspected. The new test fixture explicitly supplies the same two rows and three content columns without loading workspace configuration, a provider or persistent storage. It reads the reference renderer from the pinned Git object without checking out any files. Both renderers receive identical content, options, host CSS and a 1280 by 900 Chromium viewport. All browser network access is blocked.

| Observable contract | Snapshot control | Current Dict |
| --- | --- | --- |
| Enabled action scope | Two checkboxes and Export to Anki | Nine desktop toolbar buttons |
| Desktop initialization | No desktop globals or bootstrap | Seven checked desktop globals present |
| Compact selection column | 40 CSS px | 843.1875 CSS px; tolerance is 1 px |
| Compact cell typography/density | Host Georgia 17px, padding 2px/4px | Application font instead of host font |
| Server-disabled output, CSS on/off | Readable rows, no controls/scripts/credential | Twelve form controls remain |
| Compact embedding | Host typography, background and geometry unchanged | Host font, background and geometry change |
| Dark/light embedding | Historical reference already leaks global theme styles | Current Dict also leaks; restoration must contain every theme |

The first characterization run exposed the historical dark/light leakage: 11 failures and 7 passes. Those two historical failures are retained in its execution artifacts. Snapshot dark/light is consequently not used as a positive isolation oracle. The final tests still require strict host isolation for current compact, dark and light output; the seven historical positive controls cover only behavior the snapshot actually satisfies. This preserves the new requirement rather than copying an old theme defect. Snapshot behavior is not an oracle for persistence/security, which retain current validated contracts.

New coverage is in `tests/test_dict_snapshot_article_contracts.py`: 16 cases, including seven historical positive controls and nine current regression cases. None is skipped or marked expected-failure. The tests remain red until production implements the requested contract.

## Existing-test disposition for review

The explicit product change is the user's request to restore the snapshot article, adopted in the approved proposal: Dict supports readable configured content and optional tagging/export; desktop editing, Undo, shortcuts, toolbar, modal, WorkspaceTabs and polling/SSE initialization remain desktop features. Offline Dict has no application runtime or controls. No existing test was edited in this phase. The following replacements are proposed for the implementation phase; their valid data/security/desktop assertions must survive.

| File and test(s) | Conflicting expectation and bounded replacement | Contract retained at its owner |
| --- | --- | --- |
| `test_static_web_shell.py`: `test_render_lookup_html_preserves_shell_containers_and_offline_stripping` | Remove the requirement that Dict contain desktop workspace, toolbar, modal and toast containers; assert dedicated article and self-contained article assets. | Desktop container checks elsewhere in this module and article content/asset closure remain. |
| `test_static_web_shell.py`: `test_standalone_html_bundles_all_components` | Split article closure from mandatory desktop component bundling. | Assert every desktop component on standalone desktop rendering; Dict must not contain those components. |
| `test_goldendict_article_transport_regressions.py`: `test_1_2_offline_article_asset_closure_and_readable_output` | Replace required disabled toolbar buttons with absence of all action controls/scripts. | Readable configured content, no credential and no unresolved assets remain. |
| Same file: `test_1_3_different_origin_article_request_destinations` | Replace Dict Re-text invocation with supported tagging/export against a separate loopback article origin. | Exact configured backend, authoritative identity, language and token header; foreign origins remain rejected. |
| Same file: `test_1_4_same_origin_and_native_save_contracts` | Its Dict fixture must become a desktop fixture for native persistence and manual Save. | Save acknowledgment ownership and native bridge contract remain desktop assertions. |
| Same file: `test_2_1_article_commands_and_autosave_reach_configured_backend` | Split supported article tagging/export from desktop Re-text/Re-word, audio, cell edits, pagehide autosave and Update. | Retain the exact routing/authentication/effects assertions on their supported surface; do not keep extra commands inside Dict. |
| Same file: `test_2_4_action_availability_projection_when_server_disabled` | Replace required toolbar methods, disabled buttons and desktop warning toasts with static article output and zero requests. | Offline readability, no implicit server launch/request and no fabricated action success remain. |
| Same file: `test_2_5_credential_safety_and_authentication_failure_mutation` | Replace readiness through desktop `getApiToken` with article-local readiness. | Keep absent/bad-token rejection, unchanged storage and no token in URL/browser storage. |
| `test_dict_row_identity_regressions.py`: `test_1_2_checkbox_row_id_differs_from_token_order` | Replace desktop `getDeltas` inspection with article request/acknowledgment and persisted readback. | Clicked display row 1 must retain sentence 1/token 9; checkbox, row styling and membership must agree. |
| Same file: `test_1_3_isolated_sqlite_sentence_duplicate_token_order` | Rehome word editing from the lookup fixture to a desktop fixture. | Preserve every existing exact SQLite before/after assertion: sentence 1 remains house, sentence 2/token 0 becomes wood-tree. |
| Same file: `test_1_3_core_edit_save_rejects_missing_sentence_coordinates` | No replacement required. | Keep structured rejection and complete no-write assertions unchanged at the backend. |
| Same file: `test_1_4_checkbox_synchronizes_on_local_deselection`, `test_1_4_checkbox_reversal_during_held_save_retains_latest_intent` | Exercise article checkboxes and controlled real HTTP acknowledgments instead of global desktop methods. | Preserve deselection, newer reversal, row projection and acknowledgment ownership; verify actual storage. |
| Same file: `test_1_4_checkbox_synchronizes_on_undo` | Rehome editing/Undo assertions to desktop; retain article checkbox projection separately. | Undo must restore the edited value without erasing canonical selection on desktop. |
| Same file: `test_1_4_checkbox_synchronizes_on_remote_membership` | Rehome `applySharedSelection` projection to desktop; verify persisted canonical membership on article acknowledgment/reopen. | Canonical normalized-lemma membership and no save echo remain; Dict gains no SSE lifecycle. |
| `test_dict_completion_contracts.py`: `test_configured_translation_field_is_emitted_and_saved` | Separate mapped article text rendering from desktop editing. | Keep `Gloss` mapping, exact SQLite/TSV save and readback assertions. |
| Same file: `test_merged_lookup_reword_carries_every_occurrence` | Rehome Re-word to desktop and add supported article tagging/export occurrence coverage. | Preserve the exact target set `{(1,0),(2,0)}` and complete merged descriptors. |
| Same file: `test_configured_selection_field_saves_and_reloads` | Replace `getDeltas` with article requests and real storage; retain mapped `Marked` field and both storage variants. | Keep checked/deselected reopen state and exact stored selection values. |
| Same file: `test_merged_lookup_edit_rejects_ambiguous_word_target` | Rehome merged word editing to desktop. | Keep rejection without explicit occurrence and exact sentence-2/token-0 edit when explicitly selected. |
| Same file: `test_different_origin_article_receives_authenticated_membership_stream` | Rehome authenticated stream assertions to desktop; article reopen/acknowledgment owns its membership check. | Keep header authentication, no URL credential and durable canonical membership. |
| Same file: read-authentication, foreign-origin and stale-status tests | No product expectation changed. | Existing HTTP rejection and desktop stale-status ownership assertions stay intact. |
| `test_dict_transport_audit_regressions.py`: `test_lowercase_token_order_survives_lookup_rendering` | Keep article identity assertions, using article-contained DOM if needed. | Lowercase token-order mapping must still emit 9. |
| Same file: `test_missing_lookup_sentence_cannot_emit_guessed_edit` | Replace nonexistent Dict edit with unsupported-edit absence and explicit rejection of incomplete article action identity. | No guessed sentence/default target; current backend missing-coordinate rejection stays intact. |
| Same file: `test_real_different_origin_save_persists_exact_occurrence`, `test_reword_selected_sentence_keeps_complete_target_descriptor` | Rehome editing/Re-word to desktop; add equivalent authoritative identity coverage for supported article selection/export. | Keep exact occurrence/storage targets; no first-occurrence or display-position substitute. |
| Same file: `test_offline_audio_dispatches_no_backend_request` | Preserve the no-request assertion; offline article has no audio/retry runtime. | Zero backend requests remains required. |
| Same file: atomic/missing-target/extra-fields/backend Re-word and desktop recovery tests | No replacement required. | All exact no-write/transaction/metadata/target/membership assertions remain. |
| `test_release_audit_regressions.py`: `test_f1_non_german_lookup_language_metadata`, `test_phase2_missing_language_rejection_without_template_fallback` | Split desktop helper/Re-text/Re-word/audio checks from article-local tagging/export and language metadata. | Preserve non-German language routing, exact session/token header and missing-language rejection. |
| Same file: `test_phase2_css_enabled_and_disabled_renderers`, `test_f3_css_disabled_output_suppresses_stylesheets_and_fetches` | Replace mandatory Dict `modular-css` desktop bundle with dedicated article CSS; leave desktop branches intact. | CSS-disabled output must contain/fetch no application styles. |
| `test_release_audit_boundaries.py`: `test_required_assets_cold_and_warm`, `test_http_required_asset_failure_is_json_without_html` | Separate desktop required assets from the new dedicated article assets, including enabled/disabled client requirements. | Cold/warm fail-fast behavior, HTTP structured errors, no partial HTML writes and desktop asset requirements stay. |
| `test_ui_dom.py`: `test_playwright_dom_validation` | Its `write_update_js`/`receiveUpdate` test belongs to the desktop renderer. | Preserve source span structure, update payload and updated destination visibility. |
| `test_sentence_idx_contract.py`: `test_find_token_data_sentence_coordinate` | Use desktop rendering for `AppState.sentenceCards`/`findTokenData`. | Preserve sentence-index versus tab-number separation and all returned token-coordinate assertions. |
| `test_lookup_renderers.py`: lookup/text/section/tooltip tests; other desktop tests and JSON-island escaping tests | No changed functional requirement. | Preserve configured section/heading/column order, supported empty columns, sorting, POS/gender/tooltips, safe serialization and desktop behavior. The unused lookup import in sentence-isolation tests does not require a behavior change. |

Shared helpers `make_lookup_page`, `render_test_lookup`, `lookup`, `mount_article` and the injected `TRANSPORT` script currently expose desktop globals. During implementation, their consumers must use the correct renderer/client boundary; retaining a hidden desktop application merely to satisfy those helpers is forbidden by the approved proposal. Existing snapshots of storage/targets are contracts, independent of these helper changes. The proposed replacements require no package, schema, provider-policy, AutoHotkey or Git-history change.

## Executed verification

```powershell
venv\Scripts\python.exe scripts\run_dict_transport_audit.py snapshot-phase1-final-20261028102008 tests\test_dict_snapshot_article_contracts.py
```

Exit 1, intentionally: **9 failed, 7 passed in 5.70s**. Every failure is a current-artifact assertion, not a timeout or fixture error. The historical positive controls all pass. The runner records protected files and its source set unchanged. The original exploratory run is retained as `snapshot-phase1-20261028102008-*`; the final run is `snapshot-phase1-final-20261028102008-*` under `results/20261027221743-dict-transport-audit/`.

```powershell
venv\Scripts\python.exe scripts\run_dict_transport_audit.py snapshot-phase1-boundaries-20261028102008 tests\test_dict_transport_audit_regressions.py::test_invalid_second_sqlite_target_leaves_entire_save_unchanged tests\test_dict_transport_audit_regressions.py::test_missing_both_sqlite_coordinates_cannot_use_display_position tests\test_dict_transport_audit_regressions.py::test_unknown_tsv_occurrence_cannot_fall_back_to_display_row tests\test_dict_completion_contracts.py::test_article_reads_require_authentication tests\test_dict_completion_contracts.py::test_arbitrary_web_origin_cannot_read_article_credentials tests\test_suite_regression_boundaries.py
```

Exit 0: **17 passed in 9.47s**, protected/source files unchanged. This covers whole-request/no-guessed-target rejection, real SQLite/TSV effects, read authentication, foreign-origin rejection, explicit storage paths, independent draft histories, invalid Re-word target formats, explicit selected occurrence behavior and desktop recovery/stale-status ownership. It is focused baseline evidence, not a full-suite or physical-host certification.

The final manifest at `results/20261028102008-dict-restoration-phase1/verification.json` records the exact additions, before/after tracked/protected hashes, new test hash, strict OpenSpec validation and whitespace/syntax results. Existing test and runtime hashes remain identical to preflight. New tests and this report are additions relative to `f35288a`; the active change's phase-1 task checkboxes are the only intended vault edit. Canonical specs and predecessor archives are outside this phase.

| Evaluation dimension | Result | Evidence/scope |
| --- | --- | --- |
| Regression | FAIL in current product | Nine current article assertions fail. |
| Orthogonality | FAIL in current product | Desktop globals/runtime inside an article. |
| Consistency | FAIL in current product | Current article contradicts the approved snapshot boundary. |
| Naming | WARN | Lookup renderer still assembles a desktop shell. |
| Production readiness | FAIL for restored contract | Offline controls and runtime remain. |
| Polishing | FAIL in current product | 843px selection allocation and changed compact density. |
| Hardcoding | WARN | Desktop allocation is applied to reduced article columns. |
| Flexibility | WARN | Full composition/configuration matrix belongs to phase 3. |
| Fragility | FAIL in current product | Host styles and unrelated desktop initialization remain coupled. |
| Scalability/performance | WARN | Prior two-row output measured about 752KB; no new performance certification. |
| Edge cases | WARN | Historical dark/light leakage recorded; physical hosts and action interleavings remain open. |
| Correctness | FAIL article / PASS focused protected boundaries | Nine article failures; 17 existing boundary checks pass. |

Phase 1 stops here for user review and a user-managed commit, as required by AGENTS.md and task 1.4. Implementation tasks 2.1-2.6 remain unchecked. The bounded next phase replaces Dict's desktop-template assembly with dedicated article assets and only the supported authenticated tagging/export client, while preserving current backend identity, atomicity, authentication and desktop behavior.
