"""FairPay Telegram evidence-submission bot.

Telegram is an evidence-submission and notification channel only. This module
collects documents and hands them to the existing audit engine through
``telegram_audit.run_submission_audit``; it never calculates anything itself,
and detailed findings stay on the website.

Conversation model
------------------
1. ``/link <partner_id>`` ties this chat to a delivery partner.
2. Photos and PDFs are collected into the currently announced platform.
3. Any other plain text announces a platform - every document sent after it
   belongs to that platform.
4. ``PLEASE AUDIT`` runs the existing pipeline over everything collected.

A persistent keyboard keeps ``PLEASE AUDIT`` suggested whenever the partner
goes to type.

Run standalone (it does not touch the FastAPI app or the WebSocket stream):

    python telegram_bot.py
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

from telegram_audit import MISSING_MARKER, run_submission_audit, summarize_for_partner
from telegram_store import (
    complete_submission,
    create_submission,
    fail_submission,
    get_linked_partner,
    link_chat,
    partner_exists,
)

logger = logging.getLogger("fairpay.telegram")

API_ROOT = "https://api.telegram.org"
AUDIT_COMMAND = "PLEASE AUDIT"

# Mirrors the per-audit limits the website upload endpoint enforces.
MAX_IMAGES_PER_PLATFORM = 20
MAX_PDFS_PER_PLATFORM = 5
MAX_SESSION_BYTES = 50 * 1024 * 1024

WEBSITE_URL = os.getenv("FAIRPAY_WEBSITE_URL", "http://localhost:5173")

# Keeps "PLEASE AUDIT" in front of the partner whenever they focus the input.
KEYBOARD = {
    "keyboard": [[{"text": AUDIT_COMMAND}]],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": f"Type a platform name, or tap {AUDIT_COMMAND}",
}


@dataclass
class Session:
    """Documents collected in one chat.

    Documents arrive first and are held in ``pending``. Naming a platform
    assigns everything pending to it and starts a fresh pending batch, so the
    next documents belong to the next platform the partner names.
    """

    partner_id: Optional[str] = None
    pending: List[Dict[str, Any]] = field(default_factory=list)
    batches: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    total_bytes: int = 0
    auditing: bool = False

    def add(self, kind: str, content: bytes) -> None:
        self.pending.append({"kind": kind, "content": content})
        self.total_bytes += len(content)

    def announce(self, platform: str) -> int:
        """Label the pending documents with this platform. Returns how many."""
        moved = len(self.pending)
        if moved:
            self.batches.setdefault(platform, []).extend(self.pending)
            self.pending = []
        return moved

    @staticmethod
    def _counts(items: List[Dict[str, Any]]) -> Dict[str, int]:
        return {
            "images": sum(1 for i in items if i["kind"] == "image"),
            "pdfs": sum(1 for i in items if i["kind"] == "pdf"),
        }

    def pending_counts(self) -> Dict[str, int]:
        return self._counts(self.pending)

    def counts(self, platform: str) -> Dict[str, int]:
        return self._counts(self.batches.get(platform, []))

    def document_count(self) -> int:
        return len(self.pending) + sum(len(batch) for batch in self.batches.values())

    def reset_documents(self) -> None:
        self.batches.clear()
        self.pending = []
        self.total_bytes = 0


SESSIONS: Dict[int, Session] = {}


def _session(chat_id: int) -> Session:
    session = SESSIONS.get(chat_id)
    if session is None:
        session = Session()
        SESSIONS[chat_id] = session
    if session.partner_id is None:
        # Survive a bot restart: the link lives in the database.
        try:
            session.partner_id = get_linked_partner(chat_id)
        except Exception:
            logger.exception("Could not read the chat link for %s", chat_id)
    return session


class TelegramClient:
    """The slice of the Telegram Bot API this bot needs."""

    def __init__(self, token: str, client: httpx.AsyncClient):
        self._token = token
        self._client = client

    async def call(self, method: str, **payload: Any) -> Any:
        response = await self._client.post(
            f"{API_ROOT}/bot{self._token}/{method}", json=payload
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {body}")
        return body.get("result")

    async def get_updates(self, offset: int, timeout: int = 30) -> List[Dict[str, Any]]:
        response = await self._client.post(
            f"{API_ROOT}/bot{self._token}/getUpdates",
            json={"offset": offset, "timeout": timeout},
            timeout=timeout + 15,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram getUpdates failed: {body}")
        return body.get("result", [])

    async def send(self, chat_id: int, text: str) -> None:
        await self.call(
            "sendMessage", chat_id=chat_id, text=text, reply_markup=KEYBOARD
        )

    async def download(self, file_id: str) -> bytes:
        info = await self.call("getFile", file_id=file_id)
        file_path = info["file_path"]
        response = await self._client.get(
            f"{API_ROOT}/file/bot{self._token}/{file_path}", timeout=120
        )
        response.raise_for_status()
        return response.content


# ---------------------------------------------------------------------------
# Message handling
# ---------------------------------------------------------------------------

HELP_TEXT = (
    "FairPay evidence bot\n\n"
    "1. Send one platform's earnings screenshots or PDF statements.\n"
    "2. Then type that platform's name (for example: Zomato). This labels the "
    "documents you just sent.\n"
    "3. Send the next platform's documents and type its name, and so on.\n"
    f"4. When everything is sent, tap or type {AUDIT_COMMAND}.\n\n"
    "Example: send 10 images and 2 PDFs, type Zepto, then send your Zomato "
    f"documents, type Zomato, then tap {AUDIT_COMMAND}.\n\n"
    "Your full results open on the FairPay website."
)


async def handle_document(bot: TelegramClient, chat_id: int, message: Dict[str, Any]) -> None:
    """Collect a photo or PDF into the current platform batch."""
    session = _session(chat_id)
    if not session.partner_id:
        await bot.send(chat_id, "Please link your account first:\n/link <your partner id>")
        return

    kind: Optional[str] = None
    file_id: Optional[str] = None

    if message.get("photo"):
        # Telegram sends several sizes; the last one is the largest.
        kind, file_id = "image", message["photo"][-1]["file_id"]
    elif message.get("document"):
        document = message["document"]
        mime = (document.get("mime_type") or "").lower()
        name = (document.get("file_name") or "").lower()
        if mime == "application/pdf" or name.endswith(".pdf"):
            kind, file_id = "pdf", document["file_id"]
        elif mime.startswith("image/"):
            kind, file_id = "image", document["file_id"]
        else:
            await bot.send(chat_id, "Please send images or PDF files only.")
            return

    if not kind or not file_id:
        return

    counts = session.pending_counts()
    if kind == "image" and counts["images"] >= MAX_IMAGES_PER_PLATFORM:
        await bot.send(
            chat_id,
            f"That is {MAX_IMAGES_PER_PLATFORM} images in this batch, which is the "
            "limit. Type the platform name for them, then carry on.",
        )
        return
    if kind == "pdf" and counts["pdfs"] >= MAX_PDFS_PER_PLATFORM:
        await bot.send(
            chat_id,
            f"That is {MAX_PDFS_PER_PLATFORM} PDFs in this batch, which is the "
            "limit. Type the platform name for them, then carry on.",
        )
        return

    try:
        content = await bot.download(file_id)
    except Exception:
        logger.exception("Download failed for chat %s", chat_id)
        await bot.send(chat_id, "That file could not be downloaded. Please send it again.")
        return

    if session.total_bytes + len(content) > MAX_SESSION_BYTES:
        await bot.send(
            chat_id,
            f"That is more than this submission can hold. Please send "
            f"{AUDIT_COMMAND} for what you have already sent.",
        )
        return

    first_of_batch = not session.pending
    session.add(kind, content)
    logger.info(
        "chat=%s collected %s (%d pending, %d total)",
        chat_id, kind, len(session.pending), session.document_count(),
    )
    if first_of_batch:
        # Nudge once per batch rather than on every file in a 10-image drop.
        await bot.send(
            chat_id,
            "Received. Send the rest for this platform, then type the platform "
            "name (for example: Zomato) to label them.",
        )


async def handle_text(bot: TelegramClient, chat_id: int, text: str) -> None:
    session = _session(chat_id)
    stripped = text.strip()

    if stripped.startswith("/"):
        await handle_command(bot, chat_id, stripped)
        return

    if stripped.upper() == AUDIT_COMMAND:
        await start_audit(bot, chat_id)
        return

    if not session.partner_id:
        await bot.send(chat_id, "Please link your account first:\n/link <your partner id>")
        return

    # Any other text names the platform for the documents just sent.
    moved = session.announce(stripped)
    if moved == 0:
        await bot.send(
            chat_id,
            f"No new documents to label as {stripped} yet.\n"
            f"Send that platform's images or PDFs first, then type {stripped} again.",
        )
        return

    counts = session.counts(stripped)
    await bot.send(
        chat_id,
        f"Labelled {moved} document(s) as {stripped}.\n"
        f"{stripped} now has {counts['images']} image(s) and {counts['pdfs']} PDF(s).\n\n"
        f"Send another platform's documents next, or tap {AUDIT_COMMAND} if you are done.",
    )


async def handle_command(bot: TelegramClient, chat_id: int, text: str) -> None:
    parts = text.split(maxsplit=1)
    command = parts[0].lower().split("@")[0]
    argument = parts[1].strip() if len(parts) > 1 else ""
    session = _session(chat_id)

    if command in ("/start", "/help"):
        await bot.send(chat_id, HELP_TEXT)
        if not session.partner_id:
            await bot.send(chat_id, "To begin, send:\n/link <your partner id>")
        return

    if command == "/link":
        if not argument:
            await bot.send(chat_id, "Send it like this:\n/link <your partner id>")
            return
        try:
            if not partner_exists(argument):
                await bot.send(chat_id, f"No partner found with the id '{argument}'.")
                return
            link_chat(chat_id, argument)
        except Exception:
            logger.exception("Linking failed for chat %s", chat_id)
            await bot.send(chat_id, "That could not be saved right now. Please try again.")
            return
        session.partner_id = argument
        await bot.send(chat_id, f"Linked to {argument}.\n\n{HELP_TEXT}")
        return

    if command == "/status":
        if not session.partner_id:
            await bot.send(chat_id, "Not linked yet. Send /link <your partner id>")
            return
        if not session.batches and not session.pending:
            await bot.send(chat_id, "No documents collected yet.")
            return
        lines = ["Collected so far:"]
        for platform in session.batches:
            counts = session.counts(platform)
            lines.append(f"• {platform}: {counts['images']} images, {counts['pdfs']} PDFs")
        if session.pending:
            pending = session.pending_counts()
            lines.append(
                f"• Not labelled yet: {pending['images']} images, {pending['pdfs']} PDFs"
            )
            lines.append("  Type the platform name for these.")
        lines.append(f"\nSend {AUDIT_COMMAND} when you are ready.")
        await bot.send(chat_id, "\n".join(lines))
        return

    if command in ("/clear", "/reset"):
        session.reset_documents()
        await bot.send(chat_id, "Cleared. Send your documents again when you are ready.")
        return

    await bot.send(chat_id, HELP_TEXT)


async def start_audit(bot: TelegramClient, chat_id: int) -> None:
    """Hand everything collected to the existing audit pipeline."""
    session = _session(chat_id)

    if not session.partner_id:
        await bot.send(chat_id, "Please link your account first:\n/link <your partner id>")
        return
    if session.auditing:
        await bot.send(chat_id, "Your audit is already running. Please wait.")
        return
    if session.document_count() == 0:
        await bot.send(chat_id, "Send at least one image or PDF before asking for an audit.")
        return

    if session.pending:
        counts = session.pending_counts()
        await bot.send(
            chat_id,
            f"{counts['images']} image(s) and {counts['pdfs']} PDF(s) are not "
            "labelled yet.\nWhich platform are they from? Type the platform name, "
            f"then tap {AUDIT_COMMAND} again.",
        )
        return

    batches = {p: list(items) for p, items in session.batches.items() if items}
    document_counts = {p: session.counts(p) for p in batches}
    platforms = list(batches)

    session.auditing = True
    submission_id: Optional[str] = None
    try:
        submission_id = create_submission(
            session.partner_id, chat_id, platforms, document_counts
        )
        await bot.send(
            chat_id,
            "Got it. Checking your documents now — this can take a few minutes.\n"
            f"Platforms: {', '.join(platforms)}",
        )

        # The pipeline is synchronous, so keep it off the event loop.
        outcome = await asyncio.to_thread(run_submission_audit, session.partner_id, batches)

        complete_submission(
            submission_id,
            outcome["results_by_platform"],
            outcome["merged_result"],
            outcome["missing_fields"],
        )
        await bot.send(chat_id, summarize_for_partner(outcome["merged_result"]))
        await bot.send(chat_id, f"Full findings: {WEBSITE_URL}")
        session.reset_documents()
    except Exception as exc:
        logger.exception("Audit failed for chat %s", chat_id)
        if submission_id:
            try:
                fail_submission(submission_id, f"{type(exc).__name__}: {exc}")
            except Exception:
                logger.exception("Could not record the failure for %s", submission_id)
        await bot.send(
            chat_id,
            "Your documents could not be audited. Nothing has been changed.\n"
            "Please check that the images are readable and try again.",
        )
    finally:
        session.auditing = False


async def handle_update(bot: TelegramClient, update: Dict[str, Any]) -> None:
    message = update.get("message") or update.get("edited_message")
    if not message:
        return
    chat_id = message.get("chat", {}).get("id")
    if chat_id is None:
        return

    if message.get("photo") or message.get("document"):
        await handle_document(bot, chat_id, message)
        return

    text = message.get("text") or message.get("caption")
    if text:
        await handle_text(bot, chat_id, text)


# ---------------------------------------------------------------------------
# Long-polling entry point
# ---------------------------------------------------------------------------

async def run_bot() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Add it to your .env before starting the bot."
        )

    async with httpx.AsyncClient(timeout=60) as client:
        bot = TelegramClient(token, client)
        me = await bot.call("getMe")
        logger.info("FairPay bot connected as @%s", me.get("username"))

        offset = 0
        while True:
            try:
                updates = await bot.get_updates(offset)
            except Exception:
                logger.exception("getUpdates failed; retrying shortly")
                await asyncio.sleep(5)
                continue

            for update in updates:
                offset = update["update_id"] + 1
                try:
                    await handle_update(bot, update)
                except Exception:
                    logger.exception("Could not handle update %s", update.get("update_id"))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("FairPay bot stopped")


if __name__ == "__main__":
    main()
