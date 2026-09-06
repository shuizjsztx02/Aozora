"""T-M4-02: Proposal validation context loader.

The context is loaded from the database — NOT from model output. This is
the critical security boundary: the LLM may hallucinate category or
account IDs that are syntactically valid (UUID-shaped strings) but do
not belong to the current user or book. The context carries the set of
authorised IDs, and ``validate_proposal_with_context`` rejects anything
outside that set.

Data source of truth:
- ``categories``: scoped by ``owner_id`` AND ``book_id`` (per-book
  categories are the norm; nullable book_id means shared defaults).
- ``accounts``: scoped by ``owner_id`` only (accounts are global per
  user, not per book — see ``Account`` model).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.ledger import Account, Category


@dataclass(frozen=True)
class ProposalValidationContext:
    """Authorised entity IDs for an ActionProposal.

    Both sets are stringified UUIDs to match the ActionProposal contract
    (``categoryId: string`` / ``accountId: string``). Empty sets are
    legal — they reject any ID (useful when no book is selected yet).
    """

    category_ids: frozenset[str] = field(default_factory=frozenset)
    account_ids: frozenset[str] = field(default_factory=frozenset)


async def load_validation_context(
    db: AsyncSession,
    owner_id: uuid.UUID,
    book_id: uuid.UUID,
) -> ProposalValidationContext:
    """Load authorised category/account IDs for the given user+book.

    Categories: owned by user AND (book-scoped to this book OR global).
    Accounts: owned by user (accounts are not book-scoped).
    """
    cat_rows = await db.scalars(
        select(Category.id).where(
            Category.owner_id == owner_id,
            (Category.book_id == book_id) | (Category.book_id.is_(None)),
        ),
    )
    acc_rows = await db.scalars(
        select(Account.id).where(Account.owner_id == owner_id),
    )
    return ProposalValidationContext(
        category_ids=frozenset(str(cid) for cid in cat_rows.all()),
        account_ids=frozenset(str(aid) for aid in acc_rows.all()),
    )


def to_validator_dict(ctx: ProposalValidationContext) -> dict:
    """Adapt the context to the TS validator's ``ProposalValidationContext`` shape.

    The TS validator expects ``{ categoryIds: Set<string>, accountIds: Set<string> }``.
    This helper returns a plain-dict serialisation that can be passed via JSON.
    """
    return {
        "categoryIds": sorted(ctx.category_ids),
        "accountIds": sorted(ctx.account_ids),
    }
