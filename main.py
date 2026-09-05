import os
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

# ── Observability bootstrap (must run before any service imports) ─────────────
from langfuse import get_client
from openinference.instrumentation.groq import GroqInstrumentor

langfuse = get_client()
if not langfuse.auth_check():
    print("WARNING: Langfuse auth failed - check your keys ✋")

GroqInstrumentor().instrument()

# ── App setup ─────────────────────────────────────────────────────────────────
from services.observability import setup_logger
logger = setup_logger(__name__)

app = FastAPI(
    title="Joule Dynamics Intelligence API",
    description="Real Estate Rate Monitor + RAG Customer Support",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

# ── Startup: seed knowledge bases idempotently ────────────────────────────────
import unified_ingest
unified_ingest.ensure_ingested()

# ── Startup: initialize live market registry ──────────────────────────────────
# Fetches the authoritative market list from Supabase once at boot.
# All downstream consumers (prompts, tools, agent_loop) read from this cache.
# Adding a new market (e.g. Dubai) is now a DB operation — no code change needed.
from services.market_registry import market_registry
market_registry.refresh()
logger.info(f"Market registry initialized: {market_registry.get_markets()}")

# ── Mount all versioned routers ───────────────────────────────────────────────
from routes import v1_router
app.include_router(v1_router)

# Backwards-compat redirect: old unversioned endpoint still works
# Remove after frontend has migrated to /api/v1/rag/chat
from routes.v1.rag import router as _rag_router
from fastapi import APIRouter
_compat = APIRouter(tags=["Deprecated — use /api/v1/rag/chat"])

from routes.v1.rag import rag_chat, RagChatRequest

@_compat.post("/api/rag/chat", deprecated=True, include_in_schema=False)
def rag_chat_compat(req: RagChatRequest):
    """Deprecated: forwards to /api/v1/rag/chat for backwards compat."""
    return rag_chat(req)

app.include_router(_compat)
