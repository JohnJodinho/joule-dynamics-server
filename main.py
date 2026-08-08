import os
import numpy as np
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
from groq import Groq
from dotenv import load_dotenv
from kb_docs import KB_DOCS

load_dotenv()
app = FastAPI()

# Allow your Vercel frontend to reach this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


from services.embedding_service import get_embedding_model
import unified_ingest

# Ensure local and remote KBs are seeded idempotently
unified_ingest.ensure_ingested()

embed_model = get_embedding_model()
kb_embeddings = np.load("kb_embeddings.npy")

groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))

SYSTEM_PROMPT = """You are the customer support assistant for Amara Home & Kitchen, an online kitchenware and home-goods retailer.
Answer ONLY using the information provided below. Keep answers short — 2-4 sentences.
If the answer isn't in the information provided, say you'll connect them with a human team member rather than guessing.
Never invent policies, prices, or timelines that aren't in the information.

INFORMATION:
{context}
"""


class ChatRequest(BaseModel):
    question: str


def retrieve(question: str, top_k: int = 3):
    q_emb = embed_model.encode([question], normalize_embeddings=True)[0]
    sims = kb_embeddings @ q_emb
    top_idx = np.argsort(sims)[::-1][:top_k]
    return [KB_DOCS[i]["text"] for i in top_idx]


def ask_groq(question: str, context: str, model: str = "llama-3.3-70b-versatile"):
    resp = groq_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT.format(
                context=context)},
            {"role": "user", "content": question},
        ],
        temperature=0.2,
        max_tokens=300,
    )
    return resp.choices[0].message.content


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/rag/chat")
def chat(req: ChatRequest):
    context_chunks = retrieve(req.question)
    context = "\n".join(context_chunks)
    try:
        answer = ask_groq(req.question, context,
                          model="llama-3.3-70b-versatile")
    except Exception:
        answer = ask_groq(req.question, context, model="llama-3.1-8b-instant")
    return {"answer": answer}

# Import and mount the new Real Estate router
from routes import real_estate_chat
app.include_router(real_estate_chat.router)
