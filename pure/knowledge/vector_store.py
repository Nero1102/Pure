from __future__ import annotations

import json
import math
import os
from abc import ABC, abstractmethod
from dataclasses import asdict
from pathlib import Path

from .splitter import Chunk


class VectorStore(ABC):
    @abstractmethod
    def add(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        raise NotImplementedError

    @abstractmethod
    def search(self, embedding: list[float], top_k: int = 5) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def clear(self) -> None:
        raise NotImplementedError


class InMemoryVectorStore(VectorStore):
    def __init__(self, persist_path: str | Path | None = None):
        self.persist_path = Path(persist_path) if persist_path else None
        self.records: list[dict] = []
        self.load()

    def add(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings length mismatch")
        for chunk, embedding in zip(chunks, embeddings):
            self.records.append({"chunk": asdict(chunk), "embedding": list(embedding)})
        self.save()

    def search(self, embedding: list[float], top_k: int = 5) -> list[dict]:
        scored = []
        for record in self.records:
            score = _cosine(embedding, record["embedding"])
            chunk = record["chunk"]
            scored.append(
                {
                    "content": chunk["content"],
                    "source": chunk["source"],
                    "score": score,
                    "metadata": dict(chunk.get("metadata", {}) or {}),
                }
            )
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[: max(0, int(top_k))]

    def clear(self) -> None:
        self.records = []
        self.save()

    def save(self) -> None:
        if self.persist_path is None:
            return
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        self.persist_path.write_text(json.dumps({"records": self.records}, ensure_ascii=True, sort_keys=True), encoding="utf-8")

    def load(self) -> None:
        if self.persist_path is None or not self.persist_path.exists():
            return
        try:
            payload = json.loads(self.persist_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self.records = []
            return
        self.records = list(payload.get("records", []) or [])


class FaissVectorStore(VectorStore):
    """Optional FAISS-backed vector store with sidecar chunk metadata."""

    SUPPORTED_METRICS = {"cosine", "inner_product", "l2"}

    def __init__(self, persist_path: str | Path | None = None, metric: str = "cosine"):
        self.faiss, self.np = _require_faiss()
        metric_name = str(metric or "cosine").strip().lower()
        if metric_name not in self.SUPPORTED_METRICS:
            raise ValueError(f"unsupported FAISS metric: {metric}")
        self.metric = metric_name
        self.index_path, self.metadata_path = _faiss_paths(persist_path)
        self.index = None
        self.dimension: int | None = None
        self.records: list[dict] = []
        self.load()

    def add(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings length mismatch")
        if not chunks:
            return

        matrix = self._matrix(embeddings)
        if self.dimension is None:
            self.dimension = int(matrix.shape[1])
        if matrix.shape[1] != self.dimension:
            raise ValueError(f"embedding dimension mismatch: expected {self.dimension}, got {matrix.shape[1]}")
        if self.index is None:
            self.index = self._new_index(self.dimension)

        self.index.add(self._search_matrix(matrix))
        self.records.extend({"chunk": asdict(chunk)} for chunk in chunks)
        self.save()

    def search(self, embedding: list[float], top_k: int = 5) -> list[dict]:
        if self.index is None or int(getattr(self.index, "ntotal", 0)) == 0:
            return []
        limit = min(max(0, int(top_k)), int(self.index.ntotal))
        if limit <= 0:
            return []

        query = self._matrix([embedding], expected_dimension=self.dimension)
        scores, indexes = self.index.search(self._search_matrix(query), limit)
        matches = []
        for score, index in zip(scores[0], indexes[0]):
            row_id = int(index)
            if row_id < 0 or row_id >= len(self.records):
                continue
            chunk = self.records[row_id].get("chunk", {})
            matches.append(
                {
                    "content": chunk.get("content", ""),
                    "source": chunk.get("source", ""),
                    "score": self._score(float(score)),
                    "metadata": dict(chunk.get("metadata", {}) or {}),
                }
            )
        return matches

    def clear(self) -> None:
        self.index = None
        self.dimension = None
        self.records = []
        self.save()

    def save(self) -> None:
        if self.index_path is None or self.metadata_path is None:
            return
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        if self.index is None:
            if self.index_path.exists():
                self.index_path.unlink()
        else:
            self.faiss.write_index(self.index, str(self.index_path))
        payload = {
            "metric": self.metric,
            "dimension": self.dimension,
            "records": self.records,
        }
        self.metadata_path.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")

    def load(self) -> None:
        if self.metadata_path is not None and self.metadata_path.exists():
            try:
                payload = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
            stored_metric = str(payload.get("metric") or self.metric).strip().lower()
            if stored_metric in self.SUPPORTED_METRICS:
                self.metric = stored_metric
            self.dimension = payload.get("dimension")
            if self.dimension is not None:
                self.dimension = int(self.dimension)
            self.records = list(payload.get("records", []) or [])

        if self.index_path is None or not self.index_path.exists():
            self.index = None
            self.dimension = None
            self.records = []
            return
        self.index = self.faiss.read_index(str(self.index_path))
        self.dimension = int(self.index.d)
        if len(self.records) != int(self.index.ntotal):
            self.records = self.records[: int(self.index.ntotal)]

    def _new_index(self, dimension: int):
        if self.metric == "l2":
            return self.faiss.IndexFlatL2(int(dimension))
        return self.faiss.IndexFlatIP(int(dimension))

    def _matrix(self, embeddings: list[list[float]], expected_dimension: int | None = None):
        matrix = self.np.asarray(embeddings, dtype="float32")
        if matrix.ndim != 2 or matrix.shape[0] != len(embeddings):
            raise ValueError("embeddings must be a two-dimensional list of floats")
        if matrix.shape[1] == 0:
            raise ValueError("embeddings must not be empty")
        if expected_dimension is not None and matrix.shape[1] != expected_dimension:
            raise ValueError(f"embedding dimension mismatch: expected {expected_dimension}, got {matrix.shape[1]}")
        if not self.np.isfinite(matrix).all():
            raise ValueError("embeddings must contain only finite numbers")
        return self.np.ascontiguousarray(matrix, dtype="float32")

    def _search_matrix(self, matrix):
        matrix = self.np.array(matrix, dtype="float32", copy=True)
        if self.metric == "cosine":
            self.faiss.normalize_L2(matrix)
        return matrix

    def _score(self, raw_score: float) -> float:
        if self.metric == "l2":
            return -raw_score
        return raw_score


def vector_store_from_environment(root: str | Path) -> VectorStore:
    store_name = os.environ.get("PURE_VECTOR_STORE", "inmemory").strip().lower()
    knowledge_dir = Path(root).resolve() / ".pure" / "knowledge"
    if store_name in {"", "fake", "inmemory", "memory"}:
        return InMemoryVectorStore(knowledge_dir / "index.json")
    if store_name == "faiss":
        return FaissVectorStore(knowledge_dir / "index.faiss")
    raise ValueError(f"unsupported vector store: {store_name}")


def _faiss_paths(persist_path: str | Path | None) -> tuple[Path | None, Path | None]:
    if persist_path is None:
        return None, None
    path = Path(persist_path)
    if path.suffix:
        return path, path.with_suffix(".metadata.json")
    return path / "index.faiss", path / "metadata.json"


def _require_faiss():
    try:
        import faiss
        import numpy
    except ImportError as exc:
        raise ImportError(
            "FAISS vector store requires optional dependencies `faiss` and `numpy`. "
            "Install `faiss-cpu` or use the default InMemoryVectorStore."
        ) from exc
    return faiss, numpy


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    dot = sum(left[index] * right[index] for index in range(size))
    left_norm = math.sqrt(sum(value * value for value in left[:size])) or 1.0
    right_norm = math.sqrt(sum(value * value for value in right[:size])) or 1.0
    return dot / (left_norm * right_norm)
