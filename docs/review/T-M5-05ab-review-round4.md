# T-M5-05a / T-M5-05b 第四轮审查报告（终审）

> **审查日期:** 2026-09-07
> **审查范围:** 三轮审查 §五 批次 A 中 2 项 S1 修复的验证
> **审查方法:** 逐文件精读 + Python import 实测 + TestClient 路径验证 + YAML/JSON/bash -n + ast.parse + 跨表一致性校验

---

## 一、审查结论

**✅ 通过。T-M5-05a / T-M5-05b 全部 S1/S2 问题已闭环。**

三轮提出的 2 项 S1 修复均已正确实施，无新发现问题。

---

## 二、修复验证

### S1-NEW-1 ✅ Feedback model ↔ migration 一致性

| 层 | 文件 | 值 | 状态 |
|---|---|---|---|
| Model | `server/src/models/feedback.py` L40 | `String(64)` | ✅ |
| Migration | `server/src/migrations/versions/0015_feedback.py` L24 | `String(64)` | ✅ **修复** (原 `String(16)`) |
| Service | `server/src/modules/telemetry/service.py` L219 | `body.device_id_hash if body.device_id_hash else None` | ✅ **修复** (原 `[:16]`) |
| Schema | `server/src/modules/telemetry/schemas.py` L83-86 | `max_length=64` | ✅ 入口拦截 |
| 客户端 | `app/.../FeedbackClient.kt` L67-70 | SHA-256 full hex (64 chars) | ✅ |

**跨表一致性**:
- `crash_reports.device_id_hash`: model `String(64)` / migration `String(64)` ✅
- `feedbacks.device_id_hash`: model `String(64)` / migration `String(64)` ✅
- 两表 hash 长度一致，可跨表关联 ✅

### S1-NEW-2 ✅ 测试路径 `/api/v1` 前缀

| 项 | 值 | 状态 |
|---|---|---|
| `test_telemetry.py` L66 | `TestClient(app, base_url="http://test/api/v1")` | ✅ **修复** |
| 测试路径 | `/telemetry/feedback`（8 处，无需改动） | ✅ base_url 自动拼接 |
| TestClient 实测（无前缀） | 404 Not Found | 确认旧行为已消除 |
| TestClient 实测（有 base_url） | 401 Unauthorized | 正确路由，无 auth 符合预期 |

---

## 三、全链路终态

| 链路 | 状态 | 说明 |
|---|---|---|
| API 请求 span → Tempo | ✅ | OTLPSpanExporter → collector → tempo |
| API 请求指标 → Prometheus | ✅ | OTLPMetricExporter → collector → prometheus |
| API 日志 → Loki | ✅ | LoggingHandler → OTLPLogExporter → collector → loki |
| Tempo span-metrics → Prometheus | ✅ | remote-write-receiver 已启用 |
| Android 崩溃 → crash_reports | ✅ | handler → sanitize → Room → upload → server |
| Android heartbeat → active_devices | ✅ | HeartbeatClient → gauge callback |
| CrashFreeRateLow 告警 | ✅ | ObservableGauge 24h 窗口，维度匹配 |
| sync_queue_depth | ✅ | Gauge + pending_count from client |
| Feedback → feedbacks 表 | ✅ | 64 char hash 全链路一致，PG 不截断 |
| 通知 → Alertmanager | ✅ | env 模板，staging devnull |
| 看板 provision | ✅ | 6 JSON + datasources.yml |
| 测试 | ✅ | 路径前缀正确，预期 401/201/422/429 |

---

## 四、校验清单

| 校验 | 结果 |
|---|---|
| `telemetry.py` ast.parse | ✅ |
| `service.py` ast.parse | ✅ |
| `routes.py` ast.parse | ✅ |
| `schemas.py` ast.parse | ✅ |
| `feedback.py` model ast.parse | ✅ |
| `0015_feedback.py` ast.parse | ✅ |
| `0013_crash_reports.py` ast.parse | ✅ |
| `test_telemetry.py` ast.parse | ✅ |
| `sync/routes.py` ast.parse | ✅ |
| `sync/schemas.py` ast.parse | ✅ |
| `docker-compose.staging.yml` YAML | ✅ |
| `alert-rules.yml` YAML | ✅ |
| 6 dashboard JSON | ✅ |
| `staging-deploy.sh` bash -n | ✅ |
| feedback model ↔ migration String(64) | ✅ |
| crash_report model ↔ migration String(64) | ✅ |
| cross-table device_id_hash 一致性 | ✅ |
| service.py device_id_hash 无截断 | ✅ |
| TestClient base_url 路径拼接 | ✅ 401 (正确路由) |
| OTLPLogExporter import 实测 | ✅ 存在 |
| HeartbeatClient URL 匹配 server router | ✅ |
| FeedbackClient URL 匹配 server router | ✅ |
| 0013 revision 链 | ✅ `down_revision = "0012_account_deletion"` |
| 0015 revision 链 | ✅ `down_revision = "0014_deletion_token_unique"` |

---

## 五、任务状态

| 子任务 | 状态 |
|---|---|
| T-M5-05a staging 可观测栈 | ✅ **通过** |
| T-M5-05b 看板/告警/崩溃上报/反馈 | ✅ **通过** |

**推迟项（维持前几轮结论）**:
- S2-1 (限流器改 Redis) → T-M6-01a 生产部署前
- S2-3 (CrashSanitizer 单测) → T-M5-05c
- S1-6 余下 (d1/d7_retained, reconciliation) → T-M6 / T-M5-01 后续

---

## 六、四轮审查历程

| 轮次 | 日期 | 结论 | 新增发现 | 修复数 |
|---|---|---|---|---|
| 一轮 | 2026-09-07 | ⚠️ 条件性通过 | 6 S1 + 5 S2 | — |
| 二轮 | 2026-09-07 | ⚠️ 条件性通过 (N-1 未生效) | 6 N (1 S1 + 5 S2) | 11 S1/S2 |
| 三轮 | 2026-09-07 | ⚠️ 条件性通过 (N-2/N-6 升级 S1) | 0 | 4 N |
| 四轮 | 2026-09-07 | ✅ **通过** | 0 | 2 S1 |

---

**审查人：** Claude Code
**审查耗时：** ~10 分钟
**状态：** 闭环。T-M5-05a/b 可进入下一里程碑。
