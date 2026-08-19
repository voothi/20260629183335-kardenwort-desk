# Performance Dynamics Over Time (By Git Commit)

## Table of Contents
- [[Commit: 3e682d7] 20260813143711](#commit-3e682d7-20260813143711)
- [[Commit: 2544a7c] 20260819013716 to desk](#commit-2544a7c-20260819013716-to-desk)
- [[Commit: 02907f0] 20260819025120 to desk](#commit-02907f0-20260819025120-to-desk)
- [[Commit: 735d0c1] 20260819031636 to desk](#commit-735d0c1-20260819031636-to-desk)
- [[Commit: 2123428] 20260819034156](#commit-2123428-20260819034156)
- [[Commit: e38410e] 20260819103446 to desk](#commit-e38410e-20260819103446-to-desk)
- [[Commit: 7ead811] 20260819112924 to desk](#commit-7ead811-20260819112924-to-desk)
- [[Commit: 31a89e5] 20260819120105 to desk](#commit-31a89e5-20260819120105-to-desk)
- [[Commit: cee003c] 20260819161016 to desk](#commit-cee003c-20260819161016-to-desk)
- [[Commit: 72ae560] 20260819173612 to desk](#commit-72ae560-20260819173612-to-desk)
- [[Commit: 6639a4d] 20260819180813 to desk](#commit-6639a4d-20260819180813-to-desk)
- [Golden Run Aggregates](#golden-run-aggregates)
- [Phase Glossary](#phase-glossary)

## [Commit: 3e682d7] 20260813143711 (Avg wait between runs: 12.80s)
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

## [Commit: 31a89e5] 20260819120105 to desk
```text
Run Session: 20260807200100 [Golden EN] (Total Batch E2E Duration: 6.290s)
---------------------------------------------------------------------------
lemmatization (00)                  | ███████                                  | 1.246s
translate_text (00)                 |        █                                 | 0.139s
the_cut (00)                        |          █                               | 0.012s
html_generation (00)                |          █                               | 0.006s
background_text_translation (00)    |             █████████████                | 2.114s
html_generation (03)                |              █                           | 0.005s
html_generation (02)                |                      █                   | 0.004s
html_generation (01)                |                              █           | 0.004s
background_text_translation (01)    |                                 █        | 0.015s
cross_pollinate_from_siblings (01)  |                                 █        | 0.011s
background_text_translation (02)    |                                        █ | 0.023s
cross_pollinate_from_siblings (02)  |                                        █ | 0.012s
background_text_translation (03)    |                                        █ | 0.025s
cross_pollinate_from_siblings (03)  |                                        █ | 0.012s
cross_pollinate_from_siblings (00)  |                                        █ | 0.006s

```

## [Commit: cee003c] 20260819161016 to desk
```text
Run Session: 20260807200100 [Golden EN] (Total Batch E2E Duration: 6.296s)
---------------------------------------------------------------------------
lemmatization (00)                  | ████                                     | 0.668s
translate_text (00)                 |     █████                                | 0.911s
the_cut (00)                        |           █                              | 0.011s
html_generation (00)                |           █                              | 0.004s
background_text_translation (00)    |             █████████████                | 2.198s
html_generation (03)                |              █                           | 0.004s
html_generation (02)                |                  █                       | 0.003s
html_generation (01)                |                      █                   | 0.003s
background_text_translation (01)    |                            █             | 0.012s
cross_pollinate_from_siblings (01)  |                            █             | 0.004s
background_text_translation (02)    |                                  █       | 0.013s
cross_pollinate_from_siblings (02)  |                                  █       | 0.004s
background_text_translation (03)    |                                  █       | 0.014s
cross_pollinate_from_siblings (03)  |                                        █ | 0.003s
cross_pollinate_from_siblings (00)  |                                        █ | 0.003s

```

## [Commit: 72ae560] 20260819173612 to desk (Avg wait between runs: 17.85s)
```text
Run Session: 20260807230200 [Golden DE] (Total Batch E2E Duration: 28.879s)
---------------------------------------------------------------------------
translate_text (00)                 | ██                                       | 2.057s
lemmatization (00)                  | ██                                       | 1.998s
the_cut (00)                        |   █                                      | 0.024s
html_generation (08)                |    █                                     | 0.004s
html_generation (07)                |     █                                    | 0.003s
html_generation (06)                |      █                                   | 0.003s
html_generation (00)                |      █                                   | 0.007s
background_text_translation (00)    |       █████████████████                  | 12.405s
intellifiller_enrichment (00)       |       █████████████████                  | 12.380s
html_generation (05)                |       █                                  | 0.003s
html_generation (04)                |        █                                 | 0.003s
html_generation (03)                |          █                               | 0.004s
html_generation (02)                |           █                              | 0.004s
html_generation (01)                |            █                             | 0.003s
background_text_translation (01)    |                        ██                | 2.055s
intellifiller_enrichment (01)       |                        ██                | 2.029s
cross_pollinate_from_siblings (01)  |                           █              | 0.015s
background_text_translation (02)    |                            █             | 0.014s
cross_pollinate_from_siblings (02)  |                             █            | 0.015s
background_text_translation (03)    |                              █           | 1.165s
intellifiller_enrichment (03)       |                              █           | 1.137s
cross_pollinate_from_siblings (03)  |                                █         | 0.013s
background_text_translation (04)    |                                 █        | 0.014s
cross_pollinate_from_siblings (04)  |                                 █        | 0.011s
background_text_translation (05)    |                                  █       | 0.015s
cross_pollinate_from_siblings (05)  |                                  █       | 0.012s
background_text_translation (06)    |                                    █     | 0.014s
cross_pollinate_from_siblings (06)  |                                    █     | 0.010s
background_text_translation (07)    |                                     █    | 1.286s
intellifiller_enrichment (07)       |                                     █    | 1.262s
cross_pollinate_from_siblings (07)  |                                       █  | 0.008s
background_text_translation (08)    |                                        █ | 0.014s
cross_pollinate_from_siblings (08)  |                                        █ | 0.010s
cross_pollinate_from_siblings (00)  |                                        █ | 0.013s

Run Session: 20260807230100 [Golden EN] (Total Batch E2E Duration: 5.829s)
---------------------------------------------------------------------------
lemmatization (00)                  | ████                                     | 0.650s
translate_text (00)                 |     █                                    | 0.206s
the_cut (00)                        |       █                                  | 0.010s
html_generation (00)                |        █                                 | 0.005s
background_text_translation (00)    |          ███████████████                 | 2.260s
intellifiller_enrichment (00)       |           ███████████████                | 2.227s
html_generation (03)                |           █                              | 0.004s
html_generation (02)                |               █                          | 0.003s
html_generation (01)                |                   █                      | 0.003s
background_text_translation (01)    |                          █               | 0.016s
cross_pollinate_from_siblings (01)  |                          █               | 0.005s
background_text_translation (02)    |                                 █        | 0.012s
cross_pollinate_from_siblings (02)  |                                 █        | 0.004s
background_text_translation (03)    |                                        █ | 0.013s
cross_pollinate_from_siblings (03)  |                                        █ | 0.003s
cross_pollinate_from_siblings (00)  |                                        █ | 0.003s

Run Session: 20260807220200 [Golden DE] (Total Batch E2E Duration: 19.000s)
---------------------------------------------------------------------------
lemmatization (00)                  | ████                                     | 2.112s
translate_text (00) [ERROR]         |          █                               | 0.015s
the_cut (00)                        |          █                               | 0.027s
html_generation (00)                |            █                             | 0.007s
background_text_translation (00)    |             █                            | 0.558s
html_generation (08)                |              █                           | 0.004s
html_generation (07)                |                █                         | 0.003s
html_generation (06)                |                 █                        | 0.003s
html_generation (05)                |                   █                      | 0.003s
html_generation (04)                |                     █                    | 0.003s
html_generation (03)                |                       █                  | 0.005s
html_generation (02)                |                         █                | 0.004s
html_generation (01)                |                           █              | 0.004s
background_text_translation (01)    |                            █             | 0.298s
cross_pollinate_from_siblings (01)  |                             █            | 0.013s
background_text_translation (02)    |                             █            | 0.151s
background_text_translation (03)    |                             █            | 0.174s
cross_pollinate_from_siblings (02)  |                               █          | 0.036s
cross_pollinate_from_siblings (03)  |                               █          | 0.024s
background_text_translation (04)    |                                   █      | 0.118s
cross_pollinate_from_siblings (04)  |                                   █      | 0.010s
background_text_translation (07)    |                                   █      | 0.162s
cross_pollinate_from_siblings (07)  |                                    █     | 0.010s
background_text_translation (08) [ERROR] |                                    ████  | 2.179s
translate_text (08) [ERROR]         |                                        █ | 0.018s
cross_pollinate_from_siblings (00)  |                                        █ | 0.019s

Run Session: 20260807220100 [Golden EN] (Total Batch E2E Duration: 6.516s)
---------------------------------------------------------------------------
lemmatization (00)                  | ████                                     | 0.654s
translate_text (00) [ERROR]         |                  █                       | 0.005s
the_cut (00)                        |                  █                       | 0.013s
html_generation (00)                |                    █                     | 0.004s
background_text_translation (00)    |                      █                   | 0.280s
html_generation (03)                |                      █                   | 0.004s
html_generation (02)                |                           █              | 0.003s
background_text_translation (02)    |                               █          | 0.155s
html_generation (01)                |                               █          | 0.004s
cross_pollinate_from_siblings (02)  |                                █         | 0.003s
background_text_translation (03)    |                                       █  | 0.269s
cross_pollinate_from_siblings (03)  |                                        █ | 0.003s
cross_pollinate_from_siblings (00)  |                                        █ | 0.015s

Run Session: 20260807210200 [Golden DE] (Total Batch E2E Duration: 14.912s)
---------------------------------------------------------------------------
translate_text (00)                 | ██                                       | 0.765s
lemmatization (00)                  | █████                                    | 2.020s
the_cut (00)                        |      █                                   | 0.024s
html_generation (08)                |        █                                 | 0.004s
html_generation (07)                |          █                               | 0.004s
html_generation (06)                |             █                            | 0.003s
html_generation (05)                |               █                          | 0.003s
html_generation (00)                |               █                          | 0.008s
background_text_translation (00)    |                 ███                      | 1.381s
html_generation (04)                |                 █                        | 0.003s
html_generation (03)                |                       █                  | 0.004s
html_generation (02)                |                         █                | 0.004s
html_generation (01)                |                            █             | 0.004s
background_text_translation (01)    |                             █            | 0.015s
cross_pollinate_from_siblings (01)  |                             █            | 0.014s
background_text_translation (02)    |                                █         | 0.016s
cross_pollinate_from_siblings (02)  |                                █         | 0.012s
background_text_translation (03)    |                                   █      | 0.022s
cross_pollinate_from_siblings (03)  |                                   █      | 0.026s
background_text_translation (04)    |                                   █      | 0.017s
cross_pollinate_from_siblings (04)  |                                   █      | 0.012s
background_text_translation (07)    |                                      █   | 0.012s
cross_pollinate_from_siblings (07)  |                                      █   | 0.007s
background_text_translation (08)    |                                        █ | 0.012s
cross_pollinate_from_siblings (08)  |                                        █ | 0.009s
cross_pollinate_from_siblings (00)  |                                        █ | 0.009s

Run Session: 20260807210100 [Golden EN] (Total Batch E2E Duration: 4.941s)
---------------------------------------------------------------------------
lemmatization (00)                  | █████                                    | 0.665s
translate_text (00)                 |         ██████                           | 0.792s
the_cut (00)                        |                █                         | 0.010s
html_generation (00)                |                  █                       | 0.005s
background_text_translation (00)    |                     █████                | 0.620s
html_generation (03)                |                     █                    | 0.004s
html_generation (02)                |                           █              | 0.003s
html_generation (01)                |                                █         | 0.004s
background_text_translation (02)    |                                █         | 0.014s
cross_pollinate_from_siblings (02)  |                                █         | 0.003s
background_text_translation (03)    |                                █         | 0.015s
cross_pollinate_from_siblings (03)  |                                        █ | 0.003s
cross_pollinate_from_siblings (00)  |                                        █ | 0.004s

Run Session: 20260807200200 [Golden DE] (Total Batch E2E Duration: 19.360s)
---------------------------------------------------------------------------
translate_text (00)                 | █                                        | 0.180s
lemmatization (00)                  | ███                                      | 1.915s
the_cut (00)                        |     █                                    | 0.025s
html_generation (00)                |     █                                    | 0.007s
background_text_translation (00)    |     █████████████                        | 6.464s
html_generation (08)                |      █                                   | 0.004s
html_generation (07)                |       █                                  | 0.003s
html_generation (06)                |        █                                 | 0.003s
html_generation (05)                |          █                               | 0.003s
html_generation (04)                |           █                              | 0.003s
html_generation (03)                |            █                             | 0.004s
html_generation (02)                |              █                           | 0.004s
html_generation (01)                |               █                          | 0.004s
background_text_translation (01)    |                   █                      | 0.015s
cross_pollinate_from_siblings (01)  |                   █                      | 0.013s
background_text_translation (02)    |                         █                | 0.014s
cross_pollinate_from_siblings (02)  |                         █                | 0.012s
background_text_translation (03)    |                             █            | 0.017s
cross_pollinate_from_siblings (03)  |                             █            | 0.012s
background_text_translation (04)    |                               █          | 0.013s
cross_pollinate_from_siblings (04)  |                               █          | 0.013s
background_text_translation (05)    |                                 █        | 0.015s
cross_pollinate_from_siblings (05)  |                                  █       | 0.011s
background_text_translation (06)    |                                    █     | 0.020s
cross_pollinate_from_siblings (06)  |                                    █     | 0.013s
background_text_translation (07)    |                                      █   | 0.249s
cross_pollinate_from_siblings (07)  |                                      █   | 0.011s
background_text_translation (08)    |                                        █ | 0.013s
cross_pollinate_from_siblings (08)  |                                        █ | 0.007s
cross_pollinate_from_siblings (00)  |                                        █ | 0.008s

Run Session: 20260807200100 [Golden EN] (Total Batch E2E Duration: 4.375s)
---------------------------------------------------------------------------
lemmatization (00)                  | █████                                    | 0.605s
translate_text (00)                 |      █                                   | 0.148s
the_cut (00)                        |        █                                 | 0.010s
html_generation (00)                |        █                                 | 0.005s
background_text_translation (00)    |           ██████████████████████         | 2.436s
html_generation (03)                |            █                             | 0.004s
html_generation (02)                |                  █                       | 0.003s
html_generation (01)                |                        █                 | 0.004s
background_text_translation (01)    |                                  █       | 0.014s
cross_pollinate_from_siblings (01)  |                                  █       | 0.005s
background_text_translation (02)    |                                        █ | 0.013s
cross_pollinate_from_siblings (02)  |                                        █ | 0.004s
background_text_translation (03)    |                                        █ | 0.014s
cross_pollinate_from_siblings (03)  |                                        █ | 0.005s
cross_pollinate_from_siblings (00)  |                                        █ | 0.004s

Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 10.377s)
---------------------------------------------------------------------------
translate_text (00)                 | █                                        | 0.396s
lemmatization (00)                  | ███████                                  | 1.877s
the_cut (00)                        |        █                                 | 0.022s
html_generation (08)                |           █                              | 0.003s
html_generation (07)                |             █                            | 0.003s
html_generation (06)                |                █                         | 0.003s
html_generation (05)                |                   █                      | 0.003s
html_generation (04)                |                      █                   | 0.003s
html_generation (00)                |                        █                 | 0.006s
html_generation (03)                |                         █                | 0.004s
background_text_translation (00)    |                         █████████        | 2.357s
html_generation (02)                |                            █             | 0.003s
html_generation (01)                |                                █         | 0.004s
background_text_translation (01)    |                                   █      | 0.012s
cross_pollinate_from_siblings (01)  |                                   █      | 0.010s
background_text_translation (03)    |                                    █     | 0.014s
cross_pollinate_from_siblings (03)  |                                    █     | 0.009s
background_text_translation (07)    |                                        █ | 0.013s
cross_pollinate_from_siblings (07)  |                                        █ | 0.010s
cross_pollinate_from_siblings (00)  |                                        █ | 0.010s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 3.713s)
---------------------------------------------------------------------------
lemmatization (00)                  | ██████                                   | 0.649s
translate_text (00)                 |        █                                 | 0.176s
the_cut (00)                        |          █                               | 0.011s
html_generation (00)                |            █                             | 0.005s
background_text_translation (00)    |               █                          | 0.012s
html_generation (03)                |                           █              | 0.003s
html_generation (02)                |                                  █       | 0.003s
cross_pollinate_from_siblings (00)  |                                        █ | 0.004s
html_generation (01)                |                                        █ | 0.003s

```

## [Commit: 6639a4d] 20260819180813 to desk (Avg wait between runs: 17.47s)
```text
Run Session: 20260807220200 [Golden DE] (Total Batch E2E Duration: 20.911s)
---------------------------------------------------------------------------
lemmatization (00)                  | ███                                      | 2.061s
translate_text (00) [ERROR]         |         █                                | 0.015s
the_cut (00)                        |         █                                | 0.029s
html_generation (08)                |           █                              | 0.004s
html_generation (00)                |             █                            | 0.007s
background_text_translation (00)    |              █                           | 0.686s
html_generation (07)                |              █                           | 0.003s
html_generation (06)                |                █                         | 0.003s
html_generation (05)                |                  █                       | 0.003s
html_generation (04)                |                   █                      | 0.003s
html_generation (03)                |                      █                   | 0.004s
html_generation (02)                |                        █                 | 0.004s
html_generation (01)                |                          █               | 0.004s
background_text_translation (01)    |                           █              | 0.332s
cross_pollinate_from_siblings (01)  |                           █              | 0.011s
background_text_translation (02)    |                             █            | 0.146s
cross_pollinate_from_siblings (02)  |                             █            | 0.012s
background_text_translation (03)    |                               █          | 0.251s
cross_pollinate_from_siblings (03)  |                                █         | 0.011s
background_text_translation (04)    |                                  █       | 0.097s
cross_pollinate_from_siblings (04)  |                                  █       | 0.011s
background_text_translation (07)    |                                  █       | 0.115s
cross_pollinate_from_siblings (07)  |                                  █       | 0.023s
background_text_translation (08) [ERROR] |                                    ████  | 2.175s
translate_text (08) [ERROR]         |                                        █ | 0.016s
cross_pollinate_from_siblings (00)  |                                        █ | 0.021s

Run Session: 20260807220100 [Golden EN] (Total Batch E2E Duration: 7.277s)
---------------------------------------------------------------------------
lemmatization (00)                  | ███                                      | 0.677s
translate_text (00) [ERROR]         |                █                         | 0.038s
the_cut (00)                        |                █                         | 0.009s
html_generation (00)                |                  █                       | 0.005s
background_text_translation (00)    |                     █                    | 0.262s
html_generation (03)                |                       █                  | 0.004s
html_generation (02)                |                            █             | 0.003s
html_generation (01)                |                                  █       | 0.003s
background_text_translation (02)    |                                      █   | 0.117s
cross_pollinate_from_siblings (02)  |                                       █  | 0.004s
background_text_translation (03)    |                                       █  | 0.280s
cross_pollinate_from_siblings (03)  |                                        █ | 0.015s
cross_pollinate_from_siblings (00)  |                                        █ | 0.018s

```

## Golden Run Aggregates

### 20260807190100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 2 | 0.649 | 3.270 | 5.890 |
| `translate_text` | 2 | 0.176 | 0.459 | 0.741 |
| `the_cut` | 2 | 0.011 | 0.042 | 0.074 |
| `background_text_translation` | 2 | 0.005 | 0.008 | 0.012 |
| `html_generation` | 8 | 0.003 | 0.004 | 0.006 |
| `cross_pollinate_from_siblings` | 2 | 0.004 | 0.004 | 0.004 |

### 20260807190200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 2 | 1.877 | 4.786 | 7.695 |
| `translate_text` | 2 | 0.396 | 1.622 | 2.848 |
| `background_text_translation` | 5 | 0.004 | 0.480 | 2.357 |
| `the_cut` | 2 | 0.022 | 0.043 | 0.065 |
| `cross_pollinate_from_siblings` | 5 | 0.009 | 0.010 | 0.012 |
| `html_generation` | 18 | 0.003 | 0.004 | 0.010 |

### 20260807200100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 10 | 0.605 | 2.710 | 4.851 |
| `background_text_translation` | 40 | 0.009 | 2.140 | 21.979 |
| `translate_text` | 10 | 0.139 | 0.910 | 2.645 |
| `the_cut` | 10 | 0.009 | 0.011 | 0.015 |
| `cross_pollinate_from_siblings` | 37 | 0.003 | 0.006 | 0.017 |
| `html_generation` | 40 | 0.003 | 0.005 | 0.008 |

### 20260807200200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 2 | 1.915 | 6.232 | 10.548 |
| `background_text_translation` | 18 | 0.013 | 3.320 | 24.897 |
| `translate_text` | 2 | 0.180 | 2.694 | 5.207 |
| `the_cut` | 2 | 0.025 | 0.031 | 0.037 |
| `cross_pollinate_from_siblings` | 10 | 0.007 | 0.011 | 0.013 |
| `html_generation` | 18 | 0.003 | 0.004 | 0.008 |

### 20260807210100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 2 | 0.665 | 2.261 | 3.857 |
| `translate_text` | 2 | 0.792 | 0.971 | 1.149 |
| `background_text_translation` | 6 | 0.014 | 0.718 | 1.506 |
| `the_cut` | 2 | 0.010 | 0.012 | 0.014 |
| `cross_pollinate_from_siblings` | 4 | 0.003 | 0.004 | 0.007 |
| `html_generation` | 8 | 0.003 | 0.004 | 0.006 |

### 20260807210200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 2 | 2.020 | 4.227 | 6.435 |
| `translate_text` | 2 | 0.765 | 0.978 | 1.191 |
| `background_text_translation` | 9 | 0.012 | 0.451 | 1.937 |
| `the_cut` | 2 | 0.024 | 0.030 | 0.037 |
| `cross_pollinate_from_siblings` | 8 | 0.007 | 0.013 | 0.026 |
| `html_generation` | 18 | 0.003 | 0.004 | 0.008 |

### 20260807220100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `background_text_translation` | 9 | 0.117 | 6.375 | 23.620 |
| `translate_text` | 3 | 0.005 | 3.054 | 9.119 |
| `lemmatization` | 3 | 0.654 | 1.859 | 4.247 |
| `the_cut` | 3 | 0.009 | 0.012 | 0.013 |
| `cross_pollinate_from_siblings` | 7 | 0.003 | 0.011 | 0.018 |
| `html_generation` | 12 | 0.003 | 0.004 | 0.006 |

### 20260807220200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 2 | 2.061 | 2.086 | 2.112 |
| `background_text_translation` | 14 | 0.097 | 0.531 | 2.179 |
| `the_cut` | 2 | 0.027 | 0.028 | 0.029 |
| `cross_pollinate_from_siblings` | 12 | 0.010 | 0.017 | 0.036 |
| `translate_text` | 4 | 0.015 | 0.016 | 0.018 |
| `html_generation` | 18 | 0.003 | 0.004 | 0.007 |

### 20260807230100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 2 | 0.650 | 6.101 | 11.552 |
| `intellifiller_enrichment` | 4 | 1.287 | 5.964 | 18.240 |
| `background_text_translation` | 8 | 0.005 | 2.999 | 18.256 |
| `translate_text` | 2 | 0.206 | 1.721 | 3.235 |
| `the_cut` | 2 | 0.010 | 0.011 | 0.012 |
| `html_generation` | 8 | 0.003 | 0.004 | 0.006 |
| `cross_pollinate_from_siblings` | 5 | 0.003 | 0.004 | 0.005 |

### 20260807230200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `intellifiller_enrichment` | 4 | 1.137 | 4.202 | 12.380 |
| `translate_text` | 1 | 2.057 | 2.057 | 2.057 |
| `lemmatization` | 1 | 1.998 | 1.998 | 1.998 |
| `background_text_translation` | 9 | 0.014 | 1.887 | 12.405 |
| `the_cut` | 1 | 0.024 | 0.024 | 0.024 |
| `cross_pollinate_from_siblings` | 9 | 0.008 | 0.012 | 0.015 |
| `html_generation` | 9 | 0.003 | 0.004 | 0.007 |

## Phase Glossary
- **`translate_text`**: (Network IO-Bound) Holistically translating the source paragraph/sentence via external APIs (e.g. DeepL).
- **`lemmatization`**: (CPU-Bound) Tokenizing text and executing morphological/Anki lookups via Kardenwort Core to generate the data grid.
- **`the_cut`**: (CPU-Bound) Slicing the master TSV into individual child sentence files during Multi-mode runs.
- **`background_text_translation`**: The progressive worker updating BOTH the text translation AND the individual base lemma translations asynchronously without blocking the UI.
