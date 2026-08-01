"""Decision rights for work that stays *inside* the business.

The line is egress, not size. A bookkeeping entry can be voided and reposted;
nobody outside has seen it and no third party acted on it. So internal ledger
work is the agent's, and materiality only decides how visible the agent makes it
— never whether it stops to ask. The gate belongs on the boundary: anything that
reaches a bank, a retailer, an auditor, an investor, or a regulator, and anything
that moves money. That lives in egress.py, and no track record ever earns past
it.

Three outcomes here, none of which interrupt Danny:

  AUTO      Post it. One line in the daily digest.
  DRAFT     Post it as a Xero DRAFT and say so. Not because it's risky to commit
            — it isn't — but because the agent's read of the *document* is shaky
            enough that the number shouldn't become the working figure before
            someone glances at it. Reviewed in Xero in one pass.
  ESCALATE  The document can't be read at all. Nothing is staged; a human has to
            open the PDF.

APPROVE still exists because egress.py returns it, and because a per-client flag
can opt into one-tap prompts for unmapped vendors (off by default — a draft plus
a digest line gets the same result without the interruption).

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
    # The coding might be wrong, but a wrong account code is a reclass, not a
    # loss — so this is a draft, not a question. The mapping to learn rides along
    # so it can be written when the draft is authorised.
    if not signals.get("vendor_mapped"):
        if amount_sgd <= cfg.get("auto_map_max_sgd", 0):
            return Decision(
                AUTO,
                f"Unmapped vendor but immaterial (S${amount_sgd:,.2f}) — posted on the suggested account.",
                learn=_learn(signals),
            )
        if cfg.get("ask_to_learn_mappings", False):
            return Decision(
                APPROVE,
                f"Vendor '{signals.get('vendor_name')}' is not mapped; suggested "
                f"{signals.get('account_code')} {signals.get('account_name')}.",
                ask=_ask(signals, amount_sgd),
                learn=_learn(signals),
            )
        return Decision(
            DRAFT,
            f"Vendor '{signals.get('vendor_name')}' not mapped — drafted on suggested "
            f"{signals.get('account_code')} {signals.get('account_name')}.",
            learn=_learn(signals),
        )

    # --- Mapped vendor. Confidence, not size, decides whether a human eyeballs
    # it: the amount can't make an internal posting less reversible, it only
    # makes a misread more annoying to unwind.
    if signals.get("confidence") == "high" and amount_sgd <= cfg.get("auto_authorise_max_sgd", 0):
        return Decision(AUTO, f"Mapped vendor, high confidence, S${amount_sgd:,.2f} — routine.")

    if signals.get("confidence") != "high":
        return Decision(DRAFT, f"Extraction confidence is {signals.get('confidence')} — drafted for a glance.")

    return Decision(
        DRAFT,
        f"S${amount_sgd:,.2f} is above this client's auto limit — drafted so the "
        f"coding gets a look before it becomes the working figure.",
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
