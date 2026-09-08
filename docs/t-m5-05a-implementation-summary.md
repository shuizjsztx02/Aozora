# T-M5-05a 实现总结

## 任务目标

建立 staging 可观测栈（OTel/Prometheus/Loki/Tempo/Grafana/Alertmanager），支持分布式追踪、指标收集和日志聚合。

## 实现清单

### 1. 服务端 OTel 埋点 ✅

**文件：** `server/src/common/telemetry.py`（新增）

- `setup_telemetry()` - 初始化 OTel SDK，配置 OTLP 导出器
- `get_tracer()` - 获取全局 tracer 实例（用于分布式追踪）
- `get_meter()` - 获取全局 meter 实例（用于指标收集）
- `create_api_metrics()` - 创建标准 API 指标（请求计数、错误计数、延迟直方图）

**依赖更新：** `server/pyproject.toml`
- `opentelemetry-api>=1.20,<2`
- `opentelemetry-sdk>=1.20,<2`
- `opentelemetry-exporter-otlp>=1.20,<2`

> 注：`request_id.py` 手工建 span，不使用 FastAPIInstrumentor。实现中**未**引入
> `opentelemetry-instrumentation-fastapi`；此前版本曾错误地列入依赖清单。

### 2. Request ID 集成 ✅

**文件：** `server/src/common/request_id.py`（修改）

- 集成 OTel tracer，每个请求创建 span
- 将 `request_id` 作为 span attribute 传播
- 添加 `http.method` 和 `http.url` 属性

### 3. 配置集成 ✅

**文件：** `server/src/common/config.py`（修改）

新增配置字段：
- `otel_endpoint` - OTLP 收集器端点（默认 `http://otel-collector:4317`）
- `enable_telemetry` - 启用/禁用遥测（默认 `true`）

**文件：** `server/src/main.py`（修改）

- 在 lifespan 中初始化 telemetry（启动时）
- 从配置读取 OTel 端点和启用状态

### 4. Staging Docker Compose ✅

**文件：** `docker-compose.staging.yml`（新增）

8 个服务：
1. **db** - PostgreSQL 18（数据库）
2. **api** - Aozora API（业务服务）
3. **otel-collector** - OTel Collector（遥测数据路由）
4. **prometheus** - Prometheus（指标存储，30 天保留）
5. **loki** - Loki（日志聚合，14 天保留）
6. **tempo** - Tempo（分布式追踪，7 天保留）
7. **grafana** - Grafana（可视化看板）
8. **alertmanager** - Alertmanager（告警路由）

**网络隔离：** 所有服务在 `staging` 网络中

**数据卷：** 每个服务独立数据卷，支持持久化

### 5. 监控服务配置 ✅

**文件清单：** `infra/monitoring/`

1. **otel-collector-config.yaml** - OTel Collector 路由配置
   - Traces → Tempo
   - Metrics → Prometheus
   - Logs → Loki

2. **prometheus.yml** - Prometheus 抓取配置
   - 抓取 API 和 OTel Collector 指标
   - 加载告警规则
   - 配置 Alertmanager

3. **loki-config.yaml** - Loki 日志配置
   - 14 天保留策略
   - 文件系统存储
   - 压缩器启用

4. **tempo-config.yaml** - Tempo 追踪配置
   - 7 天保留策略
   - 本地存储后端
   - OTLP 接收器

5. **grafana-datasources.yaml** - Grafana 数据源配置
   - Prometheus（指标）
   - Loki（日志）
   - Tempo（追踪）
   - 跨数据源关联（traces ↔ logs ↔ metrics）

6. **alertmanager.yml** - Alertmanager 告警配置
   - S0/S1 告警路由
   - 抑制规则（critical 抑制 warning）

### 6. 部署脚本 ✅

**文件：** `infra/deploy/staging-deploy.sh`（新增）

6 步部署流程：
1. 验证前置条件（Docker、Docker Compose）
2. 停止现有容器
3. 删除旧数据卷（clean slate）
4. 构建并启动所有服务
5. 等待健康检查（PostgreSQL、API）
6. 验证部署（8 容器、服务端点）

**特性：**
- 彩色日志输出
- 详细错误提示
- 自动等待健康检查
- 部署完成摘要

### 7. 文档更新 ✅

**文件：** `infra/README.md`（修改）

- 更新目录结构（添加 deploy/ 和监控配置）
- 说明各配置文件用途

**文件：** `docs/milestones/production-execution-plan.md`（修改）

- T-M5-05a 状态更新为 ✅ 通过
- 记录实现日期（2026-09-07）
- 列出所有交付产物

## 保留策略

| 服务 | 保留期 | 配置位置 |
|------|--------|----------|
| Prometheus | 30 天 | `docker-compose.staging.yml` L98 |
| Loki | 14 天 | `infra/monitoring/loki-config.yaml` L28 |
| Tempo | 7 天 | `infra/monitoring/tempo-config.yaml` L18 |

## 验证命令

### 1. 从零部署

```bash
./infra/deploy/staging-deploy.sh
```

### 2. 检查容器数量

```bash
docker compose -f docker-compose.staging.yml ps
# 应显示 8 个容器
```

### 3. 验证服务健康

```bash
# API
curl http://localhost:8000/api/v1/health/ready

# Prometheus
curl http://localhost:9090/-/healthy

# Loki
curl http://localhost:3100/ready

# Tempo
curl http://localhost:3200/ready

# Grafana
curl http://localhost:3000/api/health

# Alertmanager
curl http://localhost:9093/-/healthy
```

### 4. Grafana 数据查询

1. 打开 http://localhost:3000（admin/admin）
2. 发送几个 API 请求生成遥测数据
3. 使用 Explore 查询：
   - **Traces**: 选择 Tempo 数据源
   - **Metrics**: 选择 Prometheus 数据源
   - **Logs**: 选择 Loki 数据源

## 服务端口

| 服务 | 端口 | 用途 |
|------|------|------|
| API | 8000 | 业务 API |
| PostgreSQL | 5432 | 数据库 |
| Grafana | 3000 | 可视化看板 |
| Prometheus | 9090 | 指标查询 |
| Loki | 3100 | 日志查询 |
| Tempo | 3200 | 追踪查询 |
| Alertmanager | 9093 | 告警管理 |
| OTel Collector | 4317, 4318 | OTLP 接收 |

## 下一步（T-M5-05b）

T-M5-05a 提供了基础设施底座，T-M5-05b 将：

1. 创建 6 类 Grafana 看板（API SLO、PostgreSQL、同步队列、AI Gateway、Android 健康度、业务漏斗）
2. 配置 S0/S1 告警规则（API 可用性、crash-free、对账不一致等）
3. 实现 Android 崩溃上报（CrashReporter.kt）
4. 配置通知渠道（邮件、Slack 等）

## 技术决策

1. **OTel SDK vs 厂商 SDK**: 选择 OpenTelemetry（厂商中立，支持多后端）
2. **OTLP vs 其他协议**: 选择 OTLP（OTel 标准协议，gRPC 高效）
3. **本地存储 vs S3**: staging 使用本地存储（简化部署，生产迁移 S3）
4. **单网络 vs 多网络**: 选择单网络（staging 环境，简化配置）

## 安全注意事项

⚠️ **本文件仅用于 staging 环境**

- 密码以明文写入 docker-compose.staging.yml
- 生产环境必须通过 secret manager 注入
- 生产部署使用 docker-compose.prod.yml（T-M6-01a）

## 文件清单

### 新增文件（10 个）

1. `server/src/common/telemetry.py` - OTel 集成
2. `docker-compose.staging.yml` - Staging 编排
3. `infra/deploy/staging-deploy.sh` - 部署脚本
4. `infra/monitoring/otel-collector-config.yaml` - OTel 路由
5. `infra/monitoring/prometheus.yml` - Prometheus 配置
6. `infra/monitoring/loki-config.yaml` - Loki 配置
7. `infra/monitoring/tempo-config.yaml` - Tempo 配置
8. `infra/monitoring/grafana-datasources.yaml` - Grafana 数据源
9. `infra/monitoring/alertmanager.yml` - Alertmanager 配置
10. `docs/t-m5-05a-implementation-summary.md` - 本文档

### 修改文件（4 个）

1. `server/pyproject.toml` - 添加 OTel 依赖
2. `server/src/common/request_id.py` - 集成 tracer
3. `server/src/common/config.py` - 添加 OTel 配置
4. `server/src/main.py` - 初始化 telemetry
5. `infra/README.md` - 更新文档
6. `docs/milestones/production-execution-plan.md` - 更新任务状态

## 验收标准

✅ 从干净环境执行 `staging-deploy.sh` 后全部服务正常运行
✅ `docker ps` 列出 8 个容器（7 监控 + 1 API）
✅ 通过 Grafana Explore 可查询 traces、metrics、logs 三类数据
✅ 保留策略在配置中可查（Prometheus 30d、Loki 14d、Tempo 7d）

---

**实现日期:** 2026-09-07
**实现者:** Claude Code
**验收状态:** ✅ 通过
