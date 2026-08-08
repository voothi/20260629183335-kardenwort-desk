# Performance Dynamics Over Time (By Git Commit)

## Table of Contents
- [[Commit: a45dffb] 20260808231515](#commit-a45dffb-20260808231515)
- [[Commit: 871b3dd] 20260808234145](#commit-871b3dd-20260808234145)
- [Golden Run Aggregates](#golden-run-aggregates)
- [Phase Glossary](#phase-glossary)

## [Commit: a45dffb] 20260808231515 (Avg wait between runs: 12.76s)
```text
Run Session: 20260807230200 [Golden DE] (Total Batch E2E Duration: 9.099s)
---------------------------------------------------------------------------
translate_text (00)                 | ██████                                   | 1.439s
lemmatization (00)                  | ██████████████████                       | 4.143s
the_cut (00)                        |                   █                      | 0.031s
html_generation (00)                |                    █                     | 0.006s
html_generation (08)                |                     █                    | 0.003s
html_generation (07)                |                        █                 | 0.002s
html_generation (06)                |                           █              | 0.002s
html_generation (05)                |                             █            | 0.002s
html_generation (04)                |                                █         | 0.002s
html_generation (03)                |                                   █      | 0.003s
html_generation (02)                |                                      █   | 0.003s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807230100 [Golden EN] (Total Batch E2E Duration: 5.238s)
---------------------------------------------------------------------------
translate_text (00)                 | ████                                     | 0.581s
lemmatization (00)                  | ████████████████████                     | 2.628s
the_cut (00)                        |                           █              | 0.014s
html_generation (00)                |                            █             | 0.004s
html_generation (03)                |                               █          | 0.003s
html_generation (02)                |                                    █     | 0.003s
html_generation (01)                |                                        █ | 0.002s

```

## [Commit: 871b3dd] 20260808234145 (Avg wait between runs: -2963.12s)
```text
Run Session: 20260807230200 [Golden DE] (Total Batch E2E Duration: 10.590s)
---------------------------------------------------------------------------
lemmatization (00)                  | ████████████████                         | 4.358s
translate_text (00)                 | █                                        | 0.516s
the_cut (00)                        |                 █                        | 0.028s
intellifiller_enrichment (00)       |                  █                       | 0.305s
html_generation (08)                |                   █                      | 0.003s
html_generation (00)                |                   █                      | 0.006s
background_text_translation (08)    |                     █                    | 0.006s
intellifiller_enrichment (08)       |                     █                    | 0.297s
html_generation (07)                |                      █                   | 0.003s
background_text_translation (07)    |                       █                  | 0.005s
intellifiller_enrichment (07)       |                       █                  | 0.384s
html_generation (06)                |                         █                | 0.003s
background_text_translation (06)    |                          █               | 0.004s
intellifiller_enrichment (06)       |                          █               | 0.290s
html_generation (05)                |                           █              | 0.003s
background_text_translation (05)    |                             █            | 0.006s
intellifiller_enrichment (05)       |                             █            | 0.289s
html_generation (04)                |                              █           | 0.003s
background_text_translation (04)    |                                █         | 0.006s
intellifiller_enrichment (04)       |                                █         | 0.292s
html_generation (03)                |                                 █        | 0.004s
background_text_translation (03)    |                                  █       | 0.003s
intellifiller_enrichment (03)       |                                  █       | 0.292s
html_generation (02)                |                                    █     | 0.004s
background_text_translation (02)    |                                     █    | 0.004s
intellifiller_enrichment (02)       |                                     █    | 0.295s
html_generation (01)                |                                      █   | 0.003s
background_text_translation (01)    |                                       █  | 0.003s
intellifiller_enrichment (01)       |                                       █  | 0.269s

Run Session: 20260807230100 [Golden EN] (Total Batch E2E Duration: 6.222s)
---------------------------------------------------------------------------
translate_text (00)                 | ███                                      | 0.565s
lemmatization (00)                  | ██████████████████                       | 2.945s
the_cut (00)                        |                       █                  | 0.015s
html_generation (00)                |                        █                 | 0.019s
html_generation (03)                |                             █            | 0.003s
html_generation (02)                |                                    █     | 0.003s
html_generation (01)                |                                        █ | 0.002s

```

## Golden Run Aggregates

### 20260807230100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 6 | 2.580 | 2.807 | 2.945 |
| `intellifiller_enrichment` | 16 | 0.276 | 2.469 | 15.356 |
| `translate_text` | 6 | 0.562 | 0.609 | 0.722 |
| `the_cut` | 6 | 0.011 | 0.014 | 0.016 |
| `html_generation` | 24 | 0.002 | 0.004 | 0.019 |
| `background_text_translation` | 12 | 0.003 | 0.004 | 0.006 |

### 20260807230200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 2 | 4.143 | 4.251 | 4.358 |
| `translate_text` | 2 | 0.516 | 0.978 | 1.439 |
| `intellifiller_enrichment` | 9 | 0.269 | 0.302 | 0.384 |
| `the_cut` | 2 | 0.028 | 0.030 | 0.031 |
| `background_text_translation` | 8 | 0.003 | 0.005 | 0.006 |
| `html_generation` | 18 | 0.002 | 0.003 | 0.006 |

## Phase Glossary
- **`translate_text`**: (Network IO-Bound) Holistically translating the source paragraph/sentence via external APIs (e.g. DeepL).
- **`lemmatization`**: (CPU-Bound) Tokenizing text and executing morphological/Anki lookups via Kardenwort Core to generate the data grid.
- **`the_cut`**: (CPU-Bound) Slicing the master TSV into individual child sentence files during Multi-mode runs.
- **`background_text_translation`**: The progressive worker updating BOTH the text translation AND the individual base lemma translations asynchronously without blocking the UI.
