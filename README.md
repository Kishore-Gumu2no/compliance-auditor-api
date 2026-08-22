# FairPay Engine

> **Empowering delivery partners with transparent earnings audit across Zomato, Swiggy, Uber Eats and more.**

---

## Problem & Solution (98 words)

**Problem:** Gig delivery partners have no visibility into pay fairness. Platform algorithms are opaque, earnings fragmented, labor law compliance unenforced. Partners work 12+ hour shifts not knowing if they're below minimum wage.

**Solution:** FairPay Engine is a zero-trust compliance auditor. It streams live earnings via WebSocket, accepts evidence uploads with PII redacted *before* AI processing (Microsoft Presidio), runs automated audits through a LangGraph pipeline (OCR → Extraction → Qdrant labor-law RAG → Groq Llama 3.3 verdict), and returns SHA-256 receipted results. Personal data never stored; only anonymized outcomes retained.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  PUBLIC DEMO (Vercel)                                           │
│  React Frontend → Simulated WebSocket + Sample Audit Data      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  LOCAL FULL SYSTEM (Docker)                                     │
│  React Frontend ←→ FastAPI Backend ←→ AI/ML Pipeline           │
│                         │                                       │
│                         ├── LangGraph Orchestrator              │
│                         ├── Groq (Llama 3.3 70B) + Instructor   │
│                         ├── Qdrant Vector DB (Temporal Laws)    │
│                         ├── Supabase (Audit Logs + Auth)        │
│                         ├── Tesseract OCR + pdfplumber          │
│                         └── Microsoft Presidio (PII Masking)    │
└─────────────────────────────────────────────────────────────────┘
```

**Key decision:** Frontend auto-detects environment. On `localhost` → real backend. Any other host (Vercel) → demo mode with simulated data — zero backend dependency.

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
| **Database** | Supabase (PostgreSQL) | Auth + Realtime + Storage + Postgres; row-level security for audit isolation |
| **OCR** | Tesseract + pdfplumber | Open-source, handles images + PDFs; containerized |
| **PII Redaction** | Microsoft Presidio | Industry-standard NER + patterns; runs locally, zero data egress |
| **Containerization** | Docker + Docker Compose | One-command full stack; identical dev/prod; interviewer runs `docker-compose up` |
| **Demo Hosting** | Vercel | Zero-config deploy; edge network; `VITE_DEMO_MODE=true` bakes in sample data |

---

## Quick Start

### Full Local System (Recommended for Interviews)
```bash
git clone <repo>
cd compliance-auditor
cp .env.example .env   # Add GROQ, QDRANT, SUPABASE keys
docker-compose up --build
# Frontend: http://localhost:5173  |  Backend: http://localhost:8000  |  API Docs: /docs
```

### Frontend Demo Only (No Keys Needed)
```bash
cd frontend/frontend
npm install
VITE_DEMO_MODE=true npm run dev
```

### Deploy to Vercel (3 minutes)
1. Push to GitHub
2. Vercel → New Project → Root: `frontend/frontend`
3. Env: `VITE_DEMO_MODE=true`
4. Deploy → Live demo URL

---

**Built for delivery partner empowerment.** ⚖️