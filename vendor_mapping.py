import os
import gspread
from google.oauth2 import service_account
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

MAPPING_HEADERS = ["Vendor Name", "Account Code", "Account Name", "Notes"]
DEFAULT_ACCOUNT_CODE = "6010-0000"
DEFAULT_ACCOUNT_NAME = "PURCHASES"


def lookup_vendor(vendor_name: str, client_name: str = "") -> dict | None:
    """
    Returns {"account_code": ..., "account_name": ...} if vendor is mapped, else None.
    Matching is case-insensitive and tolerates minor variations.
    """
    from sheet_manager import get_mapping_sheet
    sheet = get_mapping_sheet(client_name)
    rows = sheet.get_all_records()

    name_lower = vendor_name.lower().strip()
    for row in rows:
        mapped = str(row.get("Vendor Name", "")).lower().strip()
        if not mapped:
            continue
        if mapped == name_lower or mapped in name_lower or name_lower in mapped:
            return {
                "account_code": row.get("Account Code", DEFAULT_ACCOUNT_CODE),
                "account_name": row.get("Account Name", ""),
            }
    return None


def suggest_account_code(vendor_name: str, line_items: list, description: str) -> tuple[str, str]:
    """
    Use keyword matching to suggest an account code from the chart of accounts.
    Returns (account_code, account_name).
    """
    text = f"{vendor_name} {description} {' '.join(i.get('description','') for i in (line_items or []))}".lower()

    rules = [
        (["gas", "lpg", "natural gas", "piped gas"],           "6020-0000", "GAS"),
        (["packaging", "container", "box", "bag", "wrap"],      "6030-0000", "PACKAGING"),
        (["cake", "bread", "bao", "dim sum", "pastry", "bun"],  "6C01-0000", "CAKE, BREAD, BAO, SPREAD, DIM SUM"),
        (["egg", "eggs"],                                        "6E01-0000", "EGGS"),
        (["meat", "chicken", "seafood", "lamb", "beef", "pork",
          "mutton", "duck", "prawn", "fish", "crab", "sotong"], "6M01-0000", "MEAT, CHICKEN, SEAFOOD, VEGETABLE, LAMB, BEEF, PORK"),
        (["noodle", "pasta", "vermicelli", "bee hoon", "mee"],  "6N01-0000", "NOODLES"),
        (["rice", "oil", "cooking oil"],                         "6R01-0000", "RICE & OIL"),
        (["sauce", "soy", "oyster", "seasoning", "grocery",
          "spice", "condiment", "sugar", "salt", "flour"],      "6S01-0000", "SAUCES/GROCERIES"),
        (["tofu", "tau fu", "bean curd", "soya bean"],          "6T01-0000", "TAU FU & BEAN CURD"),
        (["vegetable", "veg", "veggie", "produce", "greens",
          "lettuce", "cabbage", "spinach", "kailan"],            "6V01-0000", "VEGETABLES"),
        (["electricity", "water", "utilities", "sp group",
          "puc", "utility"],                                     "9U01-0000", "UTILITIES"),
        (["rental", "rent", "lease"],                            "9R01-0000", "RENTAL OF STALL"),
        (["telephone", "mobile", "internet", "singtel",
          "starhub", "m1"],                                      "9T01-0000", "TELEPHONE CHARGES"),
        (["repair", "maintenance", "servicing"],                 "9R03-0000", "REPAIR & MAINTENANCE"),
        (["cleaning", "pest control", "sanitation"],             "9C03-0000", "CLEANING EXPENSES"),
        (["insurance"],                                           "9I01-0000", "INSURANCE"),
        (["licence", "license", "permit"],                       "9L02-0000", "LICENCE FEES"),
        (["transport", "delivery", "courier", "grab",
          "logistics"],                                           "9T02-0000", "TRANSPORTATION"),
        (["printing", "stationery", "office supply"],            "9P01-0000", "PRINTING AND STATIONERY"),
        (["bank charge", "transaction fee", "service charge"],   "9B01-0000", "BANK CHARGES"),
        (["medical", "clinic", "pharmacy", "healthcare"],        "9M01-0000", "MEDICAL FEES"),
        (["staff", "welfare", "meal", "entertainment"],          "9S04-0000", "STAFF WELFARE"),
        (["professional", "consultant", "advisory"],             "9P02-0000", "PROFESSIONAL FEE"),
        (["it ", "software", "subscription", "saas",
          "computer", "technology"],                              "9I02-0000", "IT EXPENSES"),
    ]

    for keywords, code, name in rules:
        if any(kw in text for kw in keywords):
            return code, name

    return DEFAULT_ACCOUNT_CODE, DEFAULT_ACCOUNT_NAME


def get_account_code(vendor_name: str, line_items: list, description: str = "", client_name: str = "") -> tuple[str, str, bool]:
    """
    Returns (account_code, account_name, was_mapped).
    was_mapped=True means it came from the Vendor Mapping sheet (trusted).
    was_mapped=False means it was auto-suggested (needs review).
    """
    mapping = lookup_vendor(vendor_name, client_name)
    if mapping:
        return mapping["account_code"], mapping["account_name"], True

    suggested_code, suggested_name = suggest_account_code(vendor_name, line_items, description)
    return suggested_code, suggested_name, False
