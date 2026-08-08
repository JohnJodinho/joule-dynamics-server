import os
from supabase import create_client, Client
from services.embedding_service import get_embedding_model
from config import SUPABASE_URL, SUPABASE_KEY

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
embedder = get_embedding_model()

async def execute_tool_rpc(func_name: str, args: dict) -> dict:
    """Executes a read-only Supabase RPC matching the tool schema."""
    try:
        res = supabase.rpc(func_name, args).execute()
        return {"status": "success", "data": res.data}
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def search_methodology_rag(query: str, top_k: int = 3) -> list:
    """Embeds query and retrieves top matching methodology documentation chunks."""
    try:
        vector = embedder.encode(query).tolist()
        res = supabase.rpc(
            "match_re_methodology",
            {
                "query_embedding": vector,
                "match_threshold": 0.45,
                "match_count": top_k
            }
        ).execute()
        
        return [f"### {item['section_title']}\n{item['chunk_content']}" for item in res.data]
    except Exception as e:
        print(f"RAG Retrieval Error: {e}")
        return []
