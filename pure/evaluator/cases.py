import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REQUIRED_CASE_FIELDS = (
    "id",
    "task",
    "expected_tools",
    "forbidden_tools",
    "success_keywords",
    "max_steps",
    "expected_trace_events",
)


@dataclass(frozen=True)
class EvalCase:
    id: str
    task: str
    expected_tools: list[str]
    forbidden_tools: list[str]
    success_keywords: list[str]
    max_steps: int
    expected_trace_events: list[Any]
    mock_outputs: list[str]
    runtime_config: dict[str, Any]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]):
        missing = [key for key in REQUIRED_CASE_FIELDS if key not in data]
        if missing:
            raise ValueError(f"eval case is missing required fields: {', '.join(missing)}")
        case_id = str(data["id"]).strip()
        task = str(data["task"]).strip()
        if not case_id:
            raise ValueError("eval case id must not be empty")
        if not task:
            raise ValueError(f"eval case {case_id} task must not be empty")
        max_steps = int(data["max_steps"])
        if max_steps < 1:
            raise ValueError(f"eval case {case_id} max_steps must be positive")
        return cls(
            id=case_id,
            task=task,
            expected_tools=_string_list(data["expected_tools"], f"{case_id}.expected_tools"),
            forbidden_tools=_string_list(data["forbidden_tools"], f"{case_id}.forbidden_tools"),
            success_keywords=_string_list(data["success_keywords"], f"{case_id}.success_keywords"),
            max_steps=max_steps,
            expected_trace_events=_trace_event_expectations(
                data["expected_trace_events"],
                f"{case_id}.expected_trace_events",
            ),
            mock_outputs=_string_list(data.get("mock_outputs", []), f"{case_id}.mock_outputs"),
            runtime_config=_dict_value(data.get("runtime_config", {}), f"{case_id}.runtime_config"),
        )


def _string_list(value: Any, field_name: str):
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    items = [str(item).strip() for item in value]
    if any(not item for item in items):
        raise ValueError(f"{field_name} must not contain empty values")
    return items


def _trace_event_expectations(value: Any, field_name: str):
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    expectations = []
    for index, item in enumerate(value):
        item_name = f"{field_name}[{index}]"
        if isinstance(item, str):
            event_type = item.strip()
            if not event_type:
                raise ValueError(f"{item_name} must not be empty")
            expectations.append(event_type)
            continue
        if isinstance(item, dict):
            expectation = dict(item)
            event_type = str(expectation.get("event_type", expectation.get("event", ""))).strip()
            if not event_type:
                raise ValueError(f"{item_name} must include event_type")
            expectation["event_type"] = event_type
            if "payload" in expectation and not isinstance(expectation["payload"], dict):
                raise ValueError(f"{item_name}.payload must be a dict")
            expectations.append(expectation)
            continue
        raise ValueError(f"{item_name} must be a string or object")
    return expectations


def _dict_value(value: Any, field_name: str):
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a dict")
    return dict(value)


def validate_eval_cases(data: Any):
    if isinstance(data, dict):
        raw_cases = data.get("cases")
    else:
        raw_cases = data
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("eval_cases.json must contain a non-empty cases list")
    cases = [EvalCase.from_mapping(item) for item in raw_cases]
    seen = set()
    for case in cases:
        if case.id in seen:
            raise ValueError(f"duplicate eval case id: {case.id}")
        seen.add(case.id)
    return cases


def load_eval_cases(path: str | Path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_eval_cases(payload)
