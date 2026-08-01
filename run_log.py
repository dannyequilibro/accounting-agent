"""
Writes one row per processed file to a single shared "Run Log" Google Sheet,
so the daily digest has a durable record that survives Railway restarts
(Railway's local disk is wiped on redeploy — a Google Sheet is not).

The log sheet id lives in the RUN_LOG_SHEET_ID env var. If it's unset, the
sheet is created on first use and its id is printed once — set that id as the
env var so every restart reuses the same sheet.
"""
import os
import json
import gspread
from datetime import datetime, timezone, timedelta
from google.oauth2 import service_account
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

# Same shared "Agents" folder the client sheets live in, so it's easy to find.
AGENTS_FOLDER_ID = "1cuCuw3R55mIntjsG31szYvLjXW5EwvaT"
NOTIFY_EMAIL = "danny@equilibro.com.sg"
SGT = timezone(timedelta(hours=8))

RUN_LOG_HEADERS = [
    "Timestamp (SGT)", "Client", "Location", "Vendor",
    "Invoice #", "Total", "Currency", "Status", "Reason", "File Name",
]

_cached_sheet = None
_cached_spreadsheet = None


def _gc():
    creds = service_account.Credentials.from_service_account_file(
        os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE"), scopes=SCOPES
    )
    return gspread.authorize(creds)


def get_log_spreadsheet():
    """The Run Log spreadsheet itself, so other tabs (e.g. Approvals) can live
    alongside the log instead of in yet another file Danny has to keep track of."""
    global _cached_spreadsheet
    if _cached_spreadsheet is None:
        _cached_spreadsheet = _get_log_worksheet().spreadsheet
    return _cached_spreadsheet


def _get_log_worksheet():
    """Return the Log worksheet, creating the whole sheet on first use."""
    global _cached_sheet
    if _cached_sheet is not None:
        return _cached_sheet

    gc = _gc()
    sheet_id = os.getenv("RUN_LOG_SHEET_ID", "").strip()

    if sheet_id:
        try:
            _cached_sheet = gc.open_by_key(sheet_id).worksheet("Log")
            return _cached_sheet
        except Exception as e:
            print(f"RUN_LOG_SHEET_ID set but couldn't open it ({e}); creating a new one.")

    # Create the run-log sheet
    name = "[Accounting Agent] Run Log"
    # Reuse if it already exists in Drive (e.g. env var got lost)
    for s in gc.list_spreadsheet_files():
        if s["name"] == name:
            print(f"Found existing Run Log sheet: {s['id']} — set RUN_LOG_SHEET_ID to this.")
            _cached_sheet = gc.open_by_key(s["id"]).worksheet("Log")
            return _cached_sheet

    spreadsheet = gc.create(name, folder_id=AGENTS_FOLDER_ID)
    spreadsheet.share(NOTIFY_EMAIL, perm_type="user", role="writer", notify=False)
    ws = spreadsheet.sheet1
    ws.update_title("Log")
    ws.append_row(RUN_LOG_HEADERS)
    ws.format("A1:J1", {"textFormat": {"bold": True}})
    print(f"Created Run Log sheet: {spreadsheet.id}")
    print(f"IMPORTANT: set RUN_LOG_SHEET_ID={spreadsheet.id} (locally in .env and on Railway).")
    _cached_sheet = ws
    return ws


def log_run(client_name, location, invoice_data, status, reason="", file_name=""):
    """Append one line describing what happened to a single file. Never raises —
    logging must not break the posting pipeline."""
    try:
        ws = _get_log_worksheet()
        row = [
            datetime.now(SGT).strftime("%Y-%m-%d %H:%M"),
            client_name or "—",
            location or "—",
            (invoice_data or {}).get("vendor_name", "—"),
            (invoice_data or {}).get("invoice_number", "—"),
            (invoice_data or {}).get("total_amount", "—"),
            (invoice_data or {}).get("currency", "SGD"),
            status,
            reason or "—",
            file_name or "—",
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")
    except Exception as e:
        # Log failures are non-fatal — the invoice still posted.
        print(f"[run_log] Could not write log row: {e}")


def read_since(hours=24):
    """Return log rows (as dicts) with a timestamp within the last `hours`."""
    ws = _get_log_worksheet()
    records = ws.get_all_records()
    cutoff = datetime.now(SGT) - timedelta(hours=hours)
    recent = []
    for r in records:
        ts = str(r.get("Timestamp (SGT)", "")).strip()
        try:
            when = datetime.strptime(ts, "%Y-%m-%d %H:%M").replace(tzinfo=SGT)
        except ValueError:
            continue
        if when >= cutoff:
            recent.append(r)
    return recent
