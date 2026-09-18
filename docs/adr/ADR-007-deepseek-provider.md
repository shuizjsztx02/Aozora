# ADR-007 AI Agent 路径的 Provider 与推理策略

**状态：** 已采纳（2026-09-18，经账户所有者确认）
**范围：** M7 T-M7-02（P1）
**关联文档：** `docs/adr/ADR-006-ai-agent-architecture.md`（编排位置）、`docs/architecture/ai-agent-architecture.md`（目标架构）、`docs/APP-CONTEXT.md` §12 `D-PROD-13`

> **触发条件：** ADR-006 决策 5 明确写有「若未来引入其他 provider（含 Claude 等）：按 `AGENTS.md`『技术栈替换必须先通过 ADR』单独决策，不在本 ADR 范围」。本 ADR 即该项决策。

## 背景

### 现有 provider 无法支撑 P1

ADR-006 决策 5 保持 DashScope（`qwen3.7-flash`）。P1 需要 **tool calling** 与**可控的推理开销**，两者在该模型上都有问题：

| 问题 | 证据 |
|---|---|
| **推理不可控** | `provider.py` 的 `DashScopeProvider.complete()` 记录 T12-11 实测：该模型为混合推理模型、**默认开启 thinking**，edit/delete/query 多字段输出耗时 **20–24 s**，超出 `ai_timeout_seconds`（默认 10 s、约束 `le=12.0`）预算并静默降级。现有代码为结构化输出硬传 `enable_thinking: False` |
| **开关是二值的** | `enable_thinking` 只有开/关，无中间档位——无法「为效果多花一点时间」地折中 |
| **tool calling 未经实测** | 平台侧确认支持，但该模型的具体行为未验证 |

P1 的 loop 每步都要在 25 s 单步预算内完成。若每步都需 thinking，loop 整体不可用；若全程关闭 thinking，多步规划质量又不足。**二值开关无法同时满足效果与耗时**——这是引入新 provider 的直接动因。

### 候选：DeepSeek V4.1-Flash

**已核实事实**（官方 API 文档与定价页，2026-09-18）：

| 项 | 值 |
|---|---|
| 模型 ID | `deepseek-flash`（即 DeepSeek-V4.1-Flash；旧 `deepseek-v4-flash` 已退役但作为别名仍被接受） |
| 上下文 / 最大输出 | **1M** / 384K tokens |
| 能力 | tool calls ✅、thinking ✅（**默认开启**）、vision ✅、JSON output ✅ |
| 接入方式 | OpenAI 兼容，base URL `https://api.deepseek.com` |
| thinking 开关 | 请求体顶层 `thinking: {"type": "enabled"}`（OpenAI SDK 经 `extra_body` 传） |
| **推理档位** | `reasoning_effort`：`low` / `high` / `max`，默认 `high` |
| 定价（每 1M tokens） | 输入 cache hit **$0.003**（闲时）/ $0.006（忙时）；cache miss $0.15 / $0.30；输出 $0.60 / $1.20 |

**四项对本项目有决定性意义的特性：**

1. **推理三档**（`reasoning_effort`）——正是「按任务复杂度平衡效果与耗时」所需要的粒度，是 qwen 的二值开关给不了的。
2. **1M 上下文**——会话历史与工具定义可以放宽（缓解 R-PROD-15 的 token 压力）。
3. **cache hit 与 miss 差价 50 倍**（$0.003 vs $0.15）——agent loop 每轮都要重发历史与工具定义，命中缓存的成本几乎可忽略。**这是多轮 loop 成本可控的关键**。
4. **thinking 模式下可用 tool calls**（V3.2 起）——不必在「能推理」和「能调工具」之间二选一。

## 决策

### 决策 1：Agent 路径引入 DeepSeek，现有快路径暂留 DashScope

```
agent 路径（/ai/agent，多步 loop）→ DeepSeek `deepseek-flash`
快路径（/ai/interpret 等，单发）  → DashScope `qwen3.7-flash`（不动）
```

**不替换、不清退 DashScope。** 快路径的 prompt（`prompts.py` 的 `LEDGER_PARSE` 等）是针对 qwen 逐轮调优的，换模型需重新调优 prompt 并重跑全部 AI 回归用例——那是独立于 P1 的工作，不在本期范围。两套 provider 并存，由 `LLMProvider` Protocol（已存在）承载，互不干扰。

**Provider 抽象已就绪：** `provider.py` 的 `LLMProvider` Protocol 与 `build_provider()` 工厂即为此设计，新增 `DeepSeekProvider` 是增量实现，不改现有调用方。

### 决策 2：推理按任务复杂度分级（三档）

取代 qwen 的二值开关：

| 档位 | 触发条件 | thinking | reasoning_effort | 预期耗时 |
|---|---|---|---|---|
| **快** | 简单意图：记账、查询、闲聊 | `disabled` | — | 与单发相当 |
| **中** | 单步但需理解：编辑、删除、配置 | `enabled` | `low` | 可控 |
| **深** | 多步 / 复合任务 | `enabled` | `high` | 放宽，靠步骤可见性兜底 |

**入口判定（P1）：规则分类。** 复用现有句式信号（客户端 NLU 已有的 `OP_SENTENCE_RE` 一类判定逻辑在服务端侧重建，不改客户端）+ 关键词表。**不引入 LLM 分类调用**——那会多加一次往返，与「省时间」的初衷相悖。

**loop 内升级（关键设计）：Step 数即复杂度证据。** 入口规则判错时不必重来——若 loop 进入第 2 个 Step（说明任务实际比入口判断的复杂），后续步骤自动升级到「深」档。这一条让入口分类的准确率不再是硬约束。

> **注意：** `deepseek-flash` 的 thinking **默认开启**，因此「快」档必须**显式传 `disabled`**，不能靠省略参数。

### 决策 3：会话日志记录完整事件数据（安全条款相应放宽）

账户所有者决策：**以 agent 效果优先，事件日志记录完整数据，`docs/architecture/security.md` 的相应禁令放宽。**

原条款「禁止记录 token、完整对话、原始票据和账本明细」中的「完整对话」部分**修订为有条件允许**：会话事件日志可存储完整对话内容，但必须满足约束（保留期对齐 `D-PROD-08`、登出/删号联动清理、访问面带权限校验、日志不出现在结构化业务日志中——后者的脱敏要求不变）。

token、原始票据、账本明细三项禁令**保持绝对**。

详见 `docs/architecture/security.md` 的修订记录与 R-PROD-20。

### 决策 4：离线能力收窄已确认接受

服务端 loop 方案的固有代价：agent 路径离线不可用，断网时仅保留本地规则 NLU（覆盖记账）。账户所有者已确认接受。

这意味着 PR-3「断网时全功能可用」收窄为「断网时记账可用」。ARC-006 后果段已记录该代价，本条为明确确认。

### 决策 5：密钥注入方式不变

沿用 `D-EXEC-04` 与现有 provider 的注入路径：**本地** `server/.env`（已 gitignore）；**生产** 经 GitHub Secrets → `docker-compose.prod.yml` 环境变量注入。新增 key **不进仓库、不进 APK、不进构建产物**（`AGENTS.md` 与 `constraints.md` 的硬约束不变）。

## 拒绝的替代方案

1. **继续用 `qwen3.7-flash` 做 loop** —— thinking 与单步预算的冲突已在 T12-11 实测中暴露（20–24 s），且二值开关无法分级。这不是调参能解决的，是模型能力边界问题。

2. **换 `deepseek-v4-pro`** —— 更贵（cache miss $0.66 vs $0.15、输出 $1.98 vs $0.60，约为 Flash 的 3 倍），且不支持 vision。Flash 已满足 tool calls + thinking + 1M 上下文，无升级必要。

3. **同时按任务路由到两个 provider（qwen 快 / DeepSeek 深）** —— 需要维护两套 prompt 与两套行为预期，回归面翻倍，而 DeepSeek 的三档推理已能在单 provider 内完成同样的平衡。收益不抵复杂度。

4. **自托管 DeepSeek 开源权重** —— 552B MoE（激活 8B）的部署与运维成本远超本项目规模所需；官方 API 的 cache hit 定价已足够低。

5. **用一次 LLM 调用做入口意图分类** —— 与「省时间」目标相悖（多一次往返）；规则分类 + loop 内升级的组合已能覆盖，且入口误判可被 Step 升级机制兜底。

6. **只关 thinking 不引入新 provider** —— 多步规划质量不足，「先查预算再改」这类任务正是需要推理的场景；且关掉后与现有单发链路无本质区别，P1 失去意义。

## 后果

### 正向

- ✅ **效果与耗时可分级平衡**——三档推理 + Step 升级机制，简单任务不付推理成本，复杂任务不牺牲质量
- ✅ **多轮成本可控**——cache hit 单价 $0.003/1M，loop 重复发送的历史与工具定义几乎免费（R-PROD-15 显著缓解）
- ✅ **上下文压力解除**——1M 上下文使会话窗口、工具定义、事件日志回放的 token 顾虑大幅下降
- ✅ **tool calling 在 thinking 下可用**——不必二选一
- ✅ **与现有架构零冲突**——`LLMProvider` Protocol 已就绪，新增实现不改调用方

### 负向 / 需要处理

- ⚠️ **新增一个外部依赖与密钥**——`D-EXEC-04` 的密钥管理范围扩展；需登记新的配额与轮换责任人
- ✅ **provider 行为差异已实测（2026-09-18，T-M7-02a 九项全过）**——DeepSeek 的 tool call 参数编码（单层 JSON）、流式 tool_calls 分片（name/id 不丢失）、`reasoning_content` 回传要求、thinking 与 tools 组合、四档耗时均已验证，见 `docs/risk-register.md` R-PROD-23
- ⚠️ **`reasoning_content` 回传约束**——官方文档明确：请求含 `tools` 时，多轮必须回传前序轮次的 `reasoning_content`，否则返回 **400**。loop 实现必须保存并在下一轮带回，这是 P1 的硬性实现要求。**2026-09-18 实测修正（02a）：** 不回传亦返回 200，与官方文档**不符**；实现上仍选择回传以保持推理连续性（`messages` 原样透传，见 `provider.py` 的 `DeepSeekProvider` docstring）
- ⚠️ **Chat Completion API 的一项限制（2026-09-18 已实测澄清）**——官方文档称该 API「不支持在对话中途插入 tool calls」。**02a 实测：不影响标准多步 loop**——回传 assistant 的 `tool_calls` + `role:"tool"` 结果后可正常续跑，无需改用 Responses 或 Anthropic 格式接口
- ⚠️ **双 provider 并存期的配置面**——测试需覆盖两套 provider 的降级路径；`.env` 与生产 Secrets 增加一组变量
- ⚠️ **离线收窄**——已确认接受，但产品原则 PR-3 的表述需相应更新

### 需要更新的文档

| 文档 | 更新内容 | 状态 |
|---|---|---|
| `docs/adr/ADR-007-deepseek-provider.md` | 本文 | ✅ 已完成 |
| `docs/APP-CONTEXT.md` | 新增 `D-PROD-13`（agent 路径 provider 与推理策略） | ✅ 已完成 |
| `docs/architecture/ai-agent-architecture.md` | provider 策略、推理分级、§12 风险与未决问题 | ✅ 已完成 |
| `docs/architecture/security.md` | 「完整对话」禁令放宽为有条件允许 + 修订记录 | ✅ 已完成 |
| `docs/milestones/production-execution-plan.md` | T-M7-02a 改为 DeepSeek 实测闸门；02b/02d 增加 provider 与推理分级产物 | ✅ 已完成 |
| `docs/risk-register.md` | R-PROD-20（隐私边界结论）、R-PROD-21（推理与预算冲突缓解）、R-PROD-23（DeepSeek 接入行为未验证，新增） | ✅ 已完成 |
| `docs/adr/ADR-006-ai-agent-architecture.md` | 决策 5 的 Provider 条款标注被本文修订 | ✅ 已完成 |
| `docs/product/principles.md` | PR-3 离线范围收窄留痕 | ✅ 已完成 |
| `server/src/common/config.py` | DeepSeek 配置项与密钥 | ✅ 已完成（T-M7-02b，`953d664`） |
| `server/src/modules/ai/provider.py` | `DeepSeekProvider` 实现 | ✅ 已完成（T-M7-02b，`953d664`） |