"""Local Hugging Face embeddings via fastembed (ONNX, no API key)."""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache

from fastembed import TextEmbedding

from .settings import get_settings


def _as_floats(vec: Iterable[float]) -> list[float]:
    return [float(x) for x in vec]


@lru_cache
def get_embeddings() -> TextEmbedding:
    return TextEmbedding(model_name=get_settings().embedding_model)


def embed_query(text: str) -> list[float]:
    model = get_embeddings()
    query_embed = getattr(model, "query_embed", None)
    if query_embed is not None:
        return _as_floats(next(query_embed(text)))
    return _as_floats(next(model.embed([f"query: {text}"])))


def embed_documents(texts: list[str]) -> list[list[float]]:
    model = get_embeddings()
    passage_embed = getattr(model, "passage_embed", None)
    if passage_embed is not None:
        return [_as_floats(vec) for vec in passage_embed(texts)]
    return [_as_floats(vec) for vec in model.embed([f"passage: {t}" for t in texts])]
