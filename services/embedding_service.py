"""SentenceTransformer embedding model singleton provider."""

from sentence_transformers import SentenceTransformer

_embedder = None


def get_embedding_model() -> SentenceTransformer:
    """Loads and caches the SentenceTransformer model from local cache with download fallback."""
    global _embedder
    if _embedder is None:
        try:
            print("Loading embedding model from local cache...")
            _embedder = SentenceTransformer("all-MiniLM-L6-v2", local_files_only=True)
        except Exception:
            print("Local cache not found. Downloading embedding model...")
            _embedder = SentenceTransformer("all-MiniLM-L6-v2")
    return _embedder
