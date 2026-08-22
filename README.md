# FairPay Engine

> **Empowering delivery partners with transparent earnings audit across Zomato, Swiggy, Uber Eats and more.**

---

## The Problem

Gig economy delivery partners have no visibility into whether they're being paid fairly. Platform algorithms are opaque, earnings statements are fragmented, and labor law compliance is unenforced. Partners work 12+ hour shifts without knowing if they're earning below minimum wage.

---

## The Solution

**FairPay Engine** is a zero-trust compliance auditing system that gives delivery partners cryptographic proof of their earnings compliance.

**What it does:**
1. **Live Earnings Streaming** — Real-time WebSocket ingestion from Zomato, Swiggy, Uber Eats
2. **Zero-Trust Evidence Upload** — Upload payslips/PDFs/images; PII is redacted *before* any AI processing (Microsoft Presidio)
3. **Automated Compliance Audit** — LangGraph orchestrates: OCR → Structured Extraction → Labor Law RAG (Qdrant) → Groq Llama 3.3 verdict
4. **Verifiable Results** — SHA-256 receipt IDs, discrepancy calculations, per-platform breakdowns
5. **Privacy-First** — Personal data never stored; only anonymized audit results retained

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

**Key architectural decision:** The frontend auto-detects environment. On `localhost` it connects to the real backend. On any other host (Vercel), it runs in **demo mode** with simulated data — zero backend dependency for public deployment.

---

## Tech Stack & Rationale

| Layer | Technology | Why This Choice |
|-------|------------|-----------------|
| **Frontend** | React 19 + TypeScript + Vite | Modern, type-safe, fast HMR; Vite builds in seconds |
| **Styling** | Origin Financial Design System (CSS custom properties) | Professional fintech aesthetic; design tokens for consistency; no runtime CSS-in-JS overhead |
| **Backend** | FastAPI (Python 3.11) | Async-native, auto OpenAPI docs, Pydantic validation, WebSocket native support |
| **Orchestration** | LangGraph | Stateful, cyclic graphs for multi-step audit pipeline; better than linear chains for retries/branching |
| **LLM** | Groq (Llama 3.3 70B) via Instructor | Sub-second inference; structured output enforcement; free tier for dev |
| **Vector DB** | Qdrant Cloud | Managed, filtered vector search; temporal law queries need metadata filtering |
| **Database** | Supabase (PostgreSQL) | Auth + Realtime + Storage + Postgres in one; row-level security for audit isolation |
| **OCR** | Tesseract + pdfplumber | Open-source, handles both images and PDFs; runs in container |
| **PII Redaction** | Microsoft Presidio | Industry-standard NER + pattern recognition; runs locally, zero data egress |
| **Containerization** | Docker + Docker Compose | One-command full stack; identical dev/prod; interviewer runs `docker-compose up` |
| **Demo Hosting** | Vercel | Zero-config deploy; edge network; `VITE_DEMO_MODE=true` bakes in sample data |

---

## Quick Start

### Full Local System (Recommended for Interviews)
```bash
git clone <repo>
cd compliance-auditor
cp .env.example .env   # Add your GROQ, QDRANT, SUPABASE keys
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

## Project Structure (Essential)

```
compliance-auditor/
├── Backend/                 # FastAPI + LangGraph + AI Pipeline
│   ├── api.py              # REST (/audit/) + WebSocket (/stream)
│   ├── orchestrator.py     # LangGraph audit workflow
│   ├── sanitizer.py        # Presidio PII masking
│   ├── real_laws.json      # Seed labor law corpus
│   ├── requirements.txt
│   └── Dockerfile
├── demo-data/              # Sample audits for Vercel (no backend)
├── frontend/frontend/      # React 19 + TypeScript + Vite
│   ├── src/services/api.ts       # WebSocket client + REST + demo fallback
│   ├── src/services/demoData.ts  # 3 sample audits, 15+ day periods
│   ├── src/App.tsx               # 5 sections: Live, Why, Upload, Privacy, Results
│   └── src/App.css               # Full Origin Financial design system
├── docker-compose.yml      # Backend + Frontend(dev/prod)
├── supabase-docker-compose.yml  # Optional local Supabase
└── .env.example            # All required keys documented
```

---

## What an Interviewer Sees in 30 Seconds

| Demo Link (Vercel) | Local Docker | Code |
|--------------------|--------------|------|
| Working UI with live earnings simulation | Real WebSocket + AI audit pipeline | Clean architecture, typed end-to-end |
| Upload PDF → animated audit progress | Real OCR → PII redaction → LLM verdict | Zero-trust: redaction *before* AI |
| Click sample audits → smooth scroll results | Real Qdrant vector search + Groq | LangGraph orchestration, not spaghetti |

---

**Built for delivery partner empowerment.** ⚖️