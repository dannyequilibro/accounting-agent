"""
Dynamics 365 Business Central client — READ ONLY.

Every call in this module is a GET. Nothing here posts, patches or deletes.
It exists to answer two questions before we commit to any write integration:

  1. Does the Entra app actually have working access to the environment?
  2. What is reachable — specifically, is any fixed-asset data exposed?

Auth is service-to-service (client credentials), so unlike the Xero flow in
xero_auth.py there is no browser consent step and no refresh token to persist.

Usage:
    python bc_client.py probe                 # smoke test + discovery
    python bc_client.py companies
    python bc_client.py accounts "PSLove TH"
    python bc_client.py odata                 # list published web services
    python bc_client.py fa "PSLove TH"        # dump FA setup, if published
"""
import os
import sys
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()

BC_HOST = "https://api.businesscentral.dynamics.com"


class BCConfigError(Exception):
    """Missing or incomplete environment configuration."""


def _cfg() -> dict:
    cfg = {
        "tenant_id": os.getenv("BC_TENANT_ID", "").strip(),
        "client_id": os.getenv("BC_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("BC_CLIENT_SECRET", "").strip(),
        "environment": os.getenv("BC_ENVIRONMENT", "sandbox").strip(),
    }
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        raise BCConfigError(
            f"Missing env vars: {', '.join('BC_' + m.upper() for m in missing)}. "
            "Set them in .env (local) or Railway variables (deployed)."
        )
    return cfg


def _api_base() -> str:
    c = _cfg()
    return f"{BC_HOST}/v2.0/{c['tenant_id']}/{c['environment']}/api/v2.0"


def _odata_base() -> str:
    c = _cfg()
    return f"{BC_HOST}/v2.0/{c['tenant_id']}/{c['environment']}/ODataV4"


_token_cache = {"access_token": "", "expires_at": 0}


def _get_access_token() -> str:
    """Client-credentials token, cached in memory until shortly before expiry."""
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    c = _cfg()
    resp = requests.post(
        f"https://login.microsoftonline.com/{c['tenant_id']}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": c["client_id"],
            "client_secret": c["client_secret"],
            "scope": f"{BC_HOST}/.default",
        },
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(
            f"Token request failed ({resp.status_code}): {resp.text[:500]}"
        )
    data = resp.json()
    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + data.get("expires_in", 3600) - 60
    return _token_cache["access_token"]


def _get(url: str, params: dict = None) -> requests.Response:
    return requests.get(
        url,
        headers={
            "Authorization": f"Bearer {_get_access_token()}",
            "Accept": "application/json",
        },
        params=params,
        timeout=60,
    )


def _get_json(url: str, params: dict = None) -> dict:
    resp = _get(url, params)
    if not resp.ok:
        raise RuntimeError(f"GET {url} failed ({resp.status_code}): {resp.text[:500]}")
    return resp.json()


# ---------------------------------------------------------------------------
# Standard API v2.0 — works without any AL extension.
# ---------------------------------------------------------------------------

def list_companies() -> list[dict]:
    """Companies (legal entities) visible to the app in this environment.

    This is the real authorization check: a valid token with no BC-side
    Entra Application record still returns 401/403 here.
    """
    return _get_json(f"{_api_base()}/companies").get("value", [])


def _find_company(name_or_id: str) -> dict:
    """Match a company by id, then exact name, then case-insensitive substring."""
    companies = list_companies()
    if not companies:
        raise RuntimeError("App sees no companies in this environment.")

    target = name_or_id.lower().strip()
    for c in companies:
        if c.get("id", "").lower() == target:
            return c
    for c in companies:
        if c.get("name", "").lower().strip() == target:
            return c
    for c in companies:
        n = c.get("name", "").lower().strip()
        if target in n or n in target:
            return c
    raise ValueError(
        f"No company matches '{name_or_id}'. "
        f"Available: {[c.get('name') for c in companies]}"
    )


def list_accounts(company: str) -> list[dict]:
    """Chart of accounts for one company — the BC counterpart of list_accounts.py."""
    c = _find_company(company)
    return _get_json(f"{_api_base()}/companies({c['id']})/accounts").get("value", [])


# ---------------------------------------------------------------------------
# OData V4 — the only route to fixed assets without deploying an AL extension.
# Fixed asset tables are absent from the standard v2.0 API, so FA data is
# reachable only if someone has published the relevant pages under Web Services
# in BC. We discover what is published rather than guessing page names.
# ---------------------------------------------------------------------------

FA_KEYWORDS = ("fixedasset", "fixed_asset", "asset", "deprec", "faclass",
               "fasubclass", "fapostinggroup", "faledger")


def list_published_services() -> list[str]:
    """EntitySet names from the OData $metadata document = published web services."""
    resp = _get(f"{_odata_base()}/$metadata")
    if not resp.ok:
        raise RuntimeError(
            f"Could not read OData $metadata ({resp.status_code}): {resp.text[:300]}"
        )
    # Cheap scrape — avoids adding an XML dependency for a discovery helper.
    names = []
    for chunk in resp.text.split('<EntitySet Name="')[1:]:
        names.append(chunk.split('"')[0])
    return sorted(set(names))


def fa_services(published: list[str] = None) -> list[str]:
    """Published services whose names look fixed-asset related."""
    published = published if published is not None else list_published_services()
    return [s for s in published
            if any(k in s.lower().replace(" ", "") for k in FA_KEYWORDS)]


def odata_get(service: str, company: str, params: dict = None) -> list[dict]:
    """Read one published OData service for a given company."""
    c = _find_company(company)
    url = f"{_odata_base()}/Company('{c['name']}')/{service}"
    return _get_json(url, params).get("value", [])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def probe():
    """End-to-end read-only smoke test. Prints what works and what to do next."""
    print("=== D365 Business Central probe (read-only) ===\n")

    c = _cfg()
    print(f"Environment : {c['environment']}")
    print(f"Tenant      : {c['tenant_id']}")
    print(f"Client ID   : {c['client_id'][:8]}...\n")

    print("[1] Requesting token...")
    try:
        _get_access_token()
    except RuntimeError as e:
        print(f"    FAILED: {e}")
        print("    -> Check BC_TENANT_ID / BC_CLIENT_ID / BC_CLIENT_SECRET. A 401")
        print("       here is usually an expired secret, not a permissions problem.")
        return
    print("    OK — token acquired.\n")

    print("[2] Listing companies (verifies BC-side app authorization)...")
    try:
        companies = list_companies()
    except RuntimeError as e:
        print(f"    FAILED: {e}")
        print("    -> A 401/403 with a valid token means the app is not authorized")
        print("       inside BC, or BC_ENVIRONMENT doesn't match the sandbox name.")
        return
    if not companies:
        print("    Token works but no companies visible.")
        print("    -> Add the app on the Microsoft Entra Applications page in BC,")
        print("       assign a permission set, and set State = Enabled.")
        return
    for co in companies:
        print(f"    - {co.get('name')}  ({co.get('id')})")
    print()

    print("[3] Reading chart of accounts for the first company...")
    try:
        accounts = list_accounts(companies[0]["name"])
        print(f"    OK — {len(accounts)} accounts readable.\n")
    except Exception as e:
        print(f"    FAILED: {e}\n")

    print("[4] Discovering published OData web services...")
    try:
        published = list_published_services()
        print(f"    {len(published)} service(s) published.")
        fa = fa_services(published)
        if fa:
            print("    Fixed-asset related:")
            for s in fa:
                print(f"      - {s}")
        else:
            print("    No fixed-asset services published.")
            print("    -> Fixed assets are NOT in the standard BC API. To read FA")
            print("       setup, publish the FA pages under Web Services in BC")
            print("       (Fixed Asset, FA Classes, FA Subclasses, FA Posting Groups,")
            print("       Depreciation Books, FA Depreciation Books), or export the")
            print("       setup as a RapidStart configuration package instead.")
    except Exception as e:
        print(f"    Could not enumerate OData services: {e}")

    print("\n=== probe complete ===")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "probe"
    arg = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        if cmd == "probe":
            probe()
        elif cmd == "companies":
            for co in list_companies():
                print(f"{co.get('name')}\t{co.get('id')}")
        elif cmd == "accounts":
            if not arg:
                sys.exit('Usage: python bc_client.py accounts "Company Name"')
            rows = list_accounts(arg)
            print(f"\n{'No.':<12} {'Name':<50} {'Category'}")
            print("-" * 80)
            for a in sorted(rows, key=lambda x: x.get("number", "")):
                print(f"{a.get('number',''):<12} {a.get('displayName','')[:50]:<50} "
                      f"{a.get('category','')}")
        elif cmd == "odata":
            for s in list_published_services():
                print(s)
        elif cmd == "fa":
            if not arg:
                sys.exit('Usage: python bc_client.py fa "Company Name"')
            found = fa_services()
            if not found:
                sys.exit("No fixed-asset web services published — see 'probe' output.")
            for s in found:
                print(f"\n--- {s} ---")
                print(json.dumps(odata_get(s, arg), indent=2)[:5000])
        else:
            sys.exit(f"Unknown command '{cmd}'. See the module docstring.")
    except BCConfigError as e:
        sys.exit(f"Config error: {e}")


if __name__ == "__main__":
    main()
