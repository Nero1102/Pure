from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt"}
DEFAULT_DOCUMENT_PATHS = ("README", "README.md", "docs")
IGNORED_DIRS = {".git", ".pure", ".pytest_cache", "__pycache__", "node_modules", ".venv", "venv"}


@dataclass
class Document:
    content: str
    source: str
    metadata: dict = field(default_factory=dict)


def _within_root(root: Path, path: Path) -> bool:
    try:
        os.path.commonpath([str(root), str(path.resolve())])
    except ValueError:
        return False
    return os.path.commonpath([str(root), str(path.resolve())]) == str(root)


def load_document(path: str | Path, root: str | Path | None = None) -> Document:
    root_path = Path(root).resolve() if root is not None else Path(path).resolve().parent
    file_path = Path(path)
    file_path = file_path if file_path.is_absolute() else root_path / file_path
    file_path = file_path.resolve()
    if not _within_root(root_path, file_path):
        raise ValueError(f"document path escapes root: {path}")
    if not file_path.is_file():
        raise FileNotFoundError(str(file_path))

    text = file_path.read_text(encoding="utf-8", errors="replace")
    rel_source = file_path.relative_to(root_path).as_posix()
    if file_path.name == "report.json":
        text = _report_summary(text)
    return Document(
        content=text,
        source=rel_source,
        metadata={
            "path": rel_source,
            "suffix": file_path.suffix.lower(),
            "kind": _document_kind(file_path),
        },
    )


def discover_project_documents(root: str | Path, paths: list[str] | None = None) -> list[Path]:
    root_path = Path(root).resolve()
    candidates: list[Path] = []
    requested = paths or list(DEFAULT_DOCUMENT_PATHS)
    for raw in requested:
        path = Path(raw)
        path = path if path.is_absolute() else root_path / path
        path = path.resolve()
        if not _within_root(root_path, path) or not path.exists():
            continue
        if path.is_file():
            if _is_supported_file(path):
                candidates.append(path)
            continue
        for child in path.rglob("*"):
            if any(part in IGNORED_DIRS for part in child.relative_to(root_path).parts):
                continue
            if child.is_file() and _is_supported_file(child):
                candidates.append(child)
    return sorted(set(candidates))


def load_project_documents(root: str | Path, paths: list[str] | None = None) -> list[Document]:
    root_path = Path(root).resolve()
    return [load_document(path, root=root_path) for path in discover_project_documents(root_path, paths)]


def _is_supported_file(path: Path) -> bool:
    if path.name.upper().startswith("README"):
        return True
    if path.suffix.lower() in SUPPORTED_SUFFIXES:
        return True
    return path.name == "report.json"


def _document_kind(path: Path) -> str:
    if path.name == "report.json":
        return "report_summary"
    if path.name.upper().startswith("README"):
        return "readme"
    if path.suffix.lower() in {".md", ".markdown"}:
        return "markdown"
    return "text"


def _report_summary(text: str) -> str:
    try:
        report = json.loads(text)
    except json.JSONDecodeError:
        return text
    lines = ["Run report summary:"]
    for key in ("run_id", "task_id", "status", "stop_reason", "final_answer"):
        value = report.get(key)
        if value:
            lines.append(f"- {key}: {value}")
    sources = report.get("knowledge_sources") or []
    if sources:
        lines.append("- knowledge_sources: " + ", ".join(str(item.get("source", item)) for item in sources))
    return "\n".join(lines)
