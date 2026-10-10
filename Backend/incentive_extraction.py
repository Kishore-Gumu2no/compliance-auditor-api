"""Reading incentive evidence off a partner's documents.

Same reading path as every other FairPay evidence: PDF text is PII-masked,
images are PII-redacted before they leave the machine, and the LLM only
copies what is printed. This module adds an incentive-specific schema
(order status, batched trips, incentive progress, incentive payments,
performance figures) without changing the full audit's extractor or prompt.

The LLM never counts, compares or decides anything here; the output is
normalised by ``incentive_evidence.evidence_from_extraction`` and evaluated
deterministically by ``incentive_engine``.
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import math
import os
import re
import time
from decimal import Decimal
from typing import Any, Callable, Dict, List, Literal, Optional, Set, Tuple

import instructor
import pytesseract
from PIL import Image, ImageOps, ImageStat
from pydantic import BaseModel, Field

from evidence_extractor import (
    TEXT_EXTRACTION_MODEL,
    VISION_EXTRACTION_MODEL,
    EvidenceExtractionError,
    WorkTimeEntry,
    _image_data_url,
    client,
    mask_pii,
    read_pdfs,
    redact_pii,
    to_work_time,
)
from incentive_evidence import _parse_clock
# The reader choice (Qwen on Groq first, the NVIDIA fallback once Groq's day is
# used up) is shared with the full audit; the names are kept here as before.
from reader_fallback import (  # noqa: F401
    CAPACITY_BACKOFF_SECONDS,
    FALLBACK_NOTICE,
    FALLBACK_VISION_MODEL,
    READ_ATTEMPTS,
    SHORT_WAIT_SECONDS,
    ReaderBusy,
    _CHOICE,
    _ReaderChoice,
    _SWITCH_LISTENER,
    _is_daily_limit,
    _limit_wait,
    _over_capacity,
    call_patiently as _call_patiently,
    read_with_fallback as _read_with_fallback,
    reader_choice,
    reader_switch_listener,
)

# A screenshot is read again, at most this many times in all, while the AI
# returns fewer trip times than are printed on it.
MAX_READS_PER_IMAGE = 3
# The reading service is asked for the same answer every time for a given
# document (same model, same prompt, same seed), so a check can be repeated and
# give the same result. A re-read of a document that came back incomplete uses
# the next seed, so it is a genuinely fresh attempt rather than the same one.
FIRST_READ_SEED = 7

class IncentiveTrip(BaseModel):
    order_id: Optional[str] = Field(default=None, description="Order or trip ID exactly as printed, only when the screen labels it as an order or trip ID; never a store, restaurant, hub or zone name or code")
    date_text: Optional[str] = Field(default=None, description="The date for this row exactly as printed, e.g. '8 Oct' or '08 Oct, 2026': the row's own date, or else the date heading or date picker above the list")
    date: Optional[dt.date] = Field(default=None, description="Date printed for this order, YYYY-MM-DD, only when the year is printed; null otherwise")
    accepted_time: Optional[str] = Field(default=None, description="Printed accepted/assigned/order time, e.g. '11:42 PM'")
    completed_time: Optional[str] = Field(default=None, description="Printed delivered/completed time, e.g. '12:10 AM'")
    status_label: Optional[str] = Field(default=None, description="Printed status exactly as shown, e.g. 'Delivered', 'Cancelled', 'Customer unavailable'")
    orders_in_trip: Optional[int] = Field(default=None, description="Number of orders in this trip if printed (e.g. '2 orders', 'Batched x2')")
    pickups_in_trip: Optional[int] = Field(default=None, description="Number of pickups if printed (e.g. 'Multi-pickup', '2 pickups')")
    batch_id: Optional[str] = Field(default=None, description="Printed trip/batch ID that several orders share, if any")
    amount: Optional[Decimal] = Field(default=None, description="Printed earning for this order")
    tip_amount: Optional[Decimal] = Field(default=None, description="Customer tip printed for this order, e.g. 'Includes customer tip of Rs 10' -> 10; null if none")
    distance_km: Optional[Decimal] = Field(default=None, description="Printed total distance in km")


class IncentiveProgress(BaseModel):
    incentive_name: Optional[str] = Field(default=None, description="Name of the incentive/target as printed")
    period_start: Optional[dt.date] = None
    period_end: Optional[dt.date] = None
    target_orders: Optional[int] = Field(default=None, description="Printed order target, e.g. 20 in '16/20 orders'")
    completed_orders_shown: Optional[int] = Field(default=None, description="Orders the app shows as counted, e.g. 16 in '16/20 orders'")
    reward_amount: Optional[Decimal] = Field(default=None, description="Printed reward for reaching the target")
    status_label: Optional[str] = Field(default=None, description="Printed status, e.g. 'Missed', 'Achieved', 'In progress'")
    conditions_text: List[str] = Field(default_factory=list, description="Each printed condition, copied exactly")


class IncentivePayment(BaseModel):
    label: Optional[str] = Field(default=None, description="Printed label, e.g. 'Weekly incentive', 'Target bonus'")
    amount: Optional[Decimal] = None
    date: Optional[dt.date] = Field(default=None, description="Date the incentive was paid or credited, if printed")
    period_start: Optional[dt.date] = Field(default=None, description="Period the incentive is for, if printed")
    period_end: Optional[dt.date] = None


class PerformanceFigure(BaseModel):
    metric: Literal["acceptance_rate", "rejection_rate", "login_minutes"]
    value: Optional[str] = Field(default=None, description="Printed value: a percentage like '82%', or minutes for login time")
    date: Optional[dt.date] = None
    period_start: Optional[dt.date] = None
    period_end: Optional[dt.date] = None


class StatementPeriod(BaseModel):
    period_start: Optional[dt.date] = None
    period_end: Optional[dt.date] = None
    itemizes_incentives: bool = Field(default=False, description="True only if the statement has lines for incentives/bonuses (even if zero)")


class IncentiveDocumentExtraction(BaseModel):
    trips: List[IncentiveTrip] = Field(default_factory=list)
    incentive_progress: List[IncentiveProgress] = Field(default_factory=list)
    incentive_payments: List[IncentivePayment] = Field(default_factory=list)
    performance: List[PerformanceFigure] = Field(default_factory=list)
    statement_periods: List[StatementPeriod] = Field(default_factory=list)
    work_time: List[WorkTimeEntry] = Field(default_factory=list)


def build_incentive_prompt(platform: str, text: Optional[str] = None) -> str:
    prompt = f"""You are reading a delivery partner's {platform} documents (trip history, earnings screens, incentive screens).
Copy what is printed. Never calculate, count, guess or fill in a value that is not printed.

trips: one entry per order or trip row.
- order_id: only an ID the screen labels as an order or trip ID. Never use a store, restaurant, hub or zone name or code as the order_id; if no order ID is printed, use null.
- date_text: the date for the row exactly as printed (e.g. "8 Oct"), taken from the row or, if the row has none, from the date heading or date picker on the screen. Do not add a year that is not printed.
- date: YYYY-MM-DD only when the year is printed; otherwise null.
- accepted_time and completed_time exactly as printed. If a row shows only one time, put it in accepted_time. Rows cut off at the top or bottom of the screen: copy only what is visible.
- status_label: the printed status exactly (Delivered, Cancelled, Rejected, Customer unavailable, ...). Null if none printed.
- orders_in_trip / pickups_in_trip: only if the row prints that it had several orders or pickups. batch_id: a shared trip ID if printed.
- amount: the printed earning for that order. tip_amount: the customer tip printed for that order, if any. distance_km: printed total distance.

incentive_progress: each incentive or target screen, e.g. "16/20 orders - Weekly target - Rs 300".
- completed_orders_shown is the number the app shows as done (16); target_orders is the target (20).
- Copy period dates, reward, status and every printed condition (acceptance, login, time windows) into conditions_text.

incentive_payments: each incentive, bonus or target payment shown as paid or credited, with label, amount and dates.

performance: acceptance rate, rejection rate or login time printed with a date or period. login_minutes values are minutes.

statement_periods: each earnings statement or payout breakdown, with its period and whether it has lines for incentives/bonuses.

work_time: every login, online or shift time printed, as for any FairPay document.

Use null for anything not printed."""
    if text is not None:
        prompt += f"\n\nText:\n{text}"
    return prompt


def _read_text(text: str, platform: str, seed: int = FIRST_READ_SEED) -> IncentiveDocumentExtraction:
    messages = [{"role": "user", "content": build_incentive_prompt(platform, text)}]
    return _read_with_fallback(
        lambda: client.chat.completions.create(
            model=TEXT_EXTRACTION_MODEL,
            response_model=IncentiveDocumentExtraction,
            messages=messages,
            temperature=0.0,
            seed=seed,
            max_retries=2,
        ),
        lambda reader: reader.chat.completions.create(
            model=FALLBACK_VISION_MODEL,
            response_model=IncentiveDocumentExtraction,
            messages=messages,
            temperature=0.0,
            max_retries=2,
        ),
        "Incentive document extraction failed.")


def _read_image(image, platform: str, seed: int = FIRST_READ_SEED) -> IncentiveDocumentExtraction:
    try:
        redacted, _ = redact_pii(image)
    except Exception as exc:
        raise EvidenceExtractionError("PII redaction failed; the image was not sent to the LLM.") from exc
    url = _image_data_url(redacted)
    messages = [{"role": "user", "content": [
        {"type": "text", "text": build_incentive_prompt(platform)},
        {"type": "image_url", "image_url": {"url": url}},
    ]}]
    return _read_with_fallback(
        lambda: client.chat.completions.create(
            model=VISION_EXTRACTION_MODEL,
            response_model=IncentiveDocumentExtraction,
            messages=messages,
            temperature=0.0,
            seed=seed,
            max_retries=2,
        ),
        lambda reader: reader.chat.completions.create(
            model=FALLBACK_VISION_MODEL,
            response_model=IncentiveDocumentExtraction,
            messages=messages,
            temperature=0.0,
            max_retries=2,
        ),
        "Incentive image extraction failed.")


_PRINTED_TIME = re.compile(r"\b(\d{1,2})[:.](\d{2})\s*([ap])\.?\s*m\b", re.IGNORECASE)


def printed_times(image: Image.Image) -> Set[Tuple[int, int]]:
    """Clock times such as '8:09pm' printed on a screenshot, found by OCR on this machine.

    Used only to check that the AI's reading is complete, never as data. The
    phone's status-bar clock has no am/pm, so it is not picked up.
    """
    gray = ImageOps.grayscale(image.convert("RGB"))
    if ImageStat.Stat(gray).mean[0] < 128:  # dark-mode screenshot
        gray = ImageOps.invert(gray)
    gray = gray.resize((gray.width * 2, gray.height * 2), Image.LANCZOS)
    try:
        text = pytesseract.image_to_string(gray)
    except Exception:
        return set()
    found: Set[Tuple[int, int]] = set()
    for hour, minute, half in _PRINTED_TIME.findall(text):
        h, m = int(hour), int(minute)
        if 1 <= h <= 12 and m < 60:
            found.add((h % 12 + (12 if half.lower() == "p" else 0), m))
    return found


def _trip_time(trip: IncentiveTrip) -> Optional[Tuple[int, int]]:
    return _parse_clock(trip.accepted_time) or _parse_clock(trip.completed_time)


def _read_image_completely(image: Image.Image, platform: str) -> Tuple[IncentiveDocumentExtraction, int]:
    """Read a screenshot; while it shows more trip times than the AI returned, read it again.

    A re-read only adds trips whose time is not already read. Returns the
    reading and how many printed trip times are still missing from it. A
    screen with no trips (an incentive screen, say) is not held to the count.
    """
    expected = printed_times(image)
    extraction = _read_image(image, platform, FIRST_READ_SEED)
    reads = 1

    def have() -> Set[Tuple[int, int]]:
        return {t for t in map(_trip_time, extraction.trips) if t}

    def short() -> bool:
        return len(have()) < len(expected) if extraction.trips else len(expected) >= 3

    while short() and reads < MAX_READS_PER_IMAGE:
        again = _read_image(image, platform, FIRST_READ_SEED + reads)
        reads += 1
        if not extraction.trips:
            extraction = again
            continue
        known = have()
        extraction.trips.extend(t for t in again.trips if _trip_time(t) and _trip_time(t) not in known)
    missing = max(0, len(expected) - len(have())) if extraction.trips else 0
    return extraction, missing


def extract_incentive_evidence(pdf_paths: List[str], image_paths: List[str], platform: str) -> Dict[str, Any]:
    """Read every document once; return plain data for ``evidence_from_extraction``.

    One reader choice is kept for the whole call: once Groq's daily allowance
    runs out, every later document is read by the fallback reader too.
    """
    with reader_choice():
        return _extract_incentive_evidence(pdf_paths, image_paths, platform)


def _extract_incentive_evidence(pdf_paths: List[str], image_paths: List[str], platform: str) -> Dict[str, Any]:
    from PIL import Image

    parts: List[tuple[str, IncentiveDocumentExtraction]] = []
    read_gaps: List[str] = []
    # Documents that could not be read in full. With any of these the day is
    # not judged: the partner is asked to send them again (see telegram_audit).
    unread: List[Dict[str, Any]] = []
    pdf_text, scanned = read_pdfs(pdf_paths) if pdf_paths else ("", [])
    if pdf_text.strip():
        try:
            masked = mask_pii(pdf_text)
        except Exception as exc:
            raise EvidenceExtractionError("PII sanitization failed; extraction was not sent to the LLM.") from exc
        try:
            parts.append(("PDF text", _read_text(masked, platform)))
        except ReaderBusy as exc:
            unread.append({"document": "PDF text", "reason": "busy", "wait_minutes": exc.wait_minutes,
                           "daily": exc.daily})

    images = []
    for index, path in enumerate(image_paths, start=1):
        try:
            with Image.open(path) as handle:
                images.append((f"image {index}", handle.copy()))
        except Exception:
            read_gaps.append(f"Image {index} could not be opened, so it was not checked.")
            unread.append({"document": f"image {index}", "reason": "unreadable"})
    images.extend((f"scanned PDF page {i}", page) for i, page in enumerate(scanned, start=1))
    for label, image in images:
        try:
            extraction, missing = _read_image_completely(image, platform)
            parts.append((label, extraction))
            if missing:
                read_gaps.append(f"{label.capitalize()}: {missing} trip time(s) printed on it could not be read.")
                unread.append({"document": label, "reason": "incomplete", "missing_times": missing})
        except ReaderBusy as exc:
            unread.append({"document": label, "reason": "busy", "wait_minutes": exc.wait_minutes,
                           "daily": exc.daily})
        except EvidenceExtractionError as exc:
            read_gaps.append(f"{label.capitalize()} could not be read ({exc}), so it was not checked.")
            unread.append({"document": label, "reason": "unreadable"})

    out: Dict[str, Any] = {k: [] for k in ("trips", "incentive_progress", "incentive_payments", "performance",
                                           "statement_periods")}
    work_entries: List[WorkTimeEntry] = []
    for source, extraction in parts:
        data = extraction.model_dump(mode="json")
        for key in out:
            out[key].extend({**item, "source": source} for item in data.get(key) or [])
        work_entries.extend(extraction.work_time)
    work_time, work_gaps = to_work_time(work_entries)
    out["work_time"] = work_time
    out["read_gaps"] = read_gaps + work_gaps
    out["unread"] = unread
    if not unread and not any(out[k] for k in ("trips", "incentive_progress", "incentive_payments")):
        raise EvidenceExtractionError(
            ("No orders or incentive information could be read from these documents. " + " ".join(read_gaps)).strip())
    return out
