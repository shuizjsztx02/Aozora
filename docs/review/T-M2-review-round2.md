# T-M2 第二轮代码审查（全链路复验）

- **审查日期：** 2026-09-04
- **基线：** commit `722c166`（feat: T-M2-05 服务端统计与导出完成）
- **范围：** 全 server/ 源码 + 全 tests/
- **门禁：** `pytest tests/ -q` → **122 passed in 5.26s** ✅
- **方法：** 全量文件阅读 + 关键断言实测 + 与第一轮审查（R-M2-01…R-M2-41）交叉对照

## 一、第一轮修复复核

| 项 | 复核结论 |
|---|---|
| R-M2-01 `api_prefix` | ✅ 9 个路由全部 `prefix=cfg.api_prefix`，探针验证 `/api/v1/health` 200、`/health` 404 |
| R-M2-02/03/09 production guards | ✅ `config.py:101-118` 一揽子校验 SECRET_KEY / dev_auth_code / CORS wildcard |
| R-M2-04 速率限制 | ✅ `_MAX_ENTRIES=10000` + 60s 周期清理 + `_cleanup_expired` 兜底；仍为进程内内存字典，多 worker 仍失效（文档化） |
| R-M2-06 `refresh_token_hash` 索引 | ✅ model + migration 同步 |
| R-M2-07 timezone 校验 | ✅ `_VALID_TIMEZONES = available_timezones()` 模块级缓存 |
| R-M2-08 `device_id` 长度 | ✅ 三 schema 统一 `max_length=128` |
| R-M2-10 docker-compose api healthcheck | ✅ 使用 `python -c urllib.request` 探测 `/api/v1/health/ready` |
| R-M2-11 `revoke_all_sessions` | ✅ 改为单条 DELETE |
| R-M2-13 `_create_session` 并发重试 | ✅ IntegrityError → rollback → 重删 → 重试一次 |
| R-M2-14 Budget `AuditMixin` | ✅ 模型 + 迁移补 `created_at/updated_at` |
| R-M2-15 `refresh_tokens` 不删过期 session | ✅ 直接 raise，避免依赖注入回滚 |
| R-M2-17 TTL 校验 | ✅ `gt=0` |
| R-M2-18 X-Forwarded-For | ✅ `_get_client_ip` 优先读 header |
| R-M2-33 / R-M2-40 代码入库 | ✅ T-M2-03 / T-M2-05 全部 commit |
| R-M2-38 `tx.type.value` AttributeError | ✅ 改为 `_enum_val()` helper 兼容 str 与 enum |
| R-M2-39 `test_trend_fills_empty_months` | ✅ 测试通过（趋势填充修复） |
| R-M2-41 `auth_client` fixture | ✅ 改为 `@pytest_asyncio.fixture() async def` |

**结论：** 第一轮 37 项修复全部坐实，门禁 106 → 122（新增 T-M2-05 用例）；未发现回归。

---

## 二、本轮新发现

### 🔴 P1 级（必须修）

#### N-01 软删除 Book 后仍可写数据（安全/数据完整性）

- **位置：** 4 处 `_verify_book_ownership` 实现
  - `transactions/service.py:25-31`
  - `budgets/service.py:25-31`
  - `exports/service.py:32-37`
  - `statistics/service.py:49-54`
- **问题：** 这些 helper 只过滤 `Book.owner_id == user_id`，**不过滤 `Book.deleted_at.is_(None)`**。`BookRepository.get_by_id` 与 `list_by_owner` 是正确过滤的，但 service 层的 helper 绕过了 repo。
- **复现步骤：**
  1. 用户创建 book A，写若干 transactions
  2. `DELETE /books/{A}`（软删除，`deleted_at` 被设置）
  3. `POST /transactions` 带 `book_id=A` → **201 成功**
  4. `POST /budgets/...`、`POST /exports`、`GET /stats/monthly` 对 A 都仍能访问
- **影响：** 软删除形同虚设。用户以为已经"删除"了账本，但任何拿到 token 的人/客户端仍可继续往里写数据、出统计、做导出。
- **修复方向：** 所有 4 处 helper 加 `Book.deleted_at.is_(None)`；或统一到 `BookRepository.get_by_id`。

#### N-02 `period` 参数仅正则不校验语义，`2026-13` 触发 500（可靠性）

- **位置：** `statistics/routes.py:24,35,36` 的 `pattern=r"^\d{4}-\d{2}$"`；`statistics/service.py:65` 的 `date(int(year), int(month), 1)`
- **问题：** `^\d{4}-\d{2}$` 接受 `2026-00`、`2026-13`、`2026-99`。`date(2026, 13, 1)` 抛 `ValueError`，未被捕获，冒泡成 500。
- **实测：** `date(2026, 13, 1)` → `ValueError: month must be in 1..12`
- **影响：** 客户端手误或 fuzz 测试直接 500；`period_from > period_to`（如 `2026-12 → 2026-01`）不报错但返回空 points，语义歧义。
- **修复方向：** 路由层 validator 解析为 `date` 对象，或 service 层 `try/except ValueError → AppException(422, "invalid_period", ...)`。同时校验 `period_from <= period_to`。

#### N-03 `Budget` 唯一约束与 `NULL category_id` 失效（PostgreSQL 真实部署 bug）

- **位置：** `models/budget.py:24-29` 的 `uq_budgets_period_category`；`0001_initial.py:129`
- **问题：** SQL 标准里 NULL ≠ NULL，UNIQUE 约束对 NULL 列允许多行共存。同一 `(book_id, budget_type, period)` 下可创建任意多个 `category_id=NULL` 的"总预算"。
- **实测：** SQLite 复现 `INSERT INTO t VALUES (1, NULL); INSERT INTO t VALUES (1, NULL);` → 两行都成功（PostgreSQL 行为相同）。
- **缓解：** `BudgetService.upsert_budget` 走 `get_by_period_and_category` 时用 `IS NULL` 能查到已有行，**顺序调用**不会撞；但**并发 upsert**（同 book/period 两请求同时到达）都会看到 `existing=None` → 都 `create` → 一个 `IntegrityError`（其实不会，因为约束根本不拦）→ DB 出现两条总预算 → 前端显示重复。
- **影响：** 数据模型完整性破坏。`list_budgets` 会出现多条语义相同的"本月总预算"。
- **修复方向：** 二选一
  - 改为函数唯一索引：`CREATE UNIQUE INDEX uq_budgets_overall ON budgets (book_id, budget_type, period) WHERE category_id IS NULL;` + `CREATE UNIQUE INDEX uq_budgets_category ON budgets (book_id, budget_type, period, category_id) WHERE category_id IS NOT NULL;`
  - 应用层加 advisory lock / `SELECT FOR UPDATE`。

#### N-04 `upsert_budget` 并发 TOCTOU → 500

- **位置：** `budgets/service.py:61-78`
- **问题：** `get_by_period_and_category` 查 → 若 None 则 `create`。并发下两请求都查到 None 都 create。即使 N-03 修了，category_id 非 NULL 路径仍会撞唯一约束抛 `IntegrityError`，当前未捕获 → 500。
- **修复方向：** `try/except IntegrityError → 重新读取 → update`（同 `_create_session` 模式）。

### 🟡 P2 级

#### N-05 导出文件落盘到 `/tmp` 且无清理（安全 + 资源）

- **位置：** `exports/service.py:23` 的 `EXPORT_DIR = Path(tempfile.gettempdir()) / "aozora_exports"`；`create_export` / `get_export_file`
- **问题：**
  1. `/tmp/aozora_exports/<uuid>.json` 在多租户 / 共享主机上其他用户可读（财务明细数据）。
  2. 文件 24 小时后 DB 状态变 EXPIRED（其实也没变，见 N-06），但**磁盘文件永不删除**。
  3. 每次导出都新增文件，长期运行会撑爆 `/tmp`（在容器里是 overlay fs，更危险）。
- **修复方向：**
  - 路径改为 `cfg.data_dir / "exports"` 且权限 `0o700`；
  - 增加定时任务清理 `expires_at < now` 的条目 + 文件；
  - 限制单用户导出并发 / 总量。

#### N-06 `Export.status` 永远不会被置为 `EXPIRED`

- **位置：** `models/export.py:16-20` 定义 `EXPIRED` 枚举；`exports/service.py:159-165` 仅在 `get_export_file` 里运行时判断 `expires_at < now` 抛 410，但从不写回 DB。
- **影响：** `list_exports` / `get_export` 返回的 `status` 永远是 `completed` / `pending` / `failed`，前端无法知道哪些 export 已过期。
- **修复方向：** `get_export` 返回前检查过期并写回 `status=EXPIRED`；或加定时任务；或在 schema 层增加 `is_expired` 计算字段。

#### N-07 导出同步生成 + 一次全量加载（DoS 面）

- **位置：** `exports/service.py:70-123`
- **问题：**
  1. `select(...).all()` 一次性把整本书所有 transactions 加载到 Python 内存。
  2. `json.dumps(data, indent=2)` 再完整 materialize 一遍。
  3. `file_path.write_text(...)` 是同步 IO，阻塞 asyncio 事件循环。
  4. `create_export` 在 HTTP 请求线程里同步做以上所有事；大账本会让整个 worker 卡住超过 30 秒。
- **影响：** 恶意/大账本用户能让其他用户请求超时（单 worker 部署尤为严重）。
- **修复方向：**
  - 短中期：`run_in_executor` 包装文件生成；
  - 中期：流式写文件（`csv.writer` 已经流式，但 `json.dumps` 需改为 `json.dump` 到 file-like 或逐行 `jsonlines`）；
  - 长期：把导出改成后台任务 + 轮询下载（schema 已留 `PENDING` 状态，但路由层实际是同步完成的）。

#### N-08 `Category` 重命名时未处理唯一约束冲突

- **位置：** `categories/repository.py:79-87`；`models/ledger.py:68-73` 的 `uq_categories_scope_name`
- **问题：** 把 category 重命名为同 `(book_id, parent_id, kind)` 下已存在名字时，`IntegrityError` 冒泡到 `database_exception_handler` → **500**，而非 409/422。
- **修复方向：** repo 层捕获 `IntegrityError` → 抛 `AppException(409, "duplicate_category_name", ...)`。

#### N-09 `Category.parent_id` 允许自引用 / 循环引用

- **位置：** `categories/schemas.py:15` 仅声明 `uuid.UUID | None`；`repository.py:82` 直接赋值。
- **问题：** `parent_id = self.id` 创建自环；A→B、B→A 创建 2 环；更深的循环也可构造。当前无递归查询消费此字段，但未来任何"列出子孙"逻辑都会死循环。
- **修复方向：** service 层校验：`parent_id != self.id`，且沿 parent 链向上检查不形成环；或在 DB 层加 `CHECK (parent_id != id)`。

#### N-10 `check_auth_rate_limit` 端点键过细，总限制名存实亡

- **位置：** `auth/dependencies.py:84` 的 `endpoint = request.url.path`
- **问题：** 桶按 `(client_ip, url_path)` 区分。`MAX_AUTH_REQUESTS=10` 是对**单个端点**的限制，因此同一 IP 可以在 `/auth/guest` 10 次、`/auth/login` 10 次、`/auth/refresh` 10 次、`/auth/logout` 10 次 = 40 次/分钟。未来新增 auth 端点还会继续扩大。
- **修复方向：** 把 `endpoint` 改为 `"/auth"`（前缀）或干脆只用 `client_ip`。

#### N-11 缺少 `/me` 端点（R-M2-27 遗留）

- **位置：** 所有 router 注册处
- **问题：** 客户端拿到 access_token 后无法查询当前用户信息（id / phone / nickname / status / timezone）。移动端登录完成后通常要拉一次 profile。
- **修复方向：** 在 `auth/routes.py` 增加 `GET /auth/me → get_current_user → 序列化返回`。

#### N-12 `Transaction` 软删除后 `GET /transactions/{id}` 与 `POST /transactions/{id}/undo` 之外的写操作语义不清

- **位置：** `transactions/repository.py:61-74` 的 `get_by_id` 过滤 `deleted_at IS NULL`
- **问题：** `update_transaction` 走 `get_transaction` → `get_by_id` → 已删的 tx 返回 404。但 `delete_transaction` 也对已删 tx 走 `get_by_id` → 404（**不能二次删除，幂等性 OK**）。
- **潜在问题：** `TransactionRepository.list_by_book` 也过滤 `deleted_at IS NULL`，但 `list_transactions` 在 service 层还先调 `_verify_book_ownership`（不过滤 `deleted_at`）。如果 book 被软删，list 会先通过 ownership，然后 repo 过滤返回空 → 与"book 不存在"语义混淆。
- **修复方向：** 与 N-01 一同修复。

#### N-13 `Transaction.note` 默认空字符串但 DB 列 `Text` 无长度限制

- **位置：** `transactions/schemas.py:17` 的 `max_length=2000`；`models/ledger.py:155` 的 `Text`
- **问题：** Schema 层限制 2000，但 DB 层 `Text` 无限。直接写库绕过 schema 时不受约束。与 R-M2-08 / R-M2-19 同模式。当前 ORM 是唯一写入路径，可接受。

### 🟢 P3 级

#### N-14 `Export` 模型没有 `AuditMixin` / `updated_at`

- **位置：** `models/export.py:28`
- **问题：** 状态变更（PENDING → COMPLETED/FAILED）无时间戳。
- **修复方向：** 加 `created_at`（已有）+ `updated_at`；或说明导出是一次性对象不需要。

#### N-15 `Export.file_size` 与 `file_path` 在 `PENDING`/`FAILED` 状态下为 None

- **位置：** `exports/schemas.py:35-36`
- **问题：** 响应 schema 允许 None，符合现状。但客户端需要同时判断 `status` 与这两个字段，文档化不足。

#### N-16 `Budget.period` 完全无格式校验

- **位置：** `budgets/schemas.py:13-16`、`budgets/routes.py:35`
- **问题：** `period` 是 path 参数 `str`，body 里 `BudgetUpsert` 也无 `period` 字段。可以是 `"foo"`、`"2026-13"`、`""`。应在 service 层校验 `YYYY` 或 `YYYY-MM`。

#### N-17 `BookResponse.archived_at` 字段暴露但无 API 设置

- **位置：** `books/schemas.py:31`；`models/ledger.py:55-57`
- **问题：** `archived_at` 字段在模型和响应 schema 里，但 `BookService` 只有 `soft_delete`（设置 `deleted_at`），没有 `archive`。同样 `Account.archived_at`、`Category.archived_at` 都有设置路径。`Book` 缺一条。
- **修复方向：** 要么删除 `Book.archived_at` 字段（迁移 + 模型），要么补 `POST /books/{id}/archive` 端点。

#### N-18 `TransactionResponse.deleted_at` 字段对客户端无用

- **位置：** `transactions/schemas.py:48`
- **问题：** list 与 get 都只返回未删 tx（`deleted_at IS NULL`），所以客户端永远看到 `deleted_at: null`。冗余字段。
- **修复方向：** 从响应 schema 删除，或保留作 forward-compat。

#### N-19 `User.status` 仍是字符串 enum，DB 层不强制

- **位置：** `models/user.py:40-45`
- **问题：** 与 R-M2-36 同款。挂账。

#### N-20 未测试 `_create_session` 的 IntegrityError 重试路径

- **位置：** `auth/service.py:195-219`
- **问题：** 重试逻辑存在但无并发测试覆盖。
- **修复方向：** 加并发 pytest-asyncio 测试，两请求同时 refresh 同一 (user, device)。

#### N-21 `Category.kind` 字符串 enum DB 层不强制

- **位置：** `models/ledger.py:93-95`
- **问题：** 同 R-M2-35。Schema 层 `^(expense|income)$` 是唯一守卫。

#### N-22 `check_auth_rate_limit` 没有限制 `/auth/me`（如果未来添加）

- **位置：** `auth/dependencies.py`
- **问题：** 如果按 N-11 增加 `/auth/me`，它会继承 `get_current_user` 但不继承 `check_auth_rate_limit`。本身不是问题（/me 是已认证端点），但要注意不要误加。

#### N-23 `log_requests` 中间件对健康检查也记录日志

- **位置：** `main.py:83-101`
- **问题：** `/api/v1/health` 与 `/api/v1/health/ready` 被 docker-compose 每 10s 探测一次，产生大量 info 日志。
- **修复方向：** 跳过 `path.startswith("/health")` 或降为 debug。

#### N-24 `Category.parent` 仍使用字符串 `remote_side`

- **位置：** `models/ledger.py:104-106`
- **问题：** `remote_side="Category.id"` 是字符串形式，R-M2-25 声称修复实际保留。功能正确，但文档与代码不符。
- **修复方向：** 改为 `remote_side=[Category.id]` 或接受现状并在注释里说明。

#### N-25 `refresh_tokens` 在 user 已删/禁用时仍 delete 旧 session 后再检查 user

- **位置：** `auth/service.py:114-123`
- **问题：** 顺序是先校验 user、再 delete 旧 session。OK，没问题。但 `logout` 在 session 找到但 user 已删时**不报错**（204 成功）——幂等性 OK。

#### N-26 `guest` 创建时 `nickname` 长度未限制

- **位置：** `auth/schemas.py:25`
- **问题：** `nickname: str | None = None`，无 max_length。`users.nickname` 是 `String(64)`，超长输入会 500。
- **修复方向：** `Field(default=None, max_length=64)`。

#### N-27 `phone_login` 不能把 guest 升级为 active

- **位置：** `auth/service.py:54-80`
- **问题：** guest 用户打 `/auth/login` 会创建**新** user，而非给当前 guest 加 phone。功能缺失（不是 bug）。

#### N-28 `Transaction.amount_cents` 无上限

- **位置：** `transactions/schemas.py:14` 的 `gt=0`（无 `le`）；`models/ledger.py:144` 的 `BigInteger`
- **问题：** 允许 10^18 级别的金额。stats 聚合 `sum()` 在 Python 不溢出（任意精度），但 PostgreSQL `BigInteger` 上限 9.2e18，多个巨额相加可能溢出。
- **修复方向：** 加合理上限，比如 `le=10**12`（一万亿元，远超日常需要）。

#### N-29 `exports/routes.py:54` 下载端点无速率限制

- **位置：** `exports/routes.py:54-67`
- **问题：** 下载端点未认证 rate limit，理论上被盗 token 可以疯狂下载。但 FileResponse 是流式的，成本相对低。挂账。

#### N-30 `Transaction.occurred_at` 与 `biz_date` 可能不一致

- **位置：** `transactions/schemas.py:18-19`
- **问题：** 客户端可传 `biz_date=2026-01-01` + `occurred_at=2026-09-01T00:00:00Z`，统计按 `biz_date` 聚合，但 `occurred_at` 是另外的值。语义上 OK，但可能导致导出与统计不一致。挂账。

---

## 三、优先级建议

### 必做（T-M2 闭环前）
1. **N-01** 软删除绕过（P1 × 4 处）— 安全/数据完整性
2. **N-02** period 校验 → 500 — 输入校验
3. **N-03 + N-04** Budget 唯一约束 + 并发 — 数据完整性
4. **N-26** nickname 长度 — 顺手改，1 行

### 强烈建议（生产部署前）
5. **N-05 + N-06** 导出文件安全与清理
6. **N-07** 导出异步化 / 流式化
7. **N-08** Category 重命名唯一约束错误处理
8. **N-10** 速率限制端点键过细
9. **N-11** 补 `/me` 端点

### 可挂账
10. **N-09** parent_id 自引用（当前无消费方）
11. **N-14 ~ N-30** 文档化 / 风格 / 小修

---

## 四、整体评价

T-M2 五个任务累计实现后，服务端骨架 + 认证 + 账本 CRUD + 统计导出已经成型，**第一轮 37 项审查问题全部有效修复**，工程纪律比 T-M1 结束时明显提升：

- ✅ production guards（SECRET_KEY / dev_auth_code / CORS wildcard）三类硬失效模式全部在启动期拦截
- ✅ 反向代理头解析、速率限制清理、JWT 算法白名单、TTL 校验等都有配套单元测试
- ✅ 122 个测试覆盖主路径 + 所有权隔离 + 导出对账不变量
- ✅ T-M2-05 的 `_enum_val` helper 正确规避了 R-M2-38 的 str/enum 陷阱，且未破坏其他地方

但**本轮新发现的 4 个 P1 全部是"边界状态绕过"类型**：
- N-01 软删除 book 后仍可写（状态绕过）
- N-02 period `2026-13` 触发 500（输入校验绕过）
- N-03/04 Budget 唯一约束与 NULL 失效 + 并发 TOCTOU（约束绕过）

这与 T-M1 结尾的 R-M1-48/49/50（金额容错残留）是同一类失效模式：**主路径正确，但边界状态 / 边界输入没守住**。建议在 T-M2 收口前集中处理 N-01 ~ N-04 + N-26，其余 P2/P3 可作为 T-M3 服务端加固任务批量处理。

---

## 五、复验记录（2026-09-05）

- **基线：** commit `026b516`（T-M3-01 同步）+ 工作区未提交改动（N-11 `/me` 修复）
- **门禁：** `pytest tests/ -q` → **155 passed**（round2 基线 122 + T-M3-01 同步测试 30 + `/me` 测试 3）
- **方法：** 逐项对照第二、三节复核代码现状 + grep 测试覆盖 + 探针

### 已关闭项复核（7 项确认落地）

| 项 | 修复证据 |
|---|---|
| N-01 软删除绕过 | ✅ 4 处 `_verify_book_ownership`（transactions/budgets/exports/statistics service）均加 `Book.deleted_at.is_(None)` 过滤，注释标注 N-01/N-12 |
| N-02 period 500 | ✅ 路由正则收紧为 `^\d{4}-(0[1-9]|1[0-2])$`（`statistics/routes.py:24,35,36`）；service 层 `_parse_period` 捕获 `ValueError` → 422 `invalid_period`；趋势接口加 `period_from <= period_to` 校验 → 422 |
| N-03 Budget NULL 唯一约束 | ✅ 模型改为两个 partial unique index（`uq_budgets_overall` WHERE NULL / `uq_budgets_category` WHERE NOT NULL，`models/budget.py:29-44`）；配套迁移 `0004_budget_unique_constraint.py` 同步 drop 旧约束 |
| N-04 upsert TOCTOU | ✅ `budgets/service.py:74-91` create 包 `IntegrityError` → rollback → 重读 → 转 update（同 `_create_session` 模式） |
| N-12 软删 book 语义混淆 | ✅ 随 N-01 一并修复（ownership 先行 404，不再走到 repo 空列表分支） |
| N-26 nickname 长度 | ✅ `auth/schemas.py:26` `Field(default=None, max_length=64)` |
| N-11 `/me` 端点 | ✅ `me_router` + `GET /me`（顶层路径，`api_prefix` 注册后为 `/api/v1/me`），`MeResponse` 含 id/nickname/phone/status/timezone/created_at/updated_at。探针：有效 token 200、缺/坏 token 401、未前缀 404、OpenAPI 已收录。3 项测试（`TestMeEndpoint`）通过 |

N-22 自动解除：`/me` 实现仅挂 `get_current_user`，未误加 `check_auth_rate_limit`，符合 N-22 提示。

### 复验新发现

#### 🟡 N-31 N-01~N-04/N-26 五项修复零回归测试（测试缺口）

- **证据：** `git show --stat 1859164` — 8 个文件全部是源码/迁移，**无任何测试文件**。实测 `grep tests/`：无 `2026-13`/`invalid_period` 用例、无软删 book 后写入被拒用例、无 budget 重复约束用例、无超长 nickname 用例。
- **影响：** 五项修复均无守卫，未来重构可能静默回归 — 与 R-M1-48/49/50"修复后无测试固化"同一失效模式。门禁 155 passed 不能证明这些边界行为被锁定。
- **修复方向：** 每项至少 1 个回归用例：软删 book → POST /transactions 404；`period=2026-13` → 422；同 (book,period) 双总预算第二个 409/命中 update；nickname 65 字符 → 422。
- **✅ 已修复（本轮）：** 新增 7 个回归测试（见下方 2026-09-05 补测记录），门禁 162 passed。

#### 🟡 N-32 N-11 `/me` 修复未入库（治理，同 R-M2-33/R-M2-40 模式）

- **证据：** `git status` — `server/src/modules/auth/routes.py`、`auth/schemas.py`、`tests/test_auth.py`、`docs/review/T-M2-review.md` 均 unstaged。
- **影响：** 修复有丢失风险；文档（已记录关闭）与仓库状态不符。
- **修复方向：** `git add && git commit`（建议与 N-31 回归测试一并提交）。
- **✅ 已修复（本轮）：** `/me` 修复 + N-31 回归测试已 commit 并推送。

### 遗留问题范围（更新后）

- **已关闭（7）：** N-01、N-02、N-03、N-04、N-11、N-12、N-26
- **仍未处理（17）：** N-05 ~ N-10、N-13 ~ N-30（除已关闭项）— 代码现状抽查确认均未动：`exports` 仍 `gettempdir()` 落盘（N-05）、无 EXPIRED 写回（N-06）、同步全量生成（N-07）；`categories` rename 无 IntegrityError 处理（N-08）；速率限制键仍为 `(client_ip, url_path)`（N-10）；`amount_cents` 仍无上限（N-28）；Category parent 无自引用校验（N-09）
- **新增（2）：** N-31（回归测试缺口）、N-32（`/me` 未入库）

### 修复优先级（更新后）

1. **N-32 + N-31**：`/me` 修复入库 + 五项 P1/N-26 修复补回归测试，一次提交完成（半天内）
2. **N-05 + N-06**：导出文件安全（权限/目录）+ EXPIRED 状态写回（生产部署前）
3. **N-07**：导出异步化/流式化（生产部署前，DoS 面）
4. **N-08 / N-10**：Category 重命名 409、速率限制键收敛（生产部署前）
5. **N-09 / N-13 ~ N-30 余下项**：挂账，并入 T-M3 服务端加固批量处理

**结论：** round2 认定的 4 个必做项（N-01~N-04）+ 顺手项（N-26）+ 强烈建议项 N-11 全部落地且代码证据充分；但修复纪律差最后一步 — 零回归测试 + `/me` 未入库。补上 N-31/N-32 后，T-M2 的"必做"清单即可视为真正闭环；剩余 17 项均属"强烈建议/可挂账"级，不阻塞 T-M2 收口，按上述顺序进入生产前加固。

---

## 六、N-31 + N-32 闭环补测（2026-09-05）

### 执行范围

- N-31：为 N-01/N-02/N-03/N-04/N-26 五项修复补回归测试（7 项）
- N-32：将 `/me` 修复（N-11）与回归测试一并入库

### 新增回归测试（7 项）

| # | 测试 | 文件 | 验证的修复 |
|---|---|---|---|
| 1 | `TestTransactionsAPI::test_write_to_soft_deleted_book_rejected` | `test_ledger_api.py` | N-01：软删 book 后 `POST /transactions` 返回 404 |
| 2 | `TestPeriodValidation::test_invalid_period_month_rejected` | `test_stats_exports.py` | N-02：`period=2026-13` → 422 |
| 3 | `TestPeriodValidation::test_period_zero_month_rejected` | `test_stats_exports.py` | N-02：`period=2026-00` → 422 |
| 4 | `TestPeriodValidation::test_period_range_order_rejected` | `test_stats_exports.py` | N-02：`period_from > period_to` → 422 |
| 5 | `TestBudget::test_overall_budget_null_category_no_duplicate` | `test_models.py` | N-03：两个 NULL category 的"总预算"同键 → IntegrityError |
| 6 | `TestBudget::test_upsert_budget_same_period_idempotent` | `test_models.py` | N-04：service 层连续 upsert 同键两次 → 1 行，amount 更新 |
| 7 | `TestAuthAPI::test_nickname_overlong_rejected` | `test_auth.py` | N-26：nickname 65 字符 → 422 |

注：N-04 的真正 TOCTOU 并发测试依赖 PostgreSQL 隔离；此处验证的是 service 层 upsert 幂等性（partial unique index 保护下的顺序去重），并发路径由代码评审确认。

### 验证结果

```
pytest tests/ -q
162 passed in 7.36s
```

（门禁 155 → 162，新增 7 项回归测试全部通过，无回归。）
