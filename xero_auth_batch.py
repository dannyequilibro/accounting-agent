"""
Run this to connect all your client Xero organisations.
Each loop opens a browser — select one org, click Allow, then repeat for the next.

Usage:  python xero_auth_batch.py
"""
import os
import json
import time
import webbrowser
import secrets
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("XERO_CLIENT_ID")
CLIENT_SECRET = os.getenv("XERO_CLIENT_SECRET")
REDIRECT_URI = os.getenv("XERO_REDIRECT_URI", "http://localhost:8080/callback")
SCOPES = "accounting.invoices accounting.contacts accounting.settings.read accounting.attachments offline_access"
TOKEN_FILE = "xero_tokens.json"

auth_code = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        auth_code = params.get("code", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<h2>Auth complete! You can close this tab.</h2>")

    def log_message(self, format, *args):
        pass


def load_tokens() -> dict:
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            return json.load(f)
    return {"tenants": []}


def save_tokens(tokens: dict):
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)


def do_auth_flow() -> tuple[str, str, float]:
    global auth_code
    auth_code = None

    state = secrets.token_urlsafe(16)
    auth_url = (
        "https://login.xero.com/identity/connect/authorize?"
        + urllib.parse.urlencode({
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "state": state,
        })
    )

    webbrowser.open(auth_url)
    server = HTTPServer(("localhost", 8080), CallbackHandler)
    server.handle_request()

    if not auth_code:
        return None, None, None

    resp = requests.post(
        "https://identity.xero.com/connect/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"], data["refresh_token"], time.time() + data["expires_in"] - 60


def main():
    tokens = load_tokens()
    existing_tenants = {t["tenantId"]: t for t in tokens.get("tenants", [])}

    print("=" * 55)
    print("Xero Batch Organisation Setup")
    print("=" * 55)
    print(f"Already connected: {len(existing_tenants)} org(s)")
    for t in existing_tenants.values():
        print(f"  ✓ {t['tenantName']}")
    print()

    while True:
        answer = input("Connect another organisation? (y/n): ").strip().lower()
        if answer != "y":
            break

        print("\nOpening Xero in browser — select ONE organisation and click Allow...")
        access_token, refresh_token, expires_at = do_auth_flow()

        if not access_token:
            print("Auth failed or cancelled. Try again.")
            continue

        # Fetch all connections this token can see
        connections = requests.get(
            "https://api.xero.com/connections",
            headers={"Authorization": f"Bearer {access_token}"},
        ).json()

        newly_added = []
        for c in connections:
            if c.get("tenantType") != "ORGANISATION":
                continue
            tid = c["tenantId"]
            if tid not in existing_tenants:
                existing_tenants[tid] = {"tenantId": tid, "tenantName": c["tenantName"]}
                newly_added.append(c["tenantName"])

        if newly_added:
            print(f"  Added: {', '.join(newly_added)}")
        else:
            print("  No new orgs added (already connected or same org selected).")

        # Always update to the latest refresh token
        tokens["access_token"] = access_token
        tokens["refresh_token"] = refresh_token
        tokens["expires_at"] = expires_at
        tokens["tenants"] = list(existing_tenants.values())
        save_tokens(tokens)

    print("\n" + "=" * 55)
    print(f"Done. {len(existing_tenants)} organisation(s) connected:")
    for t in existing_tenants.values():
        print(f"  ✓ {t['tenantName']}")
    print("\nMake sure your Google Drive client folder names match these.")
    print("=" * 55)


if __name__ == "__main__":
    main()
