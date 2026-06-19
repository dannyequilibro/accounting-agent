import anthropic
import base64
import json
import re
from pathlib import Path

client = anthropic.Anthropic()

EXTRACTION_PROMPT = """You are an expert accounting assistant. Extract all invoice details from this image.

Return a JSON object with exactly these fields:
{
  "vendor_name": "string or null",
  "vendor_address": "string or null",
  "invoice_number": "string or null",
  "invoice_date": "YYYY-MM-DD or null",
  "due_date": "YYYY-MM-DD or null",
  "currency": "SGD or USD or MYR etc, default SGD if unclear",
  "subtotal": number or null,
  "tax_amount": number or null,
  "total_amount": number or null,
  "line_items": [
    {"description": "string", "quantity": number, "unit_price": number, "amount": number}
  ],
  "is_handwritten": boolean,
  "confidence": "high" or "medium" or "low",
  "confidence_reasons": ["list of reasons if not high"]
}

Confidence rules:
- HIGH: printed invoice, all key fields present, totals check out
- MEDIUM: mostly printed but minor issues (one missing field, total slightly off)
- LOW: handwritten, missing vendor/amount/date, totals don't add up, blurry/unclear

Return only the JSON, no other text."""


def extract_invoice(file_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    encoded = base64.standard_b64encode(file_bytes).decode("utf-8")

    if mime_type == "application/pdf":
        # For PDFs, use document source type
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": encoded,
                        },
                    },
                    {"type": "text", "text": EXTRACTION_PROMPT}
                ],
            }],
        )
    else:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": encoded,
                        },
                    },
                    {"type": "text", "text": EXTRACTION_PROMPT}
                ],
            }],
        )

    raw = message.content[0].text.strip()
    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        # Return a low-confidence placeholder so the file gets logged as an exception
        return {
            "vendor_name": None,
            "invoice_number": None,
            "invoice_date": None,
            "due_date": None,
            "total_amount": None,
            "confidence": "low",
            "is_handwritten": False,
            "confidence_reasons": [f"Failed to parse Claude response: {e}"],
        }


def is_exception(data: dict) -> tuple[bool, list[str]]:
    reasons = []

    if data.get("confidence") == "low":
        reasons.append("Low confidence extraction")
    if data.get("is_handwritten"):
        reasons.append("Handwritten invoice")
    if not data.get("vendor_name"):
        reasons.append("Missing vendor name")
    if not data.get("total_amount"):
        reasons.append("Missing total amount")
    if not data.get("invoice_date"):
        reasons.append("Missing invoice date")

    # Cross-check totals if available
    subtotal = data.get("subtotal") or 0
    tax = data.get("tax_amount") or 0
    total = data.get("total_amount") or 0
    if subtotal and total and abs((subtotal + tax) - total) > 0.10:
        reasons.append(f"Total mismatch: {subtotal} + {tax} tax ≠ {total}")

    return len(reasons) > 0, reasons
