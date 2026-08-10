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
- [[Commit: e418fa5] 20260810042316](#commit-e418fa5-20260810042316)
- [[Commit: e793ea9] 20260810043126](#commit-e793ea9-20260810043126)
- [[Commit: 5122804] 20260810152949](#commit-5122804-20260810152949)
- [[Commit: 97cef48] 20260810163734](#commit-97cef48-20260810163734)
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

## [Commit: e418fa5] 20260810042316
```text
Run Session: 20260807230100 [Golden EN] (Total Batch E2E Duration: 6.960s)
---------------------------------------------------------------------------
translate_text (00)                 | ███                                      | 0.656s
lemmatization (00)                  | ██████████████                           | 2.591s
the_cut (00)                        |                  █                       | 0.010s
html_generation (00)                |                    █                     | 0.004s
html_generation (03)                |                       █                  | 0.003s
background_text_translation (03)    |                         ███████          | 1.269s
intellifiller_enrichment (03)       |                         ███████          | 1.253s
html_generation (02)                |                           █              | 0.003s
background_text_translation (00)    |                            ████████████  | 2.157s
intellifiller_enrichment (00)       |                            ████████████  | 2.141s
background_text_translation (02)    |                             █            | 0.003s
html_generation (01)                |                              █           | 0.002s
background_text_translation (01)    |                                ███████   | 1.339s
intellifiller_enrichment (01)       |                                ███████   | 1.323s
cross_pollinate_from_siblings (00)  |                                        █ | 0.004s

```

## [Commit: e793ea9] 20260810043126
```text
Run Session: 20260807220100 [Golden EN] (Total Batch E2E Duration: 21.723s)
---------------------------------------------------------------------------
translate_text (00)                 | █████████                                | 4.912s
lemmatization (00)                  | █████                                    | 2.806s
the_cut (00)                        |          █                               | 0.009s
html_generation (00)                |          █                               | 0.005s
background_text_translation (00)    |           ███████████████████████████    | 14.870s
html_generation (03)                |             █                            | 0.004s
background_text_translation (03)    |             ███████████████████████████  | 14.783s
html_generation (02)                |              █                           | 0.003s
background_text_translation (02)    |               █████████                  | 5.236s
html_generation (01)                |                █                         | 0.003s
cross_pollinate_from_siblings (00)  |                                        █ | 0.014s

```

## [Commit: 5122804] 20260810152949
```text
Run Session: 20260807220100 [Golden EN] (Total Batch E2E Duration: 39.135s)
---------------------------------------------------------------------------
translate_text (00)                 | █████████████                            | 12.801s
lemmatization (00)                  | ████                                     | 4.671s
the_cut (00)                        |              █                           | 0.014s
html_generation (00)                |              █                           | 0.006s
html_generation (03)                |               █                          | 0.005s
background_text_translation (03)    |               █████████████████████████  | 24.795s
background_text_translation (00)    |                ████████████████████████  | 24.431s
html_generation (02)                |                █                         | 0.005s
background_text_translation (02)    |                 █████████                | 9.207s
html_generation (01)                |                 █                        | 0.005s
cross_pollinate_from_siblings (00)  |                                        █ | 0.020s

```

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

## Golden Run Aggregates

### 20260807190100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 2 | 4.111 | 5.001 | 5.890 |
| `translate_text` | 2 | 0.696 | 0.719 | 0.741 |
| `the_cut` | 2 | 0.022 | 0.048 | 0.074 |
| `html_generation` | 8 | 0.003 | 0.005 | 0.007 |
| `background_text_translation` | 1 | 0.005 | 0.005 | 0.005 |
| `cross_pollinate_from_siblings` | 1 | 0.004 | 0.004 | 0.004 |

### 20260807190200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 2 | 4.437 | 6.066 | 7.695 |
| `translate_text` | 2 | 0.549 | 1.698 | 2.848 |
| `the_cut` | 2 | 0.027 | 0.046 | 0.065 |
| `cross_pollinate_from_siblings` | 1 | 0.012 | 0.012 | 0.012 |
| `background_text_translation` | 1 | 0.004 | 0.004 | 0.004 |
| `html_generation` | 18 | 0.002 | 0.004 | 0.010 |

### 20260807200100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 2 | 2.667 | 3.365 | 4.063 |
| `background_text_translation` | 7 | 1.179 | 3.351 | 8.017 |
| `translate_text` | 2 | 0.558 | 0.625 | 0.691 |
| `the_cut` | 2 | 0.011 | 0.013 | 0.014 |
| `html_generation` | 8 | 0.003 | 0.004 | 0.005 |
| `cross_pollinate_from_siblings` | 1 | 0.003 | 0.003 | 0.003 |

### 20260807200200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 2 | 4.831 | 7.690 | 10.548 |
| `background_text_translation` | 17 | 0.606 | 4.375 | 24.897 |
| `translate_text` | 2 | 0.618 | 2.913 | 5.207 |
| `the_cut` | 2 | 0.034 | 0.035 | 0.037 |
| `cross_pollinate_from_siblings` | 1 | 0.009 | 0.009 | 0.009 |
| `html_generation` | 18 | 0.003 | 0.004 | 0.008 |

### 20260807210100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 2 | 2.891 | 3.374 | 3.857 |
| `translate_text` | 2 | 1.099 | 1.124 | 1.149 |
| `background_text_translation` | 5 | 0.567 | 1.098 | 1.506 |
| `the_cut` | 2 | 0.014 | 0.014 | 0.014 |
| `cross_pollinate_from_siblings` | 1 | 0.007 | 0.007 | 0.007 |
| `html_generation` | 8 | 0.003 | 0.004 | 0.006 |

### 20260807210200 [Golden DE]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 2 | 4.791 | 5.613 | 6.435 |
| `translate_text` | 2 | 1.042 | 1.116 | 1.191 |
| `background_text_translation` | 4 | 0.565 | 0.945 | 1.937 |
| `the_cut` | 2 | 0.036 | 0.037 | 0.037 |
| `cross_pollinate_from_siblings` | 1 | 0.016 | 0.016 | 0.016 |
| `html_generation` | 18 | 0.002 | 0.004 | 0.008 |

### 20260807220100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `background_text_translation` | 18 | 5.236 | 14.666 | 24.795 |
| `translate_text` | 6 | 4.912 | 7.757 | 12.801 |
| `lemmatization` | 6 | 2.806 | 3.724 | 4.671 |
| `cross_pollinate_from_siblings` | 6 | 0.014 | 0.187 | 1.021 |
| `the_cut` | 6 | 0.009 | 0.012 | 0.014 |
| `html_generation` | 25 | 0.003 | 0.004 | 0.010 |

### 20260807230100 [Golden EN]
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 6 | 2.584 | 4.341 | 11.552 |
| `intellifiller_enrichment` | 17 | 0.969 | 3.157 | 18.240 |
| `background_text_translation` | 23 | 0.003 | 2.346 | 18.256 |
| `translate_text` | 6 | 0.520 | 1.100 | 3.235 |
| `the_cut` | 6 | 0.010 | 0.012 | 0.017 |
| `cross_pollinate_from_siblings` | 4 | 0.003 | 0.005 | 0.008 |
| `html_generation` | 24 | 0.002 | 0.004 | 0.006 |

## Phase Glossary
- **`translate_text`**: (Network IO-Bound) Holistically translating the source paragraph/sentence via external APIs (e.g. DeepL).
- **`lemmatization`**: (CPU-Bound) Tokenizing text and executing morphological/Anki lookups via Kardenwort Core to generate the data grid.
- **`the_cut`**: (CPU-Bound) Slicing the master TSV into individual child sentence files during Multi-mode runs.
- **`background_text_translation`**: The progressive worker updating BOTH the text translation AND the individual base lemma translations asynchronously without blocking the UI.
