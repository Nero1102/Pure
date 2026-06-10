# Pure Benchmark Results

## 1. Run Environment

| Field | Value |
|---|---|
| Timestamp | `2026-06-08T23:28:32.894216+00:00` |
| Git commit | `1bdf1b205f90edac6adccaa08c9c410572804d3b` |
| Python version | `3.12.10` |
| Command | `D:\Users\YM\AppData\Local\Programs\Python\Python312\python.exe scripts/run_pure_benchmarks.py --all --output benchmarks/pure/_runs/latest` |
| Model mode | `FakeModelClient` / mock outputs |
| Real LLM used | `false` |

Warning: no real LLM was used. These results measure Pure runtime behavior under deterministic mock outputs. They do not measure real model capability.

Unified run artifacts:

- `benchmarks/pure/_runs/latest/summary.json`
- `benchmarks/pure/_runs/latest/summary.md`
- `benchmarks/pure/_runs/latest/<suite>/results.json`
- `benchmarks/pure/_runs/latest/<suite>/per_case_results.jsonl`
- `benchmarks/pure/_runs/latest/<suite>/summary.md`

## 2. Summary Table

| Suite | Cases | Core Metrics | Failed Cases |
|---|---:|---|---:|
| Context Compression | 12 | avg baseline chars 7162.33; avg reduced chars 2339.08; avg compression 65.79%; verifier pass 100.00%; current request preserved 100.00%; normal final 100.00% | 0 |
| Tool Loop / Repetition Guard | 12 cases / 36 variants | off repeated calls 10; warn repeated calls 10; block repeated calls 0; block repeated-call reduction 100.00%; block rejected repeated calls 10; trace event hit 100.00% | 0 |
| Tool Governance | 15 | policy pass 100.00%; unsafe rejection 100.00%; safe allow 100.00%; workspace escape block 100.00%; readonly block 100.00%; risky audit coverage 100.00% | 0 |
| Checkpoint / Resume | 10 | resume status accuracy 100.00%; mismatch detection 100.00%; runtime identity detection 100.00%; checkpoint event hit 100.00%; false accept 0; false reject 0 | 0 |
| Evaluator Regression | 13 | case pass 84.62%; expected trace event hit 100.00%; forbidden tool violations 1; step budget met 100.00%; tool rejections 3; security events 2 | 2 |

No suite failed to run. The Evaluator Regression suite preserves failing cases instead of deleting or weakening them.

## 3. Context Compression Results

Formula:

```text
compression_rate = (baseline_prompt_chars - reduced_prompt_chars) / baseline_prompt_chars
```

Metrics from `benchmarks/pure/_runs/latest/context_compression/results.json`:

| Metric | Value |
|---|---:|
| case_count | 12 |
| avg_baseline_prompt_chars | 7162.33 |
| avg_reduced_prompt_chars | 2339.08 |
| avg_compression_rate | 65.79% |
| p50_compression_rate | 88.22% |
| p90_compression_rate | 91.86% |
| max_compression_rate | 92.02% |
| min_compression_rate | -1.27% |
| verifier_pass_rate | 100.00% |
| current_request_preserved_rate | 100.00% |
| normal_final_rate | 100.00% |
| failed_cases | 0 |

Section reduction breakdown:

| Section | Reduced Chars |
|---|---:|
| prefix_reduction_chars | 52614 |
| history_reduction_chars | 3194 |
| knowledge_reduction_chars | 1341 |
| tool_observation_reduction_chars | 872 |
| memory_reduction_chars | 727 |
| relevant_memory_reduction_chars | 3 |

The largest measured contribution was `prefix_reduction_chars`. In the current benchmark fixture this means static prompt/prefix material dominates the character reduction more than history, knowledge, memory, or tool-observation sections.

Characters are used as a cross-model proxy because tokenizers differ by provider and model. Character reduction is not token reduction and must not be presented as token cost reduction unless token counts are actually measured.

No context compression case failed after reduction in this run. One case had negative compression (`negative_compression_allowed_case`, -1.27%), and it is kept in the benchmark to avoid hiding weak or non-improving behavior.

Resume claim status: this can be written conservatively as an offline runtime benchmark measuring prompt character reduction with verifier/current-request/final-status checks. Do not claim token-cost savings from this result.

## 4. Tool Loop Results

Metrics from `benchmarks/pure/_runs/latest/tool_loop/results.json`:

| Guard Mode | repeated_tool_call_count | blocked_repeated_call_count | avg_repeated_call_reduction_rate | total_step_saving_estimate | Trace Events |
|---|---:|---:|---:|---:|---:|
| off | 10 | 0 | 0.00% | 0 | 0 detections / 0 rejections |
| warn | 10 | 0 | 0.00% | 0 | 10 detections / 0 rejections |
| block | 0 | 10 | 100.00% | 0 | 0 detections / 10 rejections |

Additional metrics:

| Metric | Value |
|---|---:|
| case_count | 12 |
| variant_count | 36 |
| event_hit_rate | 100.00% |
| block blocked_call_rate | 29.41% |
| failed_cases | 0 |

The repetition guard detects repeated calls by comparing the same tool name and normalized arguments inside a short recent-call window. Path normalization treats equivalent path spellings such as `pure`, `.\pure`, and `pure/` as the same logical argument, and non-semantic arguments such as timeout/limit/max_lines are ignored for repeat detection.

Warn mode records `repeated_tool_call_detected` and lets execution continue. Block mode rejects the repeated tool execution and records `tool_rejected_repeated_call`. In this run block mode reduced repeated executed calls from 10 to 0, but the estimated step saving was 0 because the fixed mock output sequences still consumed the same step budget shape.

Resume claim status: this can be written as a runtime benchmark for short-window repeated tool-call detection and blocking. Do not claim it solves all agent loops or replaces planning.

## 5. Tool Governance Results

Metrics from `benchmarks/pure/_runs/latest/tool_governance/results.json`:

| Metric | Value |
|---|---:|
| case_count | 15 |
| policy_pass_rate | 100.00% |
| unsafe_rejection_rate | 100.00% |
| safe_allow_rate | 100.00% |
| workspace_escape_block_rate | 100.00% |
| readonly_block_rate | 100.00% |
| invalid_args_rejection_rate | 100.00% |
| risky_tool_audit_coverage | 100.00% |
| trace_audit_coverage | 100.00% |
| false_allow_count | 0 |
| false_reject_count | 0 |
| failed_cases | 0 |

ToolGateway blocked readonly writes, readonly patches, readonly shell execution, parent path escape, absolute path escape, unknown tool names, missing required arguments, and wrong argument types. Safe read/list/search cases were allowed under readonly mode.

Readonly mode allows safe inspection tools and rejects workspace-changing or shell tools. Risky write/patch cases run under an allowing policy recorded workspace diff fields, including `workspace_changed` and `affected_paths`.

Resume claim status: this can be written as a local ToolGateway governance benchmark for policy, path-boundary, argument-validation, and audit-diff behavior. Do not claim production-grade sandboxing or enterprise security.

## 6. Checkpoint / Resume Results

Metrics from `benchmarks/pure/_runs/latest/checkpoint_resume/results.json`:

| Metric | Value |
|---|---:|
| case_count | 10 |
| resume_status_accuracy | 100.00% |
| mismatch_detection_rate | 100.00% |
| runtime_identity_detection_rate | 100.00% |
| schema_mismatch_detection_rate | 100.00% |
| context_reduction_checkpoint_hit_rate | 100.00% |
| checkpoint_event_hit_rate | 100.00% |
| false_accept_count | 0 |
| false_reject_count | 0 |
| failed_cases | 0 |

Clean resume passed as expected. Partial stale state was identified for single-file changes. Workspace mismatch was identified for broader workspace changes and workspace identity changes. Schema mismatch and runtime identity mismatch cases were also identified according to the current Pure status model. Context reduction produced the expected checkpoint event.

Resume claim status: this can be written as a checkpoint/resume benchmark for recovery-state recognition, workspace mismatch detection, runtime identity checks, and checkpoint trace events. Do not claim distributed transaction recovery or complete semantic consistency of every file.

## 7. Evaluator Regression Results

Metrics from `benchmarks/pure/_runs/latest/evaluator_regression/results.json`:

| Metric | Value |
|---|---:|
| case_count | 13 |
| case_pass_rate | 84.62% |
| expected_trace_event_hit_rate | 100.00% |
| expected_tool_hit_rate | 100.00% |
| forbidden_tool_violation_count | 1 |
| step_budget_met_rate | 100.00% |
| repeated_tool_call_count | 2 |
| tool_rejection_count | 3 |
| security_event_count | 2 |
| checkpoint_created_count | 24 |
| avg_steps | 0.85 |
| p95_steps | 2.00 |

Failure reason counts:

| Failure Reason | Count |
|---|---:|
| forbidden tools used: run_shell | 1 |
| run status was not completed | 1 |

Evaluator Regression measures runtime behavior, trace events, tool policy, success keywords, step budget, and failure reasons under FakeModelClient/mock outputs. It does not measure model intelligence or real coding ability.

Failed cases:

- `forbidden_tool_guard_violation`: `forbidden tools used: run_shell`
- `step_budget_case`: `run status was not completed`

Resume claim status: this can be written as an offline evaluator regression benchmark for runtime contracts and trace/report artifacts. Do not present the 84.62% pass rate as a real model success rate.

## 8. Failed Cases

Suite-level failures: none.

Case-level failures:

| Suite | Case | Failure Reason |
|---|---|---|
| Evaluator Regression | `forbidden_tool_guard_violation` | `forbidden tools used: run_shell` |
| Evaluator Regression | `step_budget_case` | `run status was not completed` |

These failures do not indicate a benchmark runner failure. They are preserved case results from the evaluator regression suite.

## 9. What Can Be Claimed

- Can claim Pure has a reproducible benchmark suite covering context compression, tool loop / repetition guard, ToolGateway governance, checkpoint/resume, and runtime evaluator regression.
- Can claim this run covered 86 offline case/variant rows across the five suites: 12 context cases, 36 tool-loop variants, 15 governance cases, 10 checkpoint/resume cases, and 13 evaluator regression cases. The suites report their own denominators, so avoid merging these into one pass-rate.
- Can claim the benchmark uses FakeModelClient/mock outputs to verify runtime behavior without real LLM calls.
- Can claim specific metrics from this run, with context, such as 65.79% average prompt character compression with 100.00% verifier pass rate, 10 repeated calls blocked in Tool Loop block mode, 100.00% ToolGateway policy pass rate over 15 offline cases, and 100.00% checkpoint/resume status accuracy over 10 offline cases.

## 10. What Cannot Be Claimed

- Cannot claim real model success rate.
- Cannot claim SWE-bench performance.
- Cannot claim token cost reduction unless tokens are actually measured.
- Cannot claim production-grade security.
- Cannot cite external project data as Pure data.

## 11. Reproduction Commands

```bash
pytest tests/test_context_compression_benchmark.py -q
pytest tests/test_tool_loop_benchmark.py -q
pytest tests/test_tool_governance_benchmark.py -q
pytest tests/test_checkpoint_resume_benchmark.py -q
pytest tests/test_evaluator_regression_benchmark.py -q
pytest tests/test_run_pure_benchmarks.py -q
pytest tests/test_runtime.py tests/test_platform_evaluator.py -q
python scripts/run_pure_benchmarks.py --all --output benchmarks/pure/_runs/latest
pytest
```
