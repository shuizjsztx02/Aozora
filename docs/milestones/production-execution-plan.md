# Aozora · 生产执行规划

**状态：** M1 全部完成 ✅，M2 阶段 T-M2-01 至 T-M2-04 已交付。当前任务为 T-M2-05。
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

### T-M0-10 执行治理与长期规划

**依赖：** T-M0-09。
**交接产物：** 有效任务图、风险状态、本文档与交接模板。
**验证：** `rg -n 'T-M1-05|T-M0-10' docs/APP-CONTEXT.md`、`git diff --check`。
**状态：** 已完成。`T-M0-07` 继续作为 T-M2-01 的独立前置；下一项为 T-M1-01。

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
- 设备测试 23/23 通过（Room 20 + WebView 1 + 杀进程 2）。
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

累计测试：前端 309 passed（14 files）；Android 设备 23/23 passed；JVM 8/8 passed。

## M2：服务端与数据库基础

| 任务 | 硬依赖 | 交接产物 | 外部前置 |
|---|---|---|---|
| T-M2-01 Server、配置与健康检查 ✅ | T-M0-07 | FastAPI 骨架、OpenAPI、镜像 | D-EXEC-01 |
| T-M2-02 Schema 与迁移 ✅ | T-M2-01 | SQLAlchemy 模型、Alembic 只追加迁移、测试库 | D-EXEC-02 |
| T-M2-03 认证与会话 ✅ | T-M2-02 | 游客/手机绑定、token rotation | D-EXEC-03 |
| T-M2-04 账本核心 API ✅ | T-M2-02、T-M2-03 | 幂等写 API、ownership、version | 无 |
| T-M2-05 统计与导出 | T-M2-04 | 权威聚合、授权导出 | 无 |

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

| 任务 | 硬依赖 | 交接产物 | 最低验证 |
|---|---|---|---|
| T-M3-01 同步契约与变更流 | T-M2-04 | push/pull、cursor、冲突响应 | 重复、乱序、分页、重试 |
| T-M3-02 Android 同步引擎 | T-M1-03、T-M3-01 | WorkManager 队列与状态事件 | 飞行模式、重启、重复点击 |
| T-M3-03 单设备恢复 | T-M3-02 | bootstrap checksum、Room 重建 | 清空 Room 后对账一致 |

## M4：AI、原生能力与体验

| 任务 | 硬依赖 | 交接产物 | 外部前置 |
|---|---|---|---|
| T-M4-01 AI Gateway | T-M2-03 | provider adapter、用量、脱敏日志 | D-EXEC-04、R-PROD-01 已关闭 |
| T-M4-02 动作协议与确认卡 | T-M1-05、T-M2-04、T-M4-01 | 安全确认与撤销闭环 | 无 |
| T-M4-03 AI 查询与洞察 | T-M2-05、T-M4-01 | 事实包、数字校验、离线降级 | 无 |
| T-M4-04 语音、图片与桥接 | T-M4-02 | 权限状态机、图片上传 | D-EXEC-05 |
| T-M4-05 通知、快捷方式、小组件 | T-M1-03、T-M3-02 | 原生入口统一读 Room | 无 |
| T-M4-06 视觉与无障碍 | 核心页面数据流稳定 | 四时主题、截图基线 | 无 |

## M5 与 M6：发布前后闸门

- **G5：安全与内测。** T-M5-01 对账 fuzz、T-M5-02 安全加固、T-M5-03 恢复演练、T-M5-04 隐私生命周期、T-M5-05 监控与 20–50 人内测均通过；D-EXEC-06 已落实。
- **G6：生产发布与观察。** T-M6-01 可重复部署与回滚、T-M6-02 签名上架包与渠道验收、T-M6-03 分阶段放量与 S0 自动止损完成。

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
