from __future__ import annotations
import base64
import io
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Literal, Optional, Tuple

import instructor
import pdfplumber
import pytesseract
from dotenv import load_dotenv
from groq import Groq
from PIL import Image, ImageDraw, ImageOps, ImageStat
from pydantic import BaseModel, Field
from pytesseract import Output

from real_data import ExtractedFinancialRecord
from sanitizer import PII_ENTITIES, analyzer, mask_pii


class EvidenceExtractionError(Exception):
    """Raised when source evidence cannot be safely extracted or normalized."""

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TESSERACT_PATH = os.getenv("TESSERACT_PATH", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
client = instructor.from_groq(Groq(api_key=GROQ_API_KEY), mode=instructor.Mode.JSON)

# Text evidence (PDF text layers) keeps the existing model. Images go to a
# vision model, because Tesseract misreads the rupee sign as a digit
# (e.g. "Rs 94.00" -> "794.00"), which corrupts amounts before any LLM sees them.
TEXT_EXTRACTION_MODEL = "openai/gpt-oss-120b"
VISION_EXTRACTION_MODEL = os.getenv("FAIRPAY_VISION_MODEL", "qwen/qwen3.8-27b")

# Gap wording other modules (e.g. the Telegram summary) look for.
NOT_CHECKED_PREFIX = "Not checked (no date on the document):"
# Raised when the documents hold totals or bank credits but no checkable
# order; the Telegram bridge reports such documents as not checked.
TOTALS_ONLY_MESSAGE = "These documents show totals (an earnings summary or bank credits), not individual orders."

# Groq rejects base64 images above 4 MB; stay safely under it.
_MAX_IMAGE_BASE64_BYTES = 3_500_000


# ---------------------------------------------------------------------------
# What the LLM reads off a document
# ---------------------------------------------------------------------------
# This is deliberately looser than ExtractedFinancialRecord: the model reports
# what is printed (including blanks), and the code below decides what can be
# reconciled. Derived values (total distance, wait time) are computed in code
# from the printed parts, never by the model.

class DocumentRecord(BaseModel):
    record_type: Literal["earning", "settlement"]
    record_id: Optional[str] = Field(default=None, description="Settlement ID or transaction ID, if printed")
    order_id: Optional[str] = None
    record_date: Optional[date] = Field(default=None, description="Date printed for this order or payment; null if the row has no date of its own")
    amount: Optional[Decimal] = Field(default=None, description="Earnings: the order's total earning. Settlements: the amount paid")
    gross_amount: Optional[Decimal] = None
    incentive: Optional[Decimal] = None
    bonus: Optional[Decimal] = None
    deduction: Optional[Decimal] = None
    deduction_policy_reference: Optional[str] = None
    distance_km: Optional[Decimal] = Field(default=None, description="Only when one total distance is printed")
    distance_legs_km: List[Decimal] = Field(default_factory=list, description="Each printed part of the distance, e.g. first mile and last mile")
    duration_minutes: Optional[int] = Field(default=None, description="Total trip time, if printed")
    wait_time_minutes: Optional[int] = Field(default=None, description="Only when waiting minutes are printed")
    arrived_at_pickup: Optional[str] = Field(default=None, description="Printed time of arriving at the pickup location, e.g. '02:16 PM'")
    picked_up_at: Optional[str] = Field(default=None, description="Printed time the order was picked up, e.g. '02:22 PM'")
    order_time: Optional[str] = Field(default=None, description="Printed time of the order itself (placed, accepted or delivered), e.g. '01:42 PM'")
    order_time_label: Optional[str] = Field(default=None, description="The label printed next to order_time, e.g. 'Delivered at'")
    contract_version: Optional[str] = None


class WorkTimeEntry(BaseModel):
    """Working (login / online / shift) time printed on a document."""
    work_date: Optional[date] = Field(default=None, description="Date this working time belongs to; null if not printed")
    login_start: Optional[str] = Field(default=None, description="Printed start of a login or shift time range, e.g. '11:00 AM'")
    login_end: Optional[str] = Field(default=None, description="Printed end of that range, e.g. '03:00 PM'")
    hours_part: Optional[Decimal] = Field(default=None, description="Hours of a printed duration, e.g. 6 in '6h 20m'")
    minutes_part: Optional[int] = Field(default=None, description="Minutes of a printed duration, e.g. 20 in '6h 20m'")


class SummaryAdjustment(BaseModel):
    """An adjustment printed on an earnings summary (not tied to one order)."""
    label: str = Field(description="The adjustment's label exactly as printed")
    amount: Optional[Decimal] = Field(default=None, description="Printed amount, as a positive number")
    kind: Literal["deduction", "fee", "cash_collected", "advance_or_instant_withdrawal", "other"] = "other"


class SummaryEntry(BaseModel):
    """A total that is not tied to one order: an earnings summary or a bank credit."""
    summary_type: Literal["earnings_summary", "bank_credit"]
    period_start: Optional[date] = Field(default=None, description="Earnings summary: first day the printed total covers")
    period_end: Optional[date] = Field(default=None, description="Earnings summary: last day the printed total covers")
    credit_date: Optional[date] = Field(default=None, description="Bank credit: the date the money was credited")
    amount: Optional[Decimal] = Field(default=None, description="The printed total or credited amount")
    amount_label: Optional[str] = Field(default=None, description="The label printed next to the amount, exactly as shown, e.g. 'Total earnings', 'Net payout', 'Amount credited'")
    order_count: Optional[int] = Field(default=None, description="Number of orders, only if printed on the summary")
    adjustments: List[SummaryAdjustment] = Field(default_factory=list, description="Deductions, fees, cash collected or advances printed on the summary")
    payer_label: Optional[str] = Field(default=None, description="Bank credit: the sender or narration as printed")
    reference: Optional[str] = Field(default=None, description="Bank credit: the printed transaction reference (e.g. UTR), if any")


class DocumentExtraction(BaseModel):
    records: List[DocumentRecord] = Field(default_factory=list)
    summaries: List[SummaryEntry] = Field(default_factory=list)
    work_time: List[WorkTimeEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Reading the source files
# ---------------------------------------------------------------------------

def read_pdfs(pdf_paths: List[str]) -> Tuple[str, List[Image.Image]]:
    """Return the PDFs' text layers, plus page images for scanned pages.

    Scanned pages go through the same redact-then-vision path as photos rather
    than Tesseract, for the same rupee-sign reason.
    """
    text_parts: List[str] = []
    page_images: List[Image.Image] = []
    for pdf in pdf_paths:
        try:
            with pdfplumber.open(pdf) as pdf_object:
                for page in pdf_object.pages:
                    page_text = page.extract_text() or ""
                    if page_text.strip():
                        text_parts.append(page_text)
                    else:
                        page_images.append(page.to_image(resolution=200).original.copy())
        except Exception as exc:
            raise EvidenceExtractionError("PDF extraction failed.") from exc
    return "\n".join(text_parts), page_images


def _looks_like_data(word: str) -> bool:
    """Amounts, IDs, dates and times are never names; never black them out as one."""
    return any(ch.isdigit() for ch in word)


def redact_pii(image: Image.Image) -> Tuple[Image.Image, int]:
    """Black out names, phone numbers and emails before an image leaves the machine.

    Tesseract is used only to *locate* words; its misread amounts are never
    used as data. The same Presidio analyzer and entity list as mask_pii()
    decide what is personal. Returns (redacted image, boxes drawn).
    """
    rgb = image.convert("RGB")
    gray = ImageOps.grayscale(rgb)
    if ImageStat.Stat(gray).mean[0] < 128:  # dark-mode screenshot
        gray = ImageOps.invert(gray)
    scale = 2
    prepped = gray.resize((gray.width * scale, gray.height * scale), Image.LANCZOS)
    data = pytesseract.image_to_data(prepped, output_type=Output.DICT)

    pieces: List[str] = []
    spans: List[Tuple[int, int, str, int, int, int, int]] = []
    cursor = 0
    last_line = None
    for i, raw in enumerate(data["text"]):
        word = (raw or "").strip()
        if not word:
            continue
        line = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        if pieces:
            pieces.append(" " if line == last_line else "\n")
            cursor += 1
        start = cursor
        pieces.append(word)
        cursor += len(word)
        spans.append((start, cursor, word, data["left"][i], data["top"][i], data["width"][i], data["height"][i]))
        last_line = line

    text = "".join(pieces)
    if not text.strip():
        return rgb, 0

    findings = analyzer.analyze(text=text, entities=PII_ENTITIES, language="en")
    draw = ImageDraw.Draw(rgb)
    boxes = 0
    pad = 4
    for finding in findings:
        for start, end, word, left, top, width, height in spans:
            if start >= finding.end or end <= finding.start:
                continue
            if finding.entity_type == "PERSON" and _looks_like_data(word):
                continue
            draw.rectangle(
                [left / scale - pad, top / scale - pad,
                 (left + width) / scale + pad, (top + height) / scale + pad],
                fill=(0, 0, 0),
            )
            boxes += 1
    return rgb, boxes


def _image_data_url(image: Image.Image) -> str:
    img = image.convert("RGB")
    payload = b""
    for _ in range(5):
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=90)
        payload = buffer.getvalue()
        if len(payload) * 4 / 3 < _MAX_IMAGE_BASE64_BYTES:
            break
        img = img.resize((int(img.width * 0.75), int(img.height * 0.75)), Image.LANCZOS)
    return "data:image/jpeg;base64," + base64.b64encode(payload).decode()


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------

def build_extraction_prompt(platform: str, text: Optional[str] = None) -> str:
    """Build the platform-specific extraction prompt (shared by text and images)."""
    prompt = f"""You are reading a delivery partner's {platform} earnings evidence.
Return every order-level record and every printed total you can see.

Record types:
- "earning": what the platform says the partner earned for one order (an order earnings screen, or a row in an order list or statement).
- "settlement": money actually paid out for one order (a settlement or payout history entry).
A total that is not tied to one order ID is NOT a record (put it in summaries instead). A policy, rate card or incentive table is NOT a record.

Summaries (put in summaries, not in records):
- "earnings_summary": a total the platform says the partner earned or is owed for a day, week or other period (for example a weekly earnings screen or a payout summary). Copy the printed period into period_start and period_end. If the screen prints several totals for the same period (for example "Total earnings" and "Net payout"), add one entry per total, each with its own amount_label.
- "bank_credit": money received in a bank account or wallet (a bank statement line, an SMS or a UPI/bank credit screen). One entry per credit, with credit_date, amount, the printed sender or narration in payer_label, and the printed reference if any. Do not include debits.
- amount_label: copy the label printed next to the amount exactly as shown. Do not reword it.
- adjustments: only deductions, fees, cash collected or advances/instant withdrawals that the summary itself prints, each with its printed label and amount.
- order_count: only if a number of orders is printed on the summary.
- Never add up order rows to make a summary. Never create a summary that is not printed.

Rules:
- Copy values exactly as printed. Amounts are plain numbers with no currency symbol or commas (1,020.00 -> 1020.00).
- Use null for anything not printed. Never guess a date, amount or ID.
- record_date: the date printed for that specific order or payment, as YYYY-MM-DD. If the row has no date of its own, use null. Do not use a statement period as a row's date.
- amount: for earnings, the order's total earning; for settlements, the amount paid.
- distance_km: only when a single total distance is printed. When the distance is printed in parts (for example first mile and last mile), list each part in distance_legs_km and leave distance_km null.
- duration_minutes: the total trip time, if printed.
- wait_time_minutes: only when waiting minutes are printed.
- arrived_at_pickup and picked_up_at: the printed times of arriving at the pickup location and of picking up the order, exactly as shown (for example "02:16 PM").
- order_time: the printed time of the order itself (placed, accepted or delivered), exactly as shown, with its printed label in order_time_label (for example "Delivered at"). Null if no such time is printed.
- incentive, bonus, deduction: only per-order values printed for that order.

Working time (put in work_time, not in records):
- Add an entry for every login, online or shift time the document prints, with its date in work_date (null if no date is printed for it).
- When a time range is printed (for example "Login 11:00 AM - 03:00 PM"), copy its ends into login_start and login_end, one entry per range.
- When only a duration is printed (for example "Online hours: 6h 20m"), put the hours in hours_part and the minutes in minutes_part (6 and 20).
- Never work out working time from order or delivery times. If no working time is printed, leave work_time empty."""

    platform_hints = {
        "uber": "Look for: trip fare, promotions, service fee, surge, wait time, distance.",
        "zepto": "Look for: order value, delivery fee, peak bonus, distance, time slots.",
        "zomato": "Look for: order amount, delivery charge, platform fee, incentives, distance.",
        "blinkit": "Look for: order value, delivery fee, bonus, distance, wait time.",
        "swiggy": "Look for: order amount, delivery fee, platform fee, peak pay, distance.",
    }
    hint = platform_hints.get(platform.lower(), "")
    if hint:
        prompt += f"\n\nPlatform-specific hints: {hint}"
    if text is not None:
        prompt += f"\n\nText:\n{text}"
    return prompt


def extract_transactions(text: str, platform: str) -> DocumentExtraction:
    """Extract records and working time from (already PII-masked) document text.

    Read by Qwen on Groq, or by the NVIDIA fallback once Groq's daily
    allowance is used up (see reader_fallback).
    """
    from reader_fallback import FALLBACK_VISION_MODEL, read_with_fallback

    messages = [{"role": "user", "content": build_extraction_prompt(platform, text)}]
    return read_with_fallback(
        lambda: client.chat.completions.create(
            model=TEXT_EXTRACTION_MODEL,
            response_model=DocumentExtraction,
            messages=messages,
            temperature=0.0,
            max_retries=2,
        ),
        lambda reader: reader.chat.completions.create(
            model=FALLBACK_VISION_MODEL,
            response_model=DocumentExtraction,
            messages=messages,
            temperature=0.0,
            max_retries=2,
        ),
        "Structured document extraction failed.")


def extract_image_records(image: Image.Image, platform: str) -> DocumentExtraction:
    """Redact personal data, then let the vision model read the image.

    Fails closed: if redaction fails, the image is not sent anywhere.
    """
    try:
        redacted, _ = redact_pii(image)
    except Exception as exc:
        raise EvidenceExtractionError("PII redaction failed; the image was not sent to the LLM.") from exc

    from reader_fallback import FALLBACK_VISION_MODEL, read_with_fallback

    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": build_extraction_prompt(platform)},
            {"type": "image_url", "image_url": {"url": _image_data_url(redacted)}},
        ],
    }]
    # Qwen on Groq, or the NVIDIA fallback once Groq's daily allowance is used up.
    return read_with_fallback(
        lambda: client.chat.completions.create(
            model=VISION_EXTRACTION_MODEL,
            response_model=DocumentExtraction,
            messages=messages,
            temperature=0.0,
            max_retries=2,
        ),
        lambda reader: reader.chat.completions.create(
            model=FALLBACK_VISION_MODEL,
            response_model=DocumentExtraction,
            messages=messages,
            temperature=0.0,
            max_retries=2,
        ),
        "Structured image extraction failed.")


# ---------------------------------------------------------------------------
# From what was read to what can be reconciled
# ---------------------------------------------------------------------------

def _parse_clock(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    cleaned = value.strip().upper().replace(".", "")
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def _rs(amount: Optional[Decimal]) -> str:
    return f"Rs {amount:.2f}" if amount is not None else "amount unreadable"


def _merge_group(group: List[DocumentRecord]) -> Tuple[Optional[DocumentRecord], Optional[str]]:
    """Merge copies of the same record seen on several documents.

    Returns (merged, None), or (None, reason) when the copies disagree on the
    amount or date - such a record is reported, not reconciled.
    """
    amounts = {r.amount for r in group if r.amount is not None}
    dates = {r.record_date for r in group if r.record_date is not None}
    if len(amounts) > 1:
        return None, "amounts " + ", ".join(_rs(a) for a in sorted(amounts))
    if len(dates) > 1:
        return None, "dates " + ", ".join(d.isoformat() for d in sorted(dates))

    merged = group[0].model_copy()
    for other in group[1:]:
        for name in DocumentRecord.model_fields:
            if name == "distance_legs_km":
                if not merged.distance_legs_km and other.distance_legs_km:
                    merged.distance_legs_km = list(other.distance_legs_km)
            elif getattr(merged, name) is None and getattr(other, name) is not None:
                setattr(merged, name, getattr(other, name))
    return merged, None


def to_financial_records(
    documents: List[DocumentRecord], platform: str
) -> Tuple[List[ExtractedFinancialRecord], List[str]]:
    """Turn what was read into records the existing reconciliation can use.

    Anything that cannot be reconciled honestly - no order ID, unreadable
    amount, no date, or copies that disagree - is reported as a gap and left
    out. Nothing is guessed.
    """
    gaps: List[str] = []
    groups: Dict[Tuple, List[DocumentRecord]] = {}

    for doc in documents:
        if not doc.order_id:
            gaps.append(f"A {doc.record_type} on the documents has no order ID, so it could not be checked.")
            continue
        if doc.record_type == "earning":
            key: Tuple = ("earning", doc.order_id)  # one earning per order
        elif doc.record_id:
            key = ("settlement", doc.record_id)  # same payment shown twice
        else:
            # No settlement ID: treat identical rows as the same printed payment.
            key = ("settlement", doc.order_id, doc.amount, doc.record_date)
        groups.setdefault(key, []).append(doc)

    records: List[ExtractedFinancialRecord] = []
    used_ids: set[str] = set()

    for group in groups.values():
        doc, conflict = _merge_group(group)
        sample = group[0]
        if conflict:
            gaps.append(
                f"Conflicting {conflict} for the {sample.record_type} of order "
                f"{sample.order_id} across documents, so it was not checked."
            )
            continue
        assert doc is not None

        if doc.amount is None or doc.amount < 0:
            gaps.append(f"The {doc.record_type} amount for order {doc.order_id} could not be read, so it was not checked.")
            continue
        if doc.record_date is None:
            gaps.append(f"{NOT_CHECKED_PREFIX} {doc.record_type} for order {doc.order_id} ({_rs(doc.amount)}).")
            continue

        # Derived values come from printed parts only, computed here.
        distance = doc.distance_km
        if distance is None and doc.distance_legs_km:
            distance = sum(doc.distance_legs_km, Decimal("0"))
        wait = doc.wait_time_minutes
        if wait is None:
            arrived, picked = _parse_clock(doc.arrived_at_pickup), _parse_clock(doc.picked_up_at)
            if arrived and picked and picked >= arrived:
                wait = int((picked - arrived).total_seconds() // 60)

        base_id = doc.record_id or (
            f"EARN-{doc.order_id}" if doc.record_type == "earning" else f"STL-{doc.order_id}"
        )
        record_id, suffix = base_id, 2
        while record_id in used_ids:
            record_id, suffix = f"{base_id}-{suffix}", suffix + 1
        used_ids.add(record_id)

        def _non_negative(value: Optional[Decimal]) -> Optional[Decimal]:
            return value if value is not None and value >= 0 else None

        records.append(ExtractedFinancialRecord(
            record_type=doc.record_type,
            record_id=record_id,
            transaction_id=record_id,
            platform=platform,  # the announced platform is authoritative for matching
            order_id=doc.order_id,
            record_date=doc.record_date,
            amount=doc.amount,
            gross_amount=_non_negative(doc.gross_amount),
            incentive=_non_negative(doc.incentive),
            bonus=_non_negative(doc.bonus),
            deduction=_non_negative(doc.deduction),
            deduction_policy_reference=doc.deduction_policy_reference,
            distance_km=_non_negative(distance),
            duration_minutes=doc.duration_minutes if doc.duration_minutes is not None and doc.duration_minutes >= 0 else None,
            wait_time_minutes=wait if wait is not None and wait >= 0 else None,
            contract_version=doc.contract_version,
        ))
    return records, gaps


def to_work_time(entries: List[WorkTimeEntry]) -> Tuple[List[Dict], List[str]]:
    """Turn printed working time into ``{date, start, end, minutes}`` entries.

    A printed range keeps its start and end (so overlaps across platforms can
    be merged later); a printed duration keeps only its minutes. A range that
    ends before it starts is taken to run past midnight. Undated entries are
    reported, not used.
    """
    gaps: List[str] = []
    seen: set = set()
    result: List[Dict] = []
    undated = 0
    for entry in entries:
        if entry.work_date is None:
            undated += 1
            continue
        start, end = _parse_clock(entry.login_start), _parse_clock(entry.login_end)
        if start and end:
            minutes = int((end - start).total_seconds() // 60)
            if minutes <= 0:
                minutes += 24 * 60
            item = {
                "date": entry.work_date.isoformat(),
                "start": start.strftime("%H:%M"),
                "end": end.strftime("%H:%M"),
                "minutes": minutes,
            }
        elif entry.hours_part is not None or entry.minutes_part is not None:
            minutes = int((entry.hours_part or Decimal("0")) * 60) + (entry.minutes_part or 0)
            if minutes <= 0:
                continue
            item = {"date": entry.work_date.isoformat(), "start": None, "end": None, "minutes": minutes}
        else:
            continue
        key = (item["date"], item["start"], item["end"], item["minutes"])
        if key in seen:  # the same screen sent twice
            continue
        seen.add(key)
        result.append(item)
    if undated:
        gaps.append(f"Working time shown without a date was not used ({undated} entr{'y' if undated == 1 else 'ies'}).")
    return result, gaps


def detect_gaps(transactions: List[ExtractedFinancialRecord]) -> List[str]:
    """Detect missing critical fields in earning records.

    Settlements never carry trip details, so they are not checked here.
    """
    gaps = []
    for tx in transactions:
        if tx.record_type != "earning":
            continue
        if tx.wait_time_minutes is None:
            gaps.append(f"wait_time_minutes missing for record {tx.record_id}")
        if tx.distance_km is None:
            gaps.append(f"distance_km missing for record {tx.record_id}")
        if tx.duration_minutes is None:
            gaps.append(f"duration_minutes missing for record {tx.record_id}")
    return gaps


def to_summaries(entries: List[Tuple[str, SummaryEntry]]) -> List[Dict]:
    """Earnings totals and bank credits as plain dicts, tagged with their source.

    Copies of the same printed total (the same screen sent twice) are kept
    once. Nothing is summed or filled in here.
    """
    seen: set = set()
    result: List[Dict] = []
    for source, entry in entries:
        if entry.amount is None or entry.amount < 0:
            continue
        key = (
            entry.summary_type, entry.period_start, entry.period_end, entry.credit_date,
            entry.amount, (entry.amount_label or "").strip().lower(), entry.reference,
        )
        if key in seen:
            continue
        seen.add(key)
        item = entry.model_dump(mode="json")
        item["source"] = source
        result.append(item)
    return result


def to_order_observations(
    documents: List[DocumentRecord], records: List[ExtractedFinancialRecord]
) -> List[Dict]:
    """Printed per-order facts for the orders that passed to_financial_records.

    Carries what the reconciliation records do not (the printed order time),
    so later checks can say exactly what the document showed.
    """
    times: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    for doc in documents:
        if doc.record_type != "earning" or not doc.order_id or doc.order_id in times:
            continue
        if doc.order_time:
            times[doc.order_id] = (doc.order_time, doc.order_time_label or "Order time")
        elif doc.picked_up_at:
            times[doc.order_id] = (doc.picked_up_at, "Picked up at")
        elif doc.arrived_at_pickup:
            times[doc.order_id] = (doc.arrived_at_pickup, "Arrived at pickup")

    observations: List[Dict] = []
    for record in records:
        if record.record_type != "earning":
            continue
        printed_time, time_label = times.get(record.order_id, (None, None))
        clock = _parse_clock(printed_time)
        observations.append({
            "order_id": record.order_id,
            "transaction_id": record.transaction_id,
            "platform": record.platform,
            "date": record.record_date.isoformat(),
            "time": clock.strftime("%H:%M") if clock else None,
            "time_label": time_label if clock else None,
            "amount": str(record.amount),
            "gross_amount": None if record.gross_amount is None else str(record.gross_amount),
            "incentive": None if record.incentive is None else str(record.incentive),
            "bonus": None if record.bonus is None else str(record.bonus),
            "deduction": None if record.deduction is None else str(record.deduction),
            "distance_km": None if record.distance_km is None else str(record.distance_km),
            "duration_minutes": record.duration_minutes,
            "wait_time_minutes": record.wait_time_minutes,
        })
    return observations


def extract_evidence(
    pdf_paths: List[str],
    image_paths: List[str],
    platform: str,
) -> Dict:
    """Read every document once: order rows, printed totals and working time.

    Same OCR / PII / LLM path for every evidence mode. Returns
    ``{"documents", "summaries", "work_entries", "read_gaps"}`` and raises
    only when nothing at all could be read.
    """
    from reader_fallback import reader_choice

    with reader_choice():
        return _extract_evidence(pdf_paths, image_paths, platform)


def _extract_evidence(pdf_paths: List[str], image_paths: List[str], platform: str) -> Dict:
    documents: List[DocumentRecord] = []
    summaries: List[Tuple[str, SummaryEntry]] = []
    work_entries: List[WorkTimeEntry] = []
    read_gaps: List[str] = []

    pdf_text, scanned_pages = read_pdfs(pdf_paths) if pdf_paths else ("", [])
    if pdf_text.strip():
        try:
            sanitized_text = mask_pii(pdf_text)
        except Exception as exc:
            raise EvidenceExtractionError("PII sanitization failed; extraction was not sent to the LLM.") from exc
        extraction = extract_transactions(sanitized_text, platform)
        documents.extend(extraction.records)
        summaries.extend(("PDF text", s) for s in extraction.summaries)
        work_entries.extend(extraction.work_time)

    images: List[Tuple[str, Image.Image]] = []
    for index, path in enumerate(image_paths, start=1):
        try:
            with Image.open(path) as image_object:
                images.append((f"image {index}", image_object.copy()))
        except Exception:
            read_gaps.append(f"Image {index} could not be opened, so it was not checked.")
    images.extend((f"scanned PDF page {i}", page) for i, page in enumerate(scanned_pages, start=1))

    # One unreadable image must not stop the rest from being audited.
    for label, image in images:
        try:
            extraction = extract_image_records(image, platform)
            documents.extend(extraction.records)
            summaries.extend((label, s) for s in extraction.summaries)
            work_entries.extend(extraction.work_time)
        except EvidenceExtractionError as exc:
            read_gaps.append(f"{label.capitalize()} could not be read ({exc}), so it was not checked.")

    return {
        "documents": documents,
        "summaries": to_summaries(summaries),
        "work_entries": work_entries,
        "read_gaps": read_gaps,
    }


def process_evidence_detailed(
    pdf_paths: List[str],
    image_paths: List[str],
    platform: str
) -> Dict:
    """Order-level evidence for the audit pipeline, plus what else was printed.

    Returns ``{"records", "gaps", "work_time", "summaries", "order_observations"}``.
    Raises when no order-level record can be checked - the order-level
    pipeline needs at least one.
    """
    bundle = extract_evidence(pdf_paths, image_paths, platform)
    documents = bundle["documents"]
    read_gaps = bundle["read_gaps"]
    summaries = bundle["summaries"]

    if not documents:
        detail = " ".join(read_gaps)
        if summaries:
            raise EvidenceExtractionError(f"{TOTALS_ONLY_MESSAGE} {detail}".strip())
        raise EvidenceExtractionError(
            ("No financial records were found in the uploaded documents. " + detail).strip()
        )

    transactions, conversion_gaps = to_financial_records(documents, platform)
    if not transactions and summaries:
        raise EvidenceExtractionError(
            f"{TOTALS_ONLY_MESSAGE} " + " ".join(read_gaps + conversion_gaps)
        )
    if not transactions:
        raise EvidenceExtractionError(
            "No checkable financial records were found. " + " ".join(read_gaps + conversion_gaps)
        )
    work_time, work_gaps = to_work_time(bundle["work_entries"])
    gaps = detect_gaps(transactions) + read_gaps + conversion_gaps + work_gaps

    return {
        "records": transactions,
        "gaps": gaps,
        "work_time": work_time,
        "summaries": summaries,
        "order_observations": to_order_observations(documents, transactions),
    }


def process_evidence(
    pdf_paths: List[str],
    image_paths: List[str],
    platform: str
) -> Tuple[List[ExtractedFinancialRecord], List[str], List[Dict]]:
    """Main entry point: extract transactions and working time from PDFs/images."""
    result = process_evidence_detailed(pdf_paths, image_paths, platform)
    return result["records"], result["gaps"], result["work_time"]
