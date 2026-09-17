"""T-M7-01: ScheduledRule model — 定时记账规则上云（第十一轮 REQ-26 / 决策 #4）。

字段对齐客户端 ``ScheduledRuleEntity``（app/src/main/java/com/aozora/app/db/entity/
ScheduledRuleEntity.kt）。规则**生成的交易**走 transactions 表（source='scheduled'），
规则本身只保存"何时/记什么"的参数。

``last_run_date`` 是引擎的当日幂等锚点。上云后多设备共享该值：设备 A 生成后上行，
设备 B pull 到即不再重复生成——这是期望语义（跨设备去重），而非数据污染。

删除语义＝归档（``archived_at``），同 SavingsGoal。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import AuditMixin, Base, VersionedMixin


class ScheduledRule(Base, AuditMixin, VersionedMixin):
    __tablename__ = "scheduled_rules"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="ck_scheduled_rules_amount_positive"),
        CheckConstraint("run_hour >= 0 AND run_hour <= 23", name="ck_scheduled_rules_run_hour"),
        CheckConstraint("run_minute >= 0 AND run_minute <= 59", name="ck_scheduled_rules_run_minute"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    book_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("books.id", ondelete="RESTRICT"),
        nullable=False,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=False,
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    include_in_budget: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False,
    )
    # 规则备注与规则名拼接成生成交易的 note（"名称｜备注"），故上限同 transactions.note。
    note: Mapped[str] = mapped_column(
        String(2000), default="", server_default="", nullable=False,
    )
    # 'daily' | 'weekly' | 'monthly' | 'yearly'（应用层校验仅此四值）
    repeat: Mapped[str] = mapped_column(String(10), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False,
    )
    last_run_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # REQ-43：每日执行时刻（墙钟），缺省 08:00。
    run_hour: Mapped[int] = mapped_column(
        Integer, default=8, server_default="8", nullable=False,
    )
    run_minute: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False,
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
