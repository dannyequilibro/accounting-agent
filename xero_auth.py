"""
Run this ONCE to get your Xero refresh token.
It opens a browser, you log in to Xero, and the token is saved to xero_tokens.json.

Usage:  python xero_auth.py
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
SCOPES = "accounting.invoices accounting.contacts accounting.settings.read offline_access"
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
        pass  # suppress server logs


def main():
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

    print("Opening Xero login in your browser...")
    webbrowser.open(auth_url)

    server = HTTPServer(("localhost", 8080), CallbackHandler)
    server.handle_request()  # handle one request then stop

    if not auth_code:
        print("No auth code received. Exiting.")
        return

    # Exchange code for tokens
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

    # Fetch all tenants (partner accounts have many)
    connections = requests.get(
        "https://api.xero.com/connections",
        headers={"Authorization": f"Bearer {data['access_token']}"},
    ).json()

    tenants = [
        {"tenantId": c["tenantId"], "tenantName": c["tenantName"]}
        for c in connections
        if c.get("tenantType") == "ORGANISATION"
    ]

    tokens = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": time.time() + data["expires_in"] - 60,
        "tenants": tenants,
    }

    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)

    print(f"\nSuccess! Tokens saved to {TOKEN_FILE}")
    print(f"\nFound {len(tenants)} organisations:")
    for t in tenants:
        print(f"  - {t['tenantName']}  ({t['tenantId']})")
    print("\nThe agent will match these names to your Google Drive client folder names.")
    print("Make sure your Drive folder names match (or closely match) the Xero org names above.")


if __name__ == "__main__":
    main()
