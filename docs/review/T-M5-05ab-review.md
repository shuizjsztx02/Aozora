# T-M5-05a / T-M5-05b 生产监控与崩溃上报 审查报告（首次）

> **审查日期:** 2026-09-07  
> **审查范围:** T-M5-05a（staging 可观测栈）+ T-M5-05b（看板/告警/崩溃上报）两子任务整体  
> **审查方法:** 逐文件精读 + 链路完整性推演 + YAML/JSON/bash -n 校验 + 调用点 grep  
> **基线文档:** `docs/milestones/production-execution-plan.md` L897–904、`docs/t-m5-05a-implementation-summary.md`、`docs/t-m5-05b-implementation-summary.md`、`docs/APP-CONTEXT.md`

---

## 一、审查结论

| 子任务 | 结论 | 说明 |
|---|---|---|
| T-M5-05a staging 可观测栈 | ⚠️ **条件性通过** | 8 容器可启动；但 **logs 链路断裂**（API 日志无通道进 Loki）、**Tempo span-metrics 链路断裂**（Prometheus 未开启 remote-write receiver），与规范"可查询 traces/metrics/logs 三类数据"的承诺存在偏差 |
| T-M5-05b 看板/告警/崩溃上报 | ⚠️ **条件性通过** | 6 看板 JSON + 7 组告警规则就绪；Android 崩溃捕获链路完整（handler → 脱敏 → Room → 上报）；但 **`active_devices_total` 指标因客户端从未调用 heartbeat 端点而永远为 0**，Android 看板与 CrashFreeRateLow 告警永远无数据；多个 record_* helper 在业务代码中无调用点 |

**总体结论：⚠️ 条件性通过**。核心崩溃捕获链路（Android → server → crash_reports 表）与告警通知链路（Prometheus → alertmanager → email/webhook）在代码层面可用；但多条指标链路因客户端缺失 heartbeat、缺少 docker log driver、Prometheus 未开 remote-write 等配套缺失，使对应看板/告警永远无数据。需在后续任务（T-M6-01a 或 T-M5-05c 内测前）补齐。

### 严重度汇总

| 严重度 | 数量 | 性质 |
|---|---|---|
| **S0**（链路断裂 / 安全） | 0 | — |
| **S1**（规范不符 / 看板告警无数据） | 6 | logs 链路、Tempo→Prometheus、heartbeat 客户端未接、crash-free 公式、`sync_queue_depth` 无写入方、4 个 record_* 无调用 |
| **S2**（健壮性 / 文档 / 防御） | 5 | 限流器并发安全、device_id_hash 熵、无测试、staging-deploy 容器数校验、heartbeat 端点 `db=None` |

---

## 二、链路完整性推演（核心）

### 2.1 链路一：API 请求 span → Tempo ✅

```
API request_id.py middleware
  → OTLPSpanExporter (grpc → otel-collector:4317)
  → collector otel-collector-config.yaml traces pipeline
  → otlp/tempo exporter → tempo:4317
  → Tempo 本地 backend 7d 保留
  → Grafana Tempo 数据源 ✓
```

校验通过。`infra/monitoring/tempo-config.yaml` 的 compactor.retention=7d 与规范一致。

### 2.2 链路二：API 请求指标 → Prometheus ⚠️ 部分通

```
main.py @log_requests middleware
  → record_request() → api_requests_total / api_errors_total / api_request_duration_ms
  → OTLPMetricExporter (grpc → otel-collector:4317)
  → collector metrics pipeline
  → prometheus exporter @ 0.0.0.0:8889
  → prometheus.yml job "aozora-api" scrape http://otel-collector:8889/metrics
  → Prometheus 30d 保留
  → Grafana Prometheus 数据源 ✓
```

链路通。但 `prometheus.yml` 注释声称 "API 本身不直接提供 /metrics"，与 pyproject.toml 引入 `opentelemetry-instrumentation-fastapi` 的暗示不一致（实际未使用、也未在 pyproject 声明；`request_id.py` 手工建 span 完成 tracing，不依赖该包）。**S2-5 文档/依赖一致性**。

### 2.3 链路三：API 日志 → Loki ❌ 断裂

```
API stdout/stderr → docker 默认 json-file driver → 容器日志
  × 无任何配置将日志转发到 OTel Collector 或 Loki
  × telemetry.py 中 _logger 仅是标准 logging.Logger，未挂 OTLPHandler
  × docker-compose.staging.yml 中 api 服务未声明 logging.driver
```

`otel-collector-config.yaml` L37-39 注释声称 "Logs → Loki (via native push API)"，且 collector 已配置 loki exporter，但 **没有任何生产者把日志送到 collector 的 OTLP receiver**。规范明确要求 "logs（Loki）" 可查，此处链路完全断裂。**S1-1**。

**修复方案（二选一）：**
1. **推荐**：在 `docker-compose.staging.yml` 的 `api` 服务中声明 `logging.driver: otel`（Docker 原生 OTLP driver，Docker 25+ 支持）或 `fluentd` + fluent-plugin-loki，并指向 `otel-collector:4317`。
2. 在 `telemetry.py` 中为 `_logger` 挂 `OTLPLogHandler`（`opentelemetry-exporter-otlp` 已提供），由进程内直推。

### 2.4 链路四：Tempo span-metrics → Prometheus ❌ 断裂

```
tempo-config.yaml overrides.metrics_generator_processors: [service-graphs, span-metrics]
  → metrics_generator 计算 span 级衍生指标
  → remote_write: http://prometheus:9090/api/v1/write
  × Prometheus 容器未配置 --web.enable-remote-write-receiver
  → 404 Not Found；衍生指标全部丢弃
```

`docker-compose.staging.yml` L99-106 Prometheus `command` 仅含 `--storage.tsdb.retention.time=30d` 等，**缺少 `--web.enable-remote-write-receiver`**。**S1-2**。

**修复**：在 Prometheus `command` 列表追加 `--web.enable-remote-write-receiver`。

### 2.5 链路五：Android 崩溃 → server → crash_reports 表 ✅

```
AozoraApp.onCreate → CrashReporter.install()
  → setDefaultUncaughtExceptionHandler (链式前 handler)
  → 崩溃 → captureSync() → CrashSanitizer.sanitize + signature
  → CrashReportDatabase (独立 aozora-crashes.db) DAO insertBlocking
  → 调前 handler 让进程终止

下次启动 AozoraApp.onCreate → CrashReporter.uploadPending()
  → consent 检查 → CoroutineScope(IO).launch
  → 遍历 pending → CrashReportUploader.upload (AuthClient + OkHttp)
  → POST {SYNC_BASE_URL}/telemetry/crash = POST /api/v1/telemetry/crash ✓
  → server 入库 → 删除本地记录
```

URL 与 server router 匹配（`SYNC_BASE_URL` 在 debug variant 已含 `/api/v1`，release 由 `AOZORA_API_BASE` 注入）。脱敏规则覆盖 token/password/payload/note 等。上报前检查 consent，链式前 handler 保留系统行为。**链路完整**。

但：
- **`uploadPending` 无 heartbeat 调用**：客户端仅上报崩溃，从未调用 `/telemetry/heartbeat`。**S1-3**。
- **`deviceIdHash` 在客户端捕获阶段永远为 null**（`CrashReporter.kt` L78），导致服务端 `device_id_hash` 列为 null，per-device 20/h 限流失效，退化为仅 per-IP 限流。**S2-2**。

### 2.6 链路六：通知 → 维护者 ✅（staging 走 devnull）

```
alert-rules.yml (Prometheus 加载)
  → 触发告警 → POST alertmanager:9093/api/v2/alerts
  → route 按 severity 分发 → critical-receiver (email + webhook) / warning-receiver (webhook)
  → staging 默认 EMAIL_TO=devnull@example.com / WEBHOOK=localhost:9999
```

`alertmanager.yml` 使用 `{{ env "VAR" "default" }}` Go 模板语法，Alertmanager 原生支持（`template.Parse` 提供 `env` 函数），**不存在模板替换失效问题**。通知链路可工作（staging 端点指向 devnull，生产通过 env 覆盖）。✅

### 2.7 看板 provision ✅

`infra/monitoring/dashboards.yml` 注册 `aozora` provider，`docker-compose.staging.yml` L156-157 把 `dashboards.yml` 与 `dashboards/` 挂载到 `/etc/grafana/provisioning/dashboards/` 与 `/var/lib/grafana/dashboards`。路径一致。6 个 JSON 全部 `json.load` 通过。**链路通**。

---

## 三、S1 问题详表（与规范/声称不符）

### S1-1 ❌ Logs 链路完全断裂

- **位置**：`server/src/common/telemetry.py` L83-85、`docker-compose.staging.yml` `api` 服务（无 `logging.driver`）
- **规范承诺**："通过 Grafana Explore 可查询 … logs（Loki）三类数据"
- **实际**：`_logger = logging.getLogger("aozora.telemetry")` 仅建一个标准 logger，未挂 OTLP handler；compose 也未把容器日志路由到 collector；Loki 容器启动后永远收不到任何日志。
- **影响**：Grafana Loki 数据源永远无数据；`/loki/api/v1/push` 端点空闲。
- **修复**：任选其一——(a) `api` 服务加 `logging: {driver: otel, options: {otel-endpoint: http://otel-collector:4317}}`（需 Docker 25+）；(b) `telemetry.py` 增加 `from opentelemetry._logs import set_logger_provider; from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogHandler`，并在 `setup_telemetry` 中给 `_logger` 挂 handler。

### S1-2 ❌ Tempo span-metrics → Prometheus 不通

- **位置**：`infra/monitoring/tempo-config.yaml` L30-32、`docker-compose.staging.yml` L100-105
- **影响**：Tempo `metrics_generator` 计算出的 span-metrics（service-graphs、span-metrics）因 Prometheus 未启用 `--web.enable-remote-write-receiver` 被 404 丢弃。**当前 6 个看板面板无一依赖此链路**（所有面板直接用 OTLP 直推指标），所以看板本身无影响；但 `tempo-config.yaml` 配置是"死代码"，未来如果新增 span-metrics 面板会立即失效。
- **修复**：`docker-compose.staging.yml` Prometheus `command` 追加 `--web.enable-remote-write-receiver`。

### S1-3 ❌ `active_devices_total` 永远为 0（客户端未实现 heartbeat）

- **位置**：全 Android 代码 grep `heartbeat` / `telemetry/heartbeat` → 零调用点
- **影响**：
  1. `infra/monitoring/dashboards/android-health.json` 的 "Version Distribution" / "Crash-Free Session Rate" / "Active Devices" 三个面板全部无数据
  2. `infra/monitoring/alert-rules.yml` `CrashFreeRateLow` 告警永不触发（分母 `sum(active_devices_total) > 0` 永远为假 → 整个表达式为 NaN）
  3. `server/src/modules/telemetry/routes.py` L60-78 的 `/telemetry/heartbeat` 端点形同虚设
- **规范符合度**：规范明确要求"Android 版本健康度"看板与 "crash-free < 99.5%" S0 告警，此链路缺失 = 规范项未交付。
- **修复**：在 `AozoraApp.onCreate` 的协程启动路径（或 MainActivity.onCreate）调用 `/telemetry/heartbeat`（可复用 `CrashReportUploader` 的 HTTP 通道与 AuthClient）；或新增独立 `HeartbeatClient`。频率建议：每日一次 + 每次启动一次。

### S1-4 ❌ `CrashFreeRateLow` 告警公式数学错误

- **位置**：`infra/monitoring/alert-rules.yml` L146-154
- **公式**：
  ```
  (1 - sum(increase(crash_reports_total[24h])) / (sum(active_devices_total) > 0)) < 0.995
  ```
- **问题**：`active_devices_total` 是 OTel `UpDownCounter`，服务端 `record_active_device()` 在每次 heartbeat 时 `add(1)`，**是单调递增的累计计数器**，不是"过去 24h 活跃设备数"。即使客户端接了 heartbeat：
  - 分子：`increase(crash_reports_total[24h])` = 过去 24h 新增崩溃数（窗口）
  - 分母：`sum(active_devices_total)` = 自进程启动以来所有 heartbeat 次数之和（累计）
  - 两者维度不匹配，比值不是 crash-free rate。
- **修复**：
  1. 服务端改用 `Gauge`（`ObservableGauge` 或 `UpDownCounter` 在 heartbeat 中先重置再 +1），或
  2. 客户端 heartbeat 同时携带 `device_id_hash`，服务端维护"过去 24h 活跃设备数"作为 gauge 指标；告警改为：`1 - (unique_device_ids_with_crash[24h] / unique_device_ids_active[24h])`。

### S1-5 ❌ `sync_queue_depth` 永远为 0

- **位置**：`server/src/common/telemetry.py` L151-155（`sync_queue_depth` 仪器创建）+ 全仓 grep `record_` → **无调用方**
- **影响**：`alert-rules.yml` L173-187 `SyncBacklogHigh` 告警表达式 `sum(sync_queue_depth) > 100` 永远为假；同步队列积压看板永远空白。
- **规范符合度**：规范"同步积压 > 100" S1 告警依赖此指标。
- **修复**：
  1. 方案 A（推荐）：客户端 `SyncWorker` 在每次 push 前上报 `sync_queue_depth = pending_mutations_count`（需新增客户端端点，如 `POST /telemetry/sync-queue-depth`）；
  2. 方案 B：服务端在 `sync/routes.py` `sync_push` 中通过查询当前所有用户待处理 mutation 数估算（不够准确，因客户端未 push 的服务端看不到）。

### S1-6 ❌ 多个 `record_*` helper 在业务代码中无调用

- **位置**：`server/src/common/telemetry.py` + 全仓 grep
- **表格**（调用点 / 无调用）：

| Helper | 调用方 | 状态 |
|---|---|---|
| `record_sync_push` | `sync/routes.py:70,77` | ✅ |
| `record_sync_conflict` | `sync/service.py:88` | ✅ |
| `record_ai_gateway` | `ai/service.py:249,1048,1241` | ✅ |
| `record_crash_report` | `telemetry/service.py:96` | ✅ |
| `record_active_device` | `telemetry/service.py:121`（但依赖 heartbeat 端点被调——见 S1-3） | ⚠️ |
| `record_signup` | `auth/service.py:47,78` | ✅ |
| `record_transaction` | `transactions/service.py:85,131` | ✅ |
| `record_export` | `exports/routes.py:33` | ✅ |
| `record_d1_retained` | **无调用** | ❌ |
| `record_d7_retained` | **无调用** | ❌ |
| `record_reconciliation_inconsistency` | **无调用** | ❌（代码注释承认 "TODO(T-M5-05b)"） |
| `record_duplicate_entry` | `sync/service.py:65` | ✅ |

- **影响**：
  - `business_funnel.json` 的 D1/D7 面板永远无数据（虽然其他面板有）
  - `alert-rules.yml` `ReconciliationInconsistency` / `DuplicateEntryDetected` 中，前者永远不触发（后者因 sync 路径有调用而能触发）
  - `business_signup_total` / `business_transaction_total` / `business_export_total` 有数据，业务漏斗主链路可看
- **规范符合度**：D1/D7 与对账不一致的告警/看板是规范显式要求的；对账不一致作为 S0 告警，当前完全无法触发 = 规范项未交付。
- **修复**：
  - `record_d1_retained` / `record_d7_retained`：需在 nightly retention job 中调用（该 job 本身未在 T-M5-05b 范围，需作为后续任务跟踪）
  - `record_reconciliation_inconsistency`：需在对账审计（server 端对账模块）中调用；对账模块未实现前该告警应**从 `alert-rules.yml` 中注释掉或标记 pending**，避免运维误以为"无告警=无问题"

---

## 四、S2 问题详表（健壮性 / 文档 / 防御）

### S2-1 ⚠️ 内存限流器并发安全

- **位置**：`server/src/modules/telemetry/service.py` L36-81（`_recent_reports` / `_last_cleanup`）
- **问题**：`_recent_reports: defaultdict(list)` 与 `_last_cleanup` 是模块级可变状态，无锁；`_check_rate_limit` 在 FastAPI async handler 中被 `await` 切点分隔（虽然该函数本身是同步的，但内部对 dict 的读-改-写序列在 `await` 前后可能被其他协程交叉）。在 CPython 中 GIL 使纯 Python 字节码级操作是原子的，但 `_recent_reports[key] = [t for t in recent if t > cutoff]` 这类列表重建赋值在语义上存在 TOCTOU 窗口（两个协程同时为同一 device_id_hash 计算并写入）。实际风险低（单进程、GIL、crash 端点 QPS 低），但 **生产多 worker 部署时（uvicorn --workers>1 或多副本）每个 worker 独立计数，per-device 配额 = 20 × workers**，与规范 "每设备每小时 20 条" 不符。
- **建议**：生产部署使用 Redis 做集中式限流（`redis_url` 配置已预留，`config.py` L76-79），当前内存实现作为 staging 起点可接受，但在 `docs/operations/` 中记录这一限制。

### S2-2 ⚠️ `device_id_hash` 熵过低

- **位置**：`server/src/modules/telemetry/routes.py` L74、`server/src/modules/telemetry/schemas.py` L35-38
- **问题**：`hashlib.sha256(device_id)[:16]` 仅保留 16 个十六进制字符 = 64 bit 熵。按 birthday paradox，约 2^32 ≈ 40 亿次设备注册即有 50% 概率出现一次碰撞。对 staging 内测（20-50 人）无影响，但生产量级会出现"设备 A 的崩溃报告被记到设备 B"的误归属，使 per-device 限流误判。
- **建议**：扩到 32 字符（128 bit），或直接用完整 SHA-256 hex（64 字符）。需同步修改 schema `max_length=16` → 64，Android 客户端 `CrashSanitizer` / `CrashReportUploader` 同步修改。

### S2-3 ⚠️ 无单元测试 / 集成测试

- **位置**：`server/tests/` 目录 grep `telemetry|crash_report|heartbeat` → 零匹配
- **影响**：
  - `CrashSanitizer.sanitize` 脱敏正则无测试覆盖，绕过风险（嵌套 JSON、空格变体、base64 编码的 token）无法在 CI 捕获
  - `/telemetry/crash` 端点的路由、限流、入库、字段截断无集成测试
  - 与项目其它模块（`auth`/`sync`/`transactions` 均有测试）不一致
- **建议**：至少补 3 个测试：(1) `CrashSanitizer` 单测；(2) `/telemetry/crash` 端到端（含限流 20/h 边界）；(3) `/telemetry/heartbeat` 端到端。

### S2-4 ⚠️ `staging-deploy.sh` 容器数校验与实现总结不符

- **位置**：`infra/deploy/staging-deploy.sh` L161-166
- **代码**：
  ```bash
  if [ "$CONTAINER_COUNT" -lt 7 ]; then
      log_error "Expected at least 7 containers, found $CONTAINER_COUNT"
  ```
- **不一致**：
  - `docker-compose.staging.yml` 实际定义 **8 个服务**（db + api + otel-collector + prometheus + loki + tempo + grafana + alertmanager）
  - `docs/t-m5-05a-implementation-summary.md` L146-149 明文 "应显示 8 个容器"
  - 计划规范 "docker ps 列出 7 个容器"（与上面两项矛盾）
  - 脚本下限设 7 = 实际少 1 个容器也能通过校验
- **建议**：改为 `if [ "$CONTAINER_COUNT" -lt 8 ]`，或更稳健 `if [ "$CONTAINER_COUNT" -ne 8 ]`；同步修正计划文档（规范）的 "7 个容器" 表述。

### S2-5 ⚠️ `pyproject.toml` 引入但从未使用的 instrumentation 包

- **位置**：实现总结声称 "opentelemetry-instrumentation-fastapi" 是依赖，但 `server/pyproject.toml` L27-30 **未声明该包**；实际依赖只有 `opentelemetry-api/sdk/exporter-otlp`
- **实际代码**：`request_id.py` 手工建 span，不使用 FastAPIInstrumentor
- **影响**：无功能影响（代码路径一致）；但实现总结与代码不符，未来维护者可能误以为已启用自动 instrumentation
- **建议**：更新 `docs/t-m5-05a-implementation-summary.md` 第 19-22 行依赖清单，移除 `opentelemetry-instrumentation-fastapi`。

### S2-6 ⚠️ heartbeat 端点 `db=None` 类型不安全

- **位置**：`server/src/modules/telemetry/routes.py` L76
- **代码**：`service = TelemetryService(db=None)  # type: ignore[arg-type]`
- **影响**：当前 `heartbeat` 不触 DB 可工作；但 `TelemetryService` 任何未来新增的 DB 依赖方法若在 heartbeat 路径被误调，将立即 `AttributeError`。`type: ignore` 掩盖了类型系统本应捕获的设计缺陷。
- **建议**：将 `heartbeat` 抽到独立 `HeartbeatService`（无 DB 依赖），或让 `TelemetryService.__init__` 接受 `db: AsyncSession | None` 并在 DB 方法入口 `assert self.db is not None`。

### S2-7 ⚠️ Android `CrashReporter.uploadPending` 协程作用域泄漏

- **位置**：`app/src/main/java/com/aozora/app/crashlytics/CrashReporter.kt` L95-123
- **代码**：`CoroutineScope(SupervisorJob() + Dispatchers.IO).launch { ... }` 创建后即丢弃引用
- **影响**：
  - 调用方（`AozoraApp.onCreate`）无法 await 完成、无法取消
  - 上传失败时的异常已被内层 try-catch 捕获，不会泄漏到 SupervisorJob；但若 `dao.listPending()` 之前的逻辑抛异常，会被 SupervisorJob 的默认 CoroutineExceptionHandler 吃掉（无日志）
  - 进程若在完成前被杀（例如低内存），上传半途而废
- **建议**：将 scope 提升为 `CrashReporter` 成员，并在 `ProcessLifecycleOwner` 的 `ON_STOP` 时 join 或 cancel。

---

## 五、规范符合度对照表

| 规范要求（计划 L897-904） | 交付情况 | 校验 |
|---|---|---|
| docker-compose.staging.yml 含 8 服务 | ✅ | `docker-compose.staging.yml` 含 db/api/otel-collector/prometheus/loki/tempo/grafana/alertmanager = 8 |
| Prometheus 30d / Loki 14d / Tempo 7d 保留策略 | ✅ | L103、loki-config.yaml L45、tempo-config.yaml L21 |
| Grafana 数据源含 Prometheus / Loki / Tempo 跨数据源关联 | ✅ | grafana-datasources.yaml L36-45 |
| 部署脚本一站部署 | ✅ | `staging-deploy.sh` bash -n 通过，逻辑完整 |
| 从干净环境执行后全部服务正常运行 | ⚠️ | 8 容器可启动；但 Loki 永远无日志，违反"可查询 logs"承诺 |
| 六类 Grafana 看板可切换且每类至少一个面板有数据 | ⚠️ | 6 JSON provision OK；但 android-health 三面板无数据（S1-3），business-funnel 的 D1/D7 无数据（S1-6），postgresql 的 pg_stat 面板无数据（postgres_exporter 未接入，已在实现总结中标为已知后续项） |
| S0 告警：API 可用性 < 99.9% | ✅ | alert-rules.yml ApiAvailabilityLow 公式正确，指标有实际调用 |
| S0 告警：crash-free < 99.5% | ❌ | CrashFreeRateLow 公式错误 + active_devices_total 永远为 0（S1-3 + S1-4） |
| S0 告警：对账不一致 | ❌ | `record_reconciliation_inconsistency` 无调用（S1-6） |
| S0 告警：重复入账 | ✅ | `record_duplicate_entry` 在 `sync/service.py:65` 调用 |
| S1 告警：p95 延迟 > 500ms | ✅ | ApiLatencyHigh 公式正确 |
| S1 告警：同步积压 > 100 | ❌ | `sync_queue_depth` 永远为 0（S1-5） |
| S1 告警：AI 错误率 > 5% | ✅ | AiErrorRateHigh 公式正确，指标有实际调用 |
| 通知渠道配置化（不硬编码） | ✅ | alertmanager.yml 使用 `{{ env }}` 模板，compose 注入 env |
| Android 崩溃捕获：UncaughtExceptionHandler | ✅ | CrashReporter.install() 链式前 handler |
| Android 脱敏：过滤 token/password/payload/note | ✅ | CrashSanitizer.SENSITIVE_KEYS 覆盖 |
| Android 本地 SQLite 存储 → 下次启动上报 | ✅ | CrashReportDatabase 独立库；CrashReportUploader 复用 AuthClient |
| 需用户已同意隐私政策 | ✅ | uploadPending L94 `if (!consentStore.hasConsented()) return` |
| release mapping 归档 | ✅ | archive-mapping.sh bash -n 通过；version 解析、sidecar .meta 完整 |

---

## 六、后续修复建议（按优先级）

按影响范围排序，可在 T-M5-05c 内测前或 T-M6-01a 生产部署前分批完成：

### 批次 A（内测前必做，影响告警可用性）

1. **S1-3 客户端 heartbeat 接入**：在 `AozoraApp.onCreate` 或首次 MainActivity 中调用 `/telemetry/heartbeat`。否则 Android 看板与 CrashFreeRateLow 告警永远空白。
2. **S1-1 Logs 链路补齐**：`docker-compose.staging.yml` 的 `api` 服务声明 OTLP logging driver，或在 `telemetry.py` 挂 OTLPLogHandler。
3. **S1-6 把未实现的告警从 alert-rules.yml 中注释掉或加 `enabled: false` 标签**：`ReconciliationInconsistency`、`SignupRateDrop`（依赖 `business_signup_total`，虽已调用但告警阈值公式依赖 7 天前同期数据 > 10 的过滤条件，早期可能不触发；可保留）、`SyncBacklogHigh`。否则 Grafana 告警面板的 "inactive" 与 "无数据" 难以区分，运维会误以为安全。

### 批次 B（生产部署前完成）

4. **S1-4 crash-free 公式修正**：明确语义——"过去 24h 内至少发生一次崩溃的设备数 / 过去 24h 至少一次 heartbeat 的设备数"，需要新增 "unique device count" 类指标（gauge 或 summary）。
5. **S1-2 Prometheus remote-write receiver 启用**：一行 flag。
6. **S1-5 sync_queue_depth 写入方**：客户端 SyncWorker 上报，或新增 `POST /telemetry/queue-depth` 端点。
7. **S1-6 余下 `record_d1_retained` / `record_d7_retained` / `record_reconciliation_inconsistency` 调用方**：需对应 nightly job 与对账模块（均超出 T-M5-05b 范围，需跟踪到 T-M6 或 T-M5-01 后续）。

### 批次 C（健壮性增强，可推迟）

8. **S2-3 单元测试**：至少 `CrashSanitizer` + `/telemetry/crash` 集成测试。
9. **S2-1 生产限流改 Redis**：staging 先用内存可接受，生产部署前切 Redis（`config.py` 已预留 `redis_url`）。
10. **S2-2 device_id_hash 扩到 128 bit**：同步修改 schema + 客户端。
11. **S2-4 staging-deploy.sh 容器数校验改 8**。
12. **S2-5 实现总结依赖清单修正**。
13. **S2-6 TelemetryService `db=None` 重构**。
14. **S2-7 CrashReporter 协程作用域提升**。

---

## 七、校验清单（客观测试）

| 校验 | 命令 | 结果 |
|---|---|---|
| YAML 语法 | `python -c "yaml.safe_load(open('infra/monitoring/alert-rules.yml'))"` 等 6 项 | ✅ 全部通过 |
| Dashboard JSON 语法 | `json.load()` × 6 | ✅ 全部通过 |
| bash -n staging-deploy.sh | `bash -n infra/deploy/staging-deploy.sh` | ✅ 通过 |
| bash -n archive-mapping.sh | `bash -n tools/archive-mapping.sh` | ✅ 通过 |
| 18 个 record_* 调用点 grep | 见 §三 S1-6 表 | 9 个有调用、4 个无调用、1 个依赖未接 heartbeat |
| Android heartbeat 调用 grep | `grep -r "heartbeat" app/` | ❌ 零调用 |
| Alertmanager env 模板机制 | `{{ env "VAR" "default" }}` | ✅ Alertmanager 原生支持 |
| `CrashReportUploader.upload` URL | `$baseUrl/telemetry/crash` | ✅ `SYNC_BASE_URL` debug variant 含 `/api/v1`，匹配 server router |
| Room 依赖 | `app/build.gradle.kts` L105-108 | ✅ room-runtime/ktx + ksp compiler |
| AozoraApp CrashReporter 安装顺序 | L159 `install()` → L161 `uploadPending()` | ✅ 链式前 handler、consent 检查就位 |
| `archive-mapping.sh` version 解析 | L38 `grep versionName` + sed | ✅ 与 `app/build.gradle.kts` `versionName = "..."` 格式匹配 |
| `0013_crash_reports.py` revision 链 | `down_revision = "0012_account_deletion"` | ✅ 与 `0012_account_deletion.py` 衔接 |

---

## 八、任务状态建议

基于以上审查：

| 子任务 | 当前计划状态 | 建议状态 |
|---|---|---|
| T-M5-05a | ✅ 通过 | ⚠️ **有条件通过**（S1-1 logs 链路未通，需后续补齐） |
| T-M5-05b | ✅ 完成 | ⚠️ **有条件完成**（S1-3/4/5/6 使部分看板/告警无数据） |

**建议**：在 `production-execution-plan.md` 中把 T-M5-05a/b 状态标为 "✅ 通过（待 S1-1/3/4/5/6 在 T-M5-05c 内测前补齐）"，并在批次 A 修复完成后闭环为 "✅ 完成"。

---

**审查人：** Claude Code (主上下文)  
**审查耗时：** 约 1 小时（含子代理尝试因模型配置失败转手动）  
**下次审查触发点：** 批次 A 修复提交后
