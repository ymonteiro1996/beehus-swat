"""High-level functions for /partner/auth/users."""
from .client import request


def create_user(
    *,
    company_id: str,
    name: str,
    email: str,
    phone: str,
    permissions: list | None = None,
    admin: bool = False,
    user_type: str = "client",
) -> dict:
    """POST /partner/auth/users/ — create a partner client user.

    Returns the created user document (including the server-assigned `_id`).
    """
    payload = {
        "admin":       admin,
        "companyId":   company_id,
        "email":       email,
        "name":        name,
        "permissions": list(permissions or []),
        "phone":       phone,
        "type":        user_type,
    }
    return request("POST", "/partner/auth/users/", json=payload)


def update_user(
    user_id: str,
    *,
    name: str,
    email: str,
    phone: str,
    groupings: list,
    admin: bool = False,
    user_type: str = "client",
) -> dict | None:
    """PATCH /partner/auth/users/{userId} — replace user-editable fields.

    `groupings` is the full list of `{_id, description, isOwn}` entries the
    user should see. The upstream endpoint replaces the previous list, so
    callers must include EVERY grouping that should remain associated.
    """
    if not user_id:
        raise ValueError("user_id is required")
    payload = {
        "admin":     admin,
        "email":     email,
        "groupings": list(groupings or []),
        "name":      name,
        "phone":     phone,
        "type":      user_type,
    }
    return request("PATCH", f"/partner/auth/users/{user_id}", json=payload)
