from __future__ import annotations

import hashlib
import json
import math
import os
import re
from http.client import RemoteDisconnected
from abc import ABC, abstractmethod
import urllib.error
import urllib.request


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_\-\u4e00-\u9fff]+")
DEFAULT_EMBEDDING_BASE_URL = "https://api.openai.com/v1"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class FakeEmbeddingProvider(EmbeddingProvider):
    """Deterministic local embeddings for tests and dry runs."""

    def __init__(self, dimensions: int = 64):
        self.dimensions = max(8, int(dimensions))

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = TOKEN_PATTERN.findall(str(text).lower())
        if not tokens:
            return vector
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """Embedding client for OpenAI-compatible `/v1/embeddings` APIs."""

    def __init__(
        self,
        model: str = DEFAULT_EMBEDDING_MODEL,
        api_key: str | None = None,
        base_url: str = DEFAULT_EMBEDDING_BASE_URL,
        timeout: int = 60,
    ):
        self.model = str(model or DEFAULT_EMBEDDING_MODEL)
        self.api_key = api_key
        self.base_url = _normalize_versioned_base_url(base_url or DEFAULT_EMBEDDING_BASE_URL)
        self.timeout = int(timeout)
        if not self.api_key:
            raise ValueError("PURE_EMBEDDING_API_KEY is required when PURE_EMBEDDING_PROVIDER uses a real API")

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {
            "model": self.model,
            "input": [str(text) for text in texts],
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "pure/0.1",
            "Authorization": f"Bearer {self.api_key}",
        }
        request = urllib.request.Request(
            self.base_url + "/embeddings",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Embedding request failed with HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, RemoteDisconnected) as exc:
            raise RuntimeError(
                "Could not reach the embedding backend.\n"
                f"Base URL: {self.base_url}\n"
                f"Model: {self.model}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("Embedding backend returned non-JSON content") from exc

        if data.get("error"):
            raise RuntimeError(f"Embedding backend error: {data['error']}")
        items = sorted(data.get("data", []) or [], key=lambda item: int(item.get("index", 0)))
        if len(items) != len(texts):
            raise RuntimeError(f"Embedding backend returned {len(items)} vectors for {len(texts)} texts")
        vectors = []
        for item in items:
            embedding = item.get("embedding")
            if not isinstance(embedding, list) or not embedding:
                raise RuntimeError("Embedding backend returned an invalid embedding vector")
            vectors.append([float(value) for value in embedding])
        return vectors


GenericAPIEmbeddingProvider = OpenAICompatibleEmbeddingProvider


class ConfigurableEmbeddingProvider(EmbeddingProvider):
    """Provider selector that defaults to fake embeddings unless configured otherwise."""

    def __init__(self, provider: EmbeddingProvider | None = None):
        self.provider = provider or self._from_environment()

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.provider.embed(texts)

    @staticmethod
    def _from_environment() -> EmbeddingProvider:
        provider_name = os.environ.get("PURE_EMBEDDING_PROVIDER", "fake").strip().lower()
        if provider_name in {"", "fake", "mock"}:
            return FakeEmbeddingProvider()
        if provider_name in {"openai", "openai-compatible", "generic", "api"}:
            return OpenAICompatibleEmbeddingProvider(
                model=os.environ.get("PURE_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
                api_key=os.environ.get("PURE_EMBEDDING_API_KEY"),
                base_url=os.environ.get("PURE_EMBEDDING_BASE_URL", DEFAULT_EMBEDDING_BASE_URL),
            )
        raise ValueError(f"unsupported embedding provider: {provider_name}")


def _normalize_versioned_base_url(base_url: str) -> str:
    base = str(base_url).rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base
