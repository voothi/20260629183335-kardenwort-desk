# Performance Dynamics Over Time (By Git Commit)

## Table of Contents
- [[Commit: a463eb0] 20260808021042](#commit-a463eb0-20260808021042)
- [[Commit: e88d77e] 20260808024001](#commit-e88d77e-20260808024001)
- [[Commit: de01b5d] 20260808025419](#commit-de01b5d-20260808025419)
- [[Commit: 2b39df3] 20260808031021](#commit-2b39df3-20260808031021)
- [[Commit: 0fdf1cd] 20260808032420](#commit-0fdf1cd-20260808032420)
- [[Commit: 4fb7ab6] 20260808033502](#commit-4fb7ab6-20260808033502)
- [[Commit: da3461a] 20260808033732](#commit-da3461a-20260808033732)
- [[Commit: d2d36c1] 20260808034503](#commit-d2d36c1-20260808034503)
- [[Commit: 661df45] 20260808034829](#commit-661df45-20260808034829)
- [[Commit: 7878d05] 20260808035015](#commit-7878d05-20260808035015)
- [[Commit: e4551fb] 20260808035500](#commit-e4551fb-20260808035500)
- [[Commit: e44fd4c] 20260808040718](#commit-e44fd4c-20260808040718)
- [[Commit: 695a336] 20260808102415](#commit-695a336-20260808102415)
- [[Commit: e442fbe] 20260808112401](#commit-e442fbe-20260808112401)
- [[Commit: f2d0089] 20260808113932](#commit-f2d0089-20260808113932)
- [[Commit: feebde2] 20260808120810](#commit-feebde2-20260808120810)
- [[Commit: 7dfaef7] 20260808124319](#commit-7dfaef7-20260808124319)
- [[Commit: c01cea3] 20260808140351](#commit-c01cea3-20260808140351)
- [[Commit: c9c1f35] 20260808190717 to desk](#commit-c9c1f35-20260808190717-to-desk)
- [[Commit: cd6a550] 20260808191432](#commit-cd6a550-20260808191432)
- [[Commit: e4cd385] 20260808191717](#commit-e4cd385-20260808191717)
- [Golden Run Aggregates](#golden-run-aggregates)
- [Phase Glossary](#phase-glossary)

## [Commit: a463eb0] 20260808021042 (Avg wait between runs: 6.44s)
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

## [Commit: e88d77e] 20260808024001 (Avg wait between runs: 25.66s)
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

## [Commit: de01b5d] 20260808025419 (Avg wait between runs: 29.00s)
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

## [Commit: 2b39df3] 20260808031021 (Avg wait between runs: 89.26s)
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

## [Commit: 0fdf1cd] 20260808032420 (Avg wait between runs: -177.11s)
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 199.204s)
---------------------------------------------------------------------------
html_generation (05)                | █                                        | 0.003s
html_generation (04)                | █                                        | 0.003s
html_generation (03)                | █                                        | 0.003s
html_generation (02)                | █                                        | 0.004s
html_generation (01)                | █                                        | 0.003s
translate_text (00)                 |                                       █  | 1.095s
lemmatization (00)                  |                                       █  | 4.579s
the_cut (00)                        |                                        █ | 0.036s
html_generation (00)                |                                        █ | 0.006s
html_generation (08)                |                                        █ | 0.003s
html_generation (07)                |                                        █ | 0.002s
html_generation (06)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 6.118s)
---------------------------------------------------------------------------
translate_text (00)                 | ████████                                 | 1.255s
lemmatization (00)                  | ██████████████████                       | 2.880s
the_cut (00)                        |                          █               | 0.013s
html_generation (00)                |                           █              | 0.004s
html_generation (03)                |                              █           | 0.003s
html_generation (02)                |                                    █     | 0.002s
html_generation (01)                |                                        █ | 0.003s

```

## [Commit: 4fb7ab6] 20260808033502 (Avg wait between runs: 1.88s)
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 9.113s)
---------------------------------------------------------------------------
translate_text (00)                 | ████                                     | 1.048s
lemmatization (00)                  | ██████████████████████                   | 5.211s
the_cut (00)                        |                       █                  | 0.032s
html_generation (00)                |                          █               | 0.006s
html_generation (08)                |                           █              | 0.003s
html_generation (07)                |                               █          | 0.003s
html_generation (06)                |                                  █       | 0.002s
html_generation (05)                |                                     █    | 0.003s
html_generation (04)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 6.392s)
---------------------------------------------------------------------------
translate_text (00)                 | ██████                                   | 1.000s
lemmatization (00)                  | ██████████████████                       | 2.978s
the_cut (00)                        |                           █              | 0.012s
html_generation (00)                |                            █             | 0.004s
html_generation (03)                |                               █          | 0.003s
html_generation (02)                |                                    █     | 0.002s
html_generation (01)                |                                        █ | 0.003s

```

## [Commit: da3461a] 20260808033732 (Avg wait between runs: 1.68s)
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 10.971s)
---------------------------------------------------------------------------
lemmatization (00)                  | ████████████████                         | 4.639s
translate_text (00)                 | ███                                      | 0.990s
the_cut (00)                        |                 █                        | 0.028s
html_generation (00)                |                   █                      | 0.006s
html_generation (08)                |                    █                     | 0.003s
html_generation (07)                |                      █                   | 0.003s
html_generation (06)                |                        █                 | 0.002s
html_generation (05)                |                          █               | 0.002s
html_generation (04)                |                             █            | 0.003s
html_generation (03)                |                               █          | 0.003s
html_generation (02)                |                                    █     | 0.003s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 5.443s)
---------------------------------------------------------------------------
translate_text (00)                 | ███████                                  | 1.026s
lemmatization (00)                  | ██████████████████                       | 2.563s
the_cut (00)                        |                          █               | 0.011s
html_generation (00)                |                           █              | 0.004s
html_generation (03)                |                              █           | 0.003s
html_generation (02)                |                                    █     | 0.003s
html_generation (01)                |                                        █ | 0.002s

```

## [Commit: d2d36c1] 20260808034503 (Avg wait between runs: 2.82s)
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 9.580s)
---------------------------------------------------------------------------
translate_text (00)                 | ████                                     | 1.057s
lemmatization (00)                  | ████████████████████                     | 4.796s
the_cut (00)                        |                     █                    | 0.028s
html_generation (00)                |                      █                   | 0.007s
html_generation (08)                |                       █                  | 0.004s
html_generation (07)                |                          █               | 0.002s
html_generation (06)                |                            █             | 0.002s
html_generation (05)                |                              █           | 0.002s
html_generation (04)                |                                 █        | 0.002s
html_generation (03)                |                                   █      | 0.003s
html_generation (02)                |                                      █   | 0.003s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 10.989s)
---------------------------------------------------------------------------
translate_text (00)                 | █████████████████████████████            | 8.031s
lemmatization (00)                  | █████████                                | 2.606s
the_cut (00)                        |                                 █        | 0.017s
html_generation (00)                |                                  █       | 0.006s
html_generation (03)                |                                    █     | 0.004s
html_generation (02)                |                                      █   | 0.002s
html_generation (01)                |                                        █ | 0.003s

```

## [Commit: 661df45] 20260808034829 (Avg wait between runs: 2.88s)
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 12.371s)
---------------------------------------------------------------------------
translate_text (00)                 | ███                                      | 0.935s
lemmatization (00)                  | ███████████████████                      | 5.938s
the_cut (00)                        |                    █                     | 0.035s
html_generation (00)                |                     █                    | 0.008s
html_generation (08)                |                      █                   | 0.003s
html_generation (07)                |                         █                | 0.002s
html_generation (06)                |                           █              | 0.002s
html_generation (05)                |                             █            | 0.003s
html_generation (04)                |                                 █        | 0.004s
html_generation (03)                |                                    █     | 0.004s
html_generation (02)                |                                      █   | 0.003s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 5.840s)
---------------------------------------------------------------------------
translate_text (00)                 | ███████                                  | 1.030s
lemmatization (00)                  | ██████████████████                       | 2.728s
the_cut (00)                        |                          █               | 0.013s
html_generation (00)                |                          █               | 0.003s
html_generation (03)                |                              █           | 0.003s
html_generation (02)                |                                    █     | 0.003s
html_generation (01)                |                                        █ | 0.002s

```

## [Commit: 7878d05] 20260808035015 (Avg wait between runs: 8.26s)
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 12.371s)
---------------------------------------------------------------------------
translate_text (00)                 | ███                                      | 1.090s
lemmatization (00)                  | ███████████████████                      | 6.081s
the_cut (00)                        |                    █                     | 0.039s
html_generation (00)                |                      █                   | 0.006s
html_generation (08)                |                       █                  | 0.003s
html_generation (07)                |                         █                | 0.002s
html_generation (06)                |                            █             | 0.002s
html_generation (05)                |                              █           | 0.002s
html_generation (04)                |                                 █        | 0.003s
html_generation (03)                |                                   █      | 0.004s
html_generation (02)                |                                      █   | 0.003s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 7.437s)
---------------------------------------------------------------------------
lemmatization (00)                  | █████████████████████                    | 4.031s
translate_text (00)                 | █████                                    | 1.069s
the_cut (00)                        |                            █             | 0.014s
html_generation (00)                |                             █            | 0.004s
html_generation (03)                |                                █         | 0.004s
html_generation (02)                |                                     █    | 0.002s
html_generation (01)                |                                        █ | 0.003s

```

## [Commit: e4551fb] 20260808035500 (Avg wait between runs: 8.16s)
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 9.294s)
---------------------------------------------------------------------------
translate_text (00)                 | ████                                     | 1.049s
lemmatization (00)                  | ██████████████████                       | 4.379s
the_cut (00)                        |                   █                      | 0.029s
html_generation (00)                |                     █                    | 0.008s
html_generation (08)                |                      █                   | 0.003s
html_generation (07)                |                         █                | 0.002s
html_generation (06)                |                           █              | 0.002s
html_generation (05)                |                              █           | 0.002s
html_generation (04)                |                                 █        | 0.002s
html_generation (03)                |                                   █      | 0.003s
html_generation (02)                |                                      █   | 0.003s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 5.599s)
---------------------------------------------------------------------------
translate_text (00)                 | ██████                                   | 0.925s
lemmatization (00)                  | ████████████████████                     | 2.865s
the_cut (00)                        |                           █              | 0.011s
html_generation (00)                |                            █             | 0.004s
html_generation (03)                |                                █         | 0.003s
html_generation (02)                |                                    █     | 0.002s
html_generation (01)                |                                        █ | 0.002s

```

## [Commit: e44fd4c] 20260808040718 (Avg wait between runs: 12.61s)
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 9.384s)
---------------------------------------------------------------------------
translate_text (00)                 | █████                                    | 1.231s
lemmatization (00)                  | ██████████████████                       | 4.338s
the_cut (00)                        |                   █                      | 0.029s
html_generation (00)                |                    █                     | 0.006s
html_generation (08)                |                      █                   | 0.003s
html_generation (07)                |                         █                | 0.003s
html_generation (06)                |                           █              | 0.002s
html_generation (05)                |                              █           | 0.002s
html_generation (04)                |                                 █        | 0.002s
html_generation (03)                |                                   █      | 0.003s
html_generation (02)                |                                      █   | 0.003s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 5.464s)
---------------------------------------------------------------------------
translate_text (00)                 | ███████                                  | 0.974s
lemmatization (00)                  | ██████████████████                       | 2.558s
the_cut (00)                        |                           █              | 0.012s
html_generation (00)                |                            █             | 0.004s
html_generation (03)                |                               █          | 0.003s
html_generation (02)                |                                    █     | 0.002s
html_generation (01)                |                                        █ | 0.002s

```

## [Commit: 695a336] 20260808102415 (Avg wait between runs: 12.40s)
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 8.904s)
---------------------------------------------------------------------------
translate_text (00)                 | ████                                     | 1.044s
lemmatization (00)                  | ██████████████████                       | 4.194s
the_cut (00)                        |                   █                      | 0.026s
html_generation (00)                |                     █                    | 0.006s
html_generation (08)                |                      █                   | 0.003s
html_generation (07)                |                        █                 | 0.002s
html_generation (06)                |                           █              | 0.002s
html_generation (05)                |                              █           | 0.002s
html_generation (04)                |                                █         | 0.002s
html_generation (03)                |                                   █      | 0.003s
html_generation (02)                |                                      █   | 0.003s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 5.169s)
---------------------------------------------------------------------------
translate_text (00)                 | ████████                                 | 1.096s
lemmatization (00)                  | ███████████████████                      | 2.533s
the_cut (00)                        |                            █             | 0.010s
html_generation (00)                |                            █             | 0.004s
html_generation (03)                |                                █         | 0.003s
html_generation (02)                |                                    █     | 0.003s
html_generation (01)                |                                        █ | 0.002s

```

## [Commit: e442fbe] 20260808112401 (Avg wait between runs: 12.85s)
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 9.245s)
---------------------------------------------------------------------------
lemmatization (00)                  | ██████████████████                       | 4.320s
translate_text (00)                 | ████                                     | 1.021s
the_cut (00)                        |                   █                      | 0.027s
html_generation (00)                |                    █                     | 0.006s
html_generation (08)                |                     █                    | 0.003s
html_generation (07)                |                        █                 | 0.002s
html_generation (06)                |                           █              | 0.002s
html_generation (05)                |                              █           | 0.002s
html_generation (04)                |                                █         | 0.003s
html_generation (03)                |                                   █      | 0.003s
html_generation (02)                |                                      █   | 0.003s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 5.285s)
---------------------------------------------------------------------------
lemmatization (00)                  | ██████████████████                       | 2.496s
translate_text (00)                 | ███████                                  | 1.028s
the_cut (00)                        |                            █             | 0.009s
html_generation (00)                |                            █             | 0.004s
html_generation (03)                |                                █         | 0.003s
html_generation (02)                |                                    █     | 0.002s
html_generation (01)                |                                        █ | 0.003s

```

## [Commit: f2d0089] 20260808113932 (Avg wait between runs: 12.85s)
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 10.277s)
---------------------------------------------------------------------------
translate_text (00)                 | █████                                    | 1.485s
lemmatization (00)                  | █████████████████                        | 4.596s
the_cut (00)                        |                  █                       | 0.026s
html_generation (00)                |                    █                     | 0.006s
html_generation (08)                |                     █                    | 0.003s
html_generation (07)                |                        █                 | 0.002s
html_generation (06)                |                          █               | 0.002s
html_generation (05)                |                             █            | 0.002s
html_generation (04)                |                               █          | 0.002s
html_generation (03)                |                                  █       | 0.003s
html_generation (02)                |                                      █   | 0.004s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 6.424s)
---------------------------------------------------------------------------
translate_text (00)                 | ██████                                   | 1.085s
lemmatization (00)                  | ████████████████████                     | 3.242s
the_cut (00)                        |                           █              | 0.012s
html_generation (00)                |                            █             | 0.004s
html_generation (03)                |                               █          | 0.003s
html_generation (02)                |                                    █     | 0.002s
html_generation (01)                |                                        █ | 0.002s

```

## [Commit: feebde2] 20260808120810 (Avg wait between runs: 12.53s)
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 9.187s)
---------------------------------------------------------------------------
translate_text (00)                 | ████                                     | 1.082s
lemmatization (00)                  | ███████████████████                      | 4.416s
the_cut (00)                        |                    █                     | 0.028s
html_generation (00)                |                     █                    | 0.007s
html_generation (08)                |                      █                   | 0.003s
html_generation (07)                |                         █                | 0.003s
html_generation (06)                |                           █              | 0.002s
html_generation (05)                |                              █           | 0.002s
html_generation (04)                |                                █         | 0.002s
html_generation (03)                |                                   █      | 0.003s
html_generation (02)                |                                      █   | 0.004s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 5.490s)
---------------------------------------------------------------------------
translate_text (00)                 | ████████                                 | 1.100s
lemmatization (00)                  | ███████████████████                      | 2.627s
the_cut (00)                        |                            █             | 0.010s
html_generation (00)                |                             █            | 0.003s
html_generation (03)                |                                █         | 0.003s
html_generation (02)                |                                    █     | 0.002s
html_generation (01)                |                                        █ | 0.002s

```

## [Commit: 7dfaef7] 20260808124319 (Avg wait between runs: -85.02s)
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 108.703s)
---------------------------------------------------------------------------
background_text_translation (00)    | █                                        | 1.909s
lemmatization (00)                  |                                    █     | 4.977s
translate_text (00)                 |                                    █     | 1.529s
the_cut (00)                        |                                      █   | 0.030s
html_generation (00)                |                                      █   | 0.007s
html_generation (08)                |                                      █   | 0.003s
html_generation (07)                |                                       █  | 0.003s
html_generation (06)                |                                       █  | 0.003s
html_generation (05)                |                                       █  | 0.003s
html_generation (04)                |                                        █ | 0.002s
html_generation (03)                |                                        █ | 0.004s
html_generation (02)                |                                        █ | 0.003s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 5.728s)
---------------------------------------------------------------------------
translate_text (00)                 | ███████                                  | 1.028s
lemmatization (00)                  | ██████████████████                       | 2.696s
the_cut (00)                        |                          █               | 0.012s
html_generation (00)                |                           █              | 0.003s
html_generation (03)                |                               █          | 0.003s
html_generation (02)                |                                    █     | 0.003s
html_generation (01)                |                                        █ | 0.003s

```

## [Commit: c01cea3] 20260808140351 (Avg wait between runs: 40.41s)
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 11.645s)
---------------------------------------------------------------------------
lemmatization (00)                  | ████████████████                         | 4.862s
translate_text (00)                 | ███                                      | 1.041s
the_cut (00)                        |                 █                        | 0.032s
html_generation (00)                |                  █                       | 0.007s
html_generation (08)                |                    █                     | 0.003s
html_generation (07)                |                       █                  | 0.003s
html_generation (06)                |                         █                | 0.002s
html_generation (05)                |                             █            | 0.004s
html_generation (04)                |                               █          | 0.004s
html_generation (03)                |                                  █       | 0.003s
html_generation (02)                |                                     █    | 0.004s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 7.069s)
---------------------------------------------------------------------------
lemmatization (00)                  | ████████████████                         | 3.000s
translate_text (00)                 | ███████                                  | 1.394s
the_cut (00)                        |                         █                | 0.013s
html_generation (00)                |                          █               | 0.004s
html_generation (03)                |                              █           | 0.006s
html_generation (02)                |                                    █     | 0.003s
html_generation (01)                |                                        █ | 0.003s

```

## [Commit: c9c1f35] 20260808190717 to desk (Avg wait between runs: 11.09s)
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 6.618s)
---------------------------------------------------------------------------
lemmatization (00)                  | ██████████████████████████               | 4.418s
html_generation (00)                |                            █             | 0.006s
background_text_translation (00)    |                               █████████  | 1.649s
translate_text (00)                 |                                    ████  | 0.705s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 4.378s)
---------------------------------------------------------------------------
lemmatization (00)                  | ██████████████████████████               | 2.869s
html_generation (00)                |                           █              | 0.004s
background_text_translation (00)    |                               █████████  | 1.058s
translate_text (00)                 |                               █████████  | 1.029s

```

## [Commit: cd6a550] 20260808191432 (Avg wait between runs: 12.56s)
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 9.207s)
---------------------------------------------------------------------------
translate_text (00)                 | ████                                     | 1.130s
lemmatization (00)                  | ██████████████████                       | 4.147s
the_cut (00)                        |                   █                      | 0.033s
html_generation (00)                |                    █                     | 0.006s
html_generation (08)                |                     █                    | 0.003s
html_generation (07)                |                        █                 | 0.002s
html_generation (06)                |                          █               | 0.002s
html_generation (05)                |                             █            | 0.002s
html_generation (04)                |                                █         | 0.002s
html_generation (03)                |                                   █      | 0.003s
html_generation (02)                |                                      █   | 0.003s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 5.567s)
---------------------------------------------------------------------------
lemmatization (00)                  | ██████████████████                       | 2.572s
translate_text (00)                 | █████████                                | 1.255s
the_cut (00)                        |                           █              | 0.014s
html_generation (00)                |                            █             | 0.004s
html_generation (03)                |                               █          | 0.003s
html_generation (02)                |                                    █     | 0.003s
html_generation (01)                |                                        █ | 0.002s

```

## [Commit: e4cd385] 20260808191717 (Avg wait between runs: 12.74s)
```text
Run Session: 20260807190200 [Golden DE] (Total Batch E2E Duration: 9.094s)
---------------------------------------------------------------------------
translate_text (00)                 | █████                                    | 1.203s
lemmatization (00)                  | ██████████████████                       | 4.208s
the_cut (00)                        |                   █                      | 0.028s
html_generation (00)                |                    █                     | 0.006s
html_generation (08)                |                      █                   | 0.003s
html_generation (07)                |                        █                 | 0.002s
html_generation (06)                |                           █              | 0.002s
html_generation (05)                |                              █           | 0.003s
html_generation (04)                |                                █         | 0.002s
html_generation (03)                |                                   █      | 0.003s
html_generation (02)                |                                      █   | 0.003s
html_generation (01)                |                                        █ | 0.003s

Run Session: 20260807190100 [Golden EN] (Total Batch E2E Duration: 5.418s)
---------------------------------------------------------------------------
translate_text (00)                 | ████████                                 | 1.183s
lemmatization (00)                  | ███████████████████                      | 2.582s
the_cut (00)                        |                            █             | 0.011s
html_generation (00)                |                            █             | 0.004s
html_generation (03)                |                               █          | 0.003s
html_generation (02)                |                                    █     | 0.003s
html_generation (01)                |                                        █ | 0.003s

```

## Golden Run Aggregates
| Phase | Cnt | Min (s) | Avg (s) | Max (s) |
| :--- | :---: | :---: | :---: | :---: |
| `lemmatization` | 78 | 2.493 | 3.916 | 10.907 |
| `background_text_translation` | 44 | 0.573 | 3.103 | 13.441 |
| `translate_text` | 82 | 0.705 | 1.166 | 8.031 |
| `the_cut` | 73 | 0.009 | 0.020 | 0.041 |
| `html_generation` | 444 | 0.002 | 0.003 | 0.013 |

## Phase Glossary
- **`translate_text`**: (Network IO-Bound) Holistically translating the source paragraph/sentence via external APIs (e.g. DeepL).
- **`lemmatization`**: (CPU-Bound) Tokenizing text and executing morphological/Anki lookups via Kardenwort Core to generate the data grid.
- **`the_cut`**: (CPU-Bound) Slicing the master TSV into individual child sentence files during Multi-mode runs.
- **`background_text_translation`**: The progressive worker updating BOTH the text translation AND the individual base lemma translations asynchronously without blocking the UI.
