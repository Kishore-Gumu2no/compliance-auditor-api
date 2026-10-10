"""FairPay Telegram evidence-submission bot.

Telegram is an evidence-submission and notification channel only. This module
collects documents and hands them to the existing audit engine through
``telegram_audit.run_submission_audit``; it never calculates anything itself,
and detailed findings stay on the website.

Conversation model (shown to the partner as GUIDE_TEXT)
--------------------------------------------------------
1. ``/start`` introduces the two options; the partner chooses one:
   Option 1 - "Didn't get your incentive?" (the incentive check, from a
   single day's entire trip history), or Option 2 - "Full Audit".
2. ``/link <partner_id>`` ties this chat to a delivery partner.
3. After sending one app's photos or PDFs, the partner picks the app
   (Zomato, Swiggy, Uber Eats, Blinkit, Other).
4. ``PLEASE AUDIT`` hands everything to ``telegram_audit.run_submission_audit``.

Messages use plain, everyday words for people with no technical background.

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

from evidence_modes import FULL_AUDIT
from telegram_audit import INCENTIVE_AUDIT, IncompleteReading, run_submission_audit, summarize_for_partner
from telegram_store import (
    complete_submission,
    create_submission,
    fail_submission,
    get_linked_partner,
    link_chat,
    partner_exists,
    purge_submission_details,
    unlink_chat,
)

logger = logging.getLogger("fairpay.telegram")

API_ROOT = "https://api.telegram.org"
AUDIT_COMMAND = "PLEASE AUDIT"
# Shown on keyboards sent before this button was renamed; still accepted.
_OLD_DONE_BUTTON = "✅ Check my files"

# Mirrors the per-submission limits used across FairPay.
MAX_IMAGES_PER_PLATFORM = 20
MAX_PDFS_PER_PLATFORM = 5
MAX_SESSION_BYTES = 50 * 1024 * 1024

WEBSITE_URL = os.getenv("FAIRPAY_WEBSITE_URL", "http://localhost:5173")

# Keeps "PLEASE AUDIT" in front of the partner whenever they focus the input.
KEYBOARD = {
    "keyboard": [[{"text": AUDIT_COMMAND}]],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "Send your files here",
}

# The two options introduced after /start. Exactly one must be chosen.
OPTION_1_TEXT = ("Option 1: Didn't get your incentive? I can check why, just send me your single day's "
                 "entire trip history, WITHOUT MISSING A SINGLE TRIP")
OPTION_2_TEXT = "Option 2: Full Audit"
OPTION_1_BUTTON = "Option 1: Didn't get your incentive?"
OPTION_2_BUTTON = "Option 2: Full Audit"
OPTION_NAMES = {INCENTIVE_AUDIT: "Option 1 (the incentive check)", FULL_AUDIT: "Option 2 (Full Audit)"}
OPTION_KEYBOARD = {
    "keyboard": [[{"text": OPTION_1_BUTTON}], [{"text": OPTION_2_BUTTON}]],
    "resize_keyboard": True,
    "one_time_keyboard": True,
    "input_field_placeholder": "Choose Option 1 or Option 2",
}
CHOOSE_OPTION = f"Please choose one option:\n\n{OPTION_1_TEXT}\n\n{OPTION_2_TEXT}"
START_TEXT = f"Welcome to FairPay.\n\n{CHOOSE_OPTION}"
# A one-line reminder with the option buttons.
CHOOSE_OPTION_SHORT = "Please choose Option 1 or Option 2 first."

OTHER_APP = "Other"
APP_KEYBOARD = {
    "keyboard": [
        [{"text": "Zomato"}, {"text": "Swiggy"}],
        [{"text": "Uber Eats"}, {"text": "Blinkit"}],
        [{"text": OTHER_APP}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": True,
    "input_field_placeholder": "Which app?",
}

# The how-to, shown on /start and as the bot's description - the text Telegram
# shows before the partner presses Start. Telegram allows 512 characters there.
GUIDE_TEXT = (
    "Didn't get an incentive? FairPay can check why.\n\n"
    "How to use the FairPay bot:\n"
    "1. Send /start and choose Option 1 (Didn't get your incentive? Send one whole day of trips, "
    "every single one) or Option 2 (Full Audit).\n"
    "2. Link your data with /link and your ID (example: /link bobby)\n"
    "3. Send one app's screenshots or PDFs, then say which app they are from.\n"
    f"4. When you are done, tap {AUDIT_COMMAND}.\n"
    "Your results appear on the FairPay website.\n\n"
    # Telegram allows 512 characters here, so this stays short.
    "FairPay keeps your ID to show your results. Send /unlink to be forgotten."
)
SHORT_DESCRIPTION = "Send your delivery earnings files. FairPay checks your pay and shows the results on its website."
BOT_COMMANDS = [
    {"command": "start", "description": "See how to use the bot"},
    {"command": "link", "description": "Link to your data, e.g. /link bobby"},
    {"command": "status", "description": "See the files you sent"},
    {"command": "clear", "description": "Remove the files you sent"},
    {"command": "unlink", "description": "Forget this chat and my past results"},
]

LINK_PROMPT = "Now link to your data. Send /link and your ID, like this:\n/link bobby"
WHICH_APP = "Which app are these records from?"


def is_done(text: str) -> bool:
    return text.strip().upper() in (AUDIT_COMMAND, _OLD_DONE_BUTTON.upper(), "DONE")


def parse_option(text: str) -> Optional[str]:
    """The option a reply picks (1 or 2), or None if it is not an option choice."""
    cleaned = " ".join(text.strip().lower().replace(":", " ").rstrip("?").split())
    if cleaned in ("1", "option 1", " ".join(OPTION_1_BUTTON.lower().replace(":", " ").rstrip("?").split()),
                   "didn't get your incentive", "didn't get my incentive", "incentive"):
        return INCENTIVE_AUDIT
    if cleaned in ("2", "option 2", "full audit", " ".join(OPTION_2_BUTTON.lower().replace(":", " ").split())):
        return FULL_AUDIT
    return None


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
    # Set after the partner taps "Other": their next message is the app's name.
    awaiting_app_name: bool = False
    # Option A or B (step 3); None until the partner chooses.
    option: Optional[str] = None

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

    async def send(self, chat_id: int, text: str, keyboard: Optional[Dict[str, Any]] = None) -> None:
        await self.call(
            "sendMessage", chat_id=chat_id, text=text, reply_markup=keyboard or KEYBOARD
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

async def handle_document(bot: TelegramClient, chat_id: int, message: Dict[str, Any]) -> None:
    """Collect a photo or PDF until the partner says which app it is from."""
    session = _session(chat_id)
    if not session.partner_id:
        await bot.send(chat_id, LINK_PROMPT)
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
            await bot.send(chat_id, "Please send pictures or PDF files.")
            return

    if not kind or not file_id:
        return

    counts = session.pending_counts()
    if (kind == "image" and counts["images"] >= MAX_IMAGES_PER_PLATFORM) or (
        kind == "pdf" and counts["pdfs"] >= MAX_PDFS_PER_PLATFORM
    ):
        await bot.send(
            chat_id,
            "That is a lot of files at once. Please tell me which app they are from first, "
            "then send the rest.\n\n" + WHICH_APP,
            APP_KEYBOARD,
        )
        return

    try:
        content = await bot.download(file_id)
    except Exception:
        logger.exception("Download failed for chat %s", chat_id)
        await bot.send(chat_id, "I could not get that file. Please send it again.")
        return

    if session.total_bytes + len(content) > MAX_SESSION_BYTES:
        await bot.send(
            chat_id,
            f"That is too much for one check. Tap {AUDIT_COMMAND} to check what you already sent.",
        )
        return

    first_of_batch = not session.pending
    session.add(kind, content)
    logger.info(
        "chat=%s collected %s (%d pending, %d total)",
        chat_id, kind, len(session.pending), session.document_count(),
    )
    if first_of_batch:
        # Ask once per batch rather than on every file in a 10-image drop.
        session.awaiting_app_name = False
        if session.option is None:
            await bot.send(chat_id, "Got it! " + CHOOSE_OPTION_SHORT, OPTION_KEYBOARD)
        else:
            await bot.send(chat_id, f"Got it! {WHICH_APP}", APP_KEYBOARD)


async def handle_text(bot: TelegramClient, chat_id: int, text: str) -> None:
    session = _session(chat_id)
    stripped = text.strip()

    if stripped.startswith("/"):
        await handle_command(bot, chat_id, stripped)
        return

    if is_done(stripped):
        await start_audit(bot, chat_id)
        return

    option = parse_option(stripped)
    if option is not None:
        await choose_option(bot, chat_id, option)
        return

    if not session.partner_id:
        await bot.send(chat_id, LINK_PROMPT)
        return

    if not session.pending:
        await bot.send(chat_id, "Please send your files first. Then tell me which app they are from.")
        return

    if stripped.lower() == OTHER_APP.lower() and not session.awaiting_app_name:
        session.awaiting_app_name = True
        await bot.send(chat_id, "Please type the name of the app.")
        return

    # The answer to "Which app are these records from?" labels the files.
    session.awaiting_app_name = False
    moved = session.announce(stripped)
    total = sum(session.counts(stripped).values())
    await bot.send(
        chat_id,
        f"Thank you! I have {total} file{'s' if total != 1 else ''} from {stripped}.\n"
        f"You can send another app's files now. When you are done, tap {AUDIT_COMMAND}.",
    )
    logger.info("chat=%s labelled %d file(s) as %s", chat_id, moved, stripped)


async def choose_option(bot: TelegramClient, chat_id: int, option: str) -> None:
    session = _session(chat_id)
    session.option = option
    if option == INCENTIVE_AUDIT:
        intro = ("You chose Option 1. I will check why you did not get your incentive.\n"
                 "Send me your single day's entire trip history, WITHOUT MISSING A SINGLE TRIP. "
                 "Then I will ask which app it is from.")
    else:
        intro = ("You chose Option 2: Full Audit.\n"
                 "Send one app's screenshots or PDFs. Then I will ask which app they are from.")
    if not session.partner_id:
        await bot.send(chat_id, f"{intro}\n\n{LINK_PROMPT}")
        return
    if session.pending:
        await bot.send(chat_id, f"{intro.splitlines()[0]}\n{WHICH_APP}", APP_KEYBOARD)
        return
    await bot.send(chat_id, intro)


async def send_next_step(bot: TelegramClient, chat_id: int) -> None:
    """After /link: ask for the option, or for the files of the option already chosen."""
    session = _session(chat_id)
    if not session.partner_id:
        await bot.send(chat_id, LINK_PROMPT)
    elif session.option is None:
        await bot.send(chat_id, CHOOSE_OPTION, OPTION_KEYBOARD)
    elif session.option == INCENTIVE_AUDIT:
        await bot.send(chat_id, "Now send me your single day's entire trip history, WITHOUT MISSING A SINGLE TRIP.")
    else:
        await bot.send(chat_id, "Now send one app's screenshots or PDFs for the Full Audit.")


async def handle_command(bot: TelegramClient, chat_id: int, text: str) -> None:
    parts = text.split(maxsplit=1)
    command = parts[0].lower().split("@")[0]
    argument = parts[1].strip() if len(parts) > 1 else ""
    session = _session(chat_id)

    if command == "/link":
        if not argument:
            await bot.send(chat_id, LINK_PROMPT)
            return
        try:
            if not partner_exists(argument):
                await bot.send(chat_id, f"I could not find the ID '{argument}'. Please check it and try again.")
                return
            link_chat(chat_id, argument)
        except Exception:
            logger.exception("Linking failed for chat %s", chat_id)
            await bot.send(chat_id, "Something went wrong. Please try again in a moment.")
            return
        session.partner_id = argument
        await bot.send(chat_id, f"Thank you! You are linked to {argument}.")
        await send_next_step(bot, chat_id)
        return

    if command == "/status":
        if not session.partner_id:
            await bot.send(chat_id, LINK_PROMPT)
            return
        chosen = OPTION_NAMES[session.option] if session.option else "not chosen yet"
        if not session.batches and not session.pending:
            await bot.send(chat_id, f"Option: {chosen}\nYou have not sent any files yet.")
            return
        lines = [f"Option: {chosen}", "Files you sent:"]
        for platform in session.batches:
            lines.append(f"• {platform}: {sum(session.counts(platform).values())}")
        if session.pending:
            lines.append(f"• Not sure which app yet: {len(session.pending)}")
        lines.append(f"\nWhen you are done, tap {AUDIT_COMMAND}.")
        await bot.send(chat_id, "\n".join(lines))
        return

    if command in ("/clear", "/reset"):
        session.reset_documents()
        session.awaiting_app_name = False
        await bot.send(chat_id, "Done. I removed the files you sent. You can send new files now.")
        return

    if command == "/unlink":
        session.reset_documents()
        session.awaiting_app_name = False
        session.partner_id = None
        session.option = None
        try:
            removed = await asyncio.to_thread(unlink_chat, chat_id)
        except Exception:
            logger.exception("Unlinking failed for chat %s", chat_id)
            await bot.send(chat_id, "Something went wrong. Please try again in a moment.")
            return
        results = removed["submissions"]
        await bot.send(
            chat_id,
            "Done. I have forgotten this chat.\n"
            f"I removed the link to your ID and {results} past {'result' if results == 1 else 'results'}.\n"
            "Your results will no longer show on the website. Use /link with your ID to start again.",
        )
        return

    # /start, /help and anything else: introduce the two options.
    session.option = None
    await bot.send(chat_id, START_TEXT, OPTION_KEYBOARD)


async def start_audit(bot: TelegramClient, chat_id: int) -> None:
    """Hand everything collected to the backend with the chosen option."""
    session = _session(chat_id)

    if not session.partner_id:
        await bot.send(chat_id, LINK_PROMPT)
        return
    if session.auditing:
        await bot.send(chat_id, "I am still checking your files. Please wait.")
        return
    if session.option is None:
        await bot.send(chat_id, CHOOSE_OPTION_SHORT, OPTION_KEYBOARD)
        return
    if session.document_count() == 0:
        await bot.send(chat_id, "Please send at least one file first.")
        return

    if session.pending:
        count = len(session.pending)
        await bot.send(
            chat_id,
            f"I don't know which app {'this file is' if count == 1 else f'these {count} files are'} "
            f"from yet.\n{WHICH_APP}",
            APP_KEYBOARD,
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
        await bot.send(chat_id, "Thank you! I am checking your files now. This can take a few minutes.")

        # The pipeline is synchronous, so keep it off the event loop. The
        # chosen option decides the check; files with only totals are still
        # compared as totals.
        # The reading runs in a worker thread; if it has to switch to the slower
        # reader, this sends the partner the notice from there.
        loop = asyncio.get_running_loop()

        def notify(text: str) -> None:
            asyncio.run_coroutine_threadsafe(bot.send(chat_id, text), loop)

        outcome = await asyncio.to_thread(
            run_submission_audit, session.partner_id, batches, session.option, notify
        )

        complete_submission(
            submission_id,
            outcome["results_by_platform"],
            outcome["merged_result"],
            outcome["missing_fields"],
        )
        await bot.send(chat_id, summarize_for_partner(outcome["merged_result"]))
        await bot.send(chat_id, f"See your results here: {WEBSITE_URL}")
        # The partner has now been shown the result, so the trip-level details
        # behind it are deleted; only the outcome is kept (see telegram_store).
        try:
            await asyncio.to_thread(purge_submission_details, submission_id, session.partner_id)
        except Exception:
            logger.exception("Could not delete the trip details for %s", submission_id)
        session.reset_documents()
    except IncompleteReading as exc:
        # Not every document was read in full, so no answer was given.
        if submission_id:
            try:
                fail_submission(submission_id, "Documents not read in full; partner asked to send them again.")
            except Exception:
                logger.exception("Could not record the failure for %s", submission_id)
        if not exc.keep_documents:
            session.reset_documents()
        await bot.send(chat_id, exc.rider_message)
    except Exception as exc:
        logger.exception("Audit failed for chat %s", chat_id)
        if submission_id:
            try:
                fail_submission(submission_id, f"{type(exc).__name__}: {exc}")
            except Exception:
                logger.exception("Could not record the failure for %s", submission_id)
        await bot.send(
            chat_id,
            "Sorry, I could not check your files. Nothing was changed.\n"
            "Please make sure the pictures are clear and try again.",
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

async def publish_profile(bot: TelegramClient) -> None:
    """Show the steps before the partner presses Start (bot description),
    plus the short profile text and the command menu. Best-effort."""
    for method, payload in (
        ("setMyDescription", {"description": GUIDE_TEXT}),
        ("setMyShortDescription", {"short_description": SHORT_DESCRIPTION}),
        ("setMyCommands", {"commands": BOT_COMMANDS}),
    ):
        try:
            await bot.call(method, **payload)
        except Exception:
            logger.exception("Could not set the bot profile (%s)", method)


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
        await publish_profile(bot)

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
