from __future__ import annotations

from datetime import datetime
from typing import Any
import json


def calculate_case_metrics(case, report: dict[str, Any], events: list[dict[str, Any]], latency_ms: float):
    tool_names = _tool_names(events)
    event_types = _event_types(events)
    final_answer = str(report.get("final_answer", ""))
    success_keywords = [keyword.lower() for keyword in case.success_keywords]
    answer_lower = final_answer.lower()
    success_keyword_hits = [keyword for keyword in case.success_keywords if keyword.lower() in answer_lower]
    missing_success_keywords = [
        keyword for keyword in case.success_keywords if keyword.lower() not in answer_lower
    ]
    keyword_success = not missing_success_keywords
    status_success = str(report.get("status", "")).lower() == "completed"
    expected_hits = sum(1 for tool in case.expected_tools if tool in tool_names)
    missing_expected_tools = [tool for tool in case.expected_tools if tool not in tool_names]
    forbidden_tool_hits = [tool for tool in tool_names if tool in set(case.forbidden_tools)]
    forbidden_hits = len(forbidden_tool_hits)
    step_count = int(report.get("tool_steps", 0) or 0)
    if not step_count:
        step_count = max((int(event.get("step", 0) or 0) for event in events), default=0)
    trace_expectations = list(getattr(case, "expected_trace_events", []) or [])
    trace_event_hits, missing_trace_events = _trace_expectation_results(trace_expectations, events)
    step_budget_met = step_count <= int(case.max_steps)
    task_success = bool(status_success and keyword_success)
    failure_reasons = _failure_reasons(
        status_success=status_success,
        missing_success_keywords=missing_success_keywords,
        missing_expected_tools=missing_expected_tools,
        forbidden_tool_hits=forbidden_tool_hits,
        missing_trace_events=missing_trace_events,
        step_budget_met=step_budget_met,
        step_count=step_count,
        max_steps=int(case.max_steps),
    )
    case_passed = bool(task_success and not failure_reasons)
    tool_rejection_count = _tool_rejection_count(events)
    security_event_count = _security_event_count(events)
    repeated_tool_call_count = _repeated_tool_call_count(events, report)
    return {
        "case_passed": case_passed,
        "task_success": task_success,
        "status_success": bool(status_success),
        "keyword_success": bool(keyword_success),
        "expected_tool_hit_rate": _safe_ratio(expected_hits, len(case.expected_tools)),
        "forbidden_tool_count": forbidden_hits,
        "forbidden_tool_hits": forbidden_tool_hits,
        "missing_expected_tools": missing_expected_tools,
        "steps": step_count,
        "step_budget_met": step_budget_met,
        "latency_ms": float(latency_ms),
        "checkpoint_created": any(event.get("event_type") == "checkpoint_created" for event in events),
        "knowledge_retrieved": any(event.get("event_type") == "knowledge_retrieved" for event in events),
        "expected_trace_event_hit_rate": _safe_ratio(len(trace_event_hits), len(trace_expectations)),
        "trace_event_success": not missing_trace_events,
        "expected_trace_event_hits": trace_event_hits,
        "missing_expected_trace_events": missing_trace_events,
        "event_types": event_types,
        "tools_used": tool_names,
        "success_keyword_hits": success_keyword_hits,
        "missing_success_keywords": missing_success_keywords,
        "tool_rejection_count": tool_rejection_count,
        "security_event_count": security_event_count,
        "repeated_tool_call_count": repeated_tool_call_count,
        "failure_reasons": failure_reasons,
    }


def aggregate_metrics(rows: list[dict[str, Any]]):
    case_metrics = [row.get("metrics", {}) for row in rows]
    return {
        "case_count": len(rows),
        "case_pass_rate": _safe_ratio(sum(1 for item in case_metrics if item.get("case_passed")), len(case_metrics)),
        "task_success": _safe_ratio(sum(1 for item in case_metrics if item.get("task_success")), len(case_metrics)),
        "expected_tool_hit_rate": _average(item.get("expected_tool_hit_rate", 0.0) for item in case_metrics),
        "forbidden_tool_count": sum(int(item.get("forbidden_tool_count", 0) or 0) for item in case_metrics),
        "average_steps": _average(item.get("steps", 0) for item in case_metrics),
        "average_latency": _average(item.get("latency_ms", 0.0) for item in case_metrics),
        "checkpoint_created": _safe_ratio(sum(1 for item in case_metrics if item.get("checkpoint_created")), len(case_metrics)),
        "knowledge_retrieved": _safe_ratio(sum(1 for item in case_metrics if item.get("knowledge_retrieved")), len(case_metrics)),
        "trace_event_success": _safe_ratio(sum(1 for item in case_metrics if item.get("trace_event_success")), len(case_metrics)),
        "expected_trace_event_hit_rate": _average(
            item.get("expected_trace_event_hit_rate", 0.0) for item in case_metrics
        ),
        "step_budget_met": _safe_ratio(sum(1 for item in case_metrics if item.get("step_budget_met")), len(case_metrics)),
        "tool_rejection_count": sum(int(item.get("tool_rejection_count", 0) or 0) for item in case_metrics),
        "security_event_count": sum(int(item.get("security_event_count", 0) or 0) for item in case_metrics),
        "repeated_tool_call_count": sum(int(item.get("repeated_tool_call_count", 0) or 0) for item in case_metrics),
        "failure_reason_counts": _failure_reason_counts(rows),
    }


def infer_latency_ms(events: list[dict[str, Any]]):
    started = next((event for event in events if event.get("event_type") == "run_started"), None)
    finished = next(
        (
            event
            for event in reversed(events)
            if event.get("event_type") in {"run_completed", "run_failed", "run_cancelled"}
        ),
        None,
    )
    if started and finished:
        start_dt = _parse_datetime(started.get("timestamp"))
        end_dt = _parse_datetime(finished.get("timestamp"))
        if start_dt and end_dt:
            return max(0.0, (end_dt - start_dt).total_seconds() * 1000.0)
    return float(sum(int(event.get("latency_ms", 0) or 0) for event in events))


def _tool_names(events: list[dict[str, Any]]):
    names = []
    for event in events:
        if event.get("event_type") != "tool_executed":
            continue
        payload = event.get("payload", {}) or {}
        name = str(payload.get("name", "")).strip()
        if name:
            names.append(name)
    return names


def _event_types(events: list[dict[str, Any]]):
    return [str(event.get("event_type", "")).strip() for event in events if str(event.get("event_type", "")).strip()]


def _trace_expectation_results(expectations: list[Any], events: list[dict[str, Any]]):
    hits = []
    missing = []
    for expectation in expectations:
        label = _expectation_label(expectation)
        if any(_event_matches_expectation(event, expectation) for event in events):
            hits.append(label)
        else:
            missing.append(label)
    return hits, missing


def _event_matches_expectation(event: dict[str, Any], expectation: Any):
    if isinstance(expectation, str):
        expected_type = expectation.strip()
        return event.get("event_type") == expected_type or event.get("event") == expected_type
    if not isinstance(expectation, dict):
        return False

    expected_type = str(expectation.get("event_type", expectation.get("event", ""))).strip()
    if expected_type and event.get("event_type") != expected_type and event.get("event") != expected_type:
        return False
    for key, expected_value in expectation.items():
        if key in {"event_type", "event"}:
            continue
        if key == "payload":
            if not _mapping_contains(event.get("payload", {}) or {}, expected_value):
                return False
            continue
        if not _value_matches(event.get(key), expected_value):
            return False
    return True


def _mapping_contains(actual: Any, expected: Any):
    if not isinstance(expected, dict):
        return _value_matches(actual, expected)
    if not isinstance(actual, dict):
        return False
    for key, expected_value in expected.items():
        if key not in actual:
            return False
        if isinstance(expected_value, dict):
            if not _mapping_contains(actual.get(key), expected_value):
                return False
        elif not _value_matches(actual.get(key), expected_value):
            return False
    return True


def _value_matches(actual: Any, expected: Any):
    if isinstance(expected, str):
        return str(actual) == expected
    return actual == expected


def _expectation_label(expectation: Any):
    if isinstance(expectation, str):
        return expectation
    return json.dumps(expectation, sort_keys=True, ensure_ascii=True)


def _tool_rejection_count(events: list[dict[str, Any]]):
    rejected_statuses = {"rejected", "waiting_approval", "error", "partial_success"}
    count = 0
    for event in events:
        if event.get("event_type") != "tool_executed":
            continue
        payload = event.get("payload", {}) or {}
        if str(payload.get("tool_status", "")).strip() in rejected_statuses:
            count += 1
    return count


def _security_event_count(events: list[dict[str, Any]]):
    count = 0
    for event in events:
        if event.get("event_type") != "tool_executed":
            continue
        payload = event.get("payload", {}) or {}
        if str(payload.get("security_event_type", "")).strip():
            count += 1
    return count


def _repeated_tool_call_count(events: list[dict[str, Any]], report: dict[str, Any]):
    if "repeated_tool_call_count" in report:
        return int(report.get("repeated_tool_call_count", 0) or 0)
    repeated_events = {"repeated_tool_call_detected", "tool_rejected_repeated_call"}
    return sum(1 for event in events if event.get("event_type") in repeated_events)


def _failure_reasons(
    *,
    status_success: bool,
    missing_success_keywords: list[str],
    missing_expected_tools: list[str],
    forbidden_tool_hits: list[str],
    missing_trace_events: list[str],
    step_budget_met: bool,
    step_count: int,
    max_steps: int,
):
    reasons = []
    if not status_success:
        reasons.append("run status was not completed")
    if missing_success_keywords:
        reasons.append("missing success keywords: " + ", ".join(missing_success_keywords))
    if missing_expected_tools:
        reasons.append("missing expected tools: " + ", ".join(missing_expected_tools))
    if forbidden_tool_hits:
        reasons.append("forbidden tools used: " + ", ".join(forbidden_tool_hits))
    if missing_trace_events:
        reasons.append("missing expected trace events: " + "; ".join(missing_trace_events))
    if not step_budget_met:
        reasons.append(f"step budget exceeded: {step_count} > {max_steps}")
    return reasons


def _failure_reason_counts(rows: list[dict[str, Any]]):
    counts: dict[str, int] = {}
    for row in rows:
        for reason in row.get("failure_reasons", row.get("metrics", {}).get("failure_reasons", [])):
            reason = str(reason)
            counts[reason] = counts.get(reason, 0) + 1
    return counts


def _parse_datetime(value: Any):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _average(values):
    values = [float(value or 0.0) for value in values]
    return sum(values) / len(values) if values else 0.0


def _safe_ratio(numerator: int, denominator: int):
    return float(numerator) / float(denominator) if denominator else 0.0
