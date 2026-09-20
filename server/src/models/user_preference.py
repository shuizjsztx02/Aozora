"""T-M7-05j: UserPreference model — 偏好项上云。

T-M7-01 把三实体（savings_goal / scheduled_rule / chat_persona）送上云；本模型
是 05j 的延续——把客户端 ``sync_meta`` 里**用户级的偏好键**（默认账本 / 默认账户
/ 列表排序 / 小组件配置 / 用户画像）也送上云，使 AI 能读写它们（此前 ``meta_set``
的执行位置在设备，服务端没有这些键的存储位置，见 ``tools.DEVICE_EXECUTED_OPS``
注释与 ``BridgeProtocol.kt`` 的 "不上行"）。

形态
----
**一键一行**：每个偏好键独立一行（``(user_id, key)`` 唯一），与 T-M7-01 的三
实体同模式——sync 的 ``entity_type = "user_preference"``、``entity_id`` 取自
行的 ``id``；一次 push 改一个键只产生一条 sync change。

与客户端 ``sync_meta`` 的关系
--------------------------
客户端 ``sync_meta`` 是**混合量**：既有上云的偏好键（``default_book_id`` /
``widget_config`` 等），也有设备自管量（``sync_cursor`` / ``master_book_id`` /
``budget_carryover_mark`` / ``personas_seeded`` / ``cloud_entities_uploaded_v1``）。
**只有前者在本表落一行**——白名单见 ``SYNCABLE_KEYS``。后者**不上云**（设备自管
量的语义与换机不符）。

删除语义
--------
**不使用** ``archived_at`` / ``deleted_at``：偏好键没有"归档"概念，AI 要改一个键
就是 ``update``；若要"清除"，直接删行（``delete`` sync 操作、客户端 pull 后本地
也清掉）。
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, VersionedMixin

#: **可上云的偏好键白名单**。客户端 ``sync_meta`` 的 14 个键里，只有这 6 个是用户级
#: 偏好（换机应跟着走）。其余 8 个是设备自管量（同步游标 / 总账本标记 / 幂等 mark /
#: 角色播种标记 / 上云迁移标记 / 头像 data URI / 昵称 / 预算沿用开关）——这些键的
#: 语义**只属于当前设备**，上云会让多设备语义互相覆盖。
#:
#: - ``default_book_id`` / ``default_account_id``：UI 的"默认选择"指针，跨设备一致
#:   符合直觉。AI 改默认项（``meta_set`` 的既有两键）走的就是这两行。
#: - ``book_order`` / ``account_order``：列表排序。用户在 A 设备排好的顺序，换到 B
#:   设备理应保留。
#: - ``widget_config`` / ``user_profile``：05d 把这两个键登记成 `meta_set` 的"扩
#:   展键"候选，现在它们上云后 AI 才改得了（05j 的真目标）。
SYNCABLE_KEYS: frozenset[str] = frozenset({
    "default_book_id",
    "default_account_id",
    "book_order",
    "account_order",
    "widget_config",
    "user_profile",
})


class UserPreference(Base, VersionedMixin):
    """一个用户级偏好键的云端权威。

    ``(user_id, key)`` 唯一（复合唯一约束，主键是 ``id``——sync 表的 ``entity_id``
    需要 UUID 类型）。AI / 表单 / 换机都写这同一张表，客户端 pull 后把值回灌进本地
    ``sync_meta`` 的同名键（镜像写入，保持既有读路径不变）。
    """

    __tablename__ = "user_preferences"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    # 值形态由客户端 ``MetaWhitelist.validateValue`` 约束（UUID / JSON 数组 /
    # JSON 对象 / 短字符串）；服务端只负责透传——存 ``Text`` 留够余量（data URI
    # 类偏好未来若加入白名单也不至于被截断）。
    value: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_user_preferences_user_key"),
        Index("ix_user_preferences_user_id", "user_id"),
    )
