# T-M3-01 第四轮代码审查（独立复审）

- **审查日期：** 2026-09-06
- **基线：** commit `ac455ad`（当前工作区）
- **范围：** `server/src/modules/sync/`（service.py / repository.py / schemas.py / routes.py）+ `server/src/models/{ledger.py, budget.py, base.py}` + `server/tests/test_sync.py`
- **门禁：** `pytest tests/test_sync.py -q` → **68 passed** ✅
- **方法：** 通读 sync 模块全量代码 + 关键边界行为实测（构造"book 软删后 update 其下 transaction / budget"等多设备场景）

## 一、前几轮修复复核

### Round 1 / Round 2 / Round 3 已修复项复核

| # | 问题 | 复核结果 |
|---|---|---|
| round1-1 | Book 序列化字段错误 | ✅ `service.py:602` 输出 `deleted_at`（非 `archived_at`）|
| round1-2 | Transaction insert 不验证 account_id 归属 | ✅ `service.py:235-236` 已加 `_verify_account_ownership` |
| round1-3 | 缺少 payload 校验 | ✅ `_validate_amount_cents` / `_validate_note` / `_validate_name` / `_TX_SOURCE_VALUES` 全部落地 |
| round1-4 | `occurred_at` 可被 update 篡改 | ✅ `service.py:350-353` 注释明确不可更新，无赋值路径 |
| round1-5 | Pull limit=0 被接受 | ✅ `routes.py:35` 已改为 `ge=1, le=500` |
| N-M3-1 | Budget insert/update 不验证 category_id 归属 | ✅ `service.py:264-266` / `service.py:359-363` 已加校验 |
| F-M3-1 | `get_entity` 不过滤软删/归档实体 | ✅ `repository.py:192-208` 已加 `archived_at` 过滤；`service.py:399-406` 已加幂等检查 |
| F-M3-2 | IntegrityError 整 batch 回滚 | ✅ `service.py:69-93` 已加 savepoint 隔离 |
| F-M3-4 | 并发 push TOCTOU | 挂账中（P3）|
| F-M3-5 | `_serialize_entity` 与 schema 手动同步 | 挂账中（P3）|
| F-M3-6 / N-M3-3 | tx update `category_id: null` 语义未定义 | 挂账中（P3）|
| N-M3-2 | Budget period 格式未校验 | 挂账中（P3）|

**结论：** 所有已修复项均坐实，无回归。68 项测试全绿。

## 二、本轮新发现

### 🔴 P1 级（上线阻塞）— 无

### 🟡 P2 级

#### NEW-1: book 软删后其下 transaction / budget 仍可被 update，pull 与 bootstrap 视图不一致

**位置：**
- `service.py:296` — `_apply_update` 对 transaction / budget 不验证所属 book 是否被软删
- `repository.py:210-220` — `get_transaction` 在 `include_deleted=False` 模式下不过滤 `Book.deleted_at`
- `repository.py:222-232` — `get_budget` 同上

**问题：**

1. **insert** transaction / budget 时，`_verify_book_ownership` 显式过滤 `Book.deleted_at.is_(None)`（`service.py:429-437`），正确拒绝软删 book 的引用 ✅
2. **update** transaction / budget 时，`_apply_update` 调用 `repo.get_entity(...)` 走 `get_transaction` / `get_budget`，只按 `Book.owner_id` 过滤，**不过滤** `Book.deleted_at` → 软删 book 下的数据仍可被 update ❌
3. **bootstrap** 排除软删 book 下的 transactions / budgets（`repository.py:156-170` 仅查 `book_ids` 来自未删 book）
4. **pull** 路径下，`get_entity(..., include_deleted=True)` 能找到软删 book 下的 transaction / budget，其 change 仍被返回（带完整 entity 数据）

**实测验证：**

```python
# 构造：insert tx → soft-delete book → update tx
insert tx: status=applied, version=1
update tx after book soft-deleted: status=applied  # ❌ 应为 404 或 conflict
pull: op=insert, entity_type=transaction, has_entity=True   # bootstrap 不含
pull: op=update, entity_type=transaction, has_entity=True   # bootstrap 不含
bootstrap: books=0, txs=0                                   # 与 pull 不一致
```

budget 同理（实测结果相同）。

**影响：**
- 多设备场景：device A 软删 book，device B 晚到的 update 仍能修改 book 下 transaction / budget，与"软删 book 应被视为不存在"的语义冲突
- bootstrap 视图 A（不含）与 pull 视图 B（含）不一致
- 不是安全问题（用户只操作自己数据），是数据一致性问题

**修复方向：**

- 方案一（推荐）：`get_transaction` / `get_budget` 在 `include_deleted=False` 时加 `Book.deleted_at.is_(None)` 过滤
  ```python
  # repository.py:210-220
  conditions = [Transaction.id == tx_id, Book.owner_id == user_id]
  if not include_deleted:
      conditions.append(Transaction.deleted_at.is_(None))
      conditions.append(Book.deleted_at.is_(None))  # ✅ 新增
  ```
- 方案二：`_apply_update` 对 transaction / budget 额外调用 `_verify_book_ownership(payload_book_id or entity.book_id, user_id)`
- 两个方案可以叠加

**新增回归测试（建议）：**
- `test_update_transaction_in_soft_deleted_book_rejected`
- `test_update_budget_in_soft_deleted_book_rejected`
- `test_pull_excludes_changes_in_soft_deleted_book`（可选，更严格）

### 🟢 P3 级

#### NEW-2: category `parent_id` 自引用/循环引用未校验

- **位置：** `service.py:315-319`（update）/ `service.py:193-198`（insert）
- **问题：** category 可以将自己设为 parent（`parent_id == entity_id`），或 A→B→A 形成循环
- **影响：** 低（直接 budgets/transactions API 也未处理；前端通常不展示这种结构；但可能导致前端无限递归）
- **修复方向：** 检查 `parent_id != entity_id`；循环检测可选（DFS/BFS）

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
- **总测试数：** 68 项（同步模块）
- **通过率：** 100% ✅
- **测试分布：** 与前一轮相同（见 T-M3-01-review.md round 3 附表）

### 测试缺口

| 缺口 | 关联问题 | 优先级 |
|---|---|---|
| 软删 book 下 transaction / budget 的 update 拒绝 | **NEW-1** | 🟡 P2 |
| pull 排除软删 book 下的 changes | **NEW-1** | 🟡 P2 |
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

### 🟡 安全缺口

1. **NEW-1**：软删 book 下的 transaction / budget 仍可被 update（数据一致性问题，非直接越权）。
2. **F-M3-1 残留**：`get_transaction` / `get_budget` 在 `include_deleted=True` 模式下也拉到了软删 book 下的数据（pull 路径），与 bootstrap 视图不一致。

## 五、闸门检查清单

### 必做（上线阻塞）— 本轮无新增

前几轮 P1 闸门已全部满足。

### 强烈建议（上线前处理）

| # | 问题 | 严重性 | 状态 |
|---|---|---|---|
| NEW-1 | book 软删后其下 transaction / budget 仍可 update + pull/bootstrap 不一致 | 🟡 P2 | 待修复 |
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
✅ push/pull/bootstrap 三端点功能正确（正常路径）。

### P1 级问题
✅ 前几轮 1 项（F-M3-1）已修复并复核通过。本轮无新增。

### P2 级问题
⚠️ **NEW-1 新发现**：book 软删后其下 transaction / budget 仍可 update，且 pull/bootstrap 视图不一致。建议上线前修复。
✅ F-M3-2（IntegrityError savepoint 隔离）已修复并复核通过。
⏸️ F-M3-3（N+1 查询）可推迟到生产部署前优化。

### P3 级问题
⚠️ **NEW-2 新发现**：category `parent_id` 自引用/循环引用未校验。
✅ 4 项前几轮挂账问题保持。

### 测试覆盖
✅ 68 项测试全绿。
⚠️ 缺口：软删 book 下 transaction / budget 的 update 场景（NEW-1）。

### 最终判定

**T-M3-01 有条件通过。**

- **P1 闸门：** ✅ 全部满足（前几轮修复复核通过，本轮无新增 P1）
- **P2 建议：** ⚠️ NEW-1 建议上线前修复（涉及数据一致性，多设备场景可能触发）
- **可进入 T-M3-02：** ✅ 客户端同步模块不依赖 NEW-1 的修复（客户端不感知 book 软删后的 update 行为）

**建议：**
1. **T-M3-02 启动前**：修复 NEW-1（`get_transaction` / `get_budget` 加 `Book.deleted_at` 过滤），补齐 2 项回归测试
2. **生产部署前**：修复 F-M3-3（N+1 查询）+ NEW-2（category 自引用）+ F-M3-4/F-M3-5/F-M3-6/N-M3-2

---

## 附录 A：关键代码定位

| 文件 | 行号 | 关键点 |
|---|---|---|
| `service.py` | 69-93 | F-M3-2 savepoint 隔离 ✅ |
| `service.py` | 229-236 | tx insert 校验 book/category/account 归属 ✅ |
| `service.py` | 261-266 | budget insert 校验 book/category 归属 ✅ |
| `service.py` | 330-363 | tx/budget update — **缺少** book 软删校验 ⚠️ |
| `service.py` | 399-406 | F-M3-1 _apply_delete 幂等检查 ✅ |
| `service.py` | 429-437 | _verify_book_ownership 过滤 deleted_at ✅ |
| `repository.py` | 192-208 | get_category/get_account 过滤 archived_at ✅ |
| `repository.py` | 210-232 | get_transaction/get_budget — **缺少** Book.deleted_at 过滤 ⚠️ |

## 附录 B：测试运行记录

```
$ cd server && pytest tests/test_sync.py -q
....................................................................     [100%]
68 passed in 4.44s
```
