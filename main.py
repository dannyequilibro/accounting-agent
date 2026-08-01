import os
import base64
import hmac
import hashlib
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv

from drive_client import download_file, get_folder_path, parse_path, get_file_web_url, move_to_posted
from invoice_extractor import extract_invoice
from xero_client import create_bill, authorise_bill, void_bill, tenant_connected
from email_notifier import log_exception
from vendor_mapping import get_account_code, add_mapping
from sheet_manager import has_vendor_mappings
from run_log import log_run
from digest import send_daily_digest
from agent_core import approvals, policy

load_dotenv()

app = FastAPI(title="Accounting Agent")


def _valid_json_file(path: str) -> bool:
    """True if the file exists and holds parseable, non-empty JSON."""
    try:
        with open(path) as f:
            content = f.read().strip()
        if not content:
            return False
        import json as _json
        _json.loads(content)
        return True
    except Exception:
        return False


@app.on_event("startup")
async def startup_event():
    """Restore secret files from env vars on Railway (ephemeral filesystem).
    Restores whenever the file is missing OR empty/corrupt — an empty file left
    on a persistent volume must not block the restore (that broke Xero auth)."""
    sa_b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_B64")
    sa_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    if sa_b64 and not _valid_json_file(sa_file):
        with open(sa_file, "w") as f:
            f.write(base64.b64decode(sa_b64).decode())
        print(f"Wrote {sa_file} from env var.")

    xero_b64 = os.getenv("XERO_TOKENS_JSON_B64")
    if xero_b64 and not _valid_json_file("xero_tokens.json"):
        with open("xero_tokens.json", "w") as f:
            f.write(base64.b64decode(xero_b64).decode())
        print("Wrote xero_tokens.json from env var (was missing or empty).")
    elif not xero_b64 and not _valid_json_file("xero_tokens.json"):
        print("WARNING: xero_tokens.json missing/empty AND XERO_TOKENS_JSON_B64 not set — Xero auth will fail.")

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

    # 4. Resolve the account code first — the policy needs to know what the agent
    # would book this to before it can decide whether Danny should see it.
    account_code, account_name, was_mapped = get_account_code(
        invoice_data.get("vendor_name") or "",
        invoice_data.get("line_items", []),
        invoice_data.get("vendor_name", ""),
        client_name=client_name,
    )
    invoice_data["_account_code"] = account_code
    invoice_data["_account_name"] = account_name
    is_new_client = not was_mapped and not has_vendor_mappings(client_name)
    print(f"Account: {account_code} {account_name} ({'mapped' if was_mapped else 'suggested'})")

    # 5. Decide who owns this action — see agent_core/policy.py.
    signals = policy.signals_from_invoice(invoice_data, vendor_mapped=was_mapped, new_client=is_new_client)
    decision = policy.decide(signals, client_name=client_name)
    print(f"Policy: {decision.outcome} — {decision.reason}")

    # ESCALATE: the document itself can't be trusted. No Xero object at all.
    if decision.outcome == policy.ESCALATE:
        log_exception(
            file_name=file_name,
            client_name=client_name,
            location=location,
            drive_url=drive_url,
            invoice_data=invoice_data,
            exception_reasons=[decision.reason],
        )
        status = "new_client" if is_new_client else "exception"
        log_run(client_name, location, invoice_data, status, decision.reason, file_name)
        return {"status": status, "reason": decision.reason, "client": client_name}

    # AUTO / DRAFT / APPROVE all do the work. The only difference is whether the
    # result is committed to the ledger now, later, or on a tap.
    invoice_data["_file_bytes"] = file_bytes
    invoice_data["_file_name"] = file_name
    invoice_data["_mime_type"] = mime_type
    xero_status = "AUTHORISED" if decision.outcome == policy.AUTO else "DRAFT"
    xero_bill = create_bill(invoice_data, client_name, drive_url, location=location, status=xero_status)
    invoice_id = xero_bill.get("InvoiceID")
    print(f"Xero {xero_status}: {invoice_id}")

    if decision.outcome == policy.AUTO:
        if decision.learn.get("vendor_mapping"):
            m = decision.learn["vendor_mapping"]
            add_mapping(client_name, m["vendor"], m["code"], m["name"], note="Auto-mapped (immaterial)")
        move_to_posted(file_id)
        log_run(client_name, location, invoice_data, "posted", f"Xero {invoice_id}", file_name)
        return {"status": "posted", "client": client_name, "xero_id": invoice_id}

    if decision.outcome == policy.DRAFT:
        # Left in Drive on purpose: an un-authorised draft is unfinished work, and
        # the folder should show that. create_bill()'s InvoiceNumber check makes a
        # re-scan idempotent.
        log_run(client_name, location, invoice_data, "draft",
                f"{decision.reason} Xero {invoice_id}", file_name)
        return {"status": "draft", "client": client_name, "xero_id": invoice_id}

    approval_id = approvals.request_approval(
        kind="bill",
        client=client_name,
        summary=f"{invoice_data.get('vendor_name')} {invoice_data.get('currency', 'SGD')} {invoice_data.get('total_amount')}",
        question=f"{decision.ask}\n\n{decision.reason}\n{drive_url}",
        payload={
            "client_name": client_name,
            "location": location,
            "file_id": file_id,
            "file_name": file_name,
            "drive_url": drive_url,
            "invoice_id": invoice_id,
            "invoice_data": {k: v for k, v in invoice_data.items() if not k.startswith("_")},
            "account_code": account_code,
            "account_name": account_name,
            "learn": decision.learn,
            "reason": decision.reason,
        },
    )
    log_run(client_name, location, invoice_data, "awaiting_approval",
            f"{decision.reason} approval {approval_id}", file_name)
    return {"status": "awaiting_approval", "client": client_name,
            "approval_id": approval_id, "xero_id": invoice_id}


def _resolve_bill(action: str, payload: dict) -> str:
    """Finish a staged bill once Danny has answered. Registered with the approval
    loop at import time; see agent_core/approvals.py for the idempotency guard."""
    client_name = payload["client_name"]
    invoice_id = payload.get("invoice_id")
    invoice_data = payload.get("invoice_data", {})

    if action == "ok":
        if invoice_id:
            authorise_bill(invoice_id, client_name)
        # Teach the mapping so this vendor never generates another approval.
        m = (payload.get("learn") or {}).get("vendor_mapping")
        if m:
            add_mapping(client_name, m["vendor"], m["code"], m["name"])
        if payload.get("file_id"):
            move_to_posted(payload["file_id"])
        log_run(client_name, payload.get("location"), invoice_data, "posted",
                f"Approved — Xero {invoice_id}", payload.get("file_name", ""))
        return f"authorised {invoice_id}"

    if action == "draft":
        log_run(client_name, payload.get("location"), invoice_data, "draft",
                f"Left as draft on request — Xero {invoice_id}", payload.get("file_name", ""))
        return f"left as draft {invoice_id}"

    # Rejected: remove the draft so the ledger has no trace, and put it in front
    # of a human with the full document.
    if invoice_id:
        try:
            void_bill(invoice_id, client_name)
        except Exception as e:
            print(f"[resolve_bill] could not delete draft {invoice_id}: {e}")
    log_exception(
        file_name=payload.get("file_name", "—"),
        client_name=client_name,
        location=payload.get("location"),
        drive_url=payload.get("drive_url", ""),
        invoice_data={**invoice_data, "_account_code": payload.get("account_code")},
        exception_reasons=["Rejected on approval — draft deleted.", payload.get("reason", "")],
    )
    log_run(client_name, payload.get("location"), invoice_data, "exception",
            "Rejected on approval", payload.get("file_name", ""))
    return "draft deleted, sent to exceptions"


approvals.register_resolver("bill", _resolve_bill)


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


@app.post("/telegram/callback")
async def telegram_callback(request: Request):
    """Telegram calls this when Danny taps a button on an approval.

    Guarded by Telegram's own secret_token header rather than the shared webhook
    secret — Telegram can't put a body field in, and without it anyone who
    guesses this URL could authorise bills.
    """
    expected = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not expected or not hmac.compare_digest(got, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return approvals.handle_callback(await request.json())


@app.post("/approvals/pending")
async def approvals_pending(request: Request):
    """Open approvals, for the digest and for checking state after a redeploy."""
    body = await request.json()
    if not verify_webhook_secret(body.get("secret", "")):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"pending": approvals.pending()}


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
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")


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


