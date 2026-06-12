"""Beehus API client package.

Reusable HTTP client for https://controladoria.beehus.com.br endpoints.

Usage from any blueprint:
    from beehus_api import create_transaction, set_token
    set_token("eyJ...")  # done once per day via /beehus page
    result = create_transaction(company_id="...", wallet_id="...", balance=100, ...)

The bearer token lives server-side in process memory (see client.py).
Lost on restart by design — user re-pastes it daily on the /beehus page.
"""
from .client import set_token, get_token, clear_token, token_status
from .exceptions import BeehusAPIError, BeehusAuthError
from .transactions import create_transaction, delete_transaction, update_transaction
from .positions import (
    process_processed_position,
    delete_processed_position,
    upload_unprocessed_security_positions_file,
)
from .provisions import create_provision, delete_provision, update_provision
from .execution_prices import create_execution_price
from .consolidation import (
    calculate_nav_wallets,
    calculate_nav_groupings,
    proportion_explosion,
    publish_nav,
    unpublish_nav,
    run_heuristics,
)
from .security_mappings import update_security_mappings

__all__ = [
    "set_token",
    "get_token",
    "clear_token",
    "token_status",
    "BeehusAPIError",
    "BeehusAuthError",
    "create_transaction",
    "delete_transaction",
    "update_transaction",
    "process_processed_position",
    "delete_processed_position",
    "upload_unprocessed_security_positions_file",
    "create_provision",
    "delete_provision",
    "update_provision",
    "create_execution_price",
    "calculate_nav_wallets",
    "calculate_nav_groupings",
    "proportion_explosion",
    "publish_nav",
    "unpublish_nav",
    "run_heuristics",
    "update_security_mappings",
]
