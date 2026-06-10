# Checkpoint / Resume Benchmark

## Goal

Checkpoint/resume lets Pure save task state, later rebuild enough runtime context, and detect when the saved state no longer matches the workspace or runtime that is trying to continue it.

## Key Concepts

- create_checkpoint = save an archive of task state, memory snapshot, key file freshness, workspace hash, runtime metadata, and runtime identity.
- resume = load a saved session/checkpoint and evaluate whether it is still valid before continuing.
- workspace hash / fingerprint = a deterministic summary of the fixture workspace context used to detect drift.
- runtime identity = execution metadata such as cwd, model/model client, approval policy, feature flags, tool signature, and workspace fingerprint.
- partial stale = key file freshness changed, so file-specific memory/checkpoint facts may need re-anchoring.
- workspace mismatch = runtime identity or workspace fingerprint differs from the saved checkpoint.
- context reduction checkpoint = a checkpoint created because prompt budget reductions occurred before model completion.

## Results

| Metric | Value |
| --- | ---: |
| case_count | 10 |
| resume_status_accuracy | 100.00% |
| mismatch_detection_rate | 100.00% |
| runtime_identity_detection_rate | 100.00% |
| schema_mismatch_detection_rate | 100.00% |
| context_reduction_checkpoint_hit_rate | 100.00% |
| checkpoint_event_hit_rate | 100.00% |
| false_accept_count | 0 |
| false_reject_count | 0 |

## Case Analysis

| Case | Expected Status | Actual Status | Trigger | Passed |
| --- | --- | --- | --- | ---: |
| clean_resume | full-valid | full-valid | run_finished | true |
| partial_stale_single_file_change | partial-stale | partial-stale | freshness_mismatch | true |
| workspace_mismatch_many_files | workspace-mismatch | workspace-mismatch | workspace_mismatch | true |
| workspace_mismatch_repo_root_changed | workspace-mismatch | workspace-mismatch | workspace_mismatch | true |
| context_reduction_checkpoint | full-valid | full-valid | context_reduction | true |

## False Accept / False Reject

No false accept or false reject cases were recorded in this run.

## Limitations

- This is not production-grade transactional recovery.
- It cannot guarantee that all file semantics are unchanged.
- It is only a recovery guard based on the current project's metadata and workspace state.
- It is not equivalent to distributed task recovery.

## Resume Bullet Candidate

- Built a reproducible offline Checkpoint/Resume benchmark for Pure that measures resume status accuracy, workspace/runtime identity mismatch detection, schema mismatch detection, and checkpoint trace event coverage from FakeModelClient runs.

## Reproduction Command

```bash
python benchmarks/pure/checkpoint_resume/run_checkpoint_resume_benchmark.py
```
