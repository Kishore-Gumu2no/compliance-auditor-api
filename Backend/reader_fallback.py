"""Which AI reader reads a document, and what happens when it runs out.

Used by both checks: the incentive check (incentive_extraction) and the full
audit (evidence_extractor).

* The primary reader is Qwen on Groq. A short rate limit is waited out, and a
  busy model is retried with a back-off, so a passing busy moment is never
  mistaken for an unreadable document.
* Groq's free tier is also capped by tokens per day. Once that day's
  allowance is used up, the rest of the submission is read by
  FALLBACK_VISION_MODEL on NVIDIA's free API, which has no daily token cap but
  takes about 100 seconds a screenshot. The partner is told once, before the
  wait starts. Without NVIDIA_API_KEY there is no fallback and the reader
  reports that the day's allowance is used up, as before.

Only the switching lives here; what to read and how to read it stays with
each check.
"""

from __future__ import annotations

import contextlib
import contextvars
import math
import os
import re
import time
from typing import Any, Callable, List, Optional, TypeVar

import instructor

from evidence_extractor import EvidenceExtractionError

T = TypeVar("T")

# A rate limit this short is waited out; a longer one (e.g. the daily limit)
# is reported so the partner can try again later.
SHORT_WAIT_SECONDS = 60
# When the model is over capacity (a problem on the service's side, error
# 503/504), FairPay waits this long before each new try.
CAPACITY_BACKOFF_SECONDS = (5, 15, 30)
READ_ATTEMPTS = 3

FALLBACK_VISION_MODEL = os.getenv("FAIRPAY_NVIDIA_VISION_MODEL", "meta/muse-glimmer-30b")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
FALLBACK_TIMEOUT_SECONDS = 300  # one screenshot takes about 100 seconds on the free tier
FALLBACK_NOTICE = ("THIS WILL TAKE MORE THAN A FEW MINUTES THANK YOU FOR YOUR PATIENCE. APPROX 9 MINUTES "
                   "BECAUSE IT'S FREE TIER AND I'M A STUDENT, SO PLEASE CONNECT THE DOTS")


class ReaderBusy(EvidenceExtractionError):
    """The reading service refused for now because of its usage limit.

    ``daily`` tells the two cases apart: a short pause because too much was
    read in the last minute, or the whole day's allowance being used up. The
    partner is told something different for each.
    """

    def __init__(self, wait_minutes: int, daily: bool = False):
        limit = "today's reading allowance is used up" if daily else "the reading service is busy"
        super().__init__(f"{limit}; try again in about {wait_minutes} minutes.")
        self.wait_minutes = wait_minutes
        self.daily = daily


# ---------------------------------------------------------------------------
# Telling a limit apart from a real failure
# ---------------------------------------------------------------------------

def _limit_texts(exc: BaseException) -> str:
    """The messages of an error and of the errors that caused it, joined together."""
    texts: List[str] = []
    seen: Optional[BaseException] = exc
    while seen is not None and len(texts) < 6:
        texts.append(str(seen))
        seen = seen.__cause__ or seen.__context__
    return " ".join(texts)


def _is_daily_limit(exc: BaseException) -> bool:
    """True when the service says the limit that was hit is the one for a whole day."""
    return bool(re.search(r"per day|\bTPD\b|\bRPD\b|daily", _limit_texts(exc), flags=re.IGNORECASE))


def _limit_wait(exc: BaseException) -> Optional[float]:
    """Seconds the service asks to wait when ``exc`` is a rate limit; None for any other error."""
    text = _limit_texts(exc)
    if not re.search(r"rate.?limit|\b429\b", text, flags=re.IGNORECASE):
        return None
    m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", text)
    if not m or not any(m.groups()):
        return float(SHORT_WAIT_SECONDS)
    hours, minutes, seconds = m.groups()
    return int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0)


def _over_capacity(exc: BaseException) -> bool:
    """True when the service says the model is over capacity (its own problem, not the document's)."""
    seen: Optional[BaseException] = exc
    for _ in range(6):
        if seen is None:
            break
        if re.search(r"over capacity|service unavailable|gateway time-?out|\b50[34]\b", str(seen),
                     flags=re.IGNORECASE):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


def call_patiently(call: Callable[[], T], failure: str) -> T:
    """Run one reading call, waiting out a short rate limit or a busy model instead of giving up."""
    for attempt in range(READ_ATTEMPTS):
        try:
            return call()
        except Exception as exc:
            wait = _limit_wait(exc)
            if wait is None and _over_capacity(exc):
                wait = float(CAPACITY_BACKOFF_SECONDS[min(attempt, len(CAPACITY_BACKOFF_SECONDS) - 1)])
            if wait is None:
                raise EvidenceExtractionError(failure) from exc
            if wait > SHORT_WAIT_SECONDS or attempt == READ_ATTEMPTS - 1:
                raise ReaderBusy(max(1, math.ceil(wait / 60)), daily=_is_daily_limit(exc)) from exc
            time.sleep(wait + 1)
    raise EvidenceExtractionError(failure)


# ---------------------------------------------------------------------------
# Which reader is in use for this submission
# ---------------------------------------------------------------------------

_fallback_client: Any = None


def _fallback_reader() -> Any:
    """The NVIDIA reader, or None when no NVIDIA_API_KEY is set."""
    global _fallback_client
    key = os.getenv("NVIDIA_API_KEY")
    if not key:
        return None
    if _fallback_client is None:
        from openai import OpenAI

        _fallback_client = instructor.from_openai(
            OpenAI(base_url=NVIDIA_BASE_URL, api_key=key, timeout=FALLBACK_TIMEOUT_SECONDS, max_retries=1),
            mode=instructor.Mode.JSON,
        )
    return _fallback_client


class _ReaderChoice:
    """Remembers, for one submission, whether the switch to the fallback happened."""

    def __init__(self, on_switch: Optional[Callable[[str], None]]):
        self.on_switch = on_switch
        self.fallback = False

    def switch(self) -> None:
        if self.fallback:
            return
        self.fallback = True
        if self.on_switch:
            try:
                self.on_switch(FALLBACK_NOTICE)
            except Exception:
                pass  # telling the partner must never stop the reading


_SWITCH_LISTENER: contextvars.ContextVar = contextvars.ContextVar("reader_switch_listener", default=None)
_CHOICE: contextvars.ContextVar = contextvars.ContextVar("reader_choice", default=None)


@contextlib.contextmanager
def reader_switch_listener(callback: Optional[Callable[[str], None]]):
    """One submission: every read inside shares one reader choice, and if it
    has to switch to the slower reader, ``callback(message)`` is called once.

    Inside a submission that already has one (a later app of the same
    submission, say), the existing choice is kept, so Groq is not asked again
    and the partner is not told twice.
    """
    if _CHOICE.get() is not None:
        yield
        return
    listener = _SWITCH_LISTENER.set(callback)
    choice = _CHOICE.set(_ReaderChoice(callback))
    try:
        yield
    finally:
        _CHOICE.reset(choice)
        _SWITCH_LISTENER.reset(listener)


@contextlib.contextmanager
def reader_choice():
    """Reads inside share one reader choice: the submission's if there is one, else a new one."""
    if _CHOICE.get() is not None:
        yield
        return
    token = _CHOICE.set(_ReaderChoice(_SWITCH_LISTENER.get()))
    try:
        yield
    finally:
        _CHOICE.reset(token)


def read_with_fallback(primary: Callable[[], T], fallback: Callable[[Any], T], failure: str) -> T:
    """Read with Qwen on Groq; once its daily allowance is used up, with the NVIDIA reader."""
    choice: Optional[_ReaderChoice] = _CHOICE.get()
    reader = _fallback_reader()
    if choice is not None and choice.fallback and reader is not None:
        return call_patiently(lambda: fallback(reader), failure)
    try:
        return call_patiently(primary, failure)
    except ReaderBusy as exc:
        if not exc.daily or reader is None:
            raise  # a passing busy moment, or no fallback configured: as before
        if choice is not None:
            choice.switch()
        return call_patiently(lambda: fallback(reader), failure)
