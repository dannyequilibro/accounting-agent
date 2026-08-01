"""The approval loop — the piece that was missing.

Before this, when the agent wasn't sure it wrote a row to an Exceptions tab and
stopped. Nothing came back to Danny that he could act on; he had to remember to
open a spreadsheet, work out what the agent had been trying to do, fix the
mapping, and re-trigger the file. That's not delegation, it's a to-do list with
extra steps — and it is exactly the bottleneck to remove.

So: an approval is a durable record of a decision the agent has already made and
staged, waiting on one tap. Three properties matter.

  Durable    Pending approvals live in the Approvals tab of the Run Log sheet,
             not in memory. Railway wipes its disk on every deploy; an approval
             that evaporates on redeploy silently drops work on the floor.
  Resumable  We store the *inputs* needed to finish the job (file id, client,
             proposed account) and re-fetch the document on resume. Storing
             megabytes of PDF in a spreadsheet cell is not an option, and
             re-downloading from Drive is cheap and always current.
  Idempotent Telegram retries callbacks. The row is claimed before the action
             runs, so a double tap can't post a bill twice.

Resolvers are registered by the caller (see main.py) so nothing invoice-shaped
leaks in here — the next worker registers its own.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import requests

SGT = timezone(timedelta(hours=8))

APPROVAL_HEADERS = [
    "ID", "Created (SGT)", "Kind", "Client", "Summary",
    "Status", "Resolved (SGT)", "Resolved By", "Outcome", "Payload",
]

# Actions offered on every approval. Deliberately three: commit, park, reject.
# Free-text replies were considered and rejected — the value is in a decision
# that costs one tap, and a wrong account code is cheaper to fix in Xero than
# to describe over Telegram.
ACTIONS = {
    "ok": "✅ Approve & post",
    "draft": "📝 Keep as draft",
    "no": "🚫 Send to exceptions",
}

_RESOLVERS: dict[str, callable] = {}
_cached_ws = None


def register_resolver(kind: str, fn):
    """fn(action: str, payload: dict) -> str  (a short outcome description).

    Called when Danny answers. Raising is safe: the approval is marked failed
    with the error and stays visible in the digest.
    """
    _RESOLVERS[kind] = fn


# ---------------------------------------------------------------- store

def _ws():
    global _cached_ws
    if _cached_ws is not None:
        return _cached_ws
    from run_log import get_log_spreadsheet

    ss = get_log_spreadsheet()
    try:
        _cached_ws = ss.worksheet("Approvals")
    except Exception:
        _cached_ws = ss.add_worksheet("Approvals", rows=2000, cols=len(APPROVAL_HEADERS))
        _cached_ws.append_row(APPROVAL_HEADERS)
        _cached_ws.format("A1:J1", {"textFormat": {"bold": True}})
    return _cached_ws


def _find_row(approval_id: str) -> tuple[int, dict] | tuple[None, None]:
    ws = _ws()
    for idx, row in enumerate(ws.get_all_records(), start=2):
        if str(row.get("ID", "")).strip() == approval_id:
            return idx, row
    return None, None


def pending(max_age_hours: int | None = None) -> list[dict]:
    """Open approvals, oldest first. The digest uses this to nag about anything
    that's been sitting — an unanswered approval is blocked work."""
    rows = [r for r in _ws().get_all_records() if str(r.get("Status", "")).strip() == "pending"]
    if max_age_hours is None:
        return rows
    cutoff = datetime.now(SGT) - timedelta(hours=max_age_hours)
    out = []
    for r in rows:
        try:
            when = datetime.strptime(str(r["Created (SGT)"]).strip(), "%Y-%m-%d %H:%M").replace(tzinfo=SGT)
        except (ValueError, KeyError):
            continue
        if when <= cutoff:
            out.append(r)
    return out


# ---------------------------------------------------------------- create

def request_approval(kind: str, client: str, summary: str, question: str, payload: dict) -> str:
    """Stage an approval and push it to Danny. Returns the approval id."""
    approval_id = uuid.uuid4().hex[:10]
    _ws().append_row(
        [
            approval_id,
            datetime.now(SGT).strftime("%Y-%m-%d %H:%M"),
            kind,
            client or "—",
            summary,
            "pending",
            "", "", "",
            json.dumps(payload, default=str),
        ],
        value_input_option="USER_ENTERED",
    )
    try:
        _send_prompt(approval_id, client, question)
    except Exception as e:
        # The approval is already durable; a Telegram outage delays the ping but
        # does not lose the work. The digest will surface it as pending.
        print(f"[approvals] {approval_id} stored but Telegram push failed: {e}")
    return approval_id


def _send_prompt(approval_id: str, client: str, question: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": f"🔔 Approval — {client}\n\n{question}",
            "reply_markup": {
                "inline_keyboard": [
                    [{"text": label, "callback_data": f"{approval_id}:{action}"}]
                    for action, label in ACTIONS.items()
                ]
            },
        },
        timeout=20,
    )
    resp.raise_for_status()


# ---------------------------------------------------------------- resolve

def resolve(approval_id: str, action: str, actor: str = "telegram") -> dict:
    """Apply Danny's answer. Safe to call twice with the same id."""
    if action not in ACTIONS:
        return {"status": "error", "detail": f"unknown action {action!r}"}

    row_idx, row = _find_row(approval_id)
    if row_idx is None:
        return {"status": "error", "detail": "approval not found"}
    if str(row.get("Status", "")).strip() != "pending":
        return {"status": "already_resolved", "outcome": row.get("Outcome", "")}

    # Claim the row before doing anything irreversible, so a Telegram retry
    # arriving mid-flight finds it non-pending and stops.
    ws = _ws()
    now = datetime.now(SGT).strftime("%Y-%m-%d %H:%M")
    ws.update(f"F{row_idx}:I{row_idx}", [["resolving", now, actor, ""]])

    payload = {}
    try:
        payload = json.loads(row.get("Payload") or "{}")
    except json.JSONDecodeError as e:
        ws.update(f"F{row_idx}:I{row_idx}", [["failed", now, actor, f"bad payload: {e}"]])
        return {"status": "error", "detail": f"bad payload: {e}"}

    resolver = _RESOLVERS.get(str(row.get("Kind", "")).strip())
    if resolver is None:
        ws.update(f"F{row_idx}:I{row_idx}", [["failed", now, actor, "no resolver registered"]])
        return {"status": "error", "detail": "no resolver registered for this kind"}

    try:
        outcome = resolver(action, payload)
        ws.update(f"F{row_idx}:I{row_idx}", [["resolved", now, actor, outcome]])
        return {"status": "resolved", "action": action, "outcome": outcome}
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        ws.update(f"F{row_idx}:I{row_idx}", [["failed", now, actor, detail]])
        print(f"[approvals] {approval_id} resolver failed: {detail}")
        return {"status": "error", "detail": detail}


# ---------------------------------------------------------------- telegram plumbing

def handle_callback(update: dict) -> dict:
    """Handle one Telegram callback_query update."""
    cq = update.get("callback_query") or {}
    data = cq.get("data") or ""
    if ":" not in data:
        return {"status": "ignored"}
    approval_id, action = data.split(":", 1)

    actor = (cq.get("from") or {}).get("username") or str((cq.get("from") or {}).get("id", "telegram"))
    result = resolve(approval_id, action, actor=actor)

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    note = {
        "resolved": f"{ACTIONS.get(action, action)} — {result.get('outcome', 'done')}",
        "already_resolved": "Already handled.",
    }.get(result["status"], f"Failed: {result.get('detail', 'unknown')}")

    if token and cq.get("id"):
        # Answering the callback stops Telegram's spinner and its retries.
        requests.post(
            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
            json={"callback_query_id": cq["id"], "text": note[:200]},
            timeout=10,
        )
        msg = cq.get("message") or {}
        if msg.get("chat", {}).get("id") and msg.get("message_id"):
            # Rewrite the original message so the thread reads as a decision log
            # and the buttons can't be tapped again.
            requests.post(
                f"https://api.telegram.org/bot{token}/editMessageText",
                json={
                    "chat_id": msg["chat"]["id"],
                    "message_id": msg["message_id"],
                    "text": f"{msg.get('text', '')}\n\n— {note}",
                },
                timeout=10,
            )
    return result


def register_webhook(base_url: str) -> dict:
    """Point the Telegram bot at this app. Run once per deployment URL:

        python -m agent_core.approvals register-webhook
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
    if not secret:
        raise RuntimeError(
            "TELEGRAM_WEBHOOK_SECRET not set — without it anyone who guesses the "
            "URL can approve your bills."
        )
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json={
            "url": f"{base_url.rstrip('/')}/telegram/callback",
            "secret_token": secret,
            "allowed_updates": ["callback_query"],
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    if len(sys.argv) > 1 and sys.argv[1] == "register-webhook":
        url = sys.argv[2] if len(sys.argv) > 2 else os.getenv("RAILWAY_URL", "")
        if not url:
            sys.exit("Usage: python -m agent_core.approvals register-webhook <https://app-url>")
        print(json.dumps(register_webhook(url), indent=2))
    else:
        for r in pending():
            print(f"{r['ID']}  {r['Created (SGT)']}  {r['Client']}  {r['Summary']}")
