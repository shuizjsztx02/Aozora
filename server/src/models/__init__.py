"""T-M2-02: SQLAlchemy model registry.

All models are re-exported here so Alembic can discover them via a
single import in ``env.py``.
"""

from src.models.base import Base, AuditMixin, SoftDeleteMixin, VersionedMixin
from src.models.user import User, DeviceSession
from src.models.ledger import Book, Category, Account, Transaction
from src.models.budget import Budget
from src.models.attachment import Attachment
from src.models.ai import AIConversation, AIUsage
from src.models.sync import SyncChange

__all__ = [
    "Base",
    "AuditMixin",
    "SoftDeleteMixin",
    "VersionedMixin",
    "User",
    "DeviceSession",
    "Book",
    "Category",
    "Account",
    "Transaction",
    "Budget",
    "Attachment",
    "AIConversation",
    "AIUsage",
    "SyncChange",
]
