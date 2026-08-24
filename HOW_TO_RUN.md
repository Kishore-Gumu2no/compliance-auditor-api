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

