from pathlib import Path


def test_api_docs_include_eval_endpoints():
    api_doc = Path("docs/api.md").read_text(encoding="utf-8")
    evaluator_doc = Path("docs/evaluator.md").read_text(encoding="utf-8")

    assert "POST /eval/run" in api_doc
    assert "GET /eval/{eval_id}/report" in api_doc
    assert "dry_run=true" in evaluator_doc


def test_docker_compose_declares_api_and_db_without_redis():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "api:" in compose
    assert "db:" in compose
    assert "redis" not in compose.lower()
    assert "ENTRYPOINT" in dockerfile
    entrypoint = Path("docker-entrypoint.sh").read_text(encoding="utf-8")
    assert "pure.server.main:app" in entrypoint


def test_readme_commands_cover_required_surfaces():
    readme = Path("README.md").read_text(encoding="utf-8")

    for fragment in [
        "python -m pure --dry-run",
        "uvicorn pure.server.main:app --reload",
        "python -m pure.db.init_db",
        "docker compose up --build",
        "/eval/run",
    ]:
        assert fragment in readme
