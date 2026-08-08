# Performance Dynamics Over Time (By Git Commit)

## Table of Contents
- [[Commit: a463eb0] 20260808021042](#commit-a463eb0-20260808021042)
- [[Commit: e88d77e] 20260808024001](#commit-e88d77e-20260808024001)
- [[Commit: de01b5d] 20260808025419](#commit-de01b5d-20260808025419)
- [[Commit: 2b39df3] 20260808031021](#commit-2b39df3-20260808031021)
- [Golden Run Aggregates](#golden-run-aggregates)
- [Phase Glossary](#phase-glossary)

## [Commit: a463eb0] 20260808021042
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 16.739s)
---------------------------------------------------------------------------
translate_text (00)                 | ██                                       | 0.930s
lemmatization (00)                  | ██████                                   | 2.798s
the_cut (00)                        |       █                                  | 0.028s
html_generation (00)                |       █                                  | 0.007s
background_text_translation (00)    |        ████████████████████████████████  | 13.441s
html_generation (08)                |         █                                | 0.004s
background_text_translation (08)    |         ██████████                       | 4.225s
html_generation (07)                |          █                               | 0.003s
background_text_translation (07)    |           ███                            | 1.546s
html_generation (06)                |            █                             | 0.002s
background_text_translation (06)    |            █                             | 0.684s
html_generation (05)                |             █                            | 0.002s
background_text_translation (05)    |              ██                          | 1.185s
html_generation (04)                |               █                          | 0.003s
background_text_translation (04)    |                █████                     | 2.446s
html_generation (03)                |                █                         | 0.003s
background_text_translation (03)    |                 ████████                 | 3.414s
html_generation (02)                |                  █                       | 0.003s
background_text_translation (02)    |                   ███████                | 2.948s
html_generation (01)                |                    █                     | 0.003s
background_text_translation (01)    |                    ██████                | 2.824s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 9.160s)
---------------------------------------------------------------------------
lemmatization (00)                  | █████████████                            | 3.070s
translate_text (00)                 | █████                                    | 1.169s
the_cut (00)                        |                  █                       | 0.014s
html_generation (00)                |                  █                       | 0.005s
background_text_translation (00)    |                    ████████████████████  | 4.668s
html_generation (03)                |                     █                    | 0.004s
background_text_translation (03)    |                       ███████████████    | 3.525s
html_generation (02)                |                        █                 | 0.003s
background_text_translation (02)    |                          █████           | 1.185s
html_generation (01)                |                           █              | 0.002s
background_text_translation (01)    |                            █████         | 1.209s

```

## [Commit: e88d77e] 20260808024001
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 16.889s)
---------------------------------------------------------------------------
translate_text (00)                 | ██                                       | 0.953s
lemmatization (00)                  | ██████████                               | 4.275s
the_cut (00)                        |           █                              | 0.029s
html_generation (00)                |           █                              | 0.006s
background_text_translation (00)    |            ████████████████████████████  | 12.061s
html_generation (08)                |            █                             | 0.004s
background_text_translation (08)    |             █████████                    | 4.104s
html_generation (07)                |              █                           | 0.003s
background_text_translation (07)    |              ██                          | 1.081s
html_generation (06)                |               █                          | 0.003s
background_text_translation (06)    |                ███                       | 1.539s
html_generation (05)                |                 █                        | 0.003s
background_text_translation (05)    |                 ██                       | 1.236s
html_generation (04)                |                  █                       | 0.003s
background_text_translation (04)    |                   ████                   | 1.969s
html_generation (03)                |                    █                     | 0.004s
background_text_translation (03)    |                     ██████               | 2.739s
html_generation (02)                |                      █                   | 0.003s
background_text_translation (02)    |                       ██████             | 2.874s
html_generation (01)                |                        █                 | 0.003s
background_text_translation (01)    |                         █████            | 2.345s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 8.818s)
---------------------------------------------------------------------------
translate_text (00)                 | ████                                     | 0.975s
lemmatization (00)                  | ████████████                             | 2.771s
the_cut (00)                        |                  █                       | 0.010s
html_generation (00)                |                  █                       | 0.004s
background_text_translation (00)    |                    ████████████████████  | 4.577s
html_generation (03)                |                    █                     | 0.004s
background_text_translation (03)    |                      ███████████████     | 3.444s
html_generation (02)                |                       █                  | 0.003s
background_text_translation (02)    |                         ████             | 1.027s
html_generation (01)                |                          █               | 0.002s
background_text_translation (01)    |                            ████          | 1.033s

```

## [Commit: de01b5d] 20260808025419
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 9.093s)
---------------------------------------------------------------------------
translate_text (00)                 | ████                                     | 1.093s
lemmatization (00)                  | ███████████████████                      | 4.442s
the_cut (00)                        |                    █                     | 0.027s
html_generation (00)                |                     █                    | 0.006s
html_generation (08)                |                       █                  | 0.003s
html_generation (07)                |                         █                | 0.002s
html_generation (06)                |                            █             | 0.002s
html_generation (05)                |                              █           | 0.002s
html_generation (04)                |                                 █        | 0.002s
html_generation (03)                |                                   █      | 0.003s
html_generation (02)                |                                      █   | 0.003s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 5.478s)
---------------------------------------------------------------------------
translate_text (00)                 | ███████                                  | 1.002s
lemmatization (00)                  | ███████████████████                      | 2.715s
the_cut (00)                        |                            █             | 0.012s
html_generation (00)                |                            █             | 0.005s
html_generation (03)                |                                █         | 0.003s
html_generation (02)                |                                    █     | 0.002s
html_generation (01)                |                                        █ | 0.002s

```

## [Commit: 2b39df3] 20260808031021
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 10.036s)
---------------------------------------------------------------------------
translate_text (00)                 | ███                                      | 0.943s
lemmatization (00)                  | ██████████████████                       | 4.624s
the_cut (00)                        |                   █                      | 0.027s
html_generation (00)                |                    █                     | 0.006s
html_generation (08)                |                     █                    | 0.003s
html_generation (07)                |                        █                 | 0.002s
html_generation (06)                |                           █              | 0.002s
html_generation (05)                |                             █            | 0.002s
html_generation (04)                |                                █         | 0.002s
html_generation (03)                |                                   █      | 0.003s
html_generation (02)                |                                      █   | 0.003s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 5.703s)
---------------------------------------------------------------------------
translate_text (00)                 | ███████                                  | 1.037s
lemmatization (00)                  | ████████████████████                     | 2.930s
the_cut (00)                        |                            █             | 0.013s
html_generation (00)                |                            █             | 0.004s
html_generation (03)                |                                █         | 0.003s
html_generation (02)                |                                    █     | 0.002s
html_generation (01)                |                                        █ | 0.002s

```

## Golden Run Aggregates
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 13 | 2.715 | 3.726 | 5.848 |
| `background_text_translation` | 39 | 0.573 | 3.289 | 13.441 |
| `translate_text` | 13 | 0.855 | 1.008 | 1.169 |
| `the_cut` | 13 | 0.010 | 0.020 | 0.032 |
| `html_generation` | 79 | 0.002 | 0.003 | 0.013 |

## Phase Glossary
- **`translate_text`**: (Network IO-Bound) Holistically translating the source paragraph/sentence via external APIs (e.g. DeepL).
- **`lemmatization`**: (CPU-Bound) Tokenizing text and executing morphological/Anki lookups via Kardenwort Core to generate the data grid.
- **`the_cut`**: (CPU-Bound) Slicing the master TSV into individual child sentence files during Multi-mode runs.
- **`background_text_translation`**: The progressive worker updating BOTH the text translation AND the individual base lemma translations asynchronously without blocking the UI.
