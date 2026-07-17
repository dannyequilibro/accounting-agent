"""
Rotation logic for the 5-org Xero connection cap (shared by the Railway app and
the rotate.py CLI). No side effects on import.

The app can only hold MAX_SLOTS connected orgs at once, but there are more
clients than that. They bill at different times, so orgs are swapped in and out.
All live Xero calls here run wherever this is imported — in production that's
Railway, so Railway stays the single owner of the token (see rotate.py note).
"""
from xero_client import get_connections_detail, disconnect_org

# The clients this agent posts for. Rotation happens within this set.
TARGET_CLIENTS = [
    "JWS (BB) Pte Ltd",
    "HZ Cuisine Pte Ltd",
    "HZS Cuisine Pte Ltd",
    "Nest Delight Holding Pte Ltd",
    "Claypot Curry Fishhead Pte Ltd",     # non-261
    "S Grill House Pte. Ltd",
    "S Grill Kitchen Pte. Ltd",
    "Claypot Curry Fishhead 261 Pte. Ltd",
]
MAX_SLOTS = 5


def name_matches(target: str, connected_names: list[str]) -> bool:
    t = target.lower().strip()
    for n in connected_names:
        nl = n.lower().strip()
        if t == nl or t in nl or nl in t:
            return True
    return False


def waiting_clients(connected_names, hours=168):
    """Target clients that recently had an invoice rejected for org_not_connected
    (they have work waiting and need a slot). Reads the run log. {client: count}."""
    try:
        from run_log import read_since
        rows = read_since(hours=hours)
    except Exception:
        return {}
    waiting = {}
    for r in rows:
        if str(r.get("Status", "")).strip() == "org_not_connected":
            c = r.get("Client", "—")
            if not name_matches(c, connected_names):
                waiting[c] = waiting.get(c, 0) + 1
    return waiting


def build_status():
    """Return a plain dict describing current connections + who's waiting."""
    conns = get_connections_detail()
    connected = [c["tenantName"] for c in conns]
    targets = [
        {"client": t, "connected": name_matches(t, connected)}
        for t in TARGET_CLIENTS
    ]
    waiting = waiting_clients(connected)
    return {
        "slots_used": len(connected),
        "max_slots": MAX_SLOTS,
        "connected": connected,
        "targets": targets,
        "waiting": waiting,
    }


def do_disconnect(name_or_tenant: str):
    """Disconnect one org, return {disconnected, slots_used, max_slots}."""
    freed = disconnect_org(name_or_tenant)
    conns = get_connections_detail()
    return {
        "disconnected": freed["tenantName"],
        "slots_used": len(conns),
        "max_slots": MAX_SLOTS,
    }
