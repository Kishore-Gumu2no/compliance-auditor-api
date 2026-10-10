# How to run FairPay

Docker runs the database, backend and website. The Telegram bot is optional and runs on your machine.

## 1. You need

| What | Where | For |
|---|---|---|
| Docker Desktop | [docker.com](https://docs.docker.com/get-docker/) | the stack |
| Groq key (free) | [console.groq.com/keys](https://console.groq.com/keys) | reading screenshots |
| Qdrant Cloud cluster (free) | [cloud.qdrant.io](https://cloud.qdrant.io) | incentive rules, labour laws |
| Telegram bot token | [@BotFather](https://t.me/BotFather) | the bot (optional) |
| NVIDIA key (free) | [build.nvidia.com](https://build.nvidia.com) | backup reader (optional) |

## 2. Configure

```bash
git clone https://github.com/Kishore-Gumu2no/compliance-auditor-api.git
cd compliance-auditor-api
copy .env.example .env        # macOS/Linux: cp
```

In `.env`, fill in `GROQ_API_KEY`, `QDRANT_URL` and `QDRANT_API_KEY`. Never commit this file.

## 3. Start

```bash
docker compose --profile dev up --build
```

The first start is slow (the build can take 30–45 minutes, plus a ~400 MB model download). Bobby's 30-day history loads once and is kept.

Website: http://localhost:5173 · API docs: http://localhost:8000/docs · Database: localhost:54322

## 4. Load the rules (once)

FairPay reads rules and laws **only** from Qdrant. With the stack running, in a second terminal:

```bash
python -m venv .venv
.venv\Scripts\activate         # macOS/Linux: source .venv/bin/activate
pip install -r Backend/requirements.txt
cd Backend
python incentive_kb_ingest.py  # incentive rules
python law_kb_ingest.py        # labour laws
```

Safe to repeat: nothing already in Qdrant is overwritten. To change a law, edit it in Qdrant or add a new version with `law_kb_ingest.py --laws file.json`.

## 5. Check it works

Open the website and press **Check your 30 day audit**. The results appear under **Audit Results**.

## Telegram bot (optional)

Add `TELEGRAM_BOT_TOKEN` to `.env`, install [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki), then run `python telegram_bot.py` from `Backend`.

In Telegram: `/start`, `/link bobby`, pick an option, send screenshots, name the app, tap **PLEASE AUDIT**. Also: `/status`, `/clear`, `/unlink` (forget this chat and your results).

## When the free AI reader runs out

Groq's free tier covers about five incentive checks a day. FairPay waits out the per-minute limit by itself. When the day's allowance is gone it switches to a slower free reader if `NVIDIA_API_KEY` is set, and warns the partner. Without the key it says so and checks nothing, rather than answer from a half-read day.

## Tests

```bash
cd Backend
python -m pytest                                                # stack running, Qdrant loaded
python synthetic_verification.py --data-root ../synthetic_data  # scores 20 partners with known answers
```

## Stop

`docker compose --profile dev stop` keeps your data. Avoid `down -v`: it deletes the database.

## Troubleshooting

| Problem | Fix |
|---|---|
| Backend restarts with `GroqError` | `GROQ_API_KEY` is missing from `.env` |
| `SUPABASE_KEY is not set` | run `copy .env.example .env` |
| "Rules could not be established" or "no legal check" | do step 4 |
| Website says "Demo mode" | open it at exactly `localhost`, not `127.0.0.1` or an IP |
| Port 8000, 5173 or 54322 busy | stop what is using it, start again |
