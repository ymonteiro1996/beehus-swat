"""High-level functions for /beehus/financial/positions.

One Python function per logical operation. Returns parsed dicts.
Reusable from any blueprint — does not depend on Flask.
"""
from .client import request, request_multipart


def process_processed_position(
    *,
    company_id: str,
    position_date: str,
    wallets: list[str] | None = None,
    timeout: int = 300,
) -> dict | None:
    """POST /beehus/financial/positions/processed-position/process.

    Triggers the server-side processing of positions for the given company
    on `position_date` (ISO YYYY-MM-DD). When `wallets` is empty/None the
    server processes all wallets in the company; otherwise only the listed
    wallet ids are processed. Position processing can take a while, so the
    default timeout is wider than the API client default.
    """
    payload = {
        "companyId": company_id,
        "positionDate": position_date,
        "wallets": list(wallets or []),
    }
    return request(
        "POST",
        "/beehus/financial/positions/processed-position/process",
        json=payload,
        timeout=timeout,
    )


def delete_processed_position(
    *,
    company_id: str,
    position_date: str,
    wallet_ids: list[str] | None = None,
    timeout: int = 300,
) -> dict | None:
    """DELETE /beehus/financial/positions/processed-position/delete.

    Deletes processed positions for the given company on `position_date`.
    When `wallet_ids` is empty/None the upstream API treats it as "all
    wallets in the company". The upstream payload field is `walletIds`
    (camelCase, plural with Ids) — note the spelling difference vs. the
    `wallets` field used by the sibling /process endpoint.
    """
    payload = {
        "companyId": company_id,
        "positionDate": position_date,
        "walletIds": list(wallet_ids or []),
    }
    return request(
        "DELETE",
        "/beehus/financial/positions/processed-position/delete",
        json=payload,
        timeout=timeout,
    )


def upload_unprocessed_security_positions_file(
    *,
    company_id: str,
    file_bytes: bytes,
    filename: str = "unprocessed_security_positions.xlsx",
    timeout: int = 120,
) -> dict | None:
    """POST /beehus/financial/positions/unprocessed-security-positions/file.

    Multipart upload of an Excel workbook with the wallet's unprocessed
    security positions. The upstream endpoint expects the columns:
    `Data, Carteira, Ativo, Quant, PU, SaldoBruto, Caixa, Moeda` plus a
    form field `companyId`.
    """
    files = {
        "file": (
            filename,
            file_bytes,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    }
    data = {"companyId": company_id}
    return request_multipart(
        "POST",
        "/beehus/financial/positions/unprocessed-security-positions/file",
        files=files,
        data=data,
        timeout=timeout,
    )
