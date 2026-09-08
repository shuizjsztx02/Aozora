# T-M5-05a / T-M5-05b 第五轮审查报告（独立审查）

> **审查日期:** 2026-09-07
> **审查范围:** 全量精读核心实现文件（不依赖前四轮结论）
> **审查方法:** 逐文件精读 + 跨文件链路追踪 + Python 运行时验证（Pydantic datetime 解析）

---

## 一、审查结论

**⚠️ 条件性通过 — 存在 1 项 S1 阻断性问题。** → ✅ **已全部修复，见各问题节内 "已修复" 标注。**

整体实现质量高：架构清晰、防御层次完整、脱敏逻辑合理、告警规则维度正确。但发现 OTel Collector 的 `namespace: aozora` 导致**所有指标名被加前缀，而 dashboard 与 alert-rules 均未使用该前缀**——意味着看板全部空白、告警永远不触发。

---

## 二、问题清单

### S1 — 阻断性（必须在合入前修复）

#### S1-NEW: OTel Collector Prometheus exporter namespace 导致全链路指标名不匹配

| 层 | 配置 / 代码 | 指标名 |
|---|---|---|
| SDK 定义 | `telemetry.py` L178 | `api_requests_total` |
| OTel Collector 导出 | `otel-collector-config.yaml` L36 | `namespace: aozora` → **`aozora_api_requests_total`** |
| Prometheus 实际存储 | — | `aozora_api_requests_total` |
| Alert rule 查询 | `alert-rules.yml` L106 | `sum(rate(api_requests_total[5m]))` ❌ |
| Dashboard 查询 | `dashboards/api-slo.json` L15 | `sum(rate(api_requests_total[5m]))` ❌ |

**影响范围：** 全部 18 个 OTel 仪器 + 全部 4 个 backup textfile 指标。
- 6 个 Grafana 看板：所有面板为空（no data）。
- 全部 alert rules（backup / api_slo / android_health / sync_queue / ai_gateway / data_integrity / business_funnel）：永远不触发。
- Tempo span-metrics → Prometheus remote-write 也受 namespace 影响，derived metrics (`traces_span_metrics_*`) 同样不匹配。

**修复方案（二选一）：**

**(A) 移除 namespace**（推荐，改动最小）：
```yaml
# infra/monitoring/otel-collector-config.yaml L33-36
prometheus:
    endpoint: 0.0.0.0:8889
    # namespace: aozora   # ← 删除此行
```

**(B) 全局加前缀**（如果确实需要 namespace 隔离）：
修改 `alert-rules.yml` 所有指标引用 + 6 个 dashboard JSON 所有 `expr` 字段。工作量大且易遗漏。

**✅ 已修复（方案 A）：** 已删除 `namespace: aozora` 行。

---

### S2 — 建议性（不阻断合入，但应在后续迭代中修复）

#### S2-NEW-1: Grafana trace→logs correlation tag 名称不匹配

| 位置 | tag 值 |
|---|---|
| `request_id.py` L37 | `span.set_attribute("request.id", ...)` |
| `grafana-datasources.yaml` L38 | `tags: ["request_id"]` |

`"request.id"`（dot 分隔） ≠ `"request_id"`（underscore）。Grafana Tempo→Loki 关联查询会因找不到对应 span attribute 而返回空结果。

**修复：** 将 `grafana-datasources.yaml` L38 改为 `tags: ["request.id"]`，或把 `request_id.py` 的 attribute 名改为 `request_id`。

**✅ 已修复：** `grafana-datasources.yaml` 中 `tracesToLogs.tags` 和 `tracesToMetrics.tags` 均已改为 `["request.id"]`。

#### S2-NEW-2: `TelemetryService.count_pending_for_device` 是死代码

`service.py` L178-193 定义了 `count_pending_for_device`，但 `routes.py` 中无任何端点调用它。该方法用 `reported_at` 做 24h 窗口查询——如果未来启用，需确保 `reported_at` 的时区语义正确。

**建议：** 如当前无使用场景，删除以避免维护负担；或标注 TODO 并注明预期调用方。

**✅ 已修复：** `count_pending_for_device` 方法及其仅用的 `func`/`select` 导入已删除。

#### S2-NEW-3: `record_request` 使用原始 URL path 作为 metric label（高基数风险）

`telemetry.py` L310: `labels = {"method": method, "path": path}` — `path` 来自 `request.url.path`，包含路径参数（如 `/api/v1/books/<uuid>`）。每个唯一 UUID 都是一个新时间序列，长时间运行会导致 Prometheus 内存膨胀。

**修复建议：** 在 `main.py` 的 `log_requests` 中间件中，使用 `request.scope["route"].path`（路由模板，如 `/api/v1/books/{book_id}`）代替原始 URL path。

**✅ 已修复：** `main.py` 的 `log_requests` 中间件现在使用 `request.scope.get("route").path` 获取路由模板路径，以 `request.url.path` 作为后备（针对未匹配路由的情况）。

---

## 三、前四轮审查已修复项验证

| 编号 | 声明修复 | 本次验证 |
|---|---|---|
| S1-1 (Logs→Loki) | `telemetry.py` 挂 `OTLPLogHandler` | ✅ 确认 `LoggingHandler` 已挂到 `"aozora"` logger |
| S1-2 (Tempo span-metrics) | Prometheus `--web.enable-remote-write-receiver` | ✅ docker-compose L108 确认 |
| S1-3 (active_devices) | `HeartbeatClient.kt` 启动时发心跳 | ✅ `AozoraApp.onCreate` 中调用 |
| S1-4 (CrashFreeRateLow) | ObservableGauge + 24h 窗口 | ✅ 维度一致 |
| S1-5 (sync_queue_depth) | Gauge + pending_count | ✅ sync/routes.py 调用 `record_sync_queue_depth` |
| S1-6 (无调用点告警) | 注释 ReconciliationInconsistency | ✅ alert-rules.yml 已注释 |
| S2-2 (device_id_hash 64 chars) | 全链路扩到 64 char | ✅ model/migration/schema/client 一致 |
| S2-4 (容器数 7→8) | staging-deploy.sh | ✅ 已在 L109 修复 |
| S2-6 (db=None) | `AsyncSession \| None` | ✅ service.py L137 类型正确 |
| S2-7 (CoroutineScope) | 类成员 uploadScope | ✅ CrashReporter.kt L51 |
| Feedback model ↔ migration | String(64) 一致 | ✅ 两表 cross-table 一致 |
| TestClient base_url | `/api/v1` | ✅ test 正确 |

---

## 四、Pydantic v2 datetime 解析验证

**问题：** Android `CrashReportUploader` 发送 `reported_at` 为 epoch millis（Long），Pydantic `datetime` 字段能否正确解析？

**实测（pydantic 2.x）：**
```
millis=1725699600000 → 2024-09-07 09:00:00+00:00 ✅
secs=1725699600     → 2024-09-07 09:00:00+00:00 ✅
iso='2026-09-07T10:00:00Z' → 2026-09-07 10:00:00+00:00 ✅
```
**结论：** Pydantic v2 自动检测 epoch 量级（秒/毫秒/微秒），Android 端发送 epoch millis 可正确解析。**无问题。**

---

## 五、架构与设计评价（优点）

1. **防御层次完整** — 客户端 CrashSanitizer 脱敏 → 服务端 schema Field 长度限制 → service 层二次截断 → 内存限流 → per-IP 限流。五层防御。
2. **崩溃库独立** — `aozora-crashes.db` 与业务库分离，避免崩溃路径依赖不一致状态。
3. **隐私保护** — feedback `user_id_hash` 截断到 16 char，无法反查 users 表；crash `device_id_hash` 完整 64 char 用于防碰撞。
4. **上报策略健壮** — AuthLost 丢弃 / RateLimited 延后 / 网络错误重试 / MAX 5 次后放弃。
5. **OTel 集成标准** — 使用 OTLP gRPC 协议，不绑定特定厂商。
6. **告警维度正确** — CrashFreeRateLow 用 ObservableGauge 24h 唯一设备数做分母，维度一致。

---

## 六、校验清单

| 校验 | 结果 |
|---|---|
| `telemetry.py` 代码逻辑 | ✅ |
| `routes.py` 路由注册 | ✅ |
| `service.py` 业务逻辑 | ✅ |
| `schemas.py` 字段约束 | ✅ |
| `crash_report.py` model ↔ `0013_crash_reports.py` migration | ✅ String(64) 一致 |
| `feedback.py` model ↔ `0015_feedback.py` migration | ✅ String(16)/String(64) 一致 |
| `request_id.py` span 创建 | ✅ |
| `CrashReporter.kt` 安装/捕获/上传逻辑 | ✅ |
| `CrashSanitizer.kt` 脱敏规则 | ✅ |
| `CrashReportUploader.kt` URL 构建 | ✅ `$baseUrl/telemetry/crash` 匹配 server 路由 |
| `HeartbeatClient.kt` URL 构建 | ✅ |
| `AozoraApp.kt` 集成顺序 | ✅ install → uploadPending → heartbeat |
| `docker-compose.staging.yml` 服务定义 | ✅ 8 容器 |
| `alert-rules.yml` 公式逻辑 | ✅ 维度一致 |
| `alertmanager.yml` env 模板 | ✅ |
| `dashboards/api-slo.json` JSON 有效性 | ✅ |
| `dashboards.yml` provisioning | ✅ |
| `test_telemetry.py` 测试覆盖 | ✅ |
| Pydantic v2 epoch millis → datetime | ✅ 实测正确 |
| **OTel Collector namespace vs alert/dashboard 指标名** | ✅ **已修复（删除 namespace）** |
| **Grafana trace→logs tag `request_id` vs span attr `request.id`** | ✅ **已修复（tag 改为 `request.id`）** |
| migration revision 链 (0013, 0015) | ✅ |
| `record_*` helpers 调用点 | ✅ signup/transaction/export/duplicate 均已接入 |

---

## 七、任务状态

| 子任务 | 状态 |
|---|---|
| T-M5-05a staging 可观测栈 | ✅ **通过** |
| T-M5-05b 看板/告警/崩溃上报/反馈 | ✅ **通过** |

**全部 S1/S2 问题已修复。**

---

**审查人：** Claude Code
**状态：** ✅ 全部通过（round5 发现的问题已全部修复）。
