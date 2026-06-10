# Pure Benchmark Design

## Why Pure Needs Benchmarks

Pure is an Agent Runtime / Harness prototype. Its core claims are about runtime behavior: context governance, tool execution policy, traceability, checkpoint recovery, knowledge/evaluator integration, and repeatable offline regression checks. Those claims need reproducible benchmark cases because a manual demo can pass while the runtime contract is still weak or untested.

Benchmarks make the project falsifiable. A case can fail, a trace event can be missing, a guard can reject too much or too little, and the report should show that honestly. The goal is not to produce polished resume numbers; it is to turn Pure's runtime behavior into evidence that can be rerun from the current codebase.

## Why Demos Are Not Enough

Agent runtime projects are especially easy to overstate from demos. A single prompt may look good because the mock output was convenient, the workspace was clean, or the failure path was never exercised. Pure needs fixed cases, fixed mock outputs, structured per-case results, and summary metrics so regressions can be detected without changing expectations after the fact.

## Benchmark Suites

### Context Compression

Measures prompt character length before and after context reduction. It also checks that the current request is preserved, the verifier passes, and the final answer path remains normal. Compression is only useful if the reduced prompt still contains the request and the run can complete.

### Tool Loop / Repetition Guard

Measures short-window repeated tool calls under guard off, warn, and block modes. This is more appropriate for Pure than a broad success-rate benchmark because the feature is a runtime guard, not a planner or reasoning system. The benchmark checks repeated-call detection, rejected repeated calls, trace events, and step/tool-call usage.

### Tool Governance

Measures ToolGateway behavior as the boundary between a model and the local workspace. The suite covers safe read tools, risky write/shell tools, approval policy, workspace path escapes, invalid arguments, and risky tool audit diffs. It measures local policy and audit behavior, not production-grade sandboxing.

### Checkpoint / Resume

Measures checkpoint creation, resume status, workspace fingerprint mismatch, schema mismatch, runtime identity mismatch, and context-reduction checkpoint events. The target is recovery state recognition. It is not a distributed transaction system and does not prove semantic equivalence of every file after a resume.

### Evaluator Regression

Runs existing evaluator cases plus benchmark-only regression cases through FakeModelClient/mock outputs. It measures expected tools, forbidden tools, expected trace events, success keywords, step budget, failure reasons, and trace/report artifact generation. It is a runtime contract regression benchmark, not a real model capability evaluation.

## Why FakeModelClient

FakeModelClient keeps benchmark runs hermetic and deterministic. It prevents accidental real LLM calls, removes provider latency and availability from the measurements, and makes failures easier to attribute to runtime behavior rather than model variability. Because of that, the results must not be described as real-model success rates.

## Why Character Counts Are Used For Context Compression

Context compression uses characters as a cross-model proxy because tokenizers differ by model and provider. The benchmark formula is:

```text
compression_rate = (baseline_prompt_chars - reduced_prompt_chars) / baseline_prompt_chars
```

Character reduction is not token reduction. It should only be claimed as prompt character reduction unless token counts are actually measured with a specific tokenizer.

## Why Compression Must Be Read With Verifier Results

A high compression rate alone can be misleading. Pure reads compression together with current request preservation, verifier pass rate, and final status because an aggressively reduced prompt can look efficient while deleting the user's actual request or breaking the run. The benchmark therefore treats compression as useful only when the runtime behavior remains valid.

## Why Tool Loop Metrics Matter

ReAct-style agents can waste steps by repeatedly calling the same tool with the same normalized arguments. Pure's repetition guard is designed for that narrow runtime failure mode. Measuring repeated-call detection, warn/block behavior, and trace events is more targeted than measuring broad task success, which would mix runtime guard behavior with model intelligence and planner quality.

## Tool Governance Scope

Tool Governance tests ToolGateway as a local policy and audit layer. It validates arguments, approval mode, workspace path boundaries, security events, and risky tool diff fields. It is not a production sandbox, cannot defend against every shell risk, and should not be described as enterprise security.

## Checkpoint / Resume Scope

Checkpoint / Resume tests whether Pure can identify clean resumes, partial stale state, workspace mismatch, schema mismatch, runtime identity mismatch, and context-reduction checkpoint triggers. It does not prove production-grade transactional recovery, distributed task recovery, or complete semantic consistency of all files.

## What Is Suitable For Resume Claims

- Built a reproducible offline benchmark harness for Agent Runtime behavior.
- Added deterministic FakeModelClient-based regression suites for context governance, tool-loop control, ToolGateway policy, checkpoint/resume validation, and evaluator contracts.
- Produced structured JSON/JSONL/Markdown artifacts with per-case failure reasons and trace/report references.

## What Cannot Be Claimed

- Real model success rate.
- SWE-bench performance.
- Token cost reduction unless token counts are measured.
- Production-grade security.
- Production-grade distributed recovery.
- Pure data based on external projects or Pico author results.

## Reproduction

```bash
python scripts/run_pure_benchmarks.py --all --output benchmarks/pure/_runs/latest
python scripts/run_pure_benchmarks.py --suite context_compression --output benchmarks/pure/_runs/context_compression
python scripts/run_pure_benchmarks.py --suite tool_loop --output benchmarks/pure/_runs/tool_loop
python scripts/run_pure_benchmarks.py --suite tool_governance --output benchmarks/pure/_runs/tool_governance
python scripts/run_pure_benchmarks.py --suite checkpoint_resume --output benchmarks/pure/_runs/checkpoint_resume
python scripts/run_pure_benchmarks.py --suite evaluator_regression --output benchmarks/pure/_runs/evaluator_regression
```
