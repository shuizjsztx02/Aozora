# T-M3-01 第五轮代码审查（最终复审）

- **审查日期：** 2026-09-06
- **基线：** commit `ac455ad` + 工作区未提交改动（sync 模块 + 72 项同步测试）
- **范围：** `server/src/modules/sync/`（service.py / repository.py / schemas.py）+ `server/tests/test_sync.py`
- **门禁：** `pytest tests/test_sync.py -q` → **72 passed** ✅
- **方法：** 通读 sync 模块全量代码 + 关键边界行为实测 + 与前四轮审查交叉对照

## 一、前几轮修复复核

### Round 4 (NEW-1 / NEW-2) 复核

| # | 问题 | 复核结果 |
|---|---|---|
| NEW-1 | book 软删后其下 transaction / budget 仍可被 update，pull 与 bootstrap 视图不一致 | ✅ 已修复（见下文） |
| NEW-2 | category `parent_id` 自引用/循环引用未校验 | 挂账中（P3） |

### Round 3 (F-M3-1 / F-M3-2) 复核

| # | 问题 | 复核结果 |
|---|---|---|
| F-M3-1 | `get_entity` 不过滤软删/归档实体 | ✅ 已修复（见下文） |
| F-M3-2 | IntegrityError 整 batch 回滚 → savepoint 隔离 | ✅ 已修复（见下文） |
| F-M3-4 | 并发 push TOCTOU | 挂账中（P3） |
| F-M3-5 | `_serialize_entity` 与 schema 手动同步 | 挂账中（P3） |
| F-M3-6 / N-M3-3 | tx update `category_id: null` 语义未定义 | 挂账中（P3） |
| N-M3-2 | Budget period 格式未校验 | 挂账中（P3） |

**结论：** 所有已修复项均坐实，无回归。72 项测试全绿。

## 二、本轮新发现

### ✅ NEW-1 已修复

**修复内容：**

1. `get_transaction` 在 `include_deleted=False` 时添加 `Book.deleted_at.is_(None)` 过滤（`repository.py:210-224`）
2. `get_budget` 在 `include_deleted=False` 时添加 `Book.deleted_at.is_(None)` 过滤（`repository.py:226-240`）
3. Pull 路径使用 `include_deleted=True` 来 reconstruct 历史（`service.py:466-468`）

**行为变化：**

- 原行为：book 软删后，其下 transaction / budget 仍可被 update → 404，与 bootstrap 视图不一致
- 新行为：book 软删后，其下 transaction / budget 的 update 返回 404（符合预期）

**新增回归测试（2 项）：**

- ✅ `test_update_tx_under_soft_deleted_book_rejected`
- ✅ `test_update_budget_under_soft_deleted_book_rejected`

**验证结果：**

```bash
$ pytest tests/test_sync.py::TestSoftDeleteConsistency::test_update_tx_under_soft_deleted_book_rejected -v
============================= 1 passed =============================
$ pytest tests/test_sync.py::TestSoftDeleteConsistency::test_update_budget_under_soft_deleted_book_rejected -v
============================= 1 passed =============================
```

### ✅ F-M3-1 已修复（完整实现）

**修复内容：**

1. `get_book` 添加 `Book.deleted_at.is_(None)` 过滤（`repository.py:183-190`）
2. `get_category` 添加 `Category.archived_at.is_(None)` 过滤（`repository.py:192-199`）
3. `get_account` 添加 `Account.archived_at.is_(None)` 过滤（`repository.py:201-208`）
4. `get_transaction` 添加 `Transaction.deleted_at.is_(None)` + `Book.deleted_at.is_(None)` 过滤（`repository.py:210-224`）
5. `get_budget` 添加 `Budget.deleted_at.is_(None)` + `Book.deleted_at.is_(None)` 过滤（`repository.py:226-240`）
6. `_apply_delete` 添加幂等检查（`service.py:389-406`）：
   - 使用 `include_deleted=True` 查询实体
   - 检测已软删/归档状态，返回 `_Conflict(entity.version)` 而非重复覆盖

**新增回归测试（6 项）：**

- ✅ `test_update_soft_deleted_book_rejected`
- ✅ `test_update_archived_category_rejected`
- ✅ `test_update_archived_account_rejected`
- ✅ `test_update_soft_deleted_transaction_rejected`
- ✅ `test_double_delete_book_returns_conflict`
- ✅ `test_soft_deleted_book_rejects_sync_insert`（保留原有）

### ✅ F-M3-2 已修复

**修复内容：**

- 将每条 mutation 的 `_apply_mutation` 包裹在 `async with self.db.begin_nested()` 中（`service.py:70`）
- `IntegrityError` 触发 savepoint 回滚（只撤销该 mutation），标记为 `status="conflict"` 后 `continue`（`service.py:80-93`）
- 与 `_Conflict` 的处理路径对齐

**行为变化：**

- 原行为：`[insert(shared_id), insert(shared_id)]` → 409，DB 里 0 条记录
- 新行为：`[insert(shared_id), insert(shared_id)]` → 200，`results[0].applied + results[1].conflict`

**新增回归测试（1 项）：**

- ✅ `test_integrity_error_preserves_prior_mutations` — batch 中 [有效, 冲突PK, 有效] 三段，确认第一段和第三段都 applied

### ✅ T-M3-03: Bootstrap checksum 已实现

**修复内容：**

- 添加 `compute_bootstrap_checksum` 函数（`service.py:663-700`）
  - 按 id 排序实体列表
  - 每个实体 dict 按 key 排序
  - JSON 序列化使用 `separators=(",", ":")` 和 `sort_keys=True`
  - 拼接顺序：books|categories|accounts|transactions|budgets|cursor
  - SHA-256 哈希，返回 64 字符小写 hex
- `BootstrapResponse` 添加 `checksum: str = ""` 字段（`schemas.py:89`）
- `bootstrap` 方法计算并返回 checksum（`service.py:506-513`）

**新增测试（3 项）：**

- ✅ `test_bootstrap_checksum_is_deterministic` — 相同数据产生相同 checksum
- ✅ `test_bootstrap_checksum_changes_on_mutation` — 新 mutation 改变 checksum
- ✅ `test_bootstrap_checksum_matches_local_computation` — 客户端可重算相同 checksum

### 🟢 P3 级（可挂账）

#### NEW-2: category `parent_id` 自引用/循环引用未校验

- **位置：** `service.py:193-198`（insert）/ `service.py:315-319`（update）
- **问题：** category 可以将自己设为 parent（`parent_id == entity_id`），或 A→B→A 形成循环
- **影响：** 低（直接 budgets/transactions API 也未处理；前端通常不展示这种结构；但可能导致前端无限递归）
- **修复方向：** 检查 `parent_id != entity_id`；循环检测可选（DFS/BFS）
- **严重性：** P3（可挂账，生产部署前处理）

#### 已有挂账问题（未变）

| # | 问题 | 严重性 |
|---|---|---|
| F-M3-3 | Pull N+1 查询 | 🟡 P2（生产部署前优化）|
| F-M3-4 | 并发 push 同一 mutation_id TOCTOU | 🟢 P3 |
| F-M3-5 | `_serialize_entity` 与 schema 手动同步 | 🟢 P3 |
| F-M3-6 / N-M3-3 | tx update `category_id: null` 语义未定义 | 🟢 P3 |
| N-M3-2 | Budget period 格式未校验 | 🟢 P3 |

## 三、测试覆盖盘点

### 当前测试统计

- **总测试数：** 72 项（同步模块）
- **通过率：** 100% ✅
- **新增测试：** 12 项（NEW-1: 2 项 + F-M3-1: 6 项 + F-M3-2: 1 项 + T-M3-03: 3 项）

### 测试分布

| 测试类 | 项数 | 覆盖点 |
|---|---|---|
| TestPushInsert | 5 | 5 种 entity 的 insert |
| TestPushUpdateDelete | 5 | tx update / tx delete / budget delete / account archive / category archive |
| TestPushIdempotency | 2 | 重复 mutation_id / 独立 mutation |
| TestPushConflict | 3 | 版本冲突 / IntegrityError savepoint / 冲突前 mutation 仍提交 |
| TestPushValidation | 5 | 空 batch / 超大批 / 非法 entity_type / 缺 version / 跨用户 |
| TestPushAuth | 1 | 无 token → 401 |
| TestPull | 6 | 空 / 返回 changes / 分页 / delete null / 跨用户隔离 / 断线重连 |
| TestBootstrap | 7 | 空 / 全量 / 排除软删 / 跨用户隔离 + 3 项 checksum 测试 |
| TestIntegration | 3 | 冲突后重试 / push-then-pull / 全生命周期 |
| TestPayloadValidation | 9 | amount/note/source/biz_date/type/UUID/缺字段/重复 entity_id/100 项 batch |
| TestPullBoundaries | 2 | limit=0 拒 / limit=500 收 |
| TestAccountOwnership | 2 | 跨用户 account（insert + update）|
| TestSerialization | 2 | book deleted_at / occurred_at 不可变 |
| TestExtendedValidation | 8 | name 长度 / kind / parent_id 跨用户 / budget_type / category name / book name |
| TestSoftDeleteConsistency | 8 | 软删 book 拒 insert + 软删 book/category/account/tx 的 update 拒 + 重复 delete conflict + 软删 book 下 tx/budget update 拒 |
| TestBudgetCategoryOwnership | 2 | 跨用户 category_id（insert + update）|
| **合计** | **72** | |

### 测试缺口

| 缺口 | 关联问题 | 优先级 |
|---|---|---|
| category `parent_id == entity_id` 自引用拒绝 | NEW-2 | 🟢 P3 |
| `applyServerChanges` 细粒度单元测试（T-M3-02 客户端映射）| T-M3-02 review | 🟡 P2 |
| `ServerChange.toTransaction/toBook/...` 字段映射 | T-M3-02 review | 🟡 P2 |
| `parsePullResponse` 对 `version <= 0 && op != DELETE` 过滤 | T-M3-02 review | 🟢 P3 |

## 四、安全评审

### ✅ 安全措施（保持）

1. **Bearer token 认证**：所有 sync 端点需 `get_current_user`。
2. **跨用户隔离**：所有 `get_entity` 查询包含 `owner_id` 过滤；ownership 辅助方法校验 book/category/account 归属。
3. **幂等保护**：`mutation_id` UUID PK，重复 push 返回 `duplicate`。
4. **版本乐观锁**：`version` 校验防止 lost-update。
5. **跨用户 FK 引用校验**：transaction insert/update 校验 book/category/account 归属；budget 校验 book/category 归属。
6. **软删/归档过滤**：所有 `get_entity` 方法在 `include_deleted=False` 时过滤软删/归档实体及其父级。

### ✅ 安全缺口（已关闭）

1. **NEW-1**：✅ 已修复（`get_transaction` / `get_budget` 加 `Book.deleted_at` 过滤）
2. **F-M3-1 残留**：✅ 已修复（所有 `get_entity` 方法统一过滤）

## 五、闸门检查清单

### 必做（上线阻塞）— 本轮无新增

前几轮 P1 闸门已全部满足。

### 强烈建议（上线前处理）

| # | 问题 | 严重性 | 状态 |
|---|---|---|---|
| F-M3-3 | Pull N+1 查询 | 🟡 P2 | 挂账（生产部署前优化）|

### 可挂账

| # | 问题 | 严重性 |
|---|---|---|
| NEW-2 | category `parent_id` 自引用/循环引用未校验 | 🟢 P3 |
| F-M3-4 | 并发 push 同一 mutation_id TOCTOU | 🟢 P3 |
| F-M3-5 | `_serialize_entity` 与 schema 手动同步 | 🟢 P3 |
| F-M3-6 / N-M3-3 | tx update `category_id: null` 语义未定义 | 🟢 P3 |
| N-M3-2 | Budget period 格式校验 | 🟢 P3 |

## 六、结论

### 架构设计

✅ 分层清晰，幂等 + 版本锁 + 归属校验核心链路完整。

### 核心功能

✅ push/pull/bootstrap 三端点功能正确（正常路径 + 边界路径）。

### P1 级问题

✅ 前几轮 1 项（F-M3-1）已修复并复核通过。本轮无新增。

### P2 级问题

✅ NEW-1 已修复（book 软删后其下 transaction / budget 的 update 拒绝）。
✅ F-M3-2（IntegrityError savepoint 隔离）已修复并复核通过。
⏸️ F-M3-3（N+1 查询）可推迟到生产部署前优化。

### P3 级问题

⚠️ NEW-2（category 自引用）保持挂账。
✅ 4 项前几轮挂账问题保持。

### 测试覆盖

✅ 72 项测试全绿（+12 新增回归测试）。
✅ 所有 P1/P2 修复均有对应回归测试覆盖。

### 最终判定

**✅ T-M3-01 通过。**

- **P1 闸门：** ✅ 全部满足（前几轮修复复核通过，本轮无新增 P1）
- **P2 建议：** ✅ NEW-1 已修复，F-M3-3 可推迟到生产部署前
- **可进入 T-M3-02：** ✅ 客户端同步模块不依赖挂账问题的修复

**建议：**

1. **T-M3-02 启动前**：无阻塞项
2. **生产部署前**：修复 F-M3-3（N+1 查询）+ NEW-2（category 自引用）+ F-M3-4/F-M3-5/F-M3-6/N-M3-2

---

## 附录 A：关键代码定位

| 文件 | 行号 | 关键点 |
|---|---|---|
| `service.py` | 69-93 | F-M3-2 savepoint 隔离 ✅ |
| `service.py` | 229-236 | tx insert 校验 book/category/account 归属 ✅ |
| `service.py` | 261-266 | budget insert 校验 book/category 归属 ✅ |
| `service.py` | 330-363 | tx/budget update — **包含** book 软删校验（通过 get_entity 间接） ✅ |
| `service.py` | 389-406 | F-M3-1 _apply_delete 幂等检查 ✅ |
| `service.py` | 466-468 | pull 使用 include_deleted=True ✅ |
| `service.py` | 506-513 | T-M3-03 bootstrap checksum ✅ |
| `service.py` | 663-700 | compute_bootstrap_checksum 实现 ✅ |
| `repository.py` | 183-240 | get_book/category/account/transaction/budget 统一过滤 ✅ |
| `repository.py` | 210-224 | get_transaction 过滤 Book.deleted_at（NEW-1 修复） ✅ |
| `repository.py` | 226-240 | get_budget 过滤 Book.deleted_at（NEW-1 修复） ✅ |
| `schemas.py` | 89 | BootstrapResponse.checksum 字段 ✅ |

## 附录 B：测试运行记录

```
$ cd server && pytest tests/test_sync.py -q
========================================================================
72 passed in 4.27s
========================================================================
```

## 附录 C：修复时间线

| 日期 | 修复项 | 提交/状态 |
|---|---|---|
| 2026-09-05 | F-M3-1 (get_entity 软删过滤) | 工作区未提交 |
| 2026-09-05 | F-M3-2 (IntegrityError savepoint) | 工作区未提交 |
| 2026-09-05 | F-M3-1 幂等检查 (_apply_delete) | 工作区未提交 |
| 2026-09-06 | NEW-1 (book 软删后 tx/budget update) | 工作区未提交 |
| 2026-09-06 | T-M3-03 (bootstrap checksum) | 工作区未提交 |
