# Performance Dynamics Over Time (By Git Commit)

## Table of Contents
- [[Commit: b5853a7] 20260808221702](#commit-b5853a7-20260808221702)
- [Golden Run Aggregates](#golden-run-aggregates)
- [Phase Glossary](#phase-glossary)

## [Commit: b5853a7] 20260808221702 (Avg wait between runs: 12.34s)
```text
Run Session: 20260807200200 [Golden DE] (Total Batch E2E Duration: 14.873s)
---------------------------------------------------------------------------
translate_text (00)                 | ██                                       | 0.981s
lemmatization (00)                  | ██████████████                           | 5.385s
the_cut (00)                        |               █                          | 0.030s
html_generation (08)                |                 █                        | 0.003s
background_text_translation (08)    |                  ██████████████          | 5.209s
html_generation (07)                |                   █                      | 0.003s
background_text_translation (07)    |                    ██                    | 1.097s
html_generation (06)                |                    █                     | 0.003s
background_text_translation (06)    |                      █                   | 0.582s
html_generation (00)                |                      █                   | 0.006s
html_generation (05)                |                       █                  | 0.003s
background_text_translation (05)    |                        ████              | 1.630s
html_generation (04)                |                          █               | 0.003s
background_text_translation (04)    |                           █████          | 2.090s
html_generation (03)                |                            █             | 0.003s
background_text_translation (03)    |                             ███████      | 2.948s
html_generation (02)                |                               █          | 0.004s
background_text_translation (02)    |                                ███████   | 2.966s
html_generation (01)                |                                 █        | 0.004s
background_text_translation (01)    |                                  ██████  | 2.423s

Run Session: 20260807200100 [Golden EN] (Total Batch E2E Duration: 7.581s)
---------------------------------------------------------------------------
translate_text (00)                 | █████                                    | 1.067s
lemmatization (00)                  | █████████████                            | 2.541s
the_cut (00)                        |                   █                      | 0.011s
html_generation (03)                |                      █                   | 0.003s
background_text_translation (03)    |                       █████████████████  | 3.245s
html_generation (02)                |                         █                | 0.002s
html_generation (00)                |                          █               | 0.005s
background_text_translation (02)    |                           █████          | 1.070s
html_generation (01)                |                            █             | 0.003s
background_text_translation (01)    |                              █████       | 1.069s

Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 9.098s)
---------------------------------------------------------------------------
lemmatization (00)                  | ██████████████████                       | 4.252s
translate_text (00)                 | ████                                     | 0.970s
the_cut (00)                        |                   █                      | 0.030s
html_generation (00)                |                     █                    | 0.006s
html_generation (08)                |                      █                   | 0.003s
html_generation (07)                |                         █                | 0.003s
html_generation (06)                |                           █              | 0.002s
html_generation (05)                |                              █           | 0.002s
html_generation (04)                |                                █         | 0.002s
html_generation (03)                |                                   █      | 0.003s
html_generation (02)                |                                      █   | 0.003s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 5.820s)
---------------------------------------------------------------------------
translate_text (00)                 | ███████                                  | 1.037s
lemmatization (00)                  | ██████████████████                       | 2.700s
the_cut (00)                        |                             █            | 0.011s
html_generation (00)                |                             █            | 0.004s
html_generation (03)                |                                █         | 0.003s
html_generation (02)                |                                    █     | 0.003s
html_generation (01)                |                                        █ | 0.003s

```

## Golden Run Aggregates

### 20260807190100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 1 | 2.700 | 2.700 | 2.700 |
| `translate_text` | 1 | 1.037 | 1.037 | 1.037 |
| `the_cut` | 1 | 0.011 | 0.011 | 0.011 |
| `html_generation` | 4 | 0.003 | 0.003 | 0.004 |

### 20260807190200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 1 | 4.252 | 4.252 | 4.252 |
| `translate_text` | 1 | 0.970 | 0.970 | 0.970 |
| `the_cut` | 1 | 0.030 | 0.030 | 0.030 |
| `html_generation` | 9 | 0.002 | 0.003 | 0.006 |

### 20260807200100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 1 | 2.541 | 2.541 | 2.541 |
| `background_text_translation` | 3 | 1.069 | 1.795 | 3.245 |
| `translate_text` | 1 | 1.067 | 1.067 | 1.067 |
| `the_cut` | 1 | 0.011 | 0.011 | 0.011 |
| `html_generation` | 4 | 0.002 | 0.003 | 0.005 |

### 20260807200200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 1 | 5.385 | 5.385 | 5.385 |
| `background_text_translation` | 8 | 0.582 | 2.368 | 5.209 |
| `translate_text` | 1 | 0.981 | 0.981 | 0.981 |
| `the_cut` | 1 | 0.030 | 0.030 | 0.030 |
| `html_generation` | 9 | 0.003 | 0.004 | 0.006 |

## Phase Glossary
- **`translate_text`**: (Network IO-Bound) Holistically translating the source paragraph/sentence via external APIs (e.g. DeepL).
- **`lemmatization`**: (CPU-Bound) Tokenizing text and executing morphological/Anki lookups via Kardenwort Core to generate the data grid.
- **`the_cut`**: (CPU-Bound) Slicing the master TSV into individual child sentence files during Multi-mode runs.
- **`background_text_translation`**: The progressive worker updating BOTH the text translation AND the individual base lemma translations asynchronously without blocking the UI.
