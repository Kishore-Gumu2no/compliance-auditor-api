# How to Run FairPay Engine

Docker runs the database, backend and website. The Telegram bot (optional)
runs on your machine.

## Prerequisites

- **Docker Desktop** (with Docker Compose v2.24 or newer): https://docs.docker.com/get-docker/
  Check with `docker --version` and `docker compose version`.
- **Git**
- **Groq API key** (required): https://console.groq.com/keys
  Your account must have access to `openai/gpt-oss-120b` and to the
  image-capable model set in `FAIRPAY_VISION_MODEL` (default `qwen/qwen3.8-27b`).
- **Telegram bot token** (only if you want Telegram document submission):
  create a bot with [@BotFather](https://t.me/BotFather).
- **Python 3.11+** (only to run the Telegram bot on your machine).

Qdrant is **not** required; the audit uses the bundled local law file.

## Setup

```bash
git clone https://github.com/Kishore-Gumu2no/compliance-auditor-api.git
cd compliance-auditor-api

# Create your configuration from the template
copy .env.example .env        # Windows
# cp .env.example .env        # macOS / Linux
```

Open `.env` and fill in at least `GROQ_API_KEY`. For Telegram, also set
`TELEGRAM_BOT_TOKEN` and `VITE_TELEGRAM_BOT_URL` (e.g. `https://t.me/<your_bot>`).

`.env` is git-ignored and is never copied into Docker images. Never commit it.

## Start

```bash
docker compose --profile dev up --build
```

The `--profile dev` flag is required; it starts the website. This starts:

| Service | URL |
|---|---|
| Website | http://localhost:5173 |
| Backend API | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| PostgreSQL | localhost:54322 (user `postgres`, password `postgres`) |
| Supabase Studio | http://localhost:54323 |

**First start takes a while:**
- Building the backend image can take 30-45 minutes (Python dependencies).
- On first start the backend downloads a ~400 MB language model for
  personal-data redaction, so it needs internet access and a few minutes.
- On first start the database is created and loaded automatically with the
  schema and Bobby's 30-day dataset (341 orders). This happens only once,
  when the `fairpay_pgdata` volume is new; later starts keep your data.

## Verify

- http://localhost:8000/docs opens the API documentation.
- http://localhost:5173 shows **Live Earnings** with "Live stream active".
- Click **Discover how you can earn more** to run the 30-day audit for Bobby.

## Telegram bot (optional)

Start it after the Docker stack is running, from a separate terminal:

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # macOS / Linux
pip install -r Backend/requirements.txt

cd Backend
python telegram_bot.py
```

The bot reads the root `.env` and connects to the database on
`localhost:54322`. Screenshot OCR on your machine needs Tesseract installed;
set `TESSERACT_PATH` in `.env` if it is not on the default path.

In Telegram: send `/link bobby`, send your earnings screenshots or PDFs, type
the platform name (e.g. `Zomato`), then tap **PLEASE AUDIT**. The result
appears on the website under **Audit Results**.

## Stop

```bash
docker compose --profile dev stop
```

This keeps your database. Do **not** use `docker compose down -v` unless you
want to delete the database volume; it is recreated and reseeded on the
next start.

## Troubleshooting

- **Backend keeps restarting with `GroqError`:** `GROQ_API_KEY` is missing
  from `.env`.
- **Website loads but shows no data:** make sure the backend is healthy:
  `docker compose ps`.
- **Port already in use (8000 / 5173 / 54322):** stop any local backend,
  Vite dev server or database using that port, then start again.
