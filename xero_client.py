import os
import sys
import json
import time
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

TOKEN_FILE = "xero_tokens.json"
XERO_TOKEN_URL = "https://identity.xero.com/connect/token"
XERO_API_BASE = "https://api.xero.com/api.xro/2.0"


class XeroDailyLimitError(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f"Xero daily limit hit — resets in {retry_after//3600}h {(retry_after%3600)//60}m")


class TenantNotConnectedError(Exception):
    """Raised when a client's Drive folder has no matching connected Xero org.
    Prevents silently posting an invoice to the wrong company."""
    def __init__(self, client_name: str, available: list[str]):
        self.client_name = client_name
        self.available = available
        super().__init__(
            f"No Xero org connected for '{client_name}'. "
            f"Connected orgs: {available}. Authorize this org in Xero, or fix the folder name."
        )


def _xero_request(method: str, url: str, **kwargs) -> requests.Response:
    """Wrapper that retries on 429 using Retry-After header, up to 3 retries.
    Raises XeroDailyLimitError if Retry-After exceeds 5 minutes."""
    for attempt in range(3):
        resp = requests.request(method, url, **kwargs)
        if resp.status_code != 429:
            return resp
        retry_after = int(resp.headers.get("Retry-After", 60))
        if retry_after > 300:
            raise XeroDailyLimitError(retry_after)
        print(f"Xero rate limit hit — waiting {retry_after}s before retry (attempt {attempt + 1}/3)...")
        time.sleep(retry_after)
    return resp


def _load_tokens() -> dict:
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            return json.load(f)
    return {"refresh_token": os.getenv("XERO_REFRESH_TOKEN", ""), "access_token": "", "expires_at": 0, "tenants": []}


def _save_tokens(tokens: dict):
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)


def _refresh_access_token(tokens: dict) -> dict:
    resp = requests.post(
        XERO_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": os.getenv("XERO_CLIENT_ID"),
            "client_secret": os.getenv("XERO_CLIENT_SECRET"),
        },
    )
    resp.raise_for_status()
    data = resp.json()
    tokens["access_token"] = data["access_token"]
    tokens["refresh_token"] = data["refresh_token"]
    tokens["expires_at"] = time.time() + data["expires_in"] - 60
    _save_tokens(tokens)
    return tokens


def _get_access_token() -> str:
    tokens = _load_tokens()
    if not tokens.get("access_token") or time.time() > tokens.get("expires_at", 0):
        tokens = _refresh_access_token(tokens)
    return tokens["access_token"]


_tenants_cache = {"at": 0, "tenants": None}
_TENANTS_TTL = 300  # seconds — refresh the connected-org list every 5 min


def _fetch_live_connections() -> list[dict]:
    """Ask Xero which orgs are connected RIGHT NOW. Source of truth for rotation."""
    access_token = _get_access_token()
    connections = requests.get(
        "https://api.xero.com/connections",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    ).json()
    return [
        {"tenantId": c["tenantId"], "tenantName": c["tenantName"]}
        for c in connections
        if c.get("tenantType") == "ORGANISATION"
    ]


def _get_tenants(force_refresh: bool = False) -> list[dict]:
    """Currently-connected orgs. With rotation the set changes over time, so we
    query Xero live (cached briefly) instead of trusting the static token file.
    Falls back to the token file's tenant list if Xero can't be reached."""
    now = time.time()
    if (not force_refresh
            and _tenants_cache["tenants"] is not None
            and now - _tenants_cache["at"] < _TENANTS_TTL):
        return _tenants_cache["tenants"]

    try:
        tenants = _fetch_live_connections()
        _tenants_cache["tenants"] = tenants
        _tenants_cache["at"] = now
        # Keep the token file's tenant list in sync for offline reference.
        tokens = _load_tokens()
        tokens["tenants"] = tenants
        _save_tokens(tokens)
        return tenants
    except Exception as e:
        print(f"[xero] Could not fetch live connections ({e}); using token file list.")
        return _load_tokens().get("tenants", [])


def _find_tenant_id(client_name: str) -> str:
    """
    Match client_name (from Drive folder) to a Xero organisation name.
    Uses case-insensitive substring matching, falls back to first tenant.
    """
    tenants = _get_tenants()
    if not tenants:
        raise ValueError("No Xero organisations found. Run xero_auth.py first.")

    name_lower = client_name.lower().strip()

    for t in tenants:
        if t["tenantName"].lower().strip() == name_lower:
            return t["tenantId"]

    for t in tenants:
        xero_name = t["tenantName"].lower().strip()
        if name_lower in xero_name or xero_name in name_lower:
            return t["tenantId"]

    # No match — do NOT default to the first org (that silently posts to the
    # wrong company). Raise so the caller can flag it for review.
    raise TenantNotConnectedError(client_name, [t["tenantName"] for t in tenants])


def tenant_connected(client_name: str) -> bool:
    """True if this client maps to a connected Xero org. Cheap pre-check so we
    don't waste an extraction on an invoice we can't post."""
    try:
        _find_tenant_id(client_name)
        return True
    except TenantNotConnectedError:
        return False


def _get_headers(client_name: str) -> dict:
    access_token = _get_access_token()
    tenant_id = _find_tenant_id(client_name)
    return {
        "Authorization": f"Bearer {access_token}",
        "Xero-tenant-id": tenant_id,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def list_organisations() -> list[dict]:
    """Utility — returns all connected Xero orgs. Useful for verifying setup."""
    return _get_tenants(force_refresh=True)


# ---------------------------------------------------------------------------
# Rotation support: with a 5-org connection cap, we swap orgs in and out.
# Disconnecting is a plain API call (frees a slot, no browser, no token change).
# Connecting a new org needs Xero consent (browser) — see rotate.py / xero_auth.py.
# ---------------------------------------------------------------------------

def get_connections_detail() -> list[dict]:
    """Full connection records incl. the connection 'id' needed to disconnect."""
    access_token = _get_access_token()
    resp = requests.get(
        "https://api.xero.com/connections",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    resp.raise_for_status()
    return [c for c in resp.json() if c.get("tenantType") == "ORGANISATION"]


def disconnect_org(name_or_tenant: str) -> dict:
    """Disconnect one org by name (or tenantId), freeing a connection slot.
    Returns the disconnected org's record. Raises if no match."""
    target = name_or_tenant.lower().strip()
    conns = get_connections_detail()

    match = None
    for c in conns:
        if c.get("tenantId", "").lower() == target:
            match = c
            break
    if not match:  # fall back to name match (exact, then substring)
        for c in conns:
            if c.get("tenantName", "").lower().strip() == target:
                match = c
                break
    if not match:
        for c in conns:
            n = c.get("tenantName", "").lower().strip()
            if target in n or n in target:
                match = c
                break
    if not match:
        raise ValueError(
            f"No connected org matches '{name_or_tenant}'. "
            f"Connected: {[c['tenantName'] for c in conns]}"
        )

    access_token = _get_access_token()
    resp = requests.delete(
        f"https://api.xero.com/connections/{match['id']}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    resp.raise_for_status()
    _get_tenants(force_refresh=True)  # refresh cache + token file
    return match


_contact_cache: dict[str, str] = {}  # "client_name|vendor_name" -> ContactID


def find_or_create_contact(vendor_name: str, client_name: str) -> str:
    cache_key = f"{client_name}|{vendor_name}"
    if cache_key in _contact_cache:
        return _contact_cache[cache_key]

    headers = _get_headers(client_name)

    resp = _xero_request(
        "GET",
        f"{XERO_API_BASE}/Contacts",
        headers=headers,
        params={"where": f'Name.Contains("{vendor_name}")'},
    )
    resp.raise_for_status()
    contacts = resp.json().get("Contacts", [])
    if contacts:
        _contact_cache[cache_key] = contacts[0]["ContactID"]
        return _contact_cache[cache_key]

    resp = _xero_request(
        "POST",
        f"{XERO_API_BASE}/Contacts",
        headers=headers,
        json={"Name": vendor_name},
    )
    resp.raise_for_status()
    _contact_cache[cache_key] = resp.json()["Contacts"][0]["ContactID"]
    return _contact_cache[cache_key]


def _get_tracking_category_id(client_name: str, category_name: str = "LOCATION") -> str | None:
    """Fetch the TrackingCategoryID for the given category name in this org."""
    headers = _get_headers(client_name)
    resp = _xero_request("GET", f"{XERO_API_BASE}/TrackingCategories", headers=headers)
    if not resp.ok:
        return None
    for cat in resp.json().get("TrackingCategories", []):
        if cat.get("Name", "").upper() == category_name.upper() and cat.get("Status") == "ACTIVE":
            return cat["TrackingCategoryID"]
    return None


def _get_tracking_option_id(client_name: str, category_id: str, option_name: str) -> str | None:
    """Return the TrackingOptionID for option_name, creating it if it doesn't exist."""
    headers = _get_headers(client_name)
    resp = _xero_request("GET", f"{XERO_API_BASE}/TrackingCategories/{category_id}", headers=headers)
    if not resp.ok:
        return None
    for opt in resp.json().get("TrackingCategories", [{}])[0].get("Options", []):
        if opt.get("Name", "").lower() == option_name.lower() and opt.get("Status") == "ACTIVE":
            return opt["TrackingOptionID"]

    resp = _xero_request(
        "POST",
        f"{XERO_API_BASE}/TrackingCategories/{category_id}/Options",
        headers=headers,
        json={"Name": option_name},
    )
    if resp.ok:
        return resp.json().get("Options", [{}])[0].get("TrackingOptionID")
    return None


def create_bill(
    invoice_data: dict,
    client_name: str,
    drive_file_url: str,
    location: str = None,
    status: str = "AUTHORISED",
) -> dict:
    """Post a bill to Xero.

    `status` is the whole reversibility story. DRAFT is invisible outside the AP
    list and can be deleted without trace; AUTHORISED lands in the client's
    ledger and AP aging, shows up in their reports, and can only be voided —
    which an auditor can see. Anything the agent isn't certain about is created
    DRAFT and authorised later, on a decision, via authorise_bill().
    """
    headers = _get_headers(client_name)
    contact_id = find_or_create_contact(invoice_data["vendor_name"], client_name)

    tracking = []
    if location:
        cat_id = _get_tracking_category_id(client_name, "LOCATION")
        if cat_id:
            opt_id = _get_tracking_option_id(client_name, cat_id, location)
            if opt_id:
                tracking = [{"TrackingCategoryID": cat_id, "TrackingOptionID": opt_id}]

    def make_line_item(description, quantity, unit_amount, account_code):
        item = {
            "Description": description,
            "Quantity": quantity,
            "UnitAmount": unit_amount,
            "AccountCode": account_code,
        }
        if tracking:
            item["Tracking"] = tracking
        return item

    account_code = invoice_data.get("_account_code", "6010-0000")
    account_name = invoice_data.get("_account_name", "PURCHASES")
    total = invoice_data.get("total_amount") or invoice_data.get("subtotal") or 0

    line_items = [make_line_item(account_name, 1, total, account_code)]

    ref_parts = [f"Client: {client_name}"]
    if location:
        ref_parts.append(f"Location: {location}")
    ref_parts.append(f"Source: {drive_file_url}")

    bill_payload = {
        "Type": "ACCPAY",
        "Contact": {"ContactID": contact_id},
        "Date": invoice_data.get("invoice_date") or datetime.today().strftime("%Y-%m-%d"),
        "DueDate": invoice_data.get("due_date") or invoice_data.get("invoice_date") or datetime.today().strftime("%Y-%m-%d"),
        "InvoiceNumber": invoice_data.get("invoice_number", ""),
        "CurrencyCode": invoice_data.get("currency", "SGD"),
        "LineItems": line_items,
        "Status": status,
        "Reference": " | ".join(ref_parts),
    }

    file_bytes = invoice_data.get("_file_bytes")
    file_name = invoice_data.get("_file_name", "invoice.pdf")
    mime_type = invoice_data.get("_mime_type", "application/pdf")

    inv_number = bill_payload.get("InvoiceNumber", "")
    if inv_number:
        check = _xero_request(
            "GET",
            f"{XERO_API_BASE}/Invoices",
            headers=headers,
            params={"where": f'InvoiceNumber="{inv_number}"', "Statuses": "DRAFT,SUBMITTED,AUTHORISED"},
        )
        if check.ok and check.json().get("Invoices"):
            existing = check.json()["Invoices"][0]
            print(f"Duplicate invoice {inv_number} already exists in Xero (ID: {existing['InvoiceID']}) — attaching PDF only.")
            if file_bytes:
                _attach_file(headers, existing["InvoiceID"], file_name, file_bytes, mime_type)
            return existing

        check_voided = _xero_request(
            "GET",
            f"{XERO_API_BASE}/Invoices",
            headers=headers,
            params={"where": f'InvoiceNumber="{inv_number}"', "Statuses": "VOIDED,DELETED"},
        )
        if check_voided.ok and check_voided.json().get("Invoices"):
            bill_payload["InvoiceNumber"] = inv_number + "-R"
            print(f"Voided invoice {inv_number} found — posting as {inv_number}-R")

    xero_payload = {k: v for k, v in bill_payload.items() if not k.startswith("_")}
    resp = _xero_request(
        "POST",
        f"{XERO_API_BASE}/Invoices",
        headers=headers,
        json={"Invoices": [xero_payload]},
    )
    if not resp.ok:
        print(f"Xero error {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    bill = resp.json()["Invoices"][0]

    if bill.get("InvoiceID") and file_bytes:
        _attach_file(headers, bill["InvoiceID"], file_name, file_bytes, mime_type)

    return bill


def authorise_bill(invoice_id: str, client_name: str) -> dict:
    """Promote a DRAFT bill to AUTHORISED. This is the irreversible step, so it
    only ever runs off an explicit decision (an approval tap), never off a
    confidence score."""
    headers = _get_headers(client_name)
    resp = _xero_request(
        "POST",
        f"{XERO_API_BASE}/Invoices/{invoice_id}",
        headers=headers,
        json={"Invoices": [{"InvoiceID": invoice_id, "Status": "AUTHORISED"}]},
    )
    if not resp.ok:
        print(f"Xero authorise error {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    return resp.json()["Invoices"][0]


def void_bill(invoice_id: str, client_name: str) -> dict:
    """Delete a draft the agent shouldn't have created. DELETED is the correct
    status for a draft; VOIDED is for something already authorised."""
    headers = _get_headers(client_name)
    resp = _xero_request(
        "POST",
        f"{XERO_API_BASE}/Invoices/{invoice_id}",
        headers=headers,
        json={"Invoices": [{"InvoiceID": invoice_id, "Status": "DELETED"}]},
    )
    resp.raise_for_status()
    return resp.json()["Invoices"][0]


def _attach_file(headers: dict, invoice_id: str, file_name: str, file_bytes: bytes, mime_type: str):
    attach_headers = {**headers, "Content-Type": mime_type}
    for attempt in range(3):
        resp = _xero_request(
            "POST",
            f"{XERO_API_BASE}/Invoices/{invoice_id}/Attachments/{file_name}",
            headers=attach_headers,
            data=file_bytes,
        )
        if resp.ok:
            print(f"Attachment uploaded to Xero: {file_name}")
            return
        if resp.status_code == 500:
            wait = 10 * (attempt + 1)
            print(f"Attachment upload failed with Xero 500 — retrying in {wait}s...")
            time.sleep(wait)
        else:
            print(f"Attachment upload failed: {resp.status_code} {resp.text}")
            return
    print(f"Attachment upload failed after 3 attempts — bill posted but no PDF attached: {file_name}")
