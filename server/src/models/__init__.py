"""T-M2-02 + T-M5-04c: SQLAlchemy model registry.

All models are re-exported here so Alembic can discover them via a
single import in ``env.py``.
"""

from src.models.admin import AdminAccount, AdminAuditLog
from src.models.ai import AIConversation, AIConversationEvent, AIProposalLink, AIUsage
from src.models.attachment import Attachment
from src.models.base import AuditMixin, Base, SoftDeleteMixin, VersionedMixin
from src.models.budget import Budget
from src.models.consent import UserConsent
from src.models.crash_report import CrashReport
from src.models.deletion import DeactivationToken, DeletionAudit
from src.models.export import Export
from src.models.feedback import Feedback
from src.models.ledger import Account, Book, Category, Transaction
from src.models.mutation import MutationIdempotency
from src.models.persona import ChatPersona
from src.models.proposal import ProposalIdempotency
from src.models.savings import SavingsGoal
from src.models.scheduled import ScheduledRule
from src.models.sync import SyncChange
from src.models.user import DeviceSession, User

# T-M7-05j: 偏好项上云。
from src.models.user_preference import SYNCABLE_KEYS, UserPreference

__all__ = [
    "AIConversation",
    "AIConversationEvent",
    "AIProposalLink",
    "AIUsage",
    "AdminAccount",
    "AdminAuditLog",
    "Account",
    "Attachment",
    "AuditMixin",
    "Base",
    "Book",
    "Budget",
    "Category",
    "ChatPersona",
    "CrashReport",
    "DeactivationToken",
    "DeletionAudit",
    "DeviceSession",
    "Export",
    "Feedback",
    "MutationIdempotency",
    "ProposalIdempotency",
    "SavingsGoal",
    "ScheduledRule",
    "SoftDeleteMixin",
    "SyncChange",
    "Transaction",
    "User",
    "UserConsent",
    "UserPreference",
    "SYNCABLE_KEYS",
    "VersionedMixin",
]
