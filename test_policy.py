"""Tests for the decision-rights policy.

Worth having even though the rest of the repo has no tests: this is the file that
decides what reaches Danny and what commits to a client's ledger unattended. A
regression here is either a wrong ledger entry or a bottleneck reappearing.

    python test_policy.py
"""
from agent_core import egress, policy

BASE = {
    "confidence": "high",
    "is_handwritten": False,
    "vendor_mapped": True,
    "new_client": False,
    "totals_consistent": True,
    "amount": 120.0,
    "currency": "SGD",
    "vendor_name": "AH GUAN VEGETABLE SUPPLY",
    "invoice_date": "2026-07-30",
    "account_code": "6V01-0000",
    "account_name": "VEGETABLES",
}

CONFIG = {
    "defaults": {
        "auto_authorise_max_sgd": 500,
        "auto_map_max_sgd": 150,
        "ask_to_learn_mappings": False,
        "trust_handwritten": False,
        "trust_new_clients": False,
    },
    "clients": {
        "Generous Client Pte Ltd": {"auto_authorise_max_sgd": 5000},
        "Chatty Client Pte Ltd": {"ask_to_learn_mappings": True},
    },
    "fx_to_sgd": {"SGD": 1.0, "MYR": 0.3125, "USD": 1.35},
}

EGRESS = {
    "internal_domains": ["pslove.com", "getblood.com"],
    "external_overrides": ["auditor-shared@getblood.com"],
    "internal_handles": ["#finance-desk"],
    "sensitive_terms": ["term sheet", "valuation"],
}


def sig(**over):
    return {**BASE, **over}


def check(name, got, want):
    status = "ok  " if got == want else "FAIL"
    print(f"  [{status}] {name}: {got}" + ("" if got == want else f" (expected {want})"))
    return got == want


def main():
    results = []
    r = results.append

    print("routine work never reaches Danny")
    r(check("small mapped invoice", policy.decide(sig(), config=CONFIG).outcome, policy.AUTO))
    r(check("immaterial unmapped vendor",
            policy.decide(sig(vendor_mapped=False, amount=40.0), config=CONFIG).outcome, policy.AUTO))

    print("\nunreadable documents escalate — nothing is staged in Xero")
    r(check("low confidence", policy.decide(sig(confidence="low"), config=CONFIG).outcome, policy.ESCALATE))
    r(check("handwritten", policy.decide(sig(is_handwritten=True), config=CONFIG).outcome, policy.ESCALATE))
    r(check("missing total", policy.decide(sig(amount=None), config=CONFIG).outcome, policy.ESCALATE))
    r(check("missing date", policy.decide(sig(invoice_date=None), config=CONFIG).outcome, policy.ESCALATE))
    r(check("totals don't add up",
            policy.decide(sig(totals_consistent=False), config=CONFIG).outcome, policy.ESCALATE))
    r(check("brand new client", policy.decide(sig(new_client=True, vendor_mapped=False),
                                             config=CONFIG).outcome, policy.ESCALATE))

    print("\ninternal postings never interrupt — the worst case is a draft")
    r(check("above auto limit", policy.decide(sig(amount=2000.0), config=CONFIG).outcome, policy.DRAFT))
    r(check("far above auto limit", policy.decide(sig(amount=90000.0), config=CONFIG).outcome, policy.DRAFT))
    r(check("medium confidence is never auto",
            policy.decide(sig(confidence="medium", amount=100.0), config=CONFIG).outcome, policy.DRAFT))
    r(check("no internal amount ever becomes an approval",
            all(policy.decide(sig(amount=a), config=CONFIG).outcome != policy.APPROVE
                for a in (1.0, 999.0, 5000.0, 1_000_000.0)), True))

    print("\nunmapped vendor drafts and carries the mapping forward")
    d = policy.decide(sig(vendor_mapped=False, amount=412.50), config=CONFIG)
    r(check("drafts rather than asking", d.outcome, policy.DRAFT))
    r(check("carries the mapping to learn",
            d.learn.get("vendor_mapping", {}).get("code"), "6V01-0000"))
    d2 = policy.decide(sig(vendor_mapped=False, amount=412.50),
                       client_name="Chatty Client Pte Ltd", config=CONFIG)
    r(check("opt-in flag turns it into a tap", d2.outcome, policy.APPROVE))
    r(check("and the tap has a question", bool(d2.ask), True))

    print("\nforeign currency is valued in SGD before thresholds apply")
    r(check("MYR 1,200 ≈ S$375 → auto",
            policy.decide(sig(currency="MYR", amount=1200.0), config=CONFIG).outcome, policy.AUTO))
    r(check("MYR 4,000 ≈ S$1,250 → draft",
            policy.decide(sig(currency="MYR", amount=4000.0), config=CONFIG).outcome, policy.DRAFT))
    r(check("unpriceable currency is never auto",
            policy.decide(sig(currency="VND", amount=100.0), config=CONFIG).outcome, policy.APPROVE))

    print("\nper-client trust overrides the defaults")
    r(check("trusted client, S$2,000",
            policy.decide(sig(amount=2000.0), client_name="Generous Client Pte Ltd",
                          config=CONFIG).outcome, policy.AUTO))

    print("\nthe shipped policy.json parses and behaves")
    live = policy.load_config()
    r(check("HZ Cuisine override applies",
            policy.decide(sig(amount=4000.0), client_name="HZ Cuisine Pte Ltd", config=live).outcome,
            policy.AUTO))
    r(check("same amount, ordinary client",
            policy.decide(sig(amount=4000.0), client_name="Some Other Pte Ltd", config=live).outcome,
            policy.DRAFT))

    print("\n--- the boundary gate (egress) ---")
    print("internal recipients: the agent sends, no tap")
    r(check("slack to the team",
            egress.decide_send("slack", ["caleb@getblood.com", "peck@pslove.com"],
                               body="Draft numbers attached", config=EGRESS).outcome, policy.AUTO))
    r(check("allow-listed channel handle",
            egress.decide_send("slack", ["#finance-desk"], body="FYI", config=EGRESS).outcome, policy.AUTO))
    r(check("subdomain of an internal domain",
            egress.decide_send("email", ["ops@mail.getblood.com"], body="x", config=EGRESS).outcome,
            policy.AUTO))

    print("\nanything outside the business always asks")
    r(check("HSBC recon submission",
            egress.decide_send("email", ["rfcreditcontrolsgh@hsbc.com.sg"],
                               subject="Submission of July 2026 recon", body="Attached.",
                               config=EGRESS).outcome, policy.APPROVE))
    r(check("one external cc among internals",
            egress.decide_send("email", ["caleb@getblood.com", "partner@vc.com"],
                               body="x", config=EGRESS).outcome, policy.APPROVE))
    r(check("lookalike domain is not internal",
            egress.decide_send("email", ["x@getblood.com.evil.co"], body="x",
                               config=EGRESS).outcome, policy.APPROVE))
    r(check("shared mailbox flagged as an outside party",
            egress.decide_send("email", ["auditor-shared@getblood.com"], body="x",
                               config=EGRESS).outcome, policy.APPROVE))
    r(check("unknown slack id is not internal",
            egress.decide_send("slack", ["U09XYZ"], body="x", config=EGRESS).outcome, policy.APPROVE))
    r(check("no recipient resolved",
            egress.decide_send("email", [], body="x", config=EGRESS).outcome, policy.APPROVE))
    r(check("unrecognised channel",
            egress.decide_send("carrier_pigeon", ["caleb@getblood.com"], body="x",
                               config=EGRESS).outcome, policy.APPROVE))

    print("\nmoney and filings ask regardless of recipient")
    for ch in ("payment_instruction", "statutory_filing", "portal_submission",
               "contract_execution", "public_post"):
        r(check(ch, egress.decide_send(ch, ["finance@pslove.com"], body="x",
                                       config=EGRESS).outcome, policy.APPROVE))

    print("\nsensitive content is held even internally")
    r(check("valuation talk to the team",
            egress.decide_send("slack", ["caleb@getblood.com"],
                               body="the term sheet lands Friday", config=EGRESS).outcome,
            policy.APPROVE))

    print("\nthe approval shows what would actually be sent")
    d = egress.decide_send("email", ["rfcreditcontrolsgh@hsbc.com.sg"],
                           subject="July recon", body="Line H ties to the aging.", config=EGRESS)
    r(check("recipient in the prompt", "hsbc.com.sg" in d.ask, True))
    r(check("subject in the prompt", "July recon" in d.ask, True))
    r(check("body in the prompt", "Line H ties" in d.ask, True))
    long_body = "x" * 5000
    d = egress.decide_send("email", ["a@vc.com"], body=long_body, config=EGRESS)
    r(check("long body truncated below Telegram's limit", len(d.ask) < 4096, True))

    print("\nthe shipped egress.json parses and behaves")
    live_e = egress.load_config()
    r(check("HSBC is external", egress.classify_recipient("rfcreditcontrolsgh@hsbc.com.sg", live_e),
            "external"))
    r(check("own team is internal", egress.classify_recipient("caleb@getblood.com", live_e), "internal"))

    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
