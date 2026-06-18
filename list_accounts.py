"""
Prints the chart of accounts for a given Xero org.
Usage: python list_accounts.py "S Grill Kitchen Pte. Ltd"
"""
import sys
from xero_client import _get_headers, XERO_API_BASE
import requests

org_name = sys.argv[1] if len(sys.argv) > 1 else "S Grill Kitchen Pte. Ltd"
headers = _get_headers(org_name)

resp = requests.get(
    f"{XERO_API_BASE}/Accounts",
    headers=headers,
    params={"where": "Status==\"ACTIVE\" AND Class==\"EXPENSE\""},
)
resp.raise_for_status()

accounts = resp.json().get("Accounts", [])
print(f"\n{'Code':<10} {'Name':<50} {'Type'}")
print("-" * 75)
for a in sorted(accounts, key=lambda x: x.get("Code", "")):
    print(f"{a.get('Code',''):<10} {a.get('Name',''):<50} {a.get('Type','')}")
