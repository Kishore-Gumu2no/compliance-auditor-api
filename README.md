# FairPay Engine

> **Empowering delivery partners with transparent earnings audit across Zomato, Swiggy, Uber Eats and more.**

---

## Problem & Solution

**Problem:** Delivery partners lack a central platform to track their earnings and make sure if they are being paid rightfully. Partners work 12+ hour shifts not knowing if they're below minimum wage.

**Solution:** FairPay Engine is a zero-trust compliance auditor. It streams live earnings via WebSocket, accepts evidence uploads with PII redacted *before* AI processing (Microsoft Presidio), runs automated audits through a LangGraph pipeline (OCR → Extraction → Qdrant labor-law RAG → Groq Llama 3.3 verdict), and returns SHA-256 receipted results. Personal data never stored; only anonymized outcomes retained.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  PUBLIC DEMO (Vercel)                                           │
│  React Frontend → Simulated WebSocket + Sample Audit Data       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  LOCAL FULL SYSTEM (Docker)                                     │
│  React Frontend ←→ FastAPI Backend ←→ AI/ML Pipeline            │
│                         │                                       │
│                         ├── LangGraph Orchestrator              │
│                         ├── LLM + Instructor                    │
│                         ├── Qdrant Vector DB (Temporal Laws)    │
│                         ├── Local Supabase Stack (PostgreSQL    │
│                         │    + Auth + REST + Realtime + Storage │
│                         │    + Studio) — ALL IN CONTAINERS      │
│                         ├── Tesseract OCR + pdfplumber          │
│                         └── Microsoft Presidio (PII Masking)    │
└─────────────────────────────────────────────────────────────────┘
```

**Key decision:** Frontend auto-detects environment. On `localhost` → real backend + local Supabase. Any other host (Vercel) → demo mode with simulated data.

---

## Tech Stack & Rationale

| Layer | Technology | Why This Choice |
|-------|------------|-----------------|
| **Frontend** | React 19 + TypeScript + Vite | Modern, type-safe, fast HMR; Vite builds in seconds |
| **Styling** | Origin Financial Design System (CSS custom properties) | Professional fintech aesthetic; design tokens; no CSS-in-JS overhead |
| **Backend** | FastAPI (Python 3.11) | Async-native, auto OpenAPI docs, Pydantic validation, WebSocket support |
| **Orchestration** | LangGraph | Stateful, cyclic graphs for multi-step audit pipeline; better than linear chains |
| **LLM** | Groq (Llama 3.3 70B) via Instructor | Sub-second inference; structured output enforcement; free tier |
| **Vector DB** | Qdrant Cloud | Managed, filtered vector search; temporal law queries need metadata filtering |
| **Database** | Local Supabase (PostgreSQL + Auth + REST + Realtime + Storage) | Full Supabase stack in containers; zero cloud dependency; data never leaves machine |
| **OCR** | Tesseract + pdfplumber | Open-source, handles images + PDFs; containerized |
| **PII Redaction** | Microsoft Presidio | Industry-standard NER + patterns; runs locally, zero data egress |
| **Containerization** | Docker + Docker Compose | One-command full stack; identical dev/prod; interviewer runs `docker-compose up` |
| **Demo Hosting** | Vercel | Zero-config deploy; edge network; `VITE_DEMO_MODE=true` bakes in sample data |

---

## Quick Start

### Full Local System (Single Command)
```bash
git clone <repo>
cd compliance-auditor
cp .env.example .env   # Add your GROQ_API_KEY, QDRANT_URL, QDRANT_API_KEY
docker-compose up --build
# Frontend: http://localhost:5173  |  Backend: http://localhost:8000  |  API Docs: /docs
# Supabase Studio: http://localhost:54323
```

**Requires:** Docker, Groq API key (free), Qdrant Cloud key (free tier).  
**No Supabase account needed** — runs entirely in local containers.

See **[HOW_TO_RUN.md](HOW_TO_RUN.md)** for detailed steps, troubleshooting, and architecture notes.

---

## Demo Deployment (Vercel)

Frontend-only demo with simulated data — no backend required:

1. Push to GitHub
2. Vercel → New Project → Root: `frontend/frontend`
3. Env: `VITE_DEMO_MODE=true`
4. Deploy

Live demo URL: fully functional UI with sample audits, simulated earnings, animated audit progress.