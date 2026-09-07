# T-M5-04c 账号删除与数据保留 审查报告

> **审查日期:** 2026-09-07
> **审查范围:** T-M5-04c（账号删除与数据保留）
> **审查方法:** 代码 diff 复核 + 文档完整性检查 + 测试覆盖度分析 + 数据流追踪
> **对照基线:** `docs/T-M5-04c-implementation-plan.md`
> **关联审查:** `docs/review/T-M5-04-review.md`（04a + 04b 审查）

---

## 一、审查结论

| 子模块 | 结论 | 备注 |
|---|---|---|
| Schema 变更（users/attachments/deletion_audit/deletion_tokens） | ✅ 有条件通过 | 1 个 P2 问题（`deletion_audit` 缺 `rows_count` 字段，与计划不符） |
| Deletion API（`/me/deletion-request`、`DELETE /me`、`GET /me` 冻结字段） | ✅ 有条件通过 | 2 个 P2 问题（token 无唯一约束、并发可能产生多有效 token） |
| 冻结期行为（push/pull/stats/bootstrap） | ✅ 通过 | 拦截逻辑正确、stats 双路由已覆盖、bootstrap 一并拦截 |
| Cron 脚本（account-deletion.py / attachment-expiry.py） | ⚠️ 有条件通过 | **1 个 P1**（无事务保护，部分失败导致数据不一致）；若干 P2 |
| Kotlin 端（AccountClient / bridge handler / TokenStore 清理） | ✅ 通过 | 错误分类完整、清理路径完整 |
| 客户端（MePage / NativeBridge / tests） | ✅ 通过 | 二次确认对话框完整、8 个测试用例覆盖 |

**总体结论: ⚠️ 有条件通过**。核心功能（API、冻结期、客户端）完整，但 **`account-deletion.py` 无事务保护** 为上线阻塞项，需修复后再行部署。共 1 个 P1 / 5 个 P2 / 5 个 P3。

---

## 二、Schema 变更审查

### 1. `users` 表增加 `frozen_at` / `deletion_requested_at` ✅

**文件:** `server/src/migrations/versions/0012_user_freezing_columns.py`
**模型:** `server/src/models/user.py`（`User.frozen_at`、`User.deletion_requested_at`、`UserStatus.FROZEN`）

**实现内容:**
- 迁移 `0012`：增加两列，均 `nullable=True`，默认 `None`
- ORM 模型：两个字段均 `Mapped[datetime | None]`
- `UserStatus` 枚举新增 `FROZEN = "frozen"`

**问题:**
- ⚠️ **P3**：`frozen_at` 与 `status = FROZEN` 应同步设置，但代码中只通过 `set_account_frozen()` 同步。如果未来有人手动改 `status` 但忘了设 `frozen_at`，`is_frozen()`（同时检查两者）会返回 false，导致应被拦截的请求被放行
- **建议:** 在 `User` 模型添加 `@validates` 或在 `set_account_frozen` 之外的入口（如单元测试辅助）添加一致性检查

**判定:** 字段设计合理，通过。

---

### 2. `attachments` 增加 `keep_after_parse` ✅

**文件:** `server/src/migrations/versions/0014_attachment_keep_after_parse.py`
**模型:** `server/src/models/attachment.py`

**实现内容:**
- 迁移 `0014`：`keep_after_parse = Column(Boolean, default=False, server_default=sa.false())`
- ORM 模型：`keep_after_parse: Mapped[bool] = mapped_column(default=False)`

**问题:** 无

**判定:** 默认 false 符合计划"解析后 24h 删除"的策略。通过。

---

### 3. `deletion_audit` 表 ✅ 有问题

**文件:** `server/src/migrations/versions/0013_deletion_audit.py`
**模型:** `server/src/models/deletion.py`

**实现内容:**
- 迁移 `0013`：创建 `deletion_audit` 表
- 字段：`id` / `user_id_hash` / `requested_at` / `completed_at` / `deleted_tables`（JSON）

**问题:**
- ⚠️ **P2**：计划要求有 `rows_count` 字段，但实际 ORM 模型和迁移中**缺失此字段**。审计记录无法追溯"删了多少行数据"，合规与事后追溯价值大打折扣
- ⚠️ **P3**：`deleted_tables` 只记录显式 DELETE 的表名（`user_consents / deletion_tokens / device_sessions / books` 四个），通过 `ON DELETE CASCADE` 级联删除的表（`transactions / categories / accounts / attachments / exports / budgets / ai_cache / ai_usage_daily / sync_mutations / proposal_mutations` 等）**未被记录**
- **建议:**
  1. 增加 `rows_count` 列（JSON 或整数），记录每张表删除的行数
  2. 在 `account-deletion.py` 中用 `SELECT COUNT(*)` 预查或用 `cursor.rowcount` 累加级联影响行数
  3. 将级联删除的表也纳入 `deleted_tables`

**判定:** 表结构基本完整，但**与计划不符且审计价值不足**，P2 修复后通过。

---

### 4. `deletion_tokens` 表 ✅ 有问题

**文件:** `server/src/migrations/versions/0013_deletion_audit.py`（同一迁移）
**模型:** `server/src/models/deletion.py`

**实现内容:**
- 字段：`id` / `user_id` / `confirm_token_hash` / `expires_at` / `created_at`
- 索引：`ix_deletion_tokens_user_id`

**问题:**
- ⚠️ **P2**：`user_id` 列**缺少 UNIQUE 约束**。`request_deletion_token` 虽先 DELETE 旧记录再 INSERT，但两个并发请求（如用户快速双击、网络重试）可能都通过"先 DELETE"阶段，各自 INSERT 一条，产生**两个有效 token**。攻击者可利用任一个完成删除
- **建议:** 加 `UniqueConstraint('user_id', name='uq_deletion_tokens_user_id')`，并在 `request_deletion_token` 中使用 `INSERT ... ON CONFLICT DO UPDATE` 或应用层加锁

**判定:** 表结构基本完整，但**并发安全性不足**，P2 修复后通过。

---

## 三、Deletion API 审查

### 1. `POST /me/deletion-request` ✅

**文件:** `server/src/modules/deletion/routes.py`、`service.py`、`schemas.py`

**实现内容:**
- 校验当前用户已认证
- 限流：`check_deletion_rate_limit`（1 次/分钟，20 次/天）— 通过 Redis 或内存计数器
- 删除旧 token → 创建新 token（5 分钟有效）
- 返回 `confirm_token`（明文，一次性）+ `expires_at`

**问题:**
- ⚠️ **P3**：限流窗口 1 分钟——用户误触后需要等待 60 秒才能重新获取 token。可接受，但 UX 可考虑在错误提示中明确告知等待时间

**判定:** 限流 + 短生命周期 token 双重防御，通过。

---

### 2. `DELETE /me?confirm_token=xxx` ✅ 有问题

**文件:** `server/src/modules/deletion/routes.py`、`service.py`

**实现内容:**
- 查询 `deletion_tokens` 中 hash 匹配的 token
- 校验 `expires_at > now`
- 删除 token（一次性使用）
- 调用 `set_account_frozen(user)` 将 `status=FROZEN` + `frozen_at=now` + `deletion_requested_at=now`

**问题:**
- ⚠️ **P3**：token 校验失败（无效或过期）返回 400 `invalid_token`。但"无效"和"过期"应区分——前者可能是伪造尝试（应记录日志/限流），后者是正常场景。当前统一处理，难以事后分析
- **建议:** 区分 `invalid_token` 与 `expired_token` 两种错误码

**判定:** 校验逻辑完整（hash 对比 + 过期检查 + 一次性使用），通过。

---

### 3. `GET /me` 返回冻结字段 ✅

**文件:** `server/src/modules/users/routes.py`、`schemas.py`

**实现内容:**
- `UserPublic` schema 增加 `frozen_at: datetime | None` 和 `deletion_requested_at: datetime | None`
- `GET /me` 返回这两个字段
- 冻结期 `GET /me` 仍可访问（`get_current_user` 放行 frozen 用户，仅 `_require_active` 拦截）

**问题:** 无

**判定:** 客户端可展示"账号将于 X 日永久删除"倒计时。通过。

---

## 四、冻结期行为审查

### 1. `/sync/push` → 403 `account_frozen` ✅

**文件:** `server/src/modules/sync/routes.py`（line 61）
**冻结检查:** `server/src/modules/auth/frozen.py`

**实现内容:**
- `_require_active(current_user)` 在 push / bootstrap 入口调用
- `require_active()` 检查 `status == FROZEN` 或 `frozen_at is not None`，如是则抛 `AppException(403, 'account_frozen', ...)`

**问题:** 无

**判定:** 拦截正确。通过。

---

### 2. `/sync/pull` → 空响应 ✅

**文件:** `server/src/modules/sync/routes.py`（line 75-76）

**实现内容:**
- `if _is_frozen(current_user): return PullResponse(changes=[], cursor=body.cursor, has_more=False)`
- 不 403，保留客户端可重试形态

**问题:** 无

**判定:** 设计合理（客户端不弹错误，但停止拉新数据）。通过。

---

### 3. `/stats/*` 排除 frozen 用户 ✅

**文件:**
- `server/src/modules/stats/routes.py`（line 34、107）
- `server/src/modules/stats/service.py`（`exclude_frozen_users` 辅助函数）

**实现内容:**
- `get_monthly_summary` / `get_category_breakdown` 两个路由都调用 `exclude_frozen_users`
- 该函数在 `WHERE` 子句加 `users.frozen_at IS NULL`（注意：**不是** `status != FROZEN`，只用 `frozen_at` 判断）
- `get_daily_trend` 是聚合历史数据，不过滤 frozen（符合计划："历史趋势数据保持不变"）

**问题:**
- ⚠️ **P3**：`exclude_frozen_users` 只用 `frozen_at IS NULL`，但 `is_frozen()` 同时检查 `status == FROZEN` 和 `frozen_at`。两者判定标准不一致：
  - 一个用户可能 `status=FROZEN` 但 `frozen_at=NULL`（异常状态），stats 不会排除他，但 sync 会拦截
  - **建议:** 统一使用 `status = FROZEN` 或 `frozen_at IS NOT NULL` 作为判定标准

**判定:** 实际场景下两者同步设置，问题不会暴露，但建议统一。通过。

---

### 4. `/sync/bootstrap` 排除 frozen ✅

**文件:** `server/src/modules/sync/routes.py`（line 87）

**实现内容:**
- `bootstrap` 入口调用 `_require_active(current_user)`，frozen 用户直接 403

**问题:** 无

**判定:** 通过。

---

## 五、Cron 脚本审查

### 5.1 `account-deletion.py` ⚠️ 有 P1 问题

**文件:** `infra/cleanup/account-deletion.py`

**实现内容:**
- 扫描 `users.frozen_at <= now - 7d`
- 对每个用户，按顺序 DELETE：`user_consents` → `deletion_tokens` → `device_sessions` → `books`（CASCADE 删 transactions/categories/budgets）→ `users`（RESTRICT FK 已通过删 books 解除）
- 写 `deletion_audit` 记录

**问题:**
- 🔴 **P1 — 阻塞上线**：**无事务保护**
  - 脚本使用 `engine.execute(text(...))` 逐条执行 DELETE
  - SQLAlchemy 的 `Engine.execute()` 每次使用连接池中的一个新连接，每次执行后**自动 commit**
  - 如果中间某条 DELETE 失败（如 DB 连接中断、FK 约束违反），**之前已提交的 DELETE 不会回滚**
  - 后果：用户数据被部分删除，无法恢复，审计记录缺失
  - **修复方案:**
    ```python
    with engine.begin() as conn:  # 显式事务
        conn.execute(text("DELETE FROM user_consents WHERE user_id = :uid"), {...})
        conn.execute(text("DELETE FROM deletion_tokens WHERE user_id = :uid"), {...})
        conn.execute(text("DELETE FROM device_sessions WHERE user_id = :uid"), {...})
        conn.execute(text("DELETE FROM books WHERE owner_id = :uid"), {...})
        conn.execute(text("DELETE FROM users WHERE id = :id"), {...})
        conn.execute(text("INSERT INTO deletion_audit ..."), {...})
    ```
- ⚠️ **P2**：如前述，`deleted_tables` 不包含 CASCADE 删除的表，审计记录不完整
- ⚠️ **P3**：日志中只打印"物理删除"，建议打印每个 DELETE 的 `cursor.rowcount` 便于事后核对
- ⚠️ **P3**：未处理 `books` 中 `deleted_at IS NOT NULL`（软删除）记录的统计——它们也会被物理删除，但 `deleted_tables` 不体现

**判定:** **P1 必须修复，否则生产环境跑此脚本会导致数据不一致。**

---

### 5.2 `attachment-expiry.py` ✅

**文件:** `infra/cleanup/attachment-expiry.py`

**实现内容:**
- 扫描 `attachments.status='parsed' AND keep_after_parse=false AND updated_at <= now - 24h`
- 删除磁盘文件（`os.remove`，忽略 FileNotFoundError）
- 数据库标记：`status='expired'`、`object_key=NULL`、`updated_at=now`
- 支持 `--dry-run` 模式
- 返回过期数量用于监控

**问题:**
- ⚠️ **P3**：磁盘文件删除与数据库更新不在同一事务中（先删文件，后更新 DB）。如果 DB 更新失败，文件已删但 DB 仍标记为 `parsed`，下次扫描会重试，但 `os.remove` 会因文件不存在而跳过（已正确处理 `FileNotFoundError`），所以实际无害
- **建议:** 改为"先更新 DB，后删文件"，或在 DB 更新失败时回滚（当前设计可接受）

**判定:** 逻辑完整，错误处理合理。通过。

---

## 六、客户端审查

### 6.1 `AccountClient.kt` ✅

**文件:** `app/src/main/java/com/aozora/app/account/AccountClient.kt`

**实现内容:**
- `requestDeletionToken()`: 调用 `POST /me/deletion-request`
- `deleteAccount(confirmToken)`:
  1. 调用 `DELETE /me?confirm_token=xxx`
  2. 成功后：清空 Room（`db.clearAllTables()`）、`TokenStore.clear()`、`ConsentStore.clear()`、`SharedPreferences`（隐私同意、主题、提醒）
  3. 通过 `onAccountDeleted` 回调通知 Activity 跳转 `/onboarding`
- 错误分类：`AuthLost` / `InvalidToken` / `DeletionPending` / `Network` / `ServerError`
- `extractUserHash`：从 `WWW-Authenticate` 提取 `user_hash="..."`，校验 64 字符 hex

**问题:**
- ⚠️ **P3**：`extractUserHash` 中，如果 `WWW-Authenticate` 缺失或格式不符，默认返回 64 个零的 hash。这会导致审计日志中的 `user_id_hash` 全是零，失去识别价值
- **建议:** 返回 `null` 并在调用处记录为"unknown user"

**判定:** 错误分类完整，清理路径完整。通过。

---

### 6.2 `AozoraBridge.kt` handler ✅

**文件:** `app/src/main/java/com/aozora/app/bridge/AozoraBridge.kt`（line 215-248）

**实现内容:**
- `account.requestDeletionToken` → `accountClient.requestDeletionToken()` → 返回 `{ confirmToken, expiresAt }`
- `account.delete` → `accountClient.deleteAccount(confirmToken)` → 返回 `{ success, userHash }`
- 两个命令都包裹在 `try/catch`，错误分类完整

**问题:** 无

**判定:** 通过。

---

### 6.3 `MePage.tsx` ✅ 有问题

**文件:** `app/src/features/settings/MePage.tsx`

**实现内容:**
- "危险区"分组，"删除账号"按钮
- 二次确认对话框：输入 `DELETE`（精确匹配）才可点击"确认删除"
- 错误提示：区分 `invalid_token` / `deletion_pending` / `auth_lost` / 通用错误
- 点击遮罩层可关闭对话框（非处理中状态）

**问题:**
- ⚠️ **P3**：成功后（Kotlin 清空数据 + navigate onboarding），React 端 `deleteInProgress` 仍为 true，对话框未关闭。如果 Kotlin navigate 失败（如 Activity 被系统回收重建），用户看到卡死对话框
- **建议:** `deleteAccount` 成功后关闭对话框并显示"已提交删除请求"提示，不依赖 Kotlin 跳转

**判定:** UX 完整，错误处理到位。通过。

---

### 6.4 `MePage.test.tsx` ✅

**文件:** `app/src/__tests__/components/MePage.test.tsx`

**实现内容:**
8 个测试用例：
1. 默认不显示对话框 ✓
2. 输入 `DELETE` 后按钮启用 ✓
3. 输入错误文本按钮禁用 ✓
4. 成功流程（token → delete → Kotlin 回调）✓
5. `invalid_token` 错误提示"确认码已失效" ✓
6. `deletion_pending` 错误提示"账号已在删除流程中" ✓
7. 点击遮罩层关闭对话框 ✓
8. 处理中遮罩层点击不关闭 ✓

**问题:** 无

**判定:** 覆盖完整，包含 UX 细节验证。通过。

---

## 七、测试覆盖度

### 服务端测试 ✅

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| `server/tests/test_users_me.py` | ~20 | `/me/deletion-request` 限流 + token 生命周期 + `DELETE /me` 校验 + `set_account_frozen` |
| `server/tests/test_sync.py` | 7 新增 | frozen 用户 push 403 / pull 空 / bootstrap 403 / 非 frozen 不受影响 |
| `server/tests/test_stats_*.py` | 4 新增 | monthly/categories 排除 frozen 用户 / daily_trend 保留 |
| `server/tests/test_account_deletion.py` | 2 | cron 物理删除 + 审计记录 |
| `server/tests/test_attachment_expiry.py` | 2 | cron 过期附件 |

**问题:**
- ⚠️ **P3**：`test_account_deletion.py` 中 `DELETE FROM users` 在 PG 上会因 RESTRICT FK 失败（books 未先删除）。测试在 SQLite 跑通是因为 SQLite 默认禁用 FK 检查。**生产环境此测试无法在 PG 上运行**，建议修正测试代码

---

## 八、与计划对照

| 计划要求 | 实现状态 | 验证结果 |
|---|---|---|
| users 增加 frozen_at、deletion_requested_at 列 | ✅ | `0012_user_freezing_columns.py` |
| UserStatus 新增 FROZEN | ✅ | `models/user.py` |
| attachments 增加 keep_after_parse | ✅ | `0014_attachment_keep_after_parse.py` |
| 新建 deletion_audit 表 | ⚠️ | 缺 `rows_count` 字段（P2） |
| 新建 deletion_tokens 表 | ⚠️ | 缺 `UNIQUE(user_id)` 约束（P2） |
| `POST /me/deletion-request` 签发 5 分钟 token | ✅ | `deletion/routes.py` |
| `DELETE /me?confirm_token=xxx` 置为 frozen | ✅ | `deletion/service.py` |
| `GET /me` 返回 frozen_at 和 deletion_requested_at | ✅ | `users/schemas.py` |
| `/sync/push` → 403 account_frozen | ✅ | `sync/routes.py` |
| `/sync/pull` → 空响应 | ✅ | `sync/routes.py` |
| `/stats/*` 排除 frozen 用户 | ✅ | `stats/service.py` |
| `get_current_user` 放行 frozen 用户 | ✅ | `auth/dependencies.py` |
| `account-deletion.py` 扫描 frozen_at <= now-7d → 物理删除 | ⚠️ | 无事务保护（P1） |
| `attachment-expiry.py` 扫描 parsed + keep_after_parse=false + 24h | ✅ | `infra/cleanup/attachment-expiry.py` |
| Kotlin AccountClient 新增方法 | ✅ | `account/AccountClient.kt` |
| MePage 删除账号按钮 + 二次确认 | ✅ | `settings/MePage.tsx` |
| 成功后清空 Room + TokenStore | ✅ | `AccountClient.kt` |

---

## 九、发现的问题汇总

### P1 — 阻塞上线

1. **`account-deletion.py` 无事务保护** — 多次 `engine.execute()` 分别 auto-commit，中途失败会导致用户数据部分删除。**必须用 `with engine.begin() as conn:` 包裹所有 DELETE 和 INSERT 操作。**

### P2 — 建议修复，影响完整性/安全性

2. **`deletion_audit` 缺 `rows_count` 字段** — 与计划不符，审计记录缺失关键信息（删除行数）
3. **`deletion_tokens` 缺 `UNIQUE(user_id)` 约束** — 并发请求可能产生多个有效 token，违反"一次性"语义
4. **`deleted_tables` 不含 CASCADE 删除的表** — 审计记录仅含 4 张显式删除表，实际被级联删除的 10+ 张表未被记录
5. **`is_frozen` 与 `exclude_frozen_users` 判定标准不一致** — 前者同时检查 `status` 和 `frozen_at`，后者只看 `frozen_at`。异常数据下可能漏过
6. **`attachment-expiry.py` 中 `updated_at` 钳位逻辑** — 解析中的附件 `updated_at=now` 被跳过，设计合理，但代码中 `updated_at` 是 `onupdate=sa.func.now()`，每次 UPDATE 都会变化，可能比预期更快过期（需确认 `update()` 是否触发 `onupdate`）

### P3 — 锦上添花

7. **`extractUserHash` 默认返回 64 个零** — 审计日志失去识别价值，建议返回 null
8. **`MePage.tsx` 成功后对话框未关闭** — Kotlin 跳转失败时 UI 卡死
9. **`DELETE /me` 不区分 invalid_token 与 expired_token** — 难以事后分析
10. **`test_account_deletion.py` 的 `DELETE FROM users` 在 PG 会失败** — 测试在 SQLite 跑通是因 FK 默认禁用
11. **`deletion-request` 限流窗口 UX** — 1 分钟等待对误触用户略长

---

## 十、审查结论

**T-M5-04c 账号删除与数据保留：⚠️ 有条件通过**

### 通过的部分
- Schema 变更（users/attachments 部分）
- Deletion API（token 签发、消费、校验）
- 冻结期行为（push 拦截、pull 空响应、stats 排除、bootstrap 拦截）
- `attachment-expiry.py` cron 脚本
- Kotlin 端（AccountClient、bridge handler、本地清理）
- 客户端 UX（二次确认、错误提示、遮罩层关闭）
- 测试覆盖（API + 冻结行为 + 客户端 8 个用例）

### 阻塞上线的问题（必须修复）
1. **`account-deletion.py` 无事务保护（P1）**

### 建议修复（不阻塞上线，但影响完整性）
1. `deletion_audit` 补 `rows_count` 字段（P2）
2. `deletion_tokens` 加 `UNIQUE(user_id)` 约束（P2）
3. `deleted_tables` 包含 CASCADE 删除的表（P2）
4. 统一 `is_frozen` 判定标准（P2）

### 建议跟进（P3）
- `extractUserHash` 默认返回 null
- `MePage.tsx` 成功后关闭对话框
- `DELETE /me` 区分 invalid_token / expired_token
- `test_account_deletion.py` 修正以在 PG 上可运行
- 考虑 `deletion-request` 限流窗口 UX

**建议：修复 P1 后可部署到 staging 验收；P2 可在首个 patch 中修复；P3 可在后续迭代中跟进。**
