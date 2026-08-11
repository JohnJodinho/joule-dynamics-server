"""
RAG Chat — v1 API routes.

Endpoints:
  POST /api/v1/rag/chat  — Amara Home & Kitchen customer support RAG chat
"""
import logging
import os
from typing import Optional

import numpy as np
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from groq import Groq
from pydantic import BaseModel, Field

from services.embedding_service import get_embedding_model
from kb_docs import KB_DOCS

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/rag",
    tags=["RAG Chat — v1"],
)

# ── Model / client initialisation (lazy singleton pattern) ────────────────────
_embed_model = None
_kb_embeddings = None
_groq_client = None


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        _embed_model = get_embedding_model()
    return _embed_model


def _get_kb_embeddings():
    global _kb_embeddings
    if _kb_embeddings is None:
        _kb_embeddings = np.load("kb_embeddings.npy")
    return _kb_embeddings


def _get_groq():
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
    return _groq_client


SYSTEM_PROMPT = """You are the customer support assistant for Amara Home & Kitchen, an online kitchenware and home-goods retailer.
Answer ONLY using the information provided below. Keep answers short — 2-4 sentences.
If the answer isn't in the information provided, say you'll connect them with a human team member rather than guessing.
Never invent policies, prices, or timelines that aren't in the information.

INFORMATION:
{context}
"""


# ── Request / Response models ─────────────────────────────────────────────────

class RagChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)


class RagChatResponse(BaseModel):
    answer: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _retrieve(question: str, top_k: int = 3) -> list:
    embed = _get_embed_model()
    kb = _get_kb_embeddings()
    q_emb = embed.encode([question], normalize_embeddings=True)[0]
    sims = kb @ q_emb
    top_idx = np.argsort(sims)[::-1][:top_k]
    return [KB_DOCS[i]["text"] for i in top_idx]


def _ask_groq(question: str, context: str, model: str = "llama-3.3-70b-versatile") -> str:
    client = _get_groq()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT.format(context=context)},
            {"role": "user", "content": question},
        ],
        temperature=0.2,
        max_tokens=300,
    )
    return resp.choices[0].message.content


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post(
    "/chat",
    response_model=RagChatResponse,
    responses={
        503: {"description": "AI service unavailable"},
        500: {"description": "Unexpected server error"},
    },
    summary="Customer support RAG chat (Amara Home & Kitchen)",
)
def rag_chat(req: RagChatRequest):
    context_chunks = _retrieve(req.question)
    context = "\n".join(context_chunks)
    try:
        answer = _ask_groq(req.question, context, model="llama-3.3-70b-versatile")
    except Exception as primary_err:
        logger.warning(f"Primary model failed for RAG chat: {primary_err}. Falling back.")
        try:
            answer = _ask_groq(req.question, context, model="llama-3.1-8b-instant")
        except Exception as fallback_err:
            logger.error(f"Fallback model also failed: {fallback_err}")
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"error": {"code": "UPSTREAM_UNAVAILABLE", "message": "The AI service is temporarily unavailable.", "retryable": True}},
            )
    return RagChatResponse(answer=answer)
