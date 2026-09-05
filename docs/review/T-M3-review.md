# T-M3-01 第二轮代码审查（复验）

- **审查日期：** 2026-09-05
- **基线：** commit `9d9ff37` + 工作区未提交改动（sync 模块、budget 模型、0006 迁移、59 项同步测试）
- **范围：** `server/src/modules/sync/`、`server/src/models/{budget.py,mutation.py,sync.py}`、`server/src/migrations/versions/0006_budget_deleted_at.py`、`server/tests/test_sync.py`、`server/tests/test_migrations.py`
- **门禁：** `pytest tests/ -q` → **212 passed, 1 skipped, 1 error**（error 为 Windows 下 `test_startup_cleanup_removes_stale_files` 的 pytest tmp_path PermissionError，与同步无关，已在前几轮确认）；`pytest tests/test_sync.py -q` → **59 passed**
- **方法：** 全量阅读 `service.py` / `repository.py` / `schemas.py` / `routes.py` + 与 T-M2 review round2 遗留问题交叉对照 + 关键断言实测

## 一、上轮修复复核

上一轮审查（引用于测试注释 `review T-M3-01 #1…#5`）识别的 5 类问题，本轮逐项复核：

| # | 上轮问题 | 复核结论 |
|---|---|---|
| 1 | Book 序列化字段错误（用了 `archived_at`，应为 `deleted_at`）| ✅ `service.py:528-539` 已改为 `deleted_at`；`TestSerialization.test_pull_book_entity_has_deleted_at` 覆盖 |
| 2 | Transaction insert 不验证 `account_id` 归属 | ✅ `service.py:221-222` 加 `_verify_account_ownership`；`TestAccountOwnership` 2 项覆盖 |
| 3 | 缺少 payload 校验（amount_cents / note / name / source / enum）| ✅ `_validate_amount_cents`、`_validate_note`、`_validate_name`、`_TX_SOURCE_VALUES`、`CategoryKind()`、`BudgetType()` 全部落地；`TestPayloadValidation` 9 项 + `TestExtendedValidation` 8 项覆盖 |
| 4 | `occurred_at` 可被 sync update 篡改 | ✅ update 分支不处理 `occurred_at`；`TestSerialization.test_update_transaction_occurred_at_is_immutable` 覆盖 |
| 5 | Pull limit=0 被接受 | ✅ `routes.py:35` 已改为 `ge=1, le=500`；`TestPullBoundaries.test_pull_limit_zero_rejected` 覆盖 |

附带加固：
- `_Conflict` 之外新增 `IntegrityError` → 409、`(KeyError, ValueError, TypeError)` → 422（`service.py:73-85`），避免 DB / 解析异常冒泡为 500。`test_insert_duplicate_entity_id_conflict` / `test_insert_transaction_invalid_uuid_rejected` / `test_insert_transaction_missing_required_field_rejected` 覆盖。
- `_verify_book_ownership` 直接查 DB 并加 `Book.deleted_at.is_(None)`（N-01 跟进），`TestSoftDeleteConsistency` 覆盖。
- Category insert 的 `parent_id` 现在也做 `_verify_category_ownership`（`service.py:184`），`TestExtendedValidation.test_insert_category_parent_id_cross_user_rejected` 覆盖。
- Category/Account/Book 的 name / icon 长度校验补全。
- `Budget` 新增 `deleted_at` 列（migration 0006）、两处 partial unique index 加 `deleted_at IS NULL` 过滤，`_apply_delete` 对 budget 走 `deleted_at` 分支，bootstrap / pull 相应排除；`test_delete_budget` 一项端到端覆盖。

**结论：** 上轮 5 项修复全部坐实，门禁 30 → 59（+29 项同步测试，含本轮 N-M3-1 回归 2 项），未发现回归。

## 二、本轮新发现

### 🟡 P2 级

#### N-M3-1 Budget insert/update 不验证 `category_id` 归属 → **已修复**

- **位置：** `service.py:247-265`（insert）、`service.py:341-348`（update）
- **问题：** sync push 创建 / 更新 budget 时，对 `payload.category_id` 只做了 UUID 解析，**未调用 `_verify_category_ownership`**。客户端可以在自己账本里创建一条预算，但 `category_id` 指向别的用户的分类。
- **复现：**
  1. User A 拥有 Book A；User B 在 Book A 中有一条 Category B（`owner_id = B`）
  2. User A push `{entity_type: budget, operation: insert, payload: {book_id: A, category_id: B_cat, ...}}`
  3. 修复前：服务端返回 `applied` — 跨用户分类引用被写入
  4. 修复后：服务端返回 404 — `Category not found`
- **对比：** 直接 budgets API（`budgets/service.py:71-72`）对 `category_id` 做了归属校验；sync 漏掉。
- **修复：** insert 与 update 两个分支各加 `if category_id is not None: await self._verify_category_ownership(category_id, user_id)`。新增 `TestBudgetCategoryOwnership` 2 项（insert + update）回归覆盖。

### 🟢 P3 级（可挂账，生产部署前处理）

#### N-M3-2 Budget `period` 未做格式 / 长度校验

- **位置：** `service.py:247-265`
- **问题：** `period` 直接写入 `String(10)` 列。超长值在 PostgreSQL 触发 `DataError`（不在现有 `IntegrityError / KeyError / ValueError / TypeError` 捕获范围）→ 500。直接 budgets API 也有相同缺口（T-M2 遗留），属于一致性缺口而非 sync 独有。
- **修复方向：** 与 T-M2 的 period 校验合并处理（N-02 跟进），在 route 或 helper 里统一解析 `YYYY-MM` / `YYYY`。

#### N-M3-3 Transaction update 对 `category_id: null` 静默忽略

- **位置：** `service.py:320-323`
- **问题：** `if "category_id" in payload and payload["category_id"] is not None:` 当客户端发送 `category_id: null` 时既不报错也不清空（Transaction.category_id 实际为 NOT NULL）。客户端会误以为字段被更新。
- **修复方向：** 显式 `raise AppException(422, ..., "category_id cannot be null")`，或在更新前校验。

## 三、测试覆盖盘点

`test_sync.py` 当前 59 项，按类分布如下：

| 类 | 项数 | 覆盖点 |
|---|---|---|
| TestPushInsert | 5 | 5 种 entity 的 insert |
| TestPushUpdateDelete | 3 | tx update / tx delete / budget delete（含 bootstrap + pull 校验）|
| TestPushIdempotency | 2 | 重复 mutation_id / 不同 mutation_id 独立 |
| TestPushConflict | 2 | 版本冲突停处理 / 冲突前 mutation 仍提交 |
| TestPushValidation | 5 | 空 batch / 超大批 / 非法 entity_type / 缺 version / 跨用户 404 |
| TestPushAuth | 1 | 无 token → 401 |
| TestPull | 6 | 空 / 返回 changes / 分页 / delete null / 跨用户隔离 / 断线重连 |
| TestBootstrap | 4 | 空 / 全量 / 排除软删 / 跨用户隔离 |
| TestIntegration | 3 | 冲突后重试 / push-then-pull 一致性 / 全生命周期 |
| TestPayloadValidation | 9 | amount/note/source/biz_date/type/UUID/缺字段/重复 entity_id/100 项 batch |
| TestPullBoundaries | 2 | limit=0 拒 / limit=500 收 |
| TestAccountOwnership | 2 | 跨用户 account（insert + update）|
| TestSerialization | 2 | book deleted_at / occurred_at 不可变 |
| TestExtendedValidation | 8 | name 长度 / kind / parent_id 跨用户 / budget_type / category name / book name |
| TestSoftDeleteConsistency | 1 | 软删 book 拒写 |
| TestBudgetCategoryOwnership | 2 | 跨用户 category_id（insert + update）|
| **合计** | **59** | |

## 四、结论与闸门

- **必做（闸门）：** N-M3-1（budget category_id 归属校验）— ✅ 已修复 + 2 项回归测试覆盖（`TestBudgetCategoryOwnership`）。
- **可挂账：** N-M3-2、N-M3-3（P3，与 T-M2 既有缺口合并处理，生产部署前完成）。
- **未关闭项（与 T-M2 共享）：**
  - N-05 + N-06 导出文件安全（生产部署前）
  - N-07 导出异步化 / 流式化（生产部署前）
  - N-08 / N-10 Category 重命名 409、速率限制键收敛（生产部署前）
  - N-09 / N-13~N-30 余下项：挂账并入生产前加固
- **测试：** 212 passed（+2 回归），同步测试 59 项。
- **结论：** T-M3-01 P2 级问题全部闭环，可进入 T-M3-02 Android 同步引擎。

## 附录：N-M3-1 复现脚本

```python
# 在 server/ 目录下运行
# python -c "..."  (见本轮审查时的实测命令)
# 结果：status='applied'，确认漏洞存在
```
