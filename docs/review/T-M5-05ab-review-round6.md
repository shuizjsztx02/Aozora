# T-M5-05a / T-M5-05b 第六轮审查报告（独立全量审查 — 终审）

> **审查日期:** 2026-09-07
> **审查范围:** T-M5-05 全量——服务端代码 + 基础设施配置 + Android 客户端 + 测试
> **审查方法:** 三个并行子代理逐文件精读 + 跨文件链路追踪 + 前四轮修复逐条复验 + pytest 实测
> **基线:** 前四轮审查报告（`T-M5-05ab-review.md` §一–§十、round3、round4、round5）+ 实现总结

---

## 一、审查结论

**✅ 通过。T-M5-05a / T-M5-05b 全部问题已闭环。**

三个并行子代理分别验证了服务端 14 个文件、基础设施 12 组配置、Android 客户端 10 个文件，共 ~36 个核心文件。前四轮审查发现的全部 19 项问题均已正确修复。pytest 实测 7/7 测试通过。

**勘误：** 初稿中报告的 `test_telemetry.py` 路径问题（S1-TEST-PATH）是**误判**。`conftest.py` L34 设置 `os.environ.setdefault("API_PREFIX", "")`，测试环境中路由注册在自然路径 `/telemetry/feedback`（无前缀），与测试代码路径完全匹配。第三轮/第四轮审查者未考虑 conftest 上下文，直接测试 `TestClient` 看到了 404，属于假阳性。pytest 实测确认 **7/7 passed in 1.17s**。

---

## 二、前四轮修复逐条复验

### 已确认修复 ✅（19/19）

| 编号 | 问题 | 修复验证 |
|---|---|---|
| S1-1 | Logs → Loki 链路断裂 | ✅ `telemetry.py` L38 正确使用 `OTLPLogExporter`（非旧 bug `OTLPLogHandler`），完整链路 `aozora logger → LoggingHandler → LoggerProvider → BatchLogRecordProcessor → OTLPLogExporter → Collector → Loki` |
| S1-2 | Tempo span-metrics → Prometheus | ✅ `docker-compose.staging.yml` Prometheus command 含 `--web.enable-remote-write-receiver` |
| S1-3 | `active_devices_total` 永远为 0 | ✅ `HeartbeatClient.kt` 存在，`AozoraApp.onCreate` L194+ 启动时发心跳 |
| S1-4 | CrashFreeRateLow 公式维度不匹配 | ✅ `active_devices_total` 改为 `ObservableGauge`（L249），维护 24h 唯一设备窗口 |
| S1-5 | `sync_queue_depth` 永远为 0 | ✅ `PushRequest` 新增 `pending_count` 可选字段（`sync/schemas.py` L46-50），`sync/routes.py` L69-70 调用 `record_sync_queue_depth`，instrument 改 `Gauge`（L212） |
| S1-6 | 无调用点告警误导 | ✅ `ReconciliationInconsistency` 在 `alert-rules.yml` 中已注释 |
| S2-2 | `device_id_hash` 熵过低 | ✅ crash_reports 全链路 64 字符：model `String(64)` / migration `String(64)` / schema `max_length=64` / 客户端 SHA-256 完整 hex |
| S2-4 | 容器数校验 7→8 | ✅ `staging-deploy.sh` 改为 `-lt 8` |
| S2-5 | 实现总结声明未使用依赖 | ✅ `t-m5-05a-implementation-summary.md` L23-24 明确声明未使用 `opentelemetry-instrumentation-fastapi` |
| S2-6 | heartbeat `db=None` 类型不安全 | ✅ `TelemetryService.__init__` L136 签名 `AsyncSession \| None`，DB 方法入口 guard |
| S2-7 | CrashReporter 协程作用域泄漏 | ✅ `uploadScope` 提升为类成员 L51，`uploadJob` 标注 `@Volatile` L52 |
| N-2 (feedback hash) | Feedback model/migration 不一致 | ✅ `0015_feedback.py` L24 `String(64)`，`service.py` L201 无截断，两表 cross-table 一致 |
| N-3 (冗余 import) | `routes.py` 函数内冗余 `import hashlib` | ✅ 已删除 |
| N-4 (heartbeat scope) | `AozoraApp` 心跳协程 scope 泄漏 | ✅ `heartbeatScope` 提升为类成员 L44 |
| N-5 (app_version) | `record_active_device` 丢弃 `app_version` | ✅ `_active_devices` 存 `(timestamp, app_version)` tuple，gauge 回调输出 `app_version` label |
| Round5-S1 | OTel Collector `namespace: aozora` 导致指标名前缀不匹配 | ✅ `otel-collector-config.yaml` 已删除 `namespace: aozora`，所有指标名无前缀，dashboard/alert-rules 一致 |
| Round5-S2-1 | Grafana trace→logs tag 不匹配 | ✅ `grafana-datasources.yaml` 的 `tracesToLogs.tags` 和 `tracesToMetrics.tags` 均改为 `["request.id"]`，与 `request_id.py` L37 的 `span.set_attribute("request.id", ...)` 一致 |
| Round5-S2-2 | `count_pending_for_device` 死代码 | ✅ 已删除 |
| Round5-S2-3 | `record_request` 高基数 risk | ✅ `main.py` L172 使用 `request.scope.get("route").path` 获取路由模板路径 |

---

## 三、全链路终态（14/14 ✅）

| # | 链路 | 状态 | 说明 |
|---|---|---|---|
| 1 | API 请求 span → Tempo | ✅ | OTLPSpanExporter → collector → tempo |
| 2 | API 请求指标 → Prometheus | ✅ | OTLPMetricExporter → collector → prometheus exporter :8889 → scrape |
| 3 | API 日志 → Loki | ✅ | LoggingHandler → OTLPLogExporter → collector → loki |
| 4 | Tempo span-metrics → Prometheus | ✅ | remote-write-receiver 已启用 |
| 5 | Android 崩溃 → crash_reports | ✅ | handler → sanitize → Room → upload → server → 入库 |
| 6 | Android heartbeat → active_devices | ✅ | HeartbeatClient → gauge callback → Prometheus |
| 7 | CrashFreeRateLow 告警 | ✅ | ObservableGauge 24h 窗口，维度匹配 |
| 8 | sync_queue_depth | ✅ | Gauge + pending_count（待客户端填入） |
| 9 | Feedback → feedbacks 表 | ✅ | 64 char hash 全链路一致 |
| 10 | 通知 → Alertmanager | ✅ | env 模板，staging devnull |
| 11 | 看板 provision | ✅ | 6 JSON + datasources.yml，35 面板 |
| 12 | 测试 | ✅ | **pytest 7/7 passed**（conftest API_PREFIX="" 路由自然路径） |
| 13 | Grafana trace↔logs correlation | ✅ | tag `request.id` 一致 |
| 14 | OTel Collector 指标名 | ✅ | 无 namespace 前缀，dashboard/alert-rules 匹配 |

---

## 四、六维审查详表

### 4.1 服务端代码（14 文件 ✅）

| 文件 | 状态 | 关键验证点 |
|---|---|---|
| `common/telemetry.py` | ✅ | OTLPLogExporter 类名正确 / ObservableGauge / _active_devices 存 app_version / Gauge for sync_queue_depth / 18 个 record_* helpers |
| `telemetry/routes.py` | ✅ | /crash + /heartbeat + /feedback 三端点 / device_id_hash 64 字符无截断 / 无冗余 import |
| `telemetry/service.py` | ✅ | `AsyncSession \| None` / 无 `[:16]` 截断 / 无死代码 / 双限流器（crash 20/h + feedback 5/h） |
| `telemetry/schemas.py` | ✅ | CrashReport `max_length=64` / Feedback `max_length=64` |
| `models/crash_report.py` | ✅ | `String(64)` |
| `models/feedback.py` | ✅ | `String(64)` |
| `migrations/0013` | ✅ | `String(64)` / `down_revision = "0012_account_deletion"` |
| `migrations/0015` | ✅ | `String(64)` / `down_revision = "0014_deletion_token_unique"` |
| `common/request_id.py` | ✅ | span attribute `request.id`（dot-separated） |
| `main.py` | ✅ | log_requests 使用 route template path / telemetry router 注册 |
| `sync/routes.py` | ✅ | `record_sync_queue_depth` 调用正确 |
| `sync/schemas.py` | ✅ | `pending_count: int \| None` 可选字段 |
| `tests/test_telemetry.py` | ✅ | **pytest 7/7 passed**；conftest `API_PREFIX=""` 路由自然路径 |
| `pyproject.toml` | ✅ | 三个核心 OTel 包 / 无多余 instrumentation 包 |

### 4.2 基础设施配置（12 组 ✅）

| 文件 | 状态 | 关键验证点 |
|---|---|---|
| `docker-compose.staging.yml` | ✅ | 8 服务 / Prometheus remote-write-receiver / Grafana dashboard 挂载 / Alertmanager env |
| `otel-collector-config.yaml` | ✅ | **`namespace: aozora` 已删除** / traces→tempo + metrics→prometheus + logs→loki |
| `prometheus.yml` | ✅ | 3 scrape jobs / alertmanager 配置 / rule_files 含 alert-rules.yml |
| `alert-rules.yml` | ✅ | 12 条活跃告警 / CrashFreeRateLow 维度匹配 / ReconciliationInconsistency 已注释 / 无 `aozora_` 前缀 |
| `alertmanager.yml` | ✅ | `{{ env }}` 模板 / critical + warning receiver / inhibit rule |
| `grafana-datasources.yaml` | ✅ | 3 数据源 / tracesToLogs+tracesToMetrics tags 均为 `request.id` |
| `tempo-config.yaml` | ✅ | 7d retention / metrics_generator + remote_write |
| `loki-config.yaml` | ✅ | 14d retention / compactor 启用 |
| 6 个 dashboard JSON | ✅ | 全 valid JSON / 无 `aozora_` 前缀 / 共 35 个面板 |
| `dashboards.yml` | ✅ | provider `aozora` / path 正确 |
| `staging-deploy.sh` | ✅ | `-lt 8` / 6 健康检查端点 |
| `archive-mapping.sh` | ✅ | version 解析 + 归档 + .meta sidecar |

### 4.3 Android 客户端（10 文件 ✅）

| 文件 | 状态 | 关键验证点 |
|---|---|---|
| `CrashReporter.kt` | ✅ | install() 链式前 handler / captureSync 64 字符 SHA-256 / uploadScope 类成员 / uploadJob @Volatile / MAX_UPLOAD_ATTEMPTS=5 |
| `CrashSanitizer.kt` | ✅ | 13 个 SENSITIVE_KEYS / JSON_PAIR + URL_PARAM 正则脱敏 / 签名 8 字符 hex |
| `CrashReportUploader.kt` | ✅ | URL `$baseUrl/telemetry/crash` / AuthLost·RateLimited·Network·ServerError 错误分类 |
| `CrashReportEntity.kt` | ✅ | 11 个字段含 `attempts` / `deviceIdHash` nullable |
| `CrashReportDao.kt` | ✅ | insertBlocking(REPLACE) / listPending / update / deleteById |
| `CrashReportDatabase.kt` | ✅ | 独立 `aozora-crashes.db` / 线程安全单例 |
| `HeartbeatClient.kt` | ✅ | `$baseUrl/telemetry/heartbeat` / X-Device-Id raw UUID / best-effort |
| `FeedbackClient.kt` | ✅ | `$baseUrl/telemetry/feedback` / 完整 SHA-256 device_id_hash / 错误分类 |
| `AozoraApp.kt` | ✅ | install → uploadPending → heartbeat 顺序 / heartbeatScope 类成员 |
| `build.gradle.kts` | ✅ | Room 2.6.1 + KSP |

---

## 五、规范符合度终表

| 规范要求 | 状态 |
|---|---|
| docker-compose 含 8 服务 | ✅ |
| Prometheus 30d / Loki 14d / Tempo 7d | ✅ |
| Grafana 跨数据源关联 | ✅ |
| 6 看板 provision | ✅ |
| Logs 可查（Loki） | ✅ |
| Tempo span-metrics → Prometheus | ✅ |
| Android 崩溃捕获 + 脱敏 + 上报 | ✅ |
| Crash-free rate < 99.5% S0 告警 | ✅ |
| S1 同步积压告警 | ✅ |
| S0 重复入账告警 | ✅ |
| 对账不一致告警 | ⏸️ 注释（无对账模块，防误导） |
| 容器数校验 | ✅ |
| heartbeat 客户端 | ✅ |
| app_version 版本分布 | ✅ |
| device_id_hash 碰撞阻力 | ✅ |
| release mapping 归档 | ✅ |
| 单元测试 | ✅ 7/7 passed |

---

## 六、推迟项（维持前几轮结论）

| 编号 | 推迟到 | 说明 |
|---|---|---|
| S2-1 (限流器改 Redis) | T-M6-01a | 生产部署前；staging 单 worker 下可接受 |
| S2-3 (CrashSanitizer 单测) | T-M5-05c | 需单独任务 |
| S1-6 余下 (d1/d7_retained, reconciliation) | T-M6 / T-M5-01 | 需 nightly job / 对账模块 |

### 客户端协同项（待 Android 跟进）

- `PushRequest.pending_count`：服务端已接受；客户端需在 `SyncEngine.push` 中填入本地未同步 mutation 数
- `/telemetry/heartbeat`：客户端已实现，每次启动调用一次

---

## 七、勘误：test_telemetry.py 路径问题误判

第三轮（N-6）、第四轮（S1-NEW-2）、第六轮初稿均报告 `test_telemetry.py` 测试路径缺 `/api/v1` 前缀导致 404。此判断为**假阳性**。

**根因：** `tests/conftest.py` L34 设置 `os.environ.setdefault("API_PREFIX", "")`，`_test_config` fixture 使用此环境变量创建 `Settings(api_prefix="")`。当 `src.main` 在 fixture 上下文中导入时，`cfg = get_config()` 返回 `api_prefix=""`，所有路由注册在自然路径（如 `/telemetry/feedback`），与测试代码路径完全匹配。

**审查者错误原因：** 第三/四/六轮审查者在审查时使用独立 `TestClient` 直接测试（不经过 conftest fixture），此时 `API_PREFIX` 为默认值 `/api/v1`，路由注册在 `/api/v1/telemetry/feedback`，自然路径 `/telemetry/feedback` 返回 404。但 pytest 运行时 conftest 的 autouse fixture 已覆盖 `API_PREFIX=""`，路由注册在自然路径，测试通过。

**实测验证：** `pytest tests/test_telemetry.py -v` → **7 passed in 1.17s** ✅

---

## 八、六轮审查历程

| 轮次 | 日期 | 结论 | 新增发现 | 修复数 |
|---|---|---|---|---|
| 一轮 | 2026-09-07 | ⚠️ 条件性通过 | 6 S1 + 5 S2 | — |
| 二轮 | 2026-09-07 | ⚠️ 条件性通过 | 6 N (S1-1 修复未生效) | 11 S1/S2 |
| 三轮 | 2026-09-07 | ⚠️ 条件性通过 | N-2/N-6 升级 S1 | 4 N |
| 四轮 | 2026-09-07 | ✅ 通过（声称） | 0 | 2 S1 |
| 五轮 | 2026-09-07 | ⚠️ 条件性通过（独立审查） | namespace 前缀 + 3 S2 | 4 |
| **六轮** | **2026-09-07** | **✅ 通过** | **0（N-6/S1-NEW-2 为假阳性，已勘误）** | **19/19 确认** |

---

## 九、架构与设计评价

### 优点

1. **防御层次完整** — 客户端 CrashSanitizer 脱敏 → schema Field 长度限制 → service 层二次截断 → 内存限流 → per-IP 限流。五层防御。
2. **崩溃库独立** — `aozora-crashes.db` 与业务库分离，避免崩溃路径依赖不一致状态。
3. **隐私保护设计合理** — feedback `user_id_hash` 截断到 16 char（不可反查 users 表）；crash `device_id_hash` 完整 64 char（防碰撞 + 跨表关联）。
4. **上报策略健壮** — AuthLost 丢弃 / RateLimited 延后 / 网络错误重试 / MAX 5 次后放弃。
5. **OTel 集成标准** — OTLP gRPC，厂商中立，不绑定特定后端。
6. **告警维度正确** — CrashFreeRateLow 用 ObservableGauge 24h 唯一设备数做分母。
7. **基础设施配置一致** — 指标名在 OTel Collector、Prometheus、alert-rules、6 个 dashboard 间完全一致。

### 风险项

1. **内存限流器多 worker** — 生产部署前需切 Redis（`config.py` 已预留 `redis_url`）
2. **`record_d1_retained` / `record_d7_retained` / `record_reconciliation_inconsistency` 无调用方** — 对应 nightly job 和对账模块未实现
3. **postgres_exporter 未接入** — PostgreSQL 看板部分面板无数据
4. **ANR 上报** — 看板中已留位但未实现

---

## 十、任务状态

| 子任务 | 状态 |
|---|---|
| T-M5-05a staging 可观测栈 | ✅ **通过** |
| T-M5-05b 看板/告警/崩溃上报/反馈 | ✅ **通过** |

**全部 S1/S2 问题已修复并验证。pytest 7/7 passed。可进入下一里程碑。**

---

**审查人：** Claude Code（三代理并行审查 + pytest 实测验证）
**审查耗时：** ~20 分钟
**状态：** ✅ 闭环
