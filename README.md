# Joule Dynamics Real Estate Intelligence Server (Pulse AI)

High-throughput, token-optimized agentic AI backend powering the **Joule Dynamics Real Estate Rate Monitor & Market Intelligence Layer**. Built with **FastAPI**, **Groq LPU Inference**, **Supabase (pgvector & RPCs)**, and **Appwrite Storage**.

---

## 🏛️ System Architecture

```
                           ┌────────────────────────────────────────┐
                           │          User Query (via SSE)          │
                           └───────────────────┬────────────────────┘
                                               │
                                               ▼
                           ┌────────────────────────────────────────┐
                           │  1. ROUTER & CATEGORY GATING (Tier 1)  │
                           │  Model: openai/gpt-oss-20b (~150ms)    │
                           │  • Classifies Route (PATH_A/B/BOTH/etc)│
                           │  • Extracts Categories [MARKET, GEO...]│
                           └───────────────────┬────────────────────┘
                                               │
                                               ▼
                           ┌────────────────────────────────────────┐
                           │  2. DYNAMIC TOOL DISCOVERY (Semantic)  │
                           │  Model: all-MiniLM-L6-v2 (~20ms local) │
                           │  • Gated candidate filtering           │
                           │  • Top-3 tool vector similarity ranking│
                           │  • Attaches Universal Action Tools     │
                           └───────────────────┬────────────────────┘
                                               │
                                               ▼
                           ┌────────────────────────────────────────┐
                           │  3. BRAIN & AGENTIC SYNTHESIS (Tier 2) │
                           │  Model: openai/gpt-oss-120b            │
                           │  • Executes Supabase RPCs / Vector RAG │
                           │  • Multi-step tool loop (max 4 rounds) │
                           │  • Streams Markdown tokens via SSE     │
                           │  • Emits interactive suggested actions │
                           └────────────────────────────────────────┘
```

---

## ⚡ Core Architectural Features

### 1. Two-Tier Model Routing Hierarchy
- **Tier 1 — Classification Router (`openai/gpt-oss-20b` | Fallback: `groq/compound-mini`):** Evaluates user intent within ~190 reasoning tokens and tags tool categories (`MARKET`, `ANOMALY`, `PROPERTY`, `GEO`).
- **Tier 2 — Agentic Brain & Synthesis (`openai/gpt-oss-120b` | Fallback: `qwen/qwen3.6-27b`):** 120B parameter model orchestrating database tool calls, anomaly analysis, and streaming responses.

### 2. Hierarchical Dynamic Tool Discovery & Schema Gating
Instead of sending all 18 tool schemas on every request (consuming >2,000 prompt tokens), a 2-stage discovery engine filters tools:
1. **Category Gating:** Gated by Router-selected domain buckets.
2. **Semantic Similarity:** Cosine similarity via local `all-MiniLM-L6-v2` embeddings ranks Top-3 tools + Universal Tools (`suggest_actions`, `generate_data_export`).
3. **Efficiency:** Reduces tool prompt overhead by **75%** while keeping schema precision high.

### 3. Context-Aware Query Enrichment
Short/single-word follow-ups (e.g. selecting `"Miami"` or `"Yesterday"`) are dynamically enriched with prior turn context before computing embeddings, ensuring accurate tool retrieval.

### 4. Supabase PostgREST RPC Layer
15 read-only SQL functions with strict parameter clamping and normalization (KPIs, market snapshots, 25%+ price surge alerts, dynamic rate revisions, and geocoded radius queries).

### 5. Conversational Tool Carryover & Action Chips
- Emits native `suggest_actions` tool payloads delivered in the SSE `event: done` stream.
- Multi-step tasks exceeding loop caps provide continuation action chips (`["Yes, generate download report", "No, keep overview"]`) to seamlessly carry workflows across turns.

---

## 📡 API Endpoints & Communication Protocol

### Real-Time Chat Stream (Server-Sent Events)
- **Route:** `POST /api/v1/real-estate/chat/stream`
- **Headers:** `Content-Type: application/json`, `Accept: text/event-stream`
- **Payload:**
```json
{
  "message": "What is the average nightly rate in Miami?",
  "session_id": "session-xyz",
  "context": {
    "market": "Miami"
  }
}
```

### Event Stream Lifecycle:
1. `event: status` ➔ Route classification (`{ "classification": "PATH_A" }`)
2. `event: tool_call` ➔ Real-time tool badge (`{ "tool": "get_market_snapshot", "args": {...} }`)
3. `event: token` ➔ Incremental markdown tokens (`{ "text": "..." }`)
4. `event: done` ➔ Final payload with interactive action chips (`{ "reply": "...", "suggested_actions": ["Miami", "NYC/NJ Metro"] }`)
5. `event: error` ➔ Structured error details with automatic fallback handling

---

## 🛠️ Tech Stack & Dependencies

- **Framework:** FastAPI / Uvicorn (Dockerized)
- **Inference Engine:** Groq API SDK (High-Speed LPU Inference)
- **Database & Vectors:** Supabase (PostgreSQL, pgvector, PostgREST RPCs)
- **Local Embeddings:** Sentence-Transformers (`all-MiniLM-L6-v2`)
- **Storage:** Appwrite Storage (Markdown Report Exports)
- **Observability:** Langfuse Tracing & OpenTelemetry
