# Context Compression Benchmark

## Benchmark Goal

This benchmark measures how Pure's current context governance changes the final prompt character length sent to the model, while checking that the current request remains intact and the scripted task can still finish.

## Method

Baseline uses the same fixed case inputs with context reduction disabled or with the closest available no-clipping configuration. Reduced uses Pure's current context reduction and section budget controls. Both variants use FakeModelClient and the same mock outputs for each case.

## Formula

`compression_rate = (baseline_prompt_chars - reduced_prompt_chars) / baseline_prompt_chars`

## Why chars instead of tokens

Character count is a provider-neutral proxy available from the current Pure prompt metadata. It is not a tokenizer result and must not be described as token usage or token cost.

## Results

| Metric | Value |
| --- | ---: |
| case_count | 12 |
| avg_baseline_prompt_chars | 6661.33 |
| avg_reduced_prompt_chars | 2213.83 |
| avg_compression_rate | 65.10% |
| p50_compression_rate | 87.34% |
| p90_compression_rate | 91.24% |
| max_compression_rate | 91.46% |
| min_compression_rate | -1.38% |
| negative_compression_case_count | 1 |

## Section Breakdown

| Section | Reduction Chars |
| --- | ---: |
| prefix_reduction_chars | 48105 |
| memory_reduction_chars | 727 |
| knowledge_reduction_chars | 1341 |
| relevant_memory_reduction_chars | 3 |
| history_reduction_chars | 3194 |
| tool_observation_reduction_chars | 872 |

## Correctness Checks

| Check | Rate |
| --- | ---: |
| current_request_preserved_rate | 100.00% |
| verifier_pass_rate | 100.00% |
| normal_final_rate | 100.00% |

## Failed / Risky Cases

| Case | Missing Context | Notes |
| --- | --- | --- |
| - | - | none |

## What Can Be Claimed

- Pure has a reproducible offline benchmark for prompt character length under context budget pressure.
- The benchmark reports section-level character reductions and correctness checks from real run artifacts.
- Results are specific to FakeModelClient/scripted cases and current repository code.

## What Cannot Be Claimed

- It cannot claim token cost reduction.
- It cannot claim real model capability improvement.
- It cannot claim production-grade context compression.
- It cannot judge compression rate without checking correctness.

## Reproduction Command

```bash
python benchmarks/pure/context_compression/run_context_compression_benchmark.py
```
