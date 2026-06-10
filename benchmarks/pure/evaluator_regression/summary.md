# Evaluator Regression Benchmark

## Goal

This benchmark validates Pure runtime behavior and evaluator contracts with fixed offline cases. It measures trace events, tool use, guardrail outcomes, step budget behavior, and failure reasons; it does not measure real model capability.

## Cases

| Case | Source | Status | Steps | Passed |
| --- | --- | --- | ---: | ---: |
| dry_run_runtime_smoke | eval_cases.json | completed / final_answer_returned | 0 | true |
| dry_run_knowledge_smoke | eval_cases.json | completed / final_answer_returned | 0 | true |
| project_structure_analysis | eval_cases.json | completed / final_answer_returned | 2 | true |
| tool_policy_readonly | eval_cases.json | completed / final_answer_returned | 1 | true |
| knowledge_retrieval_context | eval_cases.json | completed / final_answer_returned | 0 | true |
| checkpoint_creation | eval_cases.json | completed / final_answer_returned | 1 | true |
| forbidden_tool_guard | eval_cases.json | completed / final_answer_returned | 0 | true |
| smoke_final_answer | evaluator_regression/cases.json | completed / final_answer_returned | 0 | true |
| forbidden_tool_guard_violation | evaluator_regression/cases.json | completed / final_answer_returned | 1 | false |
| repeated_tool_call_warn | evaluator_regression/cases.json | completed / final_answer_returned | 2 | true |
| repeated_tool_call_block | evaluator_regression/cases.json | completed / final_answer_returned | 2 | true |
| security_event_case | evaluator_regression/cases.json | completed / final_answer_returned | 1 | true |
| step_budget_case | evaluator_regression/cases.json | stopped / step_limit_reached | 1 | false |

## Results

| Metric | Value |
| --- | ---: |
| case_count | 13 |
| case_pass_rate | 84.62% |
| expected_tool_hit_rate | 100.00% |
| forbidden_tool_violation_count | 1 |
| expected_trace_event_hit_rate | 100.00% |
| success_keyword_hit_rate | 100.00% |
| step_budget_met_rate | 100.00% |
| repeated_tool_call_count | 2 |
| tool_rejection_count | 3 |
| security_event_count | 2 |
| checkpoint_created_count | 24 |
| avg_steps | 0.85 |
| p95_steps | 2.00 |

## Failure Reasons

| Reason | Count |
| --- | ---: |
| forbidden tools used: run_shell | 1 |
| run status was not completed | 1 |

## Trace Event Coverage

| Case | Expected Trace Hit | Hit Rate |
| --- | ---: | ---: |
| dry_run_runtime_smoke | true | 100.00% |
| dry_run_knowledge_smoke | true | 100.00% |
| project_structure_analysis | true | 100.00% |
| tool_policy_readonly | true | 100.00% |
| knowledge_retrieval_context | true | 100.00% |
| checkpoint_creation | true | 100.00% |
| forbidden_tool_guard | true | 100.00% |
| smoke_final_answer | true | 100.00% |
| forbidden_tool_guard_violation | true | 100.00% |
| repeated_tool_call_warn | true | 100.00% |
| repeated_tool_call_block | true | 100.00% |
| security_event_case | true | 100.00% |
| step_budget_case | true | 100.00% |

## Tool Policy Coverage

| Case | Forbidden Violations | Tool Rejections | Security Events |
| --- | --- | ---: | ---: |
| dry_run_runtime_smoke | - | 0 | 0 |
| dry_run_knowledge_smoke | - | 0 | 0 |
| project_structure_analysis | - | 0 | 0 |
| tool_policy_readonly | - | 1 | 1 |
| knowledge_retrieval_context | - | 0 | 0 |
| checkpoint_creation | - | 0 | 0 |
| forbidden_tool_guard | - | 0 | 0 |
| smoke_final_answer | - | 0 | 0 |
| forbidden_tool_guard_violation | run_shell | 0 | 0 |
| repeated_tool_call_warn | - | 0 | 0 |
| repeated_tool_call_block | - | 1 | 0 |
| security_event_case | - | 1 | 1 |
| step_budget_case | - | 0 | 0 |

## Limitations

- Uses FakeModelClient.
- Does not represent real model pass rate.
- Does not represent SWE-bench.
- Case count is limited.
- Better suited for validating runtime contracts than model intelligence.

## Resume Bullet Candidate

- Built a reproducible offline Evaluator Regression benchmark for Pure that reuses eval_cases.json plus benchmark-only cases to track runtime contracts, trace events, tool policy violations, repeated-call guard events, security events, step limits, and failure reasons from FakeModelClient runs.

## Reproduction Command

```bash
python benchmarks/pure/evaluator_regression/run_evaluator_regression_benchmark.py
```
