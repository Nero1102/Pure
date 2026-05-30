from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .embeddings import ConfigurableEmbeddingProvider, EmbeddingProvider
from .loaders import Document, load_document, load_project_documents
from .splitter import split_documents
from .vector_store import VectorStore, vector_store_from_environment


@dataclass
class KnowledgeResult:
    content: str
    source: str
    score: float
    metadata: dict = field(default_factory=dict)


class KnowledgeService:
    def __init__(
        self,
        root: str | Path,
        embedding_provider: EmbeddingProvider | None = None,
        vector_store: VectorStore | None = None,
        chunk_size: int = 900,
        chunk_overlap: int = 120,
    ):
        self.root = Path(root).resolve()
        self.embedding_provider = embedding_provider or ConfigurableEmbeddingProvider()
        self.vector_store = vector_store or vector_store_from_environment(self.root)
        self.chunk_size = int(chunk_size)
        self.chunk_overlap = int(chunk_overlap)

    def add_documents(self, documents: list[Document]) -> dict:
        chunks = split_documents(documents, chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap)
        embeddings = self.embedding_provider.embed([chunk.content for chunk in chunks])
        self.vector_store.add(chunks, embeddings)
        return {"document_count": len(documents), "chunk_count": len(chunks)}

    def add_paths(self, paths: list[str]) -> dict:
        documents = [load_document(path, root=self.root) for path in paths]
        return self.add_documents(documents)

    def index_project(self, paths: list[str] | None = None, reset: bool = True) -> dict:
        documents = load_project_documents(self.root, paths=paths)
        if reset:
            self.vector_store.clear()
        return self.add_documents(documents)

    def retrieve(self, query: str, top_k: int = 5) -> list[KnowledgeResult]:
        query_embedding = self.embedding_provider.embed([query])[0]
        matches = self.vector_store.search(query_embedding, top_k=top_k)
        return [
            KnowledgeResult(
                content=str(match.get("content", "")),
                source=str(match.get("source", "")),
                score=float(match.get("score", 0.0) or 0.0),
                metadata=dict(match.get("metadata", {}) or {}),
            )
            for match in matches
        ]

    def retrieve_for_context(self, query: str, top_k: int = 5, budget_chars: int = 1400) -> tuple[str, list[dict]]:
        results = self.retrieve(query, top_k=top_k)
        sources = []
        lines = ["Knowledge context:"]
        remaining = max(0, int(budget_chars) - len(lines[0]) - 1)
        for result in results:
            if remaining <= 0:
                break
            prefix = f"- {result.source} ({result.score:.3f}): "
            content_budget = max(0, remaining - len(prefix) - 1)
            if content_budget <= 0:
                break
            content = result.content.strip().replace("\n", " ")
            if len(content) > content_budget:
                content = content[: max(0, content_budget - 3)] + "..."
            line = prefix + content
            lines.append(line)
            remaining -= len(line) + 1
            sources.append(
                {
                    "source": result.source,
                    "score": result.score,
                    "metadata": result.metadata,
                    "content_chars": len(result.content),
                }
            )
        if not sources:
            lines.append("- none")
        return "\n".join(lines), sources
