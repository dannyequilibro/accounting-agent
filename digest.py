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
DRAFTED = {"draft"}
EXCEPTION_STATUSES = {"exception", "new_client", "error", "org_not_connected"}


def build_digest(hours=24):
    rows = read_since(hours=hours)

    if not rows:
        quiet = ["🌙 Accounting agent: no invoices processed in the last 24h. Agent is up."]
        # A quiet night still has to report anything blocked on Danny.
        quiet.extend(_approval_lines())
        return "\n".join(quiet)

    status_counts = Counter(r.get("Status", "").strip() for r in rows)
    posted_rows = [r for r in rows if r.get("Status", "").strip() in POSTED]
    exception_rows = [r for r in rows if r.get("Status", "").strip() in EXCEPTION_STATUSES]
    skipped = sum(v for k, v in status_counts.items() if k in {"skipped"})

    # Posted, grouped by client
    per_client = Counter(r.get("Client", "—") for r in posted_rows)
    client_bits = ", ".join(f"{c} {n}" for c, n in per_client.most_common())

    draft_rows = [r for r in rows if r.get("Status", "").strip() in DRAFTED]

    lines = []
    lines.append(f"📊 Accounting agent — last {hours}h")
    lines.append(f"✅ {len(posted_rows)} posted" + (f" ({client_bits})" if client_bits else ""))

    if draft_rows:
        total = _sum_totals(draft_rows)
        lines.append(f"📝 {len(draft_rows)} waiting as Xero drafts{total} — review in Xero, no reply needed here")

    # Approvals sit above exceptions on purpose: an unanswered approval is work
    # the agent has already finished and cannot release.
    lines.extend(_approval_lines())

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

    # Rotation prompt: any client whose invoices bounced because its Xero org
    # isn't currently connected needs a slot.
    not_connected = [r for r in rows if r.get("Status", "").strip() == "org_not_connected"]
    if not_connected:
        clients = {}
        for r in not_connected:
            c = r.get("Client", "—")
            clients[c] = clients.get(c, 0) + 1
        lines.append("🔄 Rotate in (not connected, invoices waiting):")
        for c, n in sorted(clients.items(), key=lambda x: -x[1]):
            lines.append(f"   • {c} — {n} waiting")
        lines.append("   Run: python rotate.py status")

    lines.append("Agent is up.")
    return "\n".join(lines)


def _sum_totals(rows) -> str:
    """' (S$1,234)' if every row has a readable total, else ''. Mixed currencies
    are left unsummed rather than quietly added together."""
    currencies = {str(r.get("Currency", "SGD")).strip().upper() or "SGD" for r in rows}
    if len(currencies) != 1:
        return ""
    total = 0.0
    for r in rows:
        try:
            total += float(str(r.get("Total", "")).replace(",", ""))
        except ValueError:
            return ""
    return f" ({currencies.pop()} {total:,.2f})"


def _approval_lines() -> list:
    """Open approvals, loudest when they've been sitting the longest."""
    try:
        from agent_core.approvals import pending
        open_items = pending()
    except Exception as e:
        return [f"⚠️ Could not read the approvals queue: {e}"]

    if not open_items:
        return []

    stale = [r for r in open_items if _age_hours(r.get("Created (SGT)")) >= 24]
    out = [f"👉 {len(open_items)} awaiting your tap"
           + (f" — {len(stale)} over 24h old" if stale else "") + ":"]
    for r in open_items[:10]:
        age = _age_hours(r.get("Created (SGT)"))
        age_txt = f" [{int(age)}h]" if age else ""
        out.append(f"   • {r.get('Client', '—')} — {r.get('Summary', '—')}{age_txt}")
    if len(open_items) > 10:
        out.append(f"   …and {len(open_items) - 10} more")
    return out


def _age_hours(created: str) -> float:
    from datetime import datetime, timedelta, timezone

    sgt = timezone(timedelta(hours=8))
    try:
        when = datetime.strptime(str(created).strip(), "%Y-%m-%d %H:%M").replace(tzinfo=sgt)
    except ValueError:
        return 0.0
    return (datetime.now(sgt) - when).total_seconds() / 3600


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
