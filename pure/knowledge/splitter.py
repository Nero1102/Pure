from __future__ import annotations

from dataclasses import dataclass, field

from .loaders import Document


@dataclass
class Chunk:
    content: str
    source: str
    metadata: dict = field(default_factory=dict)


def split_documents(documents: list[Document], chunk_size: int = 900, chunk_overlap: int = 120) -> list[Chunk]:
    chunks: list[Chunk] = []
    for document in documents:
        chunks.extend(split_document(document, chunk_size=chunk_size, chunk_overlap=chunk_overlap))
    return chunks


def split_document(document: Document, chunk_size: int = 900, chunk_overlap: int = 120) -> list[Chunk]:
    text = str(document.content or "").strip()
    if not text:
        return []
    chunk_size = max(80, int(chunk_size))
    chunk_overlap = max(0, min(int(chunk_overlap), chunk_size // 2))
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    chunks: list[Chunk] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > chunk_size:
            if current:
                chunks.append(_chunk(document, current, len(chunks)))
                current = ""
            for piece in _window_text(paragraph, chunk_size, chunk_overlap):
                chunks.append(_chunk(document, piece, len(chunks)))
            continue
        candidate = paragraph if not current else current + "\n\n" + paragraph
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            chunks.append(_chunk(document, current, len(chunks)))
            current = paragraph
    if current:
        chunks.append(_chunk(document, current, len(chunks)))
    return chunks


def _window_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    pieces = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        pieces.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - chunk_overlap, start + 1)
    return [piece for piece in pieces if piece]


def _chunk(document: Document, content: str, index: int) -> Chunk:
    metadata = dict(document.metadata or {})
    metadata["chunk_index"] = index
    return Chunk(content=content, source=document.source, metadata=metadata)
