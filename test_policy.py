"""Tests for the decision-rights policy.

Worth having even though the rest of the repo has no tests: this is the file that
decides what reaches Danny and what commits to a client's ledger unattended. A
regression here is either a wrong ledger entry or a bottleneck reappearing.

    python test_policy.py
"""
from agent_core import policy

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
        "draft_max_sgd": 5000,
        "auto_map_max_sgd": 150,
        "trust_handwritten": False,
        "trust_new_clients": False,
    },
    "clients": {"Generous Client Pte Ltd": {"auto_authorise_max_sgd": 5000, "draft_max_sgd": 50000}},
    "fx_to_sgd": {"SGD": 1.0, "MYR": 0.3125, "USD": 1.35},
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

    print("\nmateriality decides commit-now vs stage-as-draft")
    r(check("above auto, below draft cap",
            policy.decide(sig(amount=2000.0), config=CONFIG).outcome, policy.DRAFT))
    r(check("above draft cap", policy.decide(sig(amount=9000.0), config=CONFIG).outcome, policy.APPROVE))
    r(check("medium confidence is never auto",
            policy.decide(sig(confidence="medium", amount=100.0), config=CONFIG).outcome, policy.DRAFT))

    print("\nunmapped vendor above the trivial line asks once, then learns")
    d = policy.decide(sig(vendor_mapped=False, amount=412.50), config=CONFIG)
    r(check("asks", d.outcome, policy.APPROVE))
    r(check("has a tappable question", bool(d.ask), True))
    r(check("carries the mapping to learn",
            d.learn.get("vendor_mapping", {}).get("code"), "6V01-0000"))

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
            policy.decide(sig(amount=1200.0), client_name="HZ Cuisine Pte Ltd", config=live).outcome,
            policy.AUTO))
    r(check("same amount, ordinary client",
            policy.decide(sig(amount=1200.0), client_name="Some Other Pte Ltd", config=live).outcome,
            policy.DRAFT))

    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
