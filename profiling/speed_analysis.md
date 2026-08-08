# Performance Dynamics Over Time (By Git Commit)

## Table of Contents
- [[Commit: 503019f] 20260807221238](#commit-503019f-20260807221238)
- [[Commit: 0fbc1c5] 20260807230723](#commit-0fbc1c5-20260807230723)
- [[Commit: 8122154] 20260807233307](#commit-8122154-20260807233307)
- [[Commit: 209b691] 20260808000327](#commit-209b691-20260808000327)
- [[Commit: b3b7063] 20260808013600](#commit-b3b7063-20260808013600)
- [[Commit: e3eaf56] 20260808015255](#commit-e3eaf56-20260808015255)
- [[Commit: 56aa451] 20260808015914](#commit-56aa451-20260808015914)
- [Golden Run Aggregates](#golden-run-aggregates)
- [Phase Glossary](#phase-glossary)

## [Commit: 503019f] 20260807221238
```text
Run Session: 202608072212** [Unknown] (Total Batch E2E Duration: 9.563s)
---------------------------------------------------------------------------
translate_text (46)            | █████                                    | 1.257s
lemmatization (46)             |           ████████████████████████████   | 6.906s
the_cut (46)                   |                                        █ | 0.033s

Run Session: 202608071901** [Unknown] (Total Batch E2E Duration: 2.362s)
---------------------------------------------------------------------------
translate_text (00)            | ████████████████████                     | 1.229s
lemmatization (00)             |                                        █ | 0.010s
the_cut (00)                   |                                        █ | 0.015s

```

## [Commit: 0fbc1c5] 20260807230723
```text
Run Session: 202608072323** [Unknown] (Total Batch E2E Duration: 27.062s)
---------------------------------------------------------------------------
translate_text (51)            | █                                        | 0.937s
lemmatization (51)             |  ████                                    | 2.898s
the_cut (51)                   |      █                                   | 0.015s
background_text_translation (54) |                     ███████              | 4.781s
background_text_translation (53) |                          ████            | 3.206s
background_text_translation (51) |                            ████████████  | 8.551s

Run Session: 202608072322** [Unknown] (Total Batch E2E Duration: 26.606s)
---------------------------------------------------------------------------
lemmatization (18)             | ███                                      | 2.563s
background_text_translation (18) |         ████                             | 3.310s
translate_text (18)            |         █                                | 1.032s
translate_text (18)            |             █                            | 0.728s
translate_text (41)            |                                   █      | 1.111s
lemmatization (41)             |                                     ███  | 2.507s
the_cut (41)                   |                                        █ | 0.011s

Run Session: 202608072321** [Unknown] (Total Batch E2E Duration: 15.071s)
---------------------------------------------------------------------------
translate_text (21)            | ██                                       | 1.006s
lemmatization (21)             |   ███████                                | 2.710s
the_cut (21)                   |          █                               | 0.012s
background_text_translation (21) |                        ████████████████  | 6.030s

Run Session: 202608072319** [Unknown] (Total Batch E2E Duration: 3.398s)
---------------------------------------------------------------------------
translate_text (17)            | █████████                                | 0.778s
lemmatization (17)             |          ██████████████████████████████  | 2.598s
the_cut (17)                   |                                        █ | 0.007s

Run Session: 202608071901** [Unknown] (Total Batch E2E Duration: 5.031s)
---------------------------------------------------------------------------
translate_text (00)            | ████████                                 | 1.112s
lemmatization (00)             |                  ██████████████████████  | 2.854s
the_cut (00)                   |                                        █ | 0.015s

```

## [Commit: 8122154] 20260807233307
```text
Run Session: 202608071901** [Unknown] (Total Batch E2E Duration: 4.784s)
---------------------------------------------------------------------------
translate_text (00)            | ████████                                 | 1.014s
lemmatization (00)             |                  ██████████████████████  | 2.631s
the_cut (00)                   |                                        █ | 0.011s

```

## [Commit: 209b691] 20260808000327
```text
Run Session: 202608071902** [Unknown] (Total Batch E2E Duration: 35.244s)
---------------------------------------------------------------------------
translate_text (00)            | █                                        | 1.022s
lemmatization (00)             |  █████                                   | 5.280s
the_cut (00)                   |        █                                 | 0.029s
background_text_translation (00) |                          ██████████████  | 12.557s
background_text_translation (01) |                                      ██  | 2.291s

Run Session: 202608071901** [Unknown] (Total Batch E2E Duration: 5.452s)
---------------------------------------------------------------------------
translate_text (00)            | ████████                                 | 1.101s
lemmatization (00)             |                ████████████████████████  | 3.289s
the_cut (00)                   |                                        █ | 0.012s

```

## [Commit: b3b7063] 20260808013600
```text
Run Session: 202608080141** [Unknown] (Total Batch E2E Duration: 23.612s)
---------------------------------------------------------------------------
translate_text (01)            | █                                        | 1.083s
lemmatization (01)             | ███████                                  | 4.430s
the_cut (01)                   |        █                                 | 0.031s
html_generation (09)           |            █                             | 0.003s
background_text_translation (09) |            █████████                     | 5.630s
html_generation (08)           |             █                            | 0.002s
html_generation (07)           |              █                           | 0.002s
html_generation (06)           |                █                         | 0.002s
html_generation (01)           |                   █                      | 0.006s
background_text_translation (01) |                   █████████████████████  | 12.623s
html_generation (05)           |                   █                      | 0.003s
html_generation (04)           |                     █                    | 0.003s
html_generation (03)           |                         █                | 0.003s
html_generation (02)           |                           █              | 0.003s
background_text_translation (02) |                            ██████        | 3.602s

Run Session: 202608080140** [Unknown] (Total Batch E2E Duration: 45.046s)
---------------------------------------------------------------------------
translate_text (09)            | █                                        | 1.101s
lemmatization (09)             | ██                                       | 2.986s
the_cut (09)                   |   █                                      | 0.032s
html_generation (17)           |         █                                | 0.003s
background_text_translation (17) |         █████                            | 5.962s
html_generation (09)           |            █                             | 0.008s
background_text_translation (09) |             ███████                      | 8.852s
translate_text (46)            |                                 █        | 1.054s
lemmatization (46)             |                                 ██       | 2.676s
the_cut (46)                   |                                     █    | 0.014s
html_generation (46)           |                                      █   | 0.003s
html_generation (49)           |                                       █  | 0.003s
html_generation (48)           |                                        █ | 0.003s
html_generation (47)           |                                        █ | 0.002s

Run Session: 202608080139** [Unknown] (Total Batch E2E Duration: 10.169s)
---------------------------------------------------------------------------
lemmatization (50)             | ███████████████                          | 4.007s
translate_text (50)            | ███                                      | 1.011s
the_cut (50)                   |                    █                     | 0.014s
html_generation (50)           |                           █              | 0.003s
html_generation (53)           |                                  █       | 0.003s
html_generation (52)           |                                     █    | 0.003s
html_generation (51)           |                                        █ | 0.003s

```

## [Commit: e3eaf56] 20260808015255
```text
Run Session: 202608071901** [Unknown] (Total Batch E2E Duration: 9.099s)
---------------------------------------------------------------------------
lemmatization (00)             | ██████████████                           | 3.315s
translate_text (00)            | ████                                     | 1.012s
the_cut (00)                   |                    █                     | 0.012s
html_generation (03)           |                           █              | 0.003s
html_generation (02)           |                                █         | 0.002s
html_generation (01)           |                                   █      | 0.002s
html_generation (00)           |                                        █ | 0.003s

```

## [Commit: 56aa451] 20260808015914
```text
Run Session: 202608071901** [Unknown] (Total Batch E2E Duration: 49.248s)
---------------------------------------------------------------------------
translate_text (00)            | █                                        | 1.401s
lemmatization (00)             | ██                                       | 2.943s
the_cut (00)                   |    █                                     | 0.014s
html_generation (00)           |    █                                     | 0.004s
background_text_translation (00) |    █████                                 | 6.267s
html_generation (03)           |    █                                     | 0.004s
background_text_translation (03) |     ███                                  | 4.492s
html_generation (02)           |     █                                    | 0.003s
background_text_translation (02) |     █                                    | 2.067s
html_generation (01)           |     █                                    | 0.003s
background_text_translation (01) |      █                                   | 1.284s
translate_text (00)            |                               █          | 1.087s
lemmatization (00)             |                               ██         | 3.167s
the_cut (00)                   |                                   █      | 0.011s
html_generation (00)           |                                   █      | 0.005s
background_text_translation (00) |                                   █████  | 6.594s
html_generation (03)           |                                   █      | 0.005s
background_text_translation (03) |                                    ████  | 5.098s
html_generation (02)           |                                    █     | 0.005s
background_text_translation (02) |                                     █    | 1.960s
html_generation (01)           |                                     █    | 0.005s
background_text_translation (01) |                                      █   | 1.378s

```

## Golden Run Aggregates
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `background_text_translation` | 20 | 1.284 | 5.327 | 12.623 |
| `lemmatization` | 18 | 0.010 | 3.209 | 6.906 |
| `translate_text` | 19 | 0.728 | 1.057 | 1.401 |
| `the_cut` | 17 | 0.007 | 0.017 | 0.033 |
| `html_generation` | 31 | 0.002 | 0.003 | 0.008 |

## Phase Glossary
- **`translate_text`**: (Network IO-Bound) Holistically translating the source paragraph/sentence via external APIs (e.g. DeepL).
- **`lemmatization`**: (CPU-Bound) Tokenizing text and executing morphological/Anki lookups via Kardenwort Core to generate the data grid.
- **`the_cut`**: (CPU-Bound) Slicing the master TSV into individual child sentence files during Multi-mode runs.
- **`background_text_translation`**: The progressive worker updating BOTH the text translation AND the individual base lemma translations asynchronously without blocking the UI.
