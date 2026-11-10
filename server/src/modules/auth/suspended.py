"""M8-05: shared guard that rejects administrator-suspended users on writes.

暂停标记是 ``users.admin_suspended_at``——**刻意不复用 ``status = FROZEN``**
（``infra/cleanup/account-deletion.py`` 会物理删除 ``status=FROZEN AND
frozen_at <= now - 7d`` 的用户，后台若借用该状态，暂停 7 天后数据会被清掉）。

语义（`docs/architecture/admin-console.md` §8.1）：

- 写操作与同步 push 拒绝，403 ``account_suspended``；
- 读路径放行（会话 / 流水 / 导出列表等全部照常），数据完整保留；
- 解冻即清空两列，无需恢复任何业务行。

**为什么不做进 ``get_current_user``：** 同一批 C 端路由里读写混排（``GET
/conversations`` 与 ``DELETE /conversations/{id}`` 共用鉴权），在鉴权依赖里
一刀切会把读也拒掉。与 :mod:`src.modules.auth.frozen` 同构：写端点显式调用
本守卫，读端点不调用。

**刻意不覆盖的写端点**（不是数据面写入，暂停不阻断用户权利）：

- ``/auth/*``（登录 / 刷新 / 登出）——暂停用户仍要能登录后读数据；
- ``/me/deletion-*`` 与 ``DELETE /me``——用户注销自己账号的权利不受暂停影响；
- ``/consent``（无鉴权，注册前记录）与 ``/telemetry/*``（旁路埋点）。
"""

from __future__ import annotations

from src.common.errors import AppException
from src.models.user import User


def is_suspended(user: User) -> bool:
    """Return True if an administrator suspended *user*."""
    return user.admin_suspended_at is not None


def require_not_suspended(user: User) -> None:
    """Raise 403 ``account_suspended`` if *user* is administratively suspended."""
    if is_suspended(user):
        raise AppException(
            403, "account_suspended",
            "Account is suspended by an administrator; "
            "write operations are disabled",
        )
