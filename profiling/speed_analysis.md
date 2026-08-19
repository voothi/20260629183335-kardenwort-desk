# Performance Dynamics Over Time (By Git Commit)

## Table of Contents
- [[Commit: 97cef48] 20260810163734](#commit-97cef48-20260810163734)
- [[Commit: 2544a7c] 20260819013716 to desk](#commit-2544a7c-20260819013716-to-desk)
- [[Commit: 02907f0] 20260819025120 to desk](#commit-02907f0-20260819025120-to-desk)
- [[Commit: 735d0c1] 20260819031636 to desk](#commit-735d0c1-20260819031636-to-desk)
- [[Commit: 2123428] 20260819034156](#commit-2123428-20260819034156)
- [[Commit: e38410e] 20260819103446 to desk](#commit-e38410e-20260819103446-to-desk)
- [[Commit: 7ead811] 20260819112924 to desk](#commit-7ead811-20260819112924-to-desk)
- [Golden Run Aggregates](#golden-run-aggregates)
- [Phase Glossary](#phase-glossary)

## [Commit: 97cef48] 20260810163734 (Avg wait between runs: 12.80s)
```text
Run Session: 20260807230200 [Golden DE [FAILED - EXCLUDED FROM STATS]] (Total Batch E2E Duration: 45.939s)
---------------------------------------------------------------------------
translate_text (00)                 | █                                        | 0.649s
lemmatization (00)                  | █████                                    | 6.240s
the_cut (00)                        |      █                                   | 0.029s
html_generation (08)                |       █                                  | 0.004s
background_text_translation (08)    |       █                                  | 0.004s
html_generation (07)                |        █                                 | 0.004s
background_text_translation (07)    |        █                                 | 0.003s
html_generation (06)                |        █                                 | 0.003s
background_text_translation (06)    |         █                                | 0.005s
html_generation (00)                |         █                                | 0.010s
html_generation (05)                |         █                                | 0.004s
background_text_translation (00)    |          ██                              | 3.083s
intellifiller_enrichment (00)       |          ██                              | 3.073s
background_text_translation (05)    |          █                               | 0.004s
html_generation (04)                |          █                               | 0.004s
background_text_translation (04)    |           █                              | 0.004s
html_generation (03)                |           █                              | 0.004s
background_text_translation (03)    |            █                             | 0.005s
html_generation (02)                |            █                             | 0.004s
background_text_translation (02)    |             █                            | 1.997s
intellifiller_enrichment (02)       |             █                            | 1.962s
html_generation (01)                |             █                            | 0.005s
background_text_translation (01)    |              █                           | 0.004s
cross_pollinate_from_siblings (00)  |              █                           | 0.012s
validation_failed (00)              |                                         █ | 0.000s

Run Session: 20260807230100 [Golden EN] (Total Batch E2E Duration: 31.385s)
---------------------------------------------------------------------------
translate_text (00)                 | ████                                     | 3.235s
lemmatization (00)                  | ██████████████                           | 11.552s
the_cut (00)                        |                █                         | 0.012s
html_generation (00)                |                 █                        | 0.006s
background_text_translation (00)    |                 ███████████████████████  | 18.256s
intellifiller_enrichment (00)       |                 ███████████████████████  | 18.240s
html_generation (03)                |                   █                      | 0.006s
background_text_translation (03)    |                   █                      | 1.310s
intellifiller_enrichment (03)       |                   █                      | 1.287s
html_generation (02)                |                    █                     | 0.004s
background_text_translation (02)    |                     █                    | 0.005s
html_generation (01)                |                     █                    | 0.004s
background_text_translation (01)    |                      ██                  | 2.120s
intellifiller_enrichment (01)       |                      ██                  | 2.102s
cross_pollinate_from_siblings (00)  |                                        █ | 0.003s

Run Session: 20260807220200 [Golden DE [FAILED - EXCLUDED FROM STATS]] (Total Batch E2E Duration: 90.286s)
---------------------------------------------------------------------------
translate_text (00)                 | █████                                    | 11.734s
lemmatization (00)                  | ███                                      | 8.285s
the_cut (00)                        |      █                                   | 0.035s
html_generation (08)                |      █                                   | 0.005s
background_text_translation (08)    |      ███████████████████                 | 44.288s
html_generation (07)                |       █                                  | 0.004s
background_text_translation (07)    |       █████                              | 12.285s
html_generation (06)                |       █                                  | 0.004s
html_generation (00)                |        █                                 | 0.011s
background_text_translation (00)    |        ████████████████████████████████  | 73.235s
html_generation (05)                |        █                                 | 0.005s
html_generation (04)                |         █                                | 0.005s
background_text_translation (04)    |         ███████                          | 16.137s
html_generation (03)                |          █                               | 0.006s
background_text_translation (03)    |          ███████                         | 16.096s
html_generation (02)                |           █                              | 0.007s
background_text_translation (02)    |           ██████                         | 15.413s
html_generation (01)                |            █                             | 0.007s
background_text_translation (01)    |            ████████████████████          | 46.861s
validation_failed (00)              |                          █               | 0.000s
cross_pollinate_from_siblings (00)  |                                        █ | 0.034s

Run Session: 20260807220100 [Golden EN] (Total Batch E2E Duration: 35.289s)
---------------------------------------------------------------------------
translate_text (00)                 | ██████████                               | 9.119s
lemmatization (00)                  | ████                                     | 4.247s
the_cut (00)                        |           █                              | 0.013s
html_generation (00)                |           █                              | 0.006s
background_text_translation (00)    |            ██████████████████████████    | 23.491s
html_generation (03)                |             █                            | 0.005s
background_text_translation (03)    |              ██████████████████████████  | 23.620s
html_generation (02)                |              █                           | 0.005s
background_text_translation (02)    |               ██████████                 | 8.902s
html_generation (01)                |                █                         | 0.004s
cross_pollinate_from_siblings (00)  |                                        █ | 0.018s

Run Session: 20260807210200 [Golden DE] (Total Batch E2E Duration: 16.677s)
---------------------------------------------------------------------------
translate_text (00)                 | ██                                       | 1.191s
lemmatization (00)                  | ███████████████                          | 6.435s
the_cut (00)                        |                █                         | 0.037s
html_generation (00)                |                    █                     | 0.008s
background_text_translation (00)    |                     ████                 | 1.937s
html_generation (08)                |                        █                 | 0.004s
background_text_translation (08)    |                         █                | 0.647s
html_generation (07)                |                          █               | 0.004s
html_generation (06)                |                             █            | 0.003s
html_generation (05)                |                               █          | 0.003s
html_generation (04)                |                                 █        | 0.004s
html_generation (03)                |                                   █      | 0.004s
html_generation (02)                |                                      █   | 0.004s
html_generation (01)                |                                        █ | 0.004s
cross_pollinate_from_siblings (00)  |                                        █ | 0.016s

Run Session: 20260807210100 [Golden EN] (Total Batch E2E Duration: 8.294s)
---------------------------------------------------------------------------
translate_text (00)                 | █████                                    | 1.149s
lemmatization (00)                  | ██████████████████                       | 3.857s
the_cut (00)                        |                         █                | 0.014s
html_generation (00)                |                          █               | 0.006s
html_generation (03)                |                            █             | 0.004s
background_text_translation (03)    |                               ██████     | 1.438s
html_generation (02)                |                                 █        | 0.005s
background_text_translation (00)    |                                 ███████  | 1.506s
background_text_translation (02)    |                                    ███   | 0.715s
html_generation (01)                |                                       █  | 0.004s
cross_pollinate_from_siblings (00)  |                                        █ | 0.007s

Run Session: 20260807200200 [Golden DE] (Total Batch E2E Duration: 36.292s)
---------------------------------------------------------------------------
translate_text (00)                 | █████                                    | 5.207s
lemmatization (00)                  | ███████████                              | 10.548s
the_cut (00)                        |            █                             | 0.037s
html_generation (00)                |            █                             | 0.008s
background_text_translation (00)    |             ███████████████████████████  | 24.897s
html_generation (08)                |             █                            | 0.005s
background_text_translation (08)    |              ██████                      | 6.296s
html_generation (07)                |              █                           | 0.004s
background_text_translation (07)    |               █                          | 1.602s
html_generation (06)                |               █                          | 0.004s
background_text_translation (06)    |                █                         | 0.781s
html_generation (05)                |                 █                        | 0.004s
background_text_translation (05)    |                 ██                       | 2.148s
html_generation (04)                |                  █                       | 0.004s
background_text_translation (04)    |                   ███                    | 3.354s
html_generation (03)                |                   █                      | 0.006s
background_text_translation (03)    |                    ██████                | 5.893s
html_generation (02)                |                     █                    | 0.005s
background_text_translation (02)    |                      █████               | 5.030s
html_generation (01)                |                       █                  | 0.006s
background_text_translation (01)    |                       ███                | 2.937s
cross_pollinate_from_siblings (00)  |                                        █ | 0.009s

Run Session: 20260807200100 [Golden EN] (Total Batch E2E Duration: 13.287s)
---------------------------------------------------------------------------
translate_text (00)                 | ██                                       | 0.691s
lemmatization (00)                  | ████████████                             | 4.063s
the_cut (00)                        |               █                          | 0.011s
html_generation (00)                |               █                          | 0.004s
background_text_translation (00)    |                ████████████████████████  | 8.017s
html_generation (03)                |                 █                        | 0.005s
background_text_translation (03)    |                  ████████████████        | 5.480s
html_generation (02)                |                    █                     | 0.005s
background_text_translation (02)    |                     ████                 | 1.617s
html_generation (01)                |                       █                  | 0.005s
background_text_translation (01)    |                        ████              | 1.597s
cross_pollinate_from_siblings (00)  |                                        █ | 0.003s

Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 17.228s)
---------------------------------------------------------------------------
translate_text (00)                 | ██████                                   | 2.848s
lemmatization (00)                  | █████████████████                        | 7.695s
the_cut (00)                        |                  █                       | 0.065s
html_generation (00)                |                      █                   | 0.010s
background_text_translation (00)    |                       █                  | 0.004s
html_generation (08)                |                        █                 | 0.004s
html_generation (07)                |                          █               | 0.004s
html_generation (06)                |                            █             | 0.003s
html_generation (05)                |                              █           | 0.003s
html_generation (04)                |                                 █        | 0.004s
html_generation (03)                |                                   █      | 0.005s
html_generation (02)                |                                      █   | 0.004s
html_generation (01)                |                                        █ | 0.004s
cross_pollinate_from_siblings (00)  |                                        █ | 0.012s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 11.211s)
---------------------------------------------------------------------------
lemmatization (00)                  | █████████████████████                    | 5.890s
translate_text (00)                 | ██                                       | 0.741s
the_cut (00)                        |                         █                | 0.074s
html_generation (00)                |                           █              | 0.006s
html_generation (03)                |                             █            | 0.005s
background_text_translation (00)    |                              █           | 0.005s
html_generation (02)                |                                     █    | 0.004s
html_generation (01)                |                                        █ | 0.003s
cross_pollinate_from_siblings (00)  |                                        █ | 0.004s

```

## [Commit: 2544a7c] 20260819013716 to desk
```text
Run Session: 20260807200100 [Golden EN] (Total Batch E2E Duration: 11.203s)
---------------------------------------------------------------------------
lemmatization (00)                  | ██████████                               | 2.941s
translate_text (00)                 |           ██                             | 0.581s
the_cut (00)                        |             █                            | 0.011s
html_generation (00)                |             █                            | 0.005s
background_text_translation (00)    |               █████████████████████      | 6.144s
html_generation (03)                |               █                          | 0.005s
html_generation (02)                |                   █                      | 0.004s
html_generation (01)                |                      █                   | 0.004s
background_text_translation (01)    |                                     █    | 0.010s
cross_pollinate_from_siblings (01)  |                                     █    | 0.004s
background_text_translation (02)    |                                     █    | 0.009s
background_text_translation (03)    |                                        █ | 0.011s
cross_pollinate_from_siblings (02)  |                                        █ | 0.015s
cross_pollinate_from_siblings (03)  |                                        █ | 0.004s
cross_pollinate_from_siblings (00)  |                                        █ | 0.003s

```

## [Commit: 02907f0] 20260819025120 to desk
```text
Run Session: 20260807200100 [Golden EN] (Total Batch E2E Duration: 11.097s)
---------------------------------------------------------------------------
lemmatization (00)                  | ███████████                              | 3.064s
translate_text (00)                 |            █                             | 0.537s
the_cut (00)                        |              █                           | 0.009s
html_generation (00)                |              █                           | 0.005s
background_text_translation (00)    |               █████████████████          | 4.882s
html_generation (03)                |                █                         | 0.005s
html_generation (02)                |                   █                      | 0.004s
html_generation (01)                |                       █                  | 0.004s
background_text_translation (01)    |                                 █        | 0.009s
cross_pollinate_from_siblings (01)  |                                 █        | 0.004s
background_text_translation (02)    |                                     █    | 0.011s
cross_pollinate_from_siblings (02)  |                                     █    | 0.003s
background_text_translation (03)    |                                     █    | 0.010s
cross_pollinate_from_siblings (03)  |                                        █ | 0.004s
cross_pollinate_from_siblings (00)  |                                        █ | 0.004s

```

## [Commit: 735d0c1] 20260819031636 to desk
```text
Run Session: 20260807200100 [Golden EN] (Total Batch E2E Duration: 29.271s)
---------------------------------------------------------------------------
lemmatization (00)                  | ████                                     | 3.042s
translate_text (00)                 |     ███                                  | 2.645s
the_cut (00)                        |        █                                 | 0.009s
html_generation (00)                |        █                                 | 0.005s
background_text_translation (00)    |         ████████████████████████████     | 21.001s
html_generation (03)                |         █                                | 0.005s
html_generation (02)                |          █                               | 0.004s
html_generation (01)                |            █                             | 0.004s
background_text_translation (01)    |                                      █   | 0.010s
cross_pollinate_from_siblings (01)  |                                      █   | 0.004s
background_text_translation (02)    |                                       █  | 0.010s
cross_pollinate_from_siblings (02)  |                                       █  | 0.003s
background_text_translation (03)    |                                        █ | 0.009s
cross_pollinate_from_siblings (03)  |                                        █ | 0.003s
cross_pollinate_from_siblings (00)  |                                        █ | 0.003s

```

## [Commit: 2123428] 20260819034156
```text
Run Session: 20260807200100 [Golden EN] (Total Batch E2E Duration: 30.923s)
---------------------------------------------------------------------------
lemmatization (00)                  | ██████                                   | 4.851s
translate_text (00)                 |       ███                                | 2.610s
the_cut (00)                        |          █                               | 0.011s
html_generation (00)                |          █                               | 0.006s
background_text_translation (00)    |           ████████████████████████████   | 21.979s
html_generation (03)                |           █                              | 0.005s
html_generation (02)                |            █                             | 0.005s
html_generation (01)                |              █                           | 0.004s
background_text_translation (01)    |                                       █  | 0.013s
cross_pollinate_from_siblings (01)  |                                       █  | 0.005s
background_text_translation (02)    |                                       █  | 0.012s
cross_pollinate_from_siblings (02)  |                                       █  | 0.006s
background_text_translation (03)    |                                        █ | 0.011s
cross_pollinate_from_siblings (03)  |                                        █ | 0.004s
cross_pollinate_from_siblings (00)  |                                        █ | 0.005s

```

## [Commit: e38410e] 20260819103446 to desk
```text
Run Session: 20260807200100 [Golden EN] (Total Batch E2E Duration: 12.690s)
---------------------------------------------------------------------------
lemmatization (00)                  | ██████████                               | 3.355s
translate_text (00)                 |           ██                             | 0.663s
the_cut (00)                        |             █                            | 0.009s
html_generation (00)                |             █                            | 0.008s
background_text_translation (00)    |               ███████████████████        | 6.067s
html_generation (03)                |               █                          | 0.005s
html_generation (02)                |                   █                      | 0.005s
html_generation (01)                |                       █                  | 0.004s
background_text_translation (01)    |                                  █       | 0.012s
cross_pollinate_from_siblings (01)  |                                  █       | 0.006s
background_text_translation (02)    |                                     █    | 0.011s
cross_pollinate_from_siblings (02)  |                                     █    | 0.005s
background_text_translation (03)    |                                        █ | 0.014s
cross_pollinate_from_siblings (03)  |                                        █ | 0.005s
cross_pollinate_from_siblings (00)  |                                        █ | 0.005s

```

## [Commit: 7ead811] 20260819112924 to desk
```text
Run Session: 20260807200100 [Golden EN] (Total Batch E2E Duration: 8.352s)
---------------------------------------------------------------------------
lemmatization (00)                  | ███████████████                          | 3.268s
translate_text (00)                 |                █                         | 0.172s
the_cut (00)                        |                 █                        | 0.015s
html_generation (00)                |                  █                       | 0.007s
background_text_translation (00)    |                    ████████              | 1.714s
html_generation (03)                |                     █                    | 0.007s
html_generation (02)                |                           █              | 0.006s
html_generation (01)                |                                 █        | 0.005s
background_text_translation (01)    |                                   █      | 0.012s
cross_pollinate_from_siblings (01)  |                                   █      | 0.004s
background_text_translation (02)    |                                    █     | 0.013s
background_text_translation (03)    |                                    █     | 0.014s
cross_pollinate_from_siblings (02)  |                                        █ | 0.017s
cross_pollinate_from_siblings (03)  |                                        █ | 0.006s
cross_pollinate_from_siblings (00)  |                                        █ | 0.005s

```

## Golden Run Aggregates

### 20260807190100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 1 | 5.890 | 5.890 | 5.890 |
| `translate_text` | 1 | 0.741 | 0.741 | 0.741 |
| `the_cut` | 1 | 0.074 | 0.074 | 0.074 |
| `background_text_translation` | 1 | 0.005 | 0.005 | 0.005 |
| `html_generation` | 4 | 0.003 | 0.005 | 0.006 |
| `cross_pollinate_from_siblings` | 1 | 0.004 | 0.004 | 0.004 |

### 20260807190200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 1 | 7.695 | 7.695 | 7.695 |
| `translate_text` | 1 | 2.848 | 2.848 | 2.848 |
| `the_cut` | 1 | 0.065 | 0.065 | 0.065 |
| `cross_pollinate_from_siblings` | 1 | 0.012 | 0.012 | 0.012 |
| `html_generation` | 9 | 0.003 | 0.005 | 0.010 |
| `background_text_translation` | 1 | 0.004 | 0.004 | 0.004 |

### 20260807200100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 7 | 2.941 | 3.512 | 4.851 |
| `background_text_translation` | 28 | 0.009 | 2.811 | 21.979 |
| `translate_text` | 7 | 0.172 | 1.129 | 2.645 |
| `the_cut` | 7 | 0.009 | 0.011 | 0.015 |
| `cross_pollinate_from_siblings` | 25 | 0.003 | 0.005 | 0.017 |
| `html_generation` | 28 | 0.004 | 0.005 | 0.008 |

### 20260807200200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 1 | 10.548 | 10.548 | 10.548 |
| `background_text_translation` | 9 | 0.781 | 5.882 | 24.897 |
| `translate_text` | 1 | 5.207 | 5.207 | 5.207 |
| `the_cut` | 1 | 0.037 | 0.037 | 0.037 |
| `cross_pollinate_from_siblings` | 1 | 0.009 | 0.009 | 0.009 |
| `html_generation` | 9 | 0.004 | 0.005 | 0.008 |

### 20260807210100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 1 | 3.857 | 3.857 | 3.857 |
| `background_text_translation` | 3 | 0.715 | 1.220 | 1.506 |
| `translate_text` | 1 | 1.149 | 1.149 | 1.149 |
| `the_cut` | 1 | 0.014 | 0.014 | 0.014 |
| `cross_pollinate_from_siblings` | 1 | 0.007 | 0.007 | 0.007 |
| `html_generation` | 4 | 0.004 | 0.005 | 0.006 |

### 20260807210200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 1 | 6.435 | 6.435 | 6.435 |
| `background_text_translation` | 2 | 0.647 | 1.292 | 1.937 |
| `translate_text` | 1 | 1.191 | 1.191 | 1.191 |
| `the_cut` | 1 | 0.037 | 0.037 | 0.037 |
| `cross_pollinate_from_siblings` | 1 | 0.016 | 0.016 | 0.016 |
| `html_generation` | 9 | 0.003 | 0.004 | 0.008 |

### 20260807220100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `background_text_translation` | 3 | 8.902 | 18.671 | 23.620 |
| `translate_text` | 1 | 9.119 | 9.119 | 9.119 |
| `lemmatization` | 1 | 4.247 | 4.247 | 4.247 |
| `cross_pollinate_from_siblings` | 1 | 0.018 | 0.018 | 0.018 |
| `the_cut` | 1 | 0.013 | 0.013 | 0.013 |
| `html_generation` | 4 | 0.004 | 0.005 | 0.006 |

### 20260807230100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 1 | 11.552 | 11.552 | 11.552 |
| `intellifiller_enrichment` | 3 | 1.287 | 7.209 | 18.240 |
| `background_text_translation` | 4 | 0.005 | 5.423 | 18.256 |
| `translate_text` | 1 | 3.235 | 3.235 | 3.235 |
| `the_cut` | 1 | 0.012 | 0.012 | 0.012 |
| `html_generation` | 4 | 0.004 | 0.005 | 0.006 |
| `cross_pollinate_from_siblings` | 1 | 0.003 | 0.003 | 0.003 |

## Phase Glossary
- **`translate_text`**: (Network IO-Bound) Holistically translating the source paragraph/sentence via external APIs (e.g. DeepL).
- **`lemmatization`**: (CPU-Bound) Tokenizing text and executing morphological/Anki lookups via Kardenwort Core to generate the data grid.
- **`the_cut`**: (CPU-Bound) Slicing the master TSV into individual child sentence files during Multi-mode runs.
- **`background_text_translation`**: The progressive worker updating BOTH the text translation AND the individual base lemma translations asynchronously without blocking the UI.
