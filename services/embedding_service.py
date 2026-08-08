from sentence_transformers import SentenceTransformer

_embedder = None

def get_embedding_model() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        try:
            # Try to load from local cache first to prevent blocking network requests
            print("Loading embedding model from local cache...")
            _embedder = SentenceTransformer("all-MiniLM-L6-v2", local_files_only=True)
        except Exception:
            # Fall back to downloading if not cached
            print("Local cache not found. Downloading embedding model...")
            _embedder = SentenceTransformer("all-MiniLM-L6-v2")
    return _embedder
