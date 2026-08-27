# Joule Dynamics Real Estate Intelligence Server (Pulse AI)

[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org)
[![Groq LPU](https://img.shields.io/badge/Groq-LPU_Inference-F05A28?style=for-the-badge)](https://groq.com)
[![Supabase](https://img.shields.io/badge/Supabase-PostgreSQL_%26_pgvector-3ECF8E?style=for-the-badge&logo=supabase&logoColor=white)](https://supabase.com)
[![Langfuse](https://img.shields.io/badge/Langfuse-Observability-000000?style=for-the-badge&logo=langfuse&logoColor=white)](https://langfuse.com)
[![Hugging Face Spaces](https://img.shields.io/badge/Hugging_Face-Spaces_Docker-FFD21E?style=for-the-badge&logo=huggingface&logoColor=black)](https://huggingface.co/spaces)

---

## 1. Executive Summary

**Joule Dynamics Server** is a high-throughput, agentic inference backend engineered specifically to power the **Real Estate Telemetry & Rate Intelligence Dashboard**. 

Operating far beyond standard CRUD paradigms, this server functions as an autonomous reasoning engine that compiles unstructured, natural-language investor queries into deterministic, parameterized **PostgreSQL PL/pgSQL stored procedures**, **window function aggregations**, and **pgvector semantic scans**. By combining ultra-low-latency LPU inference (Groq) with hierarchical schema gating and Server-Sent Events (SSE), the engine delivers real-time market snapshots, anomaly alerts (25%+ rate deviations), and geographic proximity analytics with sub-second time-to-first-token.

---

## 2. System Architecture (The AI Pipeline)

The telemetry backend utilizes an asynchronous, non-blocking pipeline spanning intent classification, localized dynamic tool retrieval, database execution, and streaming synthesis.

```
                           ┌─────────────────────────────────────────────────────────────┐
                           │               Client Query via SSE Protocol                 │
                           │            POST /api/v1/real-estate/chat/stream             │
                           └──────────────────────────────┬──────────────────────────────┘
                                                          │
                                                          ▼
                           ┌─────────────────────────────────────────────────────────────┐
                           │          1. INTENT ROUTING & CATEGORY GATING                │
                           │          Model: openai/gpt-oss-20b (~150ms)                 │
                           │          • Classifies: PATH_A | PATH_B | BOTH | etc.        │
                           │          • Tags Domain: ["MARKET", "ANOMALY", "GEO"...]     │
                           └──────────────────────────────┬──────────────────────────────┘
                                                          │
                                                          ▼
                           ┌─────────────────────────────────────────────────────────────┐
                           │          2. DYNAMIC TOOL DISCOVERY (Embedding Engine)       │
                           │          Model: all-MiniLM-L6-v2 (Local, ~20ms latency)     │
                           │          • Filters registry by domain categories            │
                           │          • Cosine similarity ranking on tool descriptions   │
                           │          • Injects Top-3 Tools + Universal Action Tools     │
                           └──────────────────────────────┬──────────────────────────────┘
                                                          │
                                                          ▼
                           ┌─────────────────────────────────────────────────────────────┐
                           │          3. BRAIN & AGENTIC SYNTHESIS LOOP                  │
                           │          Model: openai/gpt-oss-120b                         │
                           │          • Multi-step tool orchestration (max 4 rounds)     │
                           │          • Parameter clamping & alias normalization         │
                           │          • Execution via PostgREST RPC & pgvector RAG       │
                           └──────────────────────────────┬──────────────────────────────┘
                                                          │
                                                          ▼
                           ┌─────────────────────────────────────────────────────────────┐
                           │          4. SERVER-SENT EVENTS (SSE) STREAMING              │
                           │          • event: status       (Routing state)              │
                           │          • event: tool_call    (Active DB RPC payload)      │
                           │          • event: token        (Live Markdown tokens)       │
                           │          • event: done         (Final + Action Chips)       │
                           └─────────────────────────────────────────────────────────────┘
```

### Pipeline Lifecycle:
1. **Routing & Intent Disambiguation:** Fast-path classifier (`openai/gpt-oss-20b`) categorizes the query into one of 6 pathways (`PATH_A` Live Data, `PATH_B` Methodology/RAG, `BOTH` Hybrid, `GREETING`, `COMMERCIAL_HANDOFF`, `OUT_OF_SCOPE`) and tags domain categories.
2. **Context-Aware Dynamic Schema Gating:** To prevent token bloat (saving >75% of prompt tokens), a local sentence-transformer (`all-MiniLM-L6-v2`) ranks tool schemas against the user prompt (enriched with conversational history) and exposes strictly the Top-3 relevant tools + universal tools.
3. **Autonomous Execution Loop:** The synthesis engine (`openai/gpt-oss-120b`) generates JSON function call payloads, dispatches them against Supabase PostgREST RPC endpoints, and streams real-time execution badges to the client.
4. **Resilient Loop Carryover:** If a complex, multi-intent prompt hits the 4-round loop ceiling before completing all operations (e.g. data fetch + export generation), the engine synthesizes findings and automatically emits interactive action chips (`suggest_actions`) to carry over work into subsequent turns.

---

## 3. The 18-Tool Agentic Framework

The agent has direct access to 18 specialized tools mapping to PostgreSQL PL/pgSQL stored procedures, geospatial calculations, and export workers:

| Category | Tool Name | Database / RPC Target | Technical Function & Capabilities |
|---|---|---|---|
| **Market & KPIs** | `get_dashboard_kpis` | `public.get_dashboard_kpis` | Aggregates portfolio KPIs (active properties, 7d delta, 25%+ price surge count, scrape health). |
| **Market & KPIs** | `get_market_averages` | `public.get_market_averages` | Calculates nightly rates and 7-day trailing baseline averages using SQL window functions. |
| **Market & KPIs** | `get_market_snapshot` | `public.get_market_snapshot` | Single-day comprehensive summary: active inventory, min/max rates, and occupancy rate %. |
| **Market & KPIs** | `get_market_trend` | `public.get_market_trend` | Multi-day historical rate trajectory (up to 90 days) across Miami and NYC/NJ Metro. |
| **Market & KPIs** | `get_tracked_markets` | `public.get_tracked_markets` | Returns platform coverage, listing volumes, and operational regions in the database. |
| **Market & KPIs** | `get_recently_changed_tracking` | `public.get_recently_changed_tracking` | Audit log of listings newly activated or paused in the scraping registry. |
| **Anomalies** | `get_spike_alerts` | `public.get_spike_alerts` | Scans for sudden rate spikes exceeding configurable threshold (default 25% over 7d baseline). |
| **Anomalies** | `get_rate_anomaly_report` | `public.get_rate_anomaly_report` | Deep pricing anomaly investigation: lists previous rates, baseline delta, and deviation %. |
| **Anomalies** | `get_most_volatile_properties` | `public.get_most_volatile_properties` | Ranks properties with the highest dynamic pricing frequency and variance over lookback window. |
| **Listings & Rates** | `get_property_snapshot` | `public.get_property_snapshot` | Single-listing telemetry: bedrooms, coordinates, current rate, 7d baseline, and availability. |
| **Listings & Rates** | `get_property_rate_changes` | `public.get_property_rate_changes` | Chronological price revision log tracking exact host rate adjustments over time. |
| **Listings & Rates** | `compare_properties` | `public.compare_properties` | Multi-property side-by-side comparison across pricing, occupancy, and bedroom configurations. |
| **Listings & Rates** | `search_properties` | `public.search_properties` | Multi-parameter search filtered by market, bedroom count, platform, availability, and search terms. |
| **Listings & Rates** | `get_availability_rate` | `public.get_availability_rate` | Computes calendar booking ratios, vacancy percentage, and active inventory metrics. |
| **Geospatial** | `geocode_address` | *Python Geocoding Worker* | Translates natural language addresses/landmarks into decimal latitude/longitude coordinates. |
| **Geospatial** | `get_nearby_properties` | `public.get_nearby_properties` | Executes Haversine radius queries on geographic coordinates to find nearest listings. |
| **Geospatial** | `get_distance_km` | `public.get_distance_km` | PostGIS / mathematical straight-line distance computation between two property UUIDs. |
| **Universal Actions** | `generate_data_export` | *Appwrite Storage Worker* | Compiles structured real estate analysis into downloadable Markdown (`.md`) reports. |
| **Universal Actions** | `suggest_actions` | *SSE UI Protocol* | Dispatches native interactive action chips/buttons to guide user follow-ups and next steps. |

---

## 4. Observability & Telemetry

Every request passing through `joule-dynamics-server` is deeply instrumented with **Langfuse** and **OpenTelemetry** for end-to-end tracing:

```
[Trace: chat-session-001]
 ├── [Span: router-classification]      Model: openai/gpt-oss-20b  Tokens: 210  Latency: 148ms
 ├── [Span: dynamic-tool-discovery]    Model: all-MiniLM-L6-v2    Ranked: 18   Latency: 22ms
 ├── [Generation: agent-round-1]       Model: openai/gpt-oss-120b Tokens: 480  Latency: 610ms
 │    └── [Tool: get_market_snapshot]  Status: success            Rows: 1      Latency: 45ms
 └── [Generation: agent-synthesis]     Model: openai/gpt-oss-120b Tokens: 320  Latency: 410ms
```

- **Step-by-Step Trajectory Tracking:** Captures multi-round LLM tool calls, arguments, database query responses, and final streaming text.
- **Latency & Error Profiling:** Automatically detects and isolates PostgREST `PGRST202` schema errors, Groq rate-limiting retries, and tool execution bottlenecks.
- **Token & Cost Attribution:** Live calculation of input, completion, and reasoning tokens across both routing and synthesis model tiers.

---

## 5. API Contracts & Streaming Protocol

### Endpoint: `POST /api/v1/real-estate/chat/stream`
- **Request Headers:** `Content-Type: application/json`, `Accept: text/event-stream`
- **Request Body:**
```json
{
  "message": "What is the average nightly rate in Miami for 2-bedroom listings?",
  "session_id": "usr_session_8f92a",
  "context": {
    "market": "Miami"
  }
}
```

### Event Stream Chunks:

#### 1. Route Classification
```http
event: status
data: {"classification": "PATH_A"}
```

#### 2. Live Tool Execution Badge
```http
event: tool_call
data: {"tool": "search_properties", "args": {"p_market": "Miami", "p_bedrooms": 2}}
```

#### 3. Token Stream (Live Markdown)
```http
event: token
data: {"text": "In "}

event: token
data: {"text": "Miami, "}

event: token
data: {"text": "the average rate for 2-bedroom properties is **$285/night**."}
```

#### 4. Final Payload & Action Chips
```http
event: done
data: {
  "reply": "In Miami, the average rate for 2-bedroom properties is **$285/night**...",
  "suggested_actions": ["Compare with NYC", "Generate Download Report", "View Price Volatility"],
  "tools_called": [{"tool": "search_properties", "args": {"p_market": "Miami", "p_bedrooms": 2}}]
}
```

---

## 6. Local Development & Environment Setup

### 1. Prerequisites
- Python 3.10+
- Git & Virtualenv

### 2. Installation
```bash
# Clone repository
git clone https://github.com/JohnJodinho/joule-dynamics-server.git
cd joule-dynamics-server

# Create and activate virtual environment
python -m venv venv
# On Linux/macOS:
source venv/bin/activate
# On Windows:
.env\Scriptsctivate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration (`.env`)
Create a `.env` file in the project root with the following parameters:

```env
# ── Core Server Configuration ──
PORT=7860
HOST=0.0.0.0
ALLOWED_ORIGINS=*

# ── Groq LPU Inference ──
GROQ_API_KEY=gsk_your_groq_api_key_here
GROQ_ROUTE_MODEL=openai/gpt-oss-20b
GROQ_FALLBACK_ROUTE_MODEL=groq/compound-mini
GROQ_SYNTHESIS_MODEL=openai/gpt-oss-120b
GROQ_FALLBACK_SYNTHESIS_MODEL=qwen/qwen3.6-27b

# ── Supabase Database & Vectors ──
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_service_role_or_anon_key

# ── Langfuse Observability ──
LANGFUSE_PUBLIC_KEY=pk-lf-your_public_key
LANGFUSE_SECRET_KEY=sk-lf-your_secret_key
LANGFUSE_HOST=https://cloud.langfuse.com

# ── Appwrite Storage (Report Exports) ──
APPWRITE_ENDPOINT=https://cloud.appwrite.io/v1
APPWRITE_PROJECT_ID=your_project_id
APPWRITE_API_KEY=your_appwrite_api_key
APPWRITE_BUCKET_ID=your_bucket_id
```

### 4. Run Development Server
```bash
uvicorn app:app --host 0.0.0.0 --port 7860 --reload
```

Server will be active at `http://localhost:7860`. Interactive Swagger OpenAPI docs available at `http://localhost:7860/docs`.

---

## 7. Production Deployment (Hugging Face Spaces)

This server is packaged as a Docker container designed for seamless deployment on **Hugging Face Spaces**.

### Deployment Steps:
1. Create a new Space on [Hugging Face](https://huggingface.co/new-space) and select **Docker** as the SDK.
2. In your Space's root `README.md`, ensure the standard YAML metadata block is present at the very top:
```yaml
---
title: Joule Dynamics Server
emoji: 📊
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---
```
3. In your Space **Settings** $ightarrow$ **Variables and Secrets**, add all production secrets (`GROQ_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `APPWRITE_API_KEY`).
4. Push code to the Space repository:
```bash
git push hf main
```
The Docker container will automatically build, install requirements, pre-load the local embedding model cache, and expose the FastAPI SSE server on port `7860`.
