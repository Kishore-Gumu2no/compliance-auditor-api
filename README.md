# FairPay Engine

> **Empowering delivery partners with transparent earnings audit across Zomato, Swiggy, Uber Eats and more.**

A delivery partner finishes a 12-hour shift and ₹1,400 lands in the app. Was that the right number?

Today there is no way to know. The contract rates, the incentives that should have applied, the deductions that were taken — all of it stays inside the platform's app, and none of it is itemised.

**FairPay Engine recalculates every single order from the partner's own payout documents, then shows exactly where the money went missing.** Send a screenshot to a Telegram bot, get back an order-by-order breakdown of what should have been paid.

---

## Problem & Solution

**Problem:** Delivery partners lack a central platform to track their earnings and make sure if they are being paid rightfully. Partners work 12+ hour shifts not knowing if they're below minimum wage.

**Solution:** FairPay Engine is a zero-trust compliance auditor. It reconstructs each order's contractual pay from the platform's own rate card, compares it against what was actually settled, and grades every gap against Indian labour law. Evidence arrives through a Telegram bot with PII redacted *before* any AI sees it (Microsoft Presidio). Audits run through a LangGraph pipeline (Extraction → Reconciliation → labour-law lookup → Groq GPT-OSS-120B findings). Uploaded documents are content-addressed by SHA-256 and deleted from disk as soon as the audit finishes; personal data is never stored, and only anonymised outcomes are retained.

---

## What It Does

### Submit evidence over Telegram

Partners never have to learn a dashboard. Everything happens in the chat app they already have:

```
/link BOBBY-001        ties this chat to a partner
Zomato                 announces a platform
[payout screenshots / PDF statements]
Swiggy                 switch platforms, send more
PLEASE AUDIT           runs the full pipeline
```

Documents are read with OCR (Tesseract + pdfplumber), stripped of PII, and handed to the same audit engine the website uses. Redaction fails closed: if Presidio can't mask an image or a page of text, that evidence is never sent to the LLM at all. The bot is an evidence and notification channel only — it never calculates anything itself, and the detailed findings open on the website. When a run finishes, the site shows a notice that the result is waiting.

### See what you could have earned

The **Earnings Opportunity Simulator** answers "what if I worked differently?" across three scenarios:

| Scenario | Question it answers |
|----------|---------------------|
| `hours` | What if I went online 2 more hours a day? |
| `days` | What if I worked 24 days instead of 20? |
| `expenses` | What if my fuel costs dropped by ₹500? |

Projections are plain arithmetic over the partner's own observed averages — no model, no randomness, so the same inputs always produce the same numbers. Each projection carries the assumptions behind it, and completed simulations are saved so they can be revisited. The baseline comes either from the partner's 30-day history or from a finished Telegram audit. When the documents don't show hours or days worked, the simulator refuses with a plain explanation instead of guessing.

**These are estimates, never guaranteed income.**

### Audit many partners at once

Upload a synthetic dataset — `partners`, `earnings`, `settlements`, plus ground truth — and the engine audits every partner in it, then scores itself against the known answers: precision, recall and F1, broken down by anomaly type and by finding category.

This runs the **real** calculation and findings code, not a test double — the same `calculations.py` and `findings.py` that serve live audits. Because the input is already structured JSON, the pipeline skips OCR and enters at contract resolution, which is exactly the branch the orchestrator's router takes whenever transactions arrive pre-parsed.

### Evidence in, nothing left behind

Uploaded PDFs and screenshots are written to disk under a SHA-256 content hash, used for the audit, then removed in a `finally` block whether the run succeeds or fails. Nothing a partner sends survives the request.

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
│                         │     evidence → versioning →           │
│                         │     calculation → findings →          │
│                         │     controller                        │
│                         ├── LLM + Instructor                    │
│                         ├── Qdrant Vector DB (Temporal Laws)    │
│                         ├── Local Supabase Stack (PostgreSQL    │
│                         │    + Auth + REST + Realtime + Storage │
│                         │    + Studio) — ALL IN CONTAINERS      │
│                         ├── Tesseract OCR + pdfplumber          │
│                         └── Microsoft Presidio (PII Masking)    │
│                                     ▲                           │
│  Telegram Bot ──────────────────────┘                           │
│  (runs on your machine)                                         │
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
| **LLM** | Groq (GPT-OSS-120B; Qwen vision model for screenshots) via Instructor | Sub-second inference; structured output enforcement; free tier |
| **Vector DB** | Qdrant Cloud (optional; bundled local law file used by default) | Managed, filtered vector search; temporal law queries need metadata filtering |
| **Database** | Local Supabase (PostgreSQL + Auth + REST + Realtime + Storage) | Full Supabase stack in containers; zero cloud dependency; data never leaves machine |
| **Evidence intake** | Telegram Bot API | Partners already have it installed; no app to download, no login to remember |
| **OCR** | Tesseract + pdfplumber | Open-source, handles images + PDFs; containerized |
| **PII Redaction** | Microsoft Presidio | Industry-standard NER + patterns; runs locally, zero data egress |
| **Containerization** | Docker + Docker Compose | One-command full stack; identical dev/prod; interviewer runs `docker compose --profile dev up` |
| **Demo Hosting** | Vercel | Zero-config deploy; edge network; `VITE_DEMO_MODE=true` bakes in sample data |

---

## Quick Start

### Full Local System
```bash
git clone https://github.com/Kishore-Gumu2no/compliance-auditor-api.git
cd compliance-auditor-api
copy .env.example .env   # Windows (cp on macOS/Linux) - then add your GROQ_API_KEY
docker compose --profile dev up --build
# Frontend: http://localhost:5173  |  Backend: http://localhost:8000  |  API Docs: /docs
# Supabase Studio: http://localhost:54323
```

**Requires:** Docker Desktop and a Groq API key (free). Qdrant is optional.
The database is created and seeded with Bobby's 30-day dataset automatically on first start.
**No Supabase account needed** — runs entirely in local containers. The optional Telegram bot runs on your machine.

### Telegram bot (optional)
```bash
# Add TELEGRAM_BOT_TOKEN to .env, then:
cd Backend
python telegram_bot.py
```
Runs standalone — it does not touch the FastAPI app or the WebSocket stream.

See **[HOW_TO_RUN.md](HOW_TO_RUN.md)** for detailed steps, troubleshooting, and architecture notes.
