"""T-M2-02: SQLAlchemy model registry.

All models are re-exported here so Alembic can discover them via a
single import in ``env.py``.
"""

from src.models.ai import AIConversation, AIUsage
from src.models.attachment import Attachment
from src.models.base import AuditMixin, Base, SoftDeleteMixin, VersionedMixin
from src.models.budget import Budget
from src.models.export import Export
from src.models.ledger import Account, Book, Category, Transaction
from src.models.mutation import MutationIdempotency
from src.models.proposal import ProposalIdempotency
from src.models.sync import SyncChange
from src.models.user import DeviceSession, User

__all__ = [
    "AIConversation",
    "AIUsage",
    "Account",
    "Attachment",
    "AuditMixin",
    "Base",
    "Book",
    "Budget",
    "Category",
    "DeviceSession",
    "Export",
    "MutationIdempotency",
    "ProposalIdempotency",
    "SoftDeleteMixin",
    "SyncChange",
    "Transaction",
    "User",
    "VersionedMixin",
]
