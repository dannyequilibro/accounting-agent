from datetime import datetime
from sheet_manager import get_exceptions_sheet


def log_exception(
    file_name: str,
    client_name: str,
    location: str,
    drive_url: str,
    invoice_data: dict,
    exception_reasons: list[str],
    **kwargs,
):
    sheet = get_exceptions_sheet(client_name)

    account_code = invoice_data.get("_account_code", "—")
    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        client_name,
        location or "—",
        file_name,
        drive_url,
        invoice_data.get("vendor_name", "—"),
        invoice_data.get("invoice_number", "—"),
        invoice_data.get("invoice_date", "—"),
        invoice_data.get("total_amount", "—"),
        invoice_data.get("currency", "SGD"),
        account_code,
        "Yes" if invoice_data.get("is_handwritten") else "No",
        invoice_data.get("confidence", "—"),
        " | ".join(exception_reasons),
    ]

    sheet.append_row(row)
    print(f"Exception logged to {client_name} sheet: {file_name}")
