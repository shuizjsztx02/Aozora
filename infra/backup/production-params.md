# 生产备份参数 — D-EXEC-02 回填清单

**状态：** 待外部确认（pending external input）
**最晚确认时点：** T-M6-01a 前
**关联：** ADR-001 / ADR-005 / R-PROD-09

> 本清单由账户所有者在外部控制台确认后回填。不得在仓库内虚构参数。

## 存储

| 参数 | 说明 | 值 |
|---|---|---|
| `S3_ENDPOINT` | S3 兼容存储端点 | _待填_ |
| `S3_BUCKET` | 备份存储桶名称 | _待填_ |
| `S3_REGION` | 存储桶地域 | _待填_ |
| `S3_ACCESS_KEY` | 访问密钥（通过 secret manager 引用，不在此填写明文） | _待填_ |
| `S3_SECRET_KEY` | 秘密密钥（通过 secret manager 引用，不在此填写明文） | _待填_ |

## 保留策略

> 词汇对照：脚本使用 `RETENTION_LOCAL_DAYS` / `RETENTION_OFFSITE_DAYS`；
> 本表沿用同义词 `RETENTION_FULL_DAYS` 表达"全量备份保留天数"。
> ADR-005 仅承诺全量备份（无差异备份），因此 `RETENTION_DIFF_DAYS` 已不再适用。

| 参数 | 说明 | 值 |
|---|---|---|
| `RETENTION_FULL_DAYS` | 全量备份保留天数（ADR-005 默认 7；脚本变量 `RETENTION_LOCAL_DAYS`） | _待确认_ |
| `RETENTION_OFFSITE_DAYS` | 异地副本保留天数（ADR-005 默认 90） | _待确认_ |

## 异地副本

| 参数 | 说明 | 值 |
|---|---|---|
| `OFFSITE_HOST` | 异地存储主机 | _待填_ |
| `OFFSITE_PATH` | 异地存储路径 | _待填_ |
| `OFFSITE_USER` | rsync/scp 用户 | _待填_ |
| `OFFSITE_METHOD` | 传输方式（rsync / scp / s3） | _待填_ |

## 加密

| 参数 | 说明 | 值 |
|---|---|---|
| `BACKUP_ENCRYPTION` | 是否启用备份加密（true / false） | _待确认_ |
| `BACKUP_PASSPHRASE_REF` | 加密密钥在 secret manager 中的引用路径 | _待填_ |

## 通知

| 参数 | 说明 | 值 |
|---|---|---|
| `ALERTMANAGER_URL` | Alertmanager 端点 | _待填_ |
| `NOTIFICATION_CHANNEL` | 通知渠道（email / slack / webhook） | _待填_ |
| `NOTIFICATION_RECIPIENTS` | 接收者列表 | _待填_ |

## 监控盲区

> 当前告警规则（`infra/monitoring/alert-rules.yml`）通过 node_exporter textfile-collector 消费脚本写入的 `.prom` 文件。以下场景不在告警覆盖范围内，需在 T-M5-05 可观测栈扩展时补齐：

| 盲区 | 说明 | 建议补充 |
|---|---|---|
| 节点 / 容器不可达 | 当节点 down 或 backup 容器挂掉时，textfile 不可读，`BackupStale` 规则 no-data 不触发 | 容器 up metric / restart count；或新增 `absent(backup_last_exit_code)` 持续 10min → warning 规则 |
| textfile 目录挂载丢失 | `PROMETHEUS_TEXTFILE_DIR` 挂载失效时指标停写，告警按旧时间戳判断 | 磁盘 / 挂载健康检查 |
| 时钟漂移 | 脚本使用 `date +%s` 写入时间戳，节点时钟漂移会直接影响 `BackupStale` 判断 | NTP 同步告警 |

**当前缓解：** backup 容器使用 `restart: unless-stopped` 策略，容器级崩溃会由 Docker 自动重启；节点级故障依赖外部监控（云厂商 / 独立探针）。

## 回填方式

1. 账户所有者在外部控制台确认上述参数
2. 更新本表对应行的"值"列
3. 将敏感参数写入 secret manager（不在此文件留明文）
4. 同步更新 `docs/adr/ADR-001-infrastructure-baseline.md` 关闭 D-EXEC-02
5. 同步更新 `docs/risk-register.md` 关闭 R-PROD-09
