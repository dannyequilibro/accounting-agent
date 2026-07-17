"""
Rotation helper for the 5-org Xero connection cap.

The app can only hold 5 connected orgs at once, but there are more clients than
that. They bill at different times, so we swap orgs in and out.

  python rotate.py status                 # who's connected, who's waiting for a slot
  python rotate.py disconnect "<org>"     # free a slot (instant, no browser)
  python rotate.py connect                # open Xero consent to add a client (browser)
  python rotate.py railway-token          # base64 to paste into Railway after a connect

IMPORTANT — why status/disconnect go through Railway, not your laptop:
Xero rotates the refresh token on every use. If your laptop and Railway both
refresh the same token they invalidate each other and the agent's auth silently
breaks. So `status` and `disconnect` call the LIVE Railway app (the single token
owner). Only `connect` runs locally — it mints a FRESH token via browser consent
that you then push to Railway, so there's still only ever one owner.

Needs two env vars (already in .env): RAILWAY_URL and WEBHOOK_SECRET.
"""
import os
import sys
import base64
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import requests
from dotenv import load_dotenv

load_dotenv()

RAILWAY_URL = os.getenv("RAILWAY_URL", "https://web-production-4ed8d.up.railway.app").rstrip("/")
SECRET = os.getenv("WEBHOOK_SECRET", "")


def _post(path, extra=None):
    payload = {"secret": SECRET}
    if extra:
        payload.update(extra)
    resp = requests.post(f"{RAILWAY_URL}{path}", json=payload, timeout=40)
    if resp.status_code == 401:
        print("Unauthorized — WEBHOOK_SECRET in .env must match Railway's. Aborting.")
        sys.exit(1)
    resp.raise_for_status()
    return resp.json()


def cmd_status():
    s = _post("/rotate/status")
    print(f"Connected now: {s['slots_used']}/{s['max_slots']}")
    for n in s["connected"]:
        print(f"   • {n}")

    print("\nTarget clients:")
    for t in s["targets"]:
        mark = "connected " if t["connected"] else "NOT conn. "
        print(f"   [{mark}] {t['client']}")

    waiting = s.get("waiting", {})
    if waiting:
        print("\n⚠️  Waiting for a slot (invoices rejected, last 7 days):")
        for c, n in sorted(waiting.items(), key=lambda x: -x[1]):
            print(f"   • {c} — {n} invoice(s) waiting")
        free = s["max_slots"] - s["slots_used"]
        if free <= 0:
            print(f"\n   All {s['max_slots']} slots used — disconnect an idle org first:")
            print('     python rotate.py disconnect "<org name>"')
        print("   then bring the client in:")
        print("     python rotate.py connect")
    else:
        print("\n✅ No clients waiting for a slot.")


def cmd_disconnect(name):
    r = _post("/rotate/disconnect", {"org": name})
    print(f"✅ Disconnected: {r['disconnected']}")
    print(f"Slots now: {r['slots_used']}/{r['max_slots']} used.")
    print("Token unchanged — no Railway push needed for a disconnect.")


def cmd_connect():
    print("Opening Xero consent in your browser.")
    print("Tick the client you want to rotate IN (already-connected orgs stay connected).")
    print("If all slots are full, disconnect one first:  python rotate.py disconnect \"<org>\"\n")
    import xero_auth
    xero_auth.main()
    print("\nNow push the new token to Railway and restart the service:")
    print("   python rotate.py railway-token")


def cmd_railway_token():
    with open("xero_tokens.json", "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    print("Set this as Railway env var XERO_TOKENS_JSON_B64, then restart the service:\n")
    print(b64)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "status":
        cmd_status()
    elif cmd == "disconnect":
        if len(sys.argv) < 3:
            print('Usage: python rotate.py disconnect "<org name>"')
            return
        cmd_disconnect(sys.argv[2])
    elif cmd == "connect":
        cmd_connect()
    elif cmd == "railway-token":
        cmd_railway_token()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
