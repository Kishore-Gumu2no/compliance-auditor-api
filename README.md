# FairPay Engine

> **Empowering delivery partners with transparent earnings audit across Zomato, Swiggy, Uber Eats and more.**

FairPay Engine is a compliance auditing system for gig economy delivery partners. It tracks real-time earnings across multiple platforms, allows evidence upload for automated compliance audits against labor laws, and provides cryptographically verifiable audit receipts — all with a zero-trust architecture that never stores personal data.

## 🎯 Features

- **Live Earnings Dashboard** — Real-time WebSocket streaming from Zomato, Swiggy, Uber Eats
- **Zero-Trust Evidence Upload** — Images/PDFs processed with automatic PII redaction
- **Automated Compliance Audit** — AI-powered extraction verified against temporal labor law database
- **Transparent Results** — SHA-256 receipt IDs, discrepancy calculations, detailed explanations
- **Privacy-First** — Personal data never stored; only anonymized audit results retained
- **Demo Mode** — Fully functional Vercel deployment with sample data

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        PUBLIC DEMO (Vercel)                     │
│  React Frontend  ──►  Sample Data (No Backend Required)        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      LOCAL FULL SYSTEM (Docker)                 │
│  React Frontend  ──►  FastAPI Backend  ──►  AI/ML Services     │
│       │                  │                    │                 │
│       │                  ├── LangGraph Orchestrator             │
│       │                  ├── Groq (Llama 3.3) + Instructor      │
│       │                  ├── Qdrant Vector DB (Labor Laws)      │
│       │                  ├── Supabase (Audit Logs)              │
│       │                  ├── Tesseract OCR + pdfplumber         │
│       │                  └── Microsoft Presidio (PII Masking)   │
└─────────────────────────────────────────────────────────────────┘
```

## 🚀 Quick Start

### Prerequisites

- **Docker & Docker Compose** (for full local system)
- **Node.js 20+** (for frontend development)
- **Python 3.11+** (for backend development)
- **Tesseract OCR** installed locally

### Option 1: Full Local System (Recommended)

```bash
# 1. Clone and navigate
git clone <repository-url>
cd compliance-auditor

# 2. Create environment file
cp .env.example .env
# Edit .env with your API keys (GROQ, QDRANT, SUPABASE)

# 3. Start all services
docker-compose up --build

# 4. Access
# Frontend: http://localhost:5173 (dev) or http://localhost:3000 (prod)
# Backend API: http://localhost:8000
# API Docs: http://localhost:8000/docs
```

### Option 2: Frontend Only (Demo Mode)

```bash
cd frontend/frontend

# Install dependencies
npm install

# Run in demo mode (no backend needed)
VITE_DEMO_MODE=true npm run dev

# Or build for production
VITE_DEMO_MODE=true npm run build
```

### Option 3: Backend Only (Local Development)

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export GROQ_API_KEY=your_key
export QDRANT_URL=your_url
export QDRANT_API_KEY=your_key
export SUPABASE_URL=your_url
export SUPABASE_KEY=your_key
export TESSERACT_PATH=/usr/bin/tesseract  # or Windows path

# Run server
uvicorn api:server --host 127.0.0.1 --port 8000 --reload
```

## 📦 Deployment

### Vercel (Frontend Demo)

1. Connect repository to Vercel
2. Set Root Directory: `frontend/frontend`
3. Add Environment Variable: `VITE_DEMO_MODE=true`
4. Deploy

The demo will be fully functional with sample audit data — no backend required.

### Docker Production

```bash
# Build and run production stack
docker-compose --profile prod up --build -d
```

## 🔧 Environment Variables

### Backend (.env)

```env
# AI Models
GROQ_API_KEY=your_groq_api_key

# Vector Database (Qdrant)
QDRANT_URL=your_qdrant_url
QDRANT_API_KEY=your_qdrant_key

# Relational Database (Supabase)
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_key

# OCR
TESSERACT_PATH=/usr/bin/tesseract
```

### Frontend

| Variable | Description | Default |
|----------|-------------|---------|
| `VITE_API_BASE_URL` | Backend API URL | `http://127.0.0.1:8000` |
| `VITE_DEMO_MODE` | Enable demo/sample data | `false` |

## 📁 Project Structure

```
compliance-auditor/
├── api.py                    # FastAPI application (REST + WebSocket)
├── orchestrator.py           # LangGraph audit pipeline
├── sanitizer.py              # PII masking with Presidio
├── real_laws.json            # Labor law seed data
├── requirements.txt          # Python dependencies
├── Dockerfile                # Backend container
├── docker-compose.yml        # Multi-service orchestration
├── test_suite.py             # Integration tests
├── frontend/
│   └── frontend/             # React + Vite + TypeScript
│       ├── src/
│       │   ├── services/
│       │   │   ├── api.ts           # Backend API client
│       │   │   └── demoData.ts      # Sample audit data
│       │   ├── App.tsx              # Main application
│       │   ├── App.css              # Design system styles
│       │   ├── main.tsx             # Entry point
│       │   └── index.css            # Global styles
│       ├── public/
│       ├── index.html
│       ├── package.json
│       ├── vite.config.ts
│       ├── Dockerfile.dev
│       ├── Dockerfile.prod
│       └── nginx.conf
└── README.md
```

## 🔌 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/audit/` | Upload evidence (images/PDFs) for audit |
| `WS` | `/stream` | Real-time earnings WebSocket |

### WebSocket Message Format

**Client → Server:**
```json
{ "platform": "Zomato", "earnings": 4000 }
```

**Server → Client:**
```json
"Logged earnings of 4000 from Zomato"
```

### Audit Response

```json
{
  "status": "Audit Complete",
  "ai_analysis": {
    "final_status": "Underpaid (Below Minimum Wage)",
    "receipt_id": "a1b2c3d4e5f6..."
  }
}
```

## 🧪 Testing

```bash
# Run backend integration tests
python test_suite.py

# Frontend type checking
cd frontend/frontend && npx tsc --noEmit

# Frontend linting
npm run lint
```

## 🎨 Design System

Based on **Origin Financial** design language:
- **Typography**: DM Serif Display (headlines) + Inter (UI) + Roboto Mono (data)
- **Colors**: Obsidian canvas, Graphite cards, Cyan Signal accents
- **Layout**: 1200px max-width, 80px section gaps, 32px card padding
- **Motion**: 0.2s ease transitions, 2.5s atmospheric reveals

## 📄 License

MIT License — Built for delivery partner empowerment.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests and type checks
5. Submit a PR

---

**FairPay Engine** — Because every delivery partner deserves their rightful pay. ⚖️