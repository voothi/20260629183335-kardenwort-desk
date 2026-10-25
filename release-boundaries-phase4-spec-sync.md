# Phase 4 canonical specification synchronization review

Change: `20261025101246-fix-release-boundaries-and-contract-duplication`.

This is an exact synchronization preview, prepared for phase review. No canonical spec was written, no change was archived, and no conversation or historical evidence was edited. The complete patch and resulting five specifications are retained in `results/20261025154840-release-boundaries/spec-sync-preview/` with before/after SHA-256 values in `manifest.json`.

The accepted instruction `20261025101603` supersedes the earlier corrective change `20261024101717-restore-project-intent-and-contract-integrity` only where it prescribes persistent TypeScript tooling, declarations, checking directives, or compiler-dependent contracts. Its runtime fixes and historical verification remain evidence. Checked historical tasks are not rewritten or recertified.

The tooling replacements below are ready for review now. The complete preview also includes the Phase 2/3 selection/consumer requirements and the artifact-maintenance capability. The latter still requires Phase 5/6 execution; preparing a patch does not certify that work. Canonical application and archival remain separate actions after reviewed deltas.

The capability identifier `typed-vanilla-components` stays stable to preserve existing references; the requirement names and prescriptions become JavaScript-only. Unrelated requirements in `unified-web-workspace-tabs` are retained byte-for-byte except section spacing. Valid session language and mode values remain unchanged; missing workspace metadata is diagnosed instead of inventing language or mode values.

## Tooling replacement patch

```diff
--- openspec/specs/typed-vanilla-components/spec.md
+++ openspec/specs/typed-vanilla-components/spec.md
@@ -3,29 +3,34 @@
 ## Purpose
 TBD - created by archiving change 20261013001500-typed-vanilla-component-architecture. Update Purpose after archive.
 ## Requirements
-### Requirement: Compile-Free JSDoc Type Contracts
-The system SHALL maintain central type definitions in `web/js/types.js` using standard JSDoc `@typedef` annotations for all domain entities, including `Token`, `VocabularyRow`, `SessionState`, `UpdatePayload`, and `ProvenanceRecord`. Every JavaScript module in `web/js/` SHALL include `// @ts-check` as its first line to enforce static type safety in IDEs and AI tooling without external compilers or runtime overhead.
 
-#### Scenario: Type contract validation in editor and language server
-- **WHEN** a JavaScript module is loaded in an editor or analyzed by an AI coding assistant
-- **THEN** the language server validates property types and function signatures against `types.js` contracts
-- **AND** flags invalid property names or type mismatches.
+### Requirement: Plain JavaScript and Descriptive Contracts
+The maintained frontend SHALL execute as plain JavaScript with useful ordinary JSDoc documentation. Descriptive Token, VocabularyRow, SessionState, UpdatePayload, provenance, and workspace contracts SHALL have one canonical documented definition rather than overlapping alternative domain schemas. The project SHALL NOT retain app-contracts.d.ts, host.d.ts, the added jsconfig.json, declaration references, TypeScript directives, compiler-only assertions/import annotations, or a required TypeScript compiler in setup, runtime, tests, or release verification. Runtime fixes introduced during compiler analysis SHALL be retained where behavior requires them. Input validity SHALL be checked at actual boundaries instead of presumed from annotations. Temporary compiler tools SHALL NOT become repository dependencies.
 
-### Requirement: Host-Compatible Typed Component Structure
-Daily-use components SHALL use a loading/export form supported by every claimed host, including MSHTML/ActiveX, without requiring native ES modules. Production component projections SHALL subscribe to the authoritative state boundary for the regions/fields they own, and commands SHALL dispatch through the canonical action boundary. Each touched region/field SHALL have one production rendering owner; its superseded imperative writer SHALL be removed in the same slice. Mounting, subscription, disposal, and remounting SHALL be idempotent. Helpers SHALL use their existing canonical implementations and SHALL NOT supply private fallback copies. All bundled JavaScript files SHALL retain first-line `// @ts-check` and checked JSDoc contracts.
+#### Scenario: Application operates after compiler tooling is removed
+- **WHEN** supported hosts load the maintained scripts and the applicable behavioral suite runs with no project compiler cache or compiler integration
+- **THEN** session interaction, saves, updates, and export work through the existing JavaScript implementation
+- **AND** no setup/runtime/test step requests or invokes a TypeScript compiler or declaration file
 
-#### Scenario: Component receives an authoritative field update
-- **WHEN** a state event changes a translation field owned by a mounted component
-- **THEN** that component projects the field exactly once
-- **AND** unchanged fields preserve their node identity and interaction state
-- **AND** no competing legacy renderer overwrites the result
+#### Scenario: Documentation and runtime contract stay consistent
+- **WHEN** a domain field or optional host boundary is inspected or changed
+- **THEN** one canonical descriptive contract documents it and behavioral tests exercise its validity
+- **AND** no second widened Token/Card/State schema masks incompatible inputs
 
-#### Scenario: Repeated mount or session remount
-- **WHEN** a component is mounted repeatedly or disposed and mounted for the next session
-- **THEN** one active listener/subscription owns each required interaction
-- **AND** stale listeners are released without losing canonical handlers
+### Requirement: Host-Compatible JavaScript Component Ownership
+Daily-use components SHALL use loading/export syntax supported by all claimed hosts, including MSHTML/ActiveX, without requiring native ES modules or a build step. Each region/field SHALL have one production rendering owner consuming the authoritative state boundary; obsolete competing implementations SHALL be removed. A required public compatibility entry point SHALL delegate to that owner rather than duplicate its renderer. Mount, subscription, disposal, and remount SHALL be idempotent. Helpers SHALL use canonical implementations and unavailable required helpers SHALL fail explicitly. Removing duplicate paths SHALL preserve useful behavioral assertions, node identity for unchanged fields, focus, selection, metadata, loaders, and export context.
 
-#### Scenario: Native host loads bundled scripts
-- **WHEN** the shared shell loads in the supported native host
-- **THEN** scripts initialize without depending on unsupported ES-module syntax or missing required shared helpers
-- **AND** the same behavioral contracts are verified separately from Chromium host emulation
+#### Scenario: Component projects an authoritative update
+- **WHEN** a mounted owner's field changes through the authoritative state boundary
+- **THEN** the field is projected exactly once without a competing legacy writer
+- **AND** unchanged fields retain their DOM node identity and interaction state
+
+#### Scenario: Repeated mount and compatibility calls
+- **WHEN** components are mounted repeatedly, disposed/remounted, or invoked through supported compatibility entry points
+- **THEN** one owner and one active interaction binding remain
+- **AND** no duplicate renderer or token-splitting implementation is required for those entry points
+
+#### Scenario: Native host loads the JavaScript shell
+- **WHEN** the shared shell loads in its supported native host
+- **THEN** it initializes using supported script syntax and required canonical helpers
+- **AND** native-specific verification is recorded separately from Chromium host emulation
--- openspec/specs/unified-web-workspace-tabs/spec.md
+++ openspec/specs/unified-web-workspace-tabs/spec.md
@@ -306,27 +306,6 @@
 - **THEN** `#translation-container` displays exclusively the translation for Sentence 1
 - **AND** the full multi-sentence document translation is assigned to Card 0 without polluting Tab `[2]`'s translation display.
 
-### Requirement: Modular Typed WorkspaceTabs Component
-The client workspace tab controller SHALL be implemented as an autonomous script located at `web/js/components/WorkspaceTabs.js`, annotated with `// @ts-check` and normative JSDoc type contracts. The component SHALL consume canonical utility and tooltip functions from the shared client namespace rather than declaring private duplicates of `escapeHtml`, `formatLemmaTooltip`, `formatPosTooltip`, `formatGenderTooltip`, `formatInflectedTooltip`, `formatProvenanceTooltip`, `formatTranslationCellTooltip`, `updateEmptySelectionState`, and `syncSelectionsToCardsAndState`. Session language and delivery mode detection SHALL read directly from `AppState.bootstrap` and SHALL log explicit warnings if required session attributes are missing, strictly avoiding silent fallback to `'en'` or `'single'`.
-
-#### Scenario: Script loading and public API exposure
-- **WHEN** the browser loads `web/js/components/WorkspaceTabs.js`
-- **THEN** it exposes `window.WorkspaceTabs` with typed public methods (`init`, `switchToTab`, `getCards`, `getActiveCard`, `updateActiveTabTranslation`, `updateSentences`)
-- **AND** internal state variables remain private within component module scope
-
-#### Scenario: Static type checking validation
-- **WHEN** `web/js/components/WorkspaceTabs.js` is analyzed by TypeScript / JSDoc static checker
-- **THEN** zero type errors or undeclared property accesses are reported
-
-#### Scenario: WorkspaceTabs reuses shared utility helpers without duplication
-- **WHEN** `WorkspaceTabs.js` formats cell tooltips, escapes HTML strings, or synchronizes cross-card selections
-- **THEN** it invokes shared functions defined on the canonical client namespace
-- **AND** does not declare redundant local duplicate definitions.
-
-#### Scenario: Unresolvable session language does not silently mask errors
-- **WHEN** `WorkspaceTabs.js:getSessionLang()` encounters a session with missing bootstrap configuration
-- **THEN** it logs an explicit console error and throws or alerts the failure
-- **AND** does not silently coerce the session language to `'en'`.
 
 ### Requirement: Client Token Sentence Coordinate Contract
 The client function `findTokenData(span)` SHALL return `sentence_idx` in the card coordinate space (`0` = master overview, `1..N` = sentences) and SHALL NEVER derive it from a card's `seq_num`. The coordinate SHALL be resolved in this order: the span's `data-sentence-idx` attribute (the value `0` SHALL be honored, not replaced), then the matched word record's `sentence_idx`, then the owning card's `sentence_idx` if greater than `0`, otherwise `null`. Text-based token resolution SHALL return a token only when exactly one candidate matches within the span's sentence; when the match is ambiguous or absent the function SHALL return `null` rather than the first match.
@@ -377,3 +356,54 @@
 - **WHEN** the user selects a merged overview row without a targeted occurrence and exports
 - **THEN** all intended constituent occurrences are exported separately with their own sentence context
 - **AND** child-card export remains scoped to its active sentence
+
+### Requirement: Modular JavaScript WorkspaceTabs Component
+The client workspace tab controller SHALL remain an autonomous JavaScript script at `web/js/components/WorkspaceTabs.js` with compiler-independent JSDoc documentation. Its setup, loading, tests, and release verification SHALL NOT require TypeScript declarations, directives, project configuration, or a compiler. The component SHALL consume canonical utility and tooltip functions from the shared client namespace rather than duplicate escaping, tooltip, empty-state, or selection logic. Session language and delivery mode detection SHALL read from `AppState.bootstrap` and diagnose missing required attributes explicitly without silently choosing en or single. Internal state SHALL remain private and documented supported public methods SHALL remain compatible.
+
+#### Scenario: Script loading and public API exposure
+- **WHEN** a supported host loads WorkspaceTabs.js with required shared utilities
+- **THEN** window.WorkspaceTabs exposes init, switchToTab, getCards, getActiveCard, updateActiveTabTranslation, and updateSentences
+- **AND** no compiler or declaration file is loaded to provide those methods
+
+#### Scenario: Public contract verification without a compiler
+- **WHEN** the JavaScript component's API and boundary behavior are verified
+- **THEN** real host/browser tests verify its observable results and invalid inputs explicitly
+- **AND** passing a TypeScript check is not a prerequisite or substitute for those checks
+
+#### Scenario: Canonical helpers and missing session language
+- **WHEN** WorkspaceTabs formats tooltips or projects shared selection
+- **THEN** it delegates to the canonical implementation without private fallback copies
+- **AND** an unresolvable required session language produces an explicit diagnostic and failure rather than coercion to en
+
+### Requirement: Optional Legacy Workspace Host
+Legacy overview detection SHALL return the established non-overview result when WorkspaceTabs or its optional active-card API is absent. When a card exists, overview identity and the existing historical session cutoff SHALL retain their behavior. This optional-host boundary SHALL NOT weaken required shared asset or utility checks elsewhere.
+
+#### Scenario: Legacy shell has no WorkspaceTabs
+- **WHEN** isLegacyOverviewContext is invoked with WorkspaceTabs absent or null
+- **THEN** it returns false without throwing
+- **AND** the existing legacy-host behavioral test remains unchanged
+
+#### Scenario: Overview and child identities remain distinct
+- **WHEN** detection receives a child card, an old overview card, or a current overview card
+- **THEN** it preserves the established card identity and cutoff results
+
+### Requirement: Backend-Neutral Shared Membership Read
+Native/browser shared-membership queries SHALL use the existing storage-adapter boundary for both SQLite and TSV. Membership SHALL derive from authoritative session records using the write path's lemma and selection normalization. Querying SHALL preserve session isolation, authentication, the compatible membership/event response shape, and the configured delivery policy. The controller SHALL NOT assume every adapter exposes a database object. Reads SHALL preserve application records and database/WAL bytes, reject SQL writes, and create no save echo, application temporary file, or delivery/compatibility artifact. SQLite internal WAL/SHM bookkeeping MAY occur; literal zero filesystem writes is not required. Unavailable or invalid session data SHALL produce an explicit structured failure rather than fabricated empty membership.
+
+#### Scenario: Native polling uses TSV storage
+- **GIVEN** a valid TSV-backed session with selected and unselected rows
+- **WHEN** its native consumer polls with valid authentication
+- **THEN** the endpoint succeeds with membership matching the authoritative rows
+- **AND** it produces no AttributeError, HTTP 500, or filesystem writes
+
+#### Scenario: SQLite and TSV round trips agree
+- **WHEN** the same select/deselect commands are saved and restored through each backend
+- **THEN** both queries return the same normalized-lemma membership for that session
+- **AND** unrelated sessions and occurrence/export coordinates remain unchanged
+
+#### Scenario: SQLite polling observes committed live-WAL data
+- **GIVEN** a writer connection remains open after committing selection data to WAL
+- **WHEN** a cold query-only connection polls that session
+- **THEN** membership includes the committed selection without changing application records or database/WAL bytes
+- **AND** SQL writes are rejected and no save echo or delivery artifact is produced
+- **AND** SQLite may update internal SHM reader bookkeeping while the observed path set and SHM size remain unchanged
```
