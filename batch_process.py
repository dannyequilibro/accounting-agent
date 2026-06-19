"""
Batch processes all unposted invoices across all client Vendor invoices folders.
Skips files already in 'Posted' subfolders.

Usage:
  python batch_process.py              # dry run (list files only)
  python batch_process.py --process    # actually process and post
  python batch_process.py --process --limit 10   # process first 10 only
"""
import sys
import time
import argparse
import requests
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

from drive_client import download_file, get_file_web_url, move_to_posted, SCOPES
from invoice_extractor import extract_invoice, is_exception
from xero_client import create_bill
from email_notifier import log_exception
from vendor_mapping import get_account_code

load_dotenv()

SUPPORTED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/tiff", "image/webp"}
CLIENTS_FOLDER_ID = "1jVBFeOMXbS3X8DvLdX2afh2Ult_TRT83"

# Strip leading number prefix like "68. " from client folder names
import re
NUMBER_PREFIX = re.compile(r"^\d+\.\s*")


def _get_service():
    import os
    creds = service_account.Credentials.from_service_account_file(
        os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE"), scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)


def _list_subfolders(service, parent_id: str) -> list[dict]:
    """List all direct subfolders of a given folder."""
    results = []
    page_token = None
    while True:
        resp = service.files().list(
            q=f"'{parent_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
            fields="nextPageToken, files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageSize=100,
            **({"pageToken": page_token} if page_token else {}),
        ).execute()
        results.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results


def find_vendor_invoice_folders(client_filter: str = "") -> list[dict]:
    """
    Walk: Clients root → client folders → outlet folders → 'Vendor invoices' folders.
    Returns list of dicts with id, client_name, location.

    client_filter: optional substring to match against client name (case-insensitive).
    """
    service = _get_service()
    results = []

    client_folders = _list_subfolders(service, CLIENTS_FOLDER_ID)
    for client_folder in client_folders:
        raw_name = client_folder["name"]
        client_name = NUMBER_PREFIX.sub("", raw_name).strip()

        if client_filter and client_filter.lower() not in client_name.lower():
            continue

        outlet_folders = _list_subfolders(service, client_folder["id"])
        for outlet in outlet_folders:
            location = outlet["name"]
            vi_resp = service.files().list(
                q=f"'{outlet['id']}' in parents and name = 'Vendor invoices' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                fields="files(id, name)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            for vi in vi_resp.get("files", []):
                results.append({
                    "id": vi["id"],
                    "client_name": client_name,
                    "location": location,
                })

    return results


def find_files_in_folder(service, folder_id: str) -> list[dict]:
    """List non-folder, non-trashed files directly inside a folder, excluding Posted subfolder."""
    files = []
    page_token = None

    # First check if there's a Posted subfolder and get its ID to exclude
    posted_resp = service.files().list(
        q=f"name = 'Posted' and mimeType = 'application/vnd.google-apps.folder' and '{folder_id}' in parents and trashed = false",
        fields="files(id)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    posted_ids = {f["id"] for f in posted_resp.get("files", [])}

    while True:
        params = {
            "q": f"'{folder_id}' in parents and mimeType != 'application/vnd.google-apps.folder' and trashed = false",
            "fields": "nextPageToken, files(id, name, mimeType, parents)",
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
            "pageSize": 100,
        }
        if page_token:
            params["pageToken"] = page_token
        resp = service.files().list(**params).execute()
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return files



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--process", action="store_true", help="Actually process invoices")
    parser.add_argument("--limit", type=int, default=0, help="Max invoices to process")
    parser.add_argument("--client", type=str, default="", help="Filter by client name substring (e.g. 'S Grill')")
    args = parser.parse_args()

    service = _get_service()
    print("Scanning Drive for unposted invoices...\n")

    vendor_folders = find_vendor_invoice_folders(client_filter=args.client)
    print(f"Found {len(vendor_folders)} 'Vendor invoices' folder(s)\n")

    to_process = []
    for folder in vendor_folders:
        files = find_files_in_folder(service, folder["id"])
        for f in files:
            if f.get("mimeType") not in SUPPORTED_MIME_TYPES:
                continue
            to_process.append({
                "id": f["id"],
                "name": f["name"],
                "mime_type": f["mimeType"],
                "client": folder["client_name"],
                "location": folder["location"],
            })

    print(f"Unposted invoices to process: {len(to_process)}\n")

    for i, inv in enumerate(to_process):
        print(f"[{i+1}/{len(to_process)}] {inv['client']} | {inv['location']} | {inv['name']}")

    if not args.process:
        print("\nDry run — add --process to actually post these to Xero.")
        return

    limit = args.limit or len(to_process)
    processed = 0
    errors = 0

    for inv in to_process[:limit]:
        print(f"\n→ Processing: {inv['client']} | {inv['name']}")
        try:
            file_bytes, mime_type, file_name = download_file(inv["id"])
            drive_url = get_file_web_url(inv["id"])

            invoice_data = extract_invoice(file_bytes, mime_type)
            print(f"  Extracted: vendor={invoice_data.get('vendor_name')}, total={invoice_data.get('total_amount')}, confidence={invoice_data.get('confidence')}")

            needs_review, reasons = is_exception(invoice_data)
            if needs_review:
                log_exception(
                    file_name=file_name,
                    client_name=inv["client"],
                    location=inv["location"],
                    drive_url=drive_url,
                    invoice_data=invoice_data,
                    exception_reasons=reasons,
                )
                print(f"  ⚠ Exception logged: {reasons}")
                continue

            if not invoice_data.get("vendor_name"):
                print(f"  ✗ No vendor name — skipping")
                errors += 1
                continue

            account_code, account_name, was_mapped = get_account_code(
                invoice_data["vendor_name"],
                invoice_data.get("line_items", []),
                client_name=inv["client"],
            )
            invoice_data["_account_code"] = account_code
            invoice_data["_account_name"] = account_name
            invoice_data["_file_bytes"] = file_bytes
            invoice_data["_file_name"] = file_name
            invoice_data["_mime_type"] = mime_type

            if not was_mapped:
                log_exception(
                    file_name=file_name,
                    client_name=inv["client"],
                    location=inv["location"],
                    drive_url=drive_url,
                    invoice_data=invoice_data,
                    exception_reasons=[
                        f"Vendor '{invoice_data['vendor_name']}' not in Vendor Mapping sheet.",
                        f"Suggested account: {account_code} ({account_name}).",
                        "Add vendor to the Vendor Mapping tab, then reprocess.",
                    ],
                )
                print(f"  ⚠ Vendor not mapped — logged to exception sheet, file stays in place")
                errors += 1
                continue

            xero_bill = create_bill(invoice_data, inv["client"], drive_url, location=inv["location"])
            move_to_posted(inv["id"])
            print(f"  ✓ Posted to Xero: {xero_bill.get('InvoiceID')}")
            processed += 1

        except Exception as e:
            print(f"  ✗ Error: {e}")
            errors += 1

        time.sleep(2)  # stay well under Xero's 60 calls/min rate limit

    print(f"\n{'='*50}")
    print(f"Done. Posted: {processed} | Errors/Exceptions: {errors}")


if __name__ == "__main__":
    main()
