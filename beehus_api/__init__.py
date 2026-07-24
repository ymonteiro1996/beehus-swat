"""Beehus API client package.

Reusable HTTP client for https://controladoria.beehus.com.br endpoints.

Usage from any blueprint:
    from beehus_api import create_transaction, set_token
    set_token("eyJ...")  # done once per day via /beehus page
    result = create_transaction(company_id="...", wallet_id="...", balance=100, ...)

The bearer token lives server-side in process memory (see client.py).
Lost on restart by design — user re-pastes it daily on the /beehus page.
"""
from .client import set_token, get_token, clear_token, token_status, verify_token
from .exceptions import BeehusAPIError, BeehusAuthError
from .transactions import (create_transaction, delete_transaction,
                           update_transaction, list_transactions)
from .partner import partner_wallets, list_companies, list_entities
from .positions import (
    process_processed_position,
    delete_processed_position,
    upload_unprocessed_security_positions_file,
    get_processed_position,
    get_unprocessed_security_positions,
    get_preprocessing_status,
)
from .provisions import (create_provision, delete_provision, update_provision,
                         list_provisions)
from .execution_prices import (create_execution_price, list_execution_prices,
                               update_execution_price, delete_execution_price)
from .consolidation import (
    get_company_variables,
    list_company_variables,
    calculate_nav_wallets,
    calculate_nav_groupings,
    proportion_explosion,
    publish_nav,
    unpublish_nav,
    run_heuristics,
    get_nav_contribution,
    get_nav_results,
)
from .grouping import list_groupings
from .security_mappings import update_security_mappings, get_security_mappings
from .securities import create_security, get_security, list_securities, filtered_security_price, security_events

__all__ = [
    "set_token",
    "get_token",
    "clear_token",
    "token_status",
    "verify_token",
    "BeehusAPIError",
    "BeehusAuthError",
    "create_transaction",
    "delete_transaction",
    "update_transaction",
    "list_transactions",
    "partner_wallets",
    "list_companies",
    "list_entities",
    "process_processed_position",
    "delete_processed_position",
    "upload_unprocessed_security_positions_file",
    "get_processed_position",
    "get_unprocessed_security_positions",
    "get_preprocessing_status",
    "create_provision",
    "delete_provision",
    "update_provision",
    "list_provisions",
    "create_execution_price",
    "list_execution_prices",
    "update_execution_price",
    "delete_execution_price",
    "get_company_variables",
    "list_company_variables",
    "calculate_nav_wallets",
    "calculate_nav_groupings",
    "proportion_explosion",
    "publish_nav",
    "unpublish_nav",
    "run_heuristics",
    "get_nav_contribution",
    "get_nav_results",
    "list_groupings",
    "update_security_mappings",
    "get_security_mappings",
    "create_security",
    "get_security",
    "list_securities",
    "filtered_security_price",
    "security_events",
]
