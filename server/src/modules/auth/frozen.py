"""T-M5-04c: shared guard that rejects frozen users on data-plane endpoints.

A user in the 7-day deletion grace window must not create, mutate or
consume server-side resources (AI calls, exports, attachments, sync
pushes). GET /me and POST /me/deletion-cancel are deliberately not
guarded — they are how the user checks / revokes the deletion.

Endpoints that prefer to return empty data rather than 403 (e.g.
``/sync/pull``, ``/stats/*``) should use ``is_frozen`` instead.
"""

from __future__ import annotations

from src.common.errors import AppException
from src.models.user import User, UserStatus


def require_active(user: User) -> None:
    """Raise 403 ``account_frozen`` if *user* is pending deletion."""
    if is_frozen(user):
        raise AppException(
            403, "account_frozen",
            "Account is frozen pending deletion; "
            "data-plane operations are disabled",
        )


def is_frozen(user: User) -> bool:
    """Return True if *user* is in the deletion grace window.

    Checks BOTH ``status`` AND ``frozen_at`` so a row that only got one of
    the two fields set (a partial migration, a manual ``UPDATE`` that
    forgot to flip the other column, etc.) still fails closed. Using a
    single shared check for every guard (sync push / bootstrap / pull /
    stats / ai / exports / attachments) keeps the semantics consistent.
    """
    return (
        user.status == UserStatus.FROZEN
        or user.frozen_at is not None
    )
