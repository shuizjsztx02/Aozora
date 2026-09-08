# T-M5-05a / T-M5-05b 第三轮审查报告

> **审查日期:** 2026-09-07
> **审查范围:** 基于二轮审查（§十）提出的 6 项新问题（N-1 ~ N-6）+ 全局链路复验
> **审查方法:** 逐文件精读 + Python import 实测 + TestClient 路径验证 + YAML/JSON/bash -n + ast.parse
> **基线:** 二轮审查报告（§十，文档第 401–571 行）

---

## 一、审查结论

**总体状态：⚠️ 条件性通过（维持二轮结论），新增 1 个 S1 + 1 个 S2 需修复。**

二轮审查发现 6 项问题，本轮验证结果：

| 编号 | 严重度 | 状态 | 说明 |
|---|---|---|---|
| N-1 (S1-1 类名) | S1 | ✅ **已修复** | `OTLPLogExporter` 类名正确，import 实测通过，Loki 链路已通 |
| N-2 (feedback hash) | S2 | ⬆️ **升级为 S1** | model 与 migration 列定义不一致，运行时必崩 |
| N-3 (冗余 import) | cosmetic | ✅ **已修复** | `routes.py` 函数内冗余 `import hashlib` 已删除 |
| N-4 (heartbeat scope) | S2 | ✅ **已修复** | `AozoraApp.heartbeatScope` 提升为类成员 |
| N-5 (app_version) | S2 | ✅ **已修复** | `_active_devices` 存 `(timestamp, app_version)`，gauge 回调输出 label |
| N-6 (测试路径) | S2 | ⬆️ **升级为 S1** | 全量测试 100% 失败，非"可能"而是"必定" 404 |

---

## 二、修复验证总表

### 2.1 N-1 ✅ 已修复 — `OTLPLogExporter` 类名修正

- **`telemetry.py` L36-43**: import 块完整正确
  ```python
  from opentelemetry._logs import set_logger_provider
  from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter as _OTLPLogExporterCls
  from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
  from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
  _OTLPLogExporter = _OTLPLogExporterCls
  ```
- **实测**: `OTLPLogExporter` ✅ / `OTLPLogHandler` ❌（ImportError）/ `LoggerProvider + LoggingHandler` ✅ / `set_logger_provider` ✅ / `BatchLogRecordProcessor` ✅
- **`telemetry.py` L130-143**: 完整链路 `aozora logger → LoggingHandler → LoggerProvider → BatchLogRecordProcessor → OTLPLogExporter → OTel Collector → Loki`
- **链路一（修复后）**: API 日志 → Loki **✅ 已通**

### 2.2 N-2 ❌ **升级为 S1** — Feedback model 与 migration 列定义不一致

| 文件 | 位置 | 值 | 问题 |
|---|---|---|---|
| `server/src/models/feedback.py` | L40 | `String(64)` | model 声明 64 字符 |
| `server/src/migrations/versions/0015_feedback.py` | L24 | `String(16)` | migration 建表 16 字符 |
| `server/src/modules/telemetry/service.py` | L219 | `[:16]` | 截断为 16 字符 |
| `app/.../FeedbackClient.kt` | L67-70 | SHA-256 full hex | 客户端发送 64 字符 |

**运行时后果**：
1. Alembic 迁移后 DB 列为 `VARCHAR(16)`
2. SQLAlchemy model 声明 `String(64)` — 不报错（SQLAlchemy 不校验列宽）
3. SQLite：VARCHAR(16) 不强制截断，数据完整存储 64 字符，但**与 production PostgreSQL 行为不同**
4. **PostgreSQL**: `VARCHAR(16)` 严格截断或报错（`value too long for type character varying(16)`），feedback 入库必崩
5. `crash_reports.device_id_hash` = 64 字符，`feedbacks.device_id_hash` = 16 字符 — **同一设备在两表 hash 不同**，跨表关联失效

**修复**：三处统一：
1. `0015_feedback.py` L24: `String(16)` → `String(64)`
2. `service.py` L219: `body.device_id_hash[:16]` → `body.device_id_hash`（或 `[:64]`）
3. （可选）`service.py` L218: `user_id_hash[:16]` 保持不变（user_id_hash 设计就是 16 字符，与 feedback model L38 `String(16)` 一致）

### 2.3 N-3 ✅ 已修复 — 冗余 import 已删除

`routes.py` L82-88 heartbeat 函数内不再有 `import hashlib`，模块级 L18 的 import 被正确使用。

### 2.4 N-4 ✅ 已修复 — heartbeat scope 提升

`AozoraApp.kt` L44: `private val heartbeatScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)`
L202: `heartbeatScope.launch { ... }` — 与 `CrashReporter.uploadScope` (S2-7) 对齐。

### 2.5 N-5 ✅ 已修复 — app_version 作为 label 输出

- `telemetry.py` L55: `_active_devices: dict[str, tuple[float, str]]` — 存储 `(timestamp, app_version)`
- `telemetry.py` L75-76: ObservableGauge callback `yield Observation(1, {"device_id": device_id, "app_version": app_version})`
- `telemetry.py` L389: `record_active_device` 存储 `app_version` 到 dict
- **Grafana "Version Distribution" 面板**：有数据源 ✅

### 2.6 N-6 ❌ **升级为 S1** — 测试路径缺 `/api/v1` 前缀

**实测确认**：
```
POST /telemetry/feedback        → 404 Not Found
POST /api/v1/telemetry/feedback → 401 Unauthorized (正确：无 auth header)
```

`test_telemetry.py` 中 8 个测试用例全部使用 `/telemetry/feedback`（无 `/api/v1` 前缀）：
- L111: `client.post("/telemetry/feedback", ...)`
- L144: `client.post("/telemetry/feedback", ...)`
- L154: `client.post("/telemetry/feedback", ...)`
- L162: `client.post("/telemetry/feedback", ...)`
- L172: `client.post("/telemetry/feedback", ...)`
- L182: `client.post("/telemetry/feedback", ...)`
- L194: `client.post("/telemetry/feedback", ...)` × 2
- L232: `client.post("/telemetry/feedback", ...)`

**后果**: CI 中 8/8 测试返回 404，**100% 失败**。不是边界条件问题而是路径根本错误。

**修复（二选一）**:
1. 所有路径加 `/api/v1` 前缀
2. `TestClient(app, base_url="http://test/api/v1")` 然后测试用 `/telemetry/feedback`

---

## 三、完整链路复验（修复后终态）

### 3.1 链路一：API 日志 → Loki ✅

```
logging.getLogger("aozora") → LoggingHandler → LoggerProvider
  → BatchLogRecordProcessor → OTLPLogExporter (grpc → otel-collector:4317)
  → collector logs pipeline → loki exporter → loki:3100
  → Grafana Loki 数据源 ✅
```

### 3.2 链路二：API 请求指标 → Prometheus ✅

```
main.py @log_requests → record_request()
  → OTLPMetricExporter → otel-collector → prometheus exporter :8889
  → prometheus.yml job "aozora-api" scrape
  → Prometheus 30d ✅
```

### 3.3 链路三：Tempo span-metrics → Prometheus ✅

```
tempo metrics_generator → remote_write → prometheus:9090/api/v1/write
  → --web.enable-remote-write-receiver ✅ (docker-compose L108)
```

### 3.4 链路四：Android 崩溃 → crash_reports ✅

```
CrashReporter.install() → captureSync → CrashSanitizer → Room DB
  → 下次启动 uploadPending → POST /telemetry/crash
  → server: rate limit + sanitize + crash_reports 表
  → crash_reports_total counter → Prometheus ✅
```

### 3.5 链路五：Android heartbeat → active_devices_total ✅

```
AozoraApp.onCreate → heartbeatScope.launch → HeartbeatClient.send()
  → POST /telemetry/heartbeat + X-Device-Id header
  → server sha256(device_id) → 64 char hash
  → record_active_device(app_version, device_id_hash)
  → ObservableGauge callback → active_devices_total
  → Prometheus → Grafana ✅
  → CrashFreeRateLow 告警分母 ✅
```

### 3.6 链路六：通知 → 维护者 ✅

```
alert-rules.yml → Prometheus → Alertmanager
  → route by severity → email + webhook (staging: devnull)
  → 生产通过 env 覆盖 ✅
```

### 3.7 链路七：sync_queue_depth ✅

```
sync/routes.py L69-70 → record_sync_queue_depth(body.pending_count, user_id)
  → Gauge metric → Prometheus → SyncBacklogHigh alert ✅
  （待客户端填入 pending_count）
```

### 3.8 链路八：Feedback ❌ 有缺陷

```
FeedbackClient.submit() → POST /telemetry/feedback
  → server: submit_feedback → device_id_hash[:16] → INSERT feedbacks
  → PostgreSQL VARCHAR(16) vs 客户端发送 64 char hash
  → ❌ 入库报错 / 数据截断
```

---

## 四、规范符合度（最终）

| 规范要求 | 状态 | 说明 |
|---|---|---|
| docker-compose 含 8 服务 | ✅ | db/api/otel-collector/prometheus/loki/tempo/grafana/alertmanager |
| Prometheus 30d / Loki 14d / Tempo 7d | ✅ | |
| Grafana 跨数据源 | ✅ | prometheus + loki + tempo |
| 6 看板 provision | ✅ | 6 JSON 全通过 |
| Logs 可查（Loki） | ✅ **修复** | OTLPLogExporter + LoggingHandler 链路通 |
| Tempo span-metrics → Prometheus | ✅ **修复** | remote-write-receiver 已加 |
| Android 崩溃捕获 + 脱敏 + 上报 | ✅ | |
| Crash-free rate < 99.5% S0 告警 | ✅ **修复** | ObservableGauge + 公式维度对齐 |
| S1 同步积压告警 | ✅ **修复** | Gauge + pending_count |
| S0 重复入账告警 | ✅ | |
| 对账不一致告警 | ⏸️ 注释 | 无对账模块，已注释防误导 |
| 容器数校验 | ✅ **修复** | `-lt 8` |
| heartbeat 客户端 | ✅ **修复** | HeartbeatClient + AozoraApp 启动调用 |
| app_version 版本分布 | ✅ **修复** | ObservableGauge 输出 label |
| device_id_hash 碰撞阻力 | ⚠️ 部分 | crash_reports 64 char ✅；feedbacks 仍 16 char ❌ |
| 单元测试 | ❌ | test_telemetry.py 100% 失败（路径错误） |
| release mapping 归档 | ✅ | |

---

## 五、待修复项（按优先级）

### 批次 A（阻塞，必须本轮修复）

| 编号 | 严重度 | 修复内容 | 涉及文件 |
|---|---|---|---|
| **S1-NEW-1** (原 N-2) | **S1** | `0015_feedback.py` L24 `String(16)` → `String(64)`；`service.py` L219 去掉 `[:16]` | 2 文件 |
| **S1-NEW-2** (原 N-6) | **S1** | `test_telemetry.py` 全部路径加 `/api/v1` 前缀 | 1 文件 |

### 批次 B（建议本轮一起做）

| 编号 | 严重度 | 修复内容 | 涉及文件 |
|---|---|---|---|
| S2-REMAIN | S2 | `service.py` L218 `user_id_hash[:16]` 与 feedback `device_id_hash` 64 字符不一致。建议：要么明确文档记录 user_id_hash 与 device_id_hash 长度差异是有意为之（user_id_hash 仅用于 feedback 内部去重，不需要跨表关联），要么统一 64 字符。 | 1 文件 |

### 批次 C（推迟到后续任务）

| 编号 | 推迟到 | 说明 |
|---|---|---|
| S2-1 (限流器改 Redis) | T-M6-01a | 生产部署前 |
| S2-3 (CrashSanitizer 单测) | T-M5-05c | 需单独任务 |
| S1-6 余下 (d1/d7_retained, reconciliation) | T-M6 / T-M5-01 | 需 nightly job / 对账模块 |

---

## 六、校验清单

| 校验 | 结果 |
|---|---|
| `telemetry.py` ast.parse | ✅ |
| `service.py` ast.parse | ✅ |
| `routes.py` ast.parse | ✅ |
| `schemas.py` ast.parse | ✅ |
| `sync/routes.py` ast.parse | ✅ |
| `sync/schemas.py` ast.parse | ✅ |
| `feedback.py` model ast.parse | ✅ |
| `0015_feedback.py` ast.parse | ✅ |
| `0013_crash_reports.py` ast.parse | ✅ |
| `test_telemetry.py` ast.parse | ✅ |
| `docker-compose.staging.yml` YAML | ✅ |
| `alert-rules.yml` YAML | ✅ |
| 6 dashboard JSON | ✅ 全通过 |
| `staging-deploy.sh` bash -n | ✅ |
| `OTLPLogExporter` import 实测 | ✅ 存在 |
| `OTLPLogHandler` import 实测 | ❌ 不存在（确认 N-1 修复正确） |
| TestClient 路径 `/telemetry/feedback` | **404** ❌ |
| TestClient 路径 `/api/v1/telemetry/feedback` | 401（正确路由，无 auth） |
| crash_reports model ↔ migration | ✅ 均 String(64) |
| feedbacks model ↔ migration | **❌ model String(64) vs migration String(16)** |
| 0013 revision 链 | ✅ `down_revision = "0012_account_deletion"` |
| 0015 revision 链 | ✅ `down_revision = "0014_deletion_token_unique"` |
| HeartbeatClient URL | ✅ `{baseUrl}/telemetry/heartbeat` |
| FeedbackClient URL | ✅ `{baseUrl}/telemetry/feedback` |
| CrashReporter captureSync hash | ✅ SHA-256 完整 64 hex |
| AozoraApp.onCreate 调用顺序 | ✅ install → uploadPending → heartbeat |

---

## 七、修复建议优先级图

```
S1-NEW-1 (feedback model/migration 不一致)  ─┐
                                              ├→ 批次 A（必须修）
S1-NEW-2 (test 路径缺 /api/v1)              ─┘
                                              ↓
                               修复后 → 状态可闭环为 ✅
```

**预计修复耗时**：~15 分钟（3 文件改动，每处 1-3 行）

---

**审查人：** Claude Code
**审查耗时：** ~30 分钟
**下次审查触发点：** 批次 A 修复提交后（预计本轮即可完成）
