"""
Manages one Google Sheet per client.
Sheets are auto-created on first use and stored in client_sheets.json.
Each sheet has two tabs: Exceptions and Vendor Mapping.
"""
import os
import json
import gspread
from google.oauth2 import service_account
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

SHEETS_REGISTRY = "client_sheets.json"
NOTIFY_EMAIL = "danny@equilibro.com.sg"
AGENTS_FOLDER_ID = "1cuCuw3R55mIntjsG31szYvLjXW5EwvaT"

EXCEPTION_HEADERS = [
    "Timestamp", "Client", "Location", "File Name", "Drive Link",
    "Vendor", "Invoice #", "Date", "Total", "Currency",
    "Suggested Account", "Handwritten", "Confidence", "Flags",
]
MAPPING_HEADERS = ["Vendor Name", "Account Code", "Account Name", "Notes"]


def _gc():
    creds = service_account.Credentials.from_service_account_file(
        os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE"), scopes=SCOPES
    )
    return gspread.authorize(creds)


def _load_registry() -> dict:
    if os.path.exists(SHEETS_REGISTRY):
        with open(SHEETS_REGISTRY) as f:
            return json.load(f)
    return {}


def _save_registry(registry: dict):
    with open(SHEETS_REGISTRY, "w") as f:
        json.dump(registry, f, indent=2)


def get_or_create_client_sheet(client_name: str) -> gspread.Spreadsheet:
    registry = _load_registry()
    gc = _gc()

    if client_name in registry:
        try:
            return gc.open_by_key(registry[client_name])
        except Exception:
            pass  # Sheet was deleted — recreate below

    # Create new spreadsheet inside the shared Agents folder
    spreadsheet = gc.create(f"[Accounting Agent] {client_name}", folder_id=AGENTS_FOLDER_ID)

    # Share with Danny so he can view/edit
    spreadsheet.share(NOTIFY_EMAIL, perm_type="user", role="writer", notify=False)

    # Set up Exceptions tab (rename Sheet1)
    exceptions_sheet = spreadsheet.sheet1
    exceptions_sheet.update_title("Exceptions")
    exceptions_sheet.append_row(EXCEPTION_HEADERS)
    exceptions_sheet.format("A1:N1", {"textFormat": {"bold": True}})

    # Set up Vendor Mapping tab
    mapping_sheet = spreadsheet.add_worksheet("Vendor Mapping", rows=500, cols=4)
    mapping_sheet.append_row(MAPPING_HEADERS)
    mapping_sheet.format("A1:D1", {"textFormat": {"bold": True}})

    # Save to registry
    registry[client_name] = spreadsheet.id
    _save_registry(registry)

    print(f"Created sheet for {client_name}: https://docs.google.com/spreadsheets/d/{spreadsheet.id}")
    return spreadsheet


def get_exceptions_sheet(client_name: str):
    return get_or_create_client_sheet(client_name).worksheet("Exceptions")


def get_mapping_sheet(client_name: str):
    return get_or_create_client_sheet(client_name).worksheet("Vendor Mapping")
