# ADR-009 ActionProposal 契约 v1 → v2 的版本策略

**状态：** 已采纳（2026-09-19）
**范围：** M7 T-M7-05a（P4，批次 1；契约是写工具全量化的硬前置）
**关联文档：** `docs/architecture/ai-architecture.md`（ActionProposal v2 契约小节）、`docs/architecture/ai-agent-architecture.md`（§3.2 全覆盖映射、§7.3 读写工具、§7.4 写操作安全边界）、`contracts/action-proposal/`（schema / schema-v2 / v1-action-aliases / fixtures）、`docs/APP-CONTEXT.md` §12 `D-PROD-15`

> **为什么需要一份 ADR：** 05a 的验收列写的是「必要的 ADR（若契约版本策略需固化取舍）」。v2 不止是「多几个动作」——它改的是**契约的扩展方式**（固定 action 集 → op 注册表 + 通用 payload），以及**新旧客户端共存时谁适配谁**。这两件事都被 P4 后续九张卡与全部既有客户端依赖，取舍必须固化。

## 背景

### 现状：三条影响结论的硬事实

| # | 事实 | 依据 |
|---|---|---|
| 1 | **v1 schema 是三方共享的构建期输入，不是远端资源。** TS 侧 `import schema from '.../schema.json'`（构建期打包进 APK），Python 侧 import 时加载并缓存，ajv 在测试里 `compile(schema)` | `app/src/domain/action-proposal.ts:16`、`server/src/modules/ai/validator.py`（`_SCHEMA_PATH`）、`app/src/__tests__/domain/action-proposal.test.ts` |
| 2 | **旧 APK 内嵌的是 v1 的副本。** 已发布的客户端带着自己的校验器与枚举，服务端换文件不会改变它们的行为——但**服务端一旦产出它们不认识的提案，它们会整条判无效** | 同上；`use-assistant.ts` 的提案分支只认 `add_tx` / `add_tx_batch` |
| 3 | **v1 的形状装不下全量 CRUD。** 固定 action 集 + 根上的 `transactions` 字段；`config` 一个 action 里塞着 6 个子操作（只在 prompt 里定义，无跨语言校验）；每加一个能力要同时改 schema / TS / Python / fixtures | `schema.json`；`prompts.py` 的 6 个 config op |

### 问题的真正形态

P4 要把架构 §3.2 的十三个域映射成工具。若沿用 v1 的扩展方式，每个写工具都要改一次契约文件与三方校验器——**契约晚改＝已实现的工具返工**，这正是 05a 被排成批次 1 硬前置的原因。

## 备选方案

| 方案 | 内容 | 评估 |
|---|---|---|
| **A. 就地扩写 `schema.json`** | 直接把 v2 的枚举与字段加进现有文件 | **否决。** 事实 1 决定了这个文件是三方共享输入：改它等于同时改三条车道的判定。旧客户端的**构建期副本**固然不变，但**当前工作区的客户端**（同一份代码、同一个 APK 版本线）会立刻跟着变——「旧客户端行为不变」就失去了可验证的基线，只能靠口头承诺 |
| **B. 单文件 + `oneOf` 分派** | 一个 schema 里用 `oneOf` 同时描述 v1 与 v2 | **否决。** TS 校验器读的是 `schema.properties` / `schema.definitions.TransactionItem` 的具体路径（未知字段检查、逐项校验都靠它），套一层 `oneOf` 就全断；而且 v1 与 v2 的判定会变成「哪个分支恰好匹配」，不再是一个可判定、可断言的规则 |
| **C. 新增文件 + 字段在场判定**（**采纳**） | v1 文件一字不改；v2 是新文件；车道判定＝`contractVersion` 字段在场与否 | 旧客户端零影响（它们的世界里根本不存在新文件）；两条车道互斥且可判定；回退＝不产 v2，无数据迁移 |

## 决策

### 决策 1：v2 是**新增文件**，v1 的 `schema.json` 一字不改

`contracts/action-proposal/schema-v2.json`。旧 APK 内嵌 v1 副本、当前客户端继续用 v1 校验器——「旧客户端行为不变」由**文件布局**保证，不依赖运行时判断，因此可以被用例直接钉住（v1 的 16 个 fixture 与既有 1300+ 用例原样通过）。

### 决策 2：车道判定＝`contractVersion` 在场与否，不猜

v1 关闭 `additionalProperties` 且没有这个字段，于是：v1 提案**带上**它就是无效 v1，v2 提案**没有**它就是无效 v2。判定是一个布尔问题，不是「哪个分支恰好匹配」。双读入口 `validateProposalAny` / `validate_proposal_any` 返回实际车道（`1` / `2` / 非对象时 `null`）。

### 决策 3：v2 用 **op 注册表 + 通用 payload**，且 **payload 的字段形状不写进契约**

- 新增能力＝注册表加一行，信封不变（这直接支撑 P4 出口标准「新增一个表单能力不改 prompt 主体」的另一半：也不改契约主体）。
- 字段形状由 op 自己的工具注册表条目拥有（`tools.py` 的 `parameters`，本来就是 Draft-07）。**契约不复制第二份**——两份必然漂移，而工具注册表是唯一会随实现同步的那一份。
- **代价（已知并接受）：客户端不做 payload 级授权。** v2 的 `validateProposalV2` 是信封级校验，结构通过**不是执行许可**；payload 里的分类 / 账户 / 目标是否属于当前用户与账本，由服务端在组装与执行时施加。这与 v1 的 `ProposalValidationContext` 不冲突：那道上下文校验存在，是因为 **v1 的提案由客户端组装**（ADR-003）；**v2 的提案由服务端 loop 组装**（ADR-006）。
- 同步的代价：v1 的车道判定可以直接读 `proposal["transactions"]` 做幻觉检查，v2 不能（payload 形状不是契约的一部分）。因此 `/ai/validate` 的 v2 分流**不能只是「换个校验器」**，必须连 payload 级的授权一起设计——见下节残余 1。

### 决策 4：版本协商由**客户端声明**驱动，服务端无全局开关

`POST /ai/agent` 上带 `proposal_contract_version`（缺省＝1，旧 APK 什么都不带）。服务端按声明选车道产出；**v1 车道产不出 v2-only op**（这是实现约束：旧校验器会整条判无效，卡片不出、写入静默失败——必须让工具不可用或降级为说明）。灰度因此天然按客户端版本走，回退＝不产 v2。

## 已知残余

| # | 残余 | 归属 |
|---|---|---|
| 1 | **服务端接线尚未落地**：`/ai/validate` 仍走 v1 校验器、`/ai/execute` 只认 `add_tx` / `add_tx_batch`、`proposal_contract_version` 无读取方。在首个产出 v2 提案的卡之前不会有任何 v2 提案，因此这段空档不造成行为变化 | **05b** |
| 2 | **v1 ↔ v2 的动作名映射目前只有三个别名有真实消费者**（`add_tx` / `add_tx_batch` / `create_category`）。`config` 的 6 个子操作映射到 6 个 v2 op，但那条本地链路何时退场未定 | 05b / 05h |
| 3 | **客户端渲染 v2 提案的路径未在本 ADR 覆盖范围内**：P4 的 05b–05d 交付面是服务端工具，客户端如何出卡片（新的 op 形态不止 `transactions` 一种）不在任何单卡职责内——收口时按「功能覆盖清单逐项走查」一并裁决 | P4 收口 |

## 关联

- 契约文件：`contracts/action-proposal/`（`schema.json` v1 / `schema-v2.json` / `v1-action-aliases.json` / `fixtures/v2/`）
- 三方一致性用例：`server/tests/test_ai_proposal_v2_parity.py`、`app/src/__tests__/domain/action-proposal-v2.test.ts`
- 上游决策：`ADR-003`（提案组装在客户端，v1 语境）、`ADR-006`（服务端 Agent Loop）、`ADR-007`（provider 与会话日志）