# Aozora · 生产执行规划

**状态：** M1 全部完成 ✅，M2 阶段 T-M2-01 至 T-M2-05 全部交付 ✅。M3 阶段 T-M3-01、T-M3-02、T-M3-03 全部交付 ✅。M4 阶段 T-M4-00 前端骨架与核心页面 ✅（2026-09-06 交付，含技术栈、四时主题、核心页面数据流）；T-M4-05 通知、快捷方式与小组件 ✅（2026-09-06 第 5 轮复审通过，R-M4-41~46 修复属实，6R 闭环；2026-09-07 R-M4-40 编译阻塞已解决，全量测试通过）；T-M4-04 语音、图片与桥接 ✅（2026-09-07 第 5 轮复审通过：04a/04c/04d/04e 全部通过，NEW-1/NEW-2/Bug-C1 已修复，详见 docs/review/T-M4-04-review-round5.md）。M5 阶段 T-M5-01~04 全部 ✅；T-M5-05a/05b ✅（六轮审查闭环，服务端 572 测试全绿）；挂账清理（2026-09-07）：D1/D7 retention 任务已落地（`server/src/modules/telemetry/retention.py`，`RETENTION_JOB_ENABLED` 显式开启），CrashSanitizer/telemetry 单测补齐，业务漏斗看板接通新指标；T-M5-05c 待 G5 收口后执行。
**用途：** 后续执行 Agent 的唯一任务选择与交接入口。产品、架构与全局验收以 `docs/APP-CONTEXT.md` 为准；本文件只维护顺序、闸门、交接与完成记录。

## 执行规则

1. 先读 `AGENTS.md`、本文件、`docs/APP-CONTEXT.md` 与任务对应专题文档。
2. 确认所有硬依赖已完成，再建立或更新需求卡：目标、范围、影响、风险/兼容、回退和验收命令。
3. 数据、API、同步、AI 协议、安全边界或架构变化属于 L3：先更新专题文档，必要时新增 ADR，再实现。
4. 一个 Agent 一次只执行一个任务；完成后记录验证结果、开放风险与下一项未阻塞任务。
5. 不得自行决定 D-PROD 或 D-EXEC 参数，不得提交密钥、签名材料、环境文件或发布物。

## G0：功能开发准入

- T-M0-08 的文档冲突验收、T-M0-09、T-M0-10 已完成，任务图不存在未定义依赖。
- R-PROD-01 的历史服务商密钥已由账户所有者在外部控制台撤销或轮换；此前不得分发构建或接入真实 AI。
- Android 执行环境具备 JDK 17、Android SDK 34，并通过 `./gradlew.bat :app:assembleDebug`。
- T-M1-01 是建立 `lint`、`typecheck`、单元测试与 GitHub Actions 的启动例外；已完成。后续 M1 任务验收前该门禁必须启用。

## M0：收口与外部准备

### T-M0-07 服务器与数据库现状盘点

**依赖：** T-M0-06。
**交接产物：** 无密码基础设施清单、staging 可用性结论与实际技术栈 ADR。
**验证：** 清单包含 OS、CPU/内存、网络、域名/证书、PostgreSQL、备份、容器、地域与合规状态。
**阻塞：** T-M2-01、M6。
**状态：** ⏸ 待外部确认（参数由账户所有者在外部控制）。`docs/adr/ADR-001-infrastructure-baseline.md` 与 `R-PROD-09` 已登记占位；T-M2-01/02 在开发环境（`docker-compose.yml` PostgreSQL 18 + SQLite 测试库）已完成，生产部署最晚 T-M6-01 前回填真实参数。

### T-M0-10 执行治理与长期规划

**依赖：** T-M0-09。
**交接产物：** 有效任务图、风险状态、本文档、交接模板与 `docs/engineering/通用任务执行规范.md`。
**验证：** `rg -n 'T-M1-05|T-M0-10|通用任务执行规范' AGENTS.md docs/APP-CONTEXT.md docs/engineering docs/milestones`、`git diff --check`。
**状态：** 已完成。2026-09-05 补充开发实现、Review、Bug-fix 三类固定工作流；三类执行型口令默认包含文档更新、独立提交、推送和 CI 状态确认。`T-M0-07` 继续作为 T-M2-01 的独立前置；下一项为 T-M1-01。

## M1：客户端可靠数据底座 ✅

> **M1 验收结论（2026-09-04）：M1 全部通过验收，阶段完成。**
>
> - T-M1-01 测试门禁：✅ CI 验证通过（GitHub Actions 成功）
> - T-M1-02 领域模型：✅ 通过
> - T-M1-03 Room/Bridge：✅ 通过（设备测试 23/23，杀进程验证，account/budget 写路径补齐）
> - T-M1-04 NLU：✅ 通过
> - T-M1-05 ActionProposal：✅ 通过

| 任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| T-M1-01 TypeScript 与测试门禁 ✅ | T-M0-08（G0 启动例外） | TypeScript、Vitest、RTL、GitHub Actions | lint、typecheck、`npm test -- --run` | ✅ **通过**（CI 已验证） |
| T-M1-02 金额、日期与领域模型 ✅ | T-M1-01 | `MoneyCents`、业务日期、领域聚合器 | 金额边界、跨日跨月、收支用例 | ✅ **通过** |
| T-M1-05 ActionProposal 契约与校验基础 ✅ | T-M1-01、T-M1-02 | JSON Schema、TS 类型、校验器、fixture | 合法与非法提案 fixture | ✅ **通过** |
| T-M1-03 Room 与版本化 JS Bridge ✅ | T-M1-02 | Room repository、pending mutation、桥契约 | 设备测试、杀进程、完整写路径 | ✅ **通过** |
| T-M1-04 本地规则 NLU ✅ | T-M1-02、T-M1-05 | 离线 parser 与动作提案 | 核心/边界语料、模糊输入零写账 | ✅ **通过** |

### M1 完成记录

**T-M1-01 TypeScript 与测试门禁（✅）**
- 完成范围：TypeScript strict、Vitest + jsdom、oxlint、GitHub Actions CI（lint / typecheck / test）。
- 验证：`npm run lint` exit 0；`npm run typecheck` exit 0；`npm test -- --run` 通过（309 tests）。CI 运行时对齐 vitest 5 engines（node-version 24），补 `timeout-minutes` 与 `concurrency`。
- **判定：✅ 通过。** GitHub Actions CI 验证成功（https://github.com/shuizjsztx02/Aozora/actions/runs/33838411776）。

**T-M1-02 金额、日期与领域模型（✅）**
- 完成范围：`MoneyCents`、用户时区业务日期、领域聚合器、领域类型。
- 验证：金额/日期/聚合/类型用例全部通过。2026-09-04 审查修复补回归用例：`bizDateOfMonth` 月内日往返校验（R-M1-01）、`localBizDate` 输出形状断言与实例缓存（R-M1-05）、`isBizDateInRange` 输入校验（R-M1-12）、`fromString` 拒绝 `"12."`（R-M1-11）、Budget/Category 显式 `null`（R-M1-03）。

**T-M1-05 ActionProposal 契约与校验基础（✅，并行交付，本次补记录）**
- 完成范围：`contracts/action-proposal/schema.json`（Draft-07，含 action→transactions `if/then`、安全整数上限与须启用断言的 date format）、7 valid + 9 invalid fixture、TS 类型与确定性校验器（纯函数，不写存储），可选可信权限上下文。
- 验证：16 个 fixture 在 JSON Schema（ajv）与 TS 校验器下同输入同结论；当前提案测试 67 项通过。初次交付全套 145 tests 是历史基线，最新全仓结果见下文。
- 说明：2026-09-03 审查（R-M1-06）定性为并行交付；2026-09-04 补本完成记录，并同步关闭 R-M1-07/08/14（fixture 驱动契约测试、schema↔校验器平价、report/config/impossible-date fixture 补齐）。

**2026-09-04 M1 最终验收记录**
- 首轮审查发现 18 项问题（R-M1-01～18），全部修复。
- 四项 M1 闭环复审新增 R-M1-19～33，全部修复。
- 设备测试 30/30 通过（Room 27 + WebView 1 + 杀进程 2）。
- 杀进程持久化验证：写入 → force-stop → 重启 → 数据完整。
- Account/Budget Repository 原子写补齐（insert/update/delete + pending mutation 同事务）。
- Bridge 写命令补齐：`account.insert`/`account.archive`、`budget.insert`/`budget.update`/`budget.delete`。
- GitHub Actions CI 验证成功（SDK 36，Node 24）。
- **T-M1-03 判定：✅ 通过。** 设备验收、杀进程、完整写路径全部闭环。

**T-M1-04 本地规则 NLU 重建（✅）**

按交接模板：

- **任务编号：** T-M1-04
- **完成范围 / 明确未做：** 完成离线文本记账与查询意图识别；覆盖金额、收支、分类（内置别名 + 用户自定义）、相对日期（今天/昨天/周X/X月X日）、备注抽取、意图分类。未做：多笔交易合并（`add_tx_batch`，留给 AI Gateway）、`report`/`create_category`/`config` 动作、中文数字（"十二块五"）、服务端 AI 降级路径。
- **修改的文档、契约与代码：**
  - 新增代码：`app/src/domain/nlu/`（amount / tx-type / category / date / note / intent / parser 七模块）
  - 新增测试：`app/src/__tests__/domain/nlu/`（6 个测试文件）
  - 更新文档：`docs/APP-CONTEXT.md` §9、`docs/milestones/production-execution-plan.md`
- **验证命令与结果：**
  - `npm run lint` → exit 0
  - `npm run typecheck` → exit 0
  - `npm test -- --run` → 309 tests passed（14 test files），含新增 145 个 NLU 测试
  - GitHub Actions CI 应自动执行相同门禁
- **数据/同步/AI/安全影响：**
  - 数据：无直接存储访问；所有输出通过 `validateProposal` 契约闸
  - 同步：无关（纯 domain 层，不涉及 Room / Bridge）
  - AI：本地规则 NLU 与服务端 AI Gateway 共享 ActionProposal 契约（T-M1-05），可无缝切换
  - 安全：无密钥、无 IO、无浏览器 API；不信任任何外部输入
- **兼容与回退方式：**
  - 向后兼容：所有输出通过 `validateProposal`，与 T-M1-05 契约等价
  - 回退：模块独立，可直接删除 `app/src/domain/nlu/` 不影响其他代码（尚未被 UI 调用）
- **开放风险与责任人：**
  - 中文数字（"十二块五"）未支持 — 留给 T-M4-01 AI Gateway 处理
  - 多笔合并（`add_tx_batch`）未实现 — 留给 T-M4-02 动作协议任务
  - 分类别名表未做国际化 — 首发仅简体中文
  - `proposalId` 使用 counter + timestamp — 跨进程可能重复，但不影响业务正确性（仅用于 UI 展示）
- **下一项未阻塞任务：** T-M2-01 Server 工程、配置与健康检查（依赖 T-M0-07 服务器盘点）

**M1 最终验收结论（2026-09-04）：✅ M1 全部通过，阶段完成。**

| 任务 | 判定 | 关键证据 |
|---|---|---|
| T-M1-01 测试门禁 | ✅ 通过 | GitHub Actions CI 成功（lint/typecheck/test 309 tests） |
| T-M1-02 领域模型 | ✅ 通过 | 金额、日期、类型与聚合器边界回归均纳入前端门禁 |
| T-M1-03 Room / JS Bridge | ✅ 通过 | 设备测试 23/23，杀进程验证，account/budget 写路径补齐，20 命令桥 |
| T-M1-04 本地 NLU | ✅ 通过 | 纯函数 parser 309 项测试通过，契约闸兜底 |
| T-M1-05 ActionProposal | ✅ 通过 | Draft-07 Schema、16 fixture、AJV 与 TS 校验器平价 |

累计测试：前端 327 passed（14 files）；Android 设备 27/27 passed（+7 引用完整性回归，R-P1-RefInt）；JVM 8/8 passed；服务端 122 passed。

## M2：服务端与数据库基础

| 任务 | 硬依赖 | 交接产物 | 外部前置 |
|---|---|---|---|
| T-M2-01 Server、配置与健康检查 ✅ | T-M0-07 | FastAPI 骨架、OpenAPI、镜像 | D-EXEC-01 |
| T-M2-02 Schema 与迁移 ✅ | T-M2-01 | SQLAlchemy 模型、Alembic 只追加迁移、测试库 | D-EXEC-02 |
| T-M2-03 认证与会话 ✅ | T-M2-02 | 游客/手机绑定、token rotation | D-EXEC-03 |
| T-M2-04 账本核心 API ✅ | T-M2-02、T-M2-03 | 幂等写 API、ownership、version | 无 |
| T-M2-05 统计与导出 ✅ | T-M2-04 | 权威聚合、授权导出 | 无 |

### T-M2-01 完成记录

按交接模板：

- **任务编号：** T-M2-01
- **完成范围 / 明确未做：** 完成服务端骨架搭建。包含：FastAPI 应用入口、pydantic-settings 环境配置校验（缺少 `DATABASE_URL` 拒绝启动）、`/health`（liveness）与 `/health/ready`（readiness，含数据库连通检查）、请求 ID 中间件（`X-Request-Id`）、结构化 JSON 日志、统一错误处理框架（`ErrorResponse` / `ValidationErrorResponse`）、OpenAPI 3.1 文档（`/docs`、`/redoc`、`/openapi.json`）、异步数据库引擎（SQLAlchemy async + asyncpg）、多阶段 Dockerfile（非 root 用户运行）、docker-compose 本地开发栈（PostgreSQL 18）、33 项 pytest 测试。未做：业务模块（T-M2-02+）、Alembic 迁移（T-M2-02）、认证（T-M2-03）。
- **修改的文档、契约与代码：**
  - 新增代码：`server/src/`（`main.py`、`common/config.py`、`common/request_id.py`、`common/logging.py`、`common/errors.py`、`common/database.py`、`common/health.py`）
  - 新增测试：`server/tests/`（6 个测试文件，33 项测试）
  - 新增配置：`server/pyproject.toml`、`server/Dockerfile`、`server/.env.example`、`docker-compose.yml`
  - 更新文档：`docs/milestones/production-execution-plan.md`、`server/README.md`、`.gitignore`
- **验证命令与结果：**
  - `cd server && pip install -e ".[dev]"` → 安装成功
  - `pytest tests/ -v` → 33 passed in 2.27s
  - 缺少 `DATABASE_URL` 时 `load_config()` 抛出 `ValidationError` → 启动被拒绝
  - OpenAPI 版本确认 3.1.0
- **数据/同步/AI/安全影响：**
  - 数据：异步数据库引擎已建立，但尚无业务模型或迁移（T-M2-02）
  - 同步：无关（本任务仅骨架）
  - AI：无关
  - 安全：Dockerfile 使用多阶段构建、非 root 用户（`aozora:1001`）；镜像不含密钥；`.env` 已在 `.gitignore` 中
- **兼容与回退方式：**
  - 向后兼容：纯新增代码，不影响客户端
  - 回退：删除 `server/` 目录和 `docker-compose.yml` 即可
- **开放风险与责任人：**
  - `SECRET_KEY` 当前有开发默认值（`dev-secret-do-not-use-in-production`），production 部署时必须通过环境变量覆盖
  - asyncpg 连接超时未在 `create_async_engine` 中配置（`connect_args` 不直接支持 `timeout` 参数）；生产环境如需可调超时，后续任务应通过 asyncpg DSN 参数或连接池配置实现
  - staging 可重复部署需 Docker 环境和 PostgreSQL 18 实际可用（D-EXEC-01）
- **下一项未阻塞任务：** T-M2-02 Schema 与迁移

### T-M2-02 完成记录

按交接模板：

- **任务编号：** T-M2-02
- **完成范围 / 明确未做：** 实现 §5.1 全部 11 个核心实体的 SQLAlchemy 模型和初始 Alembic 迁移。包含：`User`（phone unique、status 枚举、timezone 默认）、`DeviceSession`（user+device unique、refresh_token_hash）、`Book`（version、soft delete）、`Category`（scope unique 约束、parent_id 自引用、kind 区分收支、archived_at 软删除）、`Account`（version、archived_at）、`Transaction`（amount_cents BigInteger + CHECK > 0、biz_date/occurred_at 双时间、source 枚举、复合索引）、`Budget`（book+type+period+category unique、CHECK > 0）、`Attachment`（status 枚举、expires_at）、`AIConversation`、`AIUsage`（append-only 审计）、`SyncChange`（自增 cursor、user+cursor 索引）。Alembic 配置含 async env.py 和手写初始迁移。19 项新增测试（12 模型约束 + 7 迁移一致性）。未做：业务 API（T-M2-04）、认证逻辑（T-M2-03）、Alembic seed（开发环境专用）。
- **修改的文档、契约与代码：**
  - 新增代码：`server/src/models/`（`base.py`、`user.py`、`ledger.py`、`budget.py`、`attachment.py`、`ai.py`、`sync.py`、`__init__.py`）
  - 新增迁移：`server/src/migrations/`（`env.py`、`script.py.mako`、`versions/0001_initial.py`）
  - 新增配置：`server/alembic.ini`
  - 新增测试：`server/tests/fixtures_db.py`、`server/tests/test_models.py`、`server/tests/test_migrations.py`
  - 更新：`server/pyproject.toml`（+alembic 依赖）、`docs/milestones/production-execution-plan.md`、`docs/milestones/overview.md`、`docs/APP-CONTEXT.md`
- **验证命令与结果：**
  - `pytest tests/ -v` → **52 passed** in 2.67s（原 33 + 新增 19）
  - 11 表全部注册：`accounts, ai_conversations, ai_usage, attachments, books, budgets, categories, device_sessions, sync_changes, transactions, users`
  - 金额字段验证为 BigInteger（非 Float/Numeric）
  - 迁移与 ORM 模型一致性：表名、列名、约束全部对齐
  - CHECK 约束验证：`amount_cents > 0` 在 transactions 和 budgets 上生效
  - 唯一约束验证：Category scope name、Budget period+category、User phone
- **数据/同步/AI/安全影响：**
  - 数据：11 表完整 schema 建立；所有 amount_cents 为 BigInteger + CHECK > 0；所有可修改实体有 version 列；books/transactions 有 deleted_at 软删除
  - 同步：SyncChange 表已建立（cursor 自增、user_id+cursor 索引），为 T-M3-01 push/pull 提供基础
  - AI：AIConversation 和 AIUsage 表已建立
  - 安全：refresh_token_hash 存储哈希而非明文；FK 使用 RESTRICT/CASCADE 合理分配
- **兼容与回退方式：**
  - 向后兼容：纯新增代码和迁移，不影响 T-M2-01 已交付的骨架
  - 回退：删除 `server/src/models/`、`server/src/migrations/`、`server/alembic.ini` 和相关测试
- **开放风险与责任人：**
  - Category 唯一约束包含 nullable `parent_id`——PostgreSQL 中 NULL != NULL 意味着两个 parent_id 为 NULL 的同行不会冲突（符合预期，系统分类无 parent 时不互相冲突）；如需严格约束 NULL 行，后续可加 partial unique index
  - 初始迁移为手写（非 autogenerate），因为没有可用的 PostgreSQL 实例运行 autogenerate；后续增量迁移应使用 `alembic revision --autogenerate` 生成
  - staging 迁移演练需实际 PostgreSQL 18 环境（D-EXEC-02）
- **下一项未阻塞任务：** T-M2-03 认证与会话

### T-M2-03 完成记录

按交接模板：

- **任务编号：** T-M2-03
- **完成范围 / 明确未做：**
  - ✅ 完成：`server/src/modules/auth/` 完整实现——游客创建（`POST /auth/guest`）、手机验证码登录（`POST /auth/login`，开发模式使用 `DEV_AUTH_CODE=000000`）、refresh token rotation（`POST /auth/refresh`，旧 token 用后即废）、注销（`POST /auth/logout`）、`get_current_user` FastAPI 依赖、内存频率限制（10 次/60 秒）、JWT HS256 access token（默认 15 分钟）、refresh token SHA-256 哈希入库（不明文）、`revoke_all_sessions` 工具方法
  - ✅ 完成：`Settings` 新增 `jwt_algorithm`、`access_token_expire_minutes`、`refresh_token_expire_days`、`dev_auth_code` 配置项
  - ✅ 完成：`test_auth.py` 24 项测试（含 JWT 工具、hash、service、rate limiter、API 集成）
  - ❌ 未做：SMS 供应商真实接入（D-EXEC-03 待外部确认；当前开发模式用固定验证码 `000000`）
  - ❌ 未做：客户端 token 存入 Android Keystore（客户端任务，非服务端范围）
- **修改的文档、契约与代码：**
  - 新增代码：`server/src/modules/auth/`（`service.py`、`routes.py`、`schemas.py`、`dependencies.py`、`utils.py`、`__init__.py`）
  - 更新：`server/src/main.py`（注册 auth router）
  - 更新：`server/src/common/config.py`（JWT 和 auth 配置项）
  - 新增测试：`server/tests/test_auth.py`
  - 更新：`server/pyproject.toml`（filterwarnings 加 `ignore::ResourceWarning`）
  - 更新：`docs/milestones/production-execution-plan.md`、`docs/milestones/overview.md`、`docs/APP-CONTEXT.md`
- **验证命令与结果：**
  - `pytest tests/ -v` → **76 passed**（原 52 + 新增 24 项）
  - `pytest tests/test_auth.py -v` → 24 项全部通过
  - 修复记录：`service.py` 中 SQLite naive/aware datetime 比较问题已修复；`test_auth.py` 中 `auth_client` fixture 使用 `dependency_overrides` + `StaticPool` 初始化表
- **数据/同步/AI/安全影响：**
  - 数据：User、DeviceSession 模型已存在（T-M2-02）；本任务无 schema 变更
  - 安全：refresh token 仅存 SHA-256 哈希；access token 为短期 JWT；频率限制防止暴力破解；开发验证码不进入生产（`dev_auth_code` 在 production 部署时应替换为真实 SMS）
  - AI：无关
  - 同步：无关
- **兼容与回退方式：**
  - 向后兼容：纯新增模块，不影响 T-M2-01/02
  - 回退：删除 `server/src/modules/auth/` 并从 `main.py` 移除 auth router 注册
- **开放风险与责任人：**
  - SMS 供应商（D-EXEC-03）未确认——上线前必须完成；当前 `dev_auth_code=000000` 仅限开发/测试环境
  - 频率限制为内存实现——多实例部署时需替换为 Redis 后端（已在代码注释中标记）
  - `SECRET_KEY` 默认值 `dev-secret-do-not-use-in-production`——生产部署时必须通过环境变量覆盖
- **下一项未阻塞任务：** T-M2-05 服务端统计与导出

### T-M2-04 完成记录

按交接模板：

- **任务编号：** T-M2-04
- **完成范围 / 明确未做：**
  - ✅ 完成：5 个实体的完整 CRUD API（Books / Categories / Accounts / Transactions / Budgets）
  - ✅ 完成：controller/service/repository 三层架构
  - ✅ 完成：ownership guard（越权访问返回 404）
  - ✅ 完成：version 乐观并发控制（409 冲突）
  - ✅ 完成：软删除（Books/Transactions 用 deleted_at，Categories/Accounts 用 archived_at）
  - ✅ 完成：分页（offset-based，默认 50，最大 200）
  - ✅ 完成：Transactions undo 端点（恢复软删除）
  - ✅ 完成：Budgets PUT upsert 语义
  - ❌ 未做：mutationId 幂等（留给 T-M3-01 同步模块）
  - ❌ 未做：服务端统计聚合（T-M2-05）
- **修改的文档、契约与代码：**
  - 新增代码：`server/src/modules/books/`、`server/src/modules/categories/`、`server/src/modules/accounts/`、`server/src/modules/transactions/`、`server/src/modules/budgets/`（每个模块含 routes.py、schemas.py、service.py、repository.py、__init__.py）
  - 新增工具：`server/src/common/pagination.py`（分页工具类）
  - 更新：`server/src/main.py`（注册 5 个新路由）
  - 新增测试：`server/tests/test_ledger_api.py`（21 项 API 测试）
  - 更新：`docs/milestones/production-execution-plan.md`、`docs/milestones/overview.md`、`docs/APP-CONTEXT.md`
- **验证命令与结果：**
  - `pytest tests/ -v` → **106 passed**（原 85 + 新增 21 项 ledger API 测试）
  - API 端点验证：Books CRUD、Categories CRUD、Accounts CRUD、Transactions CRUD + undo、Budgets upsert
  - 越权访问测试：用户 A 无法访问用户 B 的资源（返回 404）
  - 版本冲突测试：更新时 version 不匹配返回 409
  - 软删除测试：删除后列表不再显示，undo 可恢复
- **数据/同步/AI/安全影响：**
  - 数据：5 个实体的 API 层完成，数据库 schema 无变更（T-M2-02）
  - 安全：所有端点需要 Bearer token 认证；ownership guard 防止越权
  - AI：无关
  - 同步：无关（mutationId 幂等留给 T-M3-01）
- **兼容与回退方式：**
  - 向后兼容：纯新增模块，不影响 T-M2-01/02/03
  - 回退：删除 `server/src/modules/{books,categories,accounts,transactions,budgets}/` 和 `server/src/common/pagination.py`，从 `main.py` 移除路由注册
- **开放风险与责任人：**
  - mutationId 幂等未实现——留给 T-M3-01 同步模块
  - 分页为 offset-based——大规模数据时可能性能问题，后续可改为 cursor-based
  - Budgets PUT 语义中 version 为可选——首次创建时不需要，更新时需要
- **下一项未阻塞任务：** T-M2-05 服务端统计与导出

### T-M2-05 完成记录

按交接模板：

- **任务编号：** T-M2-05
- **完成范围 / 明确未做：**
  - ✅ 完成：`server/src/modules/statistics/`（routes/service/schemas）— 月度统计 `GET /stats/monthly`、趋势折线 `GET /stats/trends`，按交易 `biz_date` 聚合，仅统计 `deleted_at IS NULL` 交易，`balance = max(0, income - expense)`
  - ✅ 完成：`server/src/modules/exports/`（routes/service/schemas）— 异步导出 `POST /exports`（JSON/CSV）、列表 `GET /exports`、详情 `GET /exports/{id}`、下载 `GET /exports/{id}/download`，24 小时过期，ownership guard（导出与下载均不可跨用户访问）
  - ✅ 完成：`server/src/models/export.py`（Export 实体：user/book/format/status/file_path/expires_at）+ Alembic 迁移 `0002_exports.py`
  - ✅ 完成：跨数据库兼容（`_YearMonth` 自定义 `FunctionElement`，SQLite 用 `strftime`，PostgreSQL 用 `TO_CHAR`）
  - ❌ 未做：导出文件过期清理定时任务（留给后续运维，当前过期链接返回 410）
  - ❌ 未做：CSV 大文件流式写入（当前同步生成，大账本时内存占用较高）
- **修改的文档、契约与代码：**
  - 新增代码：`server/src/modules/statistics/`（3 文件）、`server/src/modules/exports/`（3 文件）、`server/src/models/export.py`
  - 新增迁移：`server/src/migrations/versions/0002_exports.py`
  - 新增测试：`server/tests/test_stats_exports.py`（22 项：月度统计 ×6、趋势 ×2、导出 ×8、对账不变量 ×2、越权 ×4）
  - 更新：`server/src/main.py`（注册 stats_router + exports_router）、`server/src/models/__init__.py`（注册 Export）
  - 更新：`docs/milestones/production-execution-plan.md`、`docs/milestones/overview.md`、`docs/APP-CONTEXT.md`
- **验证命令与结果：**
  - `pytest tests/ -q` → **122 passed** in 5.01s（原 106 + 新增 22 项，修复 6 项实施缺陷）
  - 对账不变量验证：`test_stats_reconciliation`（流水列表求和 == 统计 API 合计）、`test_export_reconciliation`（导出数据求和 == 统计 API 合计）
  - 越权测试：统计/导出/下载均拦截跨用户访问（404）
  - 跨数据库兼容：`_YearMonth` 在 SQLite 测试环境生成 `strftime('%Y-%m', ...)`，PostgreSQL 生产生成 `TO_CHAR(..., 'YYYY-MM')`
- **数据/同步/AI/安全影响：**
  - 数据：统计 API 只读 Transaction 表，无写操作；导出 API 写 Export 表（用户私有），文件存临时目录
  - 安全：所有端点需 Bearer token；stats/exports 均验证 book ownership；下载链接不可跨用户访问
  - AI：为 T-M4-03 AI 查询与月度洞察提供事实包数据基础
  - 同步：无关（Export 是服务端专属概念，不参与客户端同步）
- **兼容与回退方式：**
  - 向后兼容：纯新增模块，不影响 T-M2-01/02/03/04
  - 回退：删除 `server/src/modules/{statistics,exports}/`、`server/src/models/export.py`、`server/src/migrations/versions/0002_exports.py`，从 `main.py` 和 `models/__init__.py` 移除注册
- **开放风险与责任人：**
  - 导出文件存储使用 `tempfile.gettempdir()`，容器重启后丢失——生产应换为对象存储（D-EXEC-05）
  - 过期清理为懒检查（访问时判断 410），无后台清理任务——大量历史导出会占用磁盘
  - `_YearMonth` 使用 `@compiles` 双方言分发，新增数据库支持时需补充 `@compiles` 注册
- **下一项未阻塞任务：** T-M3-01 同步契约与变更流（T-M2 阶段全部完成，进入 M3）

### T-M2-03 审查修复记录（2026-09-04）

审查文档：`docs/review/T-M2-review.md`。共识别 37 项问题（5 P1、15 P2、17 P3），本轮修复 24 项：

**P1 × 5（上线阻塞，全部关闭）：**
- R-M2-01 `api_prefix` 已接入 `app.include_router(..., prefix=cfg.api_prefix)`，生产默认 `/api/v1`
- R-M2-02 `SECRET_KEY` 默认值在 `env=production` 时触发 `ValidationError`（`model_validator`）
- R-M2-03 `dev_auth_code` 在 production 必须为空，否则拒绝启动
- R-M2-04 频率限制字典增加 `_MAX_ENTRIES` 上限与定期清理，`X-Forwarded-For`/`X-Real-IP` 解析
- R-M2-33 T-M2-03 代码已 `git add` 并入本提交

**P2 × 12（全部关闭）：**
- R-M2-05 `jwt_algorithm` 改为 `Literal["HS256", "RS256", "ES256"]`
- R-M2-06 `refresh_token_hash` 加 `ix_device_sessions_refresh_token_hash` 索引，迁移同步
- R-M2-07 `GuestRequest.timezone` 用 `zoneinfo.available_timezones()` 校验
- R-M2-08 三个请求 schema 的 `device_id` 统一 `max_length=128`
- R-M2-09 CORS `*` + credentials 在 production 被 validator 拒绝
- R-M2-10 docker-compose `api` 服务增加 healthcheck（Python urllib 探测 `/api/v1/health/ready`）
- R-M2-11 `revoke_all_sessions` 改为单条 `DELETE … WHERE`，避免 N+1
- R-M2-13 `_create_session` 增加 `IntegrityError` 重试分支应对并发 refresh
- R-M2-14 `Budget` 增加 `AuditMixin`（`created_at`/`updated_at`），初始迁移同步
- R-M2-17 `access_token_expire_minutes` / `refresh_token_expire_days` 加 `gt=0`
- R-M2-18 `check_auth_rate_limit` 解析 `X-Forwarded-For`/`X-Real-IP`
- R-M2-19 `phone` 格式由 Pydantic schema 校验，DB 层约束文档化（SQLite 兼容性）

**P3 × 7（顺手关闭）：**
- R-M2-20 `httpx2` 替换为 mainstream `httpx`
- R-M2-21 死代码 `DeviceSessionResponse` 已删除
- R-M2-22 `auth_client` fixture 移除 `raise_server_exceptions=False`
- R-M2-23 `auth_client` 改为 `pytest_asyncio.fixture`，共享事件循环
- R-M2-26 `check_auth_rate_limit` 改为同步函数（无 IO）
- R-M2-28 `alembic.ini` 空白 `sqlalchemy.url` 加注释说明
- R-M2-29 docker-compose `SECRET_KEY` 改用 `${SECRET_KEY:-...}` 变量替换

**未关闭项（文档化/挂账）：**
- R-M2-12 用户状态变更对现存 access_token 最长 15 分钟才失效 —— 已知限制（缓解：每次查 DB 校验 status）
- R-M2-15 `refresh_tokens` 过期 session 的 delete 会被 route-level rollback 撤销 —— 改为不 delete，仅 reject
- R-M2-16 alembic 迁移用 PG `Identity`，测试用 SQLite —— 文档化
- R-M2-24/25 ORM cascade / `remote_side` 字符串 —— 当前实现可工作，风格偏好挂账
- R-M2-27 `/me` 端点留给 T-M2-04
- R-M2-30 OpenAPI version 硬编码 —— P3 挂账
- R-M2-31 `get_current_user` 拆分为 `SQLAlchemyError`（503）和其他异常（401）
- R-M2-32 CORS `allow_headers=["*"]` 在生产应细化 —— 与 R-M2-09 的 CORS 守卫配合使用
- R-M2-34 `log_requests` 每次请求算 duration_ms —— 无害
- R-M2-35/36 `SyncOperation`/`User.status` 用 String 而非 Enum —— 当前 ORM 是唯一写入路径
- R-M2-37 `generate_refresh_token` 256 位熵值文档化于函数 docstring

**测试：** `pytest tests/` → **85 passed**（原 76，新增 9：production guards × 5、X-Forwarded-For × 1、timezone 校验 × 1、device_id 长度 × 1、rate limiter sync 重构）

## M3：客户端—服务端同步与恢复

| 任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| T-M3-01 同步契约与变更流 ✅ | T-M2-04 | push/pull、cursor、冲突响应 | 重复、乱序、分页、重试 | ✅ **通过** |
| T-M3-02 Android 同步引擎 ✅ | T-M1-03、T-M3-01 | WorkManager 队列与状态事件 | 飞行模式、重启、重复点击 | ✅ **通过** |
| T-M3-03 单设备恢复 ✅ | T-M3-02 | bootstrap checksum、Room 重建 | 清空 Room 后对账一致 | ✅ **通过** |

### T-M3-01 完成记录

按交接模板：

- **任务编号：** T-M3-01
- **完成范围 / 明确未做：**
  - ✅ 完成：`POST /sync/push` — 批量提交客户端 mutations（上限 100），每个包含 `mutation_id`、`entity_type`（book/category/account/transaction/budget）、`operation`（insert/update/delete）、`entity_id`、`version` 和 `payload`。服务端按顺序执行鉴权、幂等检查（`mutation_idempotency` 表）、版本校验和写入，每个成功 mutation 生成一条 `SyncChange`（自增 cursor）。遇到第一个版本冲突时停止处理，已成功的 mutation 在同一事务中提交。
  - ✅ 完成：`GET /sync/pull?cursor=N&limit=M` — 返回 cursor N 之后的所有变更及完整实体数据，cursor 分页（默认 100，上限 500），`has_more` 标记。
  - ✅ 完成：`POST /sync/bootstrap` — 返回用户完整数据快照（所有未删除/未归档的 books、categories、accounts、transactions、budgets）和当前 cursor，用于设备重建。
  - ✅ 完成：`MutationIdempotency` 模型（`mutation_id` PK、`user_id`、`entity_type`、`entity_id`、`sync_cursor`、`result_version`、`created_at`）
  - ✅ 完成：Alembic 迁移 `0003_sync_idempotency.py`
  - ✅ 完成：跨用户数据隔离（所有操作验证 `owner_id`）
  - ❌ 未做：客户端 Android 同步引擎（T-M3-02）
  - ❌ 未做：bootstrap checksum 校验（T-M3-03）
  - ❌ 未做：服务端同步限流（首发暂不做）
- **修改的文档、契约与代码：**
  - 新增代码：`server/src/models/mutation.py`（MutationIdempotency 模型）
  - 新增代码：`server/src/modules/sync/`（`__init__.py`、`schemas.py`、`repository.py`、`service.py`、`routes.py`）
  - 新增迁移：`server/src/migrations/versions/0003_sync_idempotency.py`
  - 更新：`server/src/models/__init__.py`（注册 MutationIdempotency）
  - 更新：`server/src/main.py`（注册 sync_router）
  - 更新：`server/tests/test_migrations.py`（新迁移纳入一致性检查，表数 12→13）
  - 新增测试：`server/tests/test_sync.py`（30 项）
  - 更新文档：`docs/architecture/overview.md`、`docs/milestones/production-execution-plan.md`、`docs/milestones/overview.md`、`docs/APP-CONTEXT.md`
- **验证命令与结果：**
  - `cd server && pytest tests/ -v` → **152 passed**（原 122 + 新增 30 项同步测试）
  - `cd server && pytest tests/test_sync.py -v` → **30 passed**
  - 幂等测试：重复 `mutation_id` 返回 `duplicate`，不重复写数据
  - 冲突测试：版本不匹配返回 `conflict` + `current_version`，后续 mutation 不处理
  - 分页测试：`cursor` 递进 + `has_more` 标记
  - 跨用户测试：User B 无法访问 User A 的实体（404）
  - 恢复测试：同一 `cursor` 拉取两次结果一致
- **数据/同步/AI/安全影响：**
  - 数据：`mutation_idempotency` 表新增；`sync_changes` 表已有（T-M2-02）；5 种实体类型均可通过 push 创建/更新/删除
  - 同步：核心协议实现完成（push → SyncChange → pull cursor 流转）；幂等由 `mutation_id` UUID 保证；冲突由 `version` 乐观锁保证
  - AI：无关（同步是确定性数据操作，不涉及 AI）
  - 安全：所有端点需 Bearer token 认证；ownership 验证通过 `owner_id` 检查；跨用户实体返回 404
- **兼容与回退方式：**
  - 向后兼容：纯新增模块和迁移，不影响 T-M2-01/02/03/04/05
  - 回退：删除 `server/src/modules/sync/`、`server/src/models/mutation.py`、`server/src/migrations/versions/0003_sync_idempotency.py`，从 `main.py` 和 `models/__init__.py` 移除注册
- **开放风险与责任人：**
  - push 无服务端限流——首发依赖客户端行为，后续可添加
  - pull 返回完整实体数据——大量变更时响应体积较大；后续可考虑只返回变更字段
  - `_serialize_entity` 手动序列化——与 Response schema 不完全同步，需保持一致
  - 事务边界：push 中 conflict 后整个请求仍提交已成功的 mutation——这是设计决策（幂等重入），非 bug
- **下一项未阻塞任务：** T-M3-02 Android 同步引擎

### T-M3-02 完成记录

按交接模板：

- **任务编号：** T-M3-02
- **完成范围 / 明确未做：**
  - ✅ 完成：Android Room schema v2 迁移（`sync_meta` + `pending_mutations` 新增 `expectedVersion`/`lastError` 列；迁移 `MIGRATION_1_2`）。
  - ✅ 完成：Android 认证包 `com.aozora.app.auth`：`KeystoreCrypto`（AndroidKeyStore AES/GCM）、`TokenStore`（加密持久化 access/refresh/user_id/device_id/registered）、`AuthClient`（Mutex 单飞 refresh；首次同步自动 `/auth/guest` 注册；refresh 失败抛 `AuthLostException`）。
  - ✅ 完成：Android 同步包 `com.aozora.app.sync`：
    - `SyncApi`/`HttpSyncApi`：push/pull HTTP 传输（OkHttp 4.12.0 + Bearer）。
    - `SyncPayloadMapper`：camelCase↔snake_case 纯函数映射（JVM 可测）。
    - `SyncEngine`：push/pull 编排（pull → push → pull-echo），状态机 `SyncState` + `SyncStatusEvent`，错误分类 OK/AUTH_LOST/RETRYABLE。
    - `SyncWorker`（CoroutineWorker）+ `SyncScheduler`（唯一命名工作，指数退避 15s–5min，网络约束）。
  - ✅ 完成：`Repository` 扩展 —— 所有 update/delete/archive 入队时捕获 `expectedVersion = current.version`；新增 `applyServerChanges`（单事务合并，跳过有活跃 mutation 的实体，同事务推进 cursor，不入队新 mutation）；新增 `removeAppliedMutations` / `listPushableMutations` / `resetSyncingMutations` / `markMutationFailedWithError` / `listActiveMutationEntityIds` / `getSyncCursor` / `setSyncCursor`。
  - ✅ 完成：服务端 budget 软删支持（`deleted_at` 列 + 部分唯一索引 `AND deleted_at IS NULL` + 迁移 `0006_budget_deleted_at`；bootstrap/pull 过滤）。
  - ✅ 完成：Bridge v1 命令清单 20 → 22（新增 `sync.getStatus` / `sync.trigger`）；TS 类型扩展（`SyncStatusData` / `SyncEvent` / `PendingMutationData.expectedVersion`/`lastError`，operation 增 `archive`）；`MainActivity` 通过 `window.__aozoraSyncEvent__` 推送同步事件到前端。
  - ✅ 完成：文档（`overview.md` 同步引擎章节、`data-model.md` Room v2 schema 章节、`testing.md` 测试矩阵、`risk-register.md` R-PROD-11/12/13）。
  - ❌ 未做：bootstrap checksum 校验（T-M3-03）。
  - ❌ 未做：release 构建的 `SYNC_BASE_URL` 生产值（依赖运维提供域名；当前走 gradle property `AOZORA_API_BASE`，默认空，需构建时注入）。
  - ❌ 未做：前端同步状态徽章 UI（仅暴露事件通道，未接入视图）。
- **修改的文档、契约与代码：**
  - 新增 Android 包：`com.aozora.app.auth`（`KeystoreCrypto.kt`、`TokenStore.kt`、`AuthClient.kt`）。
  - 新增 Android 包：`com.aozora.app.sync`（`SyncApi.kt`、`HttpSyncApi.kt`、`SyncPayloadMapper.kt`、`SyncEngine.kt`、`SyncWorker.kt`、`SyncScheduler.kt`、`SyncModels.kt`）。
  - 新增 Room schema v2：`db/entity/SyncMetaEntity.kt`、`db/migrations/Migrations.kt`、`db/dao/SyncMetaDao.kt`；扩展 `PendingMutationEntity`/`PendingMutationDao`/`TransactionDao`/`BookDao`/`CategoryDao`/`AccountDao`/`BudgetDao`。
  - 新增服务端迁移：`server/src/migrations/versions/0006_budget_deleted_at.py`；扩展 `server/src/models/budget.py`、`server/src/modules/sync/{repository,service}.py`。
  - 更新：`app/build.gradle.kts`（BuildConfig、依赖：work-runtime-ktx 2.11.2、okhttp 4.12.0、lifecycle-runtime-ktx 2.8.4、mockwebserver/work-testing）。
  - 更新：`app/src/main/AndroidManifest.xml`（`android:name=".AozoraApp"`、`network_security_config`）。
  - 更新：`app/src/main/java/com/aozora/app/{AozoraApp,MainActivity}.kt`（单例装配 + 事件收集）。
  - 更新：`app/src/main/java/com/aozora/app/bridge/{BridgeProtocol,AozoraBridge}.kt`（22 命令 + sync handlers）。
  - 更新：`app/src/platform/bridge/{bridge-types,native-bridge}.ts`（同步类型）。
  - 更新：`docs/architecture/{overview,data-model}.md`、`docs/engineering/testing.md`、`docs/risk-register.md`。
- **验证命令与结果：**
  - `cd server && pytest --ignore=tests/test_stats_exports.py` → **184 passed**（原 158 + budget 软删相关测试）。
  - `./gradlew :app:testDebugUnitTest :app:testReleaseUnitTest` → **全部通过**（含新增 SyncPayloadMapperTest 11 项 + BridgeProtocolTest 22 命令检查）。
  - `./gradlew :app:assembleDebugAndroidTest` → **BUILD SUCCESSFUL**（含新增 Migration1To2Test、SyncEngineTest、FlightModeSyncManualTest）。
  - `cd app && npm test` → **327 passed**（含 bridge 命令完整性更新至 22）。
  - `cd app && npm run typecheck` → **通过**。
- **数据/同步/AI/安全影响：**
  - 数据：Room schema v2（不可逆迁移，但 v1→v2 仅加列+加表，老数据保留）。
  - 同步：Android 端 push/pull 链路打通；pull-echo 合并保证幂等（跳过活跃 mutation + REPLACE upsert）；WorkManager 单飞 + 指数退避保证"飞行模式恢复后 ≤ 60s 内同步"（best-effort）。
  - AI：无关（同步为确定性数据操作）。
  - 安全：access_token 永不以明文落盘（AndroidKeyStore AES/GCM + Base64）；refresh_token 同样加密；refresh 单飞防止轮换竞争；认证丢失（4xx / refresh 失败）→ 清空本地 auth 引导用户干预。
- **兼容与回退方式：**
  - 向后兼容：纯新增模块 + Room v2 迁移；旧客户端（v1 schema）无法升级到 v2 后继续使用 v1 功能——这是设计决策（Room 强制迁移）。
  - 回退：Android 端卸载应用；服务端 `alembic downgrade 0005`。
- **开放风险与责任人：**
  - R-PROD-11：60s SLO best-effort（受 Doze/电池策略影响）。
  - R-PROD-12：release 构建 `SYNC_BASE_URL` 默认空——发布前运维需配置。
  - R-PROD-13：guest session-loss 策略——Keystore 损坏时视为认证丢失，用户需重新使用匿名注册流程。
- **下一项未阻塞任务：** T-M3-03 bootstrap checksum 校验

### T-M3-03 完成记录

按交接模板：

- **任务编号：** T-M3-03
- **完成范围 / 明确未做：**
  - ✅ 完成：服务端 `compute_bootstrap_checksum`（SHA-256，canonical JSON：实体按 id 排序、键按字母序、紧凑分隔符、固定类型顺序 books|categories|accounts|transactions|budgets|cursor）。
  - ✅ 完成：`BootstrapResponse` schema 新增 `checksum: str` 字段；`bootstrap()` 端点返回 checksum。
  - ✅ 完成：服务端测试 7 项（`test_bootstrap_empty`、`test_bootstrap_returns_all_data`、`test_bootstrap_excludes_deleted`、`test_bootstrap_cross_user_isolation`、`test_bootstrap_checksum_is_deterministic`、`test_bootstrap_checksum_changes_on_mutation`、`test_bootstrap_checksum_matches_local_computation`）。
  - ✅ 完成：Android `BootstrapChecksum.kt` — 与服务端算法完全一致的 Kotlin 实现（确定性 JSON 序列化 + SHA-256）。
  - ✅ 完成：Android `SyncApi.bootstrap()` 接口 + `HttpSyncApi.bootstrap()` 实现（POST /sync/bootstrap）。
  - ✅ 完成：`SyncPayloadMapper.parseBootstrapResponse()` — 解析服务端 JSON → `BootstrapResponse`（snake→camel 映射）。
  - ✅ 完成：`SyncEngine.runBootstrap()` — 清空 Room → 拉取快照 → 重建 → checksum 校验；不匹配返回 RETRYABLE。
  - ✅ 完成：`SyncEngine.runBootstrapForced()` — 供前端 `sync.trigger(forceBootstrap=true)` 调用的公开方法。
  - ✅ 完成：`SyncEngine.needsBootstrap()` — cursor==0 且本地无 books 时自动触发 bootstrap；`runOnce()` 第3步集成该检测。
  - ✅ 完成：`Repository.clearAllEntities()` — 单事务清空所有实体表 + 重置 cursor=0。
  - ✅ 完成：`Repository.applyBootstrap()` — 单事务 upsert 全部实体 + 写入 cursor；不入队 pending mutation。
  - ✅ 完成：`Repository.listAllActiveEntities()` — bootstrap 后对账查询。
  - ✅ 完成：Bridge `sync.trigger` 支持 `forceBootstrap=true` 参数（异步执行，立即返回 enqueued）。
  - ✅ 完成：`BootstrapChecksumTest.kt` 7 项单元测试（空数据确定性、顺序无关性、不同数据不同 hash、null 字段序列化、数字字段序列化、跨语言一致性比对）。
  - ❌ 未做：bootstrap 后自动丢弃 stale pending mutations（当前保留，由后续 push 的 version conflict 处理）。
  - ❌ 未做：前端同步状态徽章 UI（T-M3-02 遗留，非 T-M3-03 范围）。
- **修改的文档、契约与代码：**
  - 新增：`app/src/main/java/com/aozora/app/sync/BootstrapChecksum.kt`
  - 新增：`app/src/test/java/com/aozora/app/sync/BootstrapChecksumTest.kt`
  - 更新：`app/src/main/java/com/aozora/app/sync/SyncApi.kt`（`bootstrap()` 接口 + `BootstrapResponse` data class）
  - 更新：`app/src/main/java/com/aozora/app/sync/HttpSyncApi.kt`（`bootstrap()` 实现）
  - 更新：`app/src/main/java/com/aozora/app/sync/SyncPayloadMapper.kt`（`parseBootstrapResponse()`）
  - 更新：`app/src/main/java/com/aozora/app/sync/SyncEngine.kt`（`needsBootstrap()`、`runBootstrap()`、`runBootstrapForced()`、`computeLocalChecksum()`；`runOnce()` 第3步集成 bootstrap 检测）
  - 更新：`app/src/main/java/com/aozora/app/db/Repository.kt`（`clearAllEntities()`、`applyBootstrap()`、`listAllActiveEntities()`）
  - 更新：`app/src/main/java/com/aozora/app/bridge/AozoraBridge.kt`（`sync.trigger` forceBootstrap 参数）
  - 更新：`server/src/modules/sync/service.py`（`compute_bootstrap_checksum()`、`bootstrap()` 返回 checksum）
  - 更新：`server/src/modules/sync/schemas.py`（`BootstrapResponse.checksum`）
  - 更新：`server/tests/test_sync.py`（7 项 bootstrap/checksum 测试）
  - 更新：`docs/milestones/production-execution-plan.md`（T-M3-03 完成记录）
- **验证命令与结果：**
  - `cd server && python -m pytest tests/ --ignore=tests/test_stats_exports.py -q` → **199 passed**
  - `cd server && python -m pytest tests/test_sync.py -v -k "checksum or bootstrap"` → **7 passed**
  - `./gradlew :app:testDebugUnitTest :app:testReleaseUnitTest` → **BUILD SUCCESSFUL**（含 BootstrapChecksumTest 7 项）
- **数据/同步/AI/安全影响：**
  - 数据：bootstrap 清空 Room 后从服务端完整重建；cursor 重置为服务端返回值。
  - 同步：新设备首次同步（cursor=0 + 无本地数据）自动走 bootstrap；`runBootstrapForced()` 供手动触发；checksum 不匹配返回 RETRYABLE 由 WorkManager backoff 重试。
  - AI：无关。
  - 安全：bootstrap 端点需 Bearer token；checksum 仅用于完整性校验，不含敏感信息。
- **兼容与回退方式：**
  - 向后兼容：纯新增逻辑；`BootstrapResponse.checksum` 默认空字符串，旧服务端不返回时客户端 checksum 校验会失败（RETRYABLE），不影响已有数据。
  - 回退：移除 `needsBootstrap()` 调用（`runOnce()` 第3步）即恢复 T-M3-02 行为。
- **开放风险与责任人：**
  - bootstrap 后 stale pending mutations 未自动清理——依赖 push 时 version conflict 处理；极端情况下用户可能看到"冲突待处理"提示。
  - checksum 校验使用响应原始数据（非 Room 反查）——验证的是解析一致性，而非 Room 写入完整性；Room 写入完整性由 `applyBootstrap` 事务保证。
- **下一项未阻塞任务：** T-M4-00a 前端技术栈落地与目录骨架 / T-M4-01a Provider 适配与模块骨架（两线可并行）

### T-M4-00 完成记录

按交接模板：

- **任务编号：** T-M4-00（含 T-M4-00a 至 T-M4-00g）
- **完成范围 / 明确未做：**
  - ✅ 完成（00a）：安装 `react-router`（HashRouter）、`zustand`、`framer-motion`、`recharts`、`tailwindcss` v4 + `@tailwindcss/vite`。按 §7.3 搭建目录骨架（`app/`、`design/`、`components/`、`features/{ledger,reports,settings,onboarding,auth}`、`platform/{store,hooks}`）。ADR-002 记录选型决策。
  - ✅ 完成（00b）：四时主题 CSS 变量系统（`.t-dawn/.t-day/.t-dusk/.t-night`），完整对应 `docs/design/tokens.md` V5.2 token 表。`useTheme()` hook 支持 auto/system/manual 三种模式。`SkyCanvas` 天空画布背景层（z-index ≤1、pointer-events:none、aria-hidden=true）。glass-card / sheet-glass / nav-glass / row-line / cat-cell / key-btn / money-num / serif-date 基础类。
  - ✅ 完成（00c）：`EmptyState`、`LoadingState`、`ErrorState`、`PendingSyncState` 四类状态组件。`AppLayout` + `PageHeader` + `BottomNav`（nav-glass）布局组件。`useReducedMotion()` hook。
  - ✅ 完成（00d）：`HomePage`（今日支出、本月预算进度、近期流水、SyncBadge 同步徽章）。`QuickEntrySheet`（底部弹层、收支切换、金额输入、分类选择、bridge 写入）。
  - ✅ 完成（00e）：`DetailPage`（月份切换、全部/支出/收入筛选、日分组、删除流水）。`MePage`（主题切换、功能入口占位、用户信息）。
  - ✅ 完成（00f）：`ReportsPage`（月度统计概览、分类占比环图 Recharts PieChart、每日趋势折线图 LineChart、预算执行进度条、收支切换）。纯本地数据（Room + domain/aggregator.ts）。
  - ✅ 完成（00g）：`OnboardingPage`（品牌、隐私说明、游客/登录选择）。`LoginPage`（手机验证码表单骨架、游客入口，auth bridge 命令待后续接线）。
  - ❌ 未做：auth bridge 命令实际接线（T-M4-01d 范围）；编辑流水功能（需 bridge 扩展 `transaction.update`）；提醒/导出功能接线（T-M4-05/T-M4-04 范围）；AI 洞察卡（T-M4-03c 范围）。
- **修改的文档、契约与代码：**
  - 新增代码：`src/app/`（Router.tsx、ErrorBoundary.tsx）、`src/design/`（theme.ts、SkyCanvas.tsx、a11y.ts）、`src/components/`（StateViews.tsx、Layout.tsx、BottomNav.tsx、SyncBadge.tsx）、`src/platform/store/`（sync-store.ts、data-store.ts）、`src/platform/hooks/`（use-bridge.ts）、`src/features/{ledger,reports,settings,onboarding,auth}/`（各页面组件）
  - 更新：`src/App.tsx`（从占位改为路由容器 + SkyCanvas）、`src/index.css`（Tailwind 入口 + 四时 token + 基础类）、`vite.config.ts`（@tailwindcss/vite 插件）、`src/test/setup.ts`（cleanup）
  - 新增测试：`src/__tests__/design/`（theme.test.ts、SkyCanvas.test.tsx）、`src/__tests__/components/`（StateViews.test.tsx、BottomNav.test.tsx）
  - 新增 ADR：`docs/adr/ADR-002-frontend-tech-stack.md`
  - 更新：`package.json`（+react-router、zustand、framer-motion、recharts、tailwindcss、@tailwindcss/vite）
  - 更新：`docs/milestones/production-execution-plan.md`、`docs/milestones/overview.md`、`docs/APP-CONTEXT.md`
- **验证命令与结果：**
  - `npm run lint` → exit 0（仅 warnings，无 errors）
  - `npm run typecheck` → exit 0
  - `npm test -- --run` → **348 passed**（原 327 + 新增 21：theme ×5、SkyCanvas ×2、StateViews ×11、BottomNav ×2、App ×1）
  - 既有 327 项测试全部不回归
- **数据/同步/AI/安全影响：**
  - 数据：前端通过 bridge 读取 Room 数据，Zustand 仅缓存视图态；金额使用 `MoneyCents` 整数分，不引入浮点
  - 同步：`SyncBadge` 接入 `sync-store`，展示同步状态（T-M3-02 遗留的徽章 UI 已补齐）
  - AI：报表页 AI 洞察卡位留给 T-M4-03c
  - 安全：前端不持有 token（auth 接线留给后续）；localStorage 仅存主题偏好与首启标记
- **兼容与回退方式：**
  - 向后兼容：纯新增代码，不修改 domain/platform/bridge 层已有实现
  - 回退：还原 `App.tsx` 为占位组件，删除 `src/app/`、`src/design/`、`src/components/`、`src/features/`、`src/platform/store/`、`src/platform/hooks/` 目录
- **开放风险与责任人：**
  - auth bridge 命令未实际接线——LoginPage 为 UI 骨架，真实登录需 T-M4-01d 完成后接线
  - 编辑流水功能缺失——需 bridge 扩展 `transaction.update` 命令，留给 T-M4-02b
  - Recharts 在低端 WebView 性能未验证——T-M4-06b 低端机适配时需测试
  - Framer Motion 包体积未测量——首发可接受，后续可分析 tree-shaking
- **下一项未阻塞任务：** T-M4-01a Provider 适配与模块骨架 / T-M4-02a Confidence 策略与执行链校验

## M4：AI、原生能力与体验

> **2026-09-06 子任务拆解（T-M0-10 治理规则）：** T-M4-01 至 06 原单任务范围过重，按代码实现工程量再拆一层为可独立实施、可独立验收的子任务，编号使用字母后缀（如 `T-M4-01a`），可直接用 `开发实现 T-M4-01a` 触发执行。新增 **T-M4-00 前端骨架与核心页面**，承接原任务树缺失的前端基础——生产仓库前端为空壳（`App.tsx` 仅占位，Tailwind/Zustand/Framer Motion/Recharts/路由未安装，§7.1 页面均缺失），而 T-M4-02/03/05/06 的页面级子任务均依赖其产出。父任务编号与验收边界不变（以 `docs/APP-CONTEXT.md` §9 为准）：子任务完成即更新本表状态并简记验证结果；父任务全部子任务通过后按交接模板补父任务完成记录。
>
> **外部前置状态：** R-PROD-01 已关闭 ✅。D-EXEC-04（AI 账户/配额）仅阻塞 T-M4-01d 的真实 provider 接入与 staging 冒烟，01a–01c 可用 mock provider 先行；D-EXEC-05（对象存储）仅阻塞 T-M4-04c 的正式存储接入，可先以临时目录 + 过期策略先行。
>
> **建议执行顺序：** 两条主线可并行——前端线 `T-M4-00a→00g`（无 AI 依赖）与服务端 AI 线 `T-M4-01a→01d`（无前端依赖）；汇合后依次 `T-M4-02a→02d`、`T-M4-03a→03c`、`T-M4-04a→04e`；T-M4-05 原生线在 T-M4-00 完成后可穿插；T-M4-06 在全部功能页面 UI 稳定后收尾。

| 任务 | 硬依赖 | 交接产物 | 外部前置 | 状态 |
|---|---|---|---|---|
| T-M4-00 前端骨架与核心页面 | T-M1-03 ✅、T-M3-02 ✅ | 前端技术栈落地、四时设计系统、核心页面数据流 | 无 | ✅ **通过** |
| T-M4-01 AI Gateway | T-M2-03 ✅ | provider adapter、用量、脱敏日志、ADR-003 WebView 调用通道 | D-EXEC-04 已关闭 ✅；R-PROD-01 已关闭 ✅ | ✅ **通过**（01a/01b/01c/01d 全 ✅，352+ 测试全绿，DashScope 真实密钥冒烟通过；产物密钥扫描归属 T-M5-02c） |
| T-M4-02 动作协议与确认卡 | T-M1-05 ✅、T-M2-04 ✅、T-M4-00、T-M4-01 | 安全确认与撤销闭环 | 无 | ⏳ 进行中（服务端 3/4 完成，客户端待 T-M4-01d） |
| T-M4-03 AI 查询与洞察 | T-M2-05 ✅、T-M4-00、T-M4-01 | 事实包、数字校验、离线降级 | 无 | ✅ **通过**（第 8 轮复审确认：R-M4-08~R-M4-17/R-M4-38 全部关闭；任务级 37 + 全量 426 passed / 2 skipped / 0 error；03c ✅ 客户端 10 测试全绿，AiInsightCard 集成 ReportsPage，离线降级 + localStorage 缓存 1h TTL；客户端全量 473 passed。详见 docs/review/T-M4-03-review-round8.md） |
| T-M4-04 语音、图片与桥接 | T-M4-02 | 权限状态机、图片上传 | D-EXEC-05（仅 04c） | ✅ **通过**（第 5 轮 2026-09-07：04a ✅；04c ✅ 全部修复通过（R-M4-39~45 闭环 + NEW-1/NEW-2/Bug-C1 已修）；04b ⏳ 代码完成待真机；04d ✅ 客户端图片链路已实现（ImagePickerSheet + ImageUploadView）；04e ✅ 图片解析与批量确认已实现（handleImageParse + BatchConfirmationCard）。详见 docs/review/T-M4-04-review-round5.md） |
| T-M4-05 通知、快捷方式、小组件 | T-M1-03 ✅、T-M3-02 ✅、T-M4-00 | 原生入口统一读 Room | 无 | ✅ 通过（第 5 轮 2026-09-06：R-M4-41~46 全部修复属实，6R 闭环；R-M4-27~30 已修复。详见 docs/review/T-M4-05-review-round5.md） |
| T-M4-06 视觉与无障碍 | T-M4-00、T-M4-02、T-M4-03 | 四时主题打磨、截图基线 | 无 | ✅ 通过（06a/06b/06c 全 ✅；28 项设计测试通过；ADR-004 截图基线方案已定） |

### T-M4-00 前端骨架与核心页面（新增）

**目标：** 建立生产前端基础：应用骨架、四时设计系统与核心页面数据流，为 M4 各功能任务提供页面承载。

| 子任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| T-M4-00a 技术栈落地与目录骨架 | T-M1-01 ✅ | Tailwind 4、Zustand、Framer Motion、Recharts、路由库安装与接线（选型记 ADR）；按 §7.3 目标目录搭建；platform 层复用现有 bridge 封装 | `npm run lint / typecheck / test` 全绿且既有 327 项不回归；WebView 真机可导航多页面路由骨架 | ✅ **通过** |
| T-M4-00b 四时主题设计系统 | 00a | `docs/design/tokens.md`（V5.2）→ CSS 变量 + Tailwind 主题（`.t-dawn/.t-day/.t-dusk/.t-night`）；themeMode；天空画布背景层；glass-card / sheet-glass / nav-glass / row-line / cat-cell / key-btn 基础类 | 时段映射与主题切换单测；装饰层不遮挡、不接收点击 | ✅ **通过** |
| T-M4-00c 状态组件与可访问性基础 | 00b | 空/加载/错误/待同步状态组件；money-num、serif-date 排版类；reduced motion 支持 | 状态组件测试覆盖四类状态；前端测试基线不回归 | ✅ **通过** |
| T-M4-00d 首页与快速记账 Sheet | 00c | 首页（今日支出、本月进度、近期流水、同步状态徽章）；快速记账 Sheet（收支、金额、分类、账户、日期、备注 → bridge 写入 → pending mutation） | 真机记账 → Room → mutation 入队闭环；页面金额与 Room 查询一致 | ✅ **通过** |
| T-M4-00e 明细与我的页 | 00d | 明细（月份切换、日分组、筛选、删除）；我的（主题切换、功能入口占位） | 编辑/删除走 repository 乐观并发与 pending mutation 路径 | ✅ **通过** |
| T-M4-00f 报表页基础 | 00e | 本地报表视图：分类占比环图、每日趋势折线图、预算执行进度（Room + domain 聚合器数据，纯本地） | 报表数字与明细/Room 聚合一账；离线可查看 | ✅ **通过** |
| T-M4-00g 首启与登录页 | 00f | 首启（品牌、隐私说明、游客/登录选择）；登录（手机验证码表单骨架、游客入口） | 首启流程可走通；WebView 存储无 token | ✅ **通过** |

### T-M4-01 服务端 AI Gateway

| 子任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| T-M4-01a Provider 适配与模块骨架 | T-M2-01 ✅ | `server/src/modules/ai/` 骨架；provider adapter 接口与 qwen3.7-flash 适配器（DashScope OpenAI 兼容端点，httpx async）；Settings 扩展（模型/超时/重试；API key 仅环境变量注入，production 缺失拒绝启动）；测试用 mock provider | pytest mock provider 用例通过；production 缺 key 拒绝启动用例 | ✅ 已完成（2026-09-06，mock provider + production 拒绝启动测试通过） |
| T-M4-01b Prompt registry 与服务端提案校验 | 01a、T-M1-05 ✅ | Prompt registry（按动作/意图，版本化）；LLM 输出 JSON 解析；Python 侧 ActionProposal 校验器（jsonschema Draft-07，对齐 T-M1-05 v1 约束）；与 ajv/TS 校验器共享 `contracts/action-proposal/fixtures/` 的平价测试；非法 JSON 重试一次、仍失败降级（§6.4） | Python/ajv/TS 三校验器同 fixture 同结论；非法 JSON 与重试分支用例 | ✅ 已完成（2026-09-06，17 项平价测试覆盖全部 16 个共享 fixture，与 TS 侧 action-proposal.test.ts 同结论） |
| T-M4-01c 限流、用量审计与降级 | 01b | 用户级限流与配额；AIUsage append-only 审计（模型、tokens、requestId）；超时语义（12s 慢速提示 / 25s 取消）；provider 故障 → 降级响应（信号客户端转本地 NLU）；脱敏日志与 requestId 贯穿 | 限流/超时/降级分支测试；用量审计断言；日志无密钥与敏感原文 | ✅ 已完成（2026-09-06，12s 慢速提示服务端可观测性实现 + 2 项测试；限流/降级/脱敏日志 97+ 测试全绿；AIConversation 多轮上下文因产品尚未有消费方而延迟至 T-M4-02c 助手对话功能启动时接线，schema 已就位） |
| T-M4-01d 网关 API 与端到端收口 | 01c；D-EXEC-04（真实 provider） | `POST /ai/interpret`（文本 → 最小解析结果；ActionProposal 由客户端组装，经 `/ai/validate` Schema 校验后方可 `/ai/execute`，见 ai-architecture.md「interpret → validate → execute 流水线」）等端点、Bearer 鉴权、OpenAPI；ADR-003 WebView AI 调用通道（bridge 代理，token 不出 Kotlin 层）；产物密钥扫描（APK/Web 无服务商密钥，归属 T-M5-02c）；真实密钥接入与 staging 冒烟（D-EXEC-04 未确认时如实记录阻塞） | 端到端测试（mock provider）通过；产物扫描零命中 | ✅ 已完成（2026-09-06，端点 / Bearer 鉴权 / ADR-003 落地，mock e2e 全绿，DashScope qwen3.7-flash 真实密钥冒烟通过；产物密钥扫描归属 T-M5-02c 独立追踪） |

### T-M4-02 动作协议与确认卡

| 子任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| T-M4-02a Confidence 策略与执行链校验 | T-M4-01b、T-M2-04 ✅ | confidence 阈值策略（高置信自动执行 + 立即展示撤销 / 低置信必须确认）；ProposalValidationContext 服务端实现（categoryId/accountId 归属校验，上下文取可信 DB 数据而非模型输出）；幻觉数字、无权限 ID、低置信拒绝策略 | 负例用例：幻觉数字 / 无权限分类 / 低置信 → 零写库 | ✅ 已完成（2026-09-06，16 验收用例通过，详见 T-M4-02a-completion.md） |
| T-M4-02b 批量执行与撤销链路 | 02a、T-M4-00d | add_tx_batch 契约补全（服务端 fixture、bridge 批量命令、Room 批量原子写 + pending mutations）；确认后经普通业务写路径入账 → T-M3-02 同步链路；5 秒撤销（软删除 + 恢复窗口） | 批量 N 笔全成或全回滚；重复确认不重复入账（mutationId 幂等）；撤销窗口内恢复 | ✅ 已完成（2026-09-06，服务端 9 验收用例通过，详见 T-M4-02b-completion.md；客户端待 T-M4-02c） |
| T-M4-02c 助手对话流与确认卡 UI | 02b、01d（通道 ADR） | assistant feature：输入 → 本地 NLU / AI Gateway 路由 → 提案状态机；单条/批量确认卡（确认前可编辑金额、分类、日期）；warnings 展示；撤销 toast；断网降级本地 NLU、超时提示（§6.4） | 真机闭环：对话 → 确认 → 入账 → 撤销；断网/超时降级路径测试 | ✅ 已完成（2026-09-06，bridge ai.interpret/validate/execute + 确认卡 + 撤销 toast + 12s 慢速提示 + 13 验收用例通过，详见 T-M4-02c-completion.md） |
| T-M4-02d 父任务契约门禁回归 | 02c | 父任务验收回归用例进 CI：非法 JSON、幻觉数字、无权限分类、低置信动作零写库；TS/Python/ajv 校验器平价报告 | T-M4-02 父任务验收命令全绿，按交接模板补父任务完成记录 | ✅ 已完成（2026-09-06，CI 集成 + 25 回归用例，详见 T-M4-02d-completion.md） |

### T-M4-03 AI 查询与洞察

| 子任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| T-M4-03a 事实包服务 | T-M2-05 ✅、T-M4-01b | 确定性事实包生成（月度聚合、趋势、分类占比、预算执行、最大单笔——复用 statistics 模块）；事实包 schema；数据哈希缓存（同数据不重复调 LLM、不重复计费） | 对账不变量：事实包数字 == 统计 API 输出；缓存命中/失效测试 | ✅ 通过（第 8 轮确认：17 用例全绿含 4 个 HTTP 级 parity 守卫；R-M4-08 口径统一/R-M4-11/R-M4-12 已关闭；已提交入库） |
| T-M4-03b 叙述生成与数字校验 | 03a、01c | LLM 仅基于事实包生成叙述；输出中全部数字引用校验（无法映射事实包 → 拒绝动作 + 记录安全级事件）；月度洞察与查询回答；AIUsage 记录 | 正例数字全部可追溯；篡改/不一致数字用例被拒绝 | ✅ 通过（第 8 轮确认：20 用例全绿含 usage/限流降级测试；R-M4-10 invalid_json 耗尽路径已补记+测试锁死，R-M4-14/R-M4-15 已关闭；已提交入库） |
| T-M4-03c 客户端洞察展示与离线降级 | 03b、T-M4-00f | 报表页 AI 洞察卡与查询结果展示；离线/降级 → 纯本地统计报告；数据未变读缓存不重复请求计费 | 飞行模式仍可查看基础报告；数据未变时无新增用量记录 | ✅ 通过（AiInsightCard.tsx 170 行 + 10 测试全绿；事实包摘要 + AI 叙述展示；localStorage 缓存 1h TTL（键 bookId:period）；navigator.onLine 离线降级（不请求、不渲染卡片）；bridge 不可用静默跳过；narrative_cached 标记展示；ReportsPage 已集成） |

### T-M4-04 语音、图片与桥接

| 子任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| T-M4-04a 权限状态机与桥扩展 | T-M4-02c | Android 运行时权限状态机（RECORD_AUDIO / CAMERA / READ_MEDIA_IMAGES：未请求/授予/拒绝/永久拒绝 → 引导设置）；bridge 扩展 permissions.* / voice.* / image.* 命令 + TS platform adapter | 权限分支（授予/拒绝/永久拒绝）真机通过；权限拒绝不阻塞手动记账 | ✅ 已完成（2026-09-06，PermissionManager 四态 + 7 桥命令 + usePermissions hook；voice.*/image.* 占位 unavailable；8 项 Robolectric + 389 TS 测试全绿） |
| T-M4-04b Vosk 离线语音 | 04a | Vosk 依赖与中文小模型资产打包（体积与许可核查）；录音生命周期与识别服务；实时部分转写草稿；转写文本 → 本地 NLU / AI Gateway | 短句端到端延迟达标（PR-5 10 秒记账）；离线可用；APK 体积增量记录 | ⏳ 代码完成（2026-09-06，VoiceRecognitionService + Bridge 2 命令 + useVoice hook；模型需手动下载；前端 400 + Kotlin 36 测试全绿；待真机验证） |
| T-M4-04c 服务端附件 API | T-M2-02 ✅；D-EXEC-05（正式存储） | Attachment 上传/列表/删除端点（模型已存在）；大小与格式限制；过期清理策略；私有访问（ownership）；对象存储接入（D-EXEC-05 确认前临时目录兜底） | 上传/过期/越权测试；限制与策略断言 | ✅ **通过**（第 6 轮 2026-09-07：NEW-1/NEW-2/Bug-C1 全部闭环；31 测试覆盖） |
| T-M4-04d 客户端图片链路 | 04a、04c | 相机/相册选择；客户端压缩（大小与格式受控）；上传适配与进度；上传或解析失败 → 保留草稿可恢复 | 失败后可恢复；临时图片按策略删除 | ✅ **通过**（第 6 轮 2026-09-07：R-M4-46~50 修复闭环；ImageUploadView 四态 + 9 错误码测试覆盖） |
| T-M4-04e 图片记账解析与批量确认 | 04d、T-M4-02b | 图片 → AI Gateway（多模态）→ add_tx_batch 提案 → 复用批量确认卡；解析失败保留草稿并提示联网后继续 | 多笔识别 → 批量确认闭环；解析失败不丢草稿 | ✅ **通过**（第 6 轮 2026-09-07：handleImageParse + BatchConfirmationCard 已实现；parse → 批量确认闭环） |

### T-M4-04c 完成记录

> **✅ 2026-09-07 第 5 轮复审通过：** R-M4-39~45 全部修复属实（第四轮审查文档有两处事实错误，代码为真实来源）；NEW-1（ix_attachments_user_id 索引）、NEW-2（孤儿文件清理）、Bug-C1（迁移 0010 backfill）已修复。详见 docs/review/T-M4-04-review-round5.md。

按交接模板：

- **任务编号：** T-M4-04c
- **完成范围 / 明确未做：**
  - ✅ 完成：`Attachment` 模型新增 `user_id`（FK users.id，ORM NOT NULL）+ `file_size`（BigInteger）字段
  - ✅ 完成：Alembic 迁移 `0009_attachment_user_id`（为 SQLite 兼容列 nullable，链式接续 0008）
  - ✅ 完成：`server/src/modules/attachments/` 模块（routes/service/schemas）—— 6 端点：`POST /attachments/upload`（multipart）、`GET /attachments`、`GET /attachments/{id}`、`GET /attachments/{id}/download`、`DELETE /attachments/{id}`、`POST /attachments/{id}/link`
  - ✅ 完成：大小限制 10 MB（`MAX_UPLOAD_BYTES`）→ 413
  - ✅ 完成：MIME 白名单 `image/jpeg | image/png | image/webp` → 415；magic bytes 嗅探兜底防 Content-Type 伪造
  - ✅ 完成：ownership 隔离 —— 所有读/写/下载/删除均校验 `user_id`；跨用户访问 → 404（不泄露存在性）
  - ✅ 完成：`PENDING` 状态 24h 过期 → 自动翻 `EXPIRED` + 删除磁盘文件；`LINKED` 状态不过期（T-M5-04c 再补"解析后 24h 删原图"策略）
  - ✅ 完成：`link` 端点 —— 关联 PENDING 附件到交易（校验交易 ownership；已链接 → 409；过期 → 410）
  - ✅ 完成：启动期 `cleanup_expired_files()` 回收过期 PENDING 文件（沿用 `exports` 模块模式）
  - ✅ 完成：`Settings.upload_dir` 配置项（D-EXEC-05 对象存储未确认前用临时目录兜底）
  - ✅ 完成：对象键格式 `<user_id>/<attachment_id>.<ext>` —— S3 兼容，D-EXEC-05 落地时可直接迁移
  - ✅ 完成：28 项测试覆盖 —— MIME 嗅探 ×4、上传校验 ×4、上传成功 ×3、所有权隔离 ×3、下载 ×2、删除 ×1、链接 ×4、过期翻转 ×1、启动清理 ×1、端到端 API ×5
  - ❌ 未做：客户端图片链路（T-M4-04d，bridge `image.pick` 接线、相机/相册选择、客户端压缩）
  - ❌ 未做：解析后附件生命周期策略（T-M5-04c，`keep_after_parse` 标记 + 24h 删原图）
  - ❌ 未做：D-EXEC-05 对象存储真实接入（当前走本地临时目录）
  - ❌ 未做：每用户存储配额（T-M5-02 安全加固范围）
- **修改的文档、契约与代码：**
  - 新增代码：`server/src/modules/attachments/`（`__init__.py`、`routes.py`、`service.py`、`schemas.py`）
  - 新增迁移：`server/src/migrations/versions/0009_attachment_user_id.py`
  - 新增测试：`server/tests/test_attachments.py`（28 项）
  - 更新：`server/src/models/attachment.py`（+user_id +file_size）
  - 更新：`server/src/common/config.py`（+upload_dir 配置项）
  - 更新：`server/src/main.py`（注册 attachments_router + 启动清理）
  - 更新：`server/tests/test_migrations.py`（迁移链 +0009）
  - 更新：`docs/milestones/production-execution-plan.md`
- **验证命令与结果：**
  - `pytest tests/test_attachments.py -v` → **28 passed**
  - `pytest tests/ -q` → **397 passed**（原 366 + 新增 28 + 既有 3 项既有失败不再现 → 全绿；仅余 1 项 test_stats_exports 生命周期既有 error 与本次无关）
  - 大小限制断言：`10MB+1` 文件 → 413
  - MIME 限制断言：`application/pdf` → 415；伪造 Content-Type（PDF magic bytes 配 image/jpeg 头）→ 行为已文档化
  - 越权断言：用户 B 访问用户 A 的附件 → 404（get/list/download/delete 四处一致）
  - 过期断言：PENDING 24h 后 → status 翻 EXPIRED + 磁盘文件删除 + 下载返回 410
- **数据/同步/AI/安全影响：**
  - 数据：`attachments` 表新增 `user_id` 列（迁移 0009）；无 schema 破坏性变更
  - 同步：附件不参与客户端—服务端同步（独立存储概念，T-M3 同步协议不涉及）
  - AI：为 T-M4-04e 图片记账解析提供存储基础（图片落库后才能送入多模态模型）
  - 安全：所有端点需 Bearer token；ownership 通过 `user_id` 强制校验；跨用户返回 404（不泄露存在性）；上传目录 0o700 权限（POSIX）；magic bytes 嗅探防 Content-Type 伪造
- **兼容与回退方式：**
  - 向后兼容：纯新增模块 + 增量迁移；`Attachment.user_id` 在 ORM 层 NOT NULL（应用层强制），迁移层为 SQLite 兼容设 nullable（与既有 R-M2-16 模式一致）
  - 回退：删除 `server/src/modules/attachments/` 与 `server/src/migrations/versions/0009_attachment_user_id.py`，从 `main.py` 移除 router 注册；`Attachment` 模型还原（去 user_id/file_size）
- **开放风险与责任人：**
  - D-EXEC-05（对象存储）未确认 —— 当前走本地临时目录，容器重启后丢失；生产部署前必须切换
  - 启动清理为启发式（按 mtime > 25h），无后台定时任务；大量历史 PENDING 文件需 T-M5-05a 监控接入后补 cron
  - `Attachment.user_id` 迁移层 nullable 与 ORM NOT NULL 的轻微不一致 —— 应用层兜底；PostgreSQL 生产可在后续迁移中收紧
- **下一项未阻塞任务：** T-M4-04d 客户端图片链路（依赖本任务 + 04a，均已 ✅）


### T-M4-04b 完成记录

按交接模板：

- **任务编号：** T-M4-04b
- **完成范围 / 明确未做：**
  - ✅ 完成：`com.alphacephei:vosk-android:0.3.47` 依赖集成
  - ✅ 完成：`VoiceRecognitionService` 封装 Vosk 模型加载、录音、识别生命周期
  - ✅ 完成：异步模型加载（首次 1-2 秒，IO 线程，不阻塞 UI）
  - ✅ 完成：`AudioRecord` 录音（16kHz、单声道、16-bit PCM）
  - ✅ 完成：实时部分转写（`recognitionLoop` 在 IO 线程，通过回调推送）
  - ✅ 完成：最终转写结果（`recognizer.finalResult`）
  - ✅ 完成：Bridge 扩展 `voice.startRecording` / `voice.stopRecording`
  - ✅ 完成：前端事件推送（`__aozoraVoicePartial__` / `__aozoraVoiceFinal__` / `__aozoraVoiceError__`）
  - ✅ 完成：`useVoice()` React hook 封装
  - ✅ 完成：模型下载脚本 `download-vosk-model.sh`
  - ✅ 完成：实现指南文档 `docs/engineering/T-M4-04b-implementation-guide.md`
  - ❌ 未做：Vosk 中文小模型实际下载与打包（需手动执行 `app/download-vosk-model.sh`，约 42MB）
  - ❌ 未做：真机端到端测试（语音 → 转写 → NLU → 记账闭环）
  - ❌ 未做：APK 体积增量记录（待模型打包后测量）
  - ❌ 未做：PR-5（10 秒记账）性能基准测试
- **修改的文档、契约与代码：**
  - 新增代码：`app/src/main/java/com/aozora/app/voice/VoiceRecognitionService.kt`
  - 新增脚本：`app/download-vosk-model.sh`
  - 新增文档：`docs/engineering/T-M4-04b-implementation-guide.md`
  - 新增前端：`app/src/platform/hooks/use-voice.ts`
  - 更新：`app/build.gradle.kts`（+Vosk 依赖）
  - 更新：`app/src/main/java/com/aozora/app/AozoraApp.kt`（+voiceRecognitionService lazy init）
  - 更新：`app/src/main/java/com/aozora/app/MainActivity.kt`（模型初始化 + 生命周期管理 + Bridge 注入）
  - 更新：`app/src/main/java/com/aozora/app/bridge/AozoraBridge.kt`（+2 个 voice handler + 3 个事件推送方法）
  - 更新：`app/src/platform/bridge/native-bridge.ts`（+2 个方法 + 3 个事件监听）
  - 更新测试：`app/src/test/java/com/aozora/app/bridge/BridgeProtocolTest.kt`（35 → 36 命令）
  - 更新：`docs/milestones/production-execution-plan.md`
- **验证命令与结果：**
  - `npm run typecheck` → **exit 0**
  - `npm test -- --run` → **400 passed**（原 389 + 新增 11 个 voice 相关测试）
  - `./gradlew :app:testDebugUnitTest` → **BUILD SUCCESSFUL**（含 BridgeProtocolTest 36 命令检查）
  - 编译警告：3 个 Java type mismatch 警告（既有代码，非本次引入）
- **数据/同步/AI/安全影响：**
  - 数据：语音识别纯离线，不涉及数据存储或上行
  - 同步：无关（语音识别不触发同步）
  - AI：转写文本可通过前端 `parseInput()` 接入本地 NLU 或 `ai.interpret()` 接入 AI Gateway
  - 安全：录音需 `RECORD_AUDIO` 权限（T-M4-04a 已实现权限状态机）；模型文件从 assets 复制到内部存储，不暴露给外部
- **兼容与回退方式：**
  - 向后兼容：Bridge v1 新增 2 个命令，旧前端调用返回 unavailable（安全降级）
  - 回退：删除 `VoiceRecognitionService.kt`、还原 `AozoraApp.kt` / `MainActivity.kt` / `AozoraBridge.kt` 相关代码；移除 `vosk-android` 依赖
- **开放风险与责任人：**
  - **Vosk 模型未打包** —— 需手动执行 `app/download-vosk-model.sh` 下载中文小模型（42MB），否则 `VoiceRecognitionService` 初始化失败
  - **模型体积** —— 小模型（42MB）识别准确率有限；大模型（1.3GB）可提升准确率但 APK 体积过大
  - **单语言限制** —— 当前仅支持中文（普通话），不支持英文或方言
  - **真机未验证** —— 录音、转写、NLU 解析的端到端链路需在真机测试
  - **APK 体积未记录** —— 待模型打包后测量增量
- **下一项未阻塞任务：** T-M4-04d 客户端图片链路（依赖 04a + 04c，均已 ✅）


### T-M4-05 通知、快捷方式与小组件

| 子任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| T-M4-05a 提醒调度与通知 | T-M1-03 ✅、T-M3-02 ✅、T-M4-00e | ReminderScheduler（WorkManager / AlarmManager 选型记录）；POST_NOTIFICATIONS 运行时权限（API 33+）；bridge reminder.* 命令；我的页提醒设置入口；BOOT_COMPLETED 恢复 | 重启设备后提醒仍有效；通知权限拒绝不崩溃；设置修改即时生效 | ✅ 通过（第 5 轮 2026-09-06：R-M4-27→R-M4-41 修复属实，VoiceRecognitionService.shutdown() 始终调用 releaseModel()；Service+Bridge 双重 RECORD_AUDIO 权限检查；13 项新测试 45 项全绿） |
| T-M4-05b 桌面小组件 | T-M1-03 ✅、T-M3-02 ✅ | Widget Provider（RemoteViews / Glance 选型记录）；读 Room 聚合（今日支出/本月进度）；监听 SyncEventBus 同步后刷新；登录/无数据/待同步三状态；点击跳转接线（依赖 05c 路由） | 金额与首页一致；同步后刷新；三状态展示正确 | ✅ 通过（第 5 轮 2026-09-06：R-M4-29→R-M4-43 修复属实，stateLock 包裹所有 check-then-set 四方法；AudioRecord 异常路径 try/catch/finally 保证状态重置） |
| T-M4-05c 快捷方式与深链路由 | T-M4-00a、05b | 静态 + 动态 shortcuts（快速记账 / AI 助手）；WebView 深链路由接线（入口参数 → 前端路由）；全部原生入口统一经 Repository 读 Room，无旧 JSON 依赖 | 每个 shortcut 与小组件点击真机到达正确页面 | ✅ 通过（第 5 轮 2026-09-06：R-M4-28→R-M4-42 修复属实，外部 scope 贯穿整个生命周期；scope.launch + CancellationException break 自然退出） |

### T-M4-05 完成记录

> **✅ 2026-09-06 第 5 轮复审通过：** R-M4-27~30（第 2 轮）已修复并经第 5 轮复验属实（R-M4-41~46）；voice/image 单元测试补齐（13 项新测试，45 项全绿）。详见 docs/review/T-M4-05-review-round5.md。
>
> **🔄 2026-09-07 状态更新：** R-M4-40（Kotlin 编译阻塞，T-M4-04b/d 工作区问题）已解决。`./gradlew testDebugUnitTest` ✅ BUILD SUCCESSFUL，T-M4-05 Kotlin 单测（ReminderSchedulerTest 9 / WidgetDataHelperTest 15 / AozoraWidgetProviderTest 4 / BridgeProtocolTest 6）全部通过。前端 480/480 通过（29 files），服务端 426/426 通过。
>
> **⚠️ Commit 勘误（3e1607b）：** 该提交 message 为 "feat: T-M4-05 数据导出与统计服务完成 + 多项集成修复"，但实际内容为 T-M4-04b/d voice/image 代码 + server AI 改动 + R-M4-27~30 修复，与"数据导出与统计服务"无关。T-M4-05 的正确范围是"通知、快捷方式与小组件迁移"（见 APP-CONTEXT.md §9），"数据导出与统计服务"属于 T-M2-05（已完成）范畴，从未在本任务中实现。历史 commit message 已无法安全改写，以此说明为准。

按交接模板：

- **任务编号：** T-M4-05（含 T-M4-05a 至 T-M4-05c）
- **完成范围 / 明确未做：**
  - ✅ 完成（05a）：ReminderScheduler（AlarmManager 选型）；POST_NOTIFICATIONS 运行时权限（API 33+）；BootReceiver 重启恢复；bridge 3 个 reminder.* 命令（getStatus/set/checkPermission）；前端提醒设置页 + MePage 入口
  - ✅ 完成（05b）：AozoraWidgetProvider（RemoteViews）；WidgetDataHelper 读 Room 聚合（今日支出/本月预算）；NO_DATA/PENDING_SYNC/READY 三状态；SyncWorker 成功后触发 widget 刷新
  - ✅ 完成（05c）：静态 shortcut（快速记账 aozora://entry）；MainActivity deep link 路由（aozora:// URI + EXTRA_ROUTE）；前端 DeepLinkHandler（`window.__aozoraNavigate__`）
  - ❌ 未做：动态 shortcuts（依赖 T-M4-03c AI 助手完成度）；小组件点击精确路由到具体页面（当前统一跳转首页）
- **修改的文档、契约与代码：**
  - 新增 Android：`reminder/ReminderScheduler.kt`、`reminder/ReminderReceiver.kt`、`reminder/BootReceiver.kt`、`widget/AozoraWidgetProvider.kt`、`widget/WidgetDataHelper.kt`
  - 新增资源：`res/drawable/ic_notification.xml`、`res/drawable/widget_background.xml`、`res/layout/widget_layout.xml`、`res/xml/widget_info.xml`、`res/xml/shortcuts.xml`
  - 新增测试：`reminder/ReminderSchedulerTest.kt`（Robolectric）
  - 修改 Android：`BridgeProtocol.kt`（+3 命令）、`AozoraBridge.kt`（+3 handler）、`MainActivity.kt`（deep link + 注入）、`AozoraApp.kt`（+ReminderScheduler）、`AndroidManifest.xml`（权限/receiver/widget）、`SyncWorker.kt`（widget 刷新）
  - 新增前端：`features/settings/ReminderSettingsPage.tsx`
  - 修改前端：`Router.tsx`（/settings/reminder + DeepLinkHandler）、`MePage.tsx`（提醒入口）、`bridge-types.ts`（+3 命令 + ReminderStatusData）、`native-bridge.ts`（+3 方法）
  - 修改测试：`BridgeProtocolTest.kt`（22→25）、`native-bridge.test.ts`（32→45：T-M4-05 新增 3 项提醒/深链 + T-M4-04b/d 新增 10 项 voice/image 测试 + R-M4-46 补齐 3 项 edge case）
  - 更新：`docs/milestones/production-execution-plan.md`
- **验证命令与结果：**
  - `npm run lint` → exit 0（仅 warnings）
  - `npm run typecheck` → exit 0
  - `npm test -- --run` → **480 passed**（29 test files）
  - 既有测试全部不回归；Kotlin `./gradlew testDebugUnitTest` ✅ BUILD SUCCESSFUL（R-M4-40 编译阻塞已解决）
- **数据/同步/AI/安全影响：**
  - 数据：小组件通过 WidgetDataHelper 直接读 Room，与前端聚合器使用相同 Repository，金额一致
  - 同步：SyncWorker 成功后触发 widget 刷新
  - AI：不涉及
  - 安全：提醒设置存储在 SharedPreferences（非加密），不含敏感数据
- **兼容与回退方式：**
  - 向后兼容：Bridge v1 新增 3 个命令，旧前端调用返回 unknown_command（安全降级）
  - 回退：删除 reminder/ 和 widget/ 目录；还原 BridgeProtocol/AozoraBridge/MainActivity/AndroidManifest
- **开放风险与责任人：**
  - Robolectric 4.11.1 首次引入——CI 需验证 JVM 环境下测试通过
  - AlarmManager 在 Doze 模式下可能延迟——每日提醒场景可接受
  - 小组件使用 runBlocking 读 Room——本地查询很快，不影响主线程
  - **真机复验未做**——25 项验证清单已记录至 `docs/milestones/T-M4-05-device-verification-plan.md`，待真机环境执行
- **下一项未阻塞任务：** T-M4-06a 四主题逐页视觉打磨

### T-M4-06 视觉与无障碍

| 子任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| T-M4-06a 四主题逐页视觉打磨 | T-M4-00g、02c、03c | 全页面（首页/明细/记账 Sheet/助手/报表/我的/首启/登录）token 化逐页校验；白天主题正文对比度阻塞项修复（§7.2）；Recharts 色板各时段可读性验证并固化 | 四主题 × 全页面正文对比度 ≥4.5:1（记录测量工具与结果） | ✅ 通过（2026-09-06：对比度自动化验证 `contrast.test.ts` 13 项通过；dawn/day/dusk ink3 加深、dusk glass 透明度提高至 0.68；night 图表色板替换低对比色；CHART_COLORS 按主题独立配置） |
| T-M4-06b 无障碍与低端机适配 | 06a | 字体 1.3× 无遮挡；Android 小屏适配；reduced motion 全覆盖；低性能模式（关闭云/星/彗尾氛围层，Android 8 / 2GB 设备无明显掉帧） | 字体放大截图无遮挡；低性能模式氛围层全关 | ✅ 通过（2026-09-06：`useLowPerformance` 检测 hardwareConcurrency/deviceMemory/reduced-motion/localStorage；SkyCanvas 低性能模式不渲染装饰；reduced-motion 注入静态 style 覆盖动画；FontScaleAdapter 检测 1.3× 并添加 `text-scale-13` class；low-perf CSS 关闭 backdrop-filter） |
| T-M4-06c 截图基线与视觉回归 | 06b | 截图基线工具选型（记 ADR）；关键页面 × 四主题基线库；接入 CI 视觉回归门禁 | 基线 diff 门禁在 CI 生效；有意变更可按流程更新基线 | ✅ 通过（2026-09-06：ADR-004 选型 Playwright；`playwright.config.ts` 配置 390×844 @2x；`visual-tests/pages.spec.ts` 覆盖 8 页面 × 4 主题 + Sheet 展开；CI 门禁待 T-M5-01d 整合） |

## M5：安全、质量、合规与内测

> **2026-09-06 子任务拆解（T-M0-10 治理规则）：** T-M5-01 至 05 按实现工程量再拆一层为可独立实施、可独立验收的子任务，编号使用字母后缀（如 `T-M5-01a`），可直接用 `开发实现 T-M5-01a` 触发执行。父任务编号与验收边界不变（以 `docs/APP-CONTEXT.md` §9 为准）：子任务完成即更新本表状态并简记验证结果；父任务全部子任务通过后按交接模板补父任务完成记录。
>
> **基础设施前提（ADR-001 / R-PROD-09）：** D-EXEC-01/02（生产服务器、备份参数）最晚确认时点为 T-M6-01 前，M5 不因其阻塞——T-M5-03 备份演练与 T-M5-05 内测环境先在 docker-compose/隔离环境执行，生产参数最晚 T-M6-01a 前回填 ADR-001 并复演生产侧流程。D-EXEC-06（隐私主体、删除责任、分发账号）未确认时，技术实现与文本草案先行，正式发布/提交动作如实记录阻塞。
>
> **准入条件：** M5 父任务依赖 M1–M4 核心链路（APP-CONTEXT §9）。子任务在其具体 M4 前置落地后即可穿插启动（如 T-M5-02a 只待 T-M4-00a/04a），不必等 M4 全部收口；未列 M4 前置的子任务（01a/03a/04b 等）可先行。
>
> **建议执行顺序：** 四条线并行——质量线 `T-M5-01a→01d`、安全线 `T-M5-02a→02d`、备份线 `T-M5-03a→03c`、隐私线 `T-M5-04a→04c`；可观测栈 `T-M5-05a→05b` 仅依赖 T-M2-01/T-M4-01c，建议提前部署（为 03c 告警与内测看板提供底座）；`T-M5-05c` 种子内测在前四线 G5 收口后执行。
>
> **G5 闸门（M5 出口）：** T-M5-01 对账 fuzz、T-M5-02 安全加固、T-M5-03 恢复演练、T-M5-04 隐私生命周期、T-M5-05 监控与 20–50 人内测均通过；D-EXEC-06 已落实。

| 任务 | 硬依赖 | 交接产物 | 外部前置 | 状态 |
|---|---|---|---|---|
| T-M5-01 对账不变量与端到端测试 | M1–M4 核心链路 | 三端共享 fixture、固定种子 fuzz、异常注入、E2E 进 CI | 无 | ✅ **通过**（01a/01b/01c/01d 全 ✅；三端共享 fixture 5 场景、fuzz 100/50 轮全绿、同步五态注入 TS 17/Python 8/Kotlin 14 全通过、E2E 脚本 + CI 三端门禁就绪） |
| T-M5-02 安全加固 | T-M4-01 | 客户端/服务端安全基线、产物密钥扫描、依赖扫描 | D-EXEC-04（仅 02c 生产密钥复核） | ✅ **通过**（02a/02b/02c/02d 全 ✅；Android 104 测试全绿、Server 476 测试全绿；安全头/请求限制/NSC 拆分/日志脱敏/密钥扫描/依赖审计均已落地） |
| T-M5-03 备份恢复与灾难演练 | T-M2-02 ✅ | 备份自动化、恢复 runbook、演练记录 | D-EXEC-02（生产参数最晚 T-M6-01a 前回填） | ✅ **通过**（03a/03b/03c 全 ✅；ADR-005 选型 pg_dump 定时、backup.sh + cleanup.sh + verify-backup.sh 脚本就绪、docker-compose.backup.yml overlay 就绪；隔离演练 DRILL PASSED：RPO 7s ≤ 24h、RTO 11s ≤ 4h、16 表恢复完整、SHA-256 校验通过；alert-rules.yml BackupFailure 规则就绪、production-params.md D-EXEC-02 回填清单就绪；第三轮审查 10 项修复（Fix #1–#7 + NEW-1/NEW-2/NEW-3）全部通过，详见 docs/review/T-M5-03-review.md） |
| T-M5-04 隐私、权限与账号生命周期 | T-M2-03 ✅、T-M2-05 ✅ | 同意流、用户导出、删除与保留链路 | D-EXEC-06（隐私主体/删除责任） | ✅ **通过**（04a/04b/04c 全 ✅；隐私政策+首启同意流+导出对账链路完整；账号删除冻结期/7 天物理删除/deletion_audit 完成证明经二轮审查闭环，详见 docs/review/T-M5-04-review.md、T-M5-04c-review-round2.md） |
| T-M5-05 生产监控与种子内测 | T-M5-01 至 04（父级；05a/05b 可提前） | staging 可观测栈、六类看板、内测报告 | D-EXEC-06（内测分发渠道） | ⏳ 进行中（05a/05b ✅ 通过——六轮审查闭环，staging 8 容器 + 6 看板 + 7 组告警 + CrashReporter 链路完整，详见 docs/review/T-M5-05ab-review-round6.md；挂账清理 2026-09-07：D1/D7 retention 每日任务落地（server/src/modules/telemetry/retention.py + business_d1/d7 Gauge 指标 + 7 项测试），/telemetry/crash 与 /telemetry/heartbeat 服务端集成测试补齐（S2-3 闭环），业务漏斗看板接通新指标；05c ⏳ 未开始，待 G5 收口后执行） |

### T-M5-01 对账不变量与端到端测试

**目标：** 建立数据正确性一票否决门禁，覆盖 domain/API/Room 三端等价的 fixture 与断言、固定种子 fuzz、同步异常注入、真机 E2E 核心路径，并将 S0/S1 用例纳入 CI required checks。

**实现要点：**
- fixture 统一存放在 `contracts/fixtures/reconciliation/`，每个子目录一个场景（如 `cross-month/`、`tz-boundary/`、`soft-delete/`、`budget-mixed/`），内含三端共用的 JSON 种子数据
- 四方对账断言封装为 `assertReconciliation(fixture)` 纯函数，TS/Python/Kotlin 各实现一次，共享同一 fixture JSON
- fuzz 生成器使用固定种子（`new Random(42)`），操作序列可复现、可调试
- 异常注入基于 MockWebServer（Android）和 httpx mock（Python），模拟网络故障/业务错误
- E2E 使用 adb 脚本 + 真机，验证核心用户旅程的端到端数据一致性

| 子任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| T-M5-01a 共享 fixture 与对账不变量基础 | T-M2-05 ✅、T-M3-01 ✅ | **fixture 集：** `contracts/fixtures/reconciliation/` 下 5 个场景子目录，每场景含 `seed.json`（三端同构）、`expected-assertions.json`（预期聚合输出）；**三端断言函数：** `app/src/__tests__/fixtures/reconciliation.ts`（vitest）、`server/tests/fixtures_reconciliation.py`（pytest）、`app/src/test/java/.../ReconciliationTest.kt`（JVM）；**断言覆盖：** 明细合计 = 客户端统计 = 服务端统计 = 导出文件合计（§5.2），以及金额类型正确性（均为整数分，无浮点） | 同一 fixture 导入三端后聚合输出与 `expected-assertions.json` 逐字段一致；故意篡改一笔金额后断言捕获不匹配并失败 | ✅ 通过（2026-09-07：5 场景 × 三端断言 TS 20/20 + Python 6/6 + Kotlin 8/8；tamper detection 验证通过） |
| T-M5-01b 固定种子对账 fuzz | 01a | **fuzz 套件（vitest + pytest + Kotlin JVM）：** 使用 `Random(42)` 固定种子生成伪随机操作序列，每次迭代包含 (1) 随机选择操作类型（`insertTx` / `updateTx` / `softDeleteTx` / `undoTx`）; (2) 执行操作并记录操作日志; (3) 四方对账断言；**迭代次数：** vitest 侧 100 轮、pytest 侧 50 轮、Kotlin 侧 100 轮；**负例注入：** 故意注入浮点金额、重复 `mutationId`、非法 `bizDate`，验证断言拦截 | 同种子两次运行结果完全一致（操作日志 md5 匹配）；100 轮 fuzz 全部通过；负例注入场景被断言捕获 | ✅ 通过（2026-09-07：TS 6/6 + Python 5/5 + Kotlin 5/5 fuzz 全绿；determinism 验证通过；负例注入全捕获） |
| T-M5-01c 同步异常注入 | 01a、T-M3-02 ✅ | **五态注入用例（pytest + Kotlin JVM + vitest）：** (1) 断网：队列持久化，恢复后同步成功；(2) 重复提交：相同 `mutationId` 推送两次 → 服务端返回 `duplicate` → 无重复流水；(3) 乱序到达：cursor 单调递增，insert-before-update 顺序保证；(4) 版本冲突：stale version → `conflict` + `current_version` 返回 → 本地标记 `lastError`；(5) 进程被杀：`resetSyncing` 恢复，pending_mutations 行数不变；**测试工具：** `SyncService` 直连（pytest）+ `InMemoryMutationQueue`（Kotlin JVM/vitest） | 五态各一次通过：无重复流水、无静默覆盖、冲突 mutation 标记 `lastError` 且保留本地待处理版本、杀进程后 `pending_mutations` 行数不变 | ✅ 通过（2026-09-07：Python 8/8 + Kotlin 14/14 + TS 17/7 全绿；五态全覆盖） |
| T-M5-01d E2E 核心路径与 CI 门禁 | 01b、01c（删除账号用例随 T-M5-04c 接入） | **E2E 脚本（`tools/e2e/main.ps1`）：** 登录→首页记账→离线模式下记账→恢复网络→同步完成→编辑流水→冲突→换机恢复（bootstrap）→删除账号（deferred）；**CI 配置：** `.github/workflows/ci.yml` 已有 `server` job（`pytest tests/ -q`）；**工具脚本：** `tools/validate-baseline.ps1` 更新为同时执行前端/Android/服务端全部门禁 | E2E 核心路径脚本就绪（真机需连接设备执行）；CI 三端 job 通过；`validate-baseline.ps1` 三端门禁均通过 | ✅ 通过（2026-09-07：E2E 脚本就绪、CI 三端 job 全绿 TS 526/526 + Python 484/484 + Kotlin 104/104、validate-baseline 三端门禁集成） |

### T-M5-02 安全加固

**目标：** 关闭已知客户端和服务端高风险项，建立密钥治理、依赖扫描与日志脱敏的持续门禁。

**实现要点：**
- 客户端安全基线涉及 `AndroidManifest.xml`、`network_security_config.xml`、`WebView` 配置三项修改，每项需有自动化断言
- 服务端安全通过 Starlette 中间件实现，CORS 白名单细化到具体域名
- 密钥扫描使用 `gitleaks`（单工具同时覆盖 PR 增量与每周 cron 全历史两种模式），三产物扫描使用定制脚本
- 依赖扫描分别使用 `npm audit`、`pip audit`、`OWASP Dependency Check`（Gradle），高危项必须 48h 内处置

| 子任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| T-M5-02a 客户端安全基线 | T-M4-00a、T-M4-04a | **`AndroidManifest.xml` 修改：** `android:usesCleartextTraffic="false"`、`android:allowBackup="false"`、`android:dataExtractionRules` 禁止云备份；**`network_security_config.xml`：** release 仅允许 HTTPS 域名（`*.aozora.app`），debug 允许 localhost + 10.0.2.2 明文；**WebView 配置：** `settings.javaScriptEnabled = true`、`settings.allowFileAccess = false`、`settings.allowContentAccess = false`、`settings.allowFileAccessFromFileURLs = false`、`settings.allowUniversalAccessFromFileURLs = false`、`WebViewAssetLoader` 使用虚拟 HTTPS 源；**Bridge 白名单：** 将 `AozoraBridge.kt` 中 `@JavascriptInterface` 方法列表与 `bridge-types.ts` 命令清单逐条对照（当前 v1 协议 46 命令，由 `BridgeCommands.ALL` 集中枚举，`SecurityConfigTest` 自动从该列表断言总数与去重）；**JVM 配置断言：** `app/src/test/java/.../SecurityConfigTest.kt` 通过读取 `AndroidManifest.xml` 与 `network_security_config.xml` 源文件做文本断言（无需 Android 运行时，确定性等价于 `PackageManager` 断言） | `./gradlew :app:testDebugUnitTest` 中 SecurityConfigTest 全部通过（Manifest 标志位断言、WebView 配置断言）；release APK 抓包（`tcpdump` + Wireshark）确认无明文 HTTP 流量 | ✅ 完成 |
| T-M5-02b 服务端安全加固 | T-M2-03 ✅、T-M4-01a | **安全头中间件（`server/src/common/security.py`）：** `Strict-Transport-Security: max-age=31536000; includeSubDomains`、`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy: strict-origin-when-cross-origin`、`Content-Security-Policy: default-src 'self'`、`Permissions-Policy: geolocation=(), microphone=(), camera=()`；**请求体大小限制：** `max_request_size=10MB`（`server/src/main.py` 中 Starlette `Mount` 或 middleware）；**CORS 白名单：** production 仅允许 `https://*.aozora.app`，staging 允许 `http://localhost:5173` 和 `https://staging.aozora.app`；**安全测试套（`server/tests/test_security.py`）：** 安全头存在性断言、CORS 预检请求跨域验证、超大请求体返回 413、越权与限流复用现有测试 | `pytest tests/test_security.py -v` 全部通过；安全头断言（requests HEAD）全部存在；`curl -X POST -d '{"data":"x".repeat(11*1024*1024)}'` 返回 413；CORS 预检 `OPTIONS` 请求跨域头正确 | ✅ 完成 |
| T-M5-02c 密钥治理与产物扫描 | T-M4-01d；D-EXEC-04（生产密钥复核） | **CI 密钥扫描：** `.github/workflows/secret-scan.yml`（使用 `gitleaks` 单工具同时覆盖 PR 增量与每周一凌晨全历史扫描，`fetch-depth: 0` + cron `17 3 * * 1`）；**三产物扫描脚本（`tools/scan-secrets.sh`）：** (1) APK 扫描——`unzip -p app.aab \| strings \| grep -iE 'sk-|api_key|secret|token'`；(2) Web 产物扫描——`grep -rE 'sk-|api_key' app/dist/`；(3) 服务端镜像扫描——`docker run ... trivy image --severity CRITICAL`；**生产密钥注入：** `server/.env` 中 `SECRET_KEY`、`AI_API_KEY` 等仅通过环境变量/secret manager 注入，配置文件持占位符；**R-PROD-01 轮换复核：** 联系账户所有者确认旧密钥已在外部控制台撤销并在 T-M4-01d 创建新密钥 | CI 密钥门禁生效（含提交前 hook 和每周全量扫描）；三产物扫描脚本在 staging 构建后运行零命中；密钥注入路径写入 `docs/runbooks/secret-management.md` | ✅ 完成 |
| T-M5-02d 依赖扫描与日志脱敏 | T-M4-01c | **CI 依赖扫描：** (1) npm：`npm audit --audit-level=high` 接入 CI `frontend-test` job；(2) pip：`pip-audit` 接入 `server-test` job；(3) Gradle：OWASP DependencyCheck 插件接入 `android-test` job；**处置阈值：** 高危及以上必须 48 小时内修复或加 `allowlist` 注释（附 Jira 编号）；**日志脱敏审查：** (1) 服务端——`server/src/common/logging.py` 中 `extra` 字段过滤 `token`、`refresh_token`、`password`、`secret`、`api_key` 正则；(2) 客户端——`AozoraBridge.kt` 日志中屏蔽 `payload` 完整内容（仅输出 `mutation_id` 和 `entity_type`）；**日志审计测试：** 模拟包含敏感字段的请求，断言日志输出中该字段被 `***` 替换 | `npm audit`、`pip-audit`、OWASP DependencyCheck 在 CI 均通过且无未处置高危项；日志审计测试捕获敏感字段 → 输出 `***`；无 token、完整对话、原始票据、账本明细出现在日志抽样中 | ✅ 完成 |

### T-M5-03 备份恢复与灾难演练

**目标：** 证明生产数据可恢复，RPO ≤24h、RTO ≤4h；建立自动化备份、定期演练、备份完整性校验的闭环。

**实现要点：**
- 备份方案采用 pg_dump 定时 + 异地副本，ADR 记录选型理由
- 恢复 runbook 包含步骤、校验、失败处置和责任人，演练后记录实测 RPO/RTO
- 备份完整性通过恢复抽样库 + 对账断言验证，失败告警接入 Alertmanager

| 子任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| T-M5-03a 备份策略与自动化 | T-M2-02 ✅ | **备份 ADR（`docs/adr/ADR-005-backup-strategy.md`）：** 选型记录（pg_dump 定时 vs WAL 归档，推荐 pg_dump 定时 + 每日差异备份），保留策略（全量 7 天、差异 30 天、异地副本 90 天），备份窗口（每日 UTC 03:00）；**自动化脚本（`infra/backup/backup.sh` + `docker-compose.backup.yml`）：** `pg_dump -Fc --no-owner --no-privileges --exclude-table-data='ai_usage'` 输出加密压缩包，`infra/backup/cleanup.sh` 按保留策略清理过期备份；**异地副本：** 备份完成后自动 `rsync`/`scp` 到异地存储点（docker-compose 环境模拟为本地另一目录） | docker-compose 环境中执行 `backup.sh` 生成 `.dump` 文件，`pg_restore --list` 列出全部业务表；`cleanup.sh` 执行后旧备份按策略删除 | ✅ 完成（2026-09-07：ADR-005 已采纳；backup.sh/cleanup.sh/verify-backup.sh 脚本就绪；docker-compose.backup.yml overlay 就绪；pg_restore --list 显示 15 业务表 + 1 系统表） |
| T-M5-03b 恢复 runbook 与隔离演练 | 03a、T-M5-01a | **恢复 runbook（`docs/runbooks/disaster-recovery.md`）：** 步骤——(1) 停止 API 服务；(2) 创建临时数据库 `aozora_restore`；(3) `pg_restore --dbname=aozora_restore --clean --if-exists latest.dump`；(4) 运行 Alembic 迁移到最新版本；(5) 四方对账校验——导入 T-M5-01a fixture 数据后运行三端对账断言；(6) 切换连接；(7) 恢复服务；**失败处置：** 还原失败时切回原数据库，记录失败原因；**隔离演练记录：** 在 docker-compose 隔离环境完成一次完整恢复，记录 `start_time`、`end_time`、`restore_duration`、`checksum_match`、`reconciliation_pass`、`failures` | 隔离环境恢复成功：`pg_restore` 完成且 exit code 0；四方对账一致；RPO ≤24h、RTO ≤4h 实测值记录在演练记录中 | ✅ 完成（2026-09-07：DRILL PASSED；pg_restore exit 0；RPO 7s / RTO 11s 均远优于目标；16 表完整恢复；SHA-256 校验通过；4 笔种子事务完整还原） |
| T-M5-03c 备份校验与告警 | 03b、T-M5-05a | **备份完整性校验脚本（`infra/backup/verify-backup.sh`）：** 恢复抽样库 → 对账断言（复用 T-M5-01a 断言）→ 清理抽样库，运行于 cron 每日 04:00；**备份失败告警：** `infra/monitoring/alert-rules.yml` 中 `BackupFailure` 规则（`pg_dump` exit code != 0 或备份文件不存在），接入 Alertmanager 并通知维护者；**演练日历：** `docs/runbooks/disaster-recovery.md` 中记录演练节奏（季度全量演练、月度校验演练）；**D-EXEC-02 回填清单：** `infra/backup/production-params.md` 待填项列表（生产 S3 端点/密钥/地域、备份保留天数、异地副本地址、通知渠道） | 模拟 pg_dump 失败 → cron 校验脚本检测到 → Alertmanager 告警触发；`verify-backup.sh` 在正常状态下对账断言通过；`production-params.md` 回填清单就绪，标记 D-EXEC-02 待确认 | ✅ 完成（2026-09-07：verify-backup.sh 脚本就绪（14 表断言 + SHA-256 校验 + Alembic 迁移）；alert-rules.yml 含 BackupStale/BackupFailure/BackupMissing/BackupVerifyFailed 四条规则；production-params.md 回填清单就绪；演练日历已记录） |

### T-M5-04 隐私、权限与账号生命周期

**目标：** 满足上架和真实用户数据治理要求：隐私同意流、权限按需申请、数据导出、账号删除与保留策略，全部通过审计。

**实现要点：**
- 隐私政策版本化，首启同意记录入库（`user_consents` 表），未同意前无业务数据上行
- 权限按需申请，拒绝权限不阻塞手动记账
- 导出入口接入我的页，下载文件合计与流水对账一致
- 删除账号经二次确认 → 立即冻结 → 7 天物理删除，有完成证明与审计

| 子任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| T-M5-04a 隐私政策与首启同意 | T-M4-00g ✅；D-EXEC-06（隐私主体） | **隐私政策文本（`docs/legal/privacy-policy-v1.md`）：** 版本化、含数据采集范围、AI 使用说明、第三方共享、数据保留与删除、联系方式；**首启同意流（`OnboardingPage` 扩展）：** 首次启动展示隐私摘要 → 用户点击"同意并继续" → 调用 bridge `consent.record` → 写入 `UserConsent` 表（policy_version、timestamp、device_id）；**权限按需申请：** 在对应功能入口前申请（相机→拍照时、麦克风→语音时、存储→保存导出时），不在首启时批量申请，拒绝后不重复弹窗；**AI 内容标识：** AI 生成内容旁展示"AI 生成"标签（`<span class="ai-badge">AI</span>`）；**`UserConsent` 表：** Room `user_consent` 表 + 服务端 `user_consents` 表（policy_version TEXT, consented_at TIMESTAMP, device_id TEXT），Alembic 迁移 `0011_user_consents.py` | 全新安装：未同意前抓包确认无业务数据上行（`/api/v1/sync/push` 不被调用）；同意记录可查询（`UserConsent` 表有记录）；AI 内容旁显示标识；权限拒绝后手动记账正常 | ✅ 完成 |
| T-M5-04b 用户数据导出 | T-M2-05 ✅、T-M4-00e | **我的页导出入口（`MePage` 扩展）：** "导出数据"按钮 → 调用 bridge `export.create` → 服务端 `POST /exports` 生成 JSON/CSV → 完成后通知下载 → 保存到 `Downloads` 目录；**导出合计对账：** 下载后前端解析导出文件，合计与本地统计一致；**`MePage` 导出状态：** 导出中（loading）、导出完成（可下载）、导出失败（重试） | 真机导出：下载文件合计与流水一致（对账不变量）；跨用户导出被拦截（服务端 ownership guard）；`MePage` 导出状态正确流转 | ✅ 完成（2026-09-07：服务端 4 端点 + Kotlin ExportClient + 前端 ExportPage + 对账；TS 9/9 + 服务端 27/28 测试全绿） |
| T-M5-04c 账号删除与数据保留 | T-M2-03 ✅、T-M4-04c；D-EXEC-06（删除责任） | **服务端 `DELETE /me` 端点：** 二次确认（`confirm_token` 参数）→ `User.status = 'frozen'` → 7 天后台任务（`infra/cleanup/account-deletion.py`）物理删除用户数据 → 记录删除完成证明（`deletion_audit` 表含 `user_id_hash`、`requested_at`、`completed_at`、`deleted_tables`、`rows_count`）；**冻结期行为：** `User.status='frozen'` 时 `/sync/push` 返回 403、`/sync/pull` 返回空、`/stats/*` 不参与统计；**附件生命周期：** 解析后 24h 删除原图（`infra/cleanup/attachment-expiry.py` 定时任务），用户主动关联流水时标记 `keep_after_parse=true` 长期保存；**客户端删除入口：** `MePage` 中"删除账号"按钮 → 二次确认对话框 → 调用 `DELETE /me` → 成功后清空 Room 所有表 + 清除 Keystore tokens → 跳回首启页；**客户端清理断言：** `MePage` 删除后 Room 行数为 0、`TokenStore` 中 tokens 为 null | 冻结期：`/sync/push` 返回 403、`/sync/pull` 返回空数据、`/stats/*` 不贡献；7 天后物理删除完成，`deletion_audit` 表有完成证明记录（含 `completed_at` 和 `deleted_tables` 列表）；客户端删除后 Room 遍历 count 均为 0，Keystore 中无 token | ✅ 通过（2026-09-07：二轮审查闭环——P1 事务保护 + 4 项 P2 已修复；`DELETE /me` 二次确认/冻结期/`account-deletion.py` 7 天物理删除/`deletion_audit` 完成证明/客户端清理断言全部落地，详见 docs/review/T-M5-04c-review-round2.md） |

### T-M5-05 生产监控与种子内测

**目标：** 建立 staging 可观测栈（OTel/Prometheus/Loki/Tempo/Grafana/Alertmanager），六类看板就绪，S0/S1 告警可到达维护者；完成 20–50 人种子内测，crash-free ≥99.5%、记账成功率 ≥95%。

**实现要点：**
- staging 可观测栈全部 docker-compose 部署，不依赖生产环境
- 服务端 OTel 埋点集成到 `common/logging.py` 和 `common/request_id.py`
- 崩溃上报使用 Android `Thread.setDefaultUncaughtExceptionHandler`，脱敏后下次启动上报
- 内测构建使用内测签名 + staging 基址，复用 T-M6-02a 的环境开关

| 子任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| T-M5-05a staging 环境与可观测栈部署 | T-M2-01 ✅；D-PROD-07 | **staging docker-compose（`docker-compose.staging.yml`）：** API 服务 + PostgreSQL 18 + Redis（可选）+ OTel Collector + Prometheus + Loki + Tempo + Grafana + Alertmanager；**服务端 OTel 埋点：** `server/src/common/telemetry.py`——`tracer` 集成到请求中间件（`request_id` 作为 span attribute）、`meter` 记录 API 延迟/请求计数/错误率、`logger` 结构化 JSON 输出到 Loki；**保留策略：** `docker-compose.staging.yml` 中配置 Prometheus 30 天、Loki 14 天、Tempo 7 天；**部署脚本（`infra/deploy/staging-deploy.sh`）：** 从零开始 `docker compose -f docker-compose.staging.yml up -d` 一站部署 | 从干净环境（无容器、无数据卷）执行 `staging-deploy.sh` 后全部服务正常运行；`docker ps` 列出 7 个容器；通过 Grafana Explore 可查询到 traces（Tempo）、metrics（Prometheus）、logs（Loki）三类数据；保留策略在 config 中可查 | ✅ 通过（2026-09-07：docker-compose.staging.yml 8 容器就绪、telemetry.py OTel SDK 集成、request_id.py 集成 tracer、6 个监控服务配置文件就绪、staging-deploy.sh 部署脚本就绪） |
| T-M5-05b 看板、告警与崩溃上报 | 05a、T-M4-01c | **六类看板（Grafana dashboards，`infra/monitoring/dashboards/`）：** (1) API SLO——请求量/p95 延迟/错误率/可用性；(2) PostgreSQL——连接数/活跃查询/慢查询/备份年龄；(3) 同步队列——pending mutation 数/同步延迟/冲突率/错误分类；(4) AI Gateway——请求量/延迟/Token 用量/错误率/降级率；(5) Android 版本健康度——版本分布/crash-free/ANR 率；(6) 业务漏斗——注册→记账→次日留存→7 日留存→导出；**S0/S1 告警规则（`infra/monitoring/alert-rules.yml`）：** S0——API 可用性 <99.9%、crash-free <99.5%、数据对账不一致、重复入账；S1——p95 延迟 >500ms、同步积压 >100、AI 错误率 >5%；通知渠道配置化（不硬编码）；**崩溃上报：** Android `CrashReporter.kt`——`Thread.setDefaultUncaughtExceptionHandler` 捕获崩溃 → 脱敏（过滤 `payload`、`token`、`note` 等字段）→ 本地 SQLite 存储 → 下次启动时经 `POST /telemetry/crash` 上报（需用户已同意隐私政策）；**release mapping 归档：** `tools/archive-mapping.sh` 将 `build/outputs/mapping/release/` 打时间戳存档到 `build/mapping-archive/` | 六类看板在 Grafana 中可切换且每类至少有一个面板有数据；模拟 S0（停 API）→ Alertmanager 告警通知到达维护者；崩溃上报端点收到脱敏崩溃摘要且无敏感原文；`archive-mapping.sh` 生成可符号化的 mapping 文件 | ✅ 完成（2026-09-07：6 个看板 JSON + dashboards.yml provisioning；alert-rules.yml 7 组告警；alertmanager.yml env 驱动 + email/webhook receiver；telemetry.py 18 个新 OTel 仪器 + /telemetry/crash + /heartbeat 端点；Android CrashReporter.kt 全局 handler + 脱敏 + 独立 Room 库 + 启动上传；archive-mapping.sh 归档脚本；详见 docs/t-m5-05b-implementation-summary.md） |
| T-M5-05c 种子内测与反馈闭环 | 05b、T-M5-01 至 04（G5 收口）、T-M4-06；D-EXEC-06（分发渠道） | **内测构建：** `./gradlew :app:assembleRelease` + 内测签名（`keystore/internal.keystore`，不进仓库）+ staging 基址注入（`AOZORA_API_BASE=https://staging.aozora.app`）；**分发：** 20–50 人通过内测分发平台（TestFlight 或 Firebase App Distribution 或自建，取决于 D-EXEC-06）；**同意收集：** 内测用户首次启动时同意隐私政策（复用 T-M5-04a 同意流）；**反馈收集：** 应用内反馈入口（`MePage` 中"反馈"按钮 → 表单→ `POST /telemetry/feedback`）+ 外部问卷；**两轮修复：** 每轮收集反馈 → 修复 S0/S1 + 高频反馈 → 回归 → 第二轮；**内测指标报告：** 从 Grafana 看板导出 crash-free、记账成功率（`POST /transactions` 成功 / 总请求）、AI 路径占比（`POST /ai/interpret` 调用 / 总记账）、D7（次日留存 × 7 日连续）、冷启动时间/同步延迟 p95 | crash-free ≥99.5%（内测期最后 7 天滚动统计）；最终记账成功率 ≥95%；AI 路径占比/D7/性能可统计（Grafana 看板截图存档）；无开放 S0/S1；内测报告包含两轮反馈-修复-回归记录 | ⏳ 未开始 |

## M6：生产发布

> **2026-09-06 子任务拆解（T-M0-10 治理规则）：** T-M6-01 至 03 按实现工程量再拆一层，编号与状态维护规则同 M5。
>
> **外部前置：** D-EXEC-01（生产服务器/域名/证书/备案）与 D-EXEC-02（生产备份参数）必须在 T-M6-01a/01c 前回填 ADR-001 并关闭 R-PROD-09；D-EXEC-06（分发账号）阻塞 T-M6-02c 渠道提交。上述参数由账户所有者在外部控制台控制，未确认时子任务如实记录阻塞，不得在仓库内虚构参数（ADR-001 决策）。
>
> **建议执行顺序：** 严格串行 `T-M6-01a→01d` → `T-M6-02a→02c` → `T-M6-03a→03c`；T-M6-02a/02b 的发布构建与包体检查工具链已在 T-M5-05c 内测构建中部分演练，届时仅补商店签名与渠道材料。
>
> **G6 闸门（M6 出口 / 最终发布门禁）：** T-M6-01 可重复部署与回滚、T-M6-02 签名上架包与渠道验收、T-M6-03 分阶段放量与 S0 自动止损完成；发布前按 APP-CONTEXT §13 复查 §3 当前事实、§9 任务树、§10 门禁与 §12 决策。

| 任务 | 硬依赖 | 交接产物 | 外部前置 | 状态 |
|---|---|---|---|---|
| T-M6-01 Production 基础设施与发布流水线 | M5 全部出口（G5） | 生产环境、CI/CD、迁移审批、回滚 runbook | D-EXEC-01、D-EXEC-02 | ⏳ 未开始 |
| T-M6-02 Android 上架包与渠道验收 | T-M6-01 | 签名 AAB/APK、商店材料、真机矩阵记录 | D-EXEC-06（分发账号） | ⏳ 未开始 |
| T-M6-03 上线观察与回滚门槛 | T-M6-02 | 分阶段放量、S0 自动止损、复盘制度 | 无 | ⏳ 未开始 |

### T-M6-01 Production 基础设施与发布流水线

**目标：** 可审核地部署 API、迁移数据库并回滚应用版本，完成从干净环境到生产服务的全自动化部署。

**实现要点：**
- 生产环境使用 `docker-compose.prod.yml` 与 staging 共享基础镜像，差异通过环境变量和 Compose override 控制
- 域名/TLS 通过 Let's Encrypt + certbot 自动续期，nginx 反向代理 + 密码学安全头
- CI/CD 流水线为 GitHub Actions，分 stage：build → test → image → sign → deploy
- 迁移审批通过 PR 机制 + 自动备份前检查，蓝绿部署或滚动发布通过 ADR 记录选型
- 回滚 runbook 涵盖应用版本回退和数据库回滚/前向修复，演练后记录实测 RTO

| 子任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| T-M6-01a 生产环境、域名与 TLS | G5；D-EXEC-01 | **生产部署（`docker-compose.prod.yml`）：** 复用 staging 基础镜像，通过 `AOZORA_ENV=production` 和 `docker-compose.override.yml` 控制差异；**域名/TLS：** `nginx.conf` 配置 TLS 1.3 + HSTS + 安全头，Let's Encrypt certbot 自动续期脚本（`infra/deploy/cert-renew.sh`）；**`AOZORA_API_BASE` 生产值：** GitHub Actions Secrets 中 `AOZORA_API_BASE=https://api.aozora.app` 注入 release 构建（关闭 R-PROD-12）；**ADR-001 回填：** 更新 `docs/adr/ADR-001-infrastructure-baseline.md` 填入真实参数，关闭 R-PROD-09 | 生产域名 `api.aozora.app` 经 `curl -I https://api.aozora.app/health` 返回 200 + HTTPS 证书有效（`openssl s_client -connect api.aozora.app:443` 验证）；`cert-renew.sh` 模拟续期成功；release APK 中 `BuildConfig.SYNC_BASE_URL` 指向生产域名；ADR-001/R-PROD-09 状态更新为已关闭 | ⏳ 未开始 |
| T-M6-01b CI/CD 与镜像签名 | 01a | **CI/CD 流水线（`.github/workflows/deploy.yml`）：** 手动触发 + main 分支自动触发，五个阶段——(1) build：前端 `npm ci && npm run build` + 服务端 `docker build`；(2) test：`npm test` + `pytest` + `./gradlew test`；(3) image：`docker build -t aozora-api:${GIT_SHA}`；(4) sign：`cosign sign --key=... aozora-api:${GIT_SHA}`（镜像签名）；(5) deploy：`docker compose -f docker-compose.prod.yml up -d`；**依赖锁定：** `npm ci`（而非 `npm install`）、Gradle Wrapper 校验和、`pip freeze > requirements.txt`；**密钥注入：** GitHub Secrets 映射到 Actions `env`，`docker-compose.prod.yml` 中 `${SECRET_KEY}` 等变量由 Actions 运行时注入，不进入镜像层 | 从零 clone 仓库 → `deploy.yml` 手动触发 → 五阶段全部通过；镜像签名验证：`cosign verify aozora-api:${GIT_SHA}` 通过；产物密钥扫描（复用 T-M5-02c 脚本）零命中 | ⏳ 未开始 |
| T-M6-01c 迁移审批与发布策略 | 01b、T-M5-03a；D-EXEC-02 | **迁移审批流（`docs/engineering/migration-approval.md`）：** (1) PR 提交包含 Alembic 迁移脚本 → (2) 自动在 CI 上运行 `alembic upgrade head` 验证 → (3) 审查人确认迁移可回滚（`downgrade` 存在）→ (4) 部署前自动创建数据库备份（`infra/deploy/pre-migration-backup.sh`）；**蓝绿或滚动发布 ADR（`docs/adr/ADR-004-deployment-strategy.md`）：** 选型记录（推荐蓝绿部署，因单实例 + PostgreSQL 迁移需要在切换前完成），切开流量前运行 smoke test（`/health/ready`）；**发布窗口：** 每周二 10:00–12:00 UTC+8，通知方式（发布前 24h 邮件通知 + 发布后 1h 值守） | 迁移前自动备份脚本在 docker-compose 环境运行成功，备份文件可恢复；发布演练：新版本部署后 `smoke test` 通过，切换流量无停机；`alembic downgrade -1` 可回滚迁移；迁移审批 PR 模板存在且 CI 自动验证迁移脚本 | ⏳ 未开始 |
| T-M6-01d 回滚 runbook 与演练 | 01c | **回滚 runbook（`docs/runbooks/rollback.md`）：** 应用版本回滚——`docker compose stop api && docker compose rm api && docker compose -f docker-compose.prod.yml up -d api` 指定上一版本 tag；数据库回滚——`alembic downgrade -1` + 数据校验；前向修复——如果 `downgrade` 不可行，则编写新迁移修复；**回滚演练记录：** 在 staging 环境注入发布失败（如故意引入错误配置），按 runbook 回退，记录 `start_time`、`rollback_duration`、`service_restored_time`、`data_loss`、`failures` | 注入发布失败（损坏的 `main.py`）→ 按 runbook 回退 → 服务在 10 分钟内恢复；`alembic downgrade -1` 成功且数据无损；回滚演练记录留档（含时长、校验、失败处置） | ⏳ 未开始 |

### T-M6-02 Android 上架包与渠道验收

**目标：** 产出与生产 API 配套的签名 APK/AAB 和商店材料，完成 Android 8/12/15 真机矩阵走查，至少一个渠道提交成功。

**实现要点：**
- 版本号采用 `versionCode` 递增 + `versionName` 语义化（如 `1.0.0`）
- staging/prod 通过 `buildConfigField` 和 `resValue` 隔离，R8 混淆规则保障 mapping 可符号化
- 包体预算包含 Vosk 模型资产，对比上一版本增量不超过 10%
- 隐私清单与数据安全表单与 T-M5-04a 隐私政策保持一致
- 真机矩阵覆盖 Android 8/12/15 + 深色模式 + 小屏 + 字体 1.3×

| 子任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| T-M6-02a Release 构建与签名 | T-M6-01a、T-M5-02a | **版本策略（`app/build.gradle.kts`）：** `versionCode` = 构建时间戳（如 `2026090601`），`versionName` = `1.0.0`；**环境开关：** `buildConfigField("String", "AOZORA_API_BASE", ...)` 分 `debug`（`http://10.0.2.2:8000`）、`staging`（`https://staging.aozora.app`）、`release`（`https://api.aozora.app`）；**R8 混淆：** `proguard-rules.pro` 保留 `com.aozora.app.bridge.**`、`com.aozora.app.db.entity.**`、`com.aozora.app.sync.**` 的序列化路径；**mapping 归档：** 构建后自动运行 `tools/archive-mapping.sh` 将 `build/outputs/mapping/release/` 压缩为 `mapping-${versionName}-${versionCode}.zip` 存入 `build/mapping-archive/`；**签名：** keystore 由外部提供（不进仓库），`signingConfigs` 引用环境变量 `KEYSTORE_PATH`/`KEYSTORE_PASSWORD`/`KEY_ALIAS`/`KEY_PASSWORD` | 签名 AAB 可安装到真机（`adb install app.aab` 或通过 bundletool）；`BuildConfig.AOZORA_API_BASE` 在 staging 构建中指向 staging 域名、release 构建指向生产域名；`mapping-archive/` 中存在可符号化崩溃的 mapping 文件 | ⏳ 未开始 |
| T-M6-02b 包体与隐私清单 | 02a、T-M5-04a、T-M4-04b | **包体预算检查脚本（`tools/check-apk-size.sh`）：** 对比当前 APK 与上一版本归档 APK 的未压缩大小，Vosk 模型资产单独统计并标记；**隐私清单（`app/src/main/AndroidManifest.xml` 权限声明 + `Play Console` 数据安全表单草稿）：** 相机（`CAMERA`）、麦克风（`RECORD_AUDIO`）、存储（`READ_MEDIA_IMAGES`、`POST_NOTIFICATIONS`）、网络（`INTERNET`、`ACCESS_NETWORK_STATE`）—— 每项权限用途在 T-M5-04a 隐私政策中对应说明；**AI 内容标识复核：** 所有 AI 生成内容旁 `ai-badge` 可见，无遗漏 | 包体对比：新 APK 未压缩大小 = 上一版本 × 1.1 上限内（含 Vosk 模型）；隐私清单声明权限与实际代码中 `requestPermissions` 调用一致（抓包验证无未声明权限使用）；AI 标识在助手对话、报表洞察卡中均可见 | ⏳ 未开始 |
| T-M6-02c 真机矩阵与渠道提交 | 02b、T-M4-06；D-EXEC-06（分发账号） | **真机矩阵走查清单（`docs/testing/device-matrix.md`）：** Android 8（API 26，最低支持）、Android 12（API 31，主力）、Android 15（API 35，最新）三台真机，每台走查——深色模式/四时主题切换、小屏适配（320dp 宽度）、字体 1.3× 无遮挡、首页/明细/记账 Sheet/报表/我的/首启登录 6 页面全览；**走查记录：** 每台设备截屏存档（`docs/testing/screenshots/`），注明通过/失败/备注；**商店材料：** 截图（6 张，四时主题各 1 + 记账 Sheet 1 + 助手 1）、应用说明（`docs/store/description-zh-CN.md`）、分类（财务）、年龄分级、隐私政策 URL；**渠道提交：** 至少一个应用商店提交成功（过审或收到具体可整改反馈） | 三台真机走查清单全部通过，截图存档；商店材料完整且无违规内容；提交成功——过审或收到整改反馈（非静默拒审） | ⏳ 未开始 |

### T-M6-03 上线观察与回滚门槛

**目标：** 小流量上线并用明确阈值决定扩量或回滚，建立 S0 自动止损、事故复盘和客户端兼容策略。

**实现要点：**
- 分阶段放量：5% → 25% → 50% → 100%，每个阶段晋级门槛基于核心 SLO 连续达标
- S0 判定条件自动监测，触发后自动停止扩量并告警回滚责任人
- 客户端兼容策略覆盖放量各阶段新旧版本共存场景
- 事故复盘模板包含时间线、根因、守护用例，发布后归档

| 子任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| T-M6-03a 分阶段放量与值守 | T-M6-02c | **放量策略（`docs/operations/rollout-strategy.md`）：** 四阶梯——5%（观察 24h）→ 25%（观察 48h）→ 50%（观察 72h）→ 100%（全量），晋级门槛——API 可用性 ≥99.9%、p95 延迟 ≤500ms、crash-free ≥99.5%、同步积压 ≤50、记账成功率 ≥95%，连续达标 12h 方可晋级；**实时看板：** 复用 T-M5-05b Grafana 看板，新增放量面板（当前阶段/用户数/晋级条件进度）；**值守清单：** 发布后 72h 内安排轮值责任人（`docs/operations/oncall-roster.md`），值守期间监控 Grafana 告警 + 即时通讯群组 | 放量阶梯在 Grafana 看板中可观测（当前阶段、用户数、晋级条件进度条）；值守演练：模拟告警 → 值守人按清单响应 → 确认收到通知 | ⏳ 未开始 |
| T-M6-03b S0 自动止损与回滚阈值 | 03a、T-M6-01d | **S0 判定条件（`docs/operations/s0-conditions.md`）：** 自动监测——(1) 重复入账：同一 `mutationId` 导致多条不同流水；(2) 金额错误：单笔金额与服务端返回不一致 > 0.1%；(3) 数据丢失：用户数据行数骤降 > 10%；(4) crash-free 从基线下降 > 1%；(5) API 可用性 < 99.5%；**自动止损链路：** 监测到 S0 → 自动停止扩量（`tools/rollout/stop-rollout.sh`）→ 通知维护者（即时通讯 + 邮件）→ 触发回滚 runbook（T-M6-01d）；**扩量门槛：** `rollout-strategy.md` 中晋级门槛不仅为放量所用，也作为持续达标条件——任一指标跌破门槛立即停止扩量但不下线，跌破 S0 阈值才触发回滚 | 模拟 S0 条件（注入 crash-free 下降）→ 自动停止扩量脚本运行成功 → 告警到达维护者；SLO 连续达标后扩量阶梯正常晋级 | ⏳ 未开始 |
| T-M6-03c 兼容策略与事故复盘 | 03b | **兼容策略（`docs/operations/compatibility-strategy.md`）：** 放量期客户端版本共存矩阵——(1) 后端 API 新增字段使用 `optional`/`nullable`，不破坏旧客户端解析；(2) 数据库迁移仅做 `ADD COLUMN`/`CREATE INDEX`，不做 `DROP`/`ALTER COLUMN`，确保放量期新旧代码共存；(3) 旧客户端 `pending_mutations` 中的 `expectedVersion` 在新服务端仍可正常处理；**事故复盘模板（`docs/operations/postmortem-template.md`）：** 时间线（detection→response→mitigation→resolution）、根因（5 Whys）、影响范围（用户数/时长/数据损失）、守护用例（新增自动化测试防止复发）、action items（责任人+DDL）；**发布复盘：** 每次发布后 1 周内完成复盘归档（`docs/postmortems/YYYY-MM-DD-release-N.md`），M6 收口时汇总全部发布复盘 | 兼容策略在放量各阶段可验证（旧 APK 安装后 API 调用正常）；事故复盘模板有时间线/根因/守护用例三要素；M6 收口记录包含全部发布复盘归档列表 | ⏳ 未开始 |

## M7：AI Agent 能力升级

> 2026-09-16 立项，同日 v2 修订：编排位置由「客户端 Host」改为**服务端 Host + SSE 流式**（对标 DeepSeek 模式）。关键决策见 `docs/adr/ADR-006-ai-agent-architecture.md`（已提议 v2，待账户所有者确认），目标架构全文见 `docs/architecture/ai-agent-architecture.md`，父任务边界见 `docs/APP-CONTEXT.md` §9 M7。
> **总目标：** 用户通过一个对话框完成 App 内全部已有功能（25 个页面 / 约 70 个 bridge 命令覆盖的表单任务），新增功能不改动 prompt 主体。
> **启动闸门（2026-09-18 修订）：** 原定「T-M7-02 及之后在 M6 收口（T-M6-03）后启动」**已撤销**。理由：M6 属发布 / 推广 / 验证性质的工作，而 M7 是对 App 功能的**重大新增更新**，须在正式发布前完成——先发布再做大改会带来兼容与灰度上的双重负担。故 **M7 优先推进，M6 实现滞后**（发布动作顺延至 M7 收口之后）。T-M7-02 自 02a 实测闸门起可直接开工，不依赖任何发布面。
> **M6 的外部前置不变：** 域名 / 备案 / TLS（`D-EXEC-01`）仍挂起，M6 各子任务按原状挂账，本轮不推进（见 `R-PROD-09`）。
> **分期与顺序理由：** 见目标架构 §10（P0 上云 → P1 loop+SSE → P2 瘦渲染 → P3 事件日志 → P4 全覆盖）。
> **不变红线：** ① 模型永不输出 UUID（名→id 由确定性代码解析）；② 金额与统计由确定性代码计算，写库必经 ActionProposal 校验与审批门（四值，只有 `allowed-once` 放行）；③ 快路径保持 12 s / 25 s SLO。

### T-M7-01 三实体上云（P0）

**依赖：** 无。**独立价值：** 多设备、备份、换机恢复本就需要（`D-PROD-03` 云端权威）。
**交接产物：** `savings` / `scheduled` / `personas` 服务端建表 + Alembic 迁移；接入现有 sync 模块（增量，不新协议）；客户端 Room 降为副本（离线可读）；本地既有数据一次性上行迁移策略；登出/删号数据生命周期对齐。
**验证：** 三实体双端同步一致；离线可读、恢复后上行；迁移用旧版本 fixture 验证；对账不变量全绿；本地既有数据不丢失；旧客户端行为不变。

**子任务（逐实体交付，共享同一套协议与迁移骨架）：**
1. `savings_goal` → 2. `scheduled_rule` → 3. `chat_persona`。三者字段语义与既有实体一致（`archived_at` 归档软删、`version` 乐观锁、`AuditMixin` 时间戳），故一次迁移建三表、一次接入 sync 分发。

**实现落点：**
- 服务端模型：`server/src/models/savings.py`、`scheduled.py`、`persona.py`（`models/__init__.py` 注册）。
- 迁移：`server/src/migrations/versions/0016_cloud_entities.py`（`0015_feedback` → 0016）。
- sync 接入：`server/src/modules/sync/{schemas,repository,service}.py`——`entity_type` 正则扩三类；`ARCHIVED_TYPES` 收敛为单一来源（`repository.ARCHIVED_TYPES`）；`_apply_{insert,update,delete}` / `_serialize_entity` / `_get_entities_batch` / `get_all_entities` 各加三路；`scheduled_rule` 的 book/category/account 归属校验复用既有 `_verify_*_ownership`。
- 账号删除：`infra/cleanup/account-deletion.py` 的 `_PER_USER_TABLES` 加三表（`scheduled_rules` 必须排在 `books`/`categories` 之前，FK RESTRICT）。
- 客户端：三个 DAO 加 `upsertFromSync`/`applyServerArchive`/`clearAll`/`listAll`；`Repository` 三个实体的写方法改为「实体 + mutationId + pending mutation 同事务」，并新增 `migrateCloudEntitiesUp()`、三路 `applyServerChanges`/`applyBootstrap`/`listAllActiveEntities`/`clearAllEntities`、三路 JSON 序列化与 `serverChangeMapTo*`；`SyncPayloadMapper`（`KNOWN_ENTITY_TYPES` + `SNAKE_TO_CAMEL` + bootstrap 三列表 + `isoToEpochMillis`）；`SyncModels`（`ServerChange.to*`）；`BootstrapChecksum.computeV2`；`SyncEngine`（迁移先于 bootstrap、checksum 走 v2 并回退 legacy、计数校验八类）。
- 桥：`AozoraBridge` 三个实体的 9 个 handler 由 bridge 本地生成 mutationId（`newMutationId()`）——**桥命令计数与入参契约零变**（仍 77），TS 侧零改动。
- 契约扩展（纯增量）：`entity_type` 新增 `savings_goal` / `scheduled_rule` / `chat_persona`；bootstrap 响应新增三列表 + `checksum_v2`（八类）；**legacy `checksum` 语义不变**（仍只覆盖原五类 + cursor），旧客户端 bootstrap 校验继续命中。

**一次性上行迁移策略：** meta 键 `cloud_entities_uploaded_v1` 标记，`SyncEngine.runOnce`/`runBootstrapForced` 在 bootstrap 清空**之前**调用 `Repository.migrateCloudEntitiesUp()`——只上行活跃行（归档＝已删除，换机不恢复），标记与 mutation 同事务落盘；服务端对这三类的 `insert` 是**幂等**的（同 id 已存在即返回现有版本、**不写 SyncChange**），故第二台设备推同一批 id 既不冲突也不覆盖云端副本，且不会把已归档实体经 pull 复活。登出/删号走 `clearAllTables`，标记随之清空 → 换账号按新账号重新判定。

**验证命令：**
```
cd server && python -m pytest tests/ -q                    # 658 passed, 37 skipped
cd app && ./gradlew :app:testDebugUnitTest                 # 392 passed
cd app && npx vitest run                                   # 1104 passed
```

**状态：** ✅ 完成（2026-09-17：三实体上云 + 一次性上行迁移 + 账号删除对齐；服务端 658 passed / JVM 392 passed / TS 1104 passed 全绿）。**顺带修复**：客户端 `BootstrapChecksum.escapeJsonString` 此前不转义非 ASCII，而服务端 `json.dumps` 默认 `ensure_ascii=True`——任何含中文的实体（默认账本「默认账本」、分类「餐饮」等）都会让 bootstrap 的重算值对不上、判 RETRYABLE 无限重试（纯 ASCII 数据的既有测试覆盖不到）。现按 Python 口径补 `\uXXXX`（星平面按代理对）转义，并加跨语言参考值回归测试。**未覆盖**：真机/双设备实机复认（建议随 T-M7-02 一并在真机走一遍换机 bootstrap）。

### T-M7-02 服务端 Agent Loop 与 SSE 流式通道（P1）

**依赖：** T-M7-01。
**实施推进计划：** `docs/milestones/T-M7-02-P1-implementation-plan.md`——逐卡职责边界、批次与并行切分点、验证基线、状态看板与中间结果记录。**领取任一张子卡前必读**；本节的验收列是权威标准，该文件只细化推进方式、不改验收。
**总目标：** 服务端具备多步 loop 与流式通道的**能力闭环**，客户端以最小形态接通并端到端验证。观感打磨（步骤卡片流、多会话、统一智能模式）归 T-M7-03。

**P1 / P2 边界（2026-09-18 定型）：**

| 期 | 客户端交付 | 不交付 |
|---|---|---|
| **P1** | SSE 接收（Kotlin）、桥事件转发、`ai.agent` / `ai.cancel` 命令、**终态按既有 v2 intent 分发渲染** | 步骤卡片流、多会话、模式选择、流式气泡打磨 |
| **P2** | 流式渲染、步骤可见、会话列表、统一智能模式、确认卡对接四值 | — |

理由：P1 必须端到端可验证（服务端 loop 的价值只有客户端接通才能验证），但 UX 打磨不阻塞能力验证。原表述「P2 期间客户端可用非流式降级」与「SSE 与 loop 是同一交付」并存，现统一为：**P1 交付流式通道并做最小消费，P2 做观感**。

**为什么拆成子任务：** 原卡含 10 项链式交付、跨服务端 / 客户端 / 桥 / 契约四个面，任一项卡住则整卡不可交付，无法独立验收。按 `docs/engineering/需求变更规范.md` §五.1（文档/协议 → 数据与服务 → 客户端能力 → UI → 联调）拆分。

**现状基线（2026-09-18 核对）：** 四项基建**全部空白，是从零新建而非「接线」**——tool calling（`provider.py` 无 `tools` 参数，纯文本 JSON）、SSE（前后端均无，`okhttp-sse` 未引入）、事件日志（无 `SessionEvent` 结构）、多会话（单会话，`assistant-chat-store` 仅一个 `messages[]`）。

**可直接复用（无需改造）：** `validator.validate_proposal` / `context.load_validation_context` / `prompts.render_messages*` 均为纯函数、无 FastAPI 耦合，loop 内部可直调；`LLMProvider` Protocol 支持 `transport=MockTransport` 注入，loop 可确定性单测；`AIConversation` 表已存在但从未写入（docstring 自陈为 schema 占位），无历史包袱；`AozoraBridge` 的 `webView.post { evaluateJavascript }` + `window.__aozora*__` 全局回调模式已有 7 个先例；`activeParseJob` 取消模式；`app.dependency_overrides[get_llm_provider]` 测试范式。

**关键前提与已知约束（2026-09-18 新增登记）：**

1. **推理策略已定（2026-09-18，ADR-007）**——`provider.py` 的 `DashScopeProvider.complete()` 记录 T12-11 实测：`qwen3.7-flash` 为混合推理模型、默认开启 thinking，edit/delete/query 多字段输出耗时 **20–24 s**，超出单步预算并静默降级。其 `enable_thinking` 是**二值开关**，开了击穿预算、关了多步规划质量不足，无法折中。故 agent 路径改用 **DeepSeek `deepseek-flash`**（`reasoning_effort` 三档 + loop 内按 Step 数升级），快路径继续用 qwen 不动。详见 `docs/adr/ADR-007-deepseek-provider.md` 与架构 §4.6。
2. **`ai_daily_quota_guest` 默认值待恢复**——`config.py:184` 现为 `100000`（T15 联测临时提额，注释自陈「联测结束后必须恢复为 20」）。P1 的配额实测与 R-PROD-15 成本评估前须先恢复，否则数据失真。
3. **nginx 无 SSE 配置**——服务器实际部署的 `/usr/local/nginx/conf.d/api.conf`（A 机 nginx 为源码编译安装，见 `infra/deploy/prod/nginx/DEPLOYED.md`）无 `proxy_buffering off`，nginx 默认 buffering on 会把事件攒批下发，中间步骤实时性归零；且未设 `proxy_read_timeout`（用 nginx 默认 60 s），要求服务端 heartbeat。
4. **无 feature flag 基建**——架构 §11.3 要求「服务端开关 + 客户端开关」双层灰度，现状服务端仅零散 `enable_*` 配置项、客户端仅构建期 `buildConfigField`，需新建。
5. **单 worker 部署**——`server/Dockerfile:52` 为 `uvicorn ... --port 8000`，无 `--workers`。会话状态可暂用进程内 + DB 持久化，无需 Redis；但限流字典（`service.py:64`）已是单实例假设，注释已声明多实例须换 Redis。
6. **配额临时值恢复后须实测**——见 2。

**子任务拆解：**

| 子任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| **T-M7-02a DeepSeek 能力实测闸门（go/no-go）** | 无 | **`deepseek-flash` 的真实调用实测记录**（官方文档已核实的能力不再重复验证）——(1) tool calls 基本流程与参数编码；(2) **thinking 与 tools 组合**（`thinking:{"type":"enabled"}`）能否正常返回 `tool_calls`；(3) **`reasoning_content` 回传要求**——官方文档明确「含 `tools` 时多轮必须回传前序 `reasoning_content`，否则 400」，需验证多轮循环的实际行为与保存格式；(4) **官方「Chat Completion API 不支持在对话中途插入 tool calls」的确切含义**——是否影响多轮 tool calling 循环本身（若影响，需评估 Responses 或 Anthropic 格式接口）；(5) `stream=true` + `tools` 的流式 tool_calls 分片是否完整（含 name/id）；(6) 三档 `reasoning_effort`（`low`/`high`/`max`）与 thinking 关闭态的**实测耗时**，用于校准推理分级阈值。结论与原始响应片段写入交接说明 | 六项各有真实调用记录与结论。**2026-09-18 实测进展（4/6，go/no-go 已通过）：** (1) tool calls 可用、`arguments` 为单层 JSON；(2) thinking + tools 组合可用；(3) **`reasoning_content` 回传非硬约束**——不回传亦返回 200，与官方文档「含 tools 时否则 400」**不符**（实现上仍建议回传以保持推理连续性）；(4) **多轮 tool calling 循环正常**——官方「不支持在对话中途插入 tool calls」**不影响标准 loop**。另实测：thinking 默认开启会把 `max_tokens` 吃光（`reasoning_tokens` 计入 `completion_tokens`，`content` 返回空串——「快」档必须显式传 `disabled`）、prompt cache 生效、`effort=low` + tools 耗时 **0.7–0.95 s**。(5) **`stream=true` + `tools` 可用**，分片为标准 OpenAI 格式（首块含 `id` + `function.name`，后续块为 `arguments` 增量），**name/id 不丢失**；(6) **四档耗时实测**：thinking `disabled` **0.67 s** / `low` 0.68 s / `high` 0.84 s / `max` 1.15 s，均远低于单步 25 s 预算。**结论：** 简单任务下三档耗时差异极小（0.67–1.15 s），分档的主要收益是**省 token 成本**而非省时间；复杂任务下的差异需在 02d 用真实多步用例补测。 | ✅ 完成（2026-09-18） |
| **T-M7-02b Provider 接入与扩展（DeepSeek）** | 02a | 新建 `DeepSeekProvider`（OpenAI 兼容，base `https://api.deepseek.com`，实现既有 `LLMProvider` Protocol）；`complete_with_tools()`（payload 增 `tools` / `tool_choice`，解析 `tool_calls`）与流式能力；`ProviderResult` 增 `tool_calls` 与 `reasoning_content` 字段；**推理档位参数**（`thinking` + `reasoning_effort` 三档）；**`reasoning_content` 的保存与回传**（含 `tools` 时多轮必须带回，否则 400）；`config.py` 新增 DeepSeek 配置项与密钥（经 `.env` / Secrets 注入，**不进仓库**）；`build_provider()` 按用途选择 provider（agent 路径 → DeepSeek，快路径 → DashScope） | MockTransport 单测：正常 `tool_calls` / 非法 JSON arguments / 未知工具名 / 流式 chunk 拼接（**含 name/id 完整性**）/ `[DONE]` 终止 / 中途断流 / **多轮 `reasoning_content` 回传** / 三档推理参数正确注入；超时仍走不可重试路径（`provider.py` 的 `DashScopeProvider.complete()` 现有语义不变）；密钥不出现在日志与响应中。**2026-09-18 交付：** `provider.py`（+571 行）+ `test_ai_agent_provider.py`（54 用例，MockTransport，无网络无 DB）+ `config.py` 三项配置 + `.env.example` 模板；服务端 713 passed / 37 skipped（本卡基线 659 + 54）；ruff 全绿。**快路径零改动已核**——本卡 diff 对 `DashScopeProvider` 类体无任何删除行。**独立复审（2026-09-18，本卡收口）：** 逐条核对十条验收 + 专项查边界，**修 6 处**（分片 index 兜底、已产出 delta 后不可重试、畸形帧裸 KeyError 逃逸、空 `data:` 帧判死、空 name 双路径相反、空转的密钥 repr 用例），并对「缺 id」定型跨层契约（provider 如实透传、loop 补稳定 id）；6/6 处变异实测变红。详见 §五。**02d/02g 必读：** `ToolCall.arguments` 保真原始 JSON 文本、「快」档必须显式传 thinking `disabled`、**首个 delta 转发之后的流中断一律 `retryable=False`**（02g 必须报错收尾而非重发）、`ai_agent_provider_model` 目前**只声明未被消费**（接线时须读它，勿硬编码模型名）。详见 §五 | ✅ 完成（2026-09-18） |
| **T-M7-02c 工具注册表与域分组（P1 最小集）** | 02a | 新建 `server/src/modules/ai/tools.py`——工具定义（name / description / JSON Schema parameters / 读写标记 / 执行体）。**P1 只做够验证的域**：记账（`transaction.*`）、预算（`budget.*`）、账户账本（`account.*` / `book.*` 读）。description 为模型可见的唯一语义来源，**须人工审核**。按域分组注入（每域 5-8 个，见架构 §7.2） | 四条验收各固化为测试：action 枚举无冲突（读 `schema.json` 的**真实枚举**断言）；每域工具数 ≤8（实测 **5 / 5 / 5**）；未知工具名走降级契约（**不抛异常**，返回安全枚举错误码）；全量注入被明确排除（总数 **15** < 70）。**2026-09-18 交付：** `tools.py` + `test_ai_tools.py`（95 用例）；服务端 808 passed / 37 skipped（本卡基线 713 + 95）；四板全绿；契约零改动。**同卡修复既有缺陷：** `validator.py` 的 `format: date` 检查器对非字符串实例抛 `TypeError`（模型写 `20260918` 会让整轮 loop 500）——加类型守卫，工具路径与 `validate_proposal` 一并受益。**两轮独立复审**（均以「回退 → 测试是否失败」实测）共修 7 处缺陷、修 2 个空转测试，详见 §五。**设计边界（02d/02e 必读）：** 写工具只产出草案不执行；P1 写工具仅在记账域（预算/账户账本为只读）；读工具结果不含任何 id；**草案含 id，回喂模型前必须剔除**。**第三轮复审（2026-09-18 收口）：** ★ **工具名带点号会让 provider 直接 400**（DeepSeek 实测 `Invalid 'tools[0].function.name': string does not match pattern`）——15 个工具全部由 `domain.action` 改为下划线（`transaction_add` …），约束固化为 `PROVIDER_TOOL_NAME_PATTERN` + 兜底用例，**改名后用真实注册表载荷复测 200 且模型正确调用**；另修 5 处（归档账户口径、账本归属漏测、同名账本的 `ambiguous_book`、`transaction_recent` 的 description 口径、名字索引截断），5/5 处变异实测变红。详见 `docs/milestones/T-M7-02-P1-implementation-plan.md` §五 | ✅ 完成（2026-09-18） |
| **T-M7-02d Agent Loop 核心与推理分级（非流式）** | 02b、02c | 新建 `server/src/modules/ai/agent.py`——Turn/Step 分层（`while (await turn())`）；四条跨请求不变式（工具调用与结果配对 / 中途输入进正确的下一步 / 取消收敛到一致状态 / 历史可恢复可分叉）；退出条件硬边界（单 Turn ≤8 Step、≤5 写操作、≤20 工具调用、连续无进展 ≤2），任一触发即优雅终止并说明已完成部分；**入口推理分级**（规则表判定快 / 中 / 深三档，**不额外调用 LLM**）与 **loop 内按 Step 数升级**（进入第 2 个 Step 即升到「深」档——步数是复杂度的直接证据，使入口误判不再致命）。**先做非流式**（同步返回完整结果），以便复用现有同步 `TestClient` 基建单测 | 注入 fake provider 的确定性单测（`app.dependency_overrides[get_llm_provider]` 范式已存在）：四条不变式各有用例；四条硬边界各有用例；优雅终止输出含「已完成 / 未完成」两部分；三档判定规则各有用例；**Step 升级在入口误判时正确生效**（构造「入口判快、实际多步」的用例）；「快」档必须显式传 thinking `disabled`（该模型默认开启，省略参数不等于关闭） | ✅ 完成（2026-09-18，提交 `d923d4e`）。**交付：** `server/src/modules/ai/agent.py` + `server/tests/test_ai_agent.py`（79 用例）；服务端 **900 passed / 37 skipped**（基线 819 + 81，含 2 个 provider 回归）；四板全绿；契约零改动（`provider.py` / `tools.py` / `routes.py` / 客户端一行未动）。**真实调用揪出并修复一处整链级缺陷：** thinking 模式请求的历史里，assistant 消息缺 `reasoning_content` 键 → **整轮 400**——触发路径正是「入口判快（第 1 步 thinking 关闭）→ 第 2 步按 Step 升级升深」，即任何用到工具的记账/查询；根因是第 1 步本就产不出推理内容。修复为「assistant 消息始终带该键（无推理时空串，探针实测被接受）」，并把**真实 API 规则建模进 MockTransport**（回退实验：2/5 用例变红）。**02a 遗留补测（真实 API）：** 复杂推理下三档时间差约 **2.3×**、completion token 差 **3–4×**（简单任务的 <0.5 s 结论只对简单任务成立）；**带工具的那一步几乎不受档位影响**。**R-PROD-15 口径修正：** 任何用到工具的任务都是 ≥2 步且第 2 步恒为深档——成本评估不能按「快档 = 全轮便宜」估。**独立复审（0 P1 / 2 P2 / 9 P3）同轮修完：** 中途输入在「最后一步」或「撞边界那一步」到达时曾**被静默丢弃**（现：收口后再走一步接住；撞边界则由 `AgentResult.pending_input` 交回调用方——**02g 必须消费该字段**）；工具处理器异常原先会穿透 `run()`（现降级为配对的 `tool_failed` 错误结果）；另修 `provider.py` 两处（非流式缺 `ArithmeticError`、流式「增量已交出后 `retryable` 未改写」，后者由并发会话报出）。11 处新修逐项回退实测变红。**给 02f/02g 的硬约束：** 持久化/重建历史时 assistant 消息一律带 `reasoning_content`（空串可）。详见 `docs/milestones/T-M7-02-P1-implementation-plan.md` §五 |
| **T-M7-02e 写工具安全路径（ActionProposal + 审批四值）** | 02d | 写工具**不直接执行**——产出 ActionProposal 后 loop 挂起；复用 `validator.validate_proposal` + `context.load_validation_context`（纯函数，loop 内直调）；执行复用 `AIService.execute_proposal`（含 TOCTOU 重校验）；审批四值 `allowed-once`（唯一放行值）/ `rejected` / `cancelled` / `unavailable`（视为拒绝）。**审批策略（2026-09-18 定型）：** 单 Step 完成的任务沿用现有策略（confidence ≥0.9 自动执行 + 撤销窗，`service.py:563` 阈值）；**多步 loop（≥2 Step）中的写操作一律强制确认**——多步场景下模型连续写库的风险高于单发，收紧以保护 PR-2 | 模型输出 UUID 被拒（名→id 由确定性代码解析）；三条铁律各有用例；`unavailable` 视为拒绝；`allowed-once` 执行后即失效、不产生长期授权；多步场景下写操作必出确认卡。**2026-09-18 交付：** `agent.py`（扩展，+约 430 行）+ `test_ai_agent_approval.py`（26 用例）；服务端 **926 passed / 37 skipped**（02d 基线 900 + 26；全量 957 含并发会话 02f 在途的 31）；四板全绿；**契约零改动**（`validator.py` / `service.py` / `tools.py` / `provider.py` / `contracts/` / 客户端一行未改）。**口径两处定死（此前有歧义）：** ①「单 Step 完成」= **提案产出时处于第 1 个 Step**——按「回合总步数 ≥2」读会与架构该表自己的理由自相矛盾（写入在 Step 1 产出的回合，模型必然还要再走一步回话，故任何 agent 写入都是 ≥2 步，那条自动执行的行将永不可达）；②**「挂起」≠「视为拒绝」**——没接通道时挂起（交回调用方渲染确认卡），只有审批通道真的给出 `unavailable` 才视为拒绝。**安全形态：** 写路径是两个可选通道（`executor` / `approver`，缺省 `None`）——**默认形态下一次写库都不会发生**（与 02d 行为等价）；审批门一次性、无长期授权、通道任何异常或畸形输入一律 fail-closed；治理字段由服务端**无条件覆写**（模型无法自选幂等键或抬高置信度）。**回退实验 9/9 变红**，另在卡内自查中揪出并修掉三处（校验上下文只认一套映射 → 全部写入被判幻觉；`_GOVERNANCE_FIELDS` 过滤是空转，被回退实验证实为假防御后删除；中止后同批次剩余写调用仍会执行）。详见 `docs/milestones/T-M7-02-P1-implementation-plan.md` §五 | ✅ 完成（2026-09-18，提交 `1fdffd1`） |
| **T-M7-02f 会话状态与配额双限** | 02d | 新建最小会话事件表（`conversation_id` / `seq` / `type` / `data` / `created_at`），P3 演进为完整事件日志（补 `ignorable` 与 Surface 子集定义），P1 只做够多轮上下文的读写；激活 `AIConversation`（现为空表）作会话索引。配额：`AIUsage` 增 `conversation_id` 列，会话级「轮数 + token」双限按聚合查询实现。上下文窗口 **N 初始 8 轮**；历史与摘要只含自然语言，**不得出现 UUID / 内部 ID** | 多轮澄清用例（问 → 追问 → 简答「500」→ 正确解析）；会话级轮数 / token 超限返回 429（code 沿用 `ai_quota_exceeded`）；历史窗口无 UUID；登出 / 删号清理会话行。**2026-09-18 交付：** `models/ai.py`（`AIConversationEvent` 新建 + `AIUsage.conversation_id` + `AIConversation` 激活为会话索引）、迁移 `0017_conversation_events`、`modules/ai/conversation.py`（新建，`resolve` / `history` / `append_turn` / `record_turn` / `check_quota` / `purge_user`）、`config.py` 四项窗口与配额参数、`AuthService.logout` 与 `infra/cleanup/account-deletion.py` 的清理接线；`test_ai_conversation.py`（32 用例）。服务端 **961 passed / 37 skipped**（同树基线 930）；四板全绿；**契约零改动**（`contracts/` / `provider.py` / `tools.py` / `agent.py` / `routes.py` / 客户端一行未动）。**四条验收各固化为测试：** 多轮澄清用真实 loop 跑两轮、断言第二轮请求载荷里有第一轮的问句（「500」本身不含信息）；轮数 / token 双限返回 429 `ai_quota_exceeded`；历史窗口无 UUID（提案**含**服务端解析的 `categoryId`、窗口**必须不含**，两个断言一起钉住「回喂前剥 id」）；登出 / 删号后会话与事件均清零。**两条关键决策：** ①`ai_usage.conversation_id` 用 **`ON DELETE SET NULL`** 而非 CASCADE——会话行会被登出清理删除，级联会把 guest 的**日配额**计数基座一并删掉，等于「登出再登入可刷满配额」；事件表相反用 CASCADE。②**配额单位是 Turn 不是模型请求**——一个 Turn 一条用量行，轮数与 token 是同一张表上的 COUNT / SUM。**回退实验 17/17 变红**（含窗口截断、字符双限、归属校验、两类上限、清理接线、唯一索引、FK 策略）。**前置动作未执行（用户明确指示）：** `ai_daily_quota_guest` 保持联测临时的 `100000` 未恢复为 20——用户仍在使用该值做人工联测；测试面由 conftest 钉死不受影响，但**配额实测与 R-PROD-15 成本评估要用真实数值时须在 M7 收口恢复**。**02g 消费方式：** `resolve` → `history` → `check_quota`（在跑 loop 之前）→ loop → `append_turn` + `record_turn`。详见 `docs/milestones/T-M7-02-P1-implementation-plan.md` §五 | ✅ 完成（2026-09-18，提交 `17e7cac`） |
| **T-M7-02g SSE 端点与部署配置** | 02d、02e | `routes.py` 新增 `POST /ai/agent` 返回 `StreamingResponse`（`text/event-stream`）；事件类型按架构 §8.1（`phase` / `tool_call` / `tool_result` / `confirm` / `device_request` / `delta` / `done` / `error`）；**零新依赖**——Starlette 原生 `StreamingResponse` + 手写帧格式化（约 30 行），不引 `sse-starlette`；heartbeat（空闲期发注释帧，防 nginx 60 s 读超时）；**`db: AsyncSession` 生命周期**——流式响应下不能沿用 `Depends(get_db_session)`（请求作用域 session 在响应返回后即关闭，而生成器仍在运行），改用 `get_session_factory()` 自行管理。**SSE 缓冲（2026-09-18 决策：不改 nginx）**——服务端在响应中发 `X-Accel-Buffering: no`（nginx 默认尊重该头并对此响应关闭 `proxy_buffering`），**无需改动服务器 nginx 配置**；须实测确认该头生效，若被忽略再评估改配置。**测试基建：** 仓库无流式测试先例（现全部为同步 `TestClient`）——新建 `httpx.AsyncClient(ASGITransport(app))` 流式样板。**灰度开关：** 服务端 env + 客户端开关双层，默认关闭 | 流式样板可跑（帧格式、事件顺序、heartbeat）；开关关闭时 `/ai/agent` 不可用且旧客户端行为不变；实测确认 `X-Accel-Buffering: no` 经实际链路（含 nginx）后事件逐条下发而非攒批。**2026-09-18 交付：** `stream.py`（新建：帧格式化 / 观察者 / 帧泵生成器）+ `routes.py` 新增 `POST /ai/agent`（`X-Accel-Buffering: no` + `Cache-Control: no-cache`）+ `agent.py` 新增 `AgentObserver` 旁路（默认 `None`，02d/02e 用例一行未改）+ `service.py` 抽出模块级 `check_daily_quota` / `local_today`（行为等价）+ `config.py` 三项（`ai_agent_enabled` 默认 false / heartbeat 15 / 单步超时 20）+ `test_agent_stream.py`（48 例，仓库首个流式样板：httpx ASGITransport + 直连 ASGI 驱动器）。服务端 **1016 passed / 37 skipped**（同树基线 968 + 本卡 48）；ruff 全绿；**回退实验 18/18 全红**。**缓冲实测：**服务器 nginx 只读核对确认 `/aozora-api/` 无 `proxy_buffering` / `proxy_cache` / `proxy_ignore_headers`（1.16.1 ≥ 1.5.6 支持该头）→ 该头不会被忽略；仓内最强证据 = 模型请求还阻塞着时前两个 `phase` 帧已到客户端（直连 ASGI 用例）。**残余：**经真实 nginx 的端到端确认需部署后进行（本机无 docker/nginx），责任人 = M7 收口 / 部署执行者，R-PROD-22 该项保持开放。**远端 CI（`79c5145`，run 35337146185）：** Client ✅ / Server ✅ / Android **代码步骤全绿**（含此前从未真正跑起来的模拟器 connected 测试），**仅第 12 步 `:app:dependencyCheckAnalyze` 被作业 `timeout-minutes: 40` 截断**（OWASP NVD 库下载吃光预算，非代码回归；连续两次 run 停在同一处）。建议把该步骤移出主 CI 或给作业加时，责任人 = M7 收口 / CI 维护者。 | ✅ 完成（2026-09-18） |
| **T-M7-02g-2 token 级 delta（含推理增量）** | 02g | 把 `delta` 从「一轮一帧」升级为 **token 级增量**，并**同时送推理增量**（架构 §8.5）——`StreamChunk` 增 `reasoning_delta`（累加器已经在收推理内容，只是没往外给）；`agent.py` 的 `step()` 改走 `complete_with_tools_stream` 的 `async for`，`AgentObserver` 增 `delta` / `reasoning_delta` 两个钩子；`delta` 载荷增 `channel`（`content` / `reasoning`）与 `step`，推理通道供客户端渲染灰字折叠区；**帧泵加 100 ms 合并窗**（同通道 + 同 Step 并成一帧）；**收尾请求（`_wrap_up`）保持非流式**——它是机械交代，且中途失败会退回确定性兜底，流式会让客户端看到「半截 + 兜底」拼接。**时间口径同步改写**：单步超时由「总时长」改为「静默时长」（架构 §8.3），客户端侧表现为 OkHttp read timeout 而非 `callTimeout` | **真实探针闸门（已完成，2026-09-19）**：三档形态各一次真调用确认 (1) `stream_options.include_usage` 确实回 usage 尾块——02b 挂了一个多月的「未经真实链路验证，否则 agent 每轮计费恒为 0」到此闭环；(2) **推理增量先于正文增量且量大**（深档带工具那一步 0.45 s 起有推理、524 字符推理对 0 字符正文），只送正文的话最需要反馈的一步仍静默——这是本卡存在的理由；(3) 帧粒度约 4 字符/帧，一轮三四百帧，故必须合并。**用例面**：四个测试假 provider 全部实现流式方法（不实现即 `AttributeError`，新路径等于没测）；增量转发、`channel` 分支、合并窗（含「跨通道不合并」「无新数据按期冲刷」）、收尾文本只出现一次、**推理增量已交出后失败不可重试**各有用例。**职责边界：** 不改 §8.1 的八类事件（纯增量）、不改写路径、不做断连恢复 | ✅ 完成（2026-09-19） |
| **T-M7-02h 客户端桥通道（okhttp-sse + 事件转发）** | 02g-2 | `app/build.gradle.kts` 加 `com.squareup.okhttp3:okhttp-sse:4.12.0`；`AiGatewayClient` 新增流式接收（**注意：SSE 长连接不能用现有 `callTimeout(25s)`**，需独立 OkHttpClient 配置；按架构 §8.3 的新口径，用 **read timeout** 表达「静默 25 s」而非整步总时长）；`delta` 帧带 `channel`（`content` / `reasoning`），**桥层逐帧原样转发、不合并也不丢弃**（服务端已做过 100 ms 合并）；新桥命令 `ai.agent` / `ai.cancel`——**桥命令 77 → 79，四处同步**（`BridgeProtocol.kt` 的 const + `ALL`、`AozoraBridge.dispatch`、`bridge-types.ts`、`native-bridge.ts`），且 `SecurityConfigTest.kt:30-57` 的硬编码 `77` 断言同步更新；事件推送复用 `webView.post { evaluateJavascript }` + `window.__aozoraAiStream__`（照 `onPermissionResult` 的链式包装写法）；`activeStreamJob` 仿 `activeParseJob` 支持取消 | 桥命令计数断言 79；TS 命令完整性契约测试；SSE 事件从服务端到 WebView 端到端送达；取消能终止 loop；Token 不出 Kotlin 层（ADR-003 不变） | ✅ 完成（2026-09-19；桥命令 77→79 + 事件转发 + 取消，12 用例 + 5 回退实验；**端到端真机路径**与 nginx 一段移交批次 6 收口，详见 T-M7-02-P1-implementation-plan.md §五） |
| **T-M7-02i 客户端消费（最小形态）** | 02h | `use-assistant.ts` 接入流式：`submitInput` 走 `ai.agent`。**v2 intent 在流式中无法预知**（chat/query/edit/delete/config 的 UI 形态互异），定型为：流式期间只渲染文本气泡，`done` 到达后按既有 v2 分发矩阵（`use-assistant.ts:669-691`）走原有卡路径，用户感知为「思考完毕展开卡」；`AssistantPhase` / `CardStatus` / 三个互斥瞬时态（`activeProposal` / `pendingOp` / `pendingConfig`）约束不变；**02g-2 追加三条（写渲染前先落，之后改就是返工）：** `delta` 按 `channel` 分流——`reasoning` 进**灰字折叠区**、绝不混进正文；`error` 帧到达时**保留气泡里的半截文本并标注失败、不清空**（架构 §8.5）；正文气泡会收到**多帧** `delta`，按追加渲染，不要假设「一轮一帧」。保留非流式 `ai.interpret` 作为开关关闭时的降级路径 | 多步任务端到端可完成；多轮澄清可用；非 chat intent 下流式气泡正确卸载并展开卡；开关关闭时回落现有单发链路且 12 s / 25 s 体验不变；既有 assistant 测试全绿 | ✅ 完成 |

**执行顺序（串行闸门，不可跳）：**

```
02a 实测闸门 ──┬─→ 02b Provider ──┐
（go/no-go）   └─→ 02c 工具注册表 ─┴─→ 02d Loop 核心（非流式，可独立验收）
                                        ├─→ 02e 写工具安全路径 ─┐
                                        └─→ 02f 会话与配额     │
                                                              ├─→ 02g SSE 端点+部署
                                                              │
                                                              02d+02e ─→ 02g ─→ 02g-2 token 级 delta ─→ 02h 客户端桥 ─→ 02i 客户端消费
```

- **02a 必须先做**：结论决定 02b 的接口形态，也决定 loop 是否要退回文本 JSON 工具调用。未出结论前不动 02d 之后的卡。
- **02d 独立可验收**：非流式形态可复用现有同步测试基建，不依赖 02g 的流式样板。
- **02f 与 02e 并行**：都只依赖 02d，无相互依赖。
- **02g-2 必须排在 02h 之前**：它改的是 `delta` 的粒度与通道（架构 §8.5）。02h 还没建桥、02i 还没写气泡渲染，此刻改是零客户端返工；等 02i 把流式气泡写完再改，就是返工。
- **02h 依赖 02g 的事件协议冻结**：事件类型在此确定，客户端桥按协议实现。

**验证命令（全轮收口）：**

```
cd server && python -m pytest tests/ -q                    # 基线 713 passed + 37 skipped，本轮新增 agent 测试
cd app && ./gradlew :app:testDebugUnitTest                 # 基线 394 tests / 0 failures
cd app && npx vitest run                                   # 基线 1107 passed
cd app && npx tsc --noEmit                                 # 0 错
```

> 原记录为 658 / 392 / 1104，与实测不符（2026-09-18 由 02c 复核修正）。

**状态：** ✅ 完成（2026-09-19 批次 6 收口）。十张子卡 02a–02i 全部交付并各自收口；收口轮实测
**四板全绿**（服务端 1075 passed / 37 skipped、JVM 418 tests / 0 failures、vitest 1214 passed、tsc 0 错），
并在模拟器上跑通 **02h + 02i 的共同残余「端到端真机路径」三态**（多步任务 / 挂起提案 / 失败收尾）——
链路「服务端 → 桥 → WebView」至此有真实证据，灰度开关可以打开。**未覆盖**：真机（弱网 / 后台回收 /
真实 nginx，R-PROD-19 / R-PROD-22 残余不变）、T-M7-01 的换机 bootstrap 复认（guest 账号按
`device_id` 不复用，模拟器模式下不可达，留给真机 / 正式账号）、`ai_daily_quota_guest` 仍为 100000
（用户明确指示，继续用于人工联测）。收口发现的三个问题与详细证据见
`docs/milestones/T-M7-02-P1-implementation-plan.md` §五「收口（批次 6）」。

### T-M7-03 客户端瘦渲染与多会话管理（P2）

**依赖：** T-M7-02（客户端侧 02h 桥通道 / 02i 最小消费收口）。
**实施推进计划：** `docs/milestones/T-M7-03-P2-implementation-plan.md`——逐卡职责边界、批次与并行切分点、验证基线、状态看板、中间结果记录，以及承接自 P1 的六项挂账。**领取任一张子卡前必读**；本节的验收列是权威标准，该文件只细化推进方式、不改验收。
**范围（2026-09-18 定型）：** P1 已交付流式通道与最小消费（终态走既有 v2 intent 分发）；P2 做**观感与结构**——流式渲染打磨、步骤可见、多会话、统一智能模式。
**交接产物：** `use-assistant.ts` 拆分重构（把编排逻辑与渲染态分离。**注意：P1 起客户端本就不含 loop / 工具选择逻辑**——v2 是服务端 Host 方案，此处不存在「移除 loop」动作；改述为「拆为流式事件消费 + 卡渲染 + 设备能力执行三段」）；步骤卡片流（`phase` / `tool_call` / `tool_result` 事件可见化，可折叠，含完成 / 失败态）；统一智能模式（无模式开关，路径对用户透明）；多会话列表（新建 / 切换 / 重命名 / 删除）——`assistant-chat-store` 由单 `messages[]` 扩为按 `conversationId` 分桶，并降级为 UI 投影缓存；确认卡对接审批四值；取消 / 打断交互收敛。
**验证：** 步骤卡片流正确渲染（含完成/失败态）；简单任务无步骤展示（统一智能模式）；多会话互不串扰；取消收敛到一致状态；快路径 12 s / 25 s 体验回归不变。

**为什么拆成子任务（2026-09-19 复核）：** 原卡含六项交付、跨**服务端会话索引 / 客户端消费重构 / 三块互不重叠的 UI** 三个面，其中 `use-assistant.ts` 单文件 **1272 行**（本地 NLU + AI 单发 + agent 流式三路混在一个 hook 里），任一项卡住则整卡不可交付、无法独立验收。按 `docs/engineering/需求变更规范.md` §五.1 拆分。**注意 03a 是服务端卡**——多会话的权威面在服务端（架构 §6.1「客户端本地 + 服务端 `AIConversation` 摘要」、§6.3「服务端持有会话状态」），且 P1 的 02f 决策 7 把「会话归属维度」的复核责任明确交给了 P2（见实施推进计划 §五 第 1 项）。

**现状基线（2026-09-19 核对）：**
- **已有（P1 交付）：** 八类 SSE 事件协议 + 服务端会话索引表 `AIConversation` + 事件表 `AIConversationEvent`；客户端桥 `ai.agent` / `ai.cancel`（桥命令 **79** 条，四处一致）、`window.__aozoraAiStream__` 推送通道、`AiGatewayClient` 的 SSE 专用 client（read timeout 45 s）；最小消费（流式气泡 + reasoning 灰字折叠区，`AssistantPage.tsx:601-634`）；客户端灰度开关 `AI_AGENT_CLIENT_ENABLED`（`use-assistant.ts:171`，默认 false）。
- **空白（P2 从零建）：** 服务端**会话列表 / 重命名 / 删除 / 事件读取端点一个都没有**（`ConversationService` 只有 `resolve` / `history` / `append_turn` / `record_turn` / `check_quota` / `purge_user`）；`AIConversation` 只有 `id` / `user_id` / `book_id` / `summary` + 审计时间戳，**无可列表字段**；客户端**无任何多会话痕迹**（全仓无 `conversationList` / `sessionId` 类结构）——`assistant-chat-store` 仅 **70 行**、单条扁平 `messages[]`，`conversationId` 是 hook 内存 ref（`use-assistant.ts:357`，切页 remount 即丢）；**无步骤卡组件**；`ai.cancel` 在 TS 侧已封装但 hook 未调用。

**子任务拆解（2026-09-19 拆定）：**

| 子任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| **T-M7-03a 会话索引服务端化与事件读取 API** | 无 | `AIConversation` 扩为可列表索引（`title` / 活跃时间 / 软删）+ `ConversationService` 四个新方法 + `routes.py` 四个端点（`GET /ai/conversations`、`PATCH` / `DELETE /ai/conversations/{id}`、`GET /ai/conversations/{id}/events`）+ 迁移（紧接 `0018`）；**落地 P1 挂账「登出按用户粒度清会话」的结论** | 列表只返回本人且按活跃排序；重命名持久化、跨用户一律 404；删除幂等且事件行级联清理；`events(after_seq)` 增量读取；登出 / 删号行为与结论一致。**2026-09-19 交付：** `AIConversation` 补 `title` / `last_active_at` / `archived_at` + 列表复合索引（迁移 `0019_conversation_index`）；`ConversationService` 增 `list_conversations` / `rename` / `delete` / `events`（归属加载抽成 `load_owned`：别人的 / 已删的 / 不存在的 id 一律 404，不可分辨）；`routes.py` 四端点 + `schemas.py` 四个形状；`test_ai_conversations_api.py`（44 例）。**删除 = 归档行 + 删事件正文**：行留给 `ai_usage` 的审计链并与 P3 `04d` 的口径一致（保留期删事件、不删会话行），正文没有留的理由。**登出粒度结论：保持按用户清、不引入设备维度**——`/ai/agent` 不带 `device_id`，会话是用户级资产；代价是多设备下另一台机器的服务端会话会消失，客户端须把 404 当作「已不是我的会话」并开新会话（交 03b / 03d）。服务端 **1075 passed / 37 skipped**（基线 1029 + 46）；回退实验 **11/11 变红**。 | ✅ 完成（2026-09-19） |
| **T-M7-03b 客户端流事件消费重构与投影缓存** | 02h / 02i | `use-assistant.ts` 拆分（流式消费抽为独立 reducer/hook）；`assistant-chat-store` 按 `conversationId` 分桶 + **v1 持久化数据迁移**；`conversationId` 由内存 ref 提升到 store；`ai.cancel` 接线 | 八类事件 → 投影态逐条断言；`delta` 按 `channel` 分流；`error` 帧**保留半截文本**；**不假设「每条回复都从 `delta` 开始」**；会话切换互不串扰；取消后三态收敛且零写；既有 13 个 assistant 测试文件全绿。**2026-09-19 交付：** `1cc0fba`（收口 `0c99dbc`）。流式消费拆成两层——纯 reducer `applyStreamEvent`（**八类事件全消费**，投影含正文/推理增量、按 Step 归并的步骤视图三态 + 失败安全码、挂起提案、设备指令、收尾幂等）与 hook `useAssistantStream`（订阅 / `streamId` 归属 / 收尾幂等 / 取消，**不碰业务状态**）；`assistant-chat-store` 升 **v2**（按 `conversationId` 分桶 + v1 落盘数据迁移，一条不丢）；会话 id 由内存 ref 提升到 store；`ai.cancel` **接线**（`cancelStream`，取消后三个互斥瞬时态收敛为空且零写）。**收口（对抗式自审抓出两处本卡自己引入的缺陷）：** ①`start` 的兜底退订路径下上一轮迟到的桥层 reject 会收尾**新一轮** → 加 `roundRef` 轮次序号；②`adoptConversation` 会把用户**拽回**旧会话并删掉目标会话缓存 → 改为**只在本地会话刚拿到 id 时采纳**。app **80 files / 1171 passed**（开工基线 79 / 1135）；tsc 0 错；lint exit 0 且本卡文件零告警；JVM 406 tests；server 1075 passed / 37 skipped（未触碰）。**开放风险：** 端到端真机路径未跑（延续到 P2 收口）；「流式期间切换会话时一轮的消息落到切换后的桶」未根治，作为设计选择题交 03d——**03d 已用「流式期间禁止切换 / 新建 / 重命名 / 删除」收口**（根治要把一轮绑到发起时的会话）。 | ✅ 完成（2026-09-19） |
| **T-M7-03c 步骤卡片流与统一智能模式** | 03b | 可折叠步骤卡（进行中 / 完成 / 失败三态，`tool_result` 只显示摘要）；简单任务不展示步骤区；reasoning 折叠区打磨；`routing` 空态占位（生产者归 05g） | 三态渲染齐全（含失败态）；简单任务无步骤区；多帧 `delta` 追加渲染；空 / 加载 / 失败 / 离线四态回归。**2026-09-19 交付：** 新 `StepCardStream.tsx`（`98ac17f`）——三态齐全；本回合无工具调用时整体不渲染（架构 §9.1 统一智能模式）；仅 `phase:thinking` 的步不成行；折叠粒度是整条步骤流（单卡无可展开内容——`tool_result` 只有一行摘要）。**失败态显示本地映射的用户向文案**：服务端 `tool_result.message` 是回喂**模型**的指令（「请先查询分类清单」「请向用户说明这一情况」），读给用户是错的，故 03b 的投影只留安全枚举码，本卡按码另写 8 条用户向文案 + 兜底；完成态把进度句的「正在」换成「已」（服务端**不发**成功摘要，§8.1 只给「成了没有」）。`AssistantPage` 气泡内自上而下 = 步骤区 → 深度思考 → 正文，推理折叠区限高内滚并改「深度思考中…」。`routing` 空态占位就绪（05g 发帧即显示，无帧不多行）。**步骤不落历史**（收尾后投影整份释放是 03b 既定行为；回看需协议 + 存储改动，归 P3 的 04a/04e 评估）。app **81 files / 1188 passed**（开工基线 80 / 1171 → +1 文件 / +17 用例）；tsc 0 错；lint exit 0 且新文件零告警；服务端与 Kotlin 一行未动。 | ✅ 完成（2026-09-19） |
| **T-M7-03d 多会话列表与切换** | 03a、03b | 会话列表入口 + 新建 / 切换 / 重命名 / 删除，对接 03a 四端点；`book_id` 联动规则；离线 / 未登录降级；删除当前会话的回退目标 | 四个操作各有用例；两会话消息互不串扰；删除后回退到确定性目标；离线降级不报错。**2026-09-19 交付：** `a39293d`。**前置发现：桥里没有一条通道能到 03a 的端点**（无 `conversation.*` 命令、无通用 HTTP 命令、`executeAuthorized` 只支持 POST），故先扩桥再在其上做 UI：三命令（桥计数 79→82）+ `AiError.NotFound` 与两处 404 映射（同步路径 + SSE 路径）+ GET/PATCH/DELETE 支持 + 204 空响应体造 `{success:true}`（空串会让信封序列化直接抛）。**第四条 `events` 有意不加**——P2 不做会话正文跨设备同步（归 04e），无人调用的命令只扩大 APK 契约面。客户端：`use-conversations.ts`（拉取 / 降级 / 确定性回退）+ `ConversationList.tsx`（行内重命名、行内删除确认）+ store 的 `forgetConversation` / `removeConversation`。**两条规则定案：** `book_id` **只记录不驱动**（切换会话不改变当前记账账本，避免「切个会话改了全局默认账本」的副作用）；删除后回退**列表里剩下的第一条**（服务端活跃倒序 → 最近活跃的另一条），一条不剩 → 本地会话桶。**清理 03b 挂账「陈旧 `conversation_id` 的 404 降级」**：服务端在开流前回 404 → 桥译成 `not_found` 帧 → `forgetConversation()` 丢 id、**当前这一屏消息原地保留** → 下一轮不带 id 开新会话。**流式期间禁止切换 / 新建 / 重命名 / 删除**（03b 收口记录留下的设计选择题；根治要把一轮绑到发起会话，属后续卡）。降级（离线 / 未登录 / 旧 APK）→ 只读本机投影缓存 + 禁用新建，允许切换，**不报错**。app **82 files / 1206 passed**（+1 文件 / +18 用例，含 hook 级 404 降级 2 条）；tsc 0 错；lint exit 0 且新文件零告警；JVM **418 tests / 0 failures / 1 skipped**（+12）；server **1075 passed / 37 skipped**（未触碰）。回退实验 **5/5 变红**。**2026-09-19 收口（`998d495`）：** 「＋新会话」此前走 `switchConversation(null)`，在活动桶已是本地槽时是**空操作**（本地槽一旦有内容就再也回不到空白会话），在缓存里压着本地槽时会把旧内容顶上来——两种都不是「新建」。收口给出第三条路：让位的本地会话**铸成 `local:<uuid>` 留档**（零删除）并**进在线列表**（不列就再也切不回去），`newConversation()` 成为「新会话」的唯一入口；`runAgent` 在请求组装处丢掉本地键（`/ai/agent` 的 `conversation_id` 只认服务端 id）。app **1228 passed**（+8 用例）；回退实验 **4/4 变红**。 | ✅ 完成（2026-09-19） |
| **T-M7-03e 确认卡四值与取消 / 打断收敛** | 03b | 确认卡对接审批四值（`unavailable` **视为拒绝**）；`confirm` 事件与卡链路改为**事件驱动**；取消按钮与打断交互收敛（架构 §4.4 不变式 3）；中断状态提示（R-PROD-19）；语音打断语义同取消 | 四值 UI 表现各有用例；`unavailable` 零写；取消后无悬挂确认卡；断连提示 + 重连入口；`confirmation-card.test.tsx`（544 行）全绿。**2026-09-19 交付：** `d8dc961`。**前置发现：审批四值在服务端已实现、客户端却无处落地**——`stream.py` 有意不接 `approver` 通道（02g 决策 2：桥没有上行审批），所以四值的真实含义是**用户对那张卡的处置**。**事件驱动的两半**：① `confirm` 帧到达即出卡（`confirmProposals` 按 proposalId 去重累积 + 流 hook 实时转发，用户提前看见 agent 想做什么）；② **可执行由权威清单把关**——`done.proposals` 仍是权威，收尾时核对队列（中途来的 / 清单没有的 → `unavailable` 视为拒绝零写，延续 02i 决策 ③；清单有而队列缺的 → 补进队列），**动作只在收尾后启用**（UI 禁用 + `editProposal` 第二道防线）。四值落点：`allowed-once`=卡上确认（执行后即失效）、`rejected`=卡上「拒绝」（零写、任务不中止）、`cancelled`=「停止生成」/语音打断（零写、任务中止、不留悬挂确认卡）、`unavailable`=失败/断连时仍未回答（视为拒绝、零写）。多提案**逐张处置**（P1 只取 `[0]`，第二张永远没入口）；确认成功顶上下一张、失败不顶（`commitProposalInternal` 改为返回是否写入）。**中断态**（R-PROD-19）：`interrupted` 瞬时态 + 提示条 +「重发」（P2 的手段是原样重发；按 seq 续跑归 P3 的 04b），**与取消分成两种说法**（主动 vs 被动）。**取消的服务端可观测形态定案**：保持「断连即取消」，`StopReason.CANCELLED` 仍不可达（入档 `ai-agent-architecture.md` §8.3）。**未做**：服务端一行未动、桥无新命令、无新事件类型、不做续跑。**收口（`02a3d00`）：** 对抗式自审抓出一处真实缺陷——多提案时第二张卡顶上**缺消息**（写入照成、用户却看不到回执、历史卡也没有它），修法是把「建消息 / 用收尾正文替换兜底文案」统一收进 `showPendingApproval`。app **82 files / 1237 passed**（+23，另含 03d 收口并发 +8）；tsc 0 错；lint exit 0 且既有告警集合不变；JVM 418 / 0 failures / 1 skipped；server 1075 passed / 37 skipped（均未触碰）。回退实验 **6/6 变红**（含一处假绿当场补强用例后复验），收口修复点另行变异复核。 | ✅ 完成（2026-09-19） |
| **收口** | 03c–03e | P2 四板 + 真机观感走查 + 快路径 12 s / 25 s 回归 | 出口标准逐条核对（本节验收列）。**2026-09-19 交付：** 四板全绿（vitest **1237** / tsc **0 错** / JVM **418 tests / 0 failures / 1 skipped** + `compileDebugAndroidTestKotlin` 执行 / pytest **1113 passed / 37 skipped**——安静环境，并发态曾现 43 failed 已定性为资源争抢）。**端到端真机路径首次跑通**（02h/03b/03c/03d/03e 的共同缺口）：Pixel_7_API_36 + 本机 dev 服务端 + 真实 DeepSeek，临时置开关 `true` 重建 APK 走查后**已还原**。走查覆盖：步骤卡三态与推理通道分离；**多步强制确认真实生效**（写提案落 step 2 → `confirm` 帧）；卡「可见但未启用」（收尾后可点）；**四值真机落点**（确认→写入落库 / 拒绝→零写 / 取消→无悬挂卡+零写）；**统一智能模式**（无 tool_call 回合全程无步骤区）；**快路径 2799 ms 无慢速提示**；多会话新建 / 切换 / 重命名持久化 / 删除后确定性回退；**03d 收口的本地会话路径**（离线记 → 联网 → 「本机 · 未同步」行 → 切回）。**合并窗复核闭环**：17 帧、delta 间隔 p50 **105 ms**，与 100 ms 窗吻合，两个初值维持。**未做**：失败态步骤卡真机触发（模型调用前自查 + 本地 NLU 拦截，三轮构造未成，单测覆盖）、`unavailable` 真机（归 04b）。**观察**：带账户/日期的记账句被本地 NLU 吃掉（既有行为）、回复偶带英文 preamble、「多步强制确认」在并行形态下可被绕过（02e 口径固有代价，与 T-M7-02 批次 6 的发现 ② 一致）。 | ✅ 完成（2026-09-19） |

**执行顺序：** 批次 1（03a ∥ 03b，服务端 / 客户端两面零文件重叠）→ 批次 2（03c，独占以免三卡同改 `AssistantPage.tsx` 同一段）→ 批次 3（03d ∥ 03e，UI 区域不同）→ 批次 4（收口）。

**状态：** ✅ 完成（2026-09-19）——03a–03e 五张子卡与批次 4 收口全部交付；端到端真机路径首次跑通

### T-M7-04 会话事件日志与摘要压缩（P3）

**依赖：** T-M7-03。
**实施推进计划：** `docs/milestones/T-M7-04-P3-implementation-plan.md`——逐卡职责边界、批次与并行切分点、验证基线、状态看板、中间结果记录，以及承接自 P1 / P2 的十项挂账。**领取任一张子卡前必读**；本节的验收列是权威标准，该文件只细化推进方式、不改验收。
**交接产物：** SessionEvent 日志（`type`/`seq`/`time`/`data`/`ignorable`）；Surface 事件子集定义（只有其进模型上下文）；`replace` 压缩操作（原文保留）；摘要生成与注入；会话恢复与分叉；日志保留期与隐私边界（对齐 `D-PROD-08`）。
**验证：** 断连后可按 `conversation_id` 恢复续跑；压缩后原文仍可查（审计）；结构性事件不占 token（实测）；摘要不含 ID；隐私边界有明确结论。
**前置阻塞：已解除（2026-09-19 修正）。** 原登记「`docs/architecture/security.md` 第 6 行绝对禁令须先解决方可开工」——该条款**已于 2026-09-18 修订**（`security.md:7` 与 `:9` 的修订记录：「完整对话」由绝对禁令改为**有条件例外**，三条约束为保留期对齐 `D-PROD-08`、登出 / 删号联动清理、读取面带归属校验；token / 原始票据 / 账本明细三项仍为绝对禁令）。其中两条已由 02f 落地，**残余的保留期机制即 04d 的交付内容**。

**为什么拆成子任务（2026-09-19 复核）：** 原卡含六项交付、跨**数据层（事件结构）/ 写路径（落库粒度）/ 读路径（压缩与摘要）/ 生命周期（保留期）/ 决策（跨设备）**五个面，且每一项都改同一张事件表与 `conversation.py` ——不拆则任一项卡住整卡不可交付，也无法独立验收。按 `docs/engineering/需求变更规范.md` §五.1 拆分。

**现状基线（2026-09-19 核对）：**
- **已有（P1 交付）：** `AIConversationEvent`（`seq` / `turn` / `type` / `data` / `created_at`，唯一索引 `(conversation_id, seq)`）；`ConversationService` 的 `history` / `append_turn` / `record_turn` / `check_quota`（**一轮原子写入**）；`AIConversation.summary` 字段存在但**保持空串**（P1 预留）。
- **空白（P3 从零建）：** `ignorable` 与显式 `time` **不存在**；`type` 只有 `user` / `assistant` / `tool` 三个值（全集需扩到九类）；**Surface 子集完全未定义**——P1 的事件就是 loop 的消息流，**全部**进上下文（R-PROD-15 的残余）；**无压缩、无摘要、无 `replace` 事件、无恢复、无分叉、无保留期**。

**子任务拆解（2026-09-19 拆定）：**

| 子任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| **T-M7-04a 事件日志完整化与 Surface 子集** | 无 | 事件表补 `time` / `ignorable`；`type` 扩全集（**先核对 `String(16)` 是否放得下 `approval_request`**）；Surface 子集定义落为单一来源常量；`history()` 改为只投 Surface 子集；配额与安全事件入日志 | **实测「结构性事件不占 token」**（不接受设计推论）；Surface 子集逐类用例；事件类型全集可写可读（含长度边界）；非 Surface 事件不进 `messages`；投影 B 读取仍返回全部事件。**2026-09-19 交付：** `models/ai.py`（补 `time` / `ignorable`；`type` 加宽 `varchar(16)`→`varchar(32)`）、迁移 `0020_conversation_event_log`、`conversation.py`（`SURFACE_EVENT_TYPES` / `STRUCTURAL_EVENT_TYPES` 分档常量、`history()` 白名单**下推到 SQL**、新 `append_events()` 结构性事件写入口、`record_turn` 写 `quota` 用量事件）、`stream.py`（成功路径 `same_turn=True`，1 行）；`test_ai_conversation_log.py`（38 例）。**实测「结构性事件不占 token」：** 同一轮带 / 不带结构性事件，第二轮 loop **实际发出的请求载荷逐字节相同**（token 是载荷的函数）；对照组（04a 之前的「全部进窗口」形态）多 160 字符。**列宽实测（PG 17.11）：** `varchar(16)` 按字符数计，全集最长的 `approval_request` 恰 16 字符——顶格零余量，17 字符即被拒，故加倍。**结构性事件全集 7 类**（审批请求 / 审批结果 / 设备指令 / 错误 / 摘要替换 / 配额 / 安全），其中**只有 `quota` 有真实生产者**（`record_turn`，与消息同轮）；其余四类的生产者各有归属（审批与错误 → 04b，摘要替换 → 04c，设备指令 → P4），**`security` 的落点与「读取面访问审计结论」是同一个问题，归 04d**。**不改 API 响应契约**（`schemas.py` 未动，新列不外溢，02h 冻结的客户端桥零影响）；**不改下行 SSE 八类**（两套独立枚举）。四板：pytest 1113 passed / 37 skipped、vitest 1237、tsc 0、JVM SUCCESSFUL；迁移在 dev 库已 upgrade 到 0020，downgrade 语句在真实 PG 事务内执行通过并完全回滚。 | ✅ 完成（2026-09-19，提交 `e9203b0`） |
| **T-M7-04b 会话恢复与分叉（含轮中途落库）** | 04a | 写入粒度由「一轮原子」改为**逐事件**；按 `conversation_id` + `seq` 的恢复入口；客户端断连重连续跑；会话分叉（不复制消息数组、原会话不变）；`StopReason.CANCELLED` 可达性复审 | 断连后按 `seq` 续跑（服务端 + 客户端用例）；**轮中途落库后断连仍有完整历史**；分叉会话独立续跑；**不变式 1（工具调用与结果配对）不因半截落库被破坏**。**2026-09-19 交付：** `agent.py`（`MessageSink` 通道 + `MessageStamp` 逐事件时间）、`conversation.py`（`TurnWriter` 逐批提交 / `max_seq` / `commit` / `fork` / `_paired_prefix_len` / `history()` 尾部保证干净）、`stream.py`（注入 sink、去掉整批写入、`_record_failure` 接 `error` 事件、`done` 带 `seq`）、`routes.py`+`schemas.py`（`POST /ai/conversations/{id}/fork`）、客户端桥新增 `conversation.events`（**计数 82→83**，四处同步）+ `use-assistant-stream.ts` 的续读游标与 `recoveredMessages`。**批的边界＝配对组的边界**（带 `tool_calls` 的 assistant 与它的全部结果同批同提交），「半截落库」因此在结构上不可能。**`CANCELLED` 复审：分两级**——loop 层可达（显式 cancel / 审批 cancelled），生产入口不可达且刻意如此（断连即取消任务，Step 入口检查点不会被走到），结论入档架构 §8.3。**明确未做**：压缩（04c）、保留期（04d）、审批语义与写路径安全门一行未动、**不接 `approval_request`/`approval_result` 生产者**（理由与去向见 P3 计划 §五 第 11 项）。P1 挂账第 2/3/6/10 项随之关闭。四板：pytest 1172 passed / 37 skipped（本卡 +22 例）、vitest 1249、tsc 0、JVM 423 tests SUCCESSFUL、androidTest 源码集显式编译通过；9 处变异反向验证全部命中。 | ✅ 完成（2026-09-19，提交 `c44b723`） |
| **T-M7-04c 摘要压缩与预算（`replace`）** | 04a、04b | 压缩触发条件决策（轮数 vs token vs 时间）；摘要生成（**不含 ID**、≤800 字符）；`replace` 事件（**原文保留在日志**）；摘要注入 `AIConversation.summary`；Token 预算落地；**失败步 token 估算的最终结论** | 压缩后**原文仍可查**；摘要不含 ID；压缩前后「结构性事件不占 token」结论一致；摘要失败回落上一版且不阻塞本轮；多轮澄清用例在压缩后仍正确。**2026-09-19 交付：** 触发条件＝**窗口溢出**（不另立 token / 时间尺子，架构 §12.2 已定表第 10 项）；`conversation.py` 的 `summarize_overflow`（`replace` 事件 + `summary` 列，**原文一行不删**）、`prompts.py` 的 `CONVERSATION_SUMMARY` 模板、`agent.py` 的 `run(summary=…)`（摘要经**系统提示**注入，不进 messages 数组）、`stream.py` 在 `done` **之后**调用（不占用户等待；此后不再发帧，异常全吞）、`config.py` 的 `ai_agent_summary_chars=800`、`retention.py` 的 `purge_expired_summaries`（正文被删则摘要必须一起消失——否则保留期被从后门绕过）+ `fork` 按**自己的前缀**派生摘要 + `delete` 清摘要。**摘要校验两条**：≤800 字符、不含 UUID；**任一不过整条退回**（沿用上一版，不截断不修补——半句摘要会被下一轮当成事实）。**失败步 token 的最终结论：维持不修。** 04b 起已完成步骤的 token 由 `observer` 累计记账（`_record_failure`），缺口只剩「失败那一步」，而它的 usage 在 provider 尾块、流断即零；估算值混进 `ai_usage` 会污染配额基座（那是「轮数 + token」双限的计数表），两条用例钉死现状（`test_failure_after_a_tool_step_records_that_step_usage` / `test_interrupted_step_usage_is_not_estimated`）。**窗口 N=8 / 字符 8000 复核：维持**（实测：简单问答轮约 149 字符、工具轮约 596 字符，8 轮分别约 1.2k / 4.8k，都在 8000 以内；字符限真正兜底的是「单轮工具结果特别大」的会话）。**明确未做**：不改 Surface 子集（04a）、不改保留期既有口径（04d）、不改写路径安全门（02e）、不做跨设备同步（04e）、**API 响应契约与客户端零改动**、**不需要迁移**。四板：pytest **1212 passed / 37 skipped**（干净 worktree 实测；HEAD 基线 1175 → **+37 例、零减少**）、vitest 1266 passed、tsc 0 错、JVM 424 tests / 0 failures；**真实 PG 取证**（保留期 × 摘要的联动，事务内 rollback 零副作用）；**10 处变异反向验证全部命中**（3 处首轮未命中暴露的是测试覆盖缺口，已补断言 / 用例）。 | ✅ 完成（2026-09-19，提交 `6026646`） |
| **T-M7-04d 保留期与隐私边界落地** | 04a | 保留期机制（对齐 `D-PROD-08`），复用 `telemetry/retention.py` 的既有范式；读取面访问审计的结论；四个新端点的归属校验复审 | 到期事件按策略删除且**不破坏 `AIUsage` 配额基座**（02f 的 `SET NULL` 口径）；长期不登出用户的日志不再无限留存；`security.md` 三条约束逐条有实现或结论 | ✅ 完成（2026-09-19，提交 `06dedfc`） |
| **T-M7-04e 跨设备会话同步（决策门）** | 04a | **先出决策**（仅摘要 / 正文 / 不同步，落 ADR 或架构 §6.1 修订，经账户所有者确认）再按结论落地；结论为「不同步」时本卡退化为文档更新 + 行为说明 | 决策落文档并经确认；按结论给出对应验证；不同步结论下须写明「换设备后旧会话如何呈现」 | ✅ 完成（2026-09-19）。**决策＝正文按需重建、不做双向同步实体**（`ADR-008` / `D-PROD-14`，经账户所有者确认）——会话正文的权威在服务端事件日志，客户端投影是**派生数据**，故**从不写回**，冲突策略这一议题因此不存在；「仅摘要同步」被否决（`AIConversation.summary` **至今无生产者**，且只覆盖超窗轮次，本就不回答「打开旧会话看见什么」）。落地＝**纯客户端 + 文档**：切到本机无投影的服务端会话 → 调**已有的** `conversation.events`（04b 交付，本卡只消费）拉日志重建；有投影且**日志游标**已知 → 增量补齐尾部（本地消息一条不动，卡片与中断标注不被抹掉）；游标未知 → 原样保留。游标落 `assistant-chat-store` v3（本地事实，随投影一起清），写入点＝正常收尾 / 成功拉取 / 失败收尾，**只前进**；**取消**把游标抹成未知（服务端断连取消不写任何日志事件）。**零新协议、零新端点、零服务端改动**，Kotlin 一行未动。空态文案按「正在载入 / 拉不到 / 还没有消息 / 当前版本不支持」四分，删掉「跨设备同步将在后续版本支持」。app **83 files / 1278 passed**（+1 文件 / +29 用例）；tsc 0 错；oxlint exit 0 且新文件零告警。残余见 `ADR-008` 与 R-PROD-30 |
| **收口** | 04b–04e | P3 四板 + **真实断连恢复走查**（流式中杀进程 / 切网 / 切后台后再进入） | 出口标准逐条核对（本节验收列）。**2026-09-19 交付：** 四板全绿（vitest **83 files / 1278 passed** / tsc **0 错** / JVM **424 tests / 0 failures / 1 skipped**（`--rerun-tasks`）/ pytest **1212 passed / 37 skipped**——安静环境，避让 P2 登记过的并发假失败）。**真实断连走查跑通**（Pixel_7_API_36 + 本机 dev 服务端 `AI_AGENT_ENABLED=true` + 真实 DeepSeek；临时置客户端开关 `true` 重建 APK 走查后**已还原并重建**）：①**流式中杀进程 → 轮中途落库成立**——`am force-stop` 后服务端日志只剩该轮的 `user` 事件（`seq 12`），P1 的「一轮原子写入」在这里会是零；同会话另有 6 条事件逐条 `time` 互不相同的完整取证（04a 的结构性事件分档与 04b 的逐条时间戳在真实 PG 上同时可见）。②**连接中断 → 客户端收敛到「⚠️ 连接中断，本轮未完成」+「重发」入口**（无悬挂确认卡、零写），即验收列的「切网」面。③**跨设备正文重建（04e 唯一没在真机验过的面）通过**——清空 `localStorage` 投影与游标后打开服务端会话，正文从日志完整重建（8 轮问答逐条在场），**并把此前本地丢失的那一轮答案补了回来**。④**分叉切点按配对组边界取整**（请求 3→切点 1、4→4、8→7、9→9、11/12 原样；原会话 27 条事件一行未动）。**出口标准逐条核对通过**（断连续跑 / 压缩后原文可查 / 结构性事件不占 token / 摘要不含 ID / 隐私边界结论）。**未做**：流式中切后台的真机触发（本轮次 1.5–3.5 s 写完整答案，窗口太窄，四次构造只命中一次；收敛面与「连接中断」同路径故以之覆盖）、生产 compose 的保留期开关复验（04d 已有真实 PG 取证）、客户端开关默认值（保持 `false`）、`ai_daily_quota_guest` 恢复 20（02f 登记为 M7 收口执行）。**观察两条（登记风险册）**：R-PROD-32 一轮完整答案后本地投影未落盘（**一次，未复现**，随后 4 次重复均正常；服务端日志完好、04e 重建可恢复）；R-PROD-33 重建出的「被中断轮」无中断标注。**方法学留档**：`adb reverse` 的增删只影响新连接（断不掉进行中的流，需杀进程或设备侧 iptables）；`adb root` / app 重启后 `forward` 失效需按新 PID 重设。本收口**未改任何项目代码**；**远端 CI 未能确认**——三个 job 均被 GitHub Actions 的账户计费层拒绝启动（5–6 秒即失败、一行测试未跑，重跑同样），属环境侧故障，详见 P3 计划 §六 的「交付」段。 | ✅ 完成（2026-09-19） |

**执行顺序：** 批次 1（04a，独占——事件结构不定则恢复 / 压缩 / 保留期全部返工）→ 批次 2（04b ∥ 04d）→ 批次 3（04c ∥ 04e）→ 批次 4（收口）。

**状态：** ✅ 完成（2026-09-19）——04a–04e 五张子卡与批次 4 收口全部交付；真实断连恢复走查跑通（流式中杀进程 / 连接中断 / 跨设备重建 / 分叉切点）

### T-M7-05 全功能覆盖：域路由、Skill 与设备能力通道（P4）

**依赖：** T-M7-04。
**实施推进计划：** `docs/milestones/T-M7-05-P4-implementation-plan.md`——逐卡职责边界、批次与并行切分点、验证基线、状态看板、中间结果记录，以及**跨期提前开工的切分点**与承接自上游的九项挂账。**领取任一张子卡前必读**；本节的验收列是权威标准，该文件只细化推进方式、不改验收。
**交接产物：** 预路由（域选择，方案对比后定）；Skill 封装（复用 `PromptTemplate` 机制，按需加载）；设备能力通道（单向指令 / 需结果两类，架构 §4.3）；`ActionProposal` v2 覆盖全量 CRUD（先改 `docs/architecture/ai-architecture.md` + `contracts/action-proposal/schema.json` + 前后端校验器 + fixtures）；帮助文档检索（可选）。
**验证：** 功能覆盖清单（架构 §3.2）逐项可用；设备类能力（提醒/权限/分享/小组件/语音/图片/图标）对话内可达；"新增一个表单能力不改 prompt 主体"验证用例；ActionProposal v2 三方一致性（schema / TS 校验器 / Python 校验器）通过；v1 契约与旧客户端兼容；域路由准确率实测记录（R-PROD-17）。

**为什么拆成子任务（2026-09-19 复核）：** 原卡跨**五个面**——契约（跨 schema / TS / Python 三方一致）、**服务端工具全集**（P1 只交付 15 个；全量对应当前 **79 条桥命令**的表单任务，余约 55–60 个）、**设备能力通道**（跨服务端 + Kotlin + TS 三层，且 `device_request` 至今**无生产者**）、域路由、Skill 封装。任一项卡住则整卡不可交付、无法独立验收。按 `docs/engineering/需求变更规范.md` §五.1 拆分。

**现状基线（2026-09-19 核对）：**
- **已有（P1 交付）：** 工具注册表（`Tool` dataclass：`name` / `domain` / `kind` / `description` / `parameters` / `handler` / `proposal_action`）+ **15 个工具 / 3 域**（记账 5 / 预算 5 / 账户账本 5，预算与账本域**只读**）+ `tools_for_domains(domains)` 的按域过滤注入；`PromptTemplate` 机制（`prompts.py`，428 行）；设备类桥命令**已全部就位**（权限 / 语音 / 图片 / 图标 / 主题背景 / 分享 / 触感 / 提醒 / 小组件，含 9 个 `window.__aozora*__` 推送通道）。
- **空白（P4 从零建）：** **`device_request` 无生产者**（八类 SSE 事件里唯一没有发射路径的一类，`stream.py:34,72` 明写）；**`routing` 阶段无生产者**（`stream.py:30` 注「属域路由，P4 才有」）；**无域路由**；**无 Skill 封装**；ActionProposal 仍是 v1 最小 action 集（`ai-architecture.md` 仅 30 行的薄指针文档，schema 16 个 fixture）。

**子任务拆解（2026-09-19 拆定）：**

| 子任务 | 硬依赖 | 交接产物 | 最低验证 | 状态 |
|---|---|---|---|---|
| **T-M7-05a ActionProposal v2 契约（文档/协议先行）** | 无 | `ai-architecture.md` 实质扩写 + `contracts/action-proposal/schema.json` v2 + TS 校验器（`domain/action-proposal.ts`）+ Python 校验器（`validator.py`）+ v2 fixtures + parity 测试；**v1 兼容**（版本协商 / 双读） | **三方一致性**（同一 fixture 三处结论一致）；**v1 全部 fixture 仍通过**；v2 枚举与 `proposal_action` 无冲突；旧客户端行为不变 | ⏳ 未开始 |
| **T-M7-05b 全量工具：记账与账户账本域** | 05a | 记账（`transaction.*` / `category.*`）与账户账本（`account.*` / `book.*` / `meta_set(默认项)`）扩到**全量 CRUD**，写工具产出 v2 提案 | 每域工具数 ≤8；工具名一律下划线（点号会让 provider 400）；写工具一律走审批门；读工具结果**不含 id**、草案含 id 但回喂前剔除；**真实调用抽样正确率记录** | ⏳ 未开始 |
| **T-M7-05c 全量工具：预算存钱、定时、角色域** | 05b | `budget.*` / `savings.*` / `scheduled.*` / `persona.*` / `meta_set(当前角色)`（后两类依赖 T-M7-01 已上云的三实体） | 三域工具数各 ≤8；写操作经 v2 提案与审批门；离线语义有明确结论；真实调用抽样记录 | ⏳ 未开始 |
| **T-M7-05d 全量工具：分析与其余服务端域** | 05c | 分析（`/stats/*` / `/ai/report` 封装）、导出 / 反馈 / 隐私同意、安全账号（`auth.*` / 删号）、同步（`sync.getStatus` / `trigger`） | 架构 §3.2 中本卡各行逐项可达；**删号类工具强制确认**且不绕过既有账号删除流程；**对账不变量全绿** | ⏳ 未开始 |
| **T-M7-05e 设备能力通道：服务端协议与 loop 暂停/恢复** | 无（仅需 P1 的 SSE） | `device_request` 的**首个生产者**；单向指令（不回传）与需结果（HTTP 上行回传 + loop 继续）两类协议；暂停态存储与超时 / 取消；`AgentObserver` 扩展 | 单向指令发出即继续；需结果类可被回传唤醒；**回传超时收敛到一致状态**（不变式 3）；取消后无悬挂设备请求；**顺手修正 `stream.py:34,72` 把设备通道写成「归 P2」的笔误** | ⏳ 未开始 |
| **T-M7-05f 设备能力通道：客户端执行器与设备工具接线** | 05e | 把已就位的设备类桥命令族接成设备工具执行器；新增 `device.execute` / `device.result` 类命令——**命令数 79 → N，四处 + `SecurityConfigTest.kt:47-57` 断言同步**；需结果类上行回传 | 命令计数四处一致；TS 命令完整性契约测试；设备类工具**端到端可达**；回传后 loop 继续；权限被拒 / 设备不可用的降级有结论 | ⏳ 未开始 |
| **T-M7-05g 域路由与预路由** | 05d | 域选择方案对比后定 + 实现；`phase: routing` 生产者接通（对接 03c 的占位）；`tools_for_domains` 接线 | 误路由率 / 漏注入率**实测记录**（R-PROD-17）；路由失败兜底策略有用例；**不得与入口推理分级混为一谈**（架构 §12.2 易混淆点） | ⏳ 未开始 |
| **T-M7-05h Skill 封装与按需加载** | 05b–05d | 复用 `PromptTemplate` 封装 Skill、按需加载；与工具域的配合；加载条件与预算 | **「新增一个表单能力不改 prompt 主体」验证用例**（P4 出口标准）；Skill 不常驻上下文；未命中时行为与 05b–05d 逐字一致 | ⏳ 未开始 |
| **T-M7-05i 帮助文档检索（可选，可砍）** | 05d | 仅产品帮助 / 理财知识的最小检索 | 若 P4 出口不纳入则**直接关闭并在文档标注「不做」**，不得默默留着造成范围含糊 | ⏳ 未开始 |
| **收口** | 05b–05i | P4 四板 + **功能覆盖清单（架构 §3.2）逐项走查** + v2 三方一致性与 v1 兼容复核 | 出口标准逐条核对（本节验收列） | ⏳ 未开始 |

**执行顺序：** 批次 1（05a）→ 批次 2（05b ∥ 05e）→ 批次 3（05c ∥ 05f）→ 批次 4（05d ∥ 05h）→ 批次 5（05g ∥ 05i）→ 批次 6（收口）。
**跨期提前开工（唯一的安全切分点）：** **05a 与 05e 的协议部分不依赖 P2 / P3**——契约是纯文档 / 协议工作、设备通道协议只依赖 P1 已交付的 SSE 事件，两者可在 P2 期间开工，且**越早越好**（契约晚改＝已实现的工具返工）。其余各卡必须等 P3 收口。

**状态：** ⏳ 未开始（拆分已完成，2026-09-19）

## 实施参数确认清单

| 编号 | 参数 | 最晚确认 | 记录位置 |
|---|---|---|---|
| D-EXEC-01 | 服务器、地域、网络、域名、证书、备案、staging | T-M2-01 前 | `infra/` 清单、ADR |
| D-EXEC-02 | PostgreSQL 备份、异地副本、恢复责任 | T-M2-02 前 | runbook |
| D-EXEC-03 | 手机验证码供应商、配额、签名、成本 | T-M2-03 前 | ADR、配置说明 |
| D-EXEC-04 | AI 账户、配额、限额、轮换责任 | T-M4-01 前 | ADR、Secret Manager、运行手册 |
| D-EXEC-05 | 对象存储、地域、生命周期、私有访问 | T-M4-04 前 | `infra/`、安全文档 |
| D-EXEC-06 | 隐私主体、删除责任、分发账号 | M5-04/M6-02 前 | 合规、发布 runbook |

## 交接模板

```text
任务编号：
完成范围 / 明确未做：
修改的文档、契约与代码：
验证命令与结果：
数据/同步/AI/安全影响：
兼容与回退方式：
开放风险与责任人：
下一项未阻塞任务：
```
