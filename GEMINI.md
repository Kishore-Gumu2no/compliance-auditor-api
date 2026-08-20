# Compliance Auditor - Project Instructions & Guidelines

Welcome! This document provides an architectural blueprint, environment setup details, development conventions, and running instructions for the **Compliance Auditor** project. Use this as your primary context guide for future interactions, development, and enhancements.

---

## 1. Project Overview

The **Compliance Auditor** is a specialized backend system designed to protect gig economy workers from wage theft and platform discrepancy. It audits worker earnings by extracting data from work shifts, redacting PII, checking payout details against a vector-stored database of local labor laws, calculating discrepancy margins, and keeping transparent logs.

### Main Technologies:
*   **Web Framework:** [FastAPI](https://fastapi.tiangolo.com/) with asynchronous capabilities and WebSockets.
*   **Orchestration Engine:** [LangGraph](https://langchain-ai.github.io/langgraph/) to construct a structured pipeline for multi-step document audits.
*   **Structured AI Extraction:** [Instructor](https://github.com/jxnl/instructor) with [Groq](https://groq.com/) (using `llama-3.3-70b-versatile`) to enforce strict JSON schemas on unstructured LLM output.
*   **Sanitization (PII Masking):** [Microsoft Presidio](https://microsoft.github.io/presidio/) (Analyzer & Anonymizer) to scrub Personal Identifiable Information (Person, Phone, Email) before LLM ingestion.
*   **Vector Database & Temporal RAG:** [Qdrant](https://qdrant.tech/) and [FastEmbed](https://qdrant.github.io/fastembed/) to index, store, and query year-specific local labor laws.
*   **Relational Database:** [Supabase](https://supabase.com/) to log audit execution records, explainability metrics, and streamed platform earnings.
*   **OCR & File Parsing:** `pytesseract` (Tesseract OCR) and `pdfplumber` (PDF parsing).

---

## 2. System Architecture & Pipeline Workflow

The compliance audit process follows a highly orchestrated **LangGraph StateGraph** pipeline:

```
[ START ]
    │
    ├──► (Has PDF?) ──► [ pdf_ingestion ] ──┐
    │                                       ▼
    └──► (Has Image?) ─────────────────► [ ocr_ingestion ]
                                            │
                                            ▼
                                     [ mask_data ]
                                            │
                                            ▼
                                    [ ai_extraction ]
                                            │
                                            ▼
                                   [ data_cleaning ]
                                            │
                                            ▼
                                    [ calculation ] ──► [ END ]
```

### LangGraph Nodes:
1.  **`pdf_ingestion` / `ocr_ingestion`**: Extract raw text from uploaded files using `pdfplumber` or Tesseract OCR.
2.  **`mask_data`**: Redacts worker personal info (emails, phone numbers, names) from extracted texts to preserve privacy.
3.  **`ai_extraction`**: Forwards masked text to Llama 3.3 via Instructor, enforcing a precise `RawAIExtraction` schema (`shift_date`, `extracted_hours`, `extracted_minutes`, `rate`, `actual_payout`).
4.  **`data_cleaning`**: Converts hourly durations from minutes to decimals (e.g. `8 hours 30 mins` becomes `8.5`) to prepare for math engines.
5.  **`calculation`**: Validates the session schema, performs temporal RAG on Qdrant to find relevant local minimum wages, verifies compliance, calculates discrepancy margins, constructs an immutable audit SHA-256 hash receipt, and writes transparent records to Supabase.

---

## 3. Directory & Key Files Overview

*   **`api.py`**: The main FastAPI application. Handles HTTP file upload endpoints (`/audit/`) and incoming earnings stream WebSockets (`/stream`). Integrates Lifespan events to manage asynchronous delivery streaming simulations.
*   **`orchestrator.py`**: The LangGraph execution and state engine. Houses vector database seed routines, pipeline node actions, math calculations, schema enforcement, and Supabase integration.
*   **`sanitizer.py`**: Contains `mask_pii()` using Presidio engines to recognize and mask email addresses, names, and phone numbers.
*   **`real_laws.json`**: Static seed dataset defining historical labor laws (e.g. Karnataka minimum wage rates across multiple years).
*   **`test_suite.py`**: Integration testing framework. Spins up a mock uvicorn server in the background, executes HTTP and WebSocket client calls, verifies behavior, and shuts down cleanly.
*   **`Dockerfile`**: Docker image containerization using Python 3.11-slim, installing necessary Linux components (`tesseract-ocr`, `libtesseract-dev`, `poppler-utils`).
*   **`requirements.txt`**: Complete set of Python dependency requirements.

---

## 4. Setup & Running Instructions

### System Prerequisites
Ensure that **Tesseract OCR** is installed on your operating system:
*   **Windows**: Download installer and set environment path (default target: `C:\Program Files\Tesseract-OCR\tesseract.exe`).
*   **Linux/macOS**: Install via package manager (e.g., `sudo apt-get install tesseract-ocr libtesseract-dev poppler-utils` or `brew install tesseract`).

### Environment Variables
Create a `.env` file in the root directory containing:
```env
# AI Models
GROQ_API_KEY=your_groq_api_key_here

# Vector Database (Qdrant)
QDRANT_URL=your_qdrant_instance_url
QDRANT_API_KEY=your_qdrant_api_key

# Relational Database (Supabase)
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_anon_or_service_key

# OCR Paths
TESSERACT_PATH=C:\Program Files\Tesseract-OCR\tesseract.exe
```

### Installation
Activate your virtual environment and run:
```bash
pip install -r requirements.txt
```

### Running the API Server
To start the FastAPI web server locally:
```bash
uvicorn api:server --host 127.0.0.1 --port 8000 --reload
```
Or via the FastAPI CLI:
```bash
fastapi run api.py --port 8000
```

### Docker deployment
To run within a containerized environment:
```bash
docker build -t compliance-auditor .
docker run -p 8000:8000 --env-file .env compliance-auditor
```

---

## 5. Testing Guidelines

The project is backed by an automated integration test suite that tests both the standard REST endpoints and WebSocket channels.

To execute the test suite:
```bash
python test_suite.py
```

*Note: Ensure Tesseract-OCR is installed and your environment variables in `.env` are configured properly prior to running tests, as the integration tests interact with real AI providers, databases, and OCR libraries.*

---

## 6. Development & Coding Conventions

To keep codebase additions coherent and aligned with the current architecture, adhere to the following development guidelines:

### A. Pedagogical & Explanatory Comment Style
The codebase contains detailed inline explanations, often formatted in `#region` / `#endregion` blocks and triple-quoted docstrings. These sections explicitly break down:
*   **What a piece of code does**
*   **Why a certain approach was taken**
*   **How it operates under the hood**

When writing new features, maintain this explanatory and descriptive writing style to assist future developers and maintainers.

### B. High-Precision Calculations (Decimal Type)
*   **Do not use floats for currency, earnings, rates, or exact time calculations.** Standard Python floats suffer from binary representation limitations (base 2 vs base 10), which can introduce precision errors in financial audits.
*   Always use `Decimal` from the `decimal` module for precision operations.
*   Only cast to `float` or `str` when serializing data for APIs (e.g. within Pydantic model outputs or final database payloads for systems that do not natively handle Python's `Decimal` type).

### C. Resource Safety (Context Managers)
*   Always wrap file operations, OCR requests, and PDF streams in `with` context managers (e.g., `with pdfplumber.open(path)`, `with Image.open(path)`).
*   This mathematically guarantees that file descriptors and OS memory resources are closed and released cleanly, preventing system lockups even if the pipeline encounters unexpected errors.

### D. Strict Pydantic Data Validation
*   Maintain structured Pydantic models (e.g., `RawAIExtraction`, `WorkSession`, `StreamData`) to enforce type-safety boundaries between external user inputs, AI extraction payloads, and database columns.
*   Utilize `@field_validator` hooks within models to enforce logical boundaries (e.g., ensuring hours worked do not exceed 24 per shift).
*   Rely on `.model_validate_json()` to handle dirty input transformations robustly.
