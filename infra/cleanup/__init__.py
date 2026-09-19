"""infra/cleanup — 周期性清理脚本（T-M5-04c 等）。

- ``account-deletion.py`` — 7 天后物理删除已冻结用户
- ``attachment-expiry.py`` — 解析后 24h 删除原图

建议 cron 配置（docker-compose 环境）：
```
30 4 * * * docker compose exec -T api python -m infra.cleanup.account-deletion
0 * * * * docker compose exec -T api python -m infra.cleanup.attachment-expiry
```

**注意：上面这两条目前只是「建议」——生产部署（`docker-compose.prod.yml` /
`RUNBOOK.md` / `a-deploy.sh`）没有任何 cron 或 systemd timer 接线**，即这两个
脚本在生产不会自动跑（见 `docs/risk-register.md` R-PROD-25）。

**会话事件日志的保留期（T-M7-04d）刻意不在这里**——它是**进程内任务**
（`server/src/modules/ai/retention.py`，随 API 生命周期启停，开关
`AI_CONVERSATION_RETENTION_ENABLED`），正因为本目录的脚本没有调度接线才没有
依赖 cron。改动保留期口径前先读 `docs/architecture/security.md`。
"""
