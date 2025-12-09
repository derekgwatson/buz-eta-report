# services/peter_api.py
"""
Integration with Peter API for staff verification.

Peter is the internal staff directory bot. This module checks if a Google
OAuth email belongs to an active staff member.
"""
from __future__ import annotations

import os
import logging
from typing import TypedDict

import requests

logger = logging.getLogger(__name__)

# Default URL - can be overridden by environment variable
DEFAULT_PETER_URL = "https://peter.watsonblinds.com.au"


def _get_peter_url() -> str:
    """Get Peter API URL from environment."""
    return os.environ.get("PETER_URL", DEFAULT_PETER_URL)


def _get_bot_api_key() -> str | None:
    """Get bot API key from environment."""
    return os.environ.get("BOT_API_KEY")


class StaffCheckResult(TypedDict):
    approved: bool
    name: str | None
    email: str


def is_staff_member(email: str, timeout: int = 10) -> StaffCheckResult:
    """
    Check if an email belongs to an active staff member via Peter API.

    Peter checks the email against:
    - google_primary_email
    - work_email (may be an alias)
    - personal_email (for external contractors)

    Args:
        email: The email address to check (from Google OAuth)
        timeout: Request timeout in seconds

    Returns:
        {"approved": True, "name": "...", "email": "..."} if staff
        {"approved": False, "name": None, "email": "..."} if not staff

    Raises:
        requests.RequestException: If Peter API is unavailable
        ValueError: If BOT_API_KEY is not configured
    """
    api_key = _get_bot_api_key()
    if not api_key:
        raise ValueError("BOT_API_KEY environment variable is not configured")

    peter_url = _get_peter_url()

    try:
        response = requests.get(
            f"{peter_url}/api/is-approved",
            params={"email": email},
            headers={"X-API-Key": api_key},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()

        return StaffCheckResult(
            approved=data.get("approved", False),
            name=data.get("name"),
            email=data.get("email", email),
        )
    except requests.RequestException as exc:
        logger.warning("Peter API request failed for %s: %s", email, exc)
        raise


def check_staff_with_fallback(email: str, fail_open: bool = False) -> StaffCheckResult:
    """
    Check staff status with configurable failure behavior.

    Args:
        email: The email address to check
        fail_open: If True, treat API failures as approved (less secure).
                   If False (default), treat API failures as not approved.

    Returns:
        StaffCheckResult with approved status
    """
    try:
        return is_staff_member(email)
    except (requests.RequestException, ValueError) as exc:
        logger.error("Staff check failed for %s: %s (fail_open=%s)", email, exc, fail_open)
        if fail_open:
            # Fail open - allow access when Peter is unavailable
            # Use this only if availability is more important than security
            return StaffCheckResult(approved=True, name=None, email=email)
        else:
            # Fail closed - deny access when Peter is unavailable (more secure)
            return StaffCheckResult(approved=False, name=None, email=email)
