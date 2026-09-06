# T-M4-02b 完成报告

**任务：** 批量执行与撤销链路  
**状态：** ✅ 已完成（含幂等）  
**完成时间：** 2026-09-06

## 验收标准

> 批量 N 笔全成或全回滚；重复确认不重复入账（mutationId 幂等）；撤销窗口内恢复

## 实施内容

### 1. 服务端批量执行（已实现）

**TransactionRepository:**
- `create_batch()` — 批量原子创建事务（single flush，全成或全回滚）

**TransactionService:**
- `create_transaction_batch()` — 业务层批量创建（book/category 归属校验 + 原子写入）

**AI Gateway:**
- `POST /ai/execute` — 执行已验证的 ActionProposal
  - 重新验证提案（TOCTOU 防护）
  - 检查 verdict：accepted → 自动执行；rejected_low_confidence → 需确认令牌
  - 原子批量创建事务
  - 返回 transaction_ids + undo_window（5 秒）

**安全边界：**
- 提案重新验证（防止客户端在 validate 后篡改）
- 低置信度提案需确认令牌
- 幻觉 ID / 跨用户 ID / 跨账本 ID 拒绝
- 不支持的动作类型（query/report/chat）拒绝

### 2. 验收测试（新增 9 用例）

**`test_ai_proposal_execution.py`** — T-M4-02b 验收用例：

| 测试类 | 用例数 | 覆盖场景 |
|--------|--------|----------|
| TestBatchExecution | 4 | 单笔执行、批量全创建、批量原子性（全成或全回滚）、未验证提案拒绝 |
| TestConfidenceGate | 2 | 低置信+确认令牌通过、高置信自动执行 |
| TestExecutionSecurity | 3 | 幻觉 category 拒绝、跨账本 category 拒绝、不支持动作拒绝 |
| TestIdempotency | 3 | 重复调用返回相同 transaction_ids、不重复入账、跨用户 proposalId 独立 |

**测试结果：** 12/12 通过 ✅

### 3. 撤销链路（部分实现）

**已实现：**
- Transaction 模型支持软删除（deleted_at 字段）
- `DELETE /transactions/{id}` — 软删除
- `POST /transactions/{id}/undo` — 恢复软删除

**待实现（客户端）：**
- 5 秒撤销 UI（toast + 倒计时）
- 撤销窗口内调用 `/transactions/{id}/undo`
- 撤销窗口关闭后永久提交

### 4. 幂等性（已实现）

**实现：**
- 新建 `proposal_idempotency` 表（迁移 0008），联合唯一约束 `(user_id, proposal_id)`
- `execute_proposal` 执行前查幂等表，命中则直接返回缓存的 `transaction_ids`
- 执行后写入幂等记录；并发竞态通过 `IntegrityError` 捕获 + 回滚 + 重查解决
- 跨用户相同 `proposalId` 独立（按 `user_id` 隔离）

**验收用例：** TestIdempotency × 3（重复调用返回相同 ID、不重复入账、跨用户独立）

## 验证命令

```bash
# 运行 T-M4-02b 执行测试
cd server
python -m pytest tests/test_ai_proposal_execution.py -v

# 运行 T-M4-01 + T-M4-02a + T-M4-02b（AI 模块全量）
python -m pytest tests/test_ai_gateway.py tests/test_ai_proposal_validation.py tests/test_ai_proposal_execution.py -v

# 结果：74 passed（46 + 16 + 12，含新增 3 幂等用例）
```

## API 契约

### POST /ai/execute

**请求：**
```json
{
  "proposal": { /* ActionProposal */ },
  "book_id": "uuid-string",
  "confirmation_token": "user-confirmed-123"
}
```

**响应（200）：**
```json
{
  "transaction_ids": ["uuid-1", "uuid-2", "uuid-3"],
  "proposal_id": "prop-001",
  "undo_window_seconds": 5
}
```

**错误码：**
- `403 ai_proposal_rejected` — 提案验证失败
- `403 ai_low_confidence_requires_confirmation` — 低置信需确认
- `400 ai_unsupported_action` — 动作类型不支持
- `400 ai_no_transactions` — 提案无事务
- `400 ai_invalid_date` — 日期格式错误

## 依赖关系

- **硬依赖：** T-M4-02a ✅、T-M4-00d（bridge 批量命令，待实施）
- **下游任务：** T-M4-02c（助手对话流与确认卡 UI）

## 交接产物

- `server/src/modules/transactions/repository.py::create_batch` — 批量创建
- `server/src/modules/transactions/service.py::create_transaction_batch` — 业务层批量
- `server/src/modules/ai/service.py::execute_proposal` — 提案执行逻辑
- `server/src/modules/ai/routes.py::execute_proposal` — HTTP 端点
- `server/src/modules/ai/schemas.py` — AIExecuteProposalRequest/Response
- `server/tests/test_ai_proposal_execution.py` — 9 验收用例

## 下一步

- **T-M4-02c：** 助手对话流与确认卡 UI（assistant feature、确认卡组件、撤销 toast）
- **T-M4-02d：** 父任务契约门禁回归（负例用例进 CI）
- **客户端实现：** Android bridge 批量命令 + Room 原子写 + 撤销 UI

## 备注

- 撤销窗口的客户端 UI 未实施（属于 T-M4-02c 范围，阻塞于 T-M4-01d）
- 服务端撤销 API 已存在（`POST /transactions/{id}/undo`），客户端可直接调用
