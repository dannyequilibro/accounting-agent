"""
Daily status digest. Reads the last 24h of the Run Log and sends Danny one
Telegram message via the same bot used for approval pings.

Sends even on a clean night ("all good, N posted") — Danny wants status, not
silence. Telegram creds come from env vars:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import os
import requests
from collections import Counter, defaultdict
from dotenv import load_dotenv

from run_log import read_since

load_dotenv()

# Statuses that mean "landed in Xero" vs "needs a human".
POSTED = {"posted"}
EXCEPTION_STATUSES = {"exception", "new_client", "error", "org_not_connected"}


def build_digest(hours=24):
    rows = read_since(hours=hours)

    if not rows:
        return "🌙 Accounting agent: no invoices processed in the last 24h. Agent is up."

    status_counts = Counter(r.get("Status", "").strip() for r in rows)
    posted_rows = [r for r in rows if r.get("Status", "").strip() in POSTED]
    exception_rows = [r for r in rows if r.get("Status", "").strip() in EXCEPTION_STATUSES]
    skipped = sum(v for k, v in status_counts.items() if k in {"skipped"})

    # Posted, grouped by client
    per_client = Counter(r.get("Client", "—") for r in posted_rows)
    client_bits = ", ".join(f"{c} {n}" for c, n in per_client.most_common())

    lines = []
    lines.append(f"📊 Accounting agent — last {hours}h")
    lines.append(f"✅ {len(posted_rows)} posted" + (f" ({client_bits})" if client_bits else ""))

    if exception_rows:
        lines.append(f"⚠️ {len(exception_rows)} need review:")
        for r in exception_rows[:15]:
            client = r.get("Client", "—")
            reason = r.get("Reason", "—")
            vendor = r.get("Vendor", "—")
            lines.append(f"   • {client} — {vendor}: {reason}")
        if len(exception_rows) > 15:
            lines.append(f"   …and {len(exception_rows) - 15} more (see Run Log sheet)")
    else:
        lines.append("⚠️ 0 exceptions — all clear.")

    if skipped:
        lines.append(f"↩️ {skipped} skipped (already posted / unsupported type)")

    lines.append("Agent is up.")
    return "\n".join(lines)


def send_telegram(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def send_daily_digest(hours=24):
    text = build_digest(hours=hours)
    send_telegram(text)
    return text


if __name__ == "__main__":
    print(send_daily_digest())
