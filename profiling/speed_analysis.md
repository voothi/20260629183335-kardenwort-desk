# Performance Dynamics Over Time (By Git Commit)

## Table of Contents
- [[Commit: 39a6725] 20260809094248](#commit-39a6725-20260809094248)
- [Golden Run Aggregates](#golden-run-aggregates)
- [Phase Glossary](#phase-glossary)

## [Commit: 39a6725] 20260809094248
```text
Run Session: 20260807230100 [Golden EN] (Total Batch E2E Duration: 13.785s)
---------------------------------------------------------------------------
translate_text (00)                 | █                                        | 0.597s
lemmatization (00)                  | ████████                                 | 2.936s
the_cut (00)                        |           █                              | 0.012s
intellifiller_enrichment (00)       |           █████████████████████████████  | 10.115s
html_generation (03)                |             █                            | 0.003s
background_text_translation (03)    |              █████████████████████████   | 8.953s
intellifiller_enrichment (03)       |              █████████████████████████   | 8.936s
html_generation (02)                |               █                          | 0.003s
background_text_translation (02)    |                ███                       | 1.168s
intellifiller_enrichment (02)       |                ███                       | 1.152s
html_generation (01)                |                 █                        | 0.003s
background_text_translation (01)    |                  █████                   | 1.882s
intellifiller_enrichment (01)       |                  █████                   | 1.867s
html_generation (00)                |                                        █ | 0.005s

```

## Golden Run Aggregates

### 20260807230100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `intellifiller_enrichment` | 8 | 1.044 | 5.839 | 11.339 |
| `background_text_translation` | 6 | 1.053 | 4.225 | 10.546 |
| `lemmatization` | 2 | 2.748 | 2.842 | 2.936 |
| `translate_text` | 2 | 0.597 | 0.627 | 0.656 |
| `the_cut` | 2 | 0.012 | 0.013 | 0.015 |
| `html_generation` | 8 | 0.003 | 0.003 | 0.005 |

## Phase Glossary
- **`translate_text`**: (Network IO-Bound) Holistically translating the source paragraph/sentence via external APIs (e.g. DeepL).
- **`lemmatization`**: (CPU-Bound) Tokenizing text and executing morphological/Anki lookups via Kardenwort Core to generate the data grid.
- **`the_cut`**: (CPU-Bound) Slicing the master TSV into individual child sentence files during Multi-mode runs.
- **`background_text_translation`**: The progressive worker updating BOTH the text translation AND the individual base lemma translations asynchronously without blocking the UI.
