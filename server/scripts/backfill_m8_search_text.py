"""M8-03：回填 ``ai_conversations.search_text``（会话检索面）。

迁移 ``0024_m8_admin_console`` 只加列、不回填——回填要扫全量事件，放在迁移里
会让 schema 变更被数据量绑架。本脚本按事件日志**整段重算**，因此：

- **幂等**：重复跑结果一致，可断点重跑。
- 与写入路径共用 ``conversation._truncate_search_text``，上限口径不漂移。
- 只收 ``user`` / ``assistant`` 正文；``tool`` 结果与推理内容不进检索面。

用法（在任意目录）：

    python server/scripts/backfill_m8_search_text.py

依赖 ``DATABASE_URL``（与 API 相同）。
"""

from __future__ import annotations

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
from src.models.ai import AIConversation  # noqa: E402
from src.modules.ai.conversation import rebuild_search_text  # noqa: E402

#: 每批会话数。单批一个事务，避免全量长事务把连接池占满。
BATCH_SIZE = 200


async def main() -> None:
    cfg = get_config()
    engine = create_async_engine(cfg.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        conversation_ids = list(
            (await session.execute(select(AIConversation.id))).scalars().all()
        )
    print(f"conversations to backfill: {len(conversation_ids)}")

    updated = 0
    for start in range(0, len(conversation_ids), BATCH_SIZE):
        batch = conversation_ids[start:start + BATCH_SIZE]
        async with factory() as session:
            updated += await rebuild_search_text(session, batch)
            await session.commit()
        print(f"  {min(start + BATCH_SIZE, len(conversation_ids))}"
              f"/{len(conversation_ids)} done")

    print(f"search_text rebuilt: {updated} conversations")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
