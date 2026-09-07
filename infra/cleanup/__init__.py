"""infra/cleanup — 周期性清理脚本（T-M5-04c 等）。

- ``account-deletion.py`` — 7 天后物理删除已冻结用户
- ``attachment-expiry.py`` — 解析后 24h 删除原图

建议 cron 配置（docker-compose 环境）：
```
30 4 * * * docker compose exec -T api python -m infra.cleanup.account-deletion
0 * * * * docker compose exec -T api python -m infra.cleanup.attachment-expiry
```
"""
