# ADR-005 备份策略

**状态：** 已采纳（2026-09-07）
**范围：** T-M5-03

## 背景

AOZORA 服务端数据（用户、账本、流水、预算、附件元数据、AI 会话等）需要可靠的备份与恢复机制，满足：

- **RPO ≤ 24h** — 最多丢失一天数据
- **RTO ≤ 4h** — 从开始恢复到服务就绪不超过 4 小时
- **完整性可验证** — 定期恢复抽样并运行对账断言，而非仅信任备份文件存在

当前基础设施：PostgreSQL 18，数据量级为个人记账应用（单用户至数十用户），日增量有限。

约束：

- 生产基础设施参数待 D-EXEC-02 回填（ADR-001 / R-PROD-09），M5 阶段先在 docker-compose 隔离环境实现并演练
- 不引入额外中间件（WAL 归档需持续归档管道，超出当前运维复杂度预算）
- 备份不得泄露用户数据（加密或文件系统级保护）

## 候选方案

| 方案 | 优点 | 缺点 |
|---|---|---|
| **pg_dump 定时全量** | 实现简单；恢复只需 1 个文件；pg_restore 支持选择性恢复与校验 | 数据量大时备份窗口拉长（当前量级不构成问题） |
| WAL 归档（连续归档） | RPO 趋近于零 | 需持续归档管道（S3 + wal-g / barman）；恢复步骤多；运维复杂度高 |
| 文件系统快照（LVM / ZFS） | 秒级备份 | 与存储后端强耦合；跨平台不可移植；无法做逻辑校验 |

## 决策

采用 **pg_dump 定时全量备份**，理由：

1. 应用数据量级小（数十 MB 级），pg_dump 全量在分钟级完成
2. pg_restore 原生支持 `--list` / `--clean --if-exists`，恢复流程标准化
3. 无需引入额外中间件，与当前 docker-compose 部署模型一致
4. 运维团队（单人）可独立执行恢复，无复杂依赖

> **注（H7 修复）：** 早期草案曾考虑"全量 + 每日差异"混合策略，但当前量级下
> 差异备份收益极小（全量已 < 5 分钟）且显著增加恢复复杂度（需先恢复基线再叠加差异）。
> 如未来单库规模超过 1 GB 或 RPO 收紧至 < 1h，应重新评估 WAL 归档方案。

## 保留策略

| 类型 | 保留天数 | 说明 |
|---|---|---|
| 全量备份 | 7 天 | 每日 UTC 03:00 执行；文件名 `aozora-<YYYYMMDD-HHMMSS>.dump` |
| 异地副本 | 90 天 | 备份完成后 rsync/scp 到异地存储；docker-compose 环境模拟为本地另一目录 |

清理规则由 `infra/backup/cleanup.sh` 实现，使用 `find -mtime +6`（匹配 ≥ 7 天前修改的文件，对应 `-mtime +N` 的"严格大于 N 天"语义）。

## 备份窗口

- **开始：** 每日 UTC 03:00（UTC+8 上午 11:00，低峰）
- **预计耗时：** < 5 分钟（当前量级）
- **并发影响：** pg_dump 使用 `--no-owner --no-privileges`，不加锁，不阻塞写入

调度锚定 UTC 时刻，由 `infra/backup/backup-scheduler.sh` 计算到下一目标 UTC 时刻的秒数后 sleep，避免容器启动漂移。

## 备份命令

```bash
pg_dump \
  --host="${PGHOST}" --port="${PGPORT:-5432}" \
  --username="${PGUSER}" --dbname="${PGDATABASE}" \
  --format=custom \
  --compress=6 \
  --no-owner --no-privileges \
  --exclude-table-data='ai_usage' \
  --file="${BACKUP_DIR}/${BACKUP_FILENAME}"
```

`ai_usage` 表为 AI 调用计量统计，体积大且可重算，排除数据但保留表结构。

## 完整性校验

- 备份后计算 SHA-256 校验和（`.sha256` 文件）
- 可选：设置 `BACKUP_PASSPHRASE` 环境变量启用 openssl aes-256-cbc 对称加密
- `verify-backup.sh` 每日 04:00（UTC）恢复至抽样库 → 运行对账断言 → 清理
- 失败时通过 Alertmanager `BackupFailure` 规则通知维护者

## 监控指标

备份 / 校验脚本通过 `PROMETHEUS_TEXTFILE_DIR` 环境变量向该目录写入 `.prom` 文件，由 node_exporter textfile-collector 采集。指标定义见 `infra/monitoring/alert-rules.yml`：

| 指标 | 来源 |
|---|---|
| `backup_last_success_timestamp_seconds` | backup.sh |
| `backup_last_exit_code` | backup.sh |
| `backup_files_count` | backup.sh + cleanup.sh |
| `backup_verify_exit_code` | verify-backup.sh |

## 异地副本

- 生产环境：rsync/scp 到异地存储（D-EXEC-02 回填具体端点）
- docker-compose 环境：复制到 `${OFFSITE_DIR}`（默认 `/backups/offsite`）

## 风险

- pg_dump 期间数据库负载略增（CPU / IO），但对个人记账应用量级可忽略
- 加密 passphrase 泄露 = 备份数据泄露 → 生产环境需通过 secret manager 管理
- 如备份文件传输中损坏，SHA-256 校验和会在恢复时或 verify-backup.sh 中捕获

## 待回填（D-EXEC-02）

详见 `infra/backup/production-params.md`。最晚 T-M6-01a 前由账户所有者提供：

- 生产 S3 端点 / 密钥 / 地域
- 备份保留天数确认
- 异地副本地址
- 通知渠道
