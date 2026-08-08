# Performance Dynamics Over Time (By Git Commit)

## Table of Contents
- [[Commit: a463eb0] 20260808021042](#commit-a463eb0-20260808021042)
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

## Golden Run Aggregates
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `background_text_translation` | 13 | 0.684 | 3.331 | 13.441 |
| `lemmatization` | 2 | 2.798 | 2.934 | 3.070 |
| `translate_text` | 2 | 0.930 | 1.050 | 1.169 |
| `the_cut` | 2 | 0.014 | 0.021 | 0.028 |
| `html_generation` | 13 | 0.002 | 0.003 | 0.007 |

## Phase Glossary
- **`translate_text`**: (Network IO-Bound) Holistically translating the source paragraph/sentence via external APIs (e.g. DeepL).
- **`lemmatization`**: (CPU-Bound) Tokenizing text and executing morphological/Anki lookups via Kardenwort Core to generate the data grid.
- **`the_cut`**: (CPU-Bound) Slicing the master TSV into individual child sentence files during Multi-mode runs.
- **`background_text_translation`**: The progressive worker updating BOTH the text translation AND the individual base lemma translations asynchronously without blocking the UI.
