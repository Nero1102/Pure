# Tool Governance Benchmark

## Goal

ToolGateway is the boundary between model intent and the local workspace. This benchmark measures whether the current Pure code records and enforces basic tool governance decisions before a model-directed action reaches files or shell execution.

## Case Categories

- safe tools: readonly read_file, list_files, and search calls that should be allowed.
- risky tools: write_file, patch_file, and run_shell calls that require policy and audit handling.
- approval policy: readonly mode blocks write, patch, and shell execution.
- workspace escape: parent traversal and absolute paths outside the fixture workspace are rejected.
- invalid args: unknown tools, missing required arguments, and wrong argument types are rejected.
- audit diff: allowed risky tools report workspace_changed and affected_paths from actual fixture diffs.

## Results

| Metric | Value |
| --- | ---: |
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

## Security Events

| Error Code | Security Event | Count |
| --- | --- | ---: |
| invalid_arguments | - | 2 |
| invalid_arguments | path_escape | 3 |
| readonly_block | read_only_block | 3 |
| unknown_tool | - | 1 |

## Risky Tool Audit

workspace_changed is derived from before/after snapshots of the fixture workspace. affected_paths lists changed relative paths inside that fixture only.

| Case | Tool | workspace_changed | affected_paths |
| --- | --- | ---: | --- |
| risky_write_audit_diff | write_file | true | created.txt |
| risky_patch_audit_diff | patch_file | true | patch_target.txt |
| shell_no_workspace_change | run_shell | false | - |

## False Allow / False Reject

No false allow or false reject cases were recorded in this run.

## Limitations

- This is not a production-grade sandbox.
- It cannot defend against every command risk.
- It only measures local workspace boundary checks and policy governance in offline fixture cases.
- It does not represent an enterprise security system.

## Resume Bullet Candidate

- Built a reproducible offline ToolGateway governance benchmark for Pure that measures readonly policy rejection, workspace escape blocking, invalid-argument rejection, and risky-tool audit coverage from FakeModelClient runtime traces.

## Reproduction Command

```bash
python benchmarks/pure/tool_governance/run_tool_governance_benchmark.py
```
