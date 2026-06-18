import os
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io
from dotenv import load_dotenv

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/drive"]

# Folder names that are structural (not client/outlet names)
STRUCTURAL_FOLDERS = {
    "vendor invoices", "accounting and bookkeeping",
    "02. clients", "01. admin", "03. payroll",
}
DATE_PATTERN = re.compile(r"^\d{4}[.\-]\d{2}$")  # e.g. 2026.04 or 2026-04
NUMBER_PREFIX = re.compile(r"^\d+\.\s*")          # e.g. "68. "


def _get_service():
    creds = service_account.Credentials.from_service_account_file(
        os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE"), scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)


def _is_structural(name: str) -> bool:
    return (
        name.lower() in STRUCTURAL_FOLDERS
        or DATE_PATTERN.match(name)
        or name.lower().startswith("shared drives")
    )


def _strip_number_prefix(name: str) -> str:
    return NUMBER_PREFIX.sub("", name).strip()


def get_folder_path(file_id: str) -> list[str]:
    """Returns folder names from root down to (not including) the file itself."""
    service = _get_service()
    path = []

    file = service.files().get(
        fileId=file_id,
        fields="name,parents",
        supportsAllDrives=True,
    ).execute()
    parent_id = file.get("parents", [None])[0]

    while parent_id:
        folder = service.files().get(
            fileId=parent_id,
            fields="name,parents",
            supportsAllDrives=True,
        ).execute()
        path.insert(0, folder["name"])
        parent_id = folder.get("parents", [None])[0]

    return path


def parse_path(folder_path: list[str]) -> dict:
    """
    Extract client_name and location from the folder path.

    Expected structure (segments after '02. Clients'):
      [client_folder] / [optional: outlet/location] / [optional: date] / Vendor invoices

    Returns:
      {
        "client_name": "S Grill Kitchen Pte. Ltd",
        "location": "44 Owen Road (Western)" or None,
        "period": "2026.04" or None,
      }
    """
    # Find the index of the "02. Clients" segment (case-insensitive, strip prefix)
    clients_idx = None
    for i, seg in enumerate(folder_path):
        if re.sub(r"^\d+\.\s*", "", seg).strip().lower() == "clients":
            clients_idx = i
            break

    if clients_idx is None or clients_idx + 1 >= len(folder_path):
        return {"client_name": "Unknown Client", "location": None, "period": None}

    # Segment immediately after "02. Clients" is the client folder
    client_raw = folder_path[clients_idx + 1]
    client_name = _strip_number_prefix(client_raw)

    # Remaining segments (after client, before end)
    remaining = folder_path[clients_idx + 2:]

    location = None
    period = None

    for seg in remaining:
        if _is_structural(seg):
            continue
        if DATE_PATTERN.match(seg):
            period = seg
        else:
            # Non-structural, non-date segment = outlet/location
            location = seg

    return {
        "client_name": client_name,
        "location": location,
        "period": period,
    }


def download_file(file_id: str) -> tuple[bytes, str, str]:
    """Returns (file_bytes, mime_type, file_name)"""
    service = _get_service()
    meta = service.files().get(
        fileId=file_id,
        fields="name,mimeType",
        supportsAllDrives=True,
    ).execute()
    mime_type = meta["mimeType"]
    file_name = meta["name"]

    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    return buf.getvalue(), mime_type, file_name


def move_to_posted(file_id: str):
    """
    Moves the file into a 'Posted' subfolder within its current parent folder.
    Creates the Posted folder if it doesn't exist.
    """
    service = _get_service()

    # Get current parent
    file = service.files().get(
        fileId=file_id,
        fields="name,parents",
        supportsAllDrives=True,
    ).execute()
    current_parent = file["parents"][0]

    # Find or create 'Posted' folder inside current parent
    query = f"name='Posted' and mimeType='application/vnd.google-apps.folder' and '{current_parent}' in parents and trashed=false"
    results = service.files().list(
        q=query,
        fields="files(id)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()

    folders = results.get("files", [])
    if folders:
        posted_folder_id = folders[0]["id"]
    else:
        folder_meta = {
            "name": "Posted",
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [current_parent],
        }
        created = service.files().create(
            body=folder_meta,
            fields="id",
            supportsAllDrives=True,
        ).execute()
        posted_folder_id = created["id"]

    # Move file: add new parent, remove old parent
    service.files().update(
        fileId=file_id,
        addParents=posted_folder_id,
        removeParents=current_parent,
        fields="id,parents",
        supportsAllDrives=True,
    ).execute()
    print(f"Moved {file['name']} to Posted folder")


def get_file_web_url(file_id: str) -> str:
    service = _get_service()
    file = service.files().get(
        fileId=file_id,
        fields="webViewLink",
        supportsAllDrives=True,
    ).execute()
    return file.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")
