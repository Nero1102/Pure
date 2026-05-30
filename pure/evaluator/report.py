import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_eval_report(
    *,
    eval_id: str,
    project_path: str,
    cases_path: str,
    dry_run: bool,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
):
    return {
        "schema_version": 1,
        "eval_id": eval_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_path": str(Path(project_path).resolve()),
        "cases_path": str(Path(cases_path).resolve()),
        "dry_run": bool(dry_run),
        "summary": summary,
        "rows": rows,
    }


def write_eval_report(report: dict[str, Any], output_dir: str | Path):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "report.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    return path
