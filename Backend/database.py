"""Supabase client initialization."""

import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# Read from the environment. The key is never written in the source: it comes
# from .env (copy .env.example to .env), which is git-ignored.
SUPABASE_URL = os.getenv("SUPABASE_URL", "http://localhost:54321")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_KEY:
    raise RuntimeError(
        "SUPABASE_KEY is not set. Copy .env.example to .env (it carries the local "
        "development key) before starting the backend."
    )

# Initialize the Supabase client
supabase_db: Client = create_client(SUPABASE_URL, SUPABASE_KEY)