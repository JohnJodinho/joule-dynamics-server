import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_ROUTE_MODEL = os.environ.get("GROQ_ROUTE_MODEL", "openai/gpt-oss-20b")
GROQ_SYNTHESIS_MODEL = os.environ.get("GROQ_SYNTHESIS_MODEL", "llama-3.3-70b-versatile")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

APP_WRITE_PROJECT_ID = os.environ.get("APP_WRITE_PROJECT_ID", "")
APP_WRITE_API_ENDPOINT = os.environ.get("APP_WRITE_API_ENDPOINT", "")
APP_WRITE_API_KEY = os.environ.get("APP_WRITE_API_KEY", "")
APP_WRITE_BUCKET_ID = os.environ.get("APP_WRITE_BUCKET_ID", "")

MAPBOX_ACCESS_TOKEN = os.environ.get("MAPBOX_ACCESS_TOKEN", "")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "hello@example.com")
CONTACT_WHATSAPP = os.environ.get("CONTACT_WHATSAPP", "1234567890")
