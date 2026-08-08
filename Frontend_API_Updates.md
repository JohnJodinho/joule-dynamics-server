# Frontend API Updates: Agentic Chat Upgrades

This document outlines the recent architectural upgrades to the Real Estate Intelligence Layer chat endpoint (`/api/v1/real-estate/chat`). These changes introduce multi-turn conversational memory, interactive clarification buttons, document generation, and markdown formatting. 

## 1. Multi-Turn Conversational Memory (Stateful `session_id`)

The backend now fully supports multi-turn conversations by retaining chat history in-memory using the `session_id`.

**Frontend Implementation:**
- Ensure that the frontend generates a unique string for `session_id` when a chat session starts.
- **CRITICAL:** You must pass the *same* `session_id` on every subsequent request within that chat session. If the `session_id` changes or is omitted, the backend will treat the message as a brand-new, isolated conversation and lose context.

## 2. Interactive Clarification Buttons (`suggested_actions`)

When a user's query is ambiguous or when a tool requires more parameters (e.g., asking for "market averages" without specifying a city), the LLM will now generate a set of clickable options to clarify the request instead of forcing the user to type.

**Frontend Implementation:**
- The `ChatResponse` model now includes a new optional field: `suggested_actions: List[str]`.
- Example Response:
  ```json
  {
    "reply": "I need more specific details. Here are some options to clarify your request:",
    "path_used": "PATH_A",
    "tools_called": [],
    "suggested_actions": ["Rental Rates", "Property Prices", "Other (please specify)"]
  }
  ```
- If the `suggested_actions` array is not empty, the frontend should render these strings as clickable buttons below the assistant's reply.
- When a user clicks a button, the frontend should send the button's text as the next `message` payload (using the same `session_id`).

## 3. Document Exports & Markdown Rendering

The LLM is now strictly instructed to output its replies using **Markdown** formatting (including tables, bold text, lists). Furthermore, it has access to a new `generate_data_export` tool which can generate CSV or Markdown reports on-the-fly and upload them to Appwrite Storage.

**Frontend Implementation:**
- The frontend chat UI must be capable of rendering standard Markdown into HTML.
- When the user asks to "download this data" or "export a CSV", the backend will generate the file, upload it to Appwrite, and return a Markdown link in the `reply` text (e.g., `[Download your CSV Report here](https://fra.cloud.appwrite.io/v1/storage/...)`). 
- The frontend's markdown parser should seamlessly render this as a standard clickable anchor tag (`<a>`) so the user can download the file.
