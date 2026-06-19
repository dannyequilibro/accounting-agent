import os
import base64
import hmac
import hashlib
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv

from drive_client import download_file, get_folder_path, parse_path, get_file_web_url, move_to_posted
from invoice_extractor import extract_invoice, is_exception
from xero_client import create_bill
from email_notifier import log_exception
from vendor_mapping import get_account_code
from sheet_manager import has_vendor_mappings

load_dotenv()

app = FastAPI(title="Accounting Agent")


@app.on_event("startup")
async def startup_event():
    """Write secret files from env vars on Railway where filesystem is ephemeral."""
    sa_b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_B64")
    sa_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    if sa_b64 and not os.path.exists(sa_file):
        with open(sa_file, "w") as f:
            f.write(base64.b64decode(sa_b64).decode())
        print(f"Wrote {sa_file} from env var.")

    xero_b64 = os.getenv("XERO_TOKENS_JSON_B64")
    if xero_b64 and not os.path.exists("xero_tokens.json"):
        with open("xero_tokens.json", "w") as f:
            f.write(base64.b64decode(xero_b64).decode())
        print("Wrote xero_tokens.json from env var.")

SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
}


def verify_webhook_secret(request_secret: str) -> bool:
    expected = os.getenv("WEBHOOK_SECRET", "")
    return hmac.compare_digest(request_secret, expected)


@app.post("/webhook/new-file")
async def handle_new_file(request: Request):
    body = await request.json()

    # Verify the shared secret from Apps Script
    secret = body.get("secret", "")
    if not verify_webhook_secret(secret):
        raise HTTPException(status_code=401, detail="Unauthorized")

    file_id = body.get("fileId")
    if not file_id:
        raise HTTPException(status_code=400, detail="Missing fileId")

    print(f"Processing file: {file_id}")

    # 1. Download file from Drive
    file_bytes, mime_type, file_name = download_file(file_id)

    if mime_type not in SUPPORTED_MIME_TYPES:
        print(f"Skipping unsupported file type: {mime_type} ({file_name})")
        return {"status": "skipped", "reason": f"unsupported type {mime_type}"}

    # 2. Parse client name and location from folder path
    folder_path = get_folder_path(file_id)

    # Skip files already in the Posted folder
    if "Posted" in folder_path:
        print(f"Skipping â€” file is already in Posted folder")
        return {"status": "skipped", "reason": "already in Posted folder"}

    path_info = parse_path(folder_path)
    client_name = path_info["client_name"]
    location = path_info["location"]
    drive_url = get_file_web_url(file_id)

    print(f"Client: {client_name} | Location: {location} | File: {file_name}")

    # 3. Extract invoice data with Claude Vision
    invoice_data = extract_invoice(file_bytes, mime_type)
    print(f"Extracted: vendor={invoice_data.get('vendor_name')}, total={invoice_data.get('total_amount')}, confidence={invoice_data.get('confidence')}")

    # 4. Check if this needs human review
    needs_review, reasons = is_exception(invoice_data)

    if needs_review:
        print(f"Exception â€” logging to sheet: {reasons}")
        log_exception(
            file_name=file_name,
            client_name=client_name,
            location=location,
            drive_url=drive_url,
            invoice_data=invoice_data,
            exception_reasons=reasons,
        )
        return {"status": "exception", "reasons": reasons, "client": client_name}

    # 5. Post to Xero directly
    if not invoice_data.get("vendor_name"):
        return {"status": "error", "reason": "No vendor name extracted"}

    # Resolve account code from vendor mapping or auto-suggest
    account_code, account_name, was_mapped = get_account_code(
        invoice_data["vendor_name"],
        invoice_data.get("line_items", []),
        invoice_data.get("vendor_name", ""),
        client_name=client_name,
    )
    invoice_data["_account_code"] = account_code
    invoice_data["_account_name"] = account_name
    print(f"Account: {account_code} {account_name} ({'mapped' if was_mapped else 'suggested â€” needs review'})")

    # Vendor not mapped = exception. Log it, leave file in place, stop here.
    if not was_mapped:
        log_exception(
            file_name=file_name,
            client_name=client_name,
            location=location,
            drive_url=drive_url,
            invoice_data=invoice_data,
            exception_reasons=[
                f"Vendor '{invoice_data['vendor_name']}' not in Vendor Mapping sheet.",
                f"Suggested account: {account_code} ({account_name}).",
                "Add vendor to the Vendor Mapping tab, then reprocess.",
            ],
        )
        is_new = not has_vendor_mappings(client_name)
        status = "new_client" if is_new else "exception"
        return {"status": status, "reason": "vendor_not_mapped", "client": client_name, "vendor": invoice_data.get("vendor_name")}

    # All clear â€” post to Xero, attach PDF, move to Posted
    invoice_data["_file_bytes"] = file_bytes
    invoice_data["_file_name"] = file_name
    invoice_data["_mime_type"] = mime_type
    xero_bill = create_bill(invoice_data, client_name, drive_url, location=location)
    print(f"Posted to Xero: {xero_bill.get('InvoiceID')}")

    move_to_posted(file_id)

    return {
        "status": "posted",
        "client": client_name,
        "location": location,
        "vendor": invoice_data.get("vendor_name"),
        "total": invoice_data.get("total_amount"),
        "xero_id": xero_bill.get("InvoiceID"),
    }


@app.get("/health")
def health():
    return {"status": "ok"}


