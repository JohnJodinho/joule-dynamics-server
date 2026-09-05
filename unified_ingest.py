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

def reconcile_real_estate_kb(supabase: Client, embedder, methodology_docs: list) -> dict:
    """
    Performs true 3-way idempotent reconciliation of methodology_kb.json against Supabase re_knowledge_base.
    - Inserts new documents
    - Updates modified documents and recomputes embeddings
    - Deletes orphaned/deprecated documents
    """
    stats = {"added": 0, "updated": 0, "deleted": 0, "unchanged": 0}
    
    # 1. Fetch current remote state
    try:
        remote_res = supabase.table("re_knowledge_base").select("id, section_title, chunk_content").execute()
        remote_data = {row["section_title"]: row for row in (remote_res.data or [])}
    except Exception as e:
        logger.error(f"Failed to fetch existing records from Supabase re_knowledge_base: {e}")
        remote_data = {}

    local_titles = set()

    # 2. Reconcile additions and modifications
    for doc in methodology_docs:
        title = doc.get("section_title")
        content = doc.get("chunk_content")
        if not title or not content:
            continue
        
        local_titles.add(title)
        deterministic_id = str(uuid.uuid5(uuid.NAMESPACE_URL, title))

        if title not in remote_data:
            # NEW: Insert
            logger.info(f"Adding new chunk: {title}")
            vector = embedder.encode(content).tolist()
            payload = {
                "id": deterministic_id,
                "section_title": title,
                "chunk_content": content,
                "embedding": vector,
            }
            try:
                supabase.table("re_knowledge_base").insert(payload).execute()
                stats["added"] += 1
            except Exception as e:
                logger.error(f"Failed to insert {title}: {e}")
        else:
            # Check for modification
            remote_row = remote_data[title]
            remote_content = remote_row.get("chunk_content", "")
            if remote_content.strip() != content.strip():
                logger.info(f"Updating modified chunk: {title}")
                vector = embedder.encode(content).tolist()
                payload = {
                    "chunk_content": content,
                    "embedding": vector,
                }
                try:
                    supabase.table("re_knowledge_base").update(payload).eq("section_title", title).execute()
                    stats["updated"] += 1
                except Exception as e:
                    logger.error(f"Failed to update {title}: {e}")
            else:
                stats["unchanged"] += 1

    # 3. Reconcile deletions (orphans in remote DB)
    for remote_title, remote_row in remote_data.items():
        if remote_title not in local_titles:
            logger.info(f"Deleting deprecated chunk from Supabase: {remote_title}")
            try:
                supabase.table("re_knowledge_base").delete().eq("id", remote_row["id"]).execute()
                stats["deleted"] += 1
            except Exception as e:
                logger.error(f"Failed to delete orphan {remote_title}: {e}")

    logger.info(f"Reconciliation complete: Added={stats['added']}, Updated={stats['updated']}, Deleted={stats['deleted']}, Unchanged={stats['unchanged']}")
    return stats

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

    # 3. Remote Supabase KB for Real Estate with Idempotent 3-Way Reconciliation
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    stats = reconcile_real_estate_kb(supabase, embedder, methodology_docs)

    # 4. Local Pre-Router Embeddings for section_titles (Synchronized)
    section_titles = [doc["section_title"] for doc in methodology_docs if "section_title" in doc]
    
    regenerate_titles = False
    if not os.path.exists("section_title_embeddings.npy") or not os.path.exists("section_titles.json"):
        regenerate_titles = True
    else:
        try:
            with open("section_titles.json", "r", encoding="utf-8") as f:
                saved_titles = json.load(f)
            if saved_titles != section_titles or stats["added"] > 0 or stats["deleted"] > 0 or stats["updated"] > 0:
                regenerate_titles = True
        except Exception:
            regenerate_titles = True

    if regenerate_titles and section_titles:
        logger.info("Regenerating synchronized section_title_embeddings.npy...")
        title_embeddings = embedder.encode(section_titles, normalize_embeddings=True)
        np.save("section_title_embeddings.npy", title_embeddings)
        with open("section_titles.json", "w", encoding="utf-8") as f:
            json.dump(section_titles, f, indent=2)
        logger.info(f"Saved {len(section_titles)} synchronized section title embeddings for pre-routing.")

if __name__ == "__main__":
    ensure_ingested()
