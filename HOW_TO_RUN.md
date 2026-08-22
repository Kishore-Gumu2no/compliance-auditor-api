# How to Get FairPay Engine Running on Your System

## The Only Path

```bash
# 1. Clone the repository
git clone https://github.com/your-username/compliance-auditor
cd compliance-auditor

# 2. Get your API keys (required)
#    - Groq API Key (free):     https://console.groq.com/keys
#    - Qdrant Cloud URL + Key:  https://cloud.qdrant.io/ (free tier)
#    Create a .env file:
cp .env.example .env
# Edit .env and paste your keys

# 3. Run the full stack (backend + frontend + local PostgreSQL + Supabase services)
docker-compose up --build
```

**That's it.** Frontend at `http://localhost:5173`, Backend API at `http://localhost:8000`, API Docs at `http://localhost:8000/docs`, Supabase Studio at `http://localhost:54323`.

---

## What You Need Before Running

### 1. Groq API Key (Required)
- Go to: **https://console.groq.com/keys**
- Sign up (free) → Create API Key
- Paste into `.env` as `GROQ_API_KEY=gsk_...`
- Used for: LLM inference (Llama 3.3 70B) — audit reasoning

### 2. Qdrant Cloud URL + API Key (Required)
- Go to: **https://cloud.qdrant.io/**
- Sign up (free tier: 1GB, 1M vectors) → Create Cluster
- Copy **URL** and **API Key**
- Paste into `.env` as `QDRANT_URL=https://xxx.qdrant.io` and `QDRANT_API_KEY=...`
- Used for: Vector search over temporal labor laws

### 3. Docker & Docker Compose (Required)
- Install: **https://docs.docker.com/get-docker/**
- Verify: `docker --version` && `docker compose version`

---

## What Runs Automatically (Single `docker-compose.yml`)

| Service | Port | Purpose |
|---------|------|---------|
| **Frontend (Vite)** | 5173 | React app with hot reload |
| **Backend (FastAPI)** | 8000 | REST `/audit/` + WebSocket `/stream` |
| **PostgreSQL** | 54322 | Audit logs, earnings data (local only) |
| **PostgREST** | 54321 | Auto-generated REST API on Postgres |
| **GoTrue Auth** | 54322 | JWT authentication |
| **Supabase Studio** | 54323 | Web UI to inspect database |
| **Realtime** | 54324 | WebSocket subscriptions |
| **Storage** | 54325 | File uploads (evidence PDFs/images) |

**All databases run locally in containers.** No external Supabase account needed. No cloud PostgreSQL. Your data stays on your machine.

---

## The `.env` File (Create from `.env.example`)

```env
# AI Model (Groq - Llama 3.3 70B)
GROQ_API_KEY=gsk_your_key_here

# Vector Database (Qdrant Cloud)
QDRANT_URL=https://your-cluster.qdrant.io
QDRANT_API_KEY=your_qdrant_key

# Local Supabase stack (FIXED — do not change)
# These point to internal Docker containers
SUPABASE_URL=http://localhost:54321
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNvbXBsaWFuY2UtYXVkaXRvciIsInJvbGUiOiJhbm9uIiwiaWF0IjoxNzM0NTY3MjAwLCJleHAiOjIwNTAxNDMyMDB9.AdL3r5N5LQ8xK8xK8xK8xK8xK8xK8xK8xK8xK8xK8x

# OCR
TESSERACT_PATH=/usr/bin/tesseract
```

**Only edit `GROQ_API_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`.** The Supabase values are fixed for local containers.

---

## Common Issues

| Problem | Fix |
|---------|-----|
| `SUPABASE_URL not set` | You forgot to create `.env` from `.env.example` |
| Backend crashes on startup | Check `docker logs fairpay-backend` — usually missing Groq/Qdrant keys |
| Frontend shows "Demo Mode" | You're on `localhost` — it should connect to real backend. Check backend logs. |
| Port 5432 already in use | Stop local Postgres: `sudo systemctl stop postgresql` (Linux) or close PostgreSQL app (Mac/Windows) |
| "Healthy" never appears | Wait 60–90s — Supabase stack takes time. Run `docker ps` to see status. |

---

## Stopping & Cleaning Up

```bash
# Stop everything (keeps data in ./supabase/data and ./supabase/storage)
docker-compose down

# Stop AND delete all data (database, uploads, volumes)
docker-compose down -v
```

---

## Architecture Note

This project uses **local Supabase only**. The single `docker-compose.yml` spins up a complete Supabase stack (PostgreSQL + Auth + REST + Realtime + Storage + Studio) entirely in Docker containers on your machine. There is **no cloud Supabase option** — your audit data never leaves your system.