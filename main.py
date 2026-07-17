import os
import base64
import hmac
import hashlib
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv

from drive_client import download_file, get_folder_path, parse_path, get_file_web_url, move_to_posted
from invoice_extractor import extract_invoice, is_exception
from xero_client import create_bill, tenant_connected
from email_notifier import log_exception
from vendor_mapping import get_account_code
from sheet_manager import has_vendor_mappings
from run_log import log_run
from digest import send_daily_digest

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
        log_run("—", "—", {}, "skipped", f"unsupported type {mime_type}", file_name)
        return {"status": "skipped", "reason": f"unsupported type {mime_type}"}

    # 2. Parse client name and location from folder path
    folder_path = get_folder_path(file_id)

    # Skip files already in the Posted folder
    if "Posted" in folder_path:
        print(f"Skipping â€” file is already in Posted folder")
        return {"status": "skipped", "reason": "already in Posted folder"}
        # (intentionally not logged — avoids flooding the digest with re-scans)

    path_info = parse_path(folder_path)
    client_name = path_info["client_name"]
    location = path_info["location"]
    drive_url = get_file_web_url(file_id)

    print(f"Client: {client_name} | Location: {location} | File: {file_name}")

    # Guard: if this client has no connected Xero org, flag it — don't post to
    # the wrong company and don't waste an extraction. Surfaces in the digest.
    if not tenant_connected(client_name):
        reason = f"Client '{client_name}' has no connected Xero org — authorize it in Xero or fix the folder name."
        print(f"Exception â€” {reason}")
        log_exception(
            file_name=file_name,
            client_name=client_name,
            location=location,
            drive_url=drive_url,
            invoice_data={},
            exception_reasons=[reason],
        )
        log_run(client_name, location, {}, "org_not_connected", reason, file_name)
        return {"status": "org_not_connected", "client": client_name}

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
        log_run(client_name, location, invoice_data, "exception", "; ".join(reasons), file_name)
        return {"status": "exception", "reasons": reasons, "client": client_name}

    # 5. Post to Xero directly
    if not invoice_data.get("vendor_name"):
        log_run(client_name, location, invoice_data, "error", "No vendor name extracted", file_name)
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
        log_run(client_name, location, invoice_data, status,
                f"Vendor '{invoice_data.get('vendor_name')}' not mapped", file_name)
        return {"status": status, "reason": "vendor_not_mapped", "client": client_name, "vendor": invoice_data.get("vendor_name")}

    # All clear â€” post to Xero, attach PDF, move to Posted
    invoice_data["_file_bytes"] = file_bytes
    invoice_data["_file_name"] = file_name
    invoice_data["_mime_type"] = mime_type
    xero_bill = create_bill(invoice_data, client_name, drive_url, location=location)
    print(f"Posted to Xero: {xero_bill.get('InvoiceID')}")

    move_to_posted(file_id)

    log_run(client_name, location, invoice_data, "posted",
            f"Xero {xero_bill.get('InvoiceID')}", file_name)

    return {
        "status": "posted",
        "client": client_name,
        "location": location,
        "vendor": invoice_data.get("vendor_name"),
        "total": invoice_data.get("total_amount"),
        "xero_id": xero_bill.get("InvoiceID"),
    }


@app.post("/digest/daily")
async def digest_daily(request: Request):
    """Called once a day by an Apps Script time-trigger. Reads the last 24h of
    the Run Log and sends Danny one Telegram status message."""
    body = await request.json()
    if not verify_webhook_secret(body.get("secret", "")):
        raise HTTPException(status_code=401, detail="Unauthorized")
    hours = int(body.get("hours", 24))
    text = send_daily_digest(hours=hours)
    print(f"Digest sent:\n{text}")
    return {"status": "sent", "text": text}


@app.post("/rotate/status")
async def rotate_status(request: Request):
    """Rotation status: connected orgs + which target clients are waiting for a
    slot. Runs on Railway so only Railway touches the Xero token."""
    body = await request.json()
    if not verify_webhook_secret(body.get("secret", "")):
        raise HTTPException(status_code=401, detail="Unauthorized")
    from rotation import build_status
    try:
        return build_status()
    except Exception as e:
        import traceback
        return {"error": type(e).__name__, "detail": str(e), "trace": traceback.format_exc()[-1500:]}


@app.post("/rotate/disconnect")
async def rotate_disconnect(request: Request):
    """Disconnect one org to free a slot. Runs on Railway (single token owner)."""
    body = await request.json()
    if not verify_webhook_secret(body.get("secret", "")):
        raise HTTPException(status_code=401, detail="Unauthorized")
    org = body.get("org", "").strip()
    if not org:
        raise HTTPException(status_code=400, detail="Missing 'org'")
    from rotation import do_disconnect
    try:
        return do_disconnect(org)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/health")
def health():
    return {"status": "ok"}


