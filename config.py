import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

APP_WRITE_PROJECT_ID = os.environ.get("APP_WRITE_PROJECT_ID", "")
APP_WRITE_API_ENDPOINT = os.environ.get("APP_WRITE_API_ENDPOINT", "")
APP_WRITE_API_KEY = os.environ.get("APP_WRITE_API_KEY", "")
APP_WRITE_BUCKET_ID = os.environ.get("APP_WRITE_BUCKET_ID", "")
