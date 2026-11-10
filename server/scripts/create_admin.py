"""M8-04：创建运营后台管理员账号。

管理员不属于 ``users``（架构 §6）：独立的 ``admin_accounts`` 表 + 独立 JWT。
密码用标准库 ``hashlib.scrypt`` 加盐哈希（``modules/admin/dependencies``），
本脚本不落明文。

**幂等拒绝重复用户名**：已存在时退出码 1，不覆盖密码（改密码是另一件事，
不做隐式覆盖）。

用法（在任意目录）：

    python server/scripts/create_admin.py <username> <password>

依赖 ``DATABASE_URL``（与 API 相同）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)

from src.common.config import get_config  # noqa: E402
from src.models.admin import AdminAccount  # noqa: E402
from src.modules.admin.dependencies import hash_admin_password  # noqa: E402

#: 与 ``admin_accounts.username`` 的列宽一致。
USERNAME_MAX_CHARS = 64


async def create_admin(username: str, password: str) -> int:
    """返回退出码：0 成功 / 1 已存在。"""
    cfg = get_config()
    engine = create_async_engine(cfg.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            existing = await session.scalar(
                select(AdminAccount.id).where(AdminAccount.username == username)
            )
            if existing is not None:
                print(
                    f"admin account already exists: {username}", file=sys.stderr,
                )
                return 1
            session.add(AdminAccount(
                username=username,
                password_hash=hash_admin_password(password),
                status="active",
            ))
            await session.commit()
    finally:
        await engine.dispose()

    print(f"admin account created: {username}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create an Aozora admin console account",
    )
    parser.add_argument("username", help="admin username (<= 64 chars)")
    parser.add_argument("password", help="admin password (plaintext input)")
    args = parser.parse_args()

    username = args.username.strip()
    if not username or len(username) > USERNAME_MAX_CHARS:
        print(
            f"username must be 1..{USERNAME_MAX_CHARS} characters", file=sys.stderr,
        )
        return 2
    if not args.password:
        print("password must not be empty", file=sys.stderr)
        return 2

    return asyncio.run(create_admin(username, args.password))


if __name__ == "__main__":
    raise SystemExit(main())
