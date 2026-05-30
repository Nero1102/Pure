import json

import pytest
from fastapi.testclient import TestClient

from pure import FakeModelClient, MiniAgent, SessionStore, WorkspaceContext
from pure.core.context_manager import ContextManager
from pure.knowledge import (
    ConfigurableEmbeddingProvider,
    FakeEmbeddingProvider,
    FaissVectorStore,
    InMemoryVectorStore,
    KnowledgeService,
    OpenAICompatibleEmbeddingProvider,
    load_project_documents,
    split_documents,
)
from pure.server.main import app
from pure.server.state import runtime_service


@pytest.fixture(autouse=True)
def clear_knowledge_env(monkeypatch):
    for name in (
        "PURE_VECTOR_STORE",
        "PURE_EMBEDDING_PROVIDER",
        "PURE_EMBEDDING_MODEL",
        "PURE_EMBEDDING_API_KEY",
        "PURE_EMBEDDING_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_document_loader_supports_readme_docs_and_report_summary(tmp_path):
    (tmp_path / "README.md").write_text("# Demo\n\nDeploy key is red.\n", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.txt").write_text("Use pytest for verification.\n", encoding="utf-8")
    report_dir = tmp_path / ".pure" / "runs" / "run_1"
    report_dir.mkdir(parents=True)
    (report_dir / "report.json").write_text(
        json.dumps({"run_id": "run_1", "status": "completed", "final_answer": "done"}),
        encoding="utf-8",
    )

    documents = load_project_documents(tmp_path, paths=["README.md", "docs", ".pure/runs/run_1/report.json"])

    assert {document.source for document in documents} == {"README.md", "docs/guide.txt", ".pure/runs/run_1/report.json"}
    assert any("Run report summary" in document.content for document in documents)


def test_splitter_chunks_long_documents_under_budget(tmp_path):
    (tmp_path / "README.md").write_text(("alpha " * 200) + "\n\n" + ("beta " * 200), encoding="utf-8")
    documents = load_project_documents(tmp_path, paths=["README.md"])

    chunks = split_documents(documents, chunk_size=120, chunk_overlap=20)

    assert len(chunks) > 2
    assert all(len(chunk.content) <= 120 for chunk in chunks)
    assert all(chunk.source == "README.md" for chunk in chunks)


def test_fake_embedding_is_deterministic_and_normalized():
    provider = FakeEmbeddingProvider(dimensions=16)

    first = provider.embed(["alpha beta"])[0]
    second = provider.embed(["alpha beta"])[0]

    assert first == second
    assert any(value != 0 for value in first)


def test_configurable_embedding_provider_defaults_to_fake():
    provider = ConfigurableEmbeddingProvider()

    assert isinstance(provider.provider, FakeEmbeddingProvider)


def test_openai_compatible_embedding_provider_uses_embeddings_endpoint(monkeypatch):
    requests = []

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "data": [
                        {"index": 1, "embedding": [0.0, 1.0]},
                        {"index": 0, "embedding": [1.0, 0.0]},
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return DummyResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = OpenAICompatibleEmbeddingProvider(
        model="embedding-test",
        api_key="test-key",
        base_url="https://api.example.test",
    )

    vectors = provider.embed(["alpha", "beta"])

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    request, timeout = requests[0]
    assert request.full_url == "https://api.example.test/v1/embeddings"
    assert timeout == 60
    assert json.loads(request.data.decode("utf-8")) == {"model": "embedding-test", "input": ["alpha", "beta"]}
    assert request.get_header("Authorization") == "Bearer test-key"


def test_configurable_embedding_provider_supports_openai_compatible_env(monkeypatch):
    monkeypatch.setenv("PURE_EMBEDDING_PROVIDER", "openai-compatible")
    monkeypatch.setenv("PURE_EMBEDDING_MODEL", "embedding-test")
    monkeypatch.setenv("PURE_EMBEDDING_API_KEY", "test-key")
    monkeypatch.setenv("PURE_EMBEDDING_BASE_URL", "https://api.example.test/v1")

    provider = ConfigurableEmbeddingProvider()

    assert isinstance(provider.provider, OpenAICompatibleEmbeddingProvider)
    assert provider.provider.model == "embedding-test"
    assert provider.provider.api_key == "test-key"
    assert provider.provider.base_url == "https://api.example.test/v1"


def test_vector_retrieval_returns_best_matching_chunk(tmp_path):
    (tmp_path / "README.md").write_text("Alpha deployment notes.\n\nSQLite task metadata.\n", encoding="utf-8")
    service = KnowledgeService(tmp_path)
    service.index_project(paths=["README.md"])

    results = service.retrieve("sqlite metadata", top_k=1)

    assert len(results) == 1
    assert results[0].source == "README.md"
    assert "SQLite" in results[0].content


def test_faiss_vector_store_persists_and_searches(tmp_path):
    pytest.importorskip("faiss")
    pytest.importorskip("numpy")
    from pure.knowledge import Chunk

    index_path = tmp_path / "index.faiss"
    store = FaissVectorStore(index_path)
    chunks = [
        Chunk(content="Alpha deployment notes.", source="alpha.md", metadata={"chunk_index": 0}),
        Chunk(content="SQLite task metadata.", source="sqlite.md", metadata={"chunk_index": 0}),
    ]

    store.add(chunks, [[1.0, 0.0], [0.0, 1.0]])

    assert store.search([0.0, 1.0], top_k=1)[0]["source"] == "sqlite.md"
    assert (tmp_path / "index.metadata.json").exists()

    reloaded = FaissVectorStore(index_path)
    result = reloaded.search([1.0, 0.0], top_k=1)[0]

    assert result["source"] == "alpha.md"
    assert result["metadata"] == {"chunk_index": 0}


def test_knowledge_service_uses_faiss_when_configured(monkeypatch, tmp_path):
    pytest.importorskip("faiss")
    pytest.importorskip("numpy")
    monkeypatch.setenv("PURE_VECTOR_STORE", "faiss")

    service = KnowledgeService(tmp_path)

    assert isinstance(service.vector_store, FaissVectorStore)


def test_context_manager_applies_knowledge_token_budget(tmp_path):
    (tmp_path / "README.md").write_text("demo", encoding="utf-8")
    workspace = WorkspaceContext.build(tmp_path)
    agent = MiniAgent(FakeModelClient([]), workspace, SessionStore(tmp_path / ".pure" / "sessions"), approval_policy="auto")
    agent.knowledge_context = "Knowledge context:\n- " + ("A" * 500)
    agent.knowledge_sources = [{"source": "README.md", "score": 1.0, "metadata": {}}]

    prompt, metadata = ContextManager(
        agent,
        total_budget=360,
        section_budgets={"prefix": 80, "memory": 80, "knowledge_context": 90, "relevant_memory": 80, "history": 80},
        section_floors={"knowledge_context": 40},
    ).build("preserve me")

    assert metadata["sections"]["knowledge_context"]["rendered_chars"] <= metadata["sections"]["knowledge_context"]["budget_chars"]
    assert metadata["knowledge_context"]["selected_count"] == 1
    assert "preserve me" in prompt


def test_runtime_retrieves_knowledge_and_writes_report_sources(tmp_path):
    (tmp_path / "README.md").write_text("SQLite metadata is stored in pure.db.\n", encoding="utf-8")
    workspace = WorkspaceContext.build(tmp_path)
    agent = MiniAgent(
        FakeModelClient(["<final>done</final>"]),
        workspace,
        SessionStore(tmp_path / ".pure" / "sessions"),
        approval_policy="auto",
    )

    result = agent.ask("Where is SQLite metadata stored?")
    report = json.loads(agent.run_store.report_path(agent.current_task_state.run_id).read_text(encoding="utf-8"))
    trace = agent.run_store.trace_path(agent.current_task_state.run_id).read_text(encoding="utf-8")

    assert result == "done"
    assert report["knowledge_sources"]
    assert report["knowledge_sources"][0]["source"] == "README.md"
    assert "knowledge_retrieved" in trace


def test_knowledge_api_indexes_and_searches_project_documents(tmp_path):
    runtime_service.sessions.clear()
    runtime_service.run_to_session.clear()
    runtime_service.configure_database(f"sqlite:///{tmp_path / 'pure.db'}")
    (tmp_path / "README.md").write_text("SQLite metadata lives in pure.db.\n", encoding="utf-8")
    client = TestClient(app)
    project = client.post("/projects", json={"name": "Demo", "root_path": str(tmp_path)}).json()

    indexed = client.post("/knowledge/index", json={"project_id": project["id"], "paths": ["README.md"]})
    searched = client.post("/knowledge/search", json={"project_id": project["id"], "query": "sqlite metadata", "top_k": 1})

    assert indexed.status_code == 200
    assert indexed.json()["chunk_count"] == 1
    assert searched.status_code == 200
    results = searched.json()["results"]
    assert results[0]["source"] == "README.md"
    assert "SQLite" in results[0]["content"]
