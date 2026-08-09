# Performance Dynamics Over Time (By Git Commit)

## Table of Contents
- [[Commit: 39a6725] 20260809094248](#commit-39a6725-20260809094248)
- [[Commit: 1ca23c2] 20260809115906](#commit-1ca23c2-20260809115906)
- [[Commit: a283c22] 20260809121311](#commit-a283c22-20260809121311)
- [[Commit: f5450d0] 20260809122400](#commit-f5450d0-20260809122400)
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

## [Commit: 1ca23c2] 20260809115906
```text
Run Session: 20260807230100 [Golden EN] (Total Batch E2E Duration: 13.739s)
---------------------------------------------------------------------------
translate_text (00)                 | ██                                       | 0.692s
lemmatization (00)                  | ███████                                  | 2.614s
the_cut (00)                        |          █                               | 0.014s
intellifiller_enrichment (00)       |          ██████████████████████████████  | 10.430s
html_generation (03)                |           █                              | 0.003s
background_text_translation (03)    |            █████████████████████████     | 8.629s
intellifiller_enrichment (03)       |            █████████████████████████     | 8.613s
html_generation (02)                |             █                            | 0.003s
background_text_translation (02)    |              ███                         | 1.133s
intellifiller_enrichment (02)       |              ███                         | 1.126s
html_generation (01)                |               █                          | 0.003s
background_text_translation (01)    |                █████                     | 1.938s
intellifiller_enrichment (01)       |                █████                     | 1.925s
html_generation (00)                |                                        █ | 0.003s

```

## [Commit: a283c22] 20260809121311
```text
Run Session: 20260807230100 [Golden EN] (Total Batch E2E Duration: 16.291s)
---------------------------------------------------------------------------
translate_text (00)                 | █                                        | 0.647s
lemmatization (00)                  | ███████                                  | 3.077s
the_cut (00)                        |          █                               | 0.016s
intellifiller_enrichment (00)       |          ██████████████████████████████  | 12.235s
html_generation (03)                |            █                             | 0.004s
background_text_translation (03)    |             █████████████████████████    | 10.497s
intellifiller_enrichment (03)       |             █████████████████████████    | 10.478s
html_generation (02)                |              █                           | 0.003s
background_text_translation (02)    |               ███                        | 1.287s
intellifiller_enrichment (02)       |               ███                        | 1.279s
html_generation (01)                |                █                         | 0.003s
background_text_translation (01)    |                 ██████                   | 2.722s
intellifiller_enrichment (01)       |                 ██████                   | 2.705s
html_generation (00)                |                                        █ | 0.004s

```

## [Commit: f5450d0] 20260809122400
```text
Run Session: 20260807230100 [Golden EN] (Total Batch E2E Duration: 14.019s)
---------------------------------------------------------------------------
translate_text (00)                 | █                                        | 0.624s
lemmatization (00)                  | █████████                                | 3.206s
the_cut (00)                        |           █                              | 0.012s
intellifiller_enrichment (00)       |            ████████████████████████████  | 9.951s
html_generation (03)                |              █                           | 0.004s
background_text_translation (03)    |               ███████████████████████    | 8.301s
intellifiller_enrichment (03)       |               ███████████████████████    | 8.280s
html_generation (02)                |                █                         | 0.004s
background_text_translation (02)    |                  ███                     | 1.222s
intellifiller_enrichment (02)       |                  ███                     | 1.217s
html_generation (01)                |                   █                      | 0.003s
background_text_translation (01)    |                    █████                 | 2.000s
intellifiller_enrichment (01)       |                    █████                 | 1.986s
html_generation (00)                |                                        █ | 0.004s

```

## Golden Run Aggregates

### 20260807230100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `intellifiller_enrichment` | 24 | 1.044 | 5.784 | 12.235 |
| `background_text_translation` | 18 | 1.053 | 4.169 | 10.546 |
| `lemmatization` | 6 | 2.497 | 2.846 | 3.206 |
| `translate_text` | 6 | 0.565 | 0.630 | 0.692 |
| `the_cut` | 6 | 0.010 | 0.013 | 0.016 |
| `html_generation` | 24 | 0.002 | 0.003 | 0.005 |

## Phase Glossary
- **`translate_text`**: (Network IO-Bound) Holistically translating the source paragraph/sentence via external APIs (e.g. DeepL).
- **`lemmatization`**: (CPU-Bound) Tokenizing text and executing morphological/Anki lookups via Kardenwort Core to generate the data grid.
- **`the_cut`**: (CPU-Bound) Slicing the master TSV into individual child sentence files during Multi-mode runs.
- **`background_text_translation`**: The progressive worker updating BOTH the text translation AND the individual base lemma translations asynchronously without blocking the UI.
