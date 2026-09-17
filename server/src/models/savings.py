"""T-M7-01: SavingsGoal model — 存钱目标上云（客户端 savings_goals 表的云端权威）。

字段对齐客户端 ``SavingsGoalEntity``（app/src/main/java/com/aozora/app/db/entity/
SavingsGoalEntity.kt）：日期类字段客户端以 epoch 毫秒承载，服务端不落库该列
（created_at/updated_at 由 AuditMixin 自管，push payload 不含时间戳键）。

删除语义＝归档（``archived_at``），镜像 Category/Account；sync 的 ``delete``
操作对归档型实体置 ``archived_at``（见 modules/sync/service.py ``_ARCHIVED_TYPES``）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import AuditMixin, Base, VersionedMixin


class SavingsGoal(Base, AuditMixin, VersionedMixin):
    __tablename__ = "savings_goals"
    __table_args__ = (
        CheckConstraint("target_cents > 0", name="ck_savings_goals_target_positive"),
        CheckConstraint("current_cents >= 0", name="ck_savings_goals_current_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    target_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_cents: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False,
    )
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # T9-4/REQ-14：存款期限。unit 仅 'week' | 'month'，value/unit 必须成对（应用层校验）。
    period_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    period_unit: Mapped[str | None] = mapped_column(String(8), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
