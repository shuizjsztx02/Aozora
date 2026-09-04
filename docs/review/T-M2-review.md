# T-M2 代码审查记录

- **审查日期：** 2026-09-04
- **审查范围：** T-M2-01（Server 工程、配置与健康检查）、T-M2-02（Schema 与迁移）、T-M2-03（认证与会话）
- **审查方式：** 只读代码审查 + 实测探针 + 全量测试重跑
- **基线：** 工作区未提交状态（T-M2-01/02 已 commit，T-M2-03 的 `server/src/modules/` 与 `server/tests/test_auth.py` 仍 untracked）

## 门禁验证结果（审查时实测）

| 命令 | 结果 |
|---|---|
| `cd server && pytest tests/ -q` | ✅ **76 passed in 3.10s** |
| `GET /health` | ✅ 200（但路径应在 `/api/v1/health`，见 R-M2-01） |
| `GET /api/v1/health` | ❌ 404（路由前缀未接入） |
| `POST /auth/guest` | ✅ 200 |
| `POST /api/v1/auth/guest` | ❌ 404 |
| ORM 表数量 | ✅ 11 表全部注册（users, device_sessions, books, categories, accounts, transactions, budgets, attachments, ai_conversations, ai_usage, sync_changes） |

## 问题状态汇总

| ID | 级别 | 类别 | 摘要 | 状态 |
|---|---|---|---|---|
| R-M2-01 | 🔴 P1 | 路由/配置 | `api_prefix` 配置存在但从未应用到路由 | ⬜ 未修复 |
| R-M2-02 | 🔴 P1 | 安全 | `SECRET_KEY` 默认值公开已知，production 无守卫 | ⬜ 未修复 |
| R-M2-03 | 🔴 P1 | 安全 | `dev_auth_code` 默认 `000000` 在 production 等同于万能验证码 | ⬜ 未修复 |
| R-M2-04 | 🔴 P1 | 可靠性/安全 | 内存频率限制器字典无限增长 + 多 worker 失效 | ⬜ 未修复 |
| R-M2-05 | 🟡 P2 | 安全 | `jwt_algorithm` 无白名单校验 | ⬜ 未修复 |
| R-M2-06 | 🟡 P2 | 性能 | `refresh_token_hash` 列无索引 | ⬜ 未修复 |
| R-M2-07 | 🟡 P2 | 输入校验 | `timezone` 输入无 IANA 校验 | ⬜ 未修复 |
| R-M2-08 | 🟡 P2 | 输入校验 | `device_id` 输入无长度校验 | ⬜ 未修复 |
| R-M2-09 | 🟡 P2 | 安全 | CORS 默认 `*` + `allow_credentials=True` | ⬜ 未修复 |
| R-M2-10 | 🟡 P2 | 可靠性 | `docker-compose.yml` api 服务无 healthcheck | ⬜ 未修复 |
| R-M2-11 | 🟡 P2 | 性能 | `revoke_all_sessions` 是 N+1 操作 | ⬜ 未修复 |
| R-M2-12 | 🟡 P2 | 安全 | 用户状态变更对现存 access_token 不立即生效 | ⬜ 已知（缓解：每次查 DB） |
| R-M2-13 | 🟡 P2 | 正确性 | `_create_session` 并发下会触发 unique 冲突 | ⬜ 未修复 |
| R-M2-14 | 🟡 P2 | 模型 | `Budget` 缺 `AuditMixin` | ⬜ 未修复 |
| R-M2-15 | 🟡 P2 | 正确性 | `refresh_tokens` 对过期 session 的 delete 被依赖注入回滚 | ⬜ 未修复 |
| R-M2-16 | 🟡 P2 | 测试 | `alembic` 迁移用 PG `Identity`，测试是 SQLite | ⬜ 文档化 |
| R-M2-17 | 🟡 P2 | 配置 | TTL 配置允许 0 或负值 | ⬜ 未修复 |
| R-M2-18 | 🟡 P2 | 安全 | `check_auth_rate_limit` 在反向代理后拿到代理 IP | ⬜ 未修复 |
| R-M2-19 | 🟡 P2 | 模型 | `phone` 字段 DB 层无格式约束 | ⬜ 未修复 |
| R-M2-20 | 🟢 P3 | 依赖 | `httpx2` 是非主流 fork | ⬜ 挂账 |
| R-M2-21 | 🟢 P3 | 死代码 | `DeviceSessionResponse` 已定义但未使用 | ⬜ 挂账 |
| R-M2-22 | 🟢 P3 | 测试 | `TestClient(raise_server_exceptions=False)` 隐藏异常 | ⬜ 挂账 |
| R-M2-23 | 🟢 P3 | 测试 | `auth_client` fixture 用 `asyncio.run()` 创建新事件循环 | ⬜ 挂账 |
| R-M2-24 | 🟢 P3 | 模型 | `User.device_sessions` ORM cascade 与 FK ondelete 重复 | ⬜ 挂账 |
| R-M2-25 | 🟢 P3 | 模型 | `Category.parent` 用字符串 `remote_side` | ⬜ 挂账 |
| R-M2-26 | 🟢 P3 | 代码风格 | `check_auth_rate_limit` 标 async 但无 await | ⬜ 挂账 |
| R-M2-27 | 🟢 P3 | API 缺失 | 缺 `/me` 或 `/auth/session` 端点 | ⬜ T-M2-04 应补 |
| R-M2-28 | 🟢 P3 | 配置 | `alembic.ini` 的 `sqlalchemy.url` 为空 | ⬜ 文档化 |
| R-M2-29 | 🟢 P3 | 配置 | `docker-compose.yml` 的 SECRET_KEY 明文硬编码 | ⬜ 已知（仅 dev） |
| R-M2-30 | 🟢 P3 | 配置 | OpenAPI `version="0.1.0"` 硬编码 | ⬜ 挂账 |
| R-M2-31 | 🟢 P3 | 代码风格 | `get_current_user` broad `except Exception` | ⬜ 挂账 |
| R-M2-32 | 🟢 P3 | 安全 | CORS `allow_headers=["*"]` 在生产应细化 | ⬜ 挂账 |
| R-M2-33 | 🔴 P1 | 流程 | T-M2-03 代码未 git add，与文档 ✅ 完成状态不符 | ⬜ 未修复 |
| R-M2-34 | 🟢 P3 | 性能 | `log_requests` 每次请求都算 `duration_ms` | ⬜ 挂账 |
| R-M2-35 | 🟢 P3 | 模型 | `SyncOperation` enum 与 `String(10)` 字段分离 | ⬜ 挂账 |
| R-M2-36 | 🟢 P3 | 模型 | `User.status` 是 `String(20)` 不是 SQLAlchemy Enum | ⬜ 挂账 |
| R-M2-37 | 🟢 P3 | 文档 | `generate_refresh_token` 熵值未文档化 | ⬜ 挂账 |

---

## 🔴 P1 详情

### R-M2-01 `api_prefix` 配置存在但从未应用到路由

- **文件：** `server/src/main.py:99-100`
- **问题：** `Settings.api_prefix` 默认 `/api/v1`，但 `app.include_router(health_router)` 和 `app.include_router(auth_router)` 都**没有传 `prefix=cfg.api_prefix`**。`api_prefix` 只在启动日志（`main.py:32`）里打印了一次，从未参与路由注册。
- **实测证据（探针）：**
  ```
  GET  /health            → 200   （应在 /api/v1/health）
  GET  /api/v1/health     → 404
  POST /auth/guest        → 200   （应在 /api/v1/auth/guest）
  POST /api/v1/auth/guest → 404
  ```
- **影响：**
  1. 反向代理按 `/api/v1` 路径剥离会完全失效
  2. 未来 v2 API 演进无法并存
  3. 与 APP-CONTEXT §4.4、T-M2-01 交接记录"OpenAPI 3.1 文档"宣称不符
- **修复方向：** `app.include_router(..., prefix=cfg.api_prefix)`。注意 `/`、`/docs`、`/redoc`、`/openapi.json` 是元路由，可保留在根或一并前缀，需明确决策。

### R-M2-02 `SECRET_KEY` 默认值是公开已知字符串，production 无守卫

- **文件：** `server/src/common/config.py:35-38`
- **问题：** `secret_key` 默认 `"dev-secret-do-not-use-in-production"`，无任何强制校验。`is_production` 属性存在（`config.py:84-86`）但**没有任何代码使用它**。
- **影响：** 部署时漏设 `SECRET_KEY` → 所有 JWT 用公开已知密钥签名 → 攻击者可在不接触服务器的情况下为任意用户 forge access_token，直接绕过鉴权。
- **修复方向：** pydantic `model_validator`：
  ```python
  @model_validator(mode="after")
  def _check_secret(self):
      if self.is_production and self.secret_key == "dev-secret-do-not-use-in-production":
          raise ValueError("SECRET_KEY must be set in production")
      return self
  ```

### R-M2-03 `dev_auth_code` 默认 `000000` 在 production 等同于万能验证码

- **文件：** `server/src/common/config.py:55-58` + `server/src/modules/auth/service.py:52-54`
- **问题：** `phone_login` 只校验 `verification_code == cfg.dev_auth_code`，没有任何 SMS 供应商分支。生产环境如果忘记覆盖，任何人用任意手机号 + `"000000"` 即可登录为 ACTIVE 用户。
- **影响：** 源码公开 + 默认值已知 = 完整鉴权绕过。R-PROD-01 已经吃过类似教训（历史服务商密钥未撤销），此处是同款失效模式。
- **修复方向：** 二选一：
  - production 启动时强制 `dev_auth_code` 为空或抛错
  - 在 `phone_login` 里加 `if cfg.is_production: raise AppException(501, "sms_not_configured", ...)` 直到 D-EXEC-03 落地

### R-M2-04 内存频率限制器：字典无限增长 + 多 worker/多实例完全失效

- **文件：** `server/src/modules/auth/dependencies.py:25-44`
- **问题：**
  1. `_rate_limits: dict[tuple[str, str], list[float]]` — 按 `(client_ip, endpoint)` 建键，**键永不移除**
  2. 每次请求只修剪该键的 timestamp 列表，但空列表永远留在字典里
  3. 多 worker（gunicorn -w N / uvicorn --workers N）或多实例部署时，每个进程独立维护字典，限制完全失效
- **实测证据（探针）：**
  ```
  100 个不同 IP 各请求 1 次 → len(_rate_limits) = 100
  60s 后这些键的时间戳全部过期，但字典大小仍 = 100
  ```
- **影响：**
  - 长期运行内存单调增长
  - 生产部署如果开 multi-worker，频率限制名存实亡
  - 反向代理后 `request.client.host` 都是同一 IP，所有用户共享一个桶（X-Forwarded-For 未解析，见 R-M2-18）
- **修复方向：**
  - 用 Redis 后端（代码注释里已经 TODO 了）
  - 或在 Python 层用 TTLCache / 定期清理任务
  - 在反向代理后读 `X-Forwarded-For` 或 `X-Real-IP`（见 R-M2-18）
  - 文档化此限制并列入 D-EXEC 前置

### R-M2-33 T-M2-03 代码未 git add，与文档 ✅ 完成状态不符

- **文件：** `server/src/modules/`（整个目录）、`server/tests/test_auth.py`、`server/src/main.py`（修改）、`server/src/common/config.py`（修改）、`server/pyproject.toml`（修改）
- **问题：** `git status` 显示 T-M2-03 的全部新增代码和修改都处于 **untracked / modified 未暂存**状态，但 `docs/milestones/production-execution-plan.md` 与 `docs/milestones/overview.md` 已宣布 T-M2-03 ✅ 完成。
- **影响：** 代码丢失风险；文档状态与工作区实际不符（同 R-M1-47 失效模式）。
- **修复方向：** 立即 `git add` 并提交；或把文档状态改为 `PARTIAL` 直到提交完成。

---

## 🟡 P2 详情

### R-M2-05 `jwt_algorithm` 无白名单校验

- **文件：** `server/src/common/config.py:41-44`
- **问题：** 任意字符串都可写入。实测 PyJWT 2.10 拒绝 `"none"` + key 组合（好），但 `"RS256"` 与 HS256 secret 的错误组合会运行时失败。
- **修复方向：** `Literal["HS256", "RS256", "ES256"]` 或 validator。

### R-M2-06 `refresh_token_hash` 列无索引

- **文件：** `server/src/models/user.py:69` + `server/src/migrations/versions/0001_initial.py:41`
- **问题：** `device_sessions.refresh_token_hash` 是每次 refresh / logout 的查找键，但没索引 → 全表扫描。
- **修复方向：** `index=True` + 迁移补 `sa.Index`。

### R-M2-07 `timezone` 输入无校验

- **文件：** `server/src/modules/auth/schemas.py:18` + `server/src/modules/auth/service.py:61`
- **问题：** `GuestRequest.timezone` 和 `phone_login` 默认都写 `"Asia/Shanghai"`，但任何字符串都接受。错值污染 DB 后所有客户端时间计算出错。
- **修复方向：** validator 用 `zoneinfo.available_timezones()` 校验。

### R-M2-08 `device_id` 输入无长度校验

- **文件：** `server/src/modules/auth/schemas.py:17, 24, 29`
- **问题：** `device_id: str` 无 `max_length`。DB 列是 `String(128)`，超长输入触发 500 而非 422。
- **修复方向：** `Field(max_length=128)` 并在 `GuestRequest/PhoneLoginRequest/RefreshRequest` 统一。

### R-M2-09 CORS 默认 `*` + `allow_credentials=True`

- **文件：** `server/src/common/config.py:73-76` + `server/src/main.py:61-67`
- **问题：** 浏览器规范禁止此组合；FastAPI 通过反射请求源绕过，但 dev 模式效果是"任意源都允许带 cookie"。
- **修复方向：** `is_production` 时拒绝 `*`。

### R-M2-10 `docker-compose.yml` api 服务无 healthcheck

- **文件：** `docker-compose.yml:21-32`
- **问题：** `db` 有 healthcheck，`api` 没有 → 反向代理或编排工具无法判断 api 就绪。
- **修复方向：** 加 `healthcheck: curl -f http://localhost:8000/health/ready || exit 1`。

### R-M2-11 `revoke_all_sessions` 是 N+1 操作

- **文件：** `server/src/modules/auth/service.py:136-146`
- **问题：** `select + len + for s in sessions: delete`。应改为 `delete(DeviceSession).where(user_id=...)` 一条 SQL。
- **修复方向：** 改为单条 `delete()`。

### R-M2-12 用户状态变更对现存 access_token 不立即生效

- **文件：** `server/src/modules/auth/dependencies.py:47-77`
- **问题：** `get_current_user` 每次查 DB 检查 status（✓ 已做），但 access_token 在 15 分钟内仍可被重试使用（客户端缓存）。无应急吊销手段。
- **缓解：** 已有 DB 检查，比典型 JWT 实现好；建议文档化吊销 SLA（最长 15 分钟）。

### R-M2-13 `_create_session` 的 delete-then-insert 在并发下会冲突

- **文件：** `server/src/modules/auth/service.py:160-174`
- **问题：** 同一 `(user_id, device_id)` 的两个并发 refresh 请求：两者都 delete 旧 session、都 insert 新 session → `(user_id, device_id)` unique 约束冲突 → 500。
- **修复方向：** `INSERT … ON CONFLICT DO UPDATE` 或在 service 层加锁。实际场景罕见（单设备同一时刻只应有一个客户端在刷新）。

### R-M2-14 `Budget` 缺 `AuditMixin`

- **文件：** `server/src/models/budget.py:20`
- **问题：** 11 个实体里唯一一个没有 `created_at`/`updated_at` 的。无法审计预算何时创建/修改。
- **修复方向：** 加 `AuditMixin` 并补迁移列。

### R-M2-15 `refresh_tokens` 对过期 session 的 delete+flush+raise 被依赖注入回滚

- **文件：** `server/src/modules/auth/service.py:97-100, 105-108`
- **问题：** `get_db_session` 在 route 抛异常时 rollback。service 里"清理过期 session 然后抛错"的 delete 实际没持久化。下次访问还会走同一分支。
- **修复方向：** 要么接受（反正每次查询一次），要么在 service 内用独立连接删除。

### R-M2-16 `alembic` 迁移用 PostgreSQL `sa.Identity(always=True)`，但测试环境是 SQLite

- **文件：** `server/src/migrations/versions/0001_initial.py:167` + `server/src/models/sync.py:30-34`
- **问题：** 模型用 `BigInteger().with_variant(Integer, "sqlite")` 兼容，但迁移里的 `Identity(always=True)` 是 PG 专属。`test_migrations.py` 实际测的是 ORM-vs-migration 列名一致性，并不是真的跑迁移。
- **修复方向：** 文档化；或在测试中显式跳过 sync_changes cursor 校验。

### R-M2-17 `settings.access_token_expire_minutes/refresh_token_expire_days` 允许 0 或负值

- **文件：** `server/src/common/config.py:45-52`
- **问题：** 无 `gt=0` 校验。设 0 会颁发即过期的 token，等于禁用登录。
- **修复方向：** `Field(default=15, gt=0)`。

### R-M2-18 `check_auth_rate_limit` 在反向代理后拿到的是代理 IP

- **文件：** `server/src/modules/auth/dependencies.py:33`
- **问题：** `request.client.host` 在反向代理后是代理 IP（如 `127.0.0.1` 或 LB IP），所有用户共享一个桶。
- **修复方向：** 读 `X-Forwarded-For` / `X-Real-IP`，或依赖反向代理注入的 scope 字段。

### R-M2-19 `phone` 字段 DB 层无格式约束

- **文件：** `server/src/models/user.py:31`
- **问题：** `String(32)` 接受任意字符串；Pydantic schema 校验 `\d{11}` 但直接 DB 写入可绕过。
- **修复方向：** 加 `CheckConstraint`，或接受现状（ORM 是唯一入口）。

---

## 🟢 P3 详情

| ID | 文件 | 说明 |
|---|---|---|
| R-M2-20 | `server/pyproject.toml:27` | `httpx2>=0.27,<1` 是非主流 fork，标准是 `httpx`。功能相同但增加依赖风险 |
| R-M2-21 | `server/src/modules/auth/schemas.py:54-58` | `DeviceSessionResponse` 已定义但无使用方（死代码） |
| R-M2-22 | `server/tests/test_auth.py:298` | `TestClient(raise_server_exceptions=False)` 隐藏了真实的服务端异常 |
| R-M2-23 | `server/tests/test_auth.py:268-306` | `auth_client` fixture 用 `asyncio.run()` 创建新事件循环，与 pytest-asyncio 不协调 |
| R-M2-24 | `server/src/models/user.py:47-49` | `User.device_sessions` 的 ORM `cascade="all, delete-orphan"` 与 FK `ondelete="CASCADE"` 重复 |
| R-M2-25 | `server/src/models/ledger.py:102-104` | `Category.parent` 用字符串 `remote_side="Category.id"` 而非直接引用 |
| R-M2-26 | `server/src/modules/auth/dependencies.py:31` | `check_auth_rate_limit` 标 async 但无 await |
| R-M2-27 | 路由定义 | 缺 `/me` 或 `/auth/session` 端点（客户端拿到 token 后无法查询当前用户信息）。T-M2-04 应补 |
| R-M2-28 | `server/alembic.ini:4` | `sqlalchemy.url =` 为空 → `env.py` 必须覆盖；缺失时错误信息不直观 |
| R-M2-29 | `docker-compose.yml:28` | `SECRET_KEY` 明文硬编码（仅本地 dev，OK；但应与 `.env.example` 保持一致） |
| R-M2-30 | `server/src/main.py:49` | OpenAPI `version="0.1.0"` 硬编码，与 `api_prefix` 不呼应 |
| R-M2-31 | `server/src/modules/auth/dependencies.py:55-58` | `get_current_user` broad `except Exception` 全转 401，可能掩盖 DB 连接失败等真实 bug |
| R-M2-32 | `server/src/main.py:61-67` | CORS `allow_headers=["*"]` 在生产应细化 |
| R-M2-34 | `server/src/main.py:76-93` | `log_requests` 中间件每次请求都算 `duration_ms`，无害但可优化 |
| R-M2-35 | `server/src/models/sync.py:16-19` | `SyncOperation` enum 与 `String(10)` 字段分离 → 直接 DB 写入可能绕过 enum 校验 |
| R-M2-36 | `server/src/models/user.py:33-38` | `User.status` 是 `String(20)` 不是 SQLAlchemy Enum → DB 层不强制 enum 值 |
| R-M2-37 | `server/src/modules/auth/utils.py:29-31` | `generate_refresh_token` 用 `secrets.token_hex(32)` — 64 字符 hex = 256 位熵，足够。但文档未说明 |

---

## 测试覆盖度观察

✅ 已覆盖：JWT 工具、hash、service、rate limiter、API 集成、模型约束、迁移一致性
❌ 缺失：
- 并发 `_create_session` 的 unique 约束冲突
- 多设备并发 refresh
- CORS 在 production 配置下的实际行为
- `alembic upgrade head` 在真实 PostgreSQL 的演练（无 PG 实例）
- `is_production` 守卫的单元测试（目前根本不存在此守卫）

---

## 建议处理顺序

1. **R-M2-01 / R-M2-02 / R-M2-03**（P1 × 3）：路由前缀 + SECRET_KEY 守卫 + dev_auth_code 守卫。**这三个是上线一票否决项**。
2. **R-M2-04 / R-M2-33**（P1 × 2）：频率限制 + 代码入库。**R-M2-33 应立即修复**（git add + commit），否则存在代码丢失风险。
3. **R-M2-05 / R-M2-06 / R-M2-17**（P2）：算法白名单 + refresh_token_hash 索引 + TTL 校验。
4. **R-M2-07 / R-M2-08**（P2）：timezone / device_id 校验。
5. **R-M2-09 / R-M2-10 / R-M2-18**（P2）：CORS / healthcheck / 反向代理头。
6. 其余 P2 / P3 可在 T-M2-04 开工前或顺手修复。

---

## 整体评价

工程基础扎实：异步 SQLAlchemy + Alembic 配置正确；模型约束覆盖 CHECK、UNIQUE、FK；JWT + refresh token 哈希存储方向正确；`get_current_user` 每次都查 DB 检查 status 比典型 JWT 实现更安全；76 个测试覆盖了主要路径。

但 **上线阻塞项集中在"生产配置守卫"这一类**：`api_prefix` 配置存在但未接线、`SECRET_KEY` / `dev_auth_code` 默认值在 production 允许伪造、频率限制在多 worker 失效 — 这些在 dev 测试里完全不会暴露，却在生产部署时一击致命。R-M2-01/02/03 是 T-M2-03 宣布完成前必须补的守卫。R-M2-33（代码未入库）则是治理层面的硬伤，应立即修复。
