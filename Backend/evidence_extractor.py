from __future__ import annotations
from typing import List, Tuple
from decimal import Decimal
import pdfplumber
import pytesseract
from PIL import Image
import instructor
from groq import Groq
import os
from dotenv import load_dotenv

from real_data import ExtractedFinancialRecord
from sanitizer import mask_pii


class EvidenceExtractionError(Exception):
    """Raised when source evidence cannot be safely extracted or normalized."""

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TESSERACT_PATH = os.getenv("TESSERACT_PATH", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
client = instructor.from_groq(Groq(api_key=GROQ_API_KEY))

def extract_pdf_text(pdf_paths: List[str]) -> str:
    full_text = []
    for pdf in pdf_paths:
        try:
            with pdfplumber.open(pdf) as pdf_object:
                for page in pdf_object.pages:
                    page_text = page.extract_text() or ""
                    if not page_text.strip():
                        try:
                            page_text = pytesseract.image_to_string(page.to_image(resolution=300).original)
                        except Exception as exc:
                            raise EvidenceExtractionError("OCR failed for scanned PDF page.") from exc
                    full_text.append(page_text)
        except EvidenceExtractionError:
            raise
        except Exception as exc:
            raise EvidenceExtractionError("PDF extraction failed.") from exc
    return "\n".join(full_text)

def extract_image_text(image_paths: List[str]) -> str:
    extracted_text = []
    for image in image_paths:
        try:
            with Image.open(image) as image_object:
                extracted_text.append(pytesseract.image_to_string(image_object))
        except Exception as exc:
            raise EvidenceExtractionError("Image OCR failed.") from exc
    return "\n\n".join(extracted_text)


def build_extraction_prompt(text: str, platform: str) -> str:
    """Build platform-specific extraction prompt."""
    base_prompt = f"""Extract transaction data from {platform} receipt text.

Return a JSON array of document records with these fields. Use null when a field is absent; never infer a value.
- record_type: "earning" or "settlement"
- record_id: string
- platform: string
- order_id: string
- record_date: YYYY-MM-DD
- amount: decimal (expected earning for earning records; actual paid for settlement records)
- gross_amount: decimal (optional)
- incentive, bonus, deduction: decimal (optional)
- deduction_policy_reference: string (optional)
- distance_km: decimal (optional)
- duration_minutes: int (optional)
- wait_time_minutes: int (optional)
- contract_version: string (optional)

Text:
{text}"""

    platform_hints = {
        "uber": "Look for: trip fare, promotions, service fee, surge, wait time, distance.",
        "zepto": "Look for: order value, delivery fee, peak bonus, distance, time slots.",
        "zomato": "Look for: order amount, delivery charge, platform fee, incentives, distance.",
        "blinkit": "Look for: order value, delivery fee, bonus, distance, wait time.",
        "swiggy": "Look for: order amount, delivery fee, platform fee, peak pay, distance.",
    }

    hint = platform_hints.get(platform.lower(), "")
    return base_prompt + f"\n\nPlatform-specific hints: {hint}"


def extract_transactions(text: str, platform: str) -> List[ExtractedFinancialRecord]:
    """Extract structured transactions using LLM."""
    prompt = build_extraction_prompt(text, platform)

    try:
        return client.chat.completions.create(
            model="llama-3.1-70b-versatile",
            response_model=List[ExtractedFinancialRecord],
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
    except Exception as exc:
        raise EvidenceExtractionError("Structured document extraction failed.") from exc


def detect_gaps(transactions: List[ExtractedFinancialRecord]) -> List[str]:
    """Detect missing critical fields in transactions."""
    gaps = []
    for tx in transactions:
        if tx.wait_time_minutes is None:
            gaps.append(f"wait_time_minutes missing for record {tx.record_id}")
        if tx.distance_km is None:
            gaps.append(f"distance_km missing for record {tx.record_id}")
        if tx.duration_minutes is None:
            gaps.append(f"duration_minutes missing for record {tx.record_id}")
    return gaps


def process_evidence(
    pdf_paths: List[str],
    image_paths: List[str],
    platform: str
) -> Tuple[List[ExtractedFinancialRecord], List[str]]:
    """Main entry point: extract transactions from PDFs/images."""
    all_text = ""

    if pdf_paths:
        all_text += extract_pdf_text(pdf_paths) + "\n\n"
    if image_paths:
        all_text += extract_image_text(image_paths) + "\n\n"

    if not all_text.strip():
        raise EvidenceExtractionError("No readable text could be extracted from the uploaded documents.")

    try:
        sanitized_text = mask_pii(all_text)
    except Exception as exc:
        raise EvidenceExtractionError("PII sanitization failed; extraction was not sent to the LLM.") from exc
    transactions = extract_transactions(sanitized_text, platform)
    if not transactions:
        raise EvidenceExtractionError("No financial records were extracted from the uploaded documents.")
    gaps = detect_gaps(transactions)

    return transactions, gaps
