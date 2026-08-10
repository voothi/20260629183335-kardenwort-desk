# Performance Dynamics Over Time (By Git Commit)

## Table of Contents
- [[Commit: 39a6725] 20260809094248](#commit-39a6725-20260809094248)
- [[Commit: cd71fc4] 20260809172340](#commit-cd71fc4-20260809172340)
- [[Commit: 33dce05] 20260809193341](#commit-33dce05-20260809193341)
- [[Commit: c0e9ee8] 20260810022639](#commit-c0e9ee8-20260810022639)
- [[Commit: 9102d2b] 20260810023021](#commit-9102d2b-20260810023021)
- [[Commit: 4b309b0] 20260810023417](#commit-4b309b0-20260810023417)
- [[Commit: 0b5b68e] 20260810025229](#commit-0b5b68e-20260810025229)
- [[Commit: fb1c04f] 20260810031306](#commit-fb1c04f-20260810031306)
- [[Commit: ef35ad7] 20260810032615](#commit-ef35ad7-20260810032615)
- [[Commit: c1bb194] 20260810035156](#commit-c1bb194-20260810035156)
- [[Commit: 3165396] 20260810042138](#commit-3165396-20260810042138)
- [Golden Run Aggregates](#golden-run-aggregates)
- [Phase Glossary](#phase-glossary)

## [Commit: 39a6725] 20260809094248
```text
Run Session: 20260807230100 [Golden EN] (Total Batch E2E Duration: 10.885s)
---------------------------------------------------------------------------
html_generation (03)                | █                                        | 0.003s
background_text_translation (03)    |  ██████████████████████████████████████  | 10.546s
intellifiller_enrichment (03)       |  ██████████████████████████████████████  | 10.530s
html_generation (02)                |   █                                      | 0.003s
background_text_translation (02)    |    ███                                   | 1.053s
intellifiller_enrichment (02)       |    ███                                   | 1.044s
html_generation (01)                |     █                                    | 0.003s
background_text_translation (01)    |      ██████                              | 1.748s
intellifiller_enrichment (01)       |      ██████                              | 1.733s

```

## [Commit: cd71fc4] 20260809172340 (Avg wait between runs: 33.22s)
```text
Run Session: 20260807230200 [Golden DE] (Total Batch E2E Duration: 20.243s)
---------------------------------------------------------------------------
translate_text (00)                 | █                                        | 0.843s
lemmatization (00)                  | ████████████████                         | 8.200s
the_cut (00)                        |                 █                        | 0.043s
html_generation (08)                |                    █                     | 0.007s
intellifiller_enrichment (00)       |                       ██                 | 1.151s
html_generation (07)                |                         █                | 0.004s
html_generation (00)                |                         █                | 0.009s
html_generation (06)                |                          █               | 0.003s
html_generation (05)                |                             █            | 0.003s
html_generation (04)                |                               █          | 0.004s
html_generation (03)                |                                  █       | 0.005s
html_generation (02)                |                                    █     | 0.004s
background_text_translation (02)    |                                     █    | 0.720s
intellifiller_enrichment (02)       |                                     █    | 0.694s
html_generation (01)                |                                      █   | 0.005s
background_text_translation (01)    |                                       █  | 0.684s
intellifiller_enrichment (01)       |                                       █  | 0.667s

Run Session: 20260807220100 [Golden EN] (Total Batch E2E Duration: 0.010s)
---------------------------------------------------------------------------
html_generation (00)                | ███████████████████████████████████████  | 0.010s

Run Session: 20260807210200 [Golden DE] (Total Batch E2E Duration: 12.042s)
---------------------------------------------------------------------------
translate_text (00)                 | ███                                      | 1.042s
lemmatization (00)                  | ███████████████                          | 4.791s
the_cut (00)                        |                 █                        | 0.036s
html_generation (08)                |                    █                     | 0.003s
background_text_translation (08)    |                     ██                   | 0.632s
html_generation (07)                |                       █                  | 0.003s
background_text_translation (07)    |                        █                 | 0.565s
html_generation (06)                |                         █                | 0.002s
html_generation (05)                |                            █             | 0.002s
html_generation (00)                |                              █           | 0.008s
html_generation (04)                |                               █          | 0.002s
html_generation (03)                |                                  █       | 0.003s
html_generation (02)                |                                     █    | 0.004s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807210100 [Golden EN] (Total Batch E2E Duration: 6.580s)
---------------------------------------------------------------------------
translate_text (00)                 | ██████                                   | 1.099s
lemmatization (00)                  | █████████████████                        | 2.891s
the_cut (00)                        |                         █                | 0.014s
html_generation (03)                |                              █           | 0.003s
html_generation (00)                |                               █          | 0.004s
background_text_translation (03)    |                                ███████   | 1.265s
html_generation (02)                |                                   █      | 0.003s
background_text_translation (02)    |                                     ███  | 0.567s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807200200 [Golden DE] (Total Batch E2E Duration: 16.301s)
---------------------------------------------------------------------------
translate_text (00)                 | █                                        | 0.618s
lemmatization (00)                  | ███████████                              | 4.831s
the_cut (00)                        |            █                             | 0.034s
html_generation (08)                |              █                           | 0.003s
background_text_translation (08)    |               ███████████                | 4.731s
html_generation (07)                |                █                         | 0.004s
background_text_translation (07)    |                 ███                      | 1.323s
html_generation (06)                |                  █                       | 0.003s
html_generation (00)                |                   █                      | 0.008s
background_text_translation (06)    |                   █                      | 0.606s
html_generation (05)                |                     █                    | 0.003s
background_text_translation (05)    |                     ███                  | 1.366s
html_generation (04)                |                       █                  | 0.004s
background_text_translation (04)    |                        █████             | 2.289s
html_generation (03)                |                         █                | 0.004s
background_text_translation (03)    |                          █████████       | 3.738s
html_generation (02)                |                            █             | 0.004s
background_text_translation (02)    |                             ███████████  | 4.729s
html_generation (01)                |                              █           | 0.004s
background_text_translation (01)    |                               ██████     | 2.662s

Run Session: 20260807200100 [Golden EN] (Total Batch E2E Duration: 8.364s)
---------------------------------------------------------------------------
translate_text (00)                 | ██                                       | 0.558s
lemmatization (00)                  | ████████████                             | 2.667s
the_cut (00)                        |                █                         | 0.014s
html_generation (03)                |                  █                       | 0.003s
background_text_translation (03)    |                    ████████████████████  | 4.292s
html_generation (02)                |                      █                   | 0.003s
html_generation (00)                |                       █                  | 0.005s
background_text_translation (02)    |                        █████             | 1.179s
html_generation (01)                |                           █              | 0.003s
background_text_translation (01)    |                             ██████       | 1.276s

Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 12.504s)
---------------------------------------------------------------------------
translate_text (00)                 | █                                        | 0.549s
lemmatization (00)                  | ██████████████                           | 4.437s
the_cut (00)                        |               █                          | 0.027s
html_generation (00)                |                  █                       | 0.006s
html_generation (08)                |                     █                    | 0.004s
html_generation (07)                |                        █                 | 0.003s
html_generation (06)                |                          █               | 0.002s
html_generation (05)                |                            █             | 0.002s
html_generation (04)                |                               █          | 0.002s
html_generation (03)                |                                  █       | 0.003s
html_generation (02)                |                                     █    | 0.003s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 8.075s)
---------------------------------------------------------------------------
lemmatization (00)                  | ████████████████████                     | 4.111s
translate_text (00)                 | ███                                      | 0.696s
the_cut (00)                        |                        █                 | 0.022s
html_generation (00)                |                          █               | 0.007s
html_generation (03)                |                               █          | 0.007s
html_generation (02)                |                                    █     | 0.003s
html_generation (01)                |                                        █ | 0.004s

```

## [Commit: 33dce05] 20260809193341 (Avg wait between runs: 12427.64s)
```text
Run Session: 20260807230200 [Golden DE] (Total Batch E2E Duration: 18.790s)
---------------------------------------------------------------------------
translate_text (00)                 | █                                        | 0.633s
lemmatization (00)                  | ███████████                              | 5.576s
the_cut (00)                        |             █                            | 0.049s
html_generation (08)                |               █                          | 0.007s
background_text_translation (08)    |                █                         | 0.005s
html_generation (00)                |                  █                       | 0.006s
html_generation (07)                |                  █                       | 0.004s
background_text_translation (07)    |                   █                      | 0.005s
html_generation (06)                |                    █                     | 0.003s
background_text_translation (06)    |                     █                    | 0.006s
html_generation (05)                |                       █                  | 0.004s
background_text_translation (05)    |                        █                 | 0.004s
html_generation (04)                |                          █               | 0.003s
background_text_translation (04)    |                            █             | 0.004s
html_generation (03)                |                              █           | 0.004s
background_text_translation (03)    |                                █         | 0.003s
html_generation (02)                |                                 █        | 0.004s
background_text_translation (02)    |                                   ████   | 2.017s
intellifiller_enrichment (02)       |                                   ████   | 1.951s
html_generation (01)                |                                     █    | 0.004s
intellifiller_enrichment (01)       |                                     ██   | 1.407s
background_text_translation (01)    |                                     ███  | 1.435s

Run Session: 20260807230100 [Golden EN] (Total Batch E2E Duration: 10.796s)
---------------------------------------------------------------------------
lemmatization (00)                  | ████████████                             | 3.366s
translate_text (00)                 | ███                                      | 1.075s
the_cut (00)                        |                 █                        | 0.017s
html_generation (00)                |                                        █ | 0.004s

```

## [Commit: c0e9ee8] 20260810022639
```text
Run Session: 20260807230100 [Golden EN] (Total Batch E2E Duration: 7.545s)
---------------------------------------------------------------------------
translate_text (00)                 | ██                                       | 0.544s
lemmatization (00)                  | ███████████████                          | 2.857s
the_cut (00)                        |                   █                      | 0.010s
html_generation (00)                |                    █                     | 0.004s
background_text_translation (00)    |                      ████████████        | 2.428s
intellifiller_enrichment (00)       |                      ████████████        | 2.412s
html_generation (03)                |                           █              | 0.003s
background_text_translation (03)    |                             ███████████  | 2.121s
intellifiller_enrichment (03)       |                             ███████████  | 2.106s
html_generation (02)                |                               █          | 0.003s
background_text_translation (02)    |                                 █        | 0.003s
html_generation (01)                |                                   █      | 0.002s
background_text_translation (01)    |                                     █    | 0.003s
cross_pollinate_from_siblings (00)  |                                        █ | 0.004s

```

## [Commit: 9102d2b] 20260810023021
```text
Run Session: 20260807230200 [Golden DE] (Total Batch E2E Duration: 11.798s)
---------------------------------------------------------------------------
translate_text (00)                 | ██                                       | 0.604s
lemmatization (00)                  | ███████████████                          | 4.440s
the_cut (00)                        |                █                         | 0.027s
html_generation (00)                |                   █                      | 0.006s
background_text_translation (00)    |                    ████████              | 2.547s
intellifiller_enrichment (00)       |                    ████████              | 2.527s
html_generation (08)                |                      █                   | 0.003s
background_text_translation (08)    |                       █                  | 0.004s
html_generation (07)                |                        █                 | 0.003s
background_text_translation (07)    |                         █                | 0.003s
html_generation (06)                |                          █               | 0.003s
background_text_translation (06)    |                            █             | 0.004s
html_generation (05)                |                             █            | 0.002s
background_text_translation (05)    |                              █           | 0.003s
html_generation (04)                |                               █          | 0.003s
background_text_translation (04)    |                                 █        | 0.003s
html_generation (03)                |                                  █       | 0.003s
background_text_translation (03)    |                                   █      | 0.004s
html_generation (02)                |                                     █    | 0.003s
background_text_translation (02)    |                                      █   | 0.004s
html_generation (01)                |                                       █  | 0.003s
background_text_translation (01)    |                                        █ | 0.003s
cross_pollinate_from_siblings (00)  |                                        █ | 0.012s

```

## [Commit: 4b309b0] 20260810023417 (Avg wait between runs: -232.22s)
```text
Run Session: 20260807220200 [Golden DE [FAILED - EXCLUDED FROM STATS]] (Total Batch E2E Duration: 60.542s)
---------------------------------------------------------------------------
translate_text (00)                 | ██████                                   | 10.277s
lemmatization (00)                  | ███                                      | 5.642s
the_cut (00)                        |       █                                  | 0.054s
html_generation (08)                |        █                                 | 0.004s
background_text_translation (08)    |        ██████████████████████            | 34.237s
html_generation (07)                |         █                                | 0.003s
background_text_translation (07)    |         █████                            | 7.894s
html_generation (06)                |         █                                | 0.003s
html_generation (05)                |          █                               | 0.003s
html_generation (00)                |          █                               | 0.009s
background_text_translation (00)    |          ██████████████████████████████  | 46.235s
html_generation (04)                |          █                               | 0.004s
background_text_translation (04)    |           ██████                         | 9.817s
html_generation (03)                |           █                              | 0.004s
background_text_translation (03)    |           ██████                         | 10.443s
html_generation (02)                |            █                             | 0.004s
background_text_translation (02)    |            ██████                        | 9.987s
html_generation (01)                |             █                            | 0.004s
background_text_translation (01)    |             █████████████████████████    | 38.422s
validation_failed (00)              |                                  █       | 0.000s

Run Session: 20260807220100 [Golden EN [FAILED - EXCLUDED FROM STATS]] (Total Batch E2E Duration: 39.875s)
---------------------------------------------------------------------------
translate_text (00)                 | ██████                                   | 6.161s
lemmatization (00)                  | ███                                      | 3.698s
the_cut (00)                        |       █                                  | 0.015s
html_generation (00)                |       █                                  | 0.004s
background_text_translation (00)    |       ███████████████████                | 19.607s
html_generation (03)                |       █                                  | 0.004s
background_text_translation (03)    |        ███████████████████               | 19.414s
html_generation (02)                |        █                                 | 0.004s
background_text_translation (02)    |         █████                            | 5.973s
html_generation (01)                |         █                                | 0.003s
validation_failed (00)              |                                         █ | 0.000s

```

## [Commit: 0b5b68e] 20260810025229 (Avg wait between runs: 989.99s)
```text
Run Session: 20260807230100 [Golden EN] (Total Batch E2E Duration: 7.867s)
---------------------------------------------------------------------------
lemmatization (00)                  | ███████████████                          | 3.095s
translate_text (00)                 | ██                                       | 0.571s
the_cut (00)                        |                   █                      | 0.011s
html_generation (00)                |                    █                     | 0.004s
background_text_translation (00)    |                      ██████████████████  | 3.651s
intellifiller_enrichment (00)       |                      ██████████████████  | 3.635s
html_generation (03)                |                       █                  | 0.004s
background_text_translation (03)    |                        ██████            | 1.359s
intellifiller_enrichment (03)       |                        ██████            | 1.340s
html_generation (02)                |                           █              | 0.003s
background_text_translation (02)    |                            █             | 0.003s
html_generation (01)                |                              █           | 0.002s
background_text_translation (01)    |                                █████     | 1.166s
intellifiller_enrichment (01)       |                                █████     | 1.160s

Run Session: 20260807220100 [Golden EN [FAILED - EXCLUDED FROM STATS]] (Total Batch E2E Duration: 39.355s)
---------------------------------------------------------------------------
translate_text (00)                 | █████                                    | 5.048s
lemmatization (00)                  | ██                                       | 2.774s
the_cut (00)                        |      █                                   | 0.013s
html_generation (00)                |      █                                   | 0.004s
background_text_translation (00)    |      ███████████████                     | 14.805s
html_generation (03)                |       █                                  | 0.003s
background_text_translation (03)    |        ███████████████                   | 14.902s
html_generation (02)                |        █                                 | 0.003s
background_text_translation (02)    |        █████                             | 5.606s
html_generation (01)                |         █                                | 0.022s
validation_failed (00)              |                                         █ | 0.000s

```

## [Commit: fb1c04f] 20260810031306
```text
Run Session: 20260807220100 [Golden EN] (Total Batch E2E Duration: 25.051s)
---------------------------------------------------------------------------
translate_text (00)                 | █████████                                | 5.831s
lemmatization (00)                  | █████                                    | 3.282s
the_cut (00)                        |          █                               | 0.012s
html_generation (00)                |          █                               | 0.004s
background_text_translation (00)    |           ███████████████████████████    | 17.133s
html_generation (03)                |           █                              | 0.004s
background_text_translation (03)    |            ███████████████████████████   | 17.064s
html_generation (02)                |            █                             | 0.003s
background_text_translation (02)    |             █████████                    | 5.836s
html_generation (01)                |              █                           | 0.003s
cross_pollinate_from_siblings (00)  |                                       █  | 1.021s

```

## [Commit: ef35ad7] 20260810032615 (Avg wait between runs: 1407.68s)
```text
Run Session: 20260807230100 [Golden EN] (Total Batch E2E Duration: 6.895s)
---------------------------------------------------------------------------
translate_text (00)                 | ███                                      | 0.520s
lemmatization (00)                  | ██████████████                           | 2.584s
the_cut (00)                        |                  █                       | 0.010s
html_generation (00)                |                    █                     | 0.005s
html_generation (03)                |                       █                  | 0.003s
background_text_translation (03)    |                         ███████          | 1.250s
intellifiller_enrichment (03)       |                         ███████          | 1.229s
html_generation (02)                |                           █              | 0.003s
background_text_translation (00)    |                            █████         | 0.989s
intellifiller_enrichment (00)       |                            █████         | 0.969s
background_text_translation (02)    |                             █            | 0.004s
html_generation (01)                |                               █          | 0.003s
background_text_translation (01)    |                                  ██████  | 1.177s
intellifiller_enrichment (01)       |                                  ██████  | 1.170s
cross_pollinate_from_siblings (00)  |                                        █ | 0.008s

Run Session: 20260807220100 [Golden EN] (Total Batch E2E Duration: 21.398s)
---------------------------------------------------------------------------
translate_text (00)                 | ██████████                               | 5.380s
lemmatization (00)                  | █████                                    | 3.083s
the_cut (00)                        |           █                              | 0.012s
html_generation (00)                |           █                              | 0.004s
background_text_translation (00)    |            ████████████████████████████  | 15.227s
html_generation (03)                |            █                             | 0.003s
background_text_translation (03)    |            ███████████████████████████   | 14.952s
html_generation (02)                |             █                            | 0.003s
background_text_translation (02)    |              ██████████                  | 5.483s
html_generation (01)                |               █                          | 0.003s
cross_pollinate_from_siblings (00)  |                                        █ | 0.030s

```

## [Commit: c1bb194] 20260810035156
```text
Run Session: 20260807220100 [Golden EN] (Total Batch E2E Duration: 25.978s)
---------------------------------------------------------------------------
translate_text (00)                 | █████████████                            | 8.498s
lemmatization (00)                  | ██████                                   | 4.258s
the_cut (00)                        |              █                           | 0.014s
html_generation (00)                |              █                           | 0.004s
background_text_translation (00)    |               █████████████████████████  | 16.593s
html_generation (03)                |               █                          | 0.003s
background_text_translation (03)    |               █████████████████████████  | 16.279s
html_generation (02)                |                █                         | 0.003s
background_text_translation (02)    |                 █████████                | 6.082s
html_generation (01)                |                  █                       | 0.004s
cross_pollinate_from_siblings (00)  |                                        █ | 0.017s

```

## [Commit: 3165396] 20260810042138
```text
Run Session: 20260807230100 [Golden EN [FAILED - EXCLUDED FROM STATS]] (Total Batch E2E Duration: 1.000s)
---------------------------------------------------------------------------
validation_failed (00)              | █                                        | 0.000s

```

## Golden Run Aggregates

### 20260807190100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 1 | 4.111 | 4.111 | 4.111 |
| `translate_text` | 1 | 0.696 | 0.696 | 0.696 |
| `the_cut` | 1 | 0.022 | 0.022 | 0.022 |
| `html_generation` | 4 | 0.003 | 0.005 | 0.007 |

### 20260807190200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 1 | 4.437 | 4.437 | 4.437 |
| `translate_text` | 1 | 0.549 | 0.549 | 0.549 |
| `the_cut` | 1 | 0.027 | 0.027 | 0.027 |
| `html_generation` | 9 | 0.002 | 0.003 | 0.006 |

### 20260807200100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 1 | 2.667 | 2.667 | 2.667 |
| `background_text_translation` | 3 | 1.179 | 2.249 | 4.292 |
| `translate_text` | 1 | 0.558 | 0.558 | 0.558 |
| `the_cut` | 1 | 0.014 | 0.014 | 0.014 |
| `html_generation` | 4 | 0.003 | 0.003 | 0.005 |

### 20260807200200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 1 | 4.831 | 4.831 | 4.831 |
| `background_text_translation` | 8 | 0.606 | 2.681 | 4.731 |
| `translate_text` | 1 | 0.618 | 0.618 | 0.618 |
| `the_cut` | 1 | 0.034 | 0.034 | 0.034 |
| `html_generation` | 9 | 0.003 | 0.004 | 0.008 |

### 20260807210100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 1 | 2.891 | 2.891 | 2.891 |
| `translate_text` | 1 | 1.099 | 1.099 | 1.099 |
| `background_text_translation` | 2 | 0.567 | 0.916 | 1.265 |
| `the_cut` | 1 | 0.014 | 0.014 | 0.014 |
| `html_generation` | 4 | 0.003 | 0.003 | 0.004 |

### 20260807210200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 1 | 4.791 | 4.791 | 4.791 |
| `translate_text` | 1 | 1.042 | 1.042 | 1.042 |
| `background_text_translation` | 2 | 0.565 | 0.598 | 0.632 |
| `the_cut` | 1 | 0.036 | 0.036 | 0.036 |
| `html_generation` | 9 | 0.002 | 0.003 | 0.008 |

### 20260807220100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `background_text_translation` | 9 | 5.483 | 12.739 | 17.133 |
| `translate_text` | 3 | 5.380 | 6.570 | 8.498 |
| `lemmatization` | 3 | 3.083 | 3.541 | 4.258 |
| `cross_pollinate_from_siblings` | 3 | 0.017 | 0.356 | 1.021 |
| `the_cut` | 3 | 0.012 | 0.013 | 0.014 |
| `html_generation` | 13 | 0.003 | 0.004 | 0.010 |

### 20260807230200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 3 | 4.440 | 6.072 | 8.200 |
| `intellifiller_enrichment` | 6 | 0.667 | 1.399 | 2.527 |
| `translate_text` | 3 | 0.604 | 0.693 | 0.843 |
| `background_text_translation` | 19 | 0.003 | 0.393 | 2.547 |
| `the_cut` | 3 | 0.027 | 0.040 | 0.049 |
| `cross_pollinate_from_siblings` | 1 | 0.012 | 0.012 | 0.012 |
| `html_generation` | 27 | 0.002 | 0.004 | 0.009 |

## Phase Glossary
- **`translate_text`**: (Network IO-Bound) Holistically translating the source paragraph/sentence via external APIs (e.g. DeepL).
- **`lemmatization`**: (CPU-Bound) Tokenizing text and executing morphological/Anki lookups via Kardenwort Core to generate the data grid.
- **`the_cut`**: (CPU-Bound) Slicing the master TSV into individual child sentence files during Multi-mode runs.
- **`background_text_translation`**: The progressive worker updating BOTH the text translation AND the individual base lemma translations asynchronously without blocking the UI.
