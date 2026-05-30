# Knowledge

Knowledge is project context augmentation for Runtime prompts.

## Pipeline

```text
document
  -> load
  -> split
  -> embed
  -> store
  -> retrieve
  -> knowledge_context prompt section
```

Supported sources include markdown, text files, README files, `docs/*`, and run report summaries.

## Persistence

The default vector index is stored at:

```text
.pure/knowledge/index.json
```

Tests and dry runs use deterministic fake embeddings by default. Set `PURE_EMBEDDING_PROVIDER=fake` for the same behavior explicitly.

## Vector Stores

Knowledge defaults to the JSON-backed in-memory vector store so local development, dry runs, and default CI do not require native vector search libraries.

FAISS is an optional enhancement backend. Enable it with:

```bash
pip install -e ".[faiss]"
set PURE_VECTOR_STORE=faiss
```

On POSIX shells, use `export PURE_VECTOR_STORE=faiss` instead of `set`.

When enabled, FAISS stores:

- `.pure/knowledge/index.faiss`
- `.pure/knowledge/index.metadata.json`

The metadata sidecar maps FAISS row ids back to chunk content, source, and metadata.

## Embeddings

The default embedding provider is fake and deterministic:

```bash
set PURE_EMBEDDING_PROVIDER=fake
```

To use a real OpenAI-compatible embedding endpoint:

```bash
set PURE_EMBEDDING_PROVIDER=openai-compatible
set PURE_EMBEDDING_MODEL=text-embedding-3-small
set PURE_EMBEDDING_API_KEY=sk-...
set PURE_EMBEDDING_BASE_URL=https://api.openai.com/v1
```

On POSIX shells, use `export` instead of `set`.

## Verification

Default tests intentionally do not force FAISS installation:

```bash
pytest
```

To prove the optional FAISS backend is usable in the current environment, install the optional dependency and run the FAISS-specific tests:

```bash
pip install -e ".[faiss]"
pytest tests/test_knowledge.py -k "faiss" -vv
```

The FAISS command must execute the selected tests instead of skipping them. The adapter verification covers initialization, adding chunks and embeddings, top-k search, index save/load, post-load search, and metadata mapping.

It is appropriate to describe Pure as supporting an optional FAISS backend only after the FAISS adapter test command passes in the target environment.

## CI Recommendation

If CI is added, use two jobs:

- `default-test`: run `pytest`
- `faiss-test`: run `pip install -e ".[faiss]"` and `pytest tests/test_knowledge.py -k "faiss" -vv`

## Boundary

Knowledge does not execute tasks and does not replace the Runtime. It only supplies bounded context to the prompt and records selected sources in trace/report artifacts.
