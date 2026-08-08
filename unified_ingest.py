import os
import uuid
import numpy as np
from services.embedding_service import get_embedding_model
from kb_docs import KB_DOCS
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY

METHODOLOGY_DOCS = [
    {
        "section_title": "Availability & 2-Night Window Definition",
        "chunk_content": "A listing marked as 'Unavailable' means no open booking dates were detected within a 2-night check-in window starting from the current date. The system does not look ahead across full calendar months; it tracks consecutive 2-night availability as an immediate signal."
    },
    {
        "section_title": "7-Day Trailing Average Benchmark",
        "chunk_content": "The 7-day trailing average rate is calculated per property by taking the mean nightly price recorded across the prior 6 daily scrapes. Rate volatility alerts trigger when a property's current nightly rate strays 25% or more above or below this baseline."
    },
    {
        "section_title": "Scrape Cadence & Data Refresh",
        "chunk_content": "Listings are scraped 4 times daily to capture short-term rate adjustments and booking updates. Status 'Stale' indicates a scraper job failed to return fresh price points within the last 12 hours."
    },
    {
        "section_title": "World Cup 2026 Strategic Focus",
        "chunk_content": "The Real Estate Rate Monitor specifically tracks short-term rental inventory across NYC/NJ Metro and Miami markets to capture rate surges, supply constraints, and pricing dynamic anomalies leading up to the 2026 World Cup Final."
    },
    {
        "section_title": "Vrbo Historical Tracking Status",
        "chunk_content": "Vrbo properties are flagged as 'Historical' following platform scraping accessibility adjustments. Historical listings remain visible for baseline comparisons, but fresh daily rates are actively tracked via Airbnb endpoints."
    }
]

def ensure_ingested():
    """
    Idempotent function that seeds both the local numpy embeddings and the remote Supabase database.
    """
    print("Verifying ingestion state...")
    embedder = get_embedding_model()
    
    # 1. Local Numpy KB for Amara (Idempotent)
    if not os.path.exists("kb_embeddings.npy"):
        print("kb_embeddings.npy not found, generating local embeddings...")
        texts = [d["text"] for d in KB_DOCS]
        kb_embeddings = embedder.encode(texts, normalize_embeddings=True)
        np.save("kb_embeddings.npy", kb_embeddings)
        print(f"Embedded {len(texts)} KB docs and saved to kb_embeddings.npy")
    else:
        # Check shape to ensure it's valid, otherwise overwrite
        try:
            arr = np.load("kb_embeddings.npy")
            if len(arr) != len(KB_DOCS):
                raise ValueError("Mismatch length")
        except Exception:
            print("kb_embeddings.npy is corrupted or outdated. Regenerating...")
            texts = [d["text"] for d in KB_DOCS]
            kb_embeddings = embedder.encode(texts, normalize_embeddings=True)
            np.save("kb_embeddings.npy", kb_embeddings)
            print(f"Embedded {len(texts)} KB docs and saved to kb_embeddings.npy")
            
    # 2. Remote Supabase KB for Real Estate (Idempotent)
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    
    # Check what already exists in Supabase
    try:
        existing_res = supabase.table("re_knowledge_base").select("section_title").execute()
        existing_titles = {row["section_title"] for row in existing_res.data}
    except Exception as e:
        print(f"Failed to query Supabase: {e}")
        existing_titles = set()
        
    for doc in METHODOLOGY_DOCS:
        if doc["section_title"] not in existing_titles:
            print(f"Seeding missing chunk to Supabase: {doc['section_title']}")
            vector = embedder.encode(doc["chunk_content"]).tolist()
            payload = {
                "id": str(uuid.uuid4()),
                "section_title": doc["section_title"],
                "chunk_content": doc["chunk_content"],
                "embedding": vector
            }
            try:
                supabase.table("re_knowledge_base").insert(payload).execute()
                print(f"Successfully seeded: {doc['section_title']}")
            except Exception as e:
                print(f"Failed to seed {doc['section_title']}: {e}")

if __name__ == "__main__":
    ensure_ingested()
