"""Supabase client initialization."""

import os
from supabase import create_client, Client

# Load from environment variables (with local Docker defaults)
SUPABASE_URL = os.getenv("SUPABASE_URL", "http://localhost:54321")
SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNvbXBsaWFuY2UtYXVkaXRvciIsInJvbGUiOiJhbm9uIiwiaWF0IjoxNzM0NTY3MjAwLCJleHAiOjIwNTAxNDMyMDB9.AdL3r5N5LQ8xK8xK8xK8xK8xK8xK8xK8xK8xK8xK8x"
)

# Initialize the Supabase client
supabase_db: Client = create_client(SUPABASE_URL, SUPABASE_KEY)