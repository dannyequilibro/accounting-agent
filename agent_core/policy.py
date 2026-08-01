"""Decision rights: which actions the agent owns, and which ones Danny owns.

The rule is reversibility, not difficulty. An action the agent can undo without
anyone noticing is the agent's to take. An action that leaves a mark someone
else can see — a ledger entry, a payment, an email to a bank — is Danny's until
the agent has earned it on that specific shape of work.

Four outcomes, in order of how much of Danny's attention they cost:

  AUTO      Do it. Nothing comes to Danny except a line in the daily digest.
  DRAFT     Do the work, stop one step short of committing it. In Xero that's a
            DRAFT bill: fully deletable, invisible outside the AP list. Danny
            (or a bookkeeper) sees a batch of drafts, not a queue of questions.
  APPROVE   Do the work as a draft, then ask one closed question with the answer
            already filled in. One tap commits it.
  ESCALATE  Don't guess. The document itself needs human eyes.

Everything is a pure function of `signals` and `config` so the thresholds can be
tuned from policy.json — and tested — without touching the pipeline.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

AUTO = "auto"
DRAFT = "draft"
APPROVE = "approve"
ESCALATE = "escalate"

POLICY_FILE = os.getenv("POLICY_FILE") or str(Path(__file__).with_name("policy.json"))

# Used when policy.json is missing a currency. Materiality is compared in SGD,
# so an unknown currency must not silently pass a threshold — see _to_sgd.
_FX_FALLBACK = {"SGD": 1.0}


@dataclass
class Decision:
    outcome: str
    reason: str
    # A closed question for Danny, with the agent's proposed answer. Only set on
    # APPROVE — the whole point is that he taps rather than types.
    ask: str | None = None
    # Facts to write back on approval so the same question is never asked twice.
    # e.g. {"vendor_mapping": {"vendor": "AH GUAN VEG", "code": "6V01-0000", ...}}
    learn: dict = field(default_factory=dict)

    @property
    def needs_human(self) -> bool:
        return self.outcome in (APPROVE, ESCALATE)


def load_config(path: str = POLICY_FILE) -> dict:
    with open(path) as f:
        return json.load(f)


def config_for(client_name: str, config: dict | None = None) -> dict:
    """Per-client thresholds layered over the defaults.

    A client the agent has run clean for months should be trusted further than
    one onboarded last week, and that's a config change, not a code change.
    """
    config = config or load_config()
    merged = dict(config.get("defaults", {}))
    merged.update(config.get("clients", {}).get(client_name, {}))
    merged["fx_to_sgd"] = config.get("fx_to_sgd", _FX_FALLBACK)
    return merged


def _to_sgd(amount, currency: str, fx: dict) -> float | None:
    """Amount in SGD, or None if we can't be sure. None is treated as material."""
    if amount is None:
        return None
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return None
    rate = fx.get((currency or "SGD").upper())
    if not rate:
        return None
    return amount * float(rate)


def decide(signals: dict, client_name: str = "", config: dict | None = None) -> Decision:
    """Classify one proposed bill posting.

    signals:
      confidence        "high" | "medium" | "low"  (from the extractor)
      is_handwritten    bool
      vendor_mapped     bool   — vendor found in the client's Vendor Mapping tab
      new_client        bool   — client has no mappings at all yet
      totals_consistent bool   — subtotal + tax == total
      amount            number | None
      currency          str
      vendor_name       str
      account_code      str    — mapped, or the keyword-rule suggestion
      account_name      str
    """
    cfg = config_for(client_name, config)
    fx = cfg["fx_to_sgd"]
    amount_sgd = _to_sgd(signals.get("amount"), signals.get("currency", "SGD"), fx)

    # --- Escalate: the document can't be read, so there is nothing to approve.
    if signals.get("confidence") == "low":
        return Decision(ESCALATE, "Low-confidence extraction — the document needs eyes.")
    if signals.get("is_handwritten") and not cfg.get("trust_handwritten", False):
        return Decision(ESCALATE, "Handwritten invoice.")
    for field_name, label in (("vendor_name", "vendor"), ("amount", "total"), ("invoice_date", "invoice date")):
        if not signals.get(field_name):
            return Decision(ESCALATE, f"Missing {label}.")
    if not signals.get("totals_consistent", True):
        return Decision(ESCALATE, "Subtotal + tax does not equal total.")

    # A brand-new client has no baseline: nothing is routine yet, and a wrong
    # account code compounds across every later invoice from that vendor.
    if signals.get("new_client") and not cfg.get("trust_new_clients", False):
        return Decision(
            ESCALATE,
            "First invoices for this client — set up the Vendor Mapping tab before the agent posts.",
        )

    if amount_sgd is None:
        return Decision(
            APPROVE,
            f"Cannot value {signals.get('currency')} {signals.get('amount')} in SGD — no FX rate in policy.json.",
            ask=_ask(signals, None),
            learn=_learn(signals),
        )

    # --- Unmapped vendor: the agent guessed the account from keyword rules.
    # This is the highest-yield question in the system. Answering it once writes
    # the mapping, and that vendor never asks again.
    if not signals.get("vendor_mapped"):
        if amount_sgd <= cfg.get("auto_map_max_sgd", 0):
            return Decision(
                AUTO,
                f"Unmapped vendor but immaterial (S${amount_sgd:,.2f}) — posted on the suggested account.",
                learn=_learn(signals),
            )
        return Decision(
            APPROVE,
            f"Vendor '{signals.get('vendor_name')}' is not mapped; suggested "
            f"{signals.get('account_code')} {signals.get('account_name')}.",
            ask=_ask(signals, amount_sgd),
            learn=_learn(signals),
        )

    # --- Mapped vendor, clean extraction. Materiality is the only question left.
    if amount_sgd <= cfg.get("auto_authorise_max_sgd", 0) and signals.get("confidence") == "high":
        return Decision(AUTO, f"Mapped vendor, high confidence, S${amount_sgd:,.2f} — routine.")

    if amount_sgd <= cfg.get("draft_max_sgd", 0):
        return Decision(
            DRAFT,
            f"S${amount_sgd:,.2f} is above the auto limit — posted as a Xero draft for review.",
        )

    return Decision(
        APPROVE,
        f"S${amount_sgd:,.2f} exceeds the draft limit — needs sign-off before it hits the ledger.",
        ask=_ask(signals, amount_sgd),
    )


def _ask(signals: dict, amount_sgd: float | None) -> str:
    """One line Danny can answer from a phone lock screen."""
    amount = signals.get("amount")
    currency = signals.get("currency", "SGD")
    shown = f"{currency} {float(amount):,.2f}" if amount is not None else "amount unknown"
    if amount_sgd is not None and (currency or "SGD").upper() != "SGD":
        shown += f" (≈S${amount_sgd:,.2f})"
    return (
        f"{signals.get('vendor_name')} — {shown}\n"
        f"→ {signals.get('account_code')} {signals.get('account_name')}"
    )


def _learn(signals: dict) -> dict:
    if not signals.get("vendor_name") or not signals.get("account_code"):
        return {}
    return {
        "vendor_mapping": {
            "vendor": signals["vendor_name"],
            "code": signals["account_code"],
            "name": signals.get("account_name", ""),
        }
    }


def signals_from_invoice(
    invoice_data: dict,
    vendor_mapped: bool,
    new_client: bool = False,
) -> dict:
    """Adapt the extractor's output to the policy's input vocabulary."""
    subtotal = invoice_data.get("subtotal") or 0
    tax = invoice_data.get("tax_amount") or 0
    total = invoice_data.get("total_amount") or 0
    consistent = True
    if subtotal and total:
        consistent = abs((subtotal + tax) - total) <= 0.10

    return {
        "confidence": invoice_data.get("confidence"),
        "is_handwritten": bool(invoice_data.get("is_handwritten")),
        "vendor_mapped": vendor_mapped,
        "new_client": new_client,
        "totals_consistent": consistent,
        "amount": invoice_data.get("total_amount"),
        "currency": invoice_data.get("currency", "SGD"),
        "vendor_name": invoice_data.get("vendor_name"),
        "invoice_date": invoice_data.get("invoice_date"),
        "account_code": invoice_data.get("_account_code"),
        "account_name": invoice_data.get("_account_name"),
    }
