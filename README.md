# kardenwort-desk

Python orchestration core for the Kardenwort desktop window. It runs
[kardenwort](../20241223170748-kardenwort) to extract lemmas and sentence
translations, invokes [headless IntelliFiller](../20251206123938-intellifiller-ai-addon-for-anki)
to fill per-lemma translation columns (ru/de/ua/ipa/morphology), composes the
combined HTML view, and exports favorited rows to a kardenwort-schema TSV for
Anki import.

This repo is the **backend** — a *pure orchestration module* (stdlib-only
Python, no GUI/OS dependencies) with a thin CLI wrapper. The **display
frontend** is a per-platform shim; today it is an AutoHotkey v2 script:

> Display frontend (Windows, AHK v2): `U:/voothi/20240411110510-autohotkey`
> script: `kardenwort-window.ahk`

The frontend is display-only: it captures the selected text (via a per-platform
hotkey/intent), renders the HTML this core emits, lets the user mark rows as
favorites / edit cells, and forwards selections/deltas back here. It contains
no linguistic logic.

## Single Architectural Contract (portability anchor)

The boundary between frontend and backend is a **stable, transport-agnostic
contract**: CLI today (argv → stdout), HTTP later (same payloads/responses).
The orchestration logic — tokenizer, TSV, providers, merge, edit-race
prevention — is written once here and never rewritten. Porting to Linux/Mac
reuses this core unchanged (same stdlib); porting to mobile/web wraps the same
module in an HTTP entrypoint. Only the frontend shim (hotkey/intent capture +
HTML rendering) is rewritten per platform. Global hotkeys are inherently
per-platform (AHK on Windows; AutoKey/Hammerspoon on Linux/Mac; share-sheet on
mobile) — that non-portability is absorbed in the thin frontend, not here.

## CLI contract (called by the AHK shell / SendTo)

Render mode (produces HTML to stdout):

```
python kardenwort_desk.py render --text "<selected>" --language en --zid <session> [--config <path>] [--verbose | --debug]
```

Export mode (writes favorites TSV to the configured output dir):

```
python kardenwort_desk.py export --selection-manifest <path> --language en [--config <path>]
```

Edit-save mode (applies cell-edit deltas to the word TSV, atomic write):

```
python kardenwort_desk.py edit-save --deltas <deltas.json> --zid <session> [--language en] [--config <path>]
```

Merge mode (combines multiple TSV files into one, ordered by ZID):

```
python kardenwort_desk.py merge --files <f1.tsv> <f2.tsv> --target new [--config <path>]
```

Restore mode (opens a .txt or .tsv file and reconstitutes the desk window state):

```
python kardenwort_desk.py restore --file <ZID>-<slug>.txt [--config <path>]
```

All modes accept `--config <path>` (default: `config.ini` next to `kardenwort_desk.py`).
All paths, the favorites output directory, the headless IntelliFiller entrypoint
path, provider slots, and schema mapping are read from `config.ini`.

## Configuration (`config.ini`)

All sibling-project paths in `config.ini` are **relative, resolved from the
location of this `config.ini`** (mirroring kardenwort's own convention). The
sibling repos all live on the same level under `U:/voothi/`, so they are
referenced via `../`:

```ini
[environment]
; Python interpreter for kardenwort (spacy-env venv)
kardenwort_python = ../20250825231214-spacy-env/Scripts/python.exe
; Kardenwort project (script + data files)
kardenwort_workspace = ../20241223170748-kardenwort
; Deep-translator (Google + DeepL providers)
deep_translator_python = ../20241122093311-deep-translator/venv/Scripts/python.exe
translate_google_script = ../20241122093311-deep-translator/translate_google.py
translate_deepl_script = ../20241122093311-deep-translator/translate_deepl.py
; DeepL API key secrets — point at translate-selection's settings.ini which
; contains [Security] SecretsPath (the actual secrets.ini) + Salt.
; The desk core reads the salt + secrets path from settings.ini, then
; deobfuscates the key (base64+XOR with salt, %%SEC%% marker), mirroring
; translate-selection.ahk's GetDeepLKey pattern.
deepl_settings_file = ../20240411110510-autohotkey/translate-selection/settings.ini
; IntelliFiller headless entrypoint (resolve to the installed entrypoint module)
intellifiller_headless = ../20251206123938-intellifiller-ai-addon-for-anki/IntelliFiller/headless_entrypoint.py

[translation_providers]
; Each slot: google | intellifiller | deepl | combined
main_text_translation = combined
lemmas_translation = combined
; Pass --use-local-fork to translate scripts (default: true)
use_local_fork = true

[settings]
default_language = en
default_target_language = ru
; Favorites TSV output directory (relative or absolute)
favorites_output_dir = ./favorites
; Standalone schema-mapping file (same pattern as kardenwort-mpv's anki-mapping.ini)
; NOTE: kardenwort-mpv's anki-mapping.ini has WordSource=source_word (older mapping);
; the desk core needs col_lemma=WordSource (kardenwort.py's mapping: WordSource=lemma).
; Either point at a desk-local copy with the [desk_columns] section added, or
; update the shared file to add [desk_columns] + [desk_editable].
anki_mapping_file = ./anki-mapping.ini
save_source_text = true
merge_delete_sources = false
; NOTE: file_watcher_interval_ms is a display concern and lives in the AHK-side
; config.ini [Settings] section (FileWatcherIntervalMs), not here.

[languages]
; per-language lemma data files (under kardenwort_workspace) + IntelliFiller prompt names
en_lemma_index = data/en/en-news-2023-1m-words.csv
en_lemma_override = data/en/lemma_override_en.tsv
en_prompt = English Vocabulary Analysis and Translation JSON
de_lemma_index = data/de/deu-mixed-typical-2011-1m-words.csv
de_lemma_override = data/de/lemma_override_de.tsv
de_prompt = <TBD>

[timeouts]
; Subprocess timeout in seconds (kills the subprocess on expiry; never hangs)
translation_timeout = 60
intellifiller_timeout = 120
```

Paths are validated at startup; a missing path produces a clear error naming
the offending key.

## Related

- Kardenwort (lemma/sentence extraction + import): `U:/voothi/20241223170748-kardenwort`
- IntelliFiller (headless LLM field-filling): `U:/voothi/20251206123938-intellifiller-ai-addon-for-anki`
- AHK display frontend: `U:/voothi/20240411110510-autohotkey`
- OpenSpec change: `autohotkey/openspec/changes/20260629172653-kardenwort-window`

## Processing pipeline (file-on-disk, SendTo per-stage)

Each stage operates on files on disk and is independently re-runnable via
Windows SendTo. **All state between stages is carried exclusively by the
standard kardenwort TSV/JSON files** (schema from `kardenwort.py` +
`config.ini [anki_fields]`) — no hidden/in-memory state crosses a stage
boundary. Pipeline composition (which stages run, in which order) is
config-driven, so the final Anki import can be dropped (e.g.
`stages = extract, fill`) leaving the filled TSV+JSON on disk. If Import
fails, the files remain on disk for a manual retry via SendTo.

```
Stage 1   Extract   (kardenwort)        .txt/.srt → triple.word.<lang>.tsv (+ .json)
Stage 1b  Edit      (desk window)       inline Excel-like cell editing → atomic save of the word TSV
Stage 2   Fill      (headless IntelliFiller)  word TSV → columns filled in place
Stage 3   Import    (kardenwort_runner --import-only)  filled TSV + .json → Anki
```

This core (kardenwort-desk) drives the same stages for the live window, and
its "export favorites" produces a TSV that re-enters at Stage 2 or 3. Inline
lemma/translation edits are held in-memory until an explicit Save (button /
Ctrl+S), then written atomically by this core so a crash never corrupts the
TSV.

## Inflection → lemma link & bidirectional selection

The core tokenizes the original text (Python port of the kardenwort-mpv
tokenizer) and exposes a token→lemma map derived from kardenwort's
`Quotation`/`WordSource`/`WordSourceInflectedForm` columns. The window uses
this for **bidirectional selection**: selecting word(s) in the original text
highlights the corresponding lemma row(s) in the table, and vice versa.

The lemma table is **frequency-ordered** (most frequent lemma first, per the
language's `--lemma-index-file`).

Desk window layout:
```
Original text.                       ← tokenized, selectable
Translation of the original text.    ← sentence translation
Table with lemmas and their translation.  ← frequency-ordered, editable, bidirectional link
```

## Multi-session reliability

Many desk windows can run simultaneously, each with its own text and
independent save/close lifecycle — they never conflict. Each window session
gets a unique **ZID** (14-digit timestamp + process-unique suffix if needed);
its working TSV is ZID-prefixed, so two windows never write the same file.
All TSV writes are **atomic** (temp → backup-rename → atomic promote →
rollback-on-failure, per kardenwort-quiz's `save_tsv`), and an stdlib
**advisory file lock** guards the rare same-file case. The session ZID is the
end-to-end trace key across window, working TSV, temp/backup files, and logs.

## TSV merge utility (SendTo "Kardenwort Merge")

Combines multiple selected `triple.word.<lang>.tsv` files into one, ordered by
**ZID (timestamp)** — for grouping parts/individual files into one logically
complete study unit (easier to study). Installed via `install.py` as a SendTo
entrypoint.

Merge target options:
- **Create new** (default): `<current-ZID>-merged.<lang>.tsv`
- **Append to first**: earliest-ZID file becomes the target
- **Append to chosen**: pick the target from a dropdown in the desk window

Source files are kept by default (non-destructive); `merge_delete_sources`
in config enables deletion after a verified merge. The merged file is written
atomically; mismatched schemas are refused.

## Source text saving & session restore

When a session produces its working TSV, the desk core also saves the original
source text as a `.txt` file with the **same ZID prefix**
(`<ZID>-<slug>.txt` next to `<ZID>-<slug>.<lang>.tsv`). This is gated by config
option `save_source_text` (default: enabled).

The **"Kardenwort Desk Restore"** SendTo entrypoint (via `install.py`) opens
a `.txt` or `.tsv` file, finds its sibling (same ZID prefix, other extension),
and reconstitutes the desk window's working state (source text + lemma table +
translations + edit state) for continued work. If the sibling file is missing,
it opens with what's available and warns.