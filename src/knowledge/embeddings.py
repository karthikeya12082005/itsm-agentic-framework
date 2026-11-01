"""Sentence embeddings with bge-small-en-v1.5 (Sentence Transformers), L2-normalised for cosine search."""
from functools import lru_cache

from src.common.config import EMBED_MODEL, QUERY_INSTRUCTION


@lru_cache(maxsize=1)
def get_model():
    from sentence_transformers import SentenceTransformer   # imported lazily (slow import)
    return SentenceTransformer(EMBED_MODEL)


def embed_documents(texts, batch_size: int = 32):
    return get_model().encode(list(texts), batch_size=batch_size, normalize_embeddings=True,
                              show_progress_bar=True)


def embed_query(text: str):
    return get_model().encode([QUERY_INSTRUCTION + text], normalize_embeddings=True)[0]


def to_pgvector(vec) -> str:
    """numpy vector -> pgvector text literal '[0.1,0.2,...]' (used with ::vector in SQL)."""
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"
