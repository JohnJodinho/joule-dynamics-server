import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")


GROQ_ROUTE_MODEL = os.environ.get("GROQ_ROUTE_MODEL", "openai/gpt-oss-20b")
GROQ_FALLBACK_ROUTE_MODEL = os.environ.get(
    "GROQ_FALLBACK_ROUTE_MODEL", "groq/compound-mini"
)


GROQ_SYNTHESIS_MODEL = os.environ.get("GROQ_SYNTHESIS_MODEL", "openai/gpt-oss-120b")
GROQ_FALLBACK_SYNTHESIS_MODEL = os.environ.get(
    "GROQ_FALLBACK_SYNTHESIS_MODEL", "qwen/qwen3.6-27b"
)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

APP_WRITE_PROJECT_ID = os.environ.get("APP_WRITE_PROJECT_ID", "")
APP_WRITE_API_ENDPOINT = os.environ.get("APP_WRITE_API_ENDPOINT", "")
APP_WRITE_API_KEY = os.environ.get("APP_WRITE_API_KEY", "")
APP_WRITE_BUCKET_ID = os.environ.get("APP_WRITE_BUCKET_ID", "")

MAPBOX_ACCESS_TOKEN = os.environ.get("MAPBOX_ACCESS_TOKEN", "")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "hello@example.com")
CONTACT_WHATSAPP = os.environ.get("CONTACT_WHATSAPP", "1234567890")

# ── Market Registry ───────────────────────────────────────────────────────────
# How often (seconds) the background task should re-fetch the market list.
# Default 300 s (5 min). Set to 0 to disable automatic background refresh.
MARKET_CACHE_TTL_SECONDS = int(os.environ.get("MARKET_CACHE_TTL_SECONDS", "300"))

# ── Geocoding ─────────────────────────────────────────────────────────────────
# Optional ISO 3166-1 alpha-2 country code(s) to restrict Mapbox geocoding
# (e.g. "US" for US-only, "NG" for Nigeria-only, "US,NG" for both).
# Leave empty (default) to allow global geocoding across all tracked markets.
GEOCODE_COUNTRY_FILTER = os.environ.get("GEOCODE_COUNTRY_FILTER", "")
