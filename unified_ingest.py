import os
import uuid
import json
import numpy as np
from services.embedding_service import get_embedding_model
from kb_docs import KB_DOCS
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY
from services.observability import setup_logger

logger = setup_logger(__name__)

def ensure_ingested():
    """
    Idempotent function that seeds both the local numpy embeddings and the remote Supabase database.
    """
    logger.info("Verifying ingestion state...")
    embedder = get_embedding_model()

    # 1. Local Numpy KB for Amara (Idempotent)
    if not os.path.exists("kb_embeddings.npy"):
        logger.info("kb_embeddings.npy not found, generating local embeddings...")
        texts = [d["text"] for d in KB_DOCS]
        kb_embeddings = embedder.encode(texts, normalize_embeddings=True)
        np.save("kb_embeddings.npy", kb_embeddings)
        logger.info(f"Embedded {len(texts)} KB docs and saved to kb_embeddings.npy")
    else:
        # Check shape to ensure it's valid, otherwise overwrite
        try:
            arr = np.load("kb_embeddings.npy")
            if len(arr) != len(KB_DOCS):
                raise ValueError("Mismatch length")
        except Exception:
            logger.info("kb_embeddings.npy is corrupted or outdated. Regenerating...")
            texts = [d["text"] for d in KB_DOCS]
            kb_embeddings = embedder.encode(texts, normalize_embeddings=True)
            np.save("kb_embeddings.npy", kb_embeddings)
            logger.info(f"Embedded {len(texts)} KB docs and saved to kb_embeddings.npy")

    # 2. Read Real Estate Methodology KB
    methodology_docs = []
    if os.path.exists("methodology_kb.json"):
        with open("methodology_kb.json", "r", encoding="utf-8") as f:
            methodology_docs = json.load(f)

    # 3. Remote Supabase KB for Real Estate (Idempotent)
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    try:
        existing_res = supabase.table("re_knowledge_base").select("section_title").execute()
        existing_titles = {row["section_title"] for row in existing_res.data}
    except Exception as e:
        logger.error(f"Failed to query Supabase: {e}")
        existing_titles = set()

    for doc in methodology_docs:
        if "section_title" not in doc or "chunk_content" not in doc:
            continue
        if doc["section_title"] not in existing_titles:
            logger.info(f"Seeding missing chunk to Supabase: {doc['section_title']}")
            vector = embedder.encode(doc["chunk_content"]).tolist()
            payload = {
                "id": str(uuid.uuid4()),
                "section_title": doc["section_title"],
                "chunk_content": doc["chunk_content"],
                "embedding": vector,
            }
            try:
                supabase.table("re_knowledge_base").insert(payload).execute()
                logger.info(f"Successfully seeded: {doc['section_title']}")
            except Exception as e:
                logger.error(f"Failed to seed {doc['section_title']}: {e}")

    # 4. Local Pre-Router Embeddings for section_titles (Idempotent)
    section_titles = [doc["section_title"] for doc in methodology_docs if "section_title" in doc]
    
    regenerate_titles = False
    if not os.path.exists("section_title_embeddings.npy") or not os.path.exists("section_titles.json"):
        regenerate_titles = True
    else:
        try:
            with open("section_titles.json", "r", encoding="utf-8") as f:
                saved_titles = json.load(f)
            if saved_titles != section_titles:
                regenerate_titles = True
        except Exception:
            regenerate_titles = True

    if regenerate_titles and section_titles:
        logger.info("Generating section_title_embeddings.npy...")
        title_embeddings = embedder.encode(section_titles, normalize_embeddings=True)
        np.save("section_title_embeddings.npy", title_embeddings)
        with open("section_titles.json", "w", encoding="utf-8") as f:
            json.dump(section_titles, f, indent=2)
        logger.info(f"Saved {len(section_titles)} section title embeddings for pre-routing.")

if __name__ == "__main__":
    ensure_ingested()
