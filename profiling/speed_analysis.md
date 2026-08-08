# Performance Dynamics Over Time (By Git Commit)

## Table of Contents
- [[Commit: 5813c1f] 20260808223948](#commit-5813c1f-20260808223948)
- [Golden Run Aggregates](#golden-run-aggregates)
- [Phase Glossary](#phase-glossary)

## [Commit: 5813c1f] 20260808223948 (Avg wait between runs: 12.05s)
```text
Run Session: 20260807210200 [Golden DE] (Total Batch E2E Duration: 13.299s)
---------------------------------------------------------------------------
translate_text (00)                 | ████████████████████████                 | 8.030s
lemmatization (00)                  | ██████████████████                       | 6.308s
the_cut (00)                        |                         █                | 0.030s
html_generation (00)                |                          █               | 0.006s
html_generation (08)                |                          █               | 0.003s
html_generation (07)                |                            █             | 0.002s
html_generation (06)                |                              █           | 0.002s
html_generation (05)                |                                █         | 0.002s
html_generation (04)                |                                  █       | 0.003s
html_generation (03)                |                                    █     | 0.003s
html_generation (02)                |                                       █  | 0.003s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807210100 [Golden EN] (Total Batch E2E Duration: 11.715s)
---------------------------------------------------------------------------
translate_text (00)                 | █████████████████████████████████        | 9.850s
lemmatization (00)                  | ███████████                              | 3.343s
the_cut (00)                        |                                  █       | 0.011s
html_generation (00)                |                                   █      | 0.005s
html_generation (03)                |                                    █     | 0.003s
html_generation (02)                |                                      █   | 0.002s
html_generation (01)                |                                        █ | 0.002s

Run Session: 20260807200200 [Golden DE] (Total Batch E2E Duration: 13.367s)
---------------------------------------------------------------------------
translate_text (00)                 | █                                        | 0.547s
lemmatization (00)                  | ████████████                             | 4.341s
the_cut (00)                        |              █                           | 0.029s
html_generation (08)                |               █                          | 0.003s
background_text_translation (08)    |                 █████████████            | 4.479s
html_generation (07)                |                  █                       | 0.003s
background_text_translation (07)    |                   ███                    | 1.119s
html_generation (06)                |                    █                     | 0.003s
html_generation (00)                |                     █                    | 0.007s
background_text_translation (06)    |                     █                    | 0.578s
html_generation (05)                |                      █                   | 0.003s
background_text_translation (05)    |                        ███               | 1.112s
html_generation (04)                |                         █                | 0.003s
background_text_translation (04)    |                          █████           | 1.731s
html_generation (03)                |                           █              | 0.003s
background_text_translation (03)    |                             ████████     | 2.829s
html_generation (02)                |                              █           | 0.004s
background_text_translation (02)    |                               ████████   | 2.728s
html_generation (01)                |                                 █        | 0.004s
background_text_translation (01)    |                                  ██████  | 2.258s

Run Session: 20260807200100 [Golden EN] (Total Batch E2E Duration: 7.307s)
---------------------------------------------------------------------------
translate_text (00)                 | ██                                       | 0.482s
lemmatization (00)                  | █████████████                            | 2.557s
the_cut (00)                        |                 █                        | 0.011s
html_generation (03)                |                    █                     | 0.003s
html_generation (00)                |                    █                     | 0.004s
background_text_translation (03)    |                      ██████████████████  | 3.406s
html_generation (02)                |                       █                  | 0.003s
background_text_translation (02)    |                         █████            | 0.965s
html_generation (01)                |                           █              | 0.002s
background_text_translation (01)    |                             █████        | 0.987s

Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 9.218s)
---------------------------------------------------------------------------
translate_text (00)                 | ██                                       | 0.488s
lemmatization (00)                  | ██████████████████                       | 4.272s
the_cut (00)                        |                   █                      | 0.029s
html_generation (00)                |                    █                     | 0.006s
html_generation (08)                |                      █                   | 0.003s
html_generation (07)                |                        █                 | 0.002s
html_generation (06)                |                           █              | 0.002s
html_generation (05)                |                              █           | 0.002s
html_generation (04)                |                                █         | 0.003s
html_generation (03)                |                                   █      | 0.003s
html_generation (02)                |                                      █   | 0.003s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 5.374s)
---------------------------------------------------------------------------
translate_text (00)                 | ████                                     | 0.589s
lemmatization (00)                  | ██████████████████████                   | 3.036s
the_cut (00)                        |                           █              | 0.011s
html_generation (00)                |                           █              | 0.004s
html_generation (03)                |                               █          | 0.003s
html_generation (02)                |                                    █     | 0.003s
html_generation (01)                |                                        █ | 0.002s

```

## Golden Run Aggregates

### 20260807190100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 1 | 3.036 | 3.036 | 3.036 |
| `translate_text` | 1 | 0.589 | 0.589 | 0.589 |
| `the_cut` | 1 | 0.011 | 0.011 | 0.011 |
| `html_generation` | 4 | 0.002 | 0.003 | 0.004 |

### 20260807190200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 1 | 4.272 | 4.272 | 4.272 |
| `translate_text` | 1 | 0.488 | 0.488 | 0.488 |
| `the_cut` | 1 | 0.029 | 0.029 | 0.029 |
| `html_generation` | 9 | 0.002 | 0.003 | 0.006 |

### 20260807200100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 1 | 2.557 | 2.557 | 2.557 |
| `background_text_translation` | 3 | 0.965 | 1.786 | 3.406 |
| `translate_text` | 1 | 0.482 | 0.482 | 0.482 |
| `the_cut` | 1 | 0.011 | 0.011 | 0.011 |
| `html_generation` | 4 | 0.002 | 0.003 | 0.004 |

### 20260807200200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 1 | 4.341 | 4.341 | 4.341 |
| `background_text_translation` | 8 | 0.578 | 2.104 | 4.479 |
| `translate_text` | 1 | 0.547 | 0.547 | 0.547 |
| `the_cut` | 1 | 0.029 | 0.029 | 0.029 |
| `html_generation` | 9 | 0.003 | 0.004 | 0.007 |

### 20260807210100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `translate_text` | 1 | 9.850 | 9.850 | 9.850 |
| `lemmatization` | 1 | 3.343 | 3.343 | 3.343 |
| `the_cut` | 1 | 0.011 | 0.011 | 0.011 |
| `html_generation` | 4 | 0.002 | 0.003 | 0.005 |

### 20260807210200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `translate_text` | 1 | 8.030 | 8.030 | 8.030 |
| `lemmatization` | 1 | 6.308 | 6.308 | 6.308 |
| `the_cut` | 1 | 0.030 | 0.030 | 0.030 |
| `html_generation` | 9 | 0.002 | 0.003 | 0.006 |

## Phase Glossary
- **`translate_text`**: (Network IO-Bound) Holistically translating the source paragraph/sentence via external APIs (e.g. DeepL).
- **`lemmatization`**: (CPU-Bound) Tokenizing text and executing morphological/Anki lookups via Kardenwort Core to generate the data grid.
- **`the_cut`**: (CPU-Bound) Slicing the master TSV into individual child sentence files during Multi-mode runs.
- **`background_text_translation`**: The progressive worker updating BOTH the text translation AND the individual base lemma translations asynchronously without blocking the UI.
