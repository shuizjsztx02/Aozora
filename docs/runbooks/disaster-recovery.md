# 灾难恢复 Runbook — T-M5-03b

**状态：** 已演练（2026-09-07）
**范围：** 服务端 PostgreSQL 数据库恢复
**RPO 目标：** ≤ 24h
**RTO 目标：** ≤ 4h
**关联：** ADR-005 / D-EXEC-02

---

## 1. 前提

- 最新备份文件（`aozora-*.dump`）存在于 `BACKUP_DIR`
- `pg_restore`、`createdb`、`dropdb`、`psql` 可用（PostgreSQL 18 客户端）
- 应用代码仓库已拉取至最新（含 Alembic 迁移脚本）
- 对账断言依赖 `contracts/fixtures/reconciliation/` 与 `server/tests/fixtures_reconciliation.py`（T-M5-01a）

## 2. 恢复步骤

### 步骤 1：停止 API 服务

```bash
docker compose stop api
```

确认：`docker compose ps` 显示 api 状态为 `exited`。

### 步骤 2：创建临时恢复数据库

```bash
createdb --host=localhost --port=5432 --username=aozora aozora_restore
```

### 步骤 3：恢复备份

```bash
# 定位最新备份（变量加引号，避免路径含空格）
BACKUP_DIR="${BACKUP_DIR:-./backups}"
LATEST="$(ls -t "${BACKUP_DIR}"/aozora-*.dump | head -1)"

# 校验 SHA-256（始终 cd 到备份所在目录，避免绝对/相对路径分支）
(cd "$(dirname "${LATEST}")" && sha256sum -c "$(basename "${LATEST}").sha256")

# 恢复
pg_restore \
  --host=localhost --port=5432 --username=aozora \
  --dbname=aozora_restore \
  --clean --if-exists \
  --no-owner --no-privileges \
  "${LATEST}"
```

确认：`pg_restore` 退出码为 0。

### 步骤 4：运行 Alembic 迁移

```bash
cd server
DATABASE_URL="postgresql+asyncpg://aozora:aozora@localhost:5432/aozora_restore" \
  alembic upgrade head
```

确认：输出 `Running upgrade -> <latest_revision>` 且退出码 0。

### 步骤 5：四方对账校验

导入 T-M5-01a fixture 数据并运行三端对账断言：

```bash
cd server
DATABASE_URL="postgresql+asyncpg://aozora:aozora@localhost:5432/aozora_restore" \
  pytest tests/fixtures_reconciliation.py -v
```

确认：5 个场景（basic / cross-month / tz-boundary / soft-delete / budget-mixed）全部 PASS。

> **注：** 若恢复的是生产数据而非 fixture，跳过 fixture 导入步骤，直接运行 verify-backup.sh 中的表结构断言。

### 步骤 6：切换数据库连接

```bash
# 备份当前数据库
pg_dump --host=localhost --port=5432 --username=aozora --dbname=aozora \
  --file=/tmp/aozora_pre_switch.dump

# 切换：删除旧库，重命名恢复库
dropdb --host=localhost --port=5432 --username=aozora aozora
psql --host=localhost --port=5432 --username=aozora -c \
  "ALTER DATABASE aozora_restore RENAME TO aozora;"
```

### 步骤 7：恢复服务

```bash
docker compose start api
```

确认：`curl http://localhost:8000/api/v1/health/ready` 返回 200。

## 3. 失败处置

| 步骤 | 失败场景 | 处置 |
|---|---|---|
| 3 | pg_restore 退出码非 0 | 尝试更早的备份文件（`ls -t backups/aozora-*.dump \| head -2`）；如全部失败，检查备份文件完整性 |
| 4 | Alembic 迁移失败 | 检查迁移脚本与备份版本兼容性；必要时手动修复 |
| 5 | 对账断言失败 | 数据损坏风险；不得上线，记录失败原因并通知维护者 |
| 6 | 切换失败 | 保留 aozora_restore 不动，从 pre_switch dump 还原原库 |

**总失败回退：** 如恢复过程无法继续，将 `DATABASE_URL` 指回原数据库（如有最近 pg_dump），并记录失败原因至下方演练记录。

## 4. 演练记录

### 演练 #1（隔离环境 · 2026-09-07）

**执行方式：** docker-compose 隔离环境（`tools/test-drill.sh` + `docker-compose.drill.yml`）

| 字段 | 值 |
|---|---|
| `start_time` | 2026-09-07T03:35:02Z |
| `end_time` | 2026-09-07T03:35:13Z |
| `restore_duration` | 5s |
| `total_duration` | 11s |
| `rpo_measured` | 7s（远小于 24h 目标） |
| `rto_measured` | 11s（远小于 4h 目标） |
| `checksum_match` | true（SHA-256 验证通过） |
| `table_count` | 16（≥ 14 业务表） |
| `backup_size` | 35,165 bytes（含种子数据：1 user / 1 book / 3 categories / 1 account / 4 transactions） |
| `pg_restore_exit` | 0 |
| `reconciliation_pass` | true（恢复后行数与种子数据一致） |
| `failures` | none |

**演练备注：**
- `ai_usage` 表按 ADR-005 决策排除数据（保留表结构）
- 迁移 0010 修复了 `transactions.user_id` 引用错误（改为 `books.owner_id` 路径）
- `alembic.ini` 中 em-dash 替换为 ASCII dash 以兼容 Windows configparser

### 演练节奏

| 类型 | 频率 | 说明 |
|---|---|---|
| 全量恢复演练 | 每季度 | 按本 runbook 完整执行 7 步，记录 RPO/RTO |
| 校验演练 | 每月 | 运行 `verify-backup.sh`，确认备份可恢复 |
| 告警演练 | 每季度 | 模拟 `touch $FAILURE_MARKER`，确认 Alertmanager 通知到达 |

## 5. 联系

- **备份负责人：** _D-EXEC-02 回填_
- **通知渠道：** _D-EXEC-02 回填_
