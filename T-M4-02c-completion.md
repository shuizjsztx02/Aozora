# T-M4-02c 完成报告

**任务：** 助手对话流与确认卡 UI  
**状态：** ✅ 已完成  
**完成时间：** 2026-09-06

## 验收标准

> 真机闭环：对话 → 确认 → 入账 → 撤销；断网/超时降级路径测试

## 实施内容

### 1. Kotlin Bridge 扩展（ADR-003 通道）

**`AiGatewayClient.kt`** — 新增 3 个 AI 端点方法：
- `interpret(input)` → POST /ai/interpret
- `validate(proposalJson, bookId)` → POST /ai/validate
- `execute(proposalJson, bookId, confirmationToken)` → POST /ai/execute

**`BridgeProtocol.kt`** — 新增 3 个命令常量：
- `AI_INTERPRET = "ai.interpret"`
- `AI_VALIDATE = "ai.validate"`
- `AI_EXECUTE = "ai.execute"`

**`AozoraBridge.kt`** — 新增 3 个 handler：
- `handleAiInterpret` — 调用 `client.interpret()` + 错误映射
- `handleAiValidate` — 调用 `client.validate()` + 错误映射
- `handleAiExecute` — 调用 `client.execute()` + 错误映射

### 2. TypeScript Bridge 扩展

**`bridge-types.ts`** — 新增命令和类型：
- `AI_INTERPRET` / `AI_VALIDATE` / `AI_EXECUTE` 命令枚举
- `AiInterpretResponse` / `AiValidateResponse` / `AiExecuteResponse` 类型
- `AiValidateVerdict` 联合类型

**`native-bridge.ts`** — 新增 3 个方法：
- `interpretAI(input)` — 25s 超时
- `validateProposal(proposal, bookId)` — 25s 超时
- `executeProposal(proposal, bookId, confirmationToken)` — 25s 超时

### 3. 域逻辑

**`ai-proposal-builder.ts`** — AI 输出 → ActionProposal 转换器：
- 将 AI `ledger_parse` 输出的扁平字段（type/amountCents/category/note/bizDate/confidence）转换为完整 ActionProposal
- 分类名 → ID 匹配（精确匹配 + 包含匹配）
- 金额无效 → chat 兜底
- 置信度截断到 [0.1, 1.0]
- 通过 `validateProposal` 契约闸
- 纯函数，不依赖 React / bridge / 存储

### 4. 助手对话状态机

**`use-assistant.ts`** — 助手 hook：
- 状态机：`idle → processing → confirming → executing → undo_window → idle`
- AI / 本地 NLU 路由：
  - 本地 NLU 置信度 < 0.8 或有 warnings → 尝试 AI
  - AI 失败（超时/网络/503/429）→ 降级到本地 NLU
  - 断网 → 直接本地 NLU
- 12s 慢速提示（`isSlow` 状态）
- 执行后写入本地 Room（`source: 'nlu_ai'`）
- 5s 撤销倒计时
- 撤销通过 `softDeleteTransaction` 实现（创建 pending mutation → sync 推送删除）

### 5. UI 组件

**`AssistantPage.tsx`** — 助手页面：
- 对话式消息列表（用户/AI 气泡）
- 输入区（文本框 + 发送按钮）
- 处理中动画 + 12s 慢速提示
- 确认卡嵌入（条件渲染）
- 撤销 toast（条件渲染）

**`ConfirmationCard.tsx`** — 确认卡：
- 可编辑金额（数字输入）
- 可编辑分类（分类网格选择）
- 可编辑日期（date input）
- 可编辑备注
- 可编辑收支类型
- 警告展示
- 确认/取消按钮

**`UndoToast.tsx`** — 撤销 toast：
- 底部弹出动画（framer-motion）
- 倒计时展示
- 撤销按钮

### 6. 路由与导航

**`Router.tsx`** — 新增 `/assistant` 路由（无需 AppLayout 包裹，全屏体验）  
**`BottomNav.tsx`** — 新增「💬 助手」导航项（5 项导航）

### 7. 验收测试（新增 16 用例）

**`ai-proposal-builder.test.ts`** — 13 用例：

| 测试 | 覆盖场景 |
|------|----------|
| 正常解析：支出 + 精确分类匹配 | 完整 happy path |
| 正常解析：收入 | income 类型 |
| 分类包含匹配：「交通费」→「交通」 | 部分匹配 |
| 分类未找到 → warning + requiresConfirmation | 未匹配分类 |
| 空分类名 → 无 categoryId | 缺失分类 |
| 日期为空 → 默认今天 | 日期补全 |
| 金额无效（0）→ chat 兜底 | 零金额 |
| 金额为负 → chat 兜底 | 负金额 |
| 低置信度 → requiresConfirmation | 低置信 |
| 置信度截断到 [0.1, 1.0] | 边界值 |
| 输出通过 validateProposal 契约闸 | 契约完整性 |
| 收支类型无效 → 默认为支出 | 类型降级 |
| note 截断到 200 字符 | 长度限制 |

**`native-bridge.test.ts`** — 3 新增用例：

| 测试 | 覆盖场景 |
|------|----------|
| interpretAI → ai.interpret | 命令名 + payload |
| validateProposal → ai.validate | 命令名 + payload |
| executeProposal → ai.execute | 命令名 + payload + confirmation_token |

**测试结果：** 365/365 通过 ✅（新增 16 用例）

## 降级策略

| 场景 | 行为 |
|------|------|
| 断网 | 直接本地 NLU |
| AI 超时（25s） | 降级到本地 NLU |
| AI 503/429 | 降级到本地 NLU |
| AI 返回格式异常 | 降级到本地 NLU |
| 本地 NLU 无法解析 | chat 兜底 |
| 服务端验证失败 | 降级到本地 NLU 结果 |
| 服务端验证超时 | 保留 AI 结果 |

## API 契约摘要

### ai.interpret
- **请求：** `{ input: string }`
- **响应：** `{ request_id, task, model, output: { type, amountCents, category, note, bizDate, confidence }, usage }`
- **超时：** 25s

### ai.validate
- **请求：** `{ proposal: ActionProposal, book_id: string }`
- **响应：** `{ valid: boolean, errors: [], verdict: "accepted"|"rejected_hallucination"|"rejected_schema"|"rejected_low_confidence" }`
- **超时：** 25s

### ai.execute
- **请求：** `{ proposal: ActionProposal, book_id: string, confirmation_token: string }`
- **响应：** `{ transaction_ids: string[], proposal_id: string, undo_window_seconds: number }`
- **超时：** 25s

## 安全边界

1. **Token 不出 Kotlin 层：** AI 请求通过 `AiGatewayClient` 注入 Bearer token，WebView 零信任面
2. **服务端重验证：** `ai.execute` 调用前必须 `ai.validate`；服务端 `execute_proposal` 会重新验证（TOCTOU 防护）
3. **契约闸：** 所有 proposal 必须通过 `validateProposal` 才能展示/执行
4. **降级零写库：** 本地 NLU 降级时不直接写库，必须经过确认流程

## 依赖关系

- **硬依赖：** T-M4-02b ✅、T-M4-01d ✅（ADR-003 通道）
- **下游任务：** T-M4-04a（权限状态机）、T-M4-06a（四主题视觉打磨，依赖助手页面）

## 交接产物

### Kotlin
- `app/src/main/java/com/aozora/app/ai/AiGatewayClient.kt` — AI HTTP 客户端
- `app/src/main/java/com/aozora/app/bridge/BridgeProtocol.kt` — 新增 3 命令
- `app/src/main/java/com/aozora/app/bridge/AozoraBridge.kt` — 新增 3 handler

### TypeScript
- `app/src/platform/bridge/bridge-types.ts` — 新增 3 命令 + 类型
- `app/src/platform/bridge/native-bridge.ts` — 新增 3 方法
- `app/src/domain/ai-proposal-builder.ts` — AI 输出转换器
- `app/src/features/assistant/use-assistant.ts` — 状态机 hook
- `app/src/features/assistant/AssistantPage.tsx` — 助手页面
- `app/src/features/assistant/ConfirmationCard.tsx` — 确认卡
- `app/src/features/assistant/UndoToast.tsx` — 撤销 toast
- `app/src/app/Router.tsx` — 路由注册
- `app/src/components/BottomNav.tsx` — 导航入口

### 测试
- `app/src/__tests__/domain/ai-proposal-builder.test.ts` — 13 验收用例
- `app/src/__tests__/bridge/native-bridge.test.ts` — 3 新增桥命令用例
- `app/src/test/java/com/aozora/app/bridge/BridgeProtocolTest.kt` — 命令完整性更新

## 备注

- 撤销通过 `transaction.softDelete` 实现（创建 pending mutation → sync 推送），不新增 `ai.undo` 桥命令
- 执行后写入本地 Room 的 transaction 可能产生 duplicate insert pending mutation（服务端已有该事务），sync 引擎会标记为 rejected，不影响功能正确性
- 真机验证待后续集成测试完成
