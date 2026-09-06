# T-M4-02a 完成报告

**任务：** Confidence 策略与执行链校验  
**状态：** ✅ 已完成  
**完成时间：** 2026-09-06

## 验收标准

> 负例用例：幻觉数字 / 无权限分类 / 低置信 → 零写库

## 实施内容

### 1. 服务端验证逻辑（已实现）

- **`validator.py`** — Schema 校验（JSON Schema Draft-07 + format: date 完整公历日期断言）
- **`context.py`** — `ProposalValidationContext` 从 DB 加载（owner_id + book_id 归属校验）
- **`service.py` `validate_proposal`** — 三层验证链：
  1. Schema 校验（形状/金额/日期）
  2. 幻觉 ID 校验（categoryId/accountId 不在上下文 → rejected_hallucination）
  3. 低置信拒绝（confidence < 0.9 → rejected_low_confidence）
- **`routes.py` `/ai/validate`** — HTTP 端点，Bearer 鉴权
- **`schemas.py`** — 请求/响应 schema（含 verdict 枚举：accepted/rejected_schema/rejected_hallucination/rejected_low_confidence）

### 2. 验收测试（新增 16 用例）

**`test_ai_proposal_validation.py`** — T-M4-02a 验收用例：

| 测试类 | 用例数 | 覆盖场景 |
|--------|--------|----------|
| TestSchemaValidation | 4 | 合法提案通过、非法 schema 拒绝、无效日期拒绝、负金额拒绝 |
| TestHallucinationRejection | 4 | 未授权 category 拒绝、未授权 account 拒绝、跨用户 category 拒绝、跨账本 category 拒绝 |
| TestConfidenceGate | 3 | 低置信需确认（<0.9）、高置信通过（>0.9）、阈值边界（=0.9 通过） |
| TestContextFromDB | 3 | 上下文从 DB 加载（非模型输出）、空上下文拒绝所有 ID、无效 book_id 拒绝 |
| TestBatchValidation | 2 | 批量全合法通过、批量中一笔幻觉整批拒绝 |

**测试结果：** 16/16 通过 ✅

### 3. Bug 修复

- **`fact_pack.py`** — 修复 2 处 SQLAlchemy 2.0 API 错误：
  - `func.case(...)` → `case(...)`（line 283, 460）
  - 原因：SQLAlchemy 2.0 不再支持 `func.case()` 的 `else_=` 关键字参数
- **`service.py`** — 修复 `BudgetFact` 属性访问错误（line 432-435）：
  - `b.budget.amount_cents` → `b.amount_cents`
  - 原因：walrus 操作符 `b := fact_pack.budget` 已解包，不需要再访问 `.budget`

## 验证命令

```bash
# 运行 T-M4-02a 验收测试
cd server
python -m pytest tests/test_ai_proposal_validation.py -v

# 运行 T-M4-01 + T-M4-02a + T-M4-03a（AI 模块全量）
python -m pytest tests/test_ai_gateway.py tests/test_ai_proposal_validation.py tests/test_fact_pack.py -v

# 结果：75 passed（46 + 16 + 13）
```

## 安全边界

1. **上下文来源：** `ProposalValidationContext` 从 DB 加载，不信任模型输出
2. **归属校验：** Category 按 (owner_id AND book_id) 校验，Account 按 (owner_id) 校验
3. **零写库：** 非法 JSON / 幻觉 ID / 低置信提案 → verdict != "accepted"，客户端不得执行
4. **阈值硬编码：** CONFIDENCE_THRESHOLD = 0.9（与本地 NLU 对齐，防止客户端绕过）

## 依赖关系

- **硬依赖：** T-M4-01b（Prompt registry 与服务端提案校验）✅、T-M2-04（账本核心 API）✅
- **下游任务：** T-M4-02b（批量执行与撤销链路）、T-M4-02c（助手对话流与确认卡 UI）

## 交接产物

- `server/src/modules/ai/validator.py` — Schema 校验器
- `server/src/modules/ai/context.py` — DB 上下文加载
- `server/src/modules/ai/service.py::validate_proposal` — 三层验证逻辑
- `server/src/modules/ai/routes.py::validate_proposal` — HTTP 端点
- `server/src/modules/ai/schemas.py` — 请求/响应 schema
- `server/tests/test_ai_proposal_validation.py` — 16 验收用例

## 下一步

- **T-M4-02b：** 批量执行与撤销链路（add_tx_batch 契约补全、Room 批量原子写、5 秒撤销）
- **T-M4-02c：** 助手对话流与确认卡 UI（assistant feature、确认卡组件、撤销 toast）
- **T-M4-02d：** 父任务契约门禁回归（负例用例进 CI）
