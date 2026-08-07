# Performance Dynamics Over Time (By Git Commit)

## Table of Contents
- [[Commit: 503019f] 20260807221238](#commit-503019f-20260807221238)
- [[Commit: 0fbc1c5] 20260807230723](#commit-0fbc1c5-20260807230723)
- [[Commit: 8122154] 20260807233307](#commit-8122154-20260807233307)
- [[Commit: 209b691] 20260808000327](#commit-209b691-20260808000327)
- [Golden Run Aggregates](#golden-run-aggregates)
- [Phase Glossary](#phase-glossary)

## [Commit: 503019f] 20260807221238
```text
Run Session: 202608071901** [Golden EN] (Total Batch E2E Duration: 2.362s)
---------------------------------------------------------------------------
translate_text (00)            | ████████████████████                     | 1.229s
lemmatization (00)             |                                        █ | 0.010s
the_cut (00)                   |                                        █ | 0.015s

```

## [Commit: 0fbc1c5] 20260807230723
```text
Run Session: 202608071901** [Golden EN] (Total Batch E2E Duration: 5.031s)
---------------------------------------------------------------------------
translate_text (00)            | ████████                                 | 1.112s
lemmatization (00)             |                  ██████████████████████  | 2.854s
the_cut (00)                   |                                        █ | 0.015s

```

## [Commit: 8122154] 20260807233307
```text
Run Session: 202608071901** [Golden EN] (Total Batch E2E Duration: 4.784s)
---------------------------------------------------------------------------
translate_text (00)            | ████████                                 | 1.014s
lemmatization (00)             |                  ██████████████████████  | 2.631s
the_cut (00)                   |                                        █ | 0.011s

```

## [Commit: 209b691] 20260808000327
```text
Run Session: 202608071902** [Golden DE] (Total Batch E2E Duration: 35.244s)
---------------------------------------------------------------------------
translate_text (00)            | █                                        | 1.022s
lemmatization (00)             |  █████                                   | 5.280s
the_cut (00)                   |        █                                 | 0.029s
background_text_translation (00) |                          ██████████████  | 12.557s
background_text_translation (01) |                                      ██  | 2.291s

Run Session: 202608071901** [Golden EN] (Total Batch E2E Duration: 5.452s)
---------------------------------------------------------------------------
translate_text (00)            | ████████                                 | 1.101s
lemmatization (00)             |                ████████████████████████  | 3.289s
the_cut (00)                   |                                        █ | 0.012s

```

## Golden Run Aggregates
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `background_text_translation` | 2 | 2.291 | 7.424 | 12.557 |
| `lemmatization` | 5 | 0.010 | 2.813 | 5.280 |
| `translate_text` | 5 | 1.014 | 1.096 | 1.229 |
| `the_cut` | 5 | 0.011 | 0.017 | 0.029 |

## Phase Glossary
- **`translate_text`**: (Network IO-Bound) Holistically translating the source paragraph/sentence via external APIs (e.g. DeepL).
- **`lemmatization`**: (CPU-Bound) Tokenizing text and executing morphological/Anki lookups via Kardenwort Core to generate the data grid.
- **`the_cut`**: (CPU-Bound) Slicing the master TSV into individual child sentence files during Multi-mode runs.
- **`background_text_translation`**: The progressive worker updating BOTH the text translation AND the individual base lemma translations asynchronously without blocking the UI.
