"""The boundary gate: anything leaving the business, and anything moving money.

This is the real irreversibility line. A Xero posting can be voided and reposted
— internal bookkeeping, nobody outside saw it. But once a recon email lands at
HSBC's RF team, once a claim goes into the Watsons portal, once an investor
update is sent, once a payment instruction is released, it is out. You can send a
correction; you cannot unsend. A third party has already read it and may already
have acted on it.

So the rule here is absolute and deliberately not tunable by track record:

  Every recipient internal  -> the agent sends it, and logs what it sent.
  Any recipient external    -> Danny taps, with the exact content in front of him.
  Money or a filing         -> Danny taps, always, regardless of recipient.

There is no threshold, no confidence score, and no per-client trust that gets
past an external recipient. A hundred clean sends do not earn the hundred-and-
first, because the failure isn't random — it's the one unusual message that
matters, and that's exactly the one a track record says nothing about.

Fail-closed everywhere: an unparseable recipient, an unrecognised channel, or a
domain that isn't on the internal list is treated as external.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .policy import APPROVE, AUTO, Decision

EGRESS_FILE = os.getenv("EGRESS_FILE") or str(Path(__file__).with_name("egress.json"))

# Channels where the act of sending is itself a commitment to an outside party,
# so the recipient list is beside the point.
ALWAYS_APPROVE_CHANNELS = {
    "payment_instruction",   # bank transfer, payment run release
    "statutory_filing",      # ACRA, IRAS, LHDN, DJP — filed is filed
    "portal_submission",     # retailer/bank portals: Watsons, HSBC, DFI
    "contract_execution",    # anything being signed
    "public_post",           # marketplace listings, social, anything published
}

KNOWN_CHANNELS = ALWAYS_APPROVE_CHANNELS | {
    "email",
    "slack",
    "whatsapp",
    "telegram",
    "document_share",        # granting an outside party access to a file
}

_EMAIL_RE = re.compile(r"[\w.+-]+@([\w-]+\.[\w.-]+)")


def load_config(path: str = EGRESS_FILE) -> dict:
    with open(path) as f:
        return json.load(f)


def _domain(recipient: str) -> str | None:
    """Domain of an email address, or None for anything that isn't one.

    Slack user IDs and phone numbers land here too; they can't be resolved to a
    domain, so they're handled by the explicit internal-handle list instead.
    """
    m = _EMAIL_RE.search((recipient or "").strip().lower())
    return m.group(1) if m else None


def classify_recipient(recipient: str, config: dict) -> str:
    """"internal" or "external". Anything unrecognised is external."""
    r = (recipient or "").strip().lower()
    if not r:
        return "external"

    domain = _domain(r)
    if domain:
        internal = {d.lower() for d in config.get("internal_domains", [])}
        # Subdomains of an internal domain count as internal; lookalikes don't
        # (getblood.com.evil.co must not match getblood.com).
        if domain in internal or any(domain.endswith("." + d) for d in internal):
            return "external" if r in {h.lower() for h in config.get("external_overrides", [])} else "internal"
        return "external"

    # Not an email — a Slack ID, channel, or phone. Only an explicit allow-list
    # makes it internal.
    handles = {h.lower() for h in config.get("internal_handles", [])}
    return "internal" if r in handles else "external"


def decide_send(
    channel: str,
    recipients: list[str] | str,
    subject: str = "",
    body: str = "",
    config: dict | None = None,
) -> Decision:
    """Who owns sending this. Returns AUTO (agent sends) or APPROVE (Danny taps)."""
    config = config or load_config()
    if isinstance(recipients, str):
        recipients = [recipients]
    recipients = [r for r in (recipients or []) if str(r).strip()]

    channel = (channel or "").strip().lower()
    if channel in ALWAYS_APPROVE_CHANNELS:
        return Decision(
            APPROVE,
            f"{channel.replace('_', ' ').title()} — irreversible once submitted, regardless of recipient.",
            ask=_ask(channel, recipients, subject, body),
        )
    if channel not in KNOWN_CHANNELS:
        return Decision(
            APPROVE,
            f"Unrecognised channel {channel!r} — treated as external.",
            ask=_ask(channel, recipients, subject, body),
        )

    if not recipients:
        return Decision(APPROVE, "No recipient resolved — treated as external.",
                        ask=_ask(channel, recipients, subject, body))

    external = [r for r in recipients if classify_recipient(r, config) == "external"]
    if external:
        return Decision(
            APPROVE,
            f"Goes outside the business: {', '.join(external[:4])}"
            + (f" (+{len(external) - 4} more)" if len(external) > 4 else ""),
            ask=_ask(channel, recipients, subject, body),
        )

    # Internal only — but some subjects shouldn't move without Danny even inside
    # the company. A fundraise number reaching the wrong internal channel is not
    # recoverable either.
    hit = _sensitive_hit(f"{subject}\n{body}", config)
    if hit:
        return Decision(
            APPROVE,
            f"Internal recipients, but the content mentions {hit!r} — held for you.",
            ask=_ask(channel, recipients, subject, body),
        )

    return Decision(AUTO, f"Internal only ({', '.join(recipients[:4])}) — sent and logged.")


def _sensitive_hit(text: str, config: dict) -> str | None:
    lowered = (text or "").lower()
    for term in config.get("sensitive_terms", []):
        if term.lower() in lowered:
            return term
    return None


def _ask(channel: str, recipients: list[str], subject: str, body: str) -> str:
    """The approval prompt. The full content goes in, because approving a message
    you can't see is not approval — and it's truncated at the end, never the
    start, so the ask is always visible."""
    lines = [f"Send via {channel}?", f"To: {', '.join(recipients) or '(none resolved)'}"]
    if subject:
        lines.append(f"Subject: {subject}")
    lines.append("")
    body = body or "(no body)"
    limit = 2200  # leaves room for the header inside Telegram's 4096-char message
    if len(body) > limit:
        body = body[:limit] + f"\n… [truncated, {len(body) - limit} more chars]"
    lines.append(body)
    return "\n".join(lines)


_SENDERS: dict[str, callable] = {}


def register_sender(channel: str, fn):
    """fn(payload: dict) -> str. Registers the transport for a channel so an
    approved send can be replayed later, from a different process than the one
    that staged it — Railway redeploys between the ask and the tap.

    A channel with no registered sender can still be *gated*; it just can't be
    replayed, which surfaces as a failed approval rather than a silent no-op.
    """
    _SENDERS[channel] = fn


def resolve_egress(action: str, payload: dict) -> str:
    """Approval resolver for staged sends. Register with:

        approvals.register_resolver("egress", egress.resolve_egress)
    """
    channel = payload.get("channel", "")
    if action != "ok":
        # "draft" and "no" both mean don't send. Nothing was sent, so there is
        # nothing to undo — the distinction is only what the log says.
        return "not sent (left for you)" if action == "draft" else "discarded"

    sender = _SENDERS.get(channel)
    if sender is None:
        raise RuntimeError(
            f"no sender registered for channel {channel!r} — approved but cannot send"
        )
    result = sender(payload)
    return f"sent via {channel}: {result}" if result else f"sent via {channel}"


def send_or_ask(channel: str, recipients, subject: str, body: str, send_fn=None, context: dict | None = None):
    """Send it now if internal, otherwise stage an approval.

    `send_fn()` takes no arguments and is used only for the immediate internal
    path, so the caller keeps ownership of the transport. If it's omitted the
    registered sender for the channel is used instead, which is what you want
    when the same code path handles both cases.
    """
    decision = decide_send(channel, recipients, subject, body)
    payload = {
        "channel": channel,
        "recipients": recipients,
        "subject": subject,
        "body": body,
        "reason": decision.reason,
        **(context or {}),
    }

    if decision.outcome == AUTO:
        if send_fn is not None:
            result = send_fn()
        elif channel in _SENDERS:
            result = _SENDERS[channel](payload)
        else:
            raise RuntimeError(f"no sender for channel {channel!r}")
        print(f"[egress] sent via {channel} to {recipients}: {decision.reason}")
        return {"status": "sent", "reason": decision.reason, "result": result}

    from . import approvals

    approval_id = approvals.request_approval(
        kind="egress",
        client=(context or {}).get("client", "—"),
        summary=f"{channel} → {', '.join(recipients if isinstance(recipients, list) else [recipients])}"
                + (f": {subject}" if subject else ""),
        question=f"{decision.ask}\n\n[{decision.reason}]",
        payload=payload,
    )
    return {"status": "awaiting_approval", "approval_id": approval_id, "reason": decision.reason}
