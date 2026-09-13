# Pulse AI — Alert Subscriptions: Full Technical Specification

## Architecture Overview

```
User: "let me know when property_a in Miami spikes again"
        ↓
ROUTER: classifies as ALERT_SUBSCRIPTION (fast, cheap — exactly 7 classifications)
        ↓
AGENT LOOP (_resolve_active_tools):
  - Injects exactly 2 tools: create_alert_subscription + suggest_actions
  - Prompt: _PROMPT_BASE + _PROMPT_ALERT + _PROMPT_RESPONSE_RULES
        ↓
SYNTHESIS:
  - Missing details? → invokes suggest_actions with concrete chips
  - Details complete? → invokes create_alert_subscription (PENDING, unconfirmed)
        ↓
CONFIRMATION EMAIL:
  - System sends double opt-in email with single-use verification token
  - Link: https://pulse.jouledynamics.com/alerts/confirm?token=<token>
        ↓
CONFIRMATION ENDPOINT (GET /api/v1/alerts/confirm):
  - Service role validates token, sets confirmed = true, status = 'active'
  - Pulse agent itself CANNOT confirm — double opt-in strictly enforced
        ↓
EVALUATION WORKERS (Post-scrape / Scheduled / Webhook):
  - Alert Evaluator: evaluates 9 event-driven criteria types against fresh data via existing Supabase RPCs
  - Digest Sender: evaluates scheduled digest subscriptions (daily/weekly)
  - Cleanup Job: expires and purges unconfirmed pending subscriptions > 48h
        ↓
COOLDOWN ENFORCEMENT & DELIVERY:
  - 24-hour rolling cooldown prevents duplicate fires for persistent conditions
  - HTML email with unsubscribe link (GET /api/v1/alerts/unsubscribe?token=<token>)
```

---

## 1. Supported Alert Criteria Types (10 Types)

The alert system supports 10 distinct criteria types spanning event-driven, threshold, and scheduled monitoring:

| `criteria_type` | Description | Target / Required Criteria | Evaluator RPC Used | Default Cooldown |
|---|---|---|---|---|
| `spike` | Price or rate spikes exceeding threshold | `{"market": "Miami", "threshold_pct": 25, "direction": "both"}` | `get_spike_alerts` | 24 hours |
| `rate_change` | Material rate changes over window | `{"market": "Abuja", "days": 7, "min_change_pct": 10}` | `get_market_rate_changes` | 24 hours |
| `price_threshold` | Property price crosses numeric limit | `{"property_search": "...", "operator": "below", "value": 150000}` | `get_property_snapshot` | 24 hours |
| `availability_change` | Property or market occupancy change | `{"property_search": "..."}` or `{"market": "Miami"}` | `get_property_snapshot` / `get_availability_rate` | 24 hours |
| `new_listing` | New property added to tracking | `{"market": "Lagos"}` | `get_recently_changed_tracking` | 24 hours |
| `tracking_removed` | Property delisted or untracked | `{"market": "Lagos"}` or `{"property_search": "..."}` | `get_recently_changed_tracking` | 24 hours |
| `anomaly` | Statistical rate deviation | `{"property_search": "...", "days": 30}` | `get_rate_anomaly_report` | 24 hours |
| `volatility` | High rate fluctuation in market | `{"market": "Abuja", "days": 14, "limit": 5}` | `get_most_volatile_properties` | 24 hours |
| `trend_reversal` | Market trend direction shift | `{"market": "Miami", "days": 14}` | `get_market_trend` | 24 hours |
| `digest` | Periodic market recap | `{"market": "Miami", "frequency": "weekly"}` (`"daily"` or `"weekly"`) | `get_real_estate_kpis` + `get_market_averages` | Cron-driven |

---

## 2. Router Integration (`services/query_router.py`)

`ALERT_SUBSCRIPTION` is the 7th valid classification alongside:
- `OUT_OF_SCOPE`
- `PATH_A` (live market intelligence)
- `PATH_B` (general real estate / UI concepts)
- `BOTH`
- `GREETING`
- `COMMERCIAL_HANDOFF`
- `ALERT_SUBSCRIPTION`

Prompt instructions guide the router to detect explicit future-monitoring intent ("alert me when...", "notify me if...", "send me a weekly digest of...").

---

## 3. Tool Definition & Token Budget (`services/alert_tool_schema.py`)

To preserve strict token economy:
- Exactly 2 tools are exposed during `ALERT_SUBSCRIPTION` turns: `create_alert_subscription` and `suggest_actions`.
- No heavy SQL or market analysis tools are loaded into context during subscription creation.

```json
{
  "type": "function",
  "function": {
    "name": "create_alert_subscription",
    "description": "Create a PENDING alert subscription. Never call this until you have the condition type, target (market or property), and email address.",
    "parameters": {
      "type": "object",
      "properties": {
        "email": {"type": "string"},
        "criteria_type": {
          "type": "string",
          "enum": [
            "spike", "rate_change", "price_threshold", "availability_change",
            "new_listing", "tracking_removed", "anomaly", "volatility",
            "trend_reversal", "digest"
          ]
        },
        "criteria": {"type": "object"},
        "raw_request_text": {"type": "string"}
      },
      "required": ["email", "criteria_type", "criteria", "raw_request_text"]
    }
  }
}
```

---

## 4. Prompt Extension (`_PROMPT_ALERT` in `services/prompts.py`)

When `classification == "ALERT_SUBSCRIPTION"`, the system prompt injects `_PROMPT_ALERT`:
- Demands 3 essential fields before subscription creation: WHAT condition, WHICH market/property, and EMAIL ADDRESS.
- Enforces conversational follow-up via `suggest_actions` if any field is missing.
- Explains that the subscription will be created in `PENDING` state and requires confirmation via email.
- Enforces anti-spam and privacy constraints (never store personal data beyond email).

---

## 5. Security Architecture & RLS Boundary

1. **Table Schema (`misc/alert_subscriptions_migration.sql`):**
   - Columns: `id`, `email`, `criteria_type`, `criteria`, `raw_request_text`, `confirmed`, `confirmation_token`, `unsubscribe_token`, `status`, `created_at`, `confirmed_at`, `last_checked_at`, `last_fired_at`.
   - Double opt-in default: `confirmed BOOLEAN NOT NULL DEFAULT false`, `status TEXT NOT NULL DEFAULT 'pending'`.
2. **Access Control:**
   - Frontend / Public agent role: `INSERT` only (cannot `SELECT`, `UPDATE`, or `DELETE`).
   - Service Role key (`SUPABASE_SERVICE_ROLE_KEY`): Used exclusively by backend API routes and workers to confirm, update `last_fired_at`, and unsubscribe.
3. **Caps:** Maximum 5 active subscriptions per email address to prevent abuse. Friendly error message returned if exceeded.

---

## 6. Endpoints (`routes/v1/alerts.py`)

- `GET /api/v1/alerts/confirm?token=<token>`: Validates confirmation token, activates subscription (`confirmed = true, status = 'active'`), returns branded HTML response.
- `GET /api/v1/alerts/unsubscribe?token=<token>`: Validates unsubscribe token, cancels subscription (`status = 'unsubscribed'`), returns branded confirmation.
- `POST /api/v1/internal/evaluate-alerts`: Protected internal endpoint called by scrapers or webhooks with `X-Internal-Secret` header to trigger evaluation runs.

---

## 7. Workers & Automation (`workers/`)

1. **`alert_evaluator.py`**:
   - Dispatches across the 9 event-driven criteria types.
   - Reuses existing audited RPCs (`get_spike_alerts`, `get_property_snapshot`, `get_rate_anomaly_report`, etc.).
   - Enforces 24-hour rolling cooldown (`ALERT_COOLDOWN_HOURS = 24`).
2. **`digest_sender.py`**:
   - Queries active `digest` subscriptions.
   - Compiles KPIs and average metrics via `get_real_estate_kpis` and `get_market_averages`.
   - Sends daily or weekly digests based on elapsed time since `last_fired_at`.
3. **`alert_cleanup.py`**:
   - Deletes unconfirmed subscriptions older than `CONFIRMATION_EXPIRY_HOURS = 48`.

---

## 8. Pluggable Email Service (`services/email_service.py`)

- Currently backed by Gmail SMTP via `SMTP_USER` and `SMTP_PASSWORD`.
- Configured via environment variables with fallback support:
  - `SMTP_HOST` (default: `smtp.gmail.com`)
  - `SMTP_PORT` (default: `587`)
  - `SMTP_USER` (default: `jouledynamicscto@gmail.com`)
  - `FROM_EMAIL` (default: `jouledynamicscto@gmail.com`)
  - `CONFIRMATION_BASE_URL` (default: `https://pulse.jouledynamics.com`)
- Ready to swap provider (e.g. Resend, Zoho) purely via `.env` configuration without modifying business logic.
