import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from pure.core.models import FakeModelClient, OpenAICompatibleModelClient
from pure.core.runtime import PureRuntime
from pure.core.workspace import WorkspaceContext
from pure.server.state import RuntimeService


MODEL_ENV_NAMES = (
    "PURE_MODEL_PROVIDER",
    "PURE_MODEL_NAME",
    "PURE_API_KEY",
    "PURE_BASE_URL",
    "PURE_OPENAI_MODEL",
    "OPENAI_MODEL",
    "PURE_OPENAI_API_KEY",
    "OPENAI_API_KEY",
    "PURE_OPENAI_BASE_URL",
    "PURE_OPENAI_API_BASE",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
)


def clear_model_env(monkeypatch):
    for name in MODEL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_server_model_client_dry_run_uses_fake(monkeypatch):
    clear_model_env(monkeypatch)
    service = RuntimeService()

    model_client = service._model_client({}, dry_run=True)

    assert isinstance(model_client, FakeModelClient)


def test_server_model_client_openai_compatible_from_environment(monkeypatch):
    clear_model_env(monkeypatch)
    monkeypatch.setenv("PURE_MODEL_PROVIDER", "openai-compatible")
    monkeypatch.setenv("PURE_MODEL_NAME", "gpt-test")
    monkeypatch.setenv("PURE_API_KEY", "test-key")
    monkeypatch.setenv("PURE_BASE_URL", "https://api.example.test/v1")
    service = RuntimeService()

    model_client = service._model_client({}, dry_run=False)

    assert isinstance(model_client, OpenAICompatibleModelClient)
    assert model_client.model == "gpt-test"
    assert model_client.api_key == "test-key"
    assert model_client.base_url == "https://api.example.test/v1"


def test_server_model_client_missing_api_key_is_clear(monkeypatch):
    clear_model_env(monkeypatch)
    monkeypatch.setenv("PURE_MODEL_PROVIDER", "openai-compatible")
    monkeypatch.setenv("PURE_MODEL_NAME", "gpt-test")
    monkeypatch.setenv("PURE_BASE_URL", "https://api.example.test/v1")
    service = RuntimeService()

    with pytest.raises(HTTPException) as exc_info:
        service._model_client({}, dry_run=False)

    assert exc_info.value.status_code == 400
    assert "PURE_API_KEY" in str(exc_info.value.detail)
    assert "dry_run=true" in str(exc_info.value.detail)


def checkpoint_case(tmp_path, runtime_identity):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    task = SimpleNamespace(project=SimpleNamespace(root_path=str(tmp_path)))
    checkpoint = SimpleNamespace(
        schema_version=PureRuntime.CHECKPOINT_SCHEMA_VERSION,
        workspace_hash=WorkspaceContext.build(tmp_path).fingerprint(),
        runtime_metadata=json.dumps({"runtime_identity": runtime_identity}),
    )
    return task, checkpoint


def test_checkpoint_resume_accepts_matching_real_runtime_identity(monkeypatch, tmp_path):
    clear_model_env(monkeypatch)
    monkeypatch.setenv("PURE_MODEL_PROVIDER", "openai-compatible")
    monkeypatch.setenv("PURE_MODEL_NAME", "gpt-test")
    monkeypatch.setenv("PURE_API_KEY", "test-key")
    monkeypatch.setenv("PURE_BASE_URL", "https://api.example.test/v1")
    service = RuntimeService()
    task, checkpoint = checkpoint_case(
        tmp_path,
        {"model_client": "OpenAICompatibleModelClient", "model": "gpt-test"},
    )

    validation = service._validate_checkpoint_for_resume(task, checkpoint, dry_run=False)

    assert validation == {"valid": True, "errors": []}


def test_checkpoint_resume_accepts_matching_dry_run_identity(monkeypatch, tmp_path):
    clear_model_env(monkeypatch)
    service = RuntimeService()
    task, checkpoint = checkpoint_case(
        tmp_path,
        {"model_client": "FakeModelClient", "model": ""},
    )

    validation = service._validate_checkpoint_for_resume(task, checkpoint, dry_run=True)

    assert validation == {"valid": True, "errors": []}


def test_checkpoint_resume_compares_current_runtime_identity(monkeypatch, tmp_path):
    clear_model_env(monkeypatch)
    service = RuntimeService()
    task, checkpoint = checkpoint_case(
        tmp_path,
        {"model_client": "OpenAICompatibleModelClient", "model": "gpt-test"},
    )

    validation = service._validate_checkpoint_for_resume(task, checkpoint, dry_run=True)

    assert validation["valid"] is False
    assert "runtime identity mismatch: model_client" in validation["errors"]


def test_legacy_scripts_import_current_metrics_module():
    for script in (
        "collect_resume_metrics.py",
        "run_provider_experiments.py",
        "run_large_scale_experiments.py",
    ):
        path = Path("scripts") / script
        spec = importlib.util.spec_from_file_location(f"test_import_{script.replace('.', '_')}", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
