"""
unified_ingest.py
─────────────────
Idempotent synchronization and seeding of local numpy embeddings and Supabase knowledge bases.
"""

import json
import os
import uuid
import numpy as np
from services.embedding_service import get_embedding_model
from kb_docs import KB_DOCS
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY
from services.observability import setup_logger

logger = setup_logger(__name__)


def _fetch_remote_kb(supabase: Client) -> dict:
    """Fetches existing records from Supabase re_knowledge_base indexed by section title."""
    try:
        remote_res = supabase.table("re_knowledge_base").select("id, section_title, chunk_content").execute()
        return {row["section_title"]: row for row in (remote_res.data or [])}
    except Exception as e:
        logger.error(f"Failed to fetch existing records from Supabase re_knowledge_base: {e}")
        return {}


def _insert_chunk(supabase: Client, embedder, title: str, content: str) -> bool:
    """Computes embedding and inserts a new chunk into re_knowledge_base."""
    logger.info(f"Adding new chunk: {title}")
    deterministic_id = str(uuid.uuid5(uuid.NAMESPACE_URL, title))
    vector = embedder.encode(content).tolist()
    payload = {
        "id": deterministic_id,
        "section_title": title,
        "chunk_content": content,
        "embedding": vector,
    }
    try:
        supabase.table("re_knowledge_base").insert(payload).execute()
        return True
    except Exception as e:
        logger.error(f"Failed to insert {title}: {e}")
        return False


def _update_chunk(supabase: Client, embedder, title: str, content: str) -> bool:
    """Recomputes embedding and updates an existing chunk in re_knowledge_base."""
    logger.info(f"Updating modified chunk: {title}")
    vector = embedder.encode(content).tolist()
    payload = {
        "chunk_content": content,
        "embedding": vector,
    }
    try:
        supabase.table("re_knowledge_base").update(payload).eq("section_title", title).execute()
        return True
    except Exception as e:
        logger.error(f"Failed to update {title}: {e}")
        return False


def _sync_single_doc(supabase: Client, embedder, doc: dict, remote_row: dict | None) -> str:
    """Syncs a single document against remote state, returning change status."""
    title = doc.get("section_title")
    content = doc.get("chunk_content")
    if not title or not content:
        return "skipped"

    if remote_row is None:
        return "added" if _insert_chunk(supabase, embedder, title, content) else "failed"

    remote_content = remote_row.get("chunk_content", "")
    if remote_content.strip() != content.strip():
        return "updated" if _update_chunk(supabase, embedder, title, content) else "failed"

    return "unchanged"


def _cleanup_remote_orphans(supabase: Client, remote_data: dict, local_titles: set) -> int:
    """Deletes deprecated records from Supabase that no longer exist locally."""
    deleted_count = 0
    for remote_title, remote_row in remote_data.items():
        if remote_title not in local_titles:
            logger.info(f"Deleting deprecated chunk from Supabase: {remote_title}")
            try:
                supabase.table("re_knowledge_base").delete().eq("id", remote_row["id"]).execute()
                deleted_count += 1
            except Exception as e:
                logger.error(f"Failed to delete orphan {remote_title}: {e}")
    return deleted_count


def reconcile_real_estate_kb(supabase: Client, embedder, methodology_docs: list) -> dict:
    """Performs 3-way idempotent reconciliation of methodology docs against Supabase."""
    stats = {"added": 0, "updated": 0, "deleted": 0, "unchanged": 0}
    remote_data = _fetch_remote_kb(supabase)
    local_titles = set()

    for doc in methodology_docs:
        title = doc.get("section_title")
        if title:
            local_titles.add(title)
        outcome = _sync_single_doc(supabase, embedder, doc, remote_data.get(title))
        if outcome in stats:
            stats[outcome] += 1

    stats["deleted"] = _cleanup_remote_orphans(supabase, remote_data, local_titles)
    logger.info(f"Reconciliation complete: {stats}")
    return stats


def _is_amara_kb_valid() -> bool:
    """Checks whether kb_embeddings.npy exists and has expected shape."""
    if not os.path.exists("kb_embeddings.npy"):
        return False
    try:
        arr = np.load("kb_embeddings.npy")
        return len(arr) == len(KB_DOCS)
    except Exception:
        return False


def _ensure_amara_kb(embedder):
    """Generates local numpy embeddings for Amara KB if absent or outdated."""
    if _is_amara_kb_valid():
        return
    logger.info("Regenerating local embeddings in kb_embeddings.npy...")
    texts = [d["text"] for d in KB_DOCS]
    kb_embeddings = embedder.encode(texts, normalize_embeddings=True)
    np.save("kb_embeddings.npy", kb_embeddings)
    logger.info(f"Embedded {len(texts)} KB docs and saved to kb_embeddings.npy")


def _load_methodology_docs() -> list:
    """Loads methodology_kb.json if the file is present."""
    if not os.path.exists("methodology_kb.json"):
        return []
    try:
        with open("methodology_kb.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to read methodology_kb.json: {e}")
        return []


def _should_regenerate_section_titles(section_titles: list, stats: dict) -> bool:
    """Determines if section title embeddings must be re-computed."""
    if not os.path.exists("section_title_embeddings.npy") or not os.path.exists("section_titles.json"):
        return True
    try:
        with open("section_titles.json", "r", encoding="utf-8") as f:
            saved_titles = json.load(f)
        has_changes = stats.get("added", 0) > 0 or stats.get("deleted", 0) > 0 or stats.get("updated", 0) > 0
        return saved_titles != section_titles or has_changes
    except Exception:
        return True


def _ensure_section_title_embeddings(embedder, methodology_docs: list, stats: dict):
    """Saves synchronized section title embeddings for pre-routing."""
    section_titles = [doc["section_title"] for doc in methodology_docs if "section_title" in doc]
    if not section_titles or not _should_regenerate_section_titles(section_titles, stats):
        return

    logger.info("Regenerating synchronized section_title_embeddings.npy...")
    title_embeddings = embedder.encode(section_titles, normalize_embeddings=True)
    np.save("section_title_embeddings.npy", title_embeddings)
    with open("section_titles.json", "w", encoding="utf-8") as f:
        json.dump(section_titles, f, indent=2)
    logger.info(f"Saved {len(section_titles)} synchronized section title embeddings for pre-routing.")


def ensure_ingested():
    """Seeds both local numpy embeddings and remote Supabase database idempotently."""
    logger.info("Verifying ingestion state...")
    embedder = get_embedding_model()
    _ensure_amara_kb(embedder)

    methodology_docs = _load_methodology_docs()
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    stats = reconcile_real_estate_kb(supabase, embedder, methodology_docs)
    _ensure_section_title_embeddings(embedder, methodology_docs, stats)


if __name__ == "__main__":
    ensure_ingested()
