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
| R-M2-27 | 🟢 P3 | API 缺失 | 缺 `/me` 或 `/auth/session` 端点 | ✅ 已修复（见文末 T-M2-04 修复记录） |
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

---

## 2026-09-04 复验（第一轮修复后）

### 复验范围与方法

- **基线：** 提交 `0a40ca3`（feat: T-M2-03 认证会话 + T-M2-04 账本核心 API）与 `9c797c0`（docs 同步）。
- **方法：** 逐项对照修复声称复核代码；门禁重跑；实测探针验证路由、配置守卫、JWT 白名单、反向代理头。
- **修复声称：** 24 项（P1×5 + P2×12 + P3×7），全部声称已关闭。

### 门禁与探针结果

| 检查 | 结果 |
|---|---|
| `pytest tests/ -q --ignore=tests/test_stats_exports.py` | ✅ **106 passed in 4.06s**（与声称一致） |
| `pytest tests/test_stats_exports.py -q` | ❌ **8 failed / 8 passed in 5.22s**（新发现问题，见下文） |
| `API_PREFIX="/api/v1"` 探针：`GET /api/v1/health` | ✅ 200 |
| `API_PREFIX="/api/v1"` 探针：`GET /health` | ✅ 404 |
| `API_PREFIX="/api/v1"` 探针：`POST /api/v1/auth/guest` | ✅ 路由命中（500 是 SQLite 无表所致，与路由无关） |
| production 配置守卫（SECRET_KEY 默认） | ✅ 抛 ValidationError |
| production 配置守卫（dev_auth_code 非空） | ✅ 抛 ValidationError |
| production 配置守卫（CORS *） | ✅ 抛 ValidationError |
| jwt_algorithm 白名单（"none"） | ✅ 抛 ValidationError |
| X-Forwarded-For 解析 | ✅ 独立桶 |
| TTL 0 / 负值 | ✅ 抛 ValidationError |

### 已关闭项复核（逐项确认）

#### 🔴 P1（5/5 确认关闭）

| ID | 修复证据 |
|---|---|
| R-M2-01 | `main.py:108-116` 所有 `include_router(...)` 统一使用 `prefix=cfg.api_prefix`，含 health、auth、books、categories、accounts、transactions、budgets、statistics、exports 九路由。探针验证 `/api/v1/health` 返回 200、`/health` 返回 404 ✅ |
| R-M2-02 | `config.py:101-118` `model_validator(mode="after")` 在 `env="production"` 且 `secret_key == _DEV_SECRET_KEY` 时抛 `ValueError`。单元测试 `TestProductionGuards.test_secret_key_default_rejected_in_production` 覆盖 ✅ |
| R-M2-03 | `config.py:110-113` 同一 validator 在 production 下 `dev_auth_code` 非空时抛错。单元测试 `test_dev_auth_code_rejected_in_production` 覆盖 ✅ |
| R-M2-04 | `dependencies.py:33-72` 增加 `_MAX_ENTRIES=10000`、`_last_cleanup`、`_cleanup_expired()`。60 秒周期清理过期键，超过上限时按时间戳丢弃最旧条目。`check_auth_rate_limit` 改为同步函数 ✅ |
| R-M2-33 | T-M2-03 全部代码已 commit（`0a40ca3`），含 `server/src/modules/auth/` 与 `server/tests/test_auth.py` ✅ |

#### 🟡 P2（12/12 确认关闭）

| ID | 修复证据 |
|---|---|
| R-M2-05 | `config.py:46` `jwt_algorithm: Literal["HS256", "RS256", "ES256"]`，实测拒绝 `"none"`，单元测试 `test_jwt_algorithm_whitelist` 覆盖 ✅ |
| R-M2-06 | `models/user.py:65` `Index("ix_device_sessions_refresh_token_hash", "refresh_token_hash")`；`0001_initial.py:48` 同步新增 `sa.Index(...)` ✅ |
| R-M2-07 | `auth/schemas.py:29-34` `GuestRequest._check_timezone` 用 `zoneinfo.available_timezones()` 校验；`_VALID_TIMEZONES` 模块级缓存 ✅ |
| R-M2-08 | `auth/schemas.py:17, 26, 40, 45` `DEVICE_ID_MAX_LENGTH = 128`，`GuestRequest/PhoneLoginRequest/RefreshRequest` 三 schema 统一 `Field(max_length=DEVICE_ID_MAX_LENGTH)` ✅ |
| R-M2-09 | `config.py:114-117` production validator 拒绝 `cors_origins.strip() == "*"`，单元测试覆盖 ✅ |
| R-M2-10 | `docker-compose.yml:39-46` api 增加 healthcheck：`python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health/ready')"`，10s 间隔，3s 超时，5 次重试，10s 启动期 ✅ |
| R-M2-11 | `auth/service.py:147-162` 改为 `select → count → delete(DeviceSession).where(...)` 单条 DELETE。仍 SELECT 一次以返回 count，可接受 ✅ |
| R-M2-13 | `auth/service.py:168-219` `_create_session` 包 `try/except IntegrityError`，冲突时 rollback + 重新 delete + 重新生成 token + 重试一次 ✅ |
| R-M2-14 | `models/budget.py:22` `class Budget(Base, AuditMixin, VersionedMixin)`；`0001_initial.py:127-128` 同步新增 `created_at` / `updated_at` 列 ✅ |
| R-M2-15 | `auth/service.py:108-112` 过期 session 不再 `delete + flush`，直接 raise；注释明确说明"indexed lookup 便宜，下次命中同分支" ✅ |
| R-M2-17 | `config.py:52, 57` `Field(default=15, gt=0)` / `Field(default=30, gt=0)`；单元测试 `test_ttl_must_be_positive` 覆盖 0 与 -1 ✅ |
| R-M2-18 | `dependencies.py:42-55` `_get_client_ip` 优先读 `x-forwarded-for`（取第一段）、次 `x-real-ip`、再 `request.client.host`。单元测试 `test_rate_limit_uses_x_forwarded_for` 覆盖 ✅ |
| R-M2-19 | `models/user.py:23-27` 文档注释说明 phone 校验在 Pydantic schema 层（`pattern=^\d{11}$`），DB 层未加 CHECK 以避免 SQLite 测试不兼容 PostgreSQL regex ✅ |

#### 🟢 P3（7/7 确认关闭）

| ID | 修复证据 |
|---|---|
| R-M2-20 | `pyproject.toml:29` 改 `httpx2` → `httpx>=0.27,<1`，注释说明 ✅ |
| R-M2-21 | `auth/schemas.py:70` 移除未使用的 `DeviceSessionResponse`，注释说明 ✅ |
| R-M2-22 | `tests/test_auth.py:395` `TestClient(app)` 不再传 `raise_server_exceptions=False`，服务端异常可正常冒泡 ✅ |
| R-M2-23 | `tests/test_auth.py:364` `auth_client` 改为 `@pytest_asyncio.fixture() async def`，不再 `asyncio.run()` ✅ |
| R-M2-26 | `dependencies.py:75` `def check_auth_rate_limit(...)` 改为同步函数（无 async） ✅ |
| R-M2-28 | `alembic.ini:4-8` 注释说明 `sqlalchemy.url` 留空由 `env.py` 覆盖的设计意图 ✅ |
| R-M2-29 | `docker-compose.yml:31` `SECRET_KEY: ${SECRET_KEY:-dev-secret-do-not-use-in-production}` 改用 env 变量展开 + dev 默认，注释说明 ✅ |

#### 未关闭项（保持文档化）

| ID | 状态 |
|---|---|
| R-M2-12 | 已知限制 — access_token 15 分钟 TTL 内仍可重试，缓解：`get_current_user` 每次查 DB 校验 status ✅ 已文档化 |
| R-M2-16 | PG `Identity(always=True)` vs SQLite 测试后端差异 ✅ 已文档化 |
| R-M2-24/25/30/32/34/35/36/37 | 风格/挂账项 ✅ 保持挂账 |

### 复验期间新发现问题

#### 🔴 R-M2-38 `exports/service.py` 把 String 列当 enum 访问（T-M2-05 阻塞）

- **文件：** `server/src/modules/exports/service.py:91, 99, 115, 117`
- **问题：** `tx.type.value` / `tx.source.value` 假定 SQLAlchemy 返回 enum 实例，但 `Transaction.type` / `Transaction.source` 是 `String(10)` / `String(20)` 列（非 `sqlalchemy.Enum`），运行时返回的是纯字符串 `"expense"` / `"income"` / `"manual"` 等。字符串没有 `.value` 属性，调用即 `AttributeError`。
- **实测证据：** `pytest tests/test_stats_exports.py -q` → 8 failed。其中 7 个 `TestExportsAPI` 用例全部失败，堆栈定位到 `tx.type.value`。
- **影响：** T-M2-05 的导出功能在生产环境同样会失败（PostgreSQL 也是 varchar 返回字符串），不仅 SQLite 测试问题。
- **修复方向：** 直接 `tx.type` / `tx.source`（已是字符串），或统一加 helper 兼容两种情况。同文件 4 处都需修正。
- **旁证：** `statistics/service.py:122` 用 `row.type == TxType.EXPENSE`，因 `TxType(str, enum.Enum)` 继承自 str 所以字符串比较能成功——这是正确用法。`transactions/service.py` 不读 enum 字段。`exports/service.py` 是仓库里**唯一**用 `.value` 访问这些列的地方。

#### 🟡 R-M2-39 `TestTrendStats::test_trend_fills_empty_months` 失败（T-M2-05）

- **文件：** `server/tests/test_stats_exports.py`（`TestTrendStats` 类）
- **问题：** 趋势统计测试中，跨月空档填充逻辑未按预期工作，与 R-M2-38 不同源（不同错误类型，未深入定位）。
- **影响：** T-M2-05 趋势统计功能存在边界缺陷。
- **修复方向：** 单独定位；可能与趋势区间生成或零值填充逻辑有关。

#### 🟡 R-M2-40 T-M2-05 代码未入库（同 R-M2-33 失效模式）

- **文件：** `server/src/models/export.py`、`server/src/migrations/versions/0002_exports.py`、`server/src/modules/exports/`、`server/src/modules/statistics/`、`server/tests/test_stats_exports.py` 全部 untracked；`server/src/main.py`、`server/src/models/__init__.py`、`server/tests/test_migrations.py` 已修改但未暂存。
- **影响：** 与 R-M2-33 同模式的治理问题 — 文档状态（production-execution-plan.md 已推进到 T-M2-05）与仓库实际不符；代码有丢失风险。
- **修复方向：** 在宣布 T-M2-05 完成前先 `git add && git commit`；或把文档状态回退到 `PARTIAL`。

#### 🟢 R-M2-41 `test_stats_exports.py` 的 `auth_client` fixture 退回 `asyncio.run()` 模式

- **文件：** `server/tests/test_stats_exports.py:32-33`
- **问题：** `asyncio.run(_create_tables(engine))` 在同步 fixture 里创建新事件循环。R-M2-23 已在 `test_auth.py` 修复过同款问题（改用 `@pytest_asyncio.fixture() async def`），但新写的 `test_stats_exports.py` 又回到旧模式。
- **修复方向：** 改为 `@pytest_asyncio.fixture() async def auth_client`，用 `async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)` 替代。

### 复验结论

**T-M2-01/02/03/04 的修复质量：优秀。** 第一轮审查发现的 5 项 P1、12 项 P2、7 项 P3 全部按声称修复，代码与测试证据齐全，production 守卫覆盖了三种关键失效模式（SECRET_KEY / dev_auth_code / CORS wildcard），反向代理头解析、速率限制清理、JWT 算法白名单等也都有配套单元测试。

**新增问题集中在 T-M2-05（统计与导出）**：
- **R-M2-38 是功能级 bug**，不是测试问题 — 导出端点在生产环境也会 500，因为 `tx.type.value` 在任何后端都拿不到 enum。
- R-M2-39 是趋势统计的边界缺陷。
- R-M2-40 是治理问题（代码未入库）。
- R-M2-41 是测试风格回退。

**建议处理顺序：**
1. **R-M2-38**（P1）：修 `exports/service.py` 4 处 `.value` 访问，直接改用字符串或加 helper；附导出端点的回归测试。
2. **R-M2-40**（P2）：T-M2-05 代码 git add 入库。
3. **R-M2-39**（P2）：定位趋势统计空档填充逻辑。
4. **R-M2-41**（P3）：改 async fixture。

**门禁目标：** 修复后 `pytest tests/ -q` 应恢复 114 passed（当前 106 + T-M2-05 的 8 passed）+ 新增 T-M2-05 修复后的 8 个用例。

---

## T-M2-04 审查（2026-09-04）

### 审查范围与方法

- **基线：** 提交 `0a40ca3`（feat: T-M2-03 认证会话 + T-M2-04 账本核心 API）。
- **方法：** 逐项对照 T-M2-04 完成记录（`production-execution-plan.md:232-272`）复核代码与实测探针；门禁重跑。
- **交付声称：** 5 个实体 CRUD、ownership guard、version 乐观并发、软删除、分页、Transactions undo、Budgets PUT upsert。
- **明确未做：** mutationId 幂等（→T-M3-01）、服务端统计（T-M2-05）。

### 门禁与探针结果

| 检查 | 结果 |
|---|---|
| `pytest tests/ -q --ignore=tests/test_stats_exports.py` | ✅ **106 passed in 4.06s** |
| `pytest tests/test_stats_exports.py -q` | ❌ **8 failed / 8 passed**（T-M2-05 WIP，与 T-M2-04 无关） |
| `GET /me`（R-M2-27 复测） | ❌ **404** — 端点未实现 |
| `POST /books` → `GET /books/{id}`（owner） | ✅ 201 → 200 |
| `GET /books/{id}`（non-owner） | ✅ 404 ownership guard |
| `PATCH /books/{id}` wrong version | ✅ 409 version conflict |
| `PATCH /books/{id}` correct version | ✅ 200 + version bump |
| `DELETE /books/{id}` → `GET /books/{id}` | ✅ 204 → 404 soft delete |
| `GET /books`（pagination） | ✅ 200 + items list |
| `POST /categories` + `POST /accounts` | ✅ 201 |
| `POST /transactions` + `DELETE` + `POST /undo` | ✅ 201 → 204 → 200 |
| `POST /transactions/{id}/undo`（non-owner） | ✅ 404 ownership guard |
| `PUT /budgets/{period}`（首次 + 更新） | ✅ 200 + 200 upsert |
| `PUT /budgets/{period}`（non-owner） | ✅ 404 ownership guard |
| `GET /books?limit=1000` / `limit=-1` / `offset=-1` | ✅ 422 validation errors |

### 发现问题

#### 🔴 P1（1 项）

| ID | 问题 | 证据 |
|---|---|---|
| **R-M2-27** | `/me` 端点未实现 | `production-execution-plan.md:350` 明确列出 "R-M2-27 `/me` 端点留给 T-M2-04"，但实测 `GET /me` 返回 404。`src/main.py:108-116` 路由注册中无 `/me` 端点。 |

#### 🟡 P2 / 🟢 P3

无。

### 已交付功能复核

| 声称 | 验证结果 |
|---|---|
| 5 个实体 CRUD | ✅ Books/Categories/Accounts/Transactions/Budgets 全部可用 |
| controller/service/repository 三层架构 | ✅ 每个模块含 routes.py / service.py / repository.py |
| ownership guard | ✅ non-owner 访问返回 404 |
| version 乐观并发 | ✅ PATCH 时 version 不匹配返回 409 |
| 软删除 | ✅ DELETE 后 GET 返回 404，列表不再显示 |
| 分页（offset-based，默认 50，最大 200） | ✅ `src/common/pagination.py` 实现，无效参数返回 422 |
| Transactions undo | ✅ `POST /transactions/{id}/undo` 恢复软删除 |
| Budgets PUT upsert | ✅ `PUT /budgets/{period}` 首次创建 + 更新均可 |
| mutationId 幂等（未做） | ✅ 明确留给 T-M3-01 |
| 服务端统计（未做） | ✅ 明确留给 T-M2-05 |

### 结论

T-M2-04 **除 R-M2-27 外全部交付正确**。R-M2-27 是从 T-M2-03 转移的遗留项，`production-execution-plan.md:350` 明确列出但未实现，属于**承诺未兑现**。

**升级判定：** T-M2-04 可升级为 ✅ 完全成立，**前提是修复 R-M2-27**。

### 修复建议

1. **R-M2-27**（P1）：实现 `GET /me` 端点，返回当前用户信息（user_id / nickname / status / created_at）。参考 `src/modules/auth/routes.py` 现有结构，新增路由 `@router.get("/me", response_model=UserResponse)`。

### R-M2-27 修复记录（2026-09-04）

**分析：**

- 根因：T-M2-03 审查将 `/me` 端点挂账（"留给 T-M2-04"），T-M2-04 实现时遗漏，两个任务交接间丢失。`get_current_user` 依赖已存在且被 5 个账本模块复用，补端点本身无技术障碍，纯属流程遗漏。
- 路径决策：实测探针打的是 `GET /me`（顶层路径），故未挂在 `auth` router（`prefix="/auth"`，会变成 `/auth/me`），而是在 auth 模块内新增独立 `me_router`（无前缀），由 `main.py` 统一以 `api_prefix` 注册，生产路径为 `GET /api/v1/me`。

**修复内容：**

| 文件 | 改动 |
|---|---|
| `server/src/modules/auth/schemas.py` | 新增 `MeResponse`（id / nickname / phone / status / timezone / created_at / updated_at，`from_attributes=True`） |
| `server/src/modules/auth/routes.py` | 新增 `me_router` + `GET /me`，复用 `get_current_user` 依赖（401/403/503 语义随之继承） |
| `server/src/main.py` | `include_router(me_router, prefix=cfg.api_prefix)` |
| `server/tests/test_auth.py` | 新增 `TestMeEndpoint` 3 项：有效 token 返回本人资料、缺 token 401、无效 token 401 |

**验证结果（修复后实测）：**

| 检查 | 结果 |
|---|---|
| `pytest tests/ -q --ignore=tests/test_stats_exports.py` | ✅ **139 passed**（含新增 3 项，0 failed） |
| 探针：`GET /api/v1/me`（无 token） | ✅ 401 |
| 探针：`POST /api/v1/auth/guest` → `GET /api/v1/me`（有效 token） | ✅ 200，返回 `{id, nickname, phone, status, timezone, created_at, updated_at}`，字段与注册用户一致 |
| 探针：`GET /me`（未带前缀） | ✅ 404（前缀接线正确，与 R-M2-01 修复行为一致） |
| 探针：`GET /api/v1/me`（无效 token） | ✅ 401 |
| OpenAPI | ✅ `/api/v1/me` 已出现在 paths |

**说明：** 门禁总数从审查时的 106 变为 139，因工作区在审查基线后已纳入后续任务的新增测试（含本次 +3），非回归。

**结论：** R-M2-27 关闭。按此前升级判定，T-M2-04 ✅ 完全成立。
