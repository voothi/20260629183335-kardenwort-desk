# Performance Dynamics Over Time (By Git Commit)

## Table of Contents
- [[Commit: 07f3a2a] 20260809013616](#commit-07f3a2a-20260809013616)
- [Golden Run Aggregates](#golden-run-aggregates)
- [Phase Glossary](#phase-glossary)

## [Commit: 07f3a2a] 20260809013616 (Avg wait between runs: 13949.76s)
```text
Run Session: 20260807230200 [Golden DE] (Total Batch E2E Duration: 26.552s)
---------------------------------------------------------------------------
translate_text (00)                 | █                                        | 0.682s
lemmatization (00)                  | ███████                                  | 5.021s
the_cut (00)                        |        █                                 | 0.029s
intellifiller_enrichment (00)       |        ███████████████████████████████   | 21.223s
html_generation (08)                |         █                                | 0.004s
background_text_translation (08)    |         █████████████                    | 8.853s
intellifiller_enrichment (08)       |         █████████████                    | 8.840s
html_generation (07)                |          █                               | 0.003s
background_text_translation (07)    |           ██████                         | 4.248s
intellifiller_enrichment (07)       |           ██████                         | 4.235s
html_generation (06)                |           █                              | 0.002s
background_text_translation (06)    |           █                              | 1.122s
intellifiller_enrichment (06)       |           █                              | 1.106s
html_generation (05)                |            █                             | 0.003s
intellifiller_enrichment (05)       |            ███                           | 2.372s
background_text_translation (05)    |            ███                           | 2.387s
html_generation (04)                |             █                            | 0.003s
background_text_translation (04)    |             ██████                       | 4.204s
intellifiller_enrichment (04)       |             ██████                       | 4.190s
html_generation (03)                |              █                           | 0.004s
background_text_translation (03)    |               ██████                     | 4.424s
intellifiller_enrichment (03)       |               ██████                     | 4.408s
html_generation (02)                |               █                          | 0.004s
background_text_translation (02)    |                ██████                    | 4.556s
intellifiller_enrichment (02)       |                ██████                    | 4.529s
html_generation (01)                |                █                         | 0.004s
background_text_translation (01)    |                █████                     | 3.441s
intellifiller_enrichment (01)       |                █████                     | 3.427s
html_generation (00)                |                                        █ | 0.005s

Run Session: 20260807230100 [Golden EN] (Total Batch E2E Duration: 12.799s)
---------------------------------------------------------------------------
translate_text (00)                 | █                                        | 0.601s
lemmatization (00)                  | ███████                                  | 2.555s
the_cut (00)                        |          █                               | 0.010s
intellifiller_enrichment (00)       |           █████████████████████████████  | 9.548s
html_generation (03)                |            █                             | 0.003s
background_text_translation (03)    |             ████████████████████████     | 7.763s
intellifiller_enrichment (03)       |             ████████████████████████     | 7.747s
html_generation (02)                |              █                           | 0.002s
background_text_translation (02)    |               ███                        | 1.080s
intellifiller_enrichment (02)       |               ███                        | 1.074s
html_generation (01)                |                █                         | 0.003s
background_text_translation (01)    |                 ██████                   | 1.934s
intellifiller_enrichment (01)       |                 █████                    | 1.918s
html_generation (00)                |                                        █ | 0.003s

Run Session: 20260807210200 [Golden DE] (Total Batch E2E Duration: 274.257s)
---------------------------------------------------------------------------
background_text_translation (08)    | ████████████████████████████████████████ | 274.257s

```

## Golden Run Aggregates

### 20260807210200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `background_text_translation` | 1 | 274.257 | 274.257 | 274.257 |

### 20260807230100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `intellifiller_enrichment` | 29 | 0.539 | 15.753 | 120.158 |
| `background_text_translation` | 21 | 0.547 | 9.484 | 120.047 |
| `lemmatization` | 7 | 2.555 | 2.946 | 4.328 |
| `translate_text` | 7 | 0.538 | 0.631 | 0.870 |
| `the_cut` | 7 | 0.010 | 0.012 | 0.013 |
| `html_generation` | 27 | 0.002 | 0.003 | 0.004 |

### 20260807230200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `intellifiller_enrichment` | 18 | 1.106 | 35.647 | 120.053 |
| `background_text_translation` | 16 | 1.122 | 31.294 | 103.828 |
| `lemmatization` | 1 | 5.021 | 5.021 | 5.021 |
| `translate_text` | 1 | 0.682 | 0.682 | 0.682 |
| `the_cut` | 1 | 0.029 | 0.029 | 0.029 |
| `html_generation` | 9 | 0.002 | 0.003 | 0.005 |

## Phase Glossary
- **`translate_text`**: (Network IO-Bound) Holistically translating the source paragraph/sentence via external APIs (e.g. DeepL).
- **`lemmatization`**: (CPU-Bound) Tokenizing text and executing morphological/Anki lookups via Kardenwort Core to generate the data grid.
- **`the_cut`**: (CPU-Bound) Slicing the master TSV into individual child sentence files during Multi-mode runs.
- **`background_text_translation`**: The progressive worker updating BOTH the text translation AND the individual base lemma translations asynchronously without blocking the UI.
