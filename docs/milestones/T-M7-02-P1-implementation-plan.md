# T-M7-02（P1）实施推进计划

**建立：** 2026-09-18
**关系：** 本文件是 **T-M7-02（P1）的执行细则与进度看板**。任务边界、交接产物与验收标准以 `docs/milestones/production-execution-plan.md` M7 章节为准；本文件**不改变任何验收标准**，只细化推进方式、逐卡职责边界，并记录中间结果。
**架构与决策：** `docs/architecture/ai-agent-architecture.md`（目标架构）、`docs/adr/ADR-006`（编排位置）、`docs/adr/ADR-007`（provider 与推理策略）
**用法：** 领取任一张子卡的 Agent，先读本文件对应小节，再读 `production-execution-plan.md` 该卡的验收列。

---

## 一、状态看板

| 卡 | 内容 | 状态 | 完成日 | 提交 | 备注 |
|---|---|---|---|---|---|
| **02a** | DeepSeek 能力实测闸门（go/no-go） | ✅ 完成 | 2026-09-18 | —（实测，无代码改动） | 九项全过，详见 §五 |
| **02b** | Provider 接入与扩展（DeepSeek） | ✅ 完成 | 2026-09-18 | `953d664` | 批次 1；54 用例，快路径零改动，详见 §五 |
| **02c** | 工具注册表与域分组 | ✅ 完成 | 2026-09-18 | `2f09c30` | 批次 1；15 个工具 / 3 域，详见 §五 |
| **02d** | Agent Loop 核心与推理分级 | ⏳ 未开始 | | | 批次 2 |
| **02e** | 写工具安全路径 | ⏳ 未开始 | | | 批次 3，可与 02f 并行 |
| **02f** | 会话状态与配额双限 | ⏳ 未开始 | | | 批次 3，可与 02e 并行 |
| **02g** | SSE 端点与部署配置 | ⏳ 未开始 | | | 批次 4（**整链最需留缓冲的一环**） |
| **02h** | 客户端桥通道 | ⏳ 未开始 | | | 批次 5 |
| **02i** | 客户端消费（最小形态） | ⏳ 未开始 | | | 批次 5 |
| **收口** | P1 四板 + T-M7-01 真机复认 | ⏳ 未开始 | | | 批次 6 |

**状态取值：** ⏳ 未开始 / 🔄 进行中 / ✅ 完成 / ⛔ 阻塞（阻塞须在本表备注列写明原因与解除条件）

---

## 二、批次与依赖

```
批次 1  02b ─┐
        02c ─┴─→ 批次 2  02d ─┬─→ 批次 3  02e ─┐
                              └─→ 批次 3  02f ─┴─→ 批次 4  02g ─→ 批次 5  02h ─→ 02i ─→ 批次 6 收口
```

| 批次 | 卡 | 可并行 | 说明 |
|---|---|---|---|
| 1 | 02b、02c | ✅ 两者互不依赖 | 服务端地基：provider 能力 + 工具定义 |
| 2 | 02d | — | loop 核心，依赖批次 1 全部 |
| 3 | 02e、02f | ✅ 两者均只依赖 02d | 安全路径与会话状态互不依赖 |
| 4 | 02g | — | 流式通道，依赖 02d + 02e |
| 5 | 02h、02i | — | 客户端，串行（02i 依赖 02h） |
| 6 | 收口 | — | 四板 + 真机复认 |

**并行切分点：** 若开多会话并行，**批次 1 的 02b/02c** 与 **批次 3 的 02e/02f** 是仅有的两组安全切分。其余串行。

**外部阻塞：无。** 02a 已验证 provider 可用；provider / 推理 / 安全边界 / 离线收窄四项决策均已落文档；nginx 决定不改（SSE 缓冲改走应用层响应头）。

---

## 三、逐卡细则

> 每卡均含「内容 / 职责边界 / 产出 / 验证」。**职责边界是防范围蔓延的关键，不得越界**。

### 02b Provider 接入与扩展（DeepSeek）

- **内容：** 新建 `DeepSeekProvider` 实现既有 `LLMProvider` Protocol（`provider.py:53-63`）；`complete_with_tools()`（payload 增 `tools`/`tool_choice`，解析 `tool_calls`）与流式能力；`ProviderResult` 增 `tool_calls` 与 `reasoning_content` 字段；推理档位参数注入（`thinking` + `reasoning_effort`）；`config.py` 增 `AI_AGENT_PROVIDER_*` 三项（模板见 `server/.env.example`）；`build_provider()` 按用途分派。
- **职责边界：** **不动现有 `AIService` 与 qwen 路径**——快路径的 prompt 与逻辑一行不改，只做增量实现。不接数据库。不改 `ai_timeout_seconds` 等既有配置语义。
- **产出：** `server/src/modules/ai/provider.py`（扩展）、`server/src/common/config.py`（增量）
- **验证：** MockTransport 单测覆盖——正常 `tool_calls`、非法 JSON arguments、未知工具名、流式分片拼接、`[DONE]` 终止、中途断流；超时仍走不可重试路径（`provider.py:128-132` 语义不变）；密钥不出现在日志与响应。
- **实测依据（02a）：** `arguments` 为单层 JSON（非双重编码）；流式首块含 `id`+`name`、后续为 `arguments` 增量；`reasoning_content` 建议回传（虽非硬约束，见 §五）。

### 02c 工具注册表与域分组

- **内容：** 新建 `server/src/modules/ai/tools.py`——工具定义（name / description / JSON Schema parameters / 读写标记 / 执行体）。**P1 最小集**：记账域（`transaction.*`）、预算域（`budget.*`）、账户账本域（`account.*` / `book.*` 读），约 10–15 个。按域分组，每域 5–8 个。
- **职责边界：** **不做全量工具**——全量约 77 个命令对应的工具集归 P4（T-M7-05）。不实现 loop（02d）。不生成 ActionProposal（02e）。
- **产出：** `server/src/modules/ai/tools.py`（新建）
- **验证：** 工具参数 schema 与 `contracts/action-proposal/schema.json` 的 action 枚举无冲突；每域工具数 ≤8；未知工具名走降级契约；全量注入被明确排除。
- **注意：** `description` 是模型可见的**唯一语义来源**，直接决定工具选择准确率（R-PROD-17），**须人工审核，不得机器直译**。

### 02d Agent Loop 核心与推理分级

- **内容：** 新建 `server/src/modules/ai/agent.py`——Turn/Step 分层（`while (await turn())`）；四条跨请求不变式（工具调用与结果配对 / 中途输入进正确的下一步 / 取消收敛到一致状态 / 历史可恢复可分叉）；退出条件硬边界（单 Turn ≤8 Step、≤5 写操作、≤20 工具调用、连续无进展 ≤2），任一触发即优雅终止并说明已完成部分；**入口推理分级**（规则表判定快/中/深三档，**不额外调用 LLM**）与 **loop 内按 Step 数升级**（进入第 2 个 Step 即升到「深」档）。
- **职责边界：** **先做非流式**——同步返回完整结果，以复用现有同步 `TestClient` 基建，不等 02g 的流式样板。不处理 HTTP 传输（02g）。不做写操作执行（02e）。
- **产出：** `server/src/modules/ai/agent.py`（新建）
- **验证：** 注入 fake provider 的确定性单测（`app.dependency_overrides[get_llm_provider]` 范式已存在）——四条不变式各有用例；四条硬边界各有用例；优雅终止输出含「已完成 / 未完成」两部分；三档判定规则各有用例；**构造「入口判快、实际多步」的用例验证 Step 升级生效**；「快」档必须显式传 thinking `disabled`（该模型默认开启，省略参数不等于关闭）。
- **补测项（02a 遗留）：** 复杂任务下三档的真实耗时差异——02a 的耗时数据来自简单任务（0.67–1.15 s 差异极小），复杂任务的差异需用真实多步用例补测。

### 02e 写工具安全路径（ActionProposal + 审批四值）

- **内容：** 写工具不直接执行，产出 ActionProposal 后按策略挂起或放行；复用 `validator.validate_proposal` + `context.load_validation_context`（**均为纯函数、无 FastAPI 耦合，loop 内可直调**）；执行复用 `AIService.execute_proposal`（含 TOCTOU 重校验）；实现审批四值 `allowed-once` / `rejected` / `cancelled` / `unavailable`（视为拒绝）。**审批策略：** 单 Step 完成的任务沿用现有策略（confidence ≥0.9 自动执行 + 撤销窗，阈值见 `service.py:563`）；**多步 loop（≥2 Step）的写操作一律强制确认**。
- **职责边界：** **不改现有校验器与执行器的语义**，只做接线。三条铁律不放松——模型永不输出 UUID、金额由确定性代码算、写库必经校验与审批门。
- **产出：** `server/src/modules/ai/agent.py`（扩展）
- **验证：** 模型输出 UUID 被拒；三条铁律各有用例；`unavailable` 视为拒绝；`allowed-once` 执行后即失效、不产生长期授权；多步场景下写操作必出确认卡。

### 02f 会话状态与配额双限

- **内容：** 新建最小会话事件表（`conversation_id` / `seq` / `type` / `data` / `created_at`）；激活当前空闲的 `AIConversation` 表（`models/ai.py:15`，docstring 自陈为 schema 占位）作会话索引；`AIUsage` 增 `conversation_id` 列，会话级「轮数 + token」双限按聚合查询实现。上下文窗口 **N 初始 8 轮**。
- **职责边界：** **只做够多轮上下文的最小形态**——P3（T-M7-04）才演进为完整事件日志（补 `ignorable` 与 Surface 子集），这里**不做压缩、不做分叉、不做回放**。历史与摘要只含自然语言，**不得出现 UUID / 内部 ID**。
- **产出：** 新模型文件 + Alembic 迁移（紧接 `0016_cloud_entities`）+ `AIUsage` 加列
- **验证：** 多轮澄清用例（问 → 追问 → 简答「500」→ 正确解析）；会话级轮数 / token 超限返回 429（code 沿用 `ai_quota_exceeded`）；历史窗口无 UUID；登出 / 删号清理会话行。
- **前置动作：** 先把 `config.py:184` 的 `ai_daily_quota_guest` 从联测临时的 `100000` **恢复为 `20`**（注释自陈「联测结束后必须恢复为 20」），否则配额实测与 R-PROD-15 成本评估数据失真。

### 02g SSE 端点与部署配置

- **内容：** `routes.py` 新增 `POST /ai/agent` 返回 `StreamingResponse`（`text/event-stream`）；实现架构 §8.1 的八类事件（`phase` / `tool_call` / `tool_result` / `confirm` / `device_request` / `delta` / `done` / `error`）；heartbeat（空闲期发注释帧）；SSE 响应发 **`X-Accel-Buffering: no`** 解决 nginx 缓冲；**DB session 改用 `get_session_factory()` 自行管理**（不能沿用 `Depends(get_db_session)`——请求作用域 session 在响应返回后即关闭，而生成器仍在运行）；**新建流式测试样板**；**新建双层灰度开关**（服务端 env + 客户端开关，默认关闭）。
- **职责边界：** **零新依赖**——Starlette 原生 `StreamingResponse` + 手写帧格式化（约 30 行），**不引 `sse-starlette`**。**不改服务器 nginx 配置**。
- **产出：** `server/src/modules/ai/routes.py`（增量）、SSE 帧格式化模块、`server/tests/test_agent_stream.py`、开关配置项
- **验证：** 流式样板可跑（帧格式、事件顺序、heartbeat）；**实测确认 `X-Accel-Buffering: no` 经真实链路（含 nginx）后事件逐条下发而非攒批**——若该头被忽略，须回到改 nginx 的路子并更新 R-PROD-22；开关关闭时端点不可用且旧客户端行为不变（不调 `/ai/agent` 则完全不受影响）。
- **风险提示：** 本卡是**整链最需留缓冲的一环**——流式测试样板在仓库中**零先例**（现全部为同步 `TestClient`，且无任何 async generator 用于 HTTP 响应）。若它卡住，批次 5 会连带等待。

### 02h 客户端桥通道（okhttp-sse + 事件转发）

- **内容：** `app/build.gradle.kts` 加 `com.squareup.okhttp3:okhttp-sse:4.12.0`；`AiGatewayClient` 实现流式接收（**SSE 长连接不能用现有 `callTimeout(25s)`**，需独立 OkHttpClient 配置）；新增 `ai.agent` / `ai.cancel` 桥命令——**命令数 77 → 79，四处同步**：`BridgeProtocol.kt` 的 const 与 `ALL`、`AozoraBridge.dispatch`、`bridge-types.ts`、`native-bridge.ts`，且 `SecurityConfigTest.kt:30-57` 的硬编码 `77` 断言同步更新；事件推送复用 `webView.post { evaluateJavascript }` + 新增 `window.__aozoraAiStream__`（照 `onPermissionResult` 的链式包装写法，已有 7 个全局回调先例）；`activeStreamJob` 仿 `activeParseJob` 支持取消。
- **职责边界：** **Token 不出 Kotlin 层**（ADR-003 不变）。**不做 UI**（那是 02i）。
- **产出：** `app/build.gradle.kts`、`AiGatewayClient.kt`、`AozoraBridge.kt`、`BridgeProtocol.kt`、`native-bridge.ts`、`bridge-types.ts`、`SecurityConfigTest.kt`
- **验证：** 桥命令计数断言 79；TS 命令完整性契约测试；SSE 事件从服务端到 WebView 端到端送达；取消能终止 loop。

### 02i 客户端消费（最小形态）

- **内容：** `use-assistant.ts` 的 `submitInput` 改走 `ai.agent`。**v2 intent 在流式中无法预知**（chat/query/edit/delete/config 的 UI 形态互异），定型为：流式期间只渲染文本气泡，`done` 到达后按既有 v2 分发矩阵（`use-assistant.ts:669-691`）走原有卡路径，用户感知为「思考完毕展开卡」；保留非流式 `ai.interpret` 作为开关关闭时的降级。
- **职责边界：** **不做步骤卡片流、不做多会话、不做模式选择**——归 P2（T-M7-03）。三个互斥瞬时态（`activeProposal` / `pendingOp` / `pendingConfig`）的约束不变。
- **产出：** `app/src/features/assistant/use-assistant.ts`（改造 `submitInput`）
- **验证：** 多步任务端到端可完成；多轮澄清可用；非 chat intent 下流式气泡正确卸载并展开卡；开关关闭时回落现有单发链路且 12 s / 25 s 体验不变；既有 assistant 测试全绿。

### 收口（批次 6）

- 四板全绿（见 §四）
- 顺带走一遍 **T-M7-01 遗留的真机双设备换机 bootstrap 复认**（R-PROD-18 残余，原定随 P1 验证）

---

## 四、推进机制

### 提交规则

- **每卡一次独立提交**，**逐文件精确 add**（`git add <本卡文件>`），**绝不 `git add -A`**——会把在途卡半成品与临时调试文件一并带上（第十六轮多会话并行时踩过此坑）。
- 跨卡共享文件（`AozoraBridge.kt`、`use-assistant.ts` 等）由后完成的卡负责解决冲突，不得顺手改其他卡的文件。

### 验证基线（每卡完成后必跑）

```
cd server && python -m pytest tests/ -q          # 基线 819 passed + 37 skipped（2026-09-18 批次 1 收口实测；02d 在途的 tests/test_ai_agent.py 不计入）
cd app && ./gradlew :app:testDebugUnitTest       # 基线 394 tests / 0 failures（2026-09-18 实测）
cd app && npx vitest run                         # 基线 1107 passed（2026-09-18 实测）
cd app && npx tsc --noEmit                       # 0 错
```

> 原记录为 658 / 392 / 1104，与实测不符（2026-09-18 由 02c 复核修正）。数字以实测为准。
>
> **服务端基线推导（2026-09-18 复核）：** 659（02b 前既有）+ 54（02b）+ 95（02c）= 808（批次 1 交付时）；收口核查再各补用例（02b +6、02c +5）后为 **819**（= 659 + 60 + 100）。
> ⚠️ **多会话陷阱：** 02b 提交信息里的「782 passed」**不是**本卡基线——当时 02c 的 `test_ai_tools.py` 正被并发会话在途写入（已 69 例），全量计数把它一并算进去了（713 + 69 = 782）。**在并发会话下跑全量套件，数字不可作为本卡基线**，须以 `pytest <本卡测试文件>` 的增量与 `--collect-only` 为准。

每卡只允许在基线上**增加**，不得减少。基线随卡推进更新，更新时须在本文件 §一 状态看板与 `production-execution-plan.md` 同步。

### 阻塞处理

- 卡内发现需改**架构决策**时**停下来先改文档**再动代码（`docs/engineering/需求变更规范.md` 对 AI 协议类改动的硬要求）。
- 出现阻塞：在本文件 §一 状态看板把状态改为 ⛔ 并在备注写明「原因 + 解除条件」，同时更新 `docs/risk-register.md`。
- 不替其他卡修问题，不删别人的临时产物。

### 卡完成时的记录义务

每卡完成后，在本文件 §五 追加一条中间结果记录，按 `production-execution-plan.md` 末尾的**交接模板**填写（任务编号 / 完成范围与未做 / 修改的文档契约代码 / 验证命令与结果 / 数据同步 AI 安全影响 / 兼容与回退方式 / 开放风险与责任人 / 下一项未阻塞任务）。

---

## 五、中间结果记录

### 02a · DeepSeek 能力实测闸门（✅ 2026-09-18）

**方式：** 真实 API 调用（`deepseek-flash` @ `api.deepseek.com`），无代码改动，无提交。

| # | 验证项 | 结果 |
|---|---|---|
| 1 | tool calls 基本流程与参数编码 | ✅ 返回标准 `tool_calls`，`arguments` 为**单层 JSON**（非双重编码） |
| 2 | thinking + tools 组合 | ✅ `thinking:{"type":"enabled"}` + `reasoning_effort:low` 正常返回 `tool_calls` |
| 3 | `reasoning_content` 回传要求 | ⚠️ **非硬约束**——不回传亦返回 200，与官方文档「含 tools 时否则 400」**不符**。实现上**仍建议回传**以保持推理连续性 |
| 4 | 「不支持在对话中途插入 tool calls」的含义 | ✅ **不影响标准 loop**——回传 assistant 的 `tool_calls` + `role:"tool"` 结果后可正常续跑 |
| 5 | `stream=true` + `tools` 分片完整性 | ✅ 标准 OpenAI 格式：首块含 `id` + `function.name`，后续块为 `arguments` 增量，`data: [DONE]` 收尾。**name/id 不丢失** |
| 6 | 四档耗时 | ✅ thinking `disabled` **0.67 s** / `low` 0.68 s / `high` 0.84 s / `max` 1.15 s |

**额外发现（影响实现）：**

- **thinking 默认开启的代价**——不关 thinking 时 `reasoning_tokens` 计入 `completion_tokens`，实测 `max_tokens=30` 被推理全部吃光、`content` 返回**空串**、`finish_reason: length`。**「快」档必须显式传 `disabled`**。
- **prompt cache 生效**——`prompt_cache_hit_tokens` 实测有命中值，多轮 loop 的 token 成本压力小于预估（R-PROD-15 的主要缓解手段）。
- **分级的主要收益是省 token 而非省时间**——简单任务下三档耗时差异 <0.5 s。复杂任务下的差异需在 02d 补测。

**结论：** go/no-go **通过**。02b 及之后全部解除依赖。

---

### 02b · Provider 接入与扩展（DeepSeek）（✅ 2026-09-18）

**任务编号：** T-M7-02b　**提交：** `953d664`

**完成范围 / 明确未做：**

- **完成：** `server/src/modules/ai/provider.py`（+571 行）——`DeepSeekProvider`（OpenAI 兼容，base `https://api.deepseek.com`）实现既有 `LLMProvider` Protocol，并额外提供 `complete_with_tools()` / `complete_with_tools_stream()`；`ProviderResult` 增 `tool_calls` / `reasoning_content`（带默认值，位置构造向后兼容）；`ToolCall` + `parse_arguments()`；`ReasoningLevel` 三档 + `_REASONING_PARAMS`；`_StreamAccumulator` 分片合并；`_parse_message` / `_parse_tool_calls` / `_raise_for_status`；`ProviderPurpose` + `build_provider(cfg, purpose)`；`get_agent_provider()` 并纳入 `reset_llm_provider()` / `close_llm_provider()`。`server/src/common/config.py` 增 `ai_agent_provider_{api_key,model,base_url}`；`server/.env.example` 同步模板；`conftest.py` 密封 agent key（防 ambient `.env` 污染）。
- **明确未做：** 不接线（无路由、无 loop）——`get_agent_provider()` 目前**无生产调用点**；不校验工具名（注册表归 02c）；不实现 loop（02d）；不处理 SSE 帧格式与心跳（02g）。

**修改的文档、契约与代码：**

- 代码：`server/src/modules/ai/provider.py`、`server/src/common/config.py`、`server/tests/conftest.py`、`server/.env.example`、`server/tests/test_ai_agent_provider.py`（新建）
- **契约零改动**；**快路径零改动已核**——本卡 diff 对 `provider.py` 仅 6 行删除，全部是 `build_provider` / 工厂函数的签名与 docstring，`DashScopeProvider` 类体与 qwen 调用面一行未动

**验证命令与结果：**

```
cd server && python -m pytest tests/test_ai_agent_provider.py -q   # 60 passed（交付时 54，本卡收口 +6）
cd server && python -m pytest tests/ -q                            # 819 passed, 37 skipped（批次 1 交付时 808，收口后 +11）
cd server && python -m ruff check src/ tests/                      # All checks passed
cd app && npx tsc --noEmit / npx vitest run                        # 0 错 / 1107 passed（本卡未动前端，回归确认）
./gradlew :app:testDebugUnitTest                                   # 394 tests / 0 failures（同上）
```

**收口核查与修复（2026-09-18，本卡交付后的独立复审）：**

复审方法：`pytest` 基线上逐条注入单点变异，以「实现改坏后测试是否变红」判定覆盖真假，并对每个怀疑点写独立复现脚本实跑。**共修 6 处实质问题（每处各补一条可失败的回归用例）＋ 定型 1 条跨层契约 ＋ 判定 1 项不修；6 处修复的变异实测 6/6 变红，契约本身亦有变异用例（共 7/7）。**

| # | 缺陷 | 触发场景 | 修复 |
|---|---|---|---|
| 1 | **分片 `index` 缺失时的兜底是错的**（`_merge_tool_call`） | 兜底写的是 `len(self._tool_calls)`，只在**整个流的第一片**上等于 0；任何**后续**无 index 的分片都会新开一个空槽 → 一次调用的参数被劈成多个 `name=""` 的残废 ToolCall，`tool_call_id` 全部对不上。原测试恰好让 index 等于插入序，**该分支零覆盖** | 改为续接当前已打开的调用（`max(self._tool_calls) if self._tool_calls else 0`），并补「无 index 续片」用例 |
| 2 | **流中断被标成可重试**，与自身 docstring 契约相反 | docstring 写明「已转发 delta 的调用方不应重试（会重复文本）」，代码却给 `retryable=True`；而 `service.py` 的重试契约正是 `if not exc.retryable … 降级`。02d 照抄该模式会让用户看到重复的「半句话」 | 引入 `emitted` 标记：**首个 delta 转发之后的一切中途失败一律不可重试**（transport / bad_response / 组装期违规同一规则）；转发前失败仍可重试，保留恢复能力 |
| 3 | 畸形 SSE 帧以**裸 `KeyError`** 逃出生成器 | `choices` 是非空 dict 时 `choices[0]` 抛 `KeyError`，不在 `except (ValueError, TypeError)` 内 → 穿透整个 async generator，02g 的 `StreamingResponse` 会中途 500，loop 的 `except ProviderError` 接不住。非流式路径同形状却正确归 `bad_response`——**两条路径分类不一致** | 捕获元组扩为 `(ValueError, TypeError, KeyError, IndexError, ArithmeticError)`（含 `int(inf)` 的 `OverflowError`） |
| 4 | **空 `data:` 行会把整条流判死** | SSE 允许空 data 字段，网关常拿它当 keep-alive；代码已容忍 `: ping` 注释帧与空行，却把空 data 喂给 JSON 解码器 → `bad_response` → 流中断 | 先判空再判 `[DONE]` |
| 5 | **空 name 在流式与非流式之间行为相反，且 docstring 与代码自相矛盾** | 非流式对空 name 抛 `bad_response`，流式却静默放行；而 `_parse_tool_calls` 的 docstring 写的是「未知**或空**名字透传给 loop 处理」——与它下面三行的代码直接冲突。02d 不知该按哪个契约写 | 统一为**透传 `""`**（与 docstring 的降级哲学一致：loop 回一个工具名错误的 tool-result 让模型自纠）；改掉 docstring 与代码的矛盾，两侧各补用例 |
| 6 | **空 `id` 被静默变成 `""`**（原样放行） | loop 若把它原样回传成 `{"role":"tool","tool_call_id":""}`，DeepSeek 必 400，且**失败点延后到下一轮**，归因困难 | **判定为跨层契约，不在 provider 层拦**：id 只需在 loop 自己的消息列表内成对，loop 补一个稳定 id 并**同时写回 assistant 消息**（``agent.AgentLoop._assistant_tool_calls``，02d 已实现）——provider 在此拒绝只会把一个调用方处理得了的情况变成失败的回合。契约由 ``test_tool_call_without_id_passes_through_empty`` / 流式同名用例钉住 |
| 7 | `test_provider_repr_hides_the_key` 是**空转测试** | 类没有自定义 `__repr__`，默认 repr 永远不含密钥，该断言在任何实现下都不可能失败——对「密钥不外泄」给出的是**虚假保证** | 给 `DeepSeekProvider` 加脱敏 `__repr__`，测试同时断言「脱敏 repr 在位」（删掉即变红），把空转断言变成真信号 |
| 8 | `reset_llm_provider()` 只清引用不做 `aclose()` | 池化 AsyncClient 泄漏。文档标明 test-only，MockTransport 下无真实连接。**判定为不修**，仅记录 | — |

**判定为「记录不修、交 02d」的一项：** `finish_reason` 两条路径都不读，`ProviderResult` 也无该字段。当 `max_tokens` 恰好在 tool_call 中途截断时，拼出的 `arguments` 是断 JSON，`parse_arguments()` 抛错 → loop 会判成「模型给了非法参数」让它重试，**认不出这是截断**，一路撞到「连续无进展 ≤2」才停。P1 的 `max_tokens` 余量很大（384K 输出），实际风险低，故不扩 `ProviderResult` 字段；**02d 若要让 loop 区分截断，须自行加字段**。

**关键设计决策（02d / 02g 必须知道）：**

1. **`ToolCall.arguments` 保真原始 JSON 文本**，在使用点经 `parse_arguments()` 解析。非法 JSON 是模型输出问题，须回喂模型自纠，不能在 provider 层吞成传输故障。
2. **provider 不校验工具名**（注册表层在 02c），未知名如实透传，由 loop 回 tool-result 错误。
3. **「快」档必须显式发 `thinking: {"type": "disabled"}`**——`deepseek-flash` 默认开启 thinking，省略参数不等于关闭。
4. **流式 tool_calls 按 `index` 拼接**（首片带 id+name，后续片只有 arguments 增量）；无 index 的续片续接当前调用。
5. **流式请求带 `stream_options.include_usage`**，否则 agent 每轮 token 计费恒为 0；**该参数未经真实链路验证，待 02g 确认**。
6. **重试规则（本轮新增，02g 必读）：** 首个 delta 转发之后的一切中途失败**不可重试**——`retryable` 是调用方的唯一判据，`reason` 枚举未新增值。02g 的 SSE 层必须按此实现「报错收尾」而非「重发请求」。
7. **输入契约（本轮统一，02d 必读）：** 工具名缺失/为空 → 透传 `""`（loop 回工具名错误让模型自纠）；**工具调用缺 `id` → 也透传 `""`**，由 loop 补稳定 id 并写回 assistant 消息（配对只发生在 loop 的消息列表内，见 `agent.AgentLoop._assistant_tool_calls`）。两条都由 provider 单测钉住。
8. **`messages` 原样透传** → 多轮 `reasoning_content` 回传不被剥离（ADR-007 决策 1 的实现方式）。
9. **`ai_agent_provider_model` 目前只声明、未被消费**——接线 agent 路由时**必须读它**（`cfg.ai_agent_provider_model`），勿硬编码模型名；快路径的对应项 `ai_provider_model` 是在 `service.py:182` 消费的，可照此办理。

**数据 / 同步 / AI / 安全影响：**

- **数据与同步：零影响**——无模型改动、无迁移、无同步协议改动、无前端改动。
- **AI：** agent 路径**尚未接线**（`get_agent_provider()` 无生产调用点），现有 `/ai/interpret` 快路径行为完全不变。
- **安全：** 新增攻击面是「模型可控的工具名 + 参数」（02c 承接）；密钥新增一条（`AI_AGENT_PROVIDER_API_KEY`，经 `.env` / Secrets 注入，不进仓库）；生产**不强制**该 key——agent 路径默认关闭，未配置的部署照常服务快路径。密钥不外泄已测：`str(exc)`、`caplog`、`repr(provider)` 三条路径均无密钥。

**兼容与回退方式：**

- 纯增量，**不接线即零影响**：留空 `AI_AGENT_PROVIDER_API_KEY` 时 `build_provider(AGENT)` 返回 `None`，快路径与旧客户端行为不变。
- 回退 = 恢复 `build_provider` 签名并删除 `DeepSeekProvider` 及其内部函数（约 570 行）——`DashScopeProvider` 与 qwen 调用面未被触碰，回退面干净。

**开放风险与责任人：**

- **`stream_options.include_usage` 未经真实链路验证** — 若 DeepSeek 不返回 usage 尾块，agent 每轮计费恒为 0（R-PROD-15 的成本评估会失真）。**责任人：02g**（流式接头时实测）。
- **三档推理参数的收益未经真实任务验证** — 02a 的耗时数据来自简单任务（三档差异 <0.5 s），分档的实际收益（省 token）与入口判定阈值需 02d 用真实多步用例补测。**责任人：02d**。
- **`finish_reason` 未采集** — 见上「记录不修」项。**责任人：02d**（若需区分截断）。
- **provider 层的输入契约无 conformance 断言** — 本轮把空 name / 空 id 的契约固化为用例，但**DeepSeek 其它未实测的响应形态**（如 `tool_choice` 强制、多 choice、非流式 `finish_reason=length` 下的 tool_calls）仍可能触发未分类分支；02g 真机联调是唯一能覆盖的面。

**下一项未阻塞任务：** **02d（Agent Loop 核心与推理分级）**——依赖 02b 与 02c，两者均已完成。

---

### 02c · 工具注册表与域分组（✅ 2026-09-18）

**任务编号：** T-M7-02c

**完成范围 / 明确未做：**

- **完成：** `server/src/modules/ai/tools.py`（新建）——15 个工具 / 3 域，每个含 name / domain / 读写标记（`kind`）/ description / JSON Schema parameters / 执行体。
  - **记账域** `transaction.*`（5）：`add`、`add_batch`（**写**）、`list`、`recent`、`summary`
  - **预算域** `budget.*`（5，只读）：`list`、`status`、`check`、`summary`、`overspent`
  - **账户账本域** `ledger`（5，只读）：`account.list`、`account.balances`、`book.list`、`book.detail`、`category.list`
  - **注册表能力：** `get_tool`（未知名 → `None`）、`tools_for_domains`（域分组注入）、`to_openai_tools`（OpenAI 载荷）、`load_tool_context`（名字→id 映射）、`invoke_tool`（校验 → 执行 → 降级）
- **明确未做：**
  1. **不做全量工具**——约 77 个命令对应的工具全集归 P4（T-M7-05）。
  2. **不实现 loop**（02d）；**不组装、不执行 ActionProposal**（02e）——写路径只产出草案即止。
  3. **不做域路由**——P1 三域全量注入（架构 §12.2 已定表第 2 项）；`tools_for_domains` 只是为 P4 预留的入口。
  4. **不做工具调用的进度文案**（`tool_call` 事件的人类可读描述，架构 §8.1）——归 02g 的 SSE 层。

**修改的文档、契约与代码：**

- 代码：`server/src/modules/ai/tools.py`（新建）、`server/tests/test_ai_tools.py`（新建，95 个用例）、`server/src/modules/ai/validator.py`（**1 处既有缺陷修复**，见下方「审查与修复」#1）
- 文档：本文件 §一 / §五、`docs/milestones/production-execution-plan.md` 的 T-M7-02c 行
- **契约零改动**：`contracts/action-proposal/schema.json` 与 TS 校验器一行未动

**验证命令与结果：**

```
cd server && python -m pytest tests/ -q          # 808 passed, 37 skipped（基线 713 + 本卡 95）
cd server && python -m ruff check src/ tests/    # All checks passed
cd app && npx vitest run                         # 1107 passed
cd app && npx tsc --noEmit                       # 0 错
./gradlew :app:testDebugUnitTest                 # BUILD SUCCESSFUL；394 tests / 0 failures
```

> **基线勘误：** 本文 §四 记录的基线（server 658 / JVM 392 / vitest 1104）**已过时**，实测为 **713 + 37 / 394 / 1107**。以实测为准，已同步 §四。

**四条验收的落地方式：** 逐条固化为测试而非人工核对——「action 枚举无冲突」读 `schema.json` 的真实枚举断言（`test_write_tools_declare_an_executable_contract_action`）；「每域 ≤8」`test_tools_per_domain_within_cap`；「未知工具名走降级契约」`test_unknown_tool_degrades` + `test_get_tool_unknown_returns_none`；「全量注入被排除」`test_p1_catalog_stays_minimal`（断言总数 < 70 且 ≤ 20 上限）。

**审查与修复（同卡内完成）：** 交付后由独立审查（fresh-eyes，含实跑验证）发现并修复了下列缺陷，每项都补了能在**回退修复后失败**的回归用例：

| # | 缺陷 | 触发场景 | 修复 |
|---|---|---|---|
| 1 | `format: date` 校验器对非字符串实例抛 `TypeError` | 模型把日期写成数字 `20260918`（很常见）→ **整轮 loop 500**，而非降级 | `validator.py` 的检查器加 `isinstance(str)` 守卫。**这是既有同源缺陷**——`validate_proposal` 同样受影响，修在一处两条路径同时受益 |
| 2 | 同名但收支类型不同的分类被静默合并 | 库里有「其他(支出)」「其他(收入)」时，「记一笔支出·其他」会落到收入分类的 id 上；而写入路径**没有任何环节**比对分类类型与交易类型 → **脏数据落库** | 解析改为按 `(kind, name)`；纯读过滤改为按名字匹配**全部**同名分类（读的语义是「匹配的都算」，只有写才需要唯一解） |
| 3 | `budget.summary` 的已用只累加分类预算 | 只设总预算时返回已用 `0` → 模型会对用户说「你这个月还没花预算」；有总预算+分类预算时又少算 | 总预算层（`overall`）与分类预算层（`categories`）**分开报、不相加**；`budget.list` 去掉会重复计数的合计值，消除两个同域工具的口径矛盾 |
| 4 | `load_tool_context` 不校验 `book_id` 归属 | 02e 若把客户端传来的 book_id 直接透传，所有读工具会返回**他人账本**的数据 | 加 owner + 未删除校验，不通过抛 404（与既有 `TransactionService._verify_book_ownership` 同口径）。同模块内 `book.detail` 本来就校验了 owner，此前不一致 |
| 5 | 工具 `note` 无长度上限 | 超 2000 字在 **PostgreSQL** 上以 `StringDataRightTruncation` 500；SQLite 不校验长度，测试永远发现不了 | schema 加 `maxLength: 2000`（= 列宽），把校验落在边界上 |
| 6 | 错误回喂丢掉数组下标且 `details` 重复 | `add_batch` 第 2 笔出错时，模型只看到 `amountCents` 而不知道是**哪一笔** | `required` 错误保留 `absolute_path` 前缀（`transactions/1/amountCents`）；按 `(path, rule)` 去重 |

**另有 3 处顺带修正：** `account.balances` 原先按名字分组，会把两个同名账户的金额合并成一个数（改按 id 分组）；归档分类的预算原先仍出现在 `budget.list` 里，而 `budget.status` 对同一分类返回 `unknown_category`（「看得见却查不到」）；`load_tool_context` 的查询原先无 `ORDER BY`，在 200 行上限下会返回任意子集，让名字解析结果随数据库行序漂移。

**测试缺口一并补齐：** 软删除过滤、跨账本隔离此前**都没有用例兜住**（审查时点上，把这两处过滤删掉，当时全部用例照样全绿）——现由 fixture 中的一笔软删除记录与另一个账本的一笔记录显式覆盖；月度分组的弱断言（只断言 key 存在）已加强为完整金额比对。

**第二轮复审（针对本轮修复本身，用「回退 → 测试是否失败」实测）：** 发现 3 处**由本轮修复新引入**的问题与 2 个空转测试，已全部处理。

| # | 问题 | 触发场景 | 处理 |
|---|---|---|---|
| A | 同名跨 scope 的分类被剔除后，**写路径成了永久死路** | 客户端默认分类是**全局**的（`Repository.kt seedCategory()` 建成 `bookId=null`），用户再给某个账本建一套同名分类很自然 → `category.list` 显示两行同名、写操作恒 `unknown_category`，模型**没有任何可尝试的下一步**（直接撞 §4.4「连续无进展 Step」） | 改为**本账本分类优先于同名的全局分类**（唯一约束含 `book_id`，两者可共存）：解析结果确定、符合直觉，且不留死路。读路径不受影响 |
| B | 同名账户的**读过滤变成静默空集**——比原先的「任选一个」更危险 | 两个都叫「现金」的账户 → `transaction.list {accountName:"现金"}` 返回 0 条，模型据此对用户断言「这个账户没有支出」，**是事实错误** | 读过滤改走 `account_ids_named`（基于不剔除重名的 id→name 反查，取全集）；**写**路径仍要求唯一解，但错误码区分出 `ambiguous_account`，且提示可行动（请用户指明） |
| C | `budget.status` / `budget.check` 命中多条时取 `rows[0]`，而 `_budget_rows` 无 `ORDER BY` | 支出侧与收入侧的同名分类各挂一条预算（少见）→ 「给哪条」随数据库行序漂移 | `_budget_rows` 加确定序 |
| D | `_ARG_ERROR_HINTS` 缺 `maxLength` | 超长备注的错误提示回落到笼统的「参数不合法」 | 补一行 |

**两个空转测试已修**（复审实测「把修复改回去、整文件仍全绿」，即它们根本没在验证行为）：

1. `budget.summary` 的总预算用例——fixture 里 9 月的支出**恰好全落在有预算的分类上**，新旧口径算出同一个数。已补一笔**无预算分类**的支出，使两层口径可区分。
2. `account.balances` 的按 id 分组——**完全没有覆盖**。已由同名账户用例覆盖（含「两行金额不得合并」的断言）。

两处都已用**回退实验**确认：把实现改回旧写法，对应用例立即变红。

**给 02e 的硬约束（已写进模块 docstring）：** 写工具的草案**含**服务端解析出的 `categoryId` / `accountId`——那是 ActionProposal 契约的要求、执行时必需；但**回喂给模型之前必须剔除**，否则「模型永不输出 UUID」的铁律名存实亡。

**关键设计决策（02d / 02e 必须知道）：**

1. **写工具只产出 ActionProposal 草案，不执行**（架构 §7.3）。草案含 `action` / `transactions` / `message` / `warnings`；`proposalId` / `confidence` / `requiresConfirmation` **由 02e 的组装器补齐**——它们取决于 loop 的 Step 数与审批策略，不属于工具层（`test_draft_leaves_governance_fields_to_the_proposal_assembler` 固化此约定）。补全后可直接过 `AIService.validate_proposal`（有测试）。
2. **P1 的写工具只存在于记账域。** `execute_proposal` 只放行 `add_tx` / `add_tx_batch`，其余 action 抛 400；预算 / 账户 / 账本的写操作目前由客户端本地 config 链路承载（`config` 载荷未进提案契约），迁到服务端属「ActionProposal 升 v2」，**归 P4**。边界固化为 `test_writes_are_confined_to_the_transaction_domain`。
3. **模型永不输出 UUID。** 工具参数一律收**对象名**，名→id 由 `load_tool_context` 的确定性映射解析；解析失败返回安全错误码（如 `unknown_category`）让模型自行改正。**读工具的结果里不含任何 id**（`test_list_never_exposes_ids`），避免诱导模型把 id 写回参数。
4. **金额与统计恒由 SQL 计算。** 工具只收原始参数（整数分），不接受模型的算术结论；预算「已用」口径与 `fact_pack._query_budget` 对齐（只统计未删除的支出）。
5. **降级契约不抛异常。** 未知工具名与非法参数都返回 `ToolOutcome.failure(code)`，由 loop 回喂模型纠正（架构 §11.1 的回退链不因单次工具调用失败而整体降级）。错误详情只含字段路径 + 规则名 + 中文提示，**不透传 jsonschema 原生措辞**——原生措辞会回显实例值，而实例值可能是用户原话。

**数据 / 同步 / AI / 安全影响：**

- **数据与同步：零影响**——无模型改动、无迁移、无同步协议改动。
- **AI：** 本卡是服务端 agent 能力面的地基，**尚未接线**（02d 才开始调用）。读工具只读；写工具零副作用。
- **安全：** 新增攻击面是「模型可控的工具名 + 参数」。三重防护已落地——名字白名单（`get_tool`）、Draft-07 参数校验（含 `format: date` 真断言，`2026-02-30` 被拒；`additionalProperties: false` 关闭任意字段）、名字→id 只认服务端集合（工具无论如何构造参数都拿不到别人账本里的 id，有测试）。

**兼容与回退方式：**

- 纯新增文件，**不接线即零影响**：现有 `/ai/interpret` 快路径与客户端行为完全不变。
- 回退 = 删除两个新文件（当前无人引用它们）。

**开放风险与责任人：**

- **R-PROD-17（工具集过大降低选择准确率）** — P1 每域 5 个、总数 15，均低于 §7.2 的 8 个上限。**残余：** `transaction.list` 与 `transaction.recent`、`budget.list` 与 `budget.summary` 存在语义邻接；description 已写入互斥触发语并互相引导，但**真实误选率只能等 02d 接上真实模型后实测**（本卡无 loop，无法自证）。
- **`category.list` 的域归属判断** — 架构 §3.2 把 `category.*` 划在记账域，本卡按「账户账本 = 账本结构」归入 `ledger`（`Category.book_id` 外键指向 `Book`）。域只影响注入分组，不影响功能；若 02d 的域路由需要按 §3.2 划分，改动是移动一个域常量。**责任人：02d 执行者**。
- **年度预算未覆盖** — P1 的 `budget.*` 只处理 `monthly`（`BudgetType.MONTHLY`），用户若只设了年度预算则各工具返回空。**归 P4 或按需补卡**。
- **重名账户的写入需要用户介入（有意取舍）** — `accounts` 表没有唯一性约束，两个同名账户会让**写入**解析失败（报 `ambiguous_account`，提示模型请用户指明而非自行猜测）；**读取**不受影响（按名字取全集，见「第二轮复审」B）。取舍理由：把钱记到错的账户上比让模型多问一句糟得多（数据正确性一票否决）。**代价**：这类用户无法用账户名记账，除非先给账户改名。分类不存在此问题——唯一约束含 `book_id`，同 kind 同名时按「本账本优先」确定解析（见「第二轮复审」A）。**责任人：若实测中此类用户比例可观，P4 引入「名字 + 序号」消歧交互。**
- **`category.*` 在 P1 无写入工具** — 模型不能新建/改名/归档分类（`create_category` action 存在于契约中，但 `execute_proposal` 不放行）。归 P4 的 ActionProposal v2 一并处理。

**下一项未阻塞任务：** **02b（Provider 接入与扩展）**——与 02c 同属批次 1、互不依赖；02d 需等 02b 与 02c 全部完成。

**第三轮复审（2026-09-18 收口，跨卡实测）：** 批次 1 两卡交付后做了一次收口核查，02c 侧发现并修复 5 处，另发现 **1 处跨卡阻断缺陷**（见下）。

| # | 问题 | 触发场景 | 处理 |
|---|---|---|---|
| ★ | **工具名带点号，provider 直接 400——整条 agent 路径不可用** | 15 个工具全部命名为 ``transaction.add`` 式（对齐桥命令的 ``domain.action`` 命名）。**DeepSeek 真实调用实测**：``Invalid 'tools[0].function.name': string does not match pattern. Expected a string that matches the pattern '^[a-zA-Z0-9_-]+$'`` → **400 invalid_request_error**，整轮请求失败。02b 的 MockTransport 单测**结构上不可能发现**（不经过真实接口），而 provider 层按设计不校验工具名——两层各自的正确假设合起来是个死局 | 全部改为下划线（``transaction_add`` …）；约束固化为 ``PROVIDER_TOOL_NAME_PATTERN`` 常量 + ``test_tool_names_are_provider_safe`` 兜底；**改完后用真实注册表载荷复测：200，模型正确产出 ``transaction_add`` 调用** |
| 1 | **归档账户口径不一致**：``account_balances`` 不剔除归档账户，而 ``account_list`` 与名字映射都剔除 | 归档账户仍有历史流水 → 模型看得见该账户名，用户却记不进去（``unknown_account``）——「看得见却记不了」的死路，与分类/预算侧已修过的同一类问题 | balances 加 ``Account.archived_at.is_(None)``，与另两处同口径 |
| 2 | **账本归属校验没有真正被测试兜住** | 原用例传的是**不存在的**账本 id，只证明「不存在 → 404」；删掉 ``Book.owner_id == user_id`` 一行仍全绿。02e 若透传客户端来的 book_id，这就是唯一防线 | 新增「另一个用户的库中账本」用例（存在但非本人） |
| 3 | 同名账本报 ``unknown_book``，提示**不可行动** | 两个同名账本时，``book_list`` 明明列出两行该名字，写路径却说「不在账本清单中」——模型无路可走。账户侧早已有 ``ambiguous_account``，账本侧漏了 | 增 ``ERR_AMBIGUOUS_BOOK`` + ``book_ids_named``，与账户侧同口径 |
| 4 | ``transaction_recent`` 的 description 与排序口径不符 | description 写「刚才那笔记的什么」（暗示录入序），实现是 ``biz_date DESC``。补记旧日期账目时会答错，而 description 是模型选工具的唯一语义来源（R-PROD-17） | 改成如实声明「按业务日期倒序，不是录入顺序」。**「recent 是否该按录入序」属产品口径，留给 02d/产品决定**（见残余风险） |
| 5 | 名字索引截断（``_OBJECT_NAME_LIMIT = 200``）→ 读路径**静默空集** | 分类/账户超过 200 个时，被截断掉的名字在 ``account_ids_named`` 里查不到 → 读工具返回 0 条，模型据此对用户断言「这个账户没有支出」——事实错误 | 上限提到 2000 并改写注释说明**索引必须完整**（截断会让读语义静默失真；真实数据若要逼近该值，正确做法是给 list 工具加分页，而不是在这里截断索引）；新增 205 个分类的完整性用例钉住 |

**五处修复的每一处都做了「改回旧写法 → 用例是否变红」实测，5/5 均变红。** 跨卡那处（★）用真实 API 复测：改名后完整 15 工具载荷返回 200，模型正确产出 ``transaction_add`` 调用并带上 ``amountCents``/``categoryName``/``bizDate``。

**判定为挂账、不修的项（复审已确认非「实现错误」，但都是 02d 会把结果喂给模型时要知道的边界）：**

| 项 | 影响 | 责任人 |
|---|---|---|
| ``transaction_summary`` 的分组数组无上限 | 区间由模型自选（``dateFrom``/``dateTo``），十年按天可产生数千个分组 → 顶上下文窗口、抬高每步 token 成本。与 ``transaction_list`` 已有 ``truncated`` 的做法不一致 | 02d（或按需补卡）|
| 月度分组用 ``substr(CAST(date AS String), 1, 7)`` 自造口径 | 未复用 ``statistics/service.py`` 的 ``YearMonth``（PG ``TO_CHAR``）。SQLite 与 ISO DateStyle 的 PG 上结果一致；PG 若 ``DateStyle`` 非 ISO 会得到垃圾键。测试面永远覆盖不到 | 02d / 生产联调 |
| ``budget_list`` 条目不区分收支类型 | 支出/收入各挂一条同名分类预算时，两行同名、无法分辨；``budget_status`` 取哪条只由确定序决定 | 02d |
| ``_EXECUTABLE_ACTIONS`` 是 ``service.py`` 放行集合的手抄副本 | 02e 若调整 ``execute_proposal`` 的放行集，验收断言仍会对旧集合全绿 | 02e |

**测试缺口的残余（回退实验全绿 = 无人管）：** ``category_ids_named`` 的全集语义（账户侧已有等价用例，分类侧没有）、``groupBy="day"`` 分支、``_period_range`` 的 12 月跨年分支、``created_at`` 兜底排序键。均已记录，建议随 02d 补。

**验证（第三轮后）：** ``python -m pytest tests/ -q`` → **819 passed / 37 skipped**（02c 交付时 808，02b 收口 +6、02c 收口 +5）。``ruff check src/ tests/`` 通过（唯一告警是 02d 在途文件 ``agent.py`` 缺尾换行，不属本卡）。

---

## 六、参考

- `docs/milestones/production-execution-plan.md` M7 章节 — 任务边界与验收（**权威**）
- `docs/architecture/ai-agent-architecture.md` — 目标架构（§4.4 Loop、§4.6 Provider 与推理分级、§8 流式协议）
- `docs/adr/ADR-006-ai-agent-architecture.md` — 编排位置决策
- `docs/adr/ADR-007-deepseek-provider.md` — provider 与推理策略决策
- `docs/risk-register.md` R-PROD-15 ~ 24 — M7 相关风险
- `docs/engineering/通用任务执行规范.md` — 开发实现 / Review / Bug-fix 三类工作流
- `docs/engineering/需求变更规范.md` — L1/L2/L3 变更级别与文档先行要求
- `infra/deploy/prod/nginx/DEPLOYED.md` — 服务器 nginx 实际形态（SSE 相关）