# FairPay Engine

**A delivery partner works a 12-hour shift and ₹1,400 lands in the app. Was that the right number?**

Today there is no way to know. The contract rates, the incentives that should have applied and the deductions taken all stay inside the platform's app, and none of it is itemised.

FairPay recalculates every order from the partner's own screenshots and statements, and shows exactly where the money went missing. Send a screenshot to a Telegram bot. Get the answer back.

## What you can do with it

| | |
|---|---|
| **Didn't get your incentive?** | Send one full day of trips. FairPay shows what the incentive needed, what you did, and why it was not paid. It never calls anything fraud. |
| **Full audit** | Send your orders and payments. FairPay rebuilds what each order should have paid, matches it to what was paid, and grades any gap against Indian labour law. |
| **Which incentive fits you better?** | Compares platforms' daily incentives against your own day of work. Never promises higher earnings. |
| **What if I worked differently?** | A simulator for more hours, more days or lower fuel costs. Plain arithmetic on your own averages, never a guarantee. |

## How it works

```
Telegram bot ──► redact personal details ──► AI reads the screenshot
                                                    │
        Postgres (contracts, history) ◄─────────────┤
        Qdrant (incentive rules + labour laws)  ────┤
                                                    ▼
                 calculations ──► findings ──► your result on the website
```

- **Verdicts come from code, not AI.** The AI only copies what is printed on your documents. Every count, comparison and finding is deterministic, so the same evidence always gives the same answer.
- **Unknown stays unknown.** If the evidence is missing, FairPay says so instead of guessing.
- **Rules live in Qdrant**, dated, so a law or incentive can change without touching the code.

## Privacy

- Names, phone numbers and other personal details are blacked out *before* any AI sees a document.
- Documents are deleted when the audit ends. The trips read from them are deleted once you have been shown your result.
- Your result is kept under your partner ID so it can be shown back to you. Send `/unlink` to the bot to be forgotten.

## Try it

You need Docker Desktop, a free [Groq key](https://console.groq.com/keys) and a free [Qdrant Cloud](https://cloud.qdrant.io) cluster.

```bash
git clone https://github.com/Kishore-Gumu2no/compliance-auditor-api.git
cd compliance-auditor-api
copy .env.example .env      # macOS/Linux: cp   -  then add your Groq and Qdrant keys
docker compose --profile dev up --build
```

Open **http://localhost:5173** and press **Check your 30 day audit** to audit Bobby, a demo partner whose 30-day history is loaded automatically.

Full steps, the one-time rule loading and the Telegram bot are in **[HOW_TO_RUN.md](HOW_TO_RUN.md)**.

## Built with

React 19 + TypeScript + Vite · FastAPI · LangGraph · PostgreSQL · Qdrant · Groq (Qwen vision, GPT-OSS) · Microsoft Presidio · Tesseract · Docker · Telegram Bot API
