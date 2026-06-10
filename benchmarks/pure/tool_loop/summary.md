# Tool Loop / Repetition Guard Benchmark

## Goal

ReAct-style agents can waste steps by repeatedly calling the same tool with the same arguments while exploring a workspace. This benchmark measures Pure's short-window Tool Repetition Guard on scripted offline tool loops.

## Method

Each case runs the same FakeModelClient mock_outputs under three guard configurations: off, warn, and block. Guard off records how many duplicate tool executions actually happened. Warn emits an advisory trace event and still executes the tool. Block emits a rejection trace event and returns a repeated-call error without executing the duplicate tool handler.

## Normalized Args

The guard sorts dict keys, normalizes path-like arguments relative to the workspace, collapses slash and Windows-style path variants, and ignores non-semantic keys such as timeout, limit, display_limit, and max_lines.

## Results

| Guard Mode | Cases | Tool Calls | Executed Calls | Repeated Executed Calls | Blocked Repeats | Warn Events | Block Events | Blocked Call Rate | Avg Reduction | Step Saving Estimate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| off | 12 | 34 | 34 | 10 | 0 | 0 | 0 | 0.00% | 0.00% | 0 |
| warn | 12 | 34 | 34 | 10 | 0 | 10 | 0 | 0.00% | 0.00% | 0 |
| block | 12 | 34 | 24 | 0 | 10 | 0 | 10 | 29.41% | 100.00% | 0 |

## Trace Events

- repeated_tool_call_detected is emitted in warn mode when a matching recent tool call is found.
- tool_rejected_repeated_call is emitted in block mode before returning a repeated-call error observation.

## Case Analysis

| Case | Off Repeats | Warn Events | Blocked Repeats | Block Reduction | Normalized Args Samples |
| --- | ---: | ---: | ---: | ---: | --- |
| repeated_list_files_same_path | 1 | 1 | 1 | 100.00% | {"path":"pure"} |
| repeated_list_files_windows_path | 1 | 1 | 1 | 100.00% | {"path":"pure"} |
| ignored_non_semantic_args | 1 | 1 | 1 | 100.00% | {"path":"pure"} |
| window_expiry_allows_repeat | 0 | 0 | 0 | - | - |
| mixed_tool_sequence | 2 | 2 | 2 | 100.00% | {"path":"pure"}, {"path":".","pattern":"PureRuntime"} |

## Limitations

- This only addresses short-window repeated tool calls.
- It cannot replace a planner.
- It cannot solve loops where parameters differ but are semantically similar.
- It does not represent real model overall success rate.

## Resume Bullet Candidate

- Built a reproducible offline Tool Repetition Guard benchmark for Pure that compares off/warn/block behavior, normalized-argument detection, trace events, blocked duplicate executions, and step usage from FakeModelClient runs.

## Reproduction Command

```bash
python benchmarks/pure/tool_loop/run_tool_loop_benchmark.py
```
