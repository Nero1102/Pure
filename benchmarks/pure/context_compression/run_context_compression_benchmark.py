from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pure.core.models import FakeModelClient
from pure.core.run_store import RunStore
from pure.core.runtime import PureRuntime
from pure.core.session_store import SessionStore
from pure.core.workspace import WorkspaceContext
from pure.services.trace_service import TraceService


BENCHMARK_SCHEMA_VERSION = 1
BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_CASES_PATH = BENCHMARK_DIR / "cases.json"
DEFAULT_RESULTS_PATH = BENCHMARK_DIR / "results.json"
DEFAULT_JSONL_PATH = BENCHMARK_DIR / "per_case_results.jsonl"
DEFAULT_SUMMARY_PATH = BENCHMARK_DIR / "summary.md"
DEFAULT_WORKSPACE_ROOT = BENCHMARK_DIR / "workspaces"


def load_cases(path: str | Path = DEFAULT_CASES_PATH) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = payload.get("cases", payload)
    if not isinstance(cases, list) or not cases:
        raise ValueError("context compression cases must be a non-empty list")
    required = {
        "case_id",
        "user_prompt",
        "history",
        "memory",
        "knowledge_items",
        "tool_observations",
        "mock_outputs",
        "expected_final_keywords",
        "current_request_must_contain",
        "max_steps",
        "baseline_config",
        "reduced_config",
    }
    seen = set()
    for case in cases:
        missing = sorted(required - set(case))
        if missing:
            raise ValueError(f"case {case.get('case_id', '<unknown>')} missing fields: {', '.join(missing)}")
        case_id = str(case["case_id"]).strip()
        if not case_id:
            raise ValueError("case_id must not be empty")
        if case_id in seen:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
        if int(case["max_steps"]) < 1:
            raise ValueError(f"case {case_id} max_steps must be positive")
    return cases


def run_benchmark(
    *,
    cases_path: str | Path = DEFAULT_CASES_PATH,
    results_path: str | Path = DEFAULT_RESULTS_PATH,
    per_case_path: str | Path = DEFAULT_JSONL_PATH,
    summary_path: str | Path = DEFAULT_SUMMARY_PATH,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    cases = load_cases(cases_path)
    rows: list[dict[str, Any]] = []
    case_summaries: list[dict[str, Any]] = []
    workspace_base = Path(workspace_root).resolve() if workspace_root else None
    if workspace_base:
        workspace_base.mkdir(parents=True, exist_ok=True)

    for case in cases:
        result = run_case(case, workspace_base=workspace_base)
        rows.extend(result["rows"])
        case_summaries.append(result["case_summary"])

    summary = summarize(case_summaries)
    artifact = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "artifact_type": "context-compression-benchmark",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "cases_path": str(Path(cases_path).resolve()),
        "model_provider": "FakeModelClient",
        "metric_unit": "characters",
        "formula": "compression_rate = (baseline_prompt_chars - reduced_prompt_chars) / baseline_prompt_chars",
        "summary": summary,
        "cases": case_summaries,
        "rows": rows,
    }

    results_path = Path(results_path)
    per_case_path = Path(per_case_path)
    summary_path = Path(summary_path)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    per_case_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(artifact, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    per_case_path.write_text(
        "".join(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    summary_path.write_text(render_summary_markdown(artifact), encoding="utf-8")
    return artifact


def run_case(case: dict[str, Any], *, workspace_base: Path | None = None) -> dict[str, Any]:
    baseline = _run_variant(case, "baseline", dict(case["baseline_config"]), workspace_base=workspace_base)
    reduced = _run_variant(case, "reduced", dict(case["reduced_config"]), workspace_base=workspace_base)
    row_count = max(len(baseline["prompt_rows"]), len(reduced["prompt_rows"]))
    rows = []
    for index in range(row_count):
        baseline_row = _prompt_row_at(baseline["prompt_rows"], index)
        reduced_row = _prompt_row_at(reduced["prompt_rows"], index)
        rows.append(_compare_prompt_rows(case, baseline, reduced, baseline_row, reduced_row, index + 1))

    final_row = rows[-1] if rows else _empty_comparison_row(case)
    case_summary = {
        "case_id": case["case_id"],
        "baseline_prompt_chars": final_row["baseline_prompt_chars"],
        "reduced_prompt_chars": final_row["reduced_prompt_chars"],
        "compression_rate": final_row["compression_rate"],
        "current_request_preserved": final_row["current_request_preserved"],
        "verifier_passed": final_row["verifier_passed"],
        "final_status": final_row["final_status"],
        "stop_reason": final_row["stop_reason"],
        "normal_final": final_row["final_status"] == "completed" and final_row["stop_reason"] == "final_answer_returned",
        "missing_required_context": list(final_row["missing_required_context"]),
        "notes": list(final_row["notes"]),
        "section_reductions": _section_reductions(final_row),
    }
    return {"rows": rows, "case_summary": case_summary}


def _run_variant(
    case: dict[str, Any],
    variant_name: str,
    config: dict[str, Any],
    *,
    workspace_base: Path | None,
) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    if workspace_base is None:
        root = DEFAULT_WORKSPACE_ROOT / f"{case['case_id']}-{variant_name}-{suffix}"
    else:
        root = workspace_base / f"{case['case_id']}-{variant_name}-{suffix}"
    root.mkdir(parents=True, exist_ok=True)

    _write_workspace_fixture(root, case)
    workspace = _build_isolated_workspace_context(root)
    agent = PureRuntime(
        model_client=FakeModelClient(list(case["mock_outputs"])),
        workspace=workspace,
        session_store=SessionStore(root / ".pure" / "sessions"),
        run_store=RunStore(root / ".pure" / "runs"),
        approval_policy="auto",
        max_steps=int(case["max_steps"]),
        max_new_tokens=int(config.get("max_new_tokens", 128)),
        feature_flags=dict(config.get("feature_flags", {}) or {}),
        runtime_config={"benchmark_variant": variant_name},
    )
    _seed_agent(agent, case)
    _apply_context_config(agent, config)

    final_answer = ""
    error = ""
    try:
        final_answer = agent.ask(str(case["user_prompt"]))
    except Exception as exc:  # benchmark rows must preserve failures
        error = str(exc)

    task_state = agent.current_task_state
    events: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "status": "failed" if error else "",
        "stop_reason": "runner_error" if error else "",
        "final_answer": final_answer,
        "prompt_metadata": {},
    }
    if task_state is not None:
        trace_path = agent.run_store.trace_path(task_state)
        if trace_path.exists():
            events = TraceService.load_events(trace_path)
        report_path = agent.run_store.report_path(task_state)
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
    prompt_rows = _prompt_rows_from_events(events, list(agent.model_client.prompts))
    result = {
        "variant": variant_name,
        "workspace_root": str(root),
        "error": error,
        "final_answer": final_answer or str(report.get("final_answer", "")),
        "report": report,
        "events": events,
        "prompt_rows": prompt_rows,
        "model_client_class": agent.model_client.__class__.__name__,
    }
    return result


def _build_isolated_workspace_context(root: Path) -> WorkspaceContext:
    previous_ceiling = os.environ.get("GIT_CEILING_DIRECTORIES")
    os.environ["GIT_CEILING_DIRECTORIES"] = str(REPO_ROOT)
    try:
        return WorkspaceContext.build(root, repo_root_override=root)
    finally:
        if previous_ceiling is None:
            os.environ.pop("GIT_CEILING_DIRECTORIES", None)
        else:
            os.environ["GIT_CEILING_DIRECTORIES"] = previous_ceiling


def _write_workspace_fixture(root: Path, case: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    default_readme = (
        "# Context Compression Fixture\n\n"
        "This workspace is generated by the Pure context compression benchmark.\n"
    )
    workspace_files = dict(case.get("workspace_files", {}) or {})
    workspace_files.setdefault("README.md", default_readme)
    for raw_path, content in workspace_files.items():
        path = root / str(raw_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(content), encoding="utf-8")
    knowledge_items = [str(item) for item in case.get("knowledge_items", [])]
    if knowledge_items:
        docs_dir = root / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        docs_text = "\n\n".join(f"## Knowledge {index + 1}\n{item}" for index, item in enumerate(knowledge_items))
        (docs_dir / "knowledge.md").write_text(docs_text + "\n", encoding="utf-8")


def _seed_agent(agent: PureRuntime, case: dict[str, Any]) -> None:
    memory = case.get("memory", {}) or {}
    notes = memory.get("notes", memory if isinstance(memory, list) else [])
    for index, note in enumerate(notes):
        if isinstance(note, dict):
            agent.memory.append_note(
                str(note.get("text", "")),
                tags=tuple(note.get("tags", []) or []),
                source=str(note.get("source", "")),
                created_at=str(note.get("created_at", "")) or f"2026-05-01T10:{index:02d}:00+00:00",
            )
        else:
            agent.memory.append_note(str(note), created_at=f"2026-05-01T10:{index:02d}:00+00:00")
    for path, summary in dict(memory.get("file_summaries", {}) if isinstance(memory, dict) else {}).items():
        agent.memory.set_file_summary(str(path), str(summary))
        agent.memory.remember_file(str(path))
    agent.session["memory"] = agent.memory.to_dict()

    if case.get("tool_observations_before_history"):
        _record_tool_observations(agent, case)
        _record_history(agent, case)
    else:
        _record_history(agent, case)
        _record_tool_observations(agent, case)

    checkpoint = case.get("checkpoint")
    if checkpoint:
        checkpoint_id = str(checkpoint.get("checkpoint_id", "ckpt_context"))
        payload = {
            "checkpoint_id": checkpoint_id,
            "parent_checkpoint_id": "",
            "schema_version": str(checkpoint.get("schema_version", "phase1-v1")),
            "created_at": str(checkpoint.get("created_at", "2026-05-01T12:00:00+00:00")),
            "current_goal": str(checkpoint.get("current_goal", case["user_prompt"])),
            "completed": list(checkpoint.get("completed", []) or []),
            "excluded": [],
            "current_blocker": str(checkpoint.get("current_blocker", "")),
            "next_step": str(checkpoint.get("next_step", "Continue from the latest benchmark context.")),
            "key_files": list(checkpoint.get("key_files", []) or []),
            "freshness": dict(checkpoint.get("freshness", {}) or {}),
            "summary": str(checkpoint.get("summary", "context compression checkpoint")),
            "runtime_identity": {
                "workspace_fingerprint": agent.workspace.fingerprint(),
                **dict(checkpoint.get("runtime_identity", {}) or {}),
            },
        }
        agent.session["checkpoints"] = {"current_id": checkpoint_id, "items": {checkpoint_id: payload}}
        agent.session_store.save(agent.session)
        agent.resume_state = agent.evaluate_resume_state()


def _record_history(agent: PureRuntime, case: dict[str, Any]) -> None:
    for index, item in enumerate(case.get("history", []) or []):
        if isinstance(item, dict):
            role = str(item.get("role", "user"))
            content = str(item.get("content", ""))
        else:
            role = "user" if index % 2 == 0 else "assistant"
            content = str(item)
        agent.record(
            {
                "role": role,
                "content": content,
                "created_at": f"2026-05-01T09:{index % 60:02d}:00+00:00",
            }
        )


def _record_tool_observations(agent: PureRuntime, case: dict[str, Any]) -> None:
    for index, item in enumerate(case.get("tool_observations", []) or []):
        agent.record(
            {
                "role": "tool",
                "name": str(item.get("name", "read_file")),
                "args": dict(item.get("args", {}) or {}),
                "content": str(item.get("result", "")),
                "created_at": f"2026-05-01T08:{index % 60:02d}:00+00:00",
            }
        )


def _apply_context_config(agent: PureRuntime, config: dict[str, Any]) -> None:
    feature_flags = dict(config.get("feature_flags", {}) or {})
    if feature_flags:
        agent.feature_flags.update({str(key): bool(value) for key, value in feature_flags.items()})
    manager = agent.context_manager
    if "total_budget" in config:
        manager.total_budget = int(config["total_budget"])
    if "section_budgets" in config:
        manager.section_budgets.update({str(key): int(value) for key, value in dict(config["section_budgets"]).items()})
    if "section_floors" in config:
        manager._section_floor_overrides = {
            str(key): int(value) for key, value in dict(config["section_floors"]).items()
        }
        manager.section_floors = manager._compute_section_floors()
    if "reduction_order" in config:
        manager.reduction_order = tuple(str(item) for item in config["reduction_order"])


def _prompt_rows_from_events(events: list[dict[str, Any]], prompts: list[str]) -> list[dict[str, Any]]:
    context_events = [
        event for event in events
        if event.get("event_type") == "context_built" and isinstance(event.get("payload", {}).get("prompt_metadata"), dict)
    ]
    rows = []
    for index, event in enumerate(context_events):
        prompt = prompts[index] if index < len(prompts) else ""
        metadata = dict(event.get("payload", {}).get("prompt_metadata", {}) or {})
        rows.append(
            {
                "step": int(event.get("step", 0) or 0),
                "prompt": prompt,
                "metadata": metadata,
                "event": event,
            }
        )
    return rows


def _prompt_row_at(rows: list[dict[str, Any]], index: int) -> dict[str, Any]:
    if index < len(rows):
        return rows[index]
    if rows:
        return rows[-1]
    return {"step": 0, "prompt": "", "metadata": {}, "event": {}}


def _compare_prompt_rows(
    case: dict[str, Any],
    baseline: dict[str, Any],
    reduced: dict[str, Any],
    baseline_row: dict[str, Any],
    reduced_row: dict[str, Any],
    step_index: int,
) -> dict[str, Any]:
    baseline_meta = dict(baseline_row.get("metadata", {}) or {})
    reduced_meta = dict(reduced_row.get("metadata", {}) or {})
    baseline_prompt_chars = int(baseline_meta.get("prompt_chars", len(baseline_row.get("prompt", ""))) or 0)
    reduced_prompt_chars = int(reduced_meta.get("prompt_chars", len(reduced_row.get("prompt", ""))) or 0)
    compression_rate = (
        (baseline_prompt_chars - reduced_prompt_chars) / baseline_prompt_chars
        if baseline_prompt_chars > 0 else 0.0
    )
    required_fragments = _string_list(case.get("current_request_must_contain", []))
    reduced_prompt = str(reduced_row.get("prompt", ""))
    exact_request = f"Current user request:\n{case['user_prompt']}"
    missing_required_context = [fragment for fragment in required_fragments if fragment not in reduced_prompt]
    if exact_request not in reduced_prompt:
        missing_required_context.append("exact_current_request_section")
    current_request_preserved = not missing_required_context
    final_answer = str(reduced.get("final_answer") or reduced.get("report", {}).get("final_answer", ""))
    missing_final_keywords = [
        keyword for keyword in _string_list(case.get("expected_final_keywords", []))
        if keyword.lower() not in final_answer.lower()
    ]
    final_status = str(reduced.get("report", {}).get("status", "failed") or "failed")
    stop_reason = str(reduced.get("report", {}).get("stop_reason", "") or "")
    verifier_passed = (
        final_status == "completed"
        and stop_reason == "final_answer_returned"
        and current_request_preserved
        and not missing_final_keywords
        and not reduced.get("error")
    )
    notes = []
    if compression_rate < 0:
        notes.append("negative compression recorded; reduced prompt was longer than baseline")
    if missing_final_keywords:
        notes.append("missing final keywords: " + ", ".join(missing_final_keywords))
    if reduced.get("error"):
        notes.append("reduced runner error: " + str(reduced["error"]))
    if baseline.get("error"):
        notes.append("baseline runner error: " + str(baseline["error"]))

    row = {
        "case_id": str(case["case_id"]),
        "step": step_index,
        "baseline_prompt_chars": baseline_prompt_chars,
        "reduced_prompt_chars": reduced_prompt_chars,
        "compression_rate": compression_rate,
        "prefix_raw_chars": _section_chars(reduced_meta, "prefix", "raw_chars"),
        "prefix_rendered_chars": _section_chars(reduced_meta, "prefix", "rendered_chars"),
        "memory_raw_chars": _section_chars(reduced_meta, "memory", "raw_chars"),
        "memory_rendered_chars": _section_chars(reduced_meta, "memory", "rendered_chars"),
        "knowledge_raw_chars": _section_chars(reduced_meta, "knowledge_context", "raw_chars"),
        "knowledge_rendered_chars": _section_chars(reduced_meta, "knowledge_context", "rendered_chars"),
        "relevant_memory_raw_chars": _section_chars(reduced_meta, "relevant_memory", "raw_chars"),
        "relevant_memory_rendered_chars": _section_chars(reduced_meta, "relevant_memory", "rendered_chars"),
        "history_raw_chars": _section_chars(reduced_meta, "history", "raw_chars"),
        "history_rendered_chars": _section_chars(reduced_meta, "history", "rendered_chars"),
        "tool_observation_raw_chars": _tool_observation_raw_chars(case),
        "tool_observation_rendered_chars": _tool_observation_rendered_chars(case, reduced_prompt),
        "current_request_chars": len(str(case["user_prompt"])),
        "current_request_preserved": current_request_preserved,
        "budget_reductions": list(reduced_meta.get("budget_reductions", []) or []),
        "final_status": final_status,
        "stop_reason": stop_reason,
        "verifier_passed": verifier_passed,
        "missing_required_context": missing_required_context,
        "notes": notes,
        "baseline_sections": _sections_for_output(baseline_meta),
        "reduced_sections": _sections_for_output(reduced_meta),
    }
    row["section_reductions"] = _section_reductions(row)
    return row


def _empty_comparison_row(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": str(case["case_id"]),
        "step": 0,
        "baseline_prompt_chars": 0,
        "reduced_prompt_chars": 0,
        "compression_rate": 0.0,
        "current_request_preserved": False,
        "verifier_passed": False,
        "final_status": "failed",
        "stop_reason": "no_prompt_rows",
        "missing_required_context": ["no_prompt_rows"],
        "notes": ["no prompt rows were produced"],
        "section_reductions": {},
    }


def _section_chars(metadata: dict[str, Any], section: str, key: str) -> int:
    return int((metadata.get("sections", {}).get(section, {}) or {}).get(key, 0) or 0)


def _sections_for_output(metadata: dict[str, Any]) -> dict[str, dict[str, int]]:
    sections = {}
    for section in ("prefix", "memory", "knowledge_context", "relevant_memory", "history", "current_request"):
        raw = metadata.get("sections", {}).get(section, {}) or {}
        sections[section] = {
            "raw_chars": int(raw.get("raw_chars", 0) or 0),
            "rendered_chars": int(raw.get("rendered_chars", 0) or 0),
        }
    return sections


def _section_reductions(row: dict[str, Any]) -> dict[str, int]:
    baseline_sections = row.get("baseline_sections", {})
    reduced_sections = row.get("reduced_sections", {})

    def diff(section: str) -> int:
        return int((baseline_sections.get(section, {}) or {}).get("rendered_chars", 0) or 0) - int(
            (reduced_sections.get(section, {}) or {}).get("rendered_chars", 0) or 0
        )

    return {
        "prefix_reduction_chars": diff("prefix"),
        "memory_reduction_chars": diff("memory"),
        "knowledge_reduction_chars": diff("knowledge_context"),
        "relevant_memory_reduction_chars": diff("relevant_memory"),
        "history_reduction_chars": diff("history"),
        "tool_observation_reduction_chars": int(row.get("tool_observation_raw_chars", 0) or 0)
        - int(row.get("tool_observation_rendered_chars", 0) or 0),
    }


def _tool_observation_raw_chars(case: dict[str, Any]) -> int:
    total = 0
    for item in case.get("tool_observations", []) or []:
        prefix = f"[tool:{item.get('name', 'read_file')}] {json.dumps(dict(item.get('args', {}) or {}), sort_keys=True)}"
        total += len(prefix) + 1 + len(str(item.get("result", "")))
    return total


def _tool_observation_rendered_chars(case: dict[str, Any], prompt: str) -> int:
    transcript = _transcript_section(prompt)
    if not transcript:
        return 0
    lines = transcript.splitlines()
    total = 0
    consumed_indexes = set()
    for index, line in enumerate(lines):
        if line.startswith("[tool:"):
            total += len(line)
            consumed_indexes.add(index)
            if index + 1 < len(lines) and not lines[index + 1].startswith("["):
                total += len(lines[index + 1])
                consumed_indexes.add(index + 1)
    for item in case.get("tool_observations", []) or []:
        args = dict(item.get("args", {}) or {})
        path = str(args.get("path", "")).strip()
        command = str(args.get("command", "")).strip()
        for index, line in enumerate(lines):
            if index in consumed_indexes:
                continue
            if path and line.startswith(f"{path} -> "):
                total += len(line)
                consumed_indexes.add(index)
            elif command and line.startswith(f"{command} -> "):
                total += len(line)
                consumed_indexes.add(index)
    return total


def _transcript_section(prompt: str) -> str:
    if "\n\nTranscript:" not in prompt:
        return ""
    tail = prompt.split("\n\nTranscript:", 1)[1]
    if "\n\nCurrent user request:" in tail:
        tail = tail.split("\n\nCurrent user request:", 1)[0]
    return "Transcript:" + tail


def summarize(case_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    rates = [float(item["compression_rate"]) for item in case_summaries]
    baseline_chars = [int(item["baseline_prompt_chars"]) for item in case_summaries]
    reduced_chars = [int(item["reduced_prompt_chars"]) for item in case_summaries]
    failed_cases = [
        {
            "case_id": item["case_id"],
            "missing_required_context": list(item["missing_required_context"]),
            "notes": list(item["notes"]),
        }
        for item in case_summaries
        if not item["verifier_passed"]
    ]
    section_breakdown = {
        "prefix_reduction_chars": 0,
        "memory_reduction_chars": 0,
        "knowledge_reduction_chars": 0,
        "relevant_memory_reduction_chars": 0,
        "history_reduction_chars": 0,
        "tool_observation_reduction_chars": 0,
    }
    for item in case_summaries:
        for key in section_breakdown:
            section_breakdown[key] += int(item.get("section_reductions", {}).get(key, 0) or 0)
    sorted_cases = sorted(case_summaries, key=lambda item: float(item["compression_rate"]), reverse=True)
    low_cases = sorted(case_summaries, key=lambda item: float(item["compression_rate"]))
    return {
        "case_count": len(case_summaries),
        "avg_baseline_prompt_chars": _mean(baseline_chars),
        "avg_reduced_prompt_chars": _mean(reduced_chars),
        "avg_compression_rate": _mean(rates),
        "p50_compression_rate": _percentile(rates, 0.50),
        "p90_compression_rate": _percentile(rates, 0.90),
        "max_compression_rate": max(rates) if rates else 0.0,
        "min_compression_rate": min(rates) if rates else 0.0,
        "current_request_preserved_rate": _ratio(sum(1 for item in case_summaries if item["current_request_preserved"]), len(case_summaries)),
        "verifier_pass_rate": _ratio(sum(1 for item in case_summaries if item["verifier_passed"]), len(case_summaries)),
        "normal_final_rate": _ratio(sum(1 for item in case_summaries if item["normal_final"]), len(case_summaries)),
        "negative_compression_case_count": sum(1 for item in case_summaries if float(item["compression_rate"]) < 0),
        "failed_cases": failed_cases,
        "top_compression_cases": _case_rate_list(sorted_cases[:5]),
        "low_or_negative_compression_cases": _case_rate_list(low_cases[:5]),
        "section_reduction_breakdown": section_breakdown,
    }


def render_summary_markdown(artifact: dict[str, Any]) -> str:
    summary = artifact["summary"]
    failed_cases = summary["failed_cases"] or [{"case_id": "-", "notes": ["none"], "missing_required_context": []}]
    lines = [
        "# Context Compression Benchmark",
        "",
        "## Benchmark Goal",
        "",
        "This benchmark measures how Pure's current context governance changes the final prompt character length sent to the model, while checking that the current request remains intact and the scripted task can still finish.",
        "",
        "## Method",
        "",
        "Baseline uses the same fixed case inputs with context reduction disabled or with the closest available no-clipping configuration. Reduced uses Pure's current context reduction and section budget controls. Both variants use FakeModelClient and the same mock outputs for each case.",
        "",
        "## Formula",
        "",
        "`compression_rate = (baseline_prompt_chars - reduced_prompt_chars) / baseline_prompt_chars`",
        "",
        "## Why chars instead of tokens",
        "",
        "Character count is a provider-neutral proxy available from the current Pure prompt metadata. It is not a tokenizer result and must not be described as token usage or token cost.",
        "",
        "## Results",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| case_count | {summary['case_count']} |",
        f"| avg_baseline_prompt_chars | {summary['avg_baseline_prompt_chars']:.2f} |",
        f"| avg_reduced_prompt_chars | {summary['avg_reduced_prompt_chars']:.2f} |",
        f"| avg_compression_rate | {_pct(summary['avg_compression_rate'])} |",
        f"| p50_compression_rate | {_pct(summary['p50_compression_rate'])} |",
        f"| p90_compression_rate | {_pct(summary['p90_compression_rate'])} |",
        f"| max_compression_rate | {_pct(summary['max_compression_rate'])} |",
        f"| min_compression_rate | {_pct(summary['min_compression_rate'])} |",
        f"| negative_compression_case_count | {summary['negative_compression_case_count']} |",
        "",
        "## Section Breakdown",
        "",
        "| Section | Reduction Chars |",
        "| --- | ---: |",
    ]
    for key, value in summary["section_reduction_breakdown"].items():
        lines.append(f"| {key} | {value} |")
    lines.extend(
        [
            "",
            "## Correctness Checks",
            "",
            "| Check | Rate |",
            "| --- | ---: |",
            f"| current_request_preserved_rate | {_pct(summary['current_request_preserved_rate'])} |",
            f"| verifier_pass_rate | {_pct(summary['verifier_pass_rate'])} |",
            f"| normal_final_rate | {_pct(summary['normal_final_rate'])} |",
            "",
            "## Failed / Risky Cases",
            "",
            "| Case | Missing Context | Notes |",
            "| --- | --- | --- |",
        ]
    )
    for item in failed_cases:
        lines.append(
            f"| {item['case_id']} | {', '.join(item.get('missing_required_context', [])) or '-'} | "
            f"{'; '.join(item.get('notes', [])) or '-'} |"
        )
    lines.extend(
        [
            "",
            "## What Can Be Claimed",
            "",
            "- Pure has a reproducible offline benchmark for prompt character length under context budget pressure.",
            "- The benchmark reports section-level character reductions and correctness checks from real run artifacts.",
            "- Results are specific to FakeModelClient/scripted cases and current repository code.",
            "",
            "## What Cannot Be Claimed",
            "",
            "- It cannot claim token cost reduction.",
            "- It cannot claim real model capability improvement.",
            "- It cannot claim production-grade context compression.",
            "- It cannot judge compression rate without checking correctness.",
            "",
            "## Reproduction Command",
            "",
            "```bash",
            "python benchmarks/pure/context_compression/run_context_compression_benchmark.py",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value in (None, ""):
        return []
    return [str(value)]


def _mean(values: list[float | int]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def _ratio(numerator: int, denominator: int) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * float(percentile)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _case_rate_list(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "case_id": item["case_id"],
            "compression_rate": float(item["compression_rate"]),
            "baseline_prompt_chars": int(item["baseline_prompt_chars"]),
            "reduced_prompt_chars": int(item["reduced_prompt_chars"]),
        }
        for item in cases
    ]


def _pct(value: float) -> str:
    return f"{float(value) * 100:.2f}%"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Pure's offline context compression benchmark.")
    parser.add_argument("--cases", default=str(DEFAULT_CASES_PATH), help="Path to context compression cases JSON.")
    parser.add_argument("--results", default=str(DEFAULT_RESULTS_PATH), help="Path to write results.json.")
    parser.add_argument("--per-case", default=str(DEFAULT_JSONL_PATH), help="Path to write per-case JSONL rows.")
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY_PATH), help="Path to write summary.md.")
    parser.add_argument("--workspace-root", default=None, help="Optional directory for generated temporary workspaces.")
    args = parser.parse_args(argv)
    artifact = run_benchmark(
        cases_path=args.cases,
        results_path=args.results,
        per_case_path=args.per_case,
        summary_path=args.summary,
        workspace_root=args.workspace_root,
    )
    print(json.dumps(artifact["summary"], indent=2, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
