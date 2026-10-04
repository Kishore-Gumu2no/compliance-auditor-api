"""Supabase client initialization."""

import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# Load from environment variables (with local Docker defaults)
SUPABASE_URL = os.getenv("SUPABASE_URL", "http://localhost:54321")
SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNvbXBsaWFuY2UtYXVkaXRvciIsInJvbGUiOiJhbm9uIiwiaWF0IjoxNzM0NTY3MjAwLCJleHAiOjIwNTAxNDMyMDB9.RUTBxvLXcucyr3uc_8kwulN1lswinEB65uf2b9k49_w"
)

# Initialize the Supabase client
supabase_db: Client = create_client(SUPABASE_URL, SUPABASE_KEY)