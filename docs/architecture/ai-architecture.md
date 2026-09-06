# Aozora · AI 架构

本地规则 NLU 提供离线兜底，服务端 AI Gateway 提供在线理解。模型密钥只存在服务端密钥管理环境；客户端不得直接调用模型提供商。

AI 只返回通过 JSON Schema 校验的 ActionProposal。金额、日期、权限和写库始终由确定性业务代码负责，低置信结果必须先经用户确认。

## ActionProposal 契约

- **JSON Schema：** `contracts/action-proposal/schema.json`（Draft-07，版本化 URL `$id`）
- **TypeScript 类型 + 校验器：** `app/src/domain/action-proposal.ts`
- **Fixtures：** `contracts/action-proposal/fixtures/`（valid + invalid 正反例）
- **变更规则：** 协议修改必须先更新本文件和 `contracts/action-proposal/schema.json`，并新增 ADR 或协议版本号

## WebView 调用通道

客户端 WebView 经 `AozoraBridge` 的 `ai.*` 命令族调用服务端 `/ai/*`；Kotlin 层负责 token 注入，Bearer token 全程不出 Kotlin 层（详见 `docs/adr/ADR-003-webview-ai-channel.md`）。超时预算遵循 APP-CONTEXT §10.3：12 s 客户端慢速提示 / 25 s 取消并回退本地 NLU。

### T-M1-05 v1 约束修复

- 未知字段必须拒绝；金额上限为 9007199254740991 分；出现 transactions 时必须非空。
- bizDate 的 `format: date` 必须以断言模式验证完整公历日期，不能只检查正则或把 format 当注释。此为既有 v1 约束修复，不新增动作或改变合法提案字段。
- `validateProposal(input)` 仅作结构校验；执行前传入 `ProposalValidationContext`（当前账本允许的 categoryIds/accountIds），拒绝未解析分类和未授权 ID。上下文来自可信 repository/API，不能取自模型输出。M2/M4 接线前不能把结构通过当作执行许可。
