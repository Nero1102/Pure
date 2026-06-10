from __future__ import annotations

import argparse
import json
import os
import shutil
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

REQUIRED_CASE_FIELDS = {
    "case_id",
    "setup_actions",
    "mutation_actions",
    "runtime_config",
    "expected_resume_status",
    "expected_checkpoint_trigger",
    "expected_trace_events",
    "expected_allowed_to_continue",
    "notes",
}
IDENTITY_KEYS = (
    "cwd",
    "model",
    "model_client",
    "approval_policy",
    "read_only",
    "max_steps",
    "max_new_tokens",
    "feature_flags",
    "shell_env_allowlist",
    "workspace_fingerprint",
    "tool_signature",
)
VALID_RESUME_STATUSES = {
    "no-checkpoint",
    "full-valid",
    "partial-stale",
    "workspace-mismatch",
    "schema-mismatch",
}


def load_cases(path: str | Path = DEFAULT_CASES_PATH) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = payload.get("cases", payload)
    if not isinstance(cases, list) or not cases:
        raise ValueError("checkpoint/resume cases must be a non-empty list")

    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("each checkpoint/resume case must be an object")
        missing = sorted(REQUIRED_CASE_FIELDS - set(case))
        if missing:
            raise ValueError(f"case {case.get('case_id', '<unknown>')} missing fields: {', '.join(missing)}")
        case_id = str(case["case_id"]).strip()
        if not case_id:
            raise ValueError("case_id must not be empty")
        if case_id in seen:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
        if not isinstance(case["setup_actions"], list):
            raise ValueError(f"case {case_id} setup_actions must be a list")
        if not isinstance(case["mutation_actions"], list):
            raise ValueError(f"case {case_id} mutation_actions must be a list")
        if not isinstance(case["runtime_config"], dict):
            raise ValueError(f"case {case_id} runtime_config must be an object")
        if str(case["expected_resume_status"]) not in VALID_RESUME_STATUSES:
            raise ValueError(f"case {case_id} expected_resume_status is not a known Pure status")
        if not isinstance(case["expected_trace_events"], list):
            raise ValueError(f"case {case_id} expected_trace_events must be a list")
        if not isinstance(case["expected_allowed_to_continue"], bool):
            raise ValueError(f"case {case_id} expected_allowed_to_continue must be a bool")
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
    workspace_base = Path(workspace_root).resolve() if workspace_root else DEFAULT_WORKSPACE_ROOT.resolve()
    workspace_base.mkdir(parents=True, exist_ok=True)

    rows = [run_case(case, workspace_base=workspace_base) for case in cases]
    summary = summarize(rows)
    artifact = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "artifact_type": "checkpoint-resume-benchmark",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "cases_path": str(Path(cases_path).resolve()),
        "model_provider": "FakeModelClient",
        "benchmark_target": "Checkpoint/Resume",
        "summary": summary,
        "cases": rows,
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


def run_case(case: dict[str, Any], *, workspace_base: Path) -> dict[str, Any]:
    case_id = str(case["case_id"])
    root = workspace_base / f"{case_id}-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    _write_workspace_fixture(root)

    session_store = SessionStore(root / ".pure" / "sessions")
    initial_agent = _create_initial_checkpoint(case, root=root, session_store=session_store)
    session_id = str(initial_agent.session["id"])
    checkpoint_before = dict(initial_agent.current_checkpoint() or {})
    schema_version_before = str(checkpoint_before.get("schema_version", ""))
    workspace_hash_before = _workspace_hash(root)

    resume_root = _apply_mutation_actions(case, root=root, session_store=session_store, session_id=session_id)
    session_after_mutation = session_store.load(session_id)
    checkpoint_for_resume = dict(_current_checkpoint_from_session(session_after_mutation) or {})
    runtime_identity_before = dict(checkpoint_for_resume.get("runtime_identity", {}) or {})
    schema_version_after = str(checkpoint_for_resume.get("schema_version", ""))
    workspace_hash_after = _workspace_hash(resume_root)

    resumed_agent = _create_resumed_agent(
        case,
        resume_root=resume_root,
        session_store=session_store,
        session_id=session_id,
    )
    runtime_config = dict(case.get("runtime_config", {}) or {})
    _apply_context_config(resumed_agent, dict(runtime_config.get("context_config", {}) or {}))
    runtime_identity_after = resumed_agent.current_runtime_identity()

    final_answer = ""
    runner_error = ""
    try:
        final_answer = resumed_agent.ask(f"Resume checkpoint benchmark case {case_id}.")
    except Exception as exc:  # benchmark rows preserve failures instead of hiding them
        runner_error = str(exc)

    events, report = _load_run_artifacts(resumed_agent, final_answer=final_answer, runner_error=runner_error)
    return _case_result_row(
        case=case,
        initial_root=root,
        resume_root=resume_root,
        workspace_hash_before=workspace_hash_before,
        workspace_hash_after=workspace_hash_after,
        runtime_identity_before=runtime_identity_before,
        runtime_identity_after=runtime_identity_after,
        schema_version_before=schema_version_before,
        schema_version_after=schema_version_after,
        events=events,
        report=report,
        runner_error=runner_error,
    )


def _create_initial_checkpoint(case: dict[str, Any], *, root: Path, session_store: SessionStore) -> PureRuntime:
    read_key_files = []
    for action in case.get("setup_actions", []) or []:
        if str(action.get("action", "")) == "create_checkpoint":
            read_key_files.extend(str(path) for path in action.get("read_key_files", []) or [])

    outputs = [
        _tool_call_output("read_file", {"path": path})
        for path in read_key_files
    ]
    outputs.append("<final>Initial checkpoint ready.</final>")
    workspace = _build_isolated_workspace_context(root)
    agent = PureRuntime(
        model_client=FakeModelClient(outputs),
        workspace=workspace,
        session_store=session_store,
        run_store=RunStore(root / ".pure" / "runs"),
        approval_policy="auto",
        max_steps=max(6, len(outputs) + 2),
        max_new_tokens=512,
        runtime_config={"benchmark": "checkpoint_resume", "phase": "initial", "case_id": case["case_id"]},
    )
    agent.ask(f"Create checkpoint for checkpoint/resume benchmark case {case['case_id']}.")
    return agent


def _create_resumed_agent(
    case: dict[str, Any],
    *,
    resume_root: Path,
    session_store: SessionStore,
    session_id: str,
) -> PureRuntime:
    runtime_config = dict(case.get("runtime_config", {}) or {})
    constructor_config = {key: value for key, value in runtime_config.items() if key not in {"mock_outputs", "context_config"}}
    outputs = list(runtime_config.get("mock_outputs", []) or ["<final>Resumed.</final>"])
    return PureRuntime.from_session(
        model_client=FakeModelClient(outputs),
        workspace=_build_isolated_workspace_context(resume_root),
        session_store=session_store,
        session_id=session_id,
        run_store=RunStore(resume_root / ".pure" / "runs"),
        approval_policy=str(constructor_config.pop("approval_policy", "auto")),
        approval_mode=constructor_config.pop("approval_mode", None),
        max_steps=int(constructor_config.pop("max_steps", 6)),
        max_new_tokens=int(constructor_config.pop("max_new_tokens", 512)),
        read_only=bool(constructor_config.pop("read_only", False)),
        feature_flags=dict(constructor_config.pop("feature_flags", {}) or {}),
        runtime_config={"benchmark": "checkpoint_resume", "phase": "resume", "case_id": case["case_id"]},
    )


def _write_workspace_fixture(root: Path) -> None:
    files = {
        "README.md": "# Checkpoint Resume Fixture\n\nThis workspace is generated for Pure benchmark cases.\n",
        "runtime.py": "def answer():\n    return 'alpha'\n",
        "notes.txt": "stable notes\n",
        "docs/guide.md": "fixture documentation\n",
    }
    for raw_path, content in files.items():
        path = root / raw_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _apply_mutation_actions(case: dict[str, Any], *, root: Path, session_store: SessionStore, session_id: str) -> Path:
    resume_root = root
    for action in case.get("mutation_actions", []) or []:
        name = str(action.get("action", ""))
        if name == "modify_file":
            path = root / str(action["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(action.get("content", "")), encoding="utf-8")
        elif name == "modify_many_files":
            count = int(action.get("count", 6))
            (root / "README.md").write_text(
                "# Changed Checkpoint Resume Fixture\n\n"
                "This documented workspace change is included in WorkspaceContext.fingerprint().\n",
                encoding="utf-8",
            )
            bulk_dir = root / "bulk"
            bulk_dir.mkdir(parents=True, exist_ok=True)
            for index in range(count):
                (bulk_dir / f"changed_{index}.txt").write_text(f"changed file {index}\n", encoding="utf-8")
        elif name == "change_repo_root":
            resume_root = root.parent / f"{root.name}-moved"
            _copy_workspace_files(root, resume_root)
        elif name == "set_checkpoint_schema":
            session = session_store.load(session_id)
            checkpoint = _current_checkpoint_from_session(session)
            checkpoint["schema_version"] = str(action.get("schema_version", "legacy-v0"))
            session_store.save(session)
        elif name == "set_runtime_identity":
            session = session_store.load(session_id)
            checkpoint = _current_checkpoint_from_session(session)
            identity = dict(checkpoint.get("runtime_identity", {}) or {})
            identity[str(action["key"])] = action.get("value", "")
            checkpoint["runtime_identity"] = identity
            session_store.save(session)
        elif name == "append_long_history":
            session = session_store.load(session_id)
            history = list(session.get("history", []) or [])
            entries = int(action.get("entries", 12))
            chars_per_entry = int(action.get("chars_per_entry", 240))
            for index in range(entries):
                role = "user" if index % 2 == 0 else "assistant"
                history.append(
                    {
                        "role": role,
                        "content": f"long-history-{index}-" + ("A" * chars_per_entry),
                        "created_at": f"2026-05-01T10:{index % 60:02d}:00+00:00",
                    }
                )
            session["history"] = history
            session_store.save(session)
        else:
            raise ValueError(f"unsupported mutation action: {name}")
    return resume_root


def _copy_workspace_files(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if ".pure" in relative.parts:
            continue
        destination = target / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def _apply_context_config(agent: PureRuntime, config: dict[str, Any]) -> None:
    if not config:
        return
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


def _current_checkpoint_from_session(session: dict[str, Any]) -> dict[str, Any]:
    state = session.get("checkpoints", {}) or {}
    current_id = str(state.get("current_id", ""))
    checkpoint = (state.get("items", {}) or {}).get(current_id)
    if not isinstance(checkpoint, dict):
        raise ValueError("session does not contain a current checkpoint")
    return checkpoint


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


def _workspace_hash(root: Path) -> str:
    return _build_isolated_workspace_context(root).fingerprint()


def _tool_call_output(tool_name: str, arguments: dict[str, Any]) -> str:
    payload = {"name": tool_name, "args": arguments}
    return "<tool>" + json.dumps(payload, ensure_ascii=True, sort_keys=True) + "</tool>"


def _load_run_artifacts(agent: PureRuntime, *, final_answer: str, runner_error: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "status": "failed" if runner_error else "",
        "stop_reason": "runner_error" if runner_error else "",
        "final_answer": final_answer,
        "prompt_metadata": dict(getattr(agent, "last_prompt_metadata", {}) or {}),
    }
    task_state = getattr(agent, "current_task_state", None)
    if task_state is None:
        return events, report
    trace_path = agent.run_store.trace_path(task_state)
    if trace_path.exists():
        events = TraceService.load_events(trace_path)
    report_path = agent.run_store.report_path(task_state)
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    return events, report


def _case_result_row(
    *,
    case: dict[str, Any],
    initial_root: Path,
    resume_root: Path,
    workspace_hash_before: str,
    workspace_hash_after: str,
    runtime_identity_before: dict[str, Any],
    runtime_identity_after: dict[str, Any],
    schema_version_before: str,
    schema_version_after: str,
    events: list[dict[str, Any]],
    report: dict[str, Any],
    runner_error: str,
) -> dict[str, Any]:
    checkpoint_events = [event for event in events if _event_name(event) == "checkpoint_created"]
    expected_trigger = str(case["expected_checkpoint_trigger"])
    matched_checkpoint_event = _find_checkpoint_event(checkpoint_events, expected_trigger)
    checkpoint_trigger = (
        _checkpoint_trigger(matched_checkpoint_event)
        if matched_checkpoint_event
        else (_checkpoint_trigger(checkpoint_events[-1]) if checkpoint_events else "")
    )
    checkpoint_id = ""
    if matched_checkpoint_event:
        checkpoint_id = str((matched_checkpoint_event.get("payload", {}) or {}).get("checkpoint_id", matched_checkpoint_event.get("checkpoint_id", "")))
    elif checkpoint_events:
        checkpoint_id = str((checkpoint_events[-1].get("payload", {}) or {}).get("checkpoint_id", checkpoint_events[-1].get("checkpoint_id", "")))

    actual_trace_events = _unique_event_names(events)
    prompt_metadata = dict(report.get("prompt_metadata", {}) or {})
    actual_resume_status = str(prompt_metadata.get("resume_status", ""))
    stale_paths = [str(path) for path in prompt_metadata.get("stale_paths", []) or []]
    runtime_identity_mismatch_fields = list(prompt_metadata.get("runtime_identity_mismatch_fields", []) or [])
    expected_resume_status = str(case["expected_resume_status"])
    expected_trace_events = [str(item) for item in case.get("expected_trace_events", []) or []]
    actual_allowed_to_continue = str(report.get("status", "")) == "completed" and str(report.get("stop_reason", "")) == "final_answer_returned"
    expected_allowed_to_continue = bool(case["expected_allowed_to_continue"])
    runtime_identity_match = _runtime_identity_match(runtime_identity_before, runtime_identity_after)
    false_accept = _false_accept(
        expected_resume_status=expected_resume_status,
        actual_resume_status=actual_resume_status,
        expected_allowed_to_continue=expected_allowed_to_continue,
        actual_allowed_to_continue=actual_allowed_to_continue,
    )
    false_reject = expected_allowed_to_continue and not actual_allowed_to_continue

    failure_reasons = []
    if runner_error:
        failure_reasons.append(f"runner error: {runner_error}")
    if actual_resume_status != expected_resume_status:
        failure_reasons.append(f"resume_status mismatch: expected {expected_resume_status}, got {actual_resume_status or '<empty>'}")
    if expected_trigger and checkpoint_trigger != expected_trigger:
        failure_reasons.append(f"checkpoint_trigger mismatch: expected {expected_trigger}, got {checkpoint_trigger or '<empty>'}")
    missing_trace_events = [event for event in expected_trace_events if event not in actual_trace_events]
    if missing_trace_events:
        failure_reasons.append("missing trace events: " + ", ".join(missing_trace_events))
    if actual_allowed_to_continue != expected_allowed_to_continue:
        failure_reasons.append(
            f"allowed_to_continue mismatch: expected {expected_allowed_to_continue}, got {actual_allowed_to_continue}"
        )
    if false_accept:
        failure_reasons.append("false accept")
    if false_reject:
        failure_reasons.append("false reject")

    return {
        "case_id": str(case["case_id"]),
        "category": str(case.get("category", "")),
        "expected_resume_status": expected_resume_status,
        "actual_resume_status": actual_resume_status,
        "checkpoint_created": bool(checkpoint_events),
        "checkpoint_trigger": checkpoint_trigger,
        "checkpoint_id": checkpoint_id,
        "workspace_hash_before": workspace_hash_before,
        "workspace_hash_after": workspace_hash_after,
        "workspace_changed": workspace_hash_before != workspace_hash_after or initial_root.resolve() != resume_root.resolve(),
        "runtime_identity_before": runtime_identity_before,
        "runtime_identity_after": runtime_identity_after,
        "runtime_identity_match": runtime_identity_match,
        "runtime_identity_mismatch_fields": runtime_identity_mismatch_fields,
        "stale_paths": stale_paths,
        "schema_version_before": schema_version_before,
        "schema_version_after": schema_version_after,
        "expected_trace_events": expected_trace_events,
        "actual_trace_events": actual_trace_events,
        "expected_allowed_to_continue": expected_allowed_to_continue,
        "actual_allowed_to_continue": actual_allowed_to_continue,
        "false_accept": false_accept,
        "false_reject": false_reject,
        "passed": not failure_reasons,
        "failure_reason": "; ".join(failure_reasons),
        "initial_workspace_root": str(initial_root),
        "resume_workspace_root": str(resume_root),
        "report_status": str(report.get("status", "")),
        "report_stop_reason": str(report.get("stop_reason", "")),
        "notes": str(case["notes"]),
    }


def _event_name(event: dict[str, Any]) -> str:
    return str(event.get("event") or event.get("event_type") or "")


def _unique_event_names(events: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for event in events:
        name = _event_name(event)
        if name and name not in names:
            names.append(name)
    return names


def _checkpoint_trigger(event: dict[str, Any] | None) -> str:
    if not event:
        return ""
    payload = dict(event.get("payload", {}) or {})
    return str(payload.get("trigger", event.get("trigger", "")) or "")


def _find_checkpoint_event(checkpoint_events: list[dict[str, Any]], expected_trigger: str) -> dict[str, Any] | None:
    for event in checkpoint_events:
        if _checkpoint_trigger(event) == expected_trigger:
            return event
    return None


def _runtime_identity_match(saved: dict[str, Any], current: dict[str, Any]) -> bool:
    for key in IDENTITY_KEYS:
        if key in saved and saved.get(key) != current.get(key):
            return False
    return True


def _false_accept(
    *,
    expected_resume_status: str,
    actual_resume_status: str,
    expected_allowed_to_continue: bool,
    actual_allowed_to_continue: bool,
) -> bool:
    if not expected_allowed_to_continue and actual_allowed_to_continue:
        return True
    expected_mismatch = expected_resume_status not in {"full-valid", "no-checkpoint"}
    accepted_as_clean = actual_resume_status in {"full-valid", "no-checkpoint", ""}
    return expected_mismatch and accepted_as_clean and actual_allowed_to_continue


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    case_count = len(rows)
    mismatch_cases = [
        row for row in rows
        if row["expected_resume_status"] not in {"full-valid", "no-checkpoint"}
    ]
    runtime_identity_cases = [row for row in rows if row["category"] == "runtime_identity"]
    schema_cases = [row for row in rows if row["category"] == "schema_mismatch"]
    context_cases = [row for row in rows if row["category"] == "context_reduction"]
    return {
        "case_count": case_count,
        "resume_status_accuracy": _ratio(
            sum(1 for row in rows if row["actual_resume_status"] == row["expected_resume_status"]),
            case_count,
        ),
        "mismatch_detection_rate": _ratio(
            sum(1 for row in mismatch_cases if row["actual_resume_status"] == row["expected_resume_status"]),
            len(mismatch_cases),
        ),
        "runtime_identity_detection_rate": _ratio(
            sum(
                1
                for row in runtime_identity_cases
                if row["actual_resume_status"] == row["expected_resume_status"]
                and not row["runtime_identity_match"]
                and row["runtime_identity_mismatch_fields"]
            ),
            len(runtime_identity_cases),
        ),
        "schema_mismatch_detection_rate": _ratio(
            sum(1 for row in schema_cases if row["actual_resume_status"] == "schema-mismatch"),
            len(schema_cases),
        ),
        "context_reduction_checkpoint_hit_rate": _ratio(
            sum(1 for row in context_cases if row["checkpoint_trigger"] == "context_reduction"),
            len(context_cases),
        ),
        "checkpoint_event_hit_rate": _ratio(
            sum(1 for row in rows if row["checkpoint_created"] and row["checkpoint_trigger"]),
            case_count,
        ),
        "false_accept_count": sum(1 for row in rows if row["false_accept"]),
        "false_reject_count": sum(1 for row in rows if row["false_reject"]),
        "failed_cases": [
            {"case_id": row["case_id"], "failure_reason": row["failure_reason"]}
            for row in rows
            if not row["passed"]
        ],
    }


def render_summary_markdown(artifact: dict[str, Any]) -> str:
    summary = artifact["summary"]
    rows = list(artifact["cases"])
    lines = [
        "# Checkpoint / Resume Benchmark",
        "",
        "## Goal",
        "",
        "Checkpoint/resume lets Pure save task state, later rebuild enough runtime context, and detect when the saved state no longer matches the workspace or runtime that is trying to continue it.",
        "",
        "## Key Concepts",
        "",
        "- create_checkpoint = save an archive of task state, memory snapshot, key file freshness, workspace hash, runtime metadata, and runtime identity.",
        "- resume = load a saved session/checkpoint and evaluate whether it is still valid before continuing.",
        "- workspace hash / fingerprint = a deterministic summary of the fixture workspace context used to detect drift.",
        "- runtime identity = execution metadata such as cwd, model/model client, approval policy, feature flags, tool signature, and workspace fingerprint.",
        "- partial stale = key file freshness changed, so file-specific memory/checkpoint facts may need re-anchoring.",
        "- workspace mismatch = runtime identity or workspace fingerprint differs from the saved checkpoint.",
        "- context reduction checkpoint = a checkpoint created because prompt budget reductions occurred before model completion.",
        "",
        "## Results",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| case_count | {summary['case_count']} |",
        f"| resume_status_accuracy | {_pct(summary['resume_status_accuracy'])} |",
        f"| mismatch_detection_rate | {_pct(summary['mismatch_detection_rate'])} |",
        f"| runtime_identity_detection_rate | {_pct(summary['runtime_identity_detection_rate'])} |",
        f"| schema_mismatch_detection_rate | {_pct(summary['schema_mismatch_detection_rate'])} |",
        f"| context_reduction_checkpoint_hit_rate | {_pct(summary['context_reduction_checkpoint_hit_rate'])} |",
        f"| checkpoint_event_hit_rate | {_pct(summary['checkpoint_event_hit_rate'])} |",
        f"| false_accept_count | {summary['false_accept_count']} |",
        f"| false_reject_count | {summary['false_reject_count']} |",
        "",
        "## Case Analysis",
        "",
        "| Case | Expected Status | Actual Status | Trigger | Passed |",
        "| --- | --- | --- | --- | ---: |",
    ]
    for case_id in (
        "clean_resume",
        "partial_stale_single_file_change",
        "workspace_mismatch_many_files",
        "workspace_mismatch_repo_root_changed",
        "context_reduction_checkpoint",
    ):
        row = next((item for item in rows if item["case_id"] == case_id), None)
        if row:
            lines.append(
                f"| {row['case_id']} | {row['expected_resume_status']} | {row['actual_resume_status']} | "
                f"{row['checkpoint_trigger'] or '-'} | {str(row['passed']).lower()} |"
            )

    lines.extend(["", "## False Accept / False Reject", ""])
    false_rows = [row for row in rows if row["false_accept"] or row["false_reject"]]
    if false_rows:
        lines.extend(["| Case | False Accept | False Reject | Reason |", "| --- | ---: | ---: | --- |"])
        for row in false_rows:
            lines.append(
                f"| {row['case_id']} | {str(row['false_accept']).lower()} | "
                f"{str(row['false_reject']).lower()} | {row['failure_reason'] or '-'} |"
            )
    else:
        lines.append("No false accept or false reject cases were recorded in this run.")

    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- This is not production-grade transactional recovery.",
            "- It cannot guarantee that all file semantics are unchanged.",
            "- It is only a recovery guard based on the current project's metadata and workspace state.",
            "- It is not equivalent to distributed task recovery.",
            "",
            "## Resume Bullet Candidate",
            "",
            "- Built a reproducible offline Checkpoint/Resume benchmark for Pure that measures resume status accuracy, workspace/runtime identity mismatch detection, schema mismatch detection, and checkpoint trace event coverage from FakeModelClient runs.",
            "",
            "## Reproduction Command",
            "",
            "```bash",
            "python benchmarks/pure/checkpoint_resume/run_checkpoint_resume_benchmark.py",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _ratio(numerator: int, denominator: int) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _pct(value: float) -> str:
    return f"{float(value) * 100:.2f}%"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Pure's offline Checkpoint/Resume benchmark.")
    parser.add_argument("--cases", default=str(DEFAULT_CASES_PATH), help="Path to checkpoint/resume cases JSON.")
    parser.add_argument("--results", default=str(DEFAULT_RESULTS_PATH), help="Path to write results.json.")
    parser.add_argument("--per-case", default=str(DEFAULT_JSONL_PATH), help="Path to write per_case_results.jsonl.")
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY_PATH), help="Path to write summary.md.")
    parser.add_argument("--workspace-root", default=None, help="Optional directory for generated fixture workspaces.")
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
