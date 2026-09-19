# T-M7-04（P3）实施推进计划

**建立：** 2026-09-19
**关系：** 本文件是 **T-M7-04（P3：会话事件日志与摘要压缩）的执行细则与进度看板**。任务边界、交接产物与验收标准以 `docs/milestones/production-execution-plan.md` M7 章节为准；本文件**不改变任何验收标准**，只细化推进方式、逐卡职责边界，并记录中间结果。
**架构与决策：** `docs/architecture/ai-agent-architecture.md`（§6.2 事件日志 + 派生投影、§6.3 上下文窗口、§6.4 Token 预算）、`docs/architecture/security.md`（会话日志的有条件例外）、`docs/adr/ADR-007`（决策 3）
**上游：** `docs/milestones/T-M7-02-P1-implementation-plan.md`（P1）、`docs/milestones/T-M7-03-P2-implementation-plan.md`（P2）——本文件 §五 承接二者留给 P3 的挂账项
**用法：** 领取任一张子卡的 Agent，先读本文件对应小节，再读 `production-execution-plan.md` 该卡的验收列。

---

## 一、状态看板

| 卡 | 内容 | 状态 | 完成日 | 提交 | 备注 |
|---|---|---|---|---|---|
| **04a** | 事件日志完整化与 Surface 子集 | ✅ 完成 | 2026-09-19 | `e9203b0` | 批次 1；P3 基座。Surface 子集已落为单一来源常量，`history()` 只投它 |
| **04b** | 会话恢复与分叉（含轮中途落库） | ✅ 完成 | 2026-09-19 | `c44b723` | 批次 2；写入粒度改**逐事件**（批＝配对组，故「半截落库」在结构上不可能）；恢复＝同 `conversation_id` 再跑一轮 + 客户端按 `after_seq` 续读；分叉端点；`CANCELLED` 复审：loop 层可达、生产入口不可达（刻意） |
| **04c** | 摘要压缩与预算（`replace`） | ✅ 完成 | 2026-09-19 | `6026646` | 批次 3；触发条件＝**窗口溢出**（不另立 token / 时间尺子）；摘要在轮末 `done` 之后生成、经系统提示注入；`replace` 事件 + `summary` 列；编译期零依赖，改动面＝服务端 |
| **04d** | 保留期与隐私边界落地 | ✅ 完成 | 2026-09-19 | `06dedfc` | 批次 2；窗口 **30 天**、按**整轮**物理删除（不变式 1 不破）；只删事件行，会话行与 `ai_usage` 不动；读取面审计结论＝不新增记录；四端点归属校验复审通过；**与 04b 的交汇面已核对**（分叉复制原 `time` → 保留期对分叉无需特判） |
| **04e** | 跨设备会话同步（决策门） | ✅ 完成 | 2026-09-19 | `5da7364` | 批次 3；**决策＝正文按需重建、不做同步实体**（`ADR-008` / `D-PROD-14`），经账户所有者确认。「仅摘要同步」被否决（摘要无生产者，且只覆盖超窗轮次）。落地＝**纯客户端 + 文档**：零新协议、零新端点、零服务端改动 |
| **收口** | P3 四板 + 断连恢复真机走查 | ⏳ 未开始 | | | 批次 4 |

**状态取值：** ⏳ 未开始 / 🔄 进行中 / ✅ 完成 / ⛔ 阻塞（阻塞须在本表备注列写明原因与解除条件）

---

## 二、批次与依赖

```
批次 1  04a ─┬─→ 批次 2  04b ─┐
             ├─→ 批次 2  04d ─┼─→ 批次 3  04c ─┐
             └────────────────┴─→ 批次 3  04e ─┴─→ 批次 4 收口
```

| 批次 | 卡 | 可并行 | 说明 |
|---|---|---|---|
| 1 | 04a | — | 事件日志形态与 Surface 子集是 P3 全部能力的共同基座，必须最先 |
| 2 | 04b、04d | ✅ 文件面不同 | 04b 改写路径与恢复；04d 改 retention 与文档。重叠仅 `conversation.py` |
| 3 | 04c、04e | ✅ 互不依赖 | 04c 依赖 04b 的写路径定型；04e 是决策门，先决策后落地 |
| 4 | 收口 | — | 四板 + 断连恢复真机走查 |

**并行切分点：** 批次 2 的 04b / 04d 与批次 3 的 04c / 04e 是两组安全切分。04a 独占批次 1 是硬约束——事件结构不定，恢复 / 压缩 / 保留期都会返工。

**前置阻塞：已解除（2026-09-19 核对）。** `production-execution-plan.md` 曾登记「`docs/architecture/security.md` 的绝对禁令须先在该文档层面解决方可开工」。该条款**已于 2026-09-18 修订**（`security.md:7` 与 `:9` 的修订记录：记「完整对话」由绝对禁令改为**有条件例外**，约束三条——保留期对齐 `D-PROD-08`、登出 / 删号联动清理、读取面带归属校验）。其中前两条与第三条已在 02f 落地两条，**残余的保留期机制即 04d**。本文件与执行规划已同步修正该条目的状态。

---

## 三、逐卡细则

> 每卡均含「内容 / 职责边界 / 产出 / 验证」。**职责边界是防范围蔓延的关键，不得越界**。

### 04a 事件日志完整化与 Surface 子集

- **内容：** 把 P1 的最小事件表（`AIConversationEvent`：`seq` / `turn` / `type` / `data` / `created_at`，`server/src/models/ai.py:103`）演进为**完整事件日志**——补 `time`（显式事件时间）与 `ignorable`（结构性事件标记）；`type` 扩为全集（用户输入 / 模型回复 / 工具调用 / 工具结果 / 审批请求 / 审批结果 / 设备指令 / 错误 / 摘要替换）。**开工第一件事：核对 `type` 的 `String(16)` 是否放得下全集**——`approval_request` 恰为 16 字符，`device_request`（P1 已用）为 14，须实测确认或加宽后再写迁移。**Surface 事件子集定义**（架构 §6.2 投影 A）：明确哪些 `type` 进模型上下文、哪些只进投影 B（UI）与投影 C（审计），并落到代码里的单一来源常量。`conversation.history()` 改为**只投 Surface 子集**；`_prepare_events` / `_window_chars` 相应改造。配额与安全事件入日志（投影 C）。
- **职责边界：** **不做摘要压缩**（04c）、**不做恢复与分叉**（04b）、**不做保留期**（04d）。**不改下行 SSE 的八类事件**——那是流式协议面，与日志 `type` 是两个不同的枚举，不得混为一谈。
- **产出：** `server/src/models/ai.py`、新迁移（编号紧接 P2 的 03a）、`server/src/modules/ai/conversation.py`、`server/tests/test_ai_conversation_log.py`
- **验证：** **实测「结构性事件不占 token」**（同一轮次在「有 / 无结构性事件」两种形态下的请求载荷 token 对比，结论入档）——这是 P3 出口标准之一，不接受「按设计应当如此」的推论；Surface 子集逐类用例；事件类型全集可写可读（含长度边界）；**非 Surface 事件不得出现在 `messages` 里**；投影 B 的读取仍返回全部事件；既有 `test_ai_conversation.py`（676 行）不回归。

### 04b 会话恢复与分叉（含轮中途落库）

- **内容：** **轮中途落库**——P1 是「一轮原子写入」（02f 决策 5），导致流式路径无法在轮中途落库、断连即「用户看完了答案、历史里什么都没有」（02g-2 决策 5 的已知代价）；本卡把写入粒度改为**逐事件**。**恢复：** 按 `conversation_id` + `seq` 续跑（`GET /ai/conversations/{id}/events?after_seq=` 由 P2 的 03a 提供，本卡给 loop 侧的恢复入口与语义）。**客户端断连重连续跑**（与 03e 的中断提示衔接）。**会话分叉**——从某 `seq` 起新会话，不复制消息数组、原会话不变。**`StopReason.CANCELLED` 可达性复审**（P1 不可达，P2 的 03e 定义主动取消后应可达）。
- **职责边界：** **不做压缩**（04c）、**不做保留期**（04d）。**不改审批语义与写路径的安全门**（02e 的组装 / 校验 / 审批 / 执行一行不动）。
- **产出：** `server/src/modules/ai/agent.py`、`conversation.py`、`routes.py`、客户端 `use-assistant-stream.ts`（P2 03b 的产物）与桥层增量、两侧测试
- **验证：** 断连后按 `conversation_id` + `seq` 续跑（服务端用例 + 客户端用例）；**轮中途落库后断连仍有完整历史**；分叉后原会话内容不变、新会话可独立续跑；四条不变式（架构 §4.4）在恢复路径上仍成立——**特别是第 1 条（工具调用与结果配对）不得因半截落库而被破坏**；「看到了但没落库」窗口关闭（§五 第 2、3 项）。

### 04c 摘要压缩与预算（`replace`）

- **内容：** **压缩触发条件决策**（轮数 vs token vs 时间，架构 §12.2 未决 #3），结论入档。**摘要生成**：provider 调用产出摘要，**不含 UUID / 内部 ID**、上限 800 字符（架构 §6.4）。**`replace` 事件**——长历史被替换为摘要，**原文仍留在日志中**（可审计、可回溯），不得删除。**摘要注入** `AIConversation.summary`（P1 预留的空串字段，`models/ai.py:63`）与后续轮次上下文。**Token 预算落地**（架构 §6.4：单轮输入 500 字符不变、历史窗口按 N 轮 + 字符双限、摘要 800 字符、超限用上一版摘要）。**失败步 token 估算评估**——P1 判定为「记录不修」的一项（见 §五 第 4 项），本卡给出最终结论：修或明确不修。
- **职责边界：** **不做保留期**（04d）。**不改 Surface 子集定义**（04a）。**原文不得删除**——压缩是新增一条 `replace` 事件，不是覆写。
- **产出：** `server/src/modules/ai/conversation.py`、`prompts.py`（摘要模板）、必要时 `agent.py`、`server/tests/test_ai_conversation_summary.py`
- **验证：** **压缩后原文仍可查**（审计用例，P3 出口标准）；摘要不含 ID；结构性事件不占 token 的实测在压缩前后结论一致；摘要生成失败时回落上一版摘要且不阻塞本轮；超窗轮次经 `replace` 替换后**多轮澄清用例仍正确**（沿用 02f 的「问 → 追问 → 简答『500』」范式）；历史窗口 N 与字符上限的**初始值复核**（§五 第 5 项）。

### 04d 保留期与隐私边界落地

- **内容：** **这是 `security.md` 三条约束中唯一未落地的一条**（另两条——登出 / 删号联动清理、读取面归属校验——已由 02f 交付）。落地**保留期机制**：对齐 `D-PROD-08`（请求后立即冻结、7 天内完成物理删除）；复用既有 retention 任务范式（`server/src/modules/telemetry/retention.py` 的 `compute_retention` / `retention_loop`，`RETENTION_JOB_ENABLED` 显式开启的口径）。**访问面审计**：读取会话日志这一行为本身是否需要记录，须给出结论。**读取面归属校验复审**（02f 已实现跨用户 404），确认在 03a 新增的四个端点上同样成立。
- **职责边界：** 仅服务端 + 文档。**不改事件结构**（04a）、**不做压缩**（04c）。**不得把保留期做成「删会话行」**——须与配额基座解耦（02f 已用 `ON DELETE SET NULL` 保住 `AIUsage` 的日配额计数，本卡不得破坏该口径）。
- **产出：** `server/src/modules/ai/conversation.py`（retention 接线）、`server/src/common/config.py`（保留期参数与开关）、`infra/cleanup/`（如涉及）、`docs/architecture/security.md`（如需细化条款）、保留期数值入档
- **验证：** 保留期任务按策略删除到期会话事件，且**不影响 `AIUsage` 的配额基座**（回归 02f 的口径用例）；长期不登出的用户其日志不再无限留存（§五 第 1 项）；跨用户读取在四个新端点上仍 404；`security.md` 三条约束逐条有对应实现或有明确结论。

### 04e 跨设备会话同步（决策门）

- **内容：** 架构 §12.2 未决 #6「会话正文是否跨设备同步（摘要已定同步，正文待定）」。**先出决策、后落地**：给出方案对比（仅摘要同步 / 正文同步 / 不同步）与建议，落 `docs/adr/` 或架构 §6.1 修订，**经账户所有者确认后**才动代码。
  - 结论为「仅摘要同步」→ 服务端摘要进同步面，客户端投影缓存不跨设备；
  - 结论为「正文同步」→ 客户端投影缓存降级为同步实体（接入现有 sync 模块，增量，不新协议），并定义冲突策略；
  - 结论为「不同步」→ 本卡**退化为文档更新 + 现有行为确认**，并须写明「换设备后旧会话如何呈现」的明确行为。
- **职责边界：** **决策未定不动代码。** **不做保留期**（04d）。**不改变「服务端为会话权威」的模型**（架构 §6.3）。
- **产出：** 决策文档（ADR 或架构修订）+（按结论）sync 模块与客户端增量
- **验证：** 决策落文档且经确认；按结论给出对应验证（仅摘要 → 换设备后摘要可见、正文按既定行为；正文同步 → 双端一致 + 离线可读 + 冲突用例）；不同步结论下须有「换设备后旧会话如何呈现」的行为说明。

### 收口（批次 4）

- P3 四板全绿（见 §四）
- 真实断连恢复走查：流式中杀进程 / 切网 / 切后台后再进入，按 `conversation_id` 续跑
- P3 出口标准逐条核对（`production-execution-plan.md` T-M7-04 验收列）

---

## 四、推进机制

### 提交规则

- **每卡一次独立提交**，**逐文件精确 add**（`git add <本卡文件>`），**绝不 `git add -A`**。**并发会话下用 `git commit --only <路径>`**。
- 跨卡共享文件（`conversation.py`、`agent.py`、`use-assistant-stream.ts`）由**后完成**的卡负责解决冲突。

### 验证基线（每卡完成后必跑）

```
cd server && python -m pytest tests/ -q          # 每卡只允许在基线上增加，不得减少
./gradlew :app:testDebugUnitTest --rerun-tasks   # gradlew 在仓库根目录，须从仓库根运行
cd app && npx vitest run
cd app && npx tsc --noEmit                       # 0 错
```

> **基线数值以开工时实测为准，不在本文件写死。** 参考：02g-2 交付后（2026-09-19）服务端 1029 passed + 37 skipped / JVM 394 tests / vitest 1107 passed / tsc 0 错；P2 完成后须重新实测。
> ⚠️ **多会话陷阱（继承自 P1 计划 §四）：** 并发会话下跑全量套件的数字**不可作为本卡基线**，须以 `pytest <本卡测试文件>` 的增量与 `--collect-only` 为准。

### 阻塞处理

- 卡内发现需改**架构决策**时**停下来先改文档**再动代码（含本卡的 04e 决策门）。
- 出现阻塞：本文件 §一 状态看板改为 ⛔ 并写明「原因 + 解除条件」，同时更新 `docs/risk-register.md`。
- **`security.md` 属绝对禁令面**——本阶段任何改动只能**在已修订的有条件例外范围内**进行，token / 原始票据 / 账本明细三项禁令不得触碰。

### 卡完成时的记录义务

每卡完成后，在本文件 §六 追加一条中间结果记录，按 `production-execution-plan.md` 末尾的**交接模板**填写。

---

## 五、承接自 P1 / P2 的挂账项

以下条目由上游计划明确指派给 P3，**不得在 P3 中丢失**：

| # | 挂账 | 来源 | 落卡 |
|---|---|---|---|
| 1 | **无保留期机制**——会话日志只靠登出 / 删号清理，长期不登出的用户其日志会一直留存 | 02f §五 | **04d** |
| 2 | **失败轮不落库**——断连或 provider 中途失败时本轮消息不进历史，下一轮没有这段上下文 | 02g 决策 5 | **04b** |
| 3 | **「看到了但没落库」的窗口变大**——02g 的「正文与落库几乎同时」变成「整段正文期间」 | 02g-2 决策 5 | **04b** |
| 4 | **失败那一步的 token 捞不回来**——`deepseek-flash` 的 usage 在尾块，流断了就是零；P1 判定「记录不修」（加分支只会得到真实链路下永远取不到值的空转代码） | 02g-2 §五 | **04c** ✅ **已闭环**：维持不修，依据见 §六 04c 记录 |
| 5 | **历史窗口 N=8 与字符 8000 是初始值**，多轮窗口的实际收益与超窗频率要等接通后统计 | 02f §五 | **04c** ✅ **已闭环**：实测复核维持 8 / 8000（架构 §12.2 未决 #2）；真实超窗频率待生产中统计 |
| 6 | **一轮原子写入意味着流式路径不能在轮中途落库** | 02f §五 | **04b** |
| 7 | **会话正文是否跨设备同步**（架构 §12.2 未决 #6） | 架构 §12.2 | **04e** ✅ **已闭环**：正文**按需重建**、不做双向同步实体（`ADR-008` / `D-PROD-14`，经账户所有者确认）；见架构 §12.2 已定表第 11 项 |
| 8 | **摘要压缩触发条件**（轮数 vs token vs 时间，架构 §12.2 未决 #3） | 架构 §12.2 | **04c** ✅ **已闭环**：窗口溢出，见架构 §12.2 已定表第 10 项 |
| 9 | **会话事件日志的保留期具体值**（边界已定，见架构 §12.2 已定表第 7 项） | 架构 §12.2 | **04d** |
| 10 | **R-PROD-19 的恢复侧**——断连重试 + 按 `conversation_id` 恢复状态（P2 只做提示与重连入口） | P2 计划 §五 | **04b** |
| 11 | **审批两类事件仍无生产者**——04a 把 `approval_request` / `approval_result` 的**生产者**记在 04b 名下（「恢复需要它们重建卡片」），04b **判定不接**：见下「04b 中间结果」的开放风险。归属改为「谁要做『从日志重建待确认卡片』谁接」 | 04a §六 | **未指派**（P4 收口时若无需求则关闭）。**04e 复核后维持未指派**：04e 的「本地有投影就不重建」规则使这件事**不构成回归**（本机看过的卡片不会被抹掉），代价只在「没缓存过的设备上看不到那几轮的记账卡」，已记入 `ADR-008` 的残余表 |

> **本表只记「谁承接」**——条目是否闭环看 §一 状态看板与 §六 各卡的中间结果记录。

---

## 六、中间结果记录

### T-M7-04c 摘要压缩与预算（`replace`）（2026-09-19，`6026646`）

```text
任务编号：T-M7-04c（P3 批次 3，依赖 04b 的写路径定型）

完成范围 / 明确未做：
  完成——压缩**触发条件决策**（窗口溢出）；摘要生成（provider 调用、不含 ID、
  ≤800 字符）；`replace` 事件（原文一行不删）；摘要注入 `AIConversation.summary`
  与下一轮的系统提示；Token 预算落地（新增 `ai_agent_summary_chars`）；失败步
  token 的**最终结论**（维持不修，用测试钉死）；历史窗口 N=8 / 字符 8000 的
  初始值**复核**（维持）。
  **明确未做**（守职责边界）：不改 Surface 子集定义（04a 一行未动）、不改保留期
  的既有口径（04d 一行未动）、不改写路径的安全门（02e 一行未动）、不做跨设备
  同步（04e 决策门）、**不改 API 响应契约**（`schemas.py` 一行未动）。

修改的文档、契约与代码：
  文档（先行）：架构 §6.2（「压缩」行 + 事件类型表的 `replace` 生产者）、§6.3
    （窗口行的复核结论、压缩行的落地形态、反幻觉两道）、§6.4（摘要行的执行口径）、
    §12.2（已定表新增第 10 项＝触发条件；未决 #2 / #3 标记闭环，#2 附实测量）。
  服务端：`prompts.py`（新 `CONVERSATION_SUMMARY` 模板 + 入 REGISTRY）、
    `conversation.py`（`summarize_overflow` / `_covered_turn` / `_generate_summary`；
    `history()` 抽出 `_surface_turns` + `_window` 供压缩共用；`fork` 从**前缀**派生
    摘要；`delete` 清摘要；模块级 `_upto_turn` / `_replace_summary_in` /
    `_usable_summary` / `_render_summary_input` / `_clip_summary_text`）、
    `agent.py`（`run(summary=…)` + `_system_message` 拼摘要段）、
    `stream.py`（`run` 传 summary；`done` **之后**调用压缩，异常全吞）、
    `config.py`（`ai_agent_summary_chars=800`）、`retention.py`（新
    `purge_expired_summaries` + sweep 接线，**并删掉一段 `return` 之后的死代码**——
    04d 的编辑残留，与本卡新增的两行日志同处一个函数，留着会误导下一位读者）、
    `models/ai.py`（summary 列 docstring）、`.env.example`。
  测试：新 `tests/test_ai_conversation_summary.py`（32 例）；
    `test_agent_stream.py` +5（`TestCompression`）；`test_ai_conversation_retention.py`
    两条返回契约断言随 `cleared_summaries` 更新（不是放宽断言）。

验证命令与结果：
  **干净 HEAD + 仅本卡文件**（临时 worktree `git worktree add --detach HEAD` @1333c1d，
  铺入本卡 11 个文件后跑）：
    python -m pytest tests/ -q → **1212 passed / 37 skipped / 0 failed**
    HEAD 基线同法实测 **1175 passed / 37 skipped** → 本卡 **+37 例、零减少**
    python -m ruff check src/ tests/ → All checks passed
  四板（工作区）：pytest **1211**、vitest **1266 passed**、tsc **0 错**、JVM
    `testDebugUnitTest` **424 tests / 0 failures / 1 skipped**。
    ⚠️ **客户端三板本卡零改动**——工作区此刻含着并行会话的在途客户端改动
    （`app/src/features/assistant/*` 与两个新文件），那三个数字是「没有被本卡破坏」
    的旁证，**不是本卡的证据**。
  **真实 PG 取证**（dev 库 aozora，PG 17.11，全程一个事务、结束 rollback、零副作用）：
    造两个会话各带一条 `replace`：A 的第 1 轮拨到 60 天前 → 清扫删 2 行、
    **summary 清空**；B 的第 1 轮仍新 → 一行不删、**summary 保留**；
    `run_retention_sweep` 返回 `{deleted_events: 2, cleared_summaries: 1}`；
    会话行（title）未动；回滚后残留 0。**这条取证的必要性**：`purge_expired_summaries`
    用 `tuple_(...).in_(list(...))` 的**行值 IN**，SQLite 与 PG 的语法不同，只在内存
    SQLite 上测过不算数。
  **反向验证**（守则 §3.2，10 处变异全部命中预期用例后才恢复）：①窗口的字符双限
    失效→字符溢出用例红；②摘要长度上限不校验→超限用例红；③摘要不查内部 ID→含 ID
    用例红；④增量覆盖边界写成 `<=`→增量用例红；⑤分叉复制父会话的摘要列→「切点在
    `replace` 之前」用例红；⑥摘要提示放行工具消息→工具消息用例红；⑦删除会话不清
    摘要→删除用例红；⑧保留期不清摘要→保留期用例红；⑨摘要不进系统提示→系统提示
    用例红；⑩轮末压缩的异常不再吞掉→崩溃用例红。
    **三处首轮未命中，全部是测试的覆盖缺口而不是实现的缺陷**：④只断言 `upto_turn`
    分不出来（两种情况都是 2）→ 补「第 1 轮原文不得再进提示」一条断言；⑨⑩的变异
    锚点/靶子选错（⑩的那条测试打的是 provider 失败，而它在 `summarize_overflow`
    内部就被吞掉了，走不到 stream 层的那道 try）→ 补
    `test_compression_crash_does_not_break_the_turn`（monkeypatch 让压缩自身抛）。

数据/同步/AI/安全影响：
  · **AI 成本**：R-PROD-15 的主要残余「长期成本无缓解手段」**关闭**——窗口之外的
    历史不再被无条件丢弃，而是压成 ≤800 字符的摘要继续参与上下文。新增残余两条
    见风险册（摘要调用不进 `ai_usage`；净收益待校准）。
  · **数据**：`ai_conversations.summary` 由空串变为真实内容；事件表**零改动**
    （不需要迁移）；`replace` 类型早由 04a 定义，本卡只是接上它的生产者。
  · **安全**：摘要**纳入保留期**（正文删了摘要必须一起消失，否则正文保留期被从
    后门绕过）；摘要的**输入侧**不喂工具消息、**输出侧**校验不含 UUID，两道都在；
    会话删除时摘要一并清空。摘要不改变 `security.md` 的边界——它仍是有条件例外
    范围内的会话内容。
  · **同步**：无影响（`summary` 不进 sync 协议；正文与摘要的跨设备同步是 04e 的
    决策门）。

兼容与回退方式：
  纯增量：新方法 + 新配置项 + 新提示模板 + 一个新的事件生产者。**API 响应契约零
  改动**（`schemas.py` 一行未动），**客户端零改动**（`summary` 不经任何端点外溢）。
  `AIConversation.summary` 列早已存在且默认空串，**不需要迁移**。
  回退 = 把 `stream._execute_turn` 里那次 `summarize_overflow` 调用摘掉（`summary`
  列与已写的 `replace` 事件留在库里，不读即无影响）；再彻底一点去掉
  `_system_message` 的摘要段——`AgentLoop.run(summary=…)` 默认空串，老调用点行为不变。
  **两个必须知道的口径**：①摘要生成在轮末 `done` **之后**，**本轮用的永远是上一版
  摘要**，新摘要从下一轮生效（本轮窗口在读历史那一刻就定了）；②压缩**尽力而为**：
  provider 报错 / 摘要超限 / 摘要含内部 ID 一律**整条退回**且什么都不写，这一轮照常
  结束——回退路径与「没做压缩」逐字相同。

开放风险与责任人：
  · **摘要调用的成本不进 `ai_usage`**（R-PROD-15 的新残余）——会话级「轮数 + token」
    双限不约束它。量小（每轮至多一次、输出 ≤800 字符），但成本可见性有缺口。
    责任人：M7 收口评估是否单列。
  · **摘要的净收益待校准**（省下的历史 token 是否值得它自己的开销），与窗口 N /
    字符上限 / 50 轮 / 500k token 同批，按真实使用分布。
  · **摘要描述的内容可能已被保留期删掉一部分**（覆盖范围中段先到期）——有意偏保守，
    口径见 `retention.purge_expired_summaries` 的 docstring 与风险册 R-PROD-20。
  · **04b 的挂账「审批两类事件仍无生产者」不变**（§五 第 11 项）——04c 不涉及。
  · **R-PROD-29**（对已过保留期的会话分叉会静默得到「无正文的会话」）问的是
    「留给 04c 还是收口」——**本卡结论：留给 M7 收口**（属分叉语义面，与压缩无关）。

下一项未阻塞任务：**04e**（跨设备会话同步，决策门——已由并行会话开工，见
`docs/adr/ADR-008-conversation-cross-device.md`）与**批次 4 收口**（P3 四板 +
断连恢复真机走查）。04c 给收口的输入：压缩是**尽力而为**的旁路（不产生任何一帧）、
摘要只在系统提示里出现、以及上面四条开放风险。
```

### T-M7-04a 事件日志完整化与 Surface 子集（2026-09-19，`e9203b0`）

```text
任务编号：T-M7-04a（P3 批次 1，P3 全部能力的共同基座）

完成范围 / 明确未做：
  完成——事件表补 time / ignorable；type 扩为全集并加宽列；Surface 子集落为
  单一来源常量；history() 只投 Surface 子集；配额事件入日志（投影 C）。
  **明确未做**（守职责边界）：不做压缩（04c）、不做恢复与分叉（04b）、不做保留期
  （04d）；不改下行 SSE 的八类事件（那是协议面，与日志 type 是两个枚举）；
  **不改 API 响应契约**（`schemas.py` 一行未动——`AIConversationEventItem` 只声明
  了 seq/turn/type/data/created_at，新列不会外溢，02h 冻结的客户端桥零影响）。
  审批 / 设备 / 错误 / 安全四类**只定义类型与写入口，无生产者**——理由见下。

修改的文档、契约与代码：
  文档（先行）：`docs/architecture/ai-agent-architecture.md` §6.2——「事件类型」行
    展开为「事件类型全集与 Surface 子集」表（语义 → type 值 → 是否进投影 A →
    生产者），并写明「与 SSE 八类是两套独立枚举」「列宽 32 的实测依据」。
  代码：`server/src/models/ai.py`（time / ignorable / type(32)）、新迁移
    `0020_conversation_event_log`、`server/src/modules/ai/conversation.py`
    （常量分档 + history 白名单下推 + append_events + record_turn 写 quota 事件）、
    `server/src/modules/ai/stream.py`（成功路径 record_turn 传 same_turn=True，1 行）。
  测试：新 `server/tests/test_ai_conversation_log.py`（38 例）；既有
    `test_ai_conversation.py` / `test_agent_stream.py` / `test_ai_conversations_api.py`
    共 3 处**事件计数断言的期望值**随 quota 事件更新（不是放宽断言）。

验证命令与结果：
  cd server && python -m pytest tests/ -q
    → 1113 passed / 37 skipped（基线 1075 的收口值 + 本卡 38 例）
  ./gradlew :app:testDebugUnitTest --rerun-tasks → BUILD SUCCESSFUL
  cd app && npx vitest run → 1237 passed
  cd app && npx tsc --noEmit → 0 错
  python -m ruff check src/ tests/ → All checks passed
  **实测「结构性事件不占 token」**（验收要求，不接受设计推论）：同一轮次在
    「带 3 条结构性事件 / 不带」两种形态下，第二轮 loop **实际发出的请求载荷
    逐字节相同**（token 是载荷的函数）；对照组（把结构性事件也拼进窗口，即 04a
    之前的行为）载荷多 160 字符。用例：
    `test_payload_is_byte_identical_with_and_without_structural_events` /
    `test_control_structural_events_would_have_cost_this_much`。
  **列宽实测**（PG 17.11）：varchar(16) 按字符数计，全集最长的
    `approval_request` 恰为 16 字符——顶格、零余量；17 字符即被拒
    （StringDataRightTruncationError）。故加宽到 32。
  迁移校验：dev 库 `alembic upgrade head` 已在 0020，列定义实测
    （type=varchar(32) / time=timestamptz NOT NULL DEFAULT now() /
    ignorable=boolean NOT NULL DEFAULT false）；downgrade 的四条语句在真实 PG 的
    **事务内**执行通过并完全回滚（42 行原样）——零副作用取证。

数据/同步/AI/安全影响：
  · **AI 成本**：R-PROD-15 的核心残余（P1 全部事件进上下文）**关闭**——结构性
    事件连 history() 的返回值都进不去，白名单下推到 SQL，取都不取。
  · **数据**：`ai_conversation_events` 加两列、加宽一列。downgrade 会丢
    time/ignorable 两列的值（回退路径的已知代价）。
  · **安全**：配额用量现在有会话内留痕（`quota` 事件，含 provider/model/token
    数，不含用户原话、不含 request_id）。日志面因此仍是 security.md 有条件例外
    的适用范围；保留期（04d）须把这批事件一并纳入。
  · **同步**：无影响（事件日志不进客户端同步面；正文同步是 04e 的决策门）。

兼容与回退方式：
  加列 + 加宽列，**向后兼容**：旧代码读新表无感（多余列被忽略），API 响应契约
  未变，客户端零改动。回退 = `alembic downgrade 0019_conversation_index`（丢
  time/ignorable 的值，type 超过 16 字符的行会被左截断——迁回旧代码本来就认不出
  那些类型）。
  **一个必须知道的口径**：`time` 由写入方取（`datetime.now(UTC)`），`created_at`
  是 PG 的 `now()` = **事务**时刻。一轮 loop 跑完才落库，dev 实测二者相差数秒。
  要「这一轮何时开始」看 created_at，要「事件何时写下」看 time——04b 逐事件落库
  后这层分工才完全展开。

开放风险与责任人：
  · **审批 / 设备 / 错误 / 安全四类暂无生产者**（类型与 `append_events` 写入口
    已就位）。归属：审批两类随 **04b** 的逐事件落库接上（恢复需要它们重建卡片）；
    `error` 随 **04b**；`replace` 是 **04c**；`device_request` 是 **P4**（架构
    §8.1 的同名 SSE 事件至今也无生产者）。
  · **`security` 事件无干净落点**——已核查：跨用户访问在 `load_owned` 抛 404 时
    **没有可比挂载的会话**（别人的 id 不能挂，调用者自己可能一个会话都没有），
    而配额拒绝路径的写入必然随业务事务回滚（路由层 429 后 session 回滚，写了也是
    空转代码——02e「假防御」的教训）。**结论：它的生产者落点与「读取面访问审计是
    否需要记录」是同一个问题，一并归 04d**（04d 任务卡已含该结论项）。
  · 本卡**未**把 time / ignorable 加进 `GET /ai/conversations/{id}/events` 的响应
    ——客户端此刻不需要它们，契约变更留给真正要用的卡。

下一项未阻塞任务：**04b**（会话恢复与分叉，含轮中途落库）与 **04d**（保留期与
隐私边界）可并行开工（批次 2）。04b 的直接输入：Surface 子集常量、事件表新列、
以及本卡记录的「time 与 created_at 的分工」。
```

### T-M7-04b 会话恢复与分叉（含轮中途落库）（2026-09-19，`c44b723`）

```text
任务编号：T-M7-04b（P3 批次 2）

完成范围 / 明确未做：
  完成——写入粒度由「一轮原子」改为**逐事件**（落库批的边界＝配对组的边界，逐批
  提交）；恢复（同 conversation_id 再跑一轮即续跑 + 客户端按 after_seq 续读，
  done 帧新增 seq 作为游标）；会话分叉端点；error 事件接上生产者；StopReason.
  CANCELLED 可达性复审。
  **明确未做**（守职责边界）：不做压缩（04c）、不做保留期（04d，由并行会话交付）；
  **不改审批语义与写路径的安全门**（02e 的组装 / 校验 / 审批 / 执行一行未动）；
  **不接 approval_request / approval_result 的生产者**；不做分叉的客户端 UI 入口。

修改的文档、契约与代码：
  文档（先行）：architecture §4.4 增「不变式 1 的四道防线」、§6.2 补「写入粒度」
    行与恢复 / 分叉的落地形态、§6.2 事件类型表的生产者列（user/assistant/tool/
    error）、§8.1 的 done 载荷（+seq）、§8.3 的取消复审结论、§8.5 的半截文本契约。
  服务端：agent.py（MessageSink 通道 + MessageStamp 逐事件时间 + 五个产出点按批
    外送）、conversation.py（TurnWriter / max_seq / commit / fork /
    _paired_prefix_len / history 尾部保证干净 / append_turn·_insert_events 接受
    逐条 times）、stream.py（注入 sink、去掉整批 append_turn、_record_failure 写
    error 事件、done 带 seq）、routes.py + schemas.py（POST /ai/conversations/{id}/
    fork）、models/ai.py（两处过期措辞）。
  客户端：桥新增 conversation.events（**计数 82→83**，Kotlin BridgeCommands.ALL +
    SecurityConfigTest 计数守卫 + bridge-types.ts + native-bridge.test.ts 四处同步）；
    use-assistant-stream.ts（游标 + 失败收尾前续读 + recoveredMessages 三条裁剪规则）；
    use-assistant.ts（补记落在中断提示之前）。
  测试：新 server/tests/test_ai_conversation_resume.py（16 例）；test_ai_
    conversations_api.py 增 TestForkConversation（6 例）；test_agent_stream.py 的
    3 条 P1 契约用例**改写**为新契约（断连不再「什么都不留」、失败轮留 error 事件）；
    客户端 assistant-stream-projection.test.ts（+6）、use-assistant-agent-stream.
    test.ts（+6，含桥能力缺席与续读失败两支）。

验证命令与结果：
  cd server && python -m pytest tests/ -q
    → 1172 passed / 37 skipped（**并发会话下的全量数字不作本卡基线**；本卡增量＝
      test_ai_conversation_resume 16 例 + 分叉 6 例＝22 例）
  cd server && python -m ruff check src/ tests/ → All checks passed
  ./gradlew :app:testDebugUnitTest --rerun-tasks → BUILD SUCCESSFUL（423 tests）
  ./gradlew :app:compileDebugAndroidTestKotlin --rerun-tasks → BUILD SUCCESSFUL
    （守则 §3.3：改主源码后必须显式编译 androidTest 源码集）
  cd app && npx vitest run → 1249 passed（04a 基线 1237 + 本卡 12）
  cd app && npx tsc --noEmit → 0 错
  **反向验证**（守则 §3.2，9 处变异全部命中预期用例后才恢复）：
    服务端 5 处——① TurnWriter 不提交 → 逐事件落库用例红；② 配对组拆成两批 →
      5 例红（写在真实可达路径上）；③ _paired_prefix_len 恒返回全长 → 4 例红
      （含分叉取整与读侧裁剪）；④ history 不裁尾巴 → 读侧兜底用例红；⑤ fork 顺手
      动原会话的 last_active_at → 「原会话不动」用例红。
    客户端 4 处——① 失败收尾不退化成直接 settle → 续读 2 例红；② 补记不落进消息流
      → 同 2 例红；③ 不按轮裁剪 → 规则①用例红；④ 不去重 → 2 例红。
  自洽性：`git show HEAD:...` 复核 HEAD 里不引用任何未提交产物（04d 在
    conversation.py 留的 6 行已摘出本提交，工作区已还原）。

数据/同步/AI/安全影响：
  · **数据**：同一会话的事件行由「一轮一批」变成「一批一组」，条数不变、seq 语义
    不变；`time` 现在逐条给（同一批内 assistant 与工具结果不再共用时间戳）。
    04d 的保留期按 turn 分组取 max(time) 删整轮，与该变化兼容（已在那边注明）。
  · **AI 成本**：无新增模型调用；断连不再让已完成的步骤白跑（下一轮带着它们继续）。
  · **安全**：新增 `error` 事件落日志，载荷只有安全枚举码与固定文案（与 error 帧
    同一张表），**不回显异常正文**；分叉走与其它会话端点完全相同的归属校验
    （别人的 / 已删除的 / 不存在的 一律 404，三者不可分辨）。会话日志仍是
    security.md 有条件例外的适用范围，保留期（04d）对分叉出的新会话同样适用。
  · **同步**：不进 sync 协议（正文跨设备同步是 04e 的决策门）。

兼容与回退方式：
  兼容：`done` 新增 `seq` 是纯增量字段（旧客户端忽略即可）；`conversation.events`
  是新桥命令（旧 APK 没有它 → 客户端能力探测跳过续读，中断提示照旧）；
  `append_turn` / `_insert_events` 的新参数一律关键字可选，老调用点行为不变；
  事件表与迁移**零改动**（不需要迁移）。
  契约变更：**失败轮从「一条都不写」变成「写已完成的部分 + 一条 error 事件」**——
  这是本卡的目的，不是回归；三条 P1 契约用例随之改写（未放宽断言，改的是期望的
  行为）。
  回退 = 把 sink 从 AgentLoop 里摘掉（`sink=None` 即 02d 行为）+ 恢复
  `_execute_turn` 里的整批 `append_turn`；HTTP 面删 fork 端点与 `done.seq`；
  客户端撤 conversation.events 与 catchUp。

开放风险与责任人：
  · **审批两类事件仍无生产者**（04a 记在 04b 名下）。04b **判定不接**，理由三条：
    ① 卡面「不改审批语义与写路径的安全门」——而「从日志重建一张待确认卡片」是要
    与 03e 的收敛口径相冲突的另一套语义（03e 定案：失败 / 断连时挂起提案按
    `unavailable` 收敛、**零写**，卡不留）；② 它需要**成对**接上 `approval_result`
    （审批四值的留档），那要动写路径的门；③ 当下没有消费者——卡的实时信号是
    `confirm` 帧、权威清单是 done.proposals，两者都在流内给完。**已入 §五 挂账表
    第 11 项**（改为「谁要做『从日志重建待确认卡片』谁接」，P4 收口时若无需求则关闭）。
  · **正在生成的那一段正文仍不在日志里**（残余窗口）。它还没产出，无从落库；客户端
    照架构 §8.5 保留半截并标错。§五 第 3 项按此口径判为**已关闭**：窗口从「整轮
    跑完期间」回到「当前这一批」，且失败轮已完成的步骤不再丢。
  · **断连的「续跑」是语义续跑，不是服务端把这一轮接着跑完**：工作协程仍随断连
    取消（不保留前台服务、不续跑在途请求，R-PROD-19 的既定立场）。若将来要真续跑，
    需要的是服务端可观测的取消/重连信号，那要动已冻结的桥命令表。
  · **客户端续读的游标是内存态**：换设备 / 重开 App 后没有游标 → 退回全量拉取，
    由 `recoveredMessages` 按轮裁剪保证不重影（只是多取一些行）。**责任人：04e**
    （跨设备正文同步的决策门会决定要不要把它落盘）。
  · **真机断连走查仍待做**（P3 收口，与 R-PROD-19 的残余项合并）。

下一项未阻塞任务：**04c**（摘要压缩与预算，批次 3）与 **04e**（跨设备会话同步，
决策门）可并行开工。04c 的直接输入：本卡定型的写路径（每次写入都是一个新的
`replace` 事件的挂载点）与 `history()` 的窗口裁剪点。
```

### T-M7-04d 保留期与隐私边界落地（2026-09-19，`06dedfc`）

```text
任务编号：T-M7-04d（P3 批次 2，与 04b 并行）

完成范围 / 明确未做：
  完成——保留期机制（窗口 30 天、按整轮物理删除到期事件、只删事件行）、读取面
  访问审计的结论、03a 四个端点的归属校验复审、参数与开关、部署侧接线。
  **security.md 三条约束至此逐条有实现或有结论**。
  **明确未做**（守职责边界）：不改事件结构（04a 一行未动）、不做压缩（04c）、
  不改审批语义与写路径安全门（02e 一行未动）、不做跨设备同步（04e 决策门）、
  不改 API 响应契约（客户端零改动）。
  **刻意不碰 conversation.py 的实现面**——保留期的代码全部落在新模块 ai/retention.py，
  因此与并行的 04b **没有代码冲突**；该文件里只留两处**文档级**修正（EVENT_SECURITY
  的注释更新与一句保留期指针）。**勘误：** 这两行并未随 04b 的提交落库（他们提交时的
  暂存内容早于这两行），由本卡 2026-09-19 的收口提交单独落库——交付记录里先前「随
  04b 提交落库」的说法不成立，以本段为准。

修改的文档、契约与代码：
  文档（先行）：`security.md` 新增「会话事件日志的保留期」小节——权威口径（窗口 /
    删除粒度 / 只删什么 / 为什么不动会话行与标题 / 开关 / 运行形态）+ 读取面访问
    审计结论 + 两条已知残余；架构 §6.2「项 / 设计」表补保留期一行、事件类型表
    `security` 行改为「不接生产者」、§12.2 已定表新增第 9 项（30 天）、未决 #5
    标记闭环；风险册 R-PROD-20 结项并新增 R-PROD-25 / R-PROD-26。
    （后两项随并行卡的提交落库：架构文档与风险册均为在途共享文件。）
  代码：新 `server/src/modules/ai/retention.py`（`purge_expired_events` 删除语句 +
    `run_retention_sweep` + `retention_loop`）；`server/src/common/config.py` 两项
    （`ai_conversation_retention_days=30` / `_enabled=False`）；`server/src/main.py`
    lifespan 起停任务（**不挂 `enable_telemetry`**）。
  部署：`docker-compose.prod.yml`（`x-api-env`，两实例）与
    `docker-compose.staging.yml` 置 `AI_CONVERSATION_RETENTION_ENABLED=true`；
    `infra/cleanup/__init__.py` 补指针（说清保留期为何不在此目录）。
  测试：新 `server/tests/test_ai_conversation_retention.py`（**22 例**，含「与 04b
    分叉的交汇面」2 例——见文末收口补记）；
    `server/tests/test_config.py` +4（默认关闭 / 默认 30 天 / 0 与负数被拒）。

  三条关键决策（都留了理由）：
  1. **数值取 30 天**，不是 D-PROD-08 的 7 天——7 天是**账号删除的宽限期**，不是
     内容保留期；对齐的是保证形态（有界、到点物理删除）。30 天覆盖会话被重新拾起
     的现实窗口，同时把服务端持有的正文量钉死在「活跃用户 × 最近 30 天」。初始值，
     与 N=8 / 50 轮 / 500k token 同口径，真实分布出来后校准。
  2. **按整轮删**（`max(time) < cutoff` 的轮次整体删除）。按事件时间逐条切会把
     「带 `tool_calls` 的 assistant 消息」与其 `tool` 结果切开 → 孤立结果下一轮 400。
     这个判定**不依赖** 04a 的「一轮共用一个 `time`」：04b 改成逐事件落库后同一轮
     时间戳各不相同，取 `max` 仍然只删整轮（跨线轮整轮保留，偏保守）。
  3. **实现自成一模块**而非挂在 `ConversationService` 上：它是批处理生命周期关切
     （全库扫一遍、按到期时间删行、随进程启停），与请求路径语义不同类；删除语句与
     它的调度 / 参数 / 口径同址，读一处就够。副产物是与并行卡零文件冲突。

验证命令与结果：
  **干净 HEAD + 仅本卡文件**（临时 worktree `git worktree add --detach HEAD`，铺入本卡
  9 个文件后跑）：
    python -m pytest tests/ -q → **1137 passed / 37 skipped / 0 failed**
    python -m ruff check src/ tests/ → All checks passed
  ——这是本卡提交自身可复现的 CI 结果，**不依赖并行卡的在途改动**（工作区此刻含着
  多个并行会话的半成品，全量跑会红 6 条，均与本卡无关；用 worktree 隔离出「仅本卡
  改动」的干净复现，是这个结论的证据，而不是「本地绿了就算」）。
  **真实 PG 取证**（dev 库 aozora，PG 17.11，全程一个事务、结束 rollback、零副作用）：
    到期轮删除 3 行；**跨线轮整轮保留**（工具调用 seq4 与其结果 seq5 同进同出、
    幸存流不以孤立 tool 结果开头）；ai_usage 2 行都在且 `conversation_id` 未置空、
    tokens 和 2000 不变；会话行未删（title 仍在）；二次清扫 0 行（幂等）；回滚后
    残留 0/0/0。列形态与 alembic 版本（0020）同轮实测。
  四板：pytest **1137**、vitest **1237 passed（82 files）**、tsc **0 错**、
    JVM `testDebugUnitTest` **418 tests / 0 failures / 1 skipped**；
    另跑 CI 等价门禁 `npm run lint`（0 error）。
  四个端点归属校验复审：既有用例全绿（`TestListConversations` /
    `TestRenameConversation` / `TestDeleteConversation` / `TestConversationEvents`），
    本卡另加一条「清扫不改归属语义」的用例（别人的 id 仍 404、也不进我的列表）。

数据/同步/AI/安全影响：
  · **安全**：`security.md` 有条件例外的第三条约束（保留期）落地，三条齐了；读取面
    审计给出结论（**不加记录**，理由入档）。事件正文无限留存的风险关闭。
  · **数据**：`ai_conversation_events` 的到期行被**物理删除、不可回退**；会话索引行
    （标题 / 活跃时间）与 `ai_usage` 一行不动——会话级配额因此不能被「等满一个保留期」
    绕过（02f 口径）。到期会话仍留在列表里（正文与模型上下文已无内容）。
  · **AI 成本**：无影响（窗口 N=8 远短于 30 天）；但**会话在空闲 30 天后重新拾起时
    模型侧已无上下文**（正常问答不受影响，指代类追问会失去指代对象）——这是保留期
    的必然代价，已写入 security.md 的口径。
  · **同步**：无影响（客户端零改动，事件日志不进同步面）。

兼容与回退方式：
  纯增量：新模块 + 两个配置项 + 一个进程内任务 + 部署开关；**默认关闭**，未开启的
  部署零变化。回退 = 把 `AI_CONVERSATION_RETENTION_ENABLED` 置回 false（已删的行
  回不来，机制即刻停止）。**一个必须知道的口径**：删除不可逆，把
  `AI_CONVERSATION_RETENTION_DAYS` 改小会立刻删掉更多历史——改之前先读 security.md
  那一节。

开放风险与责任人：
  · **窗口 30 天是初始值**（同 N=8 / 50 轮 / 500k token 的口径），真实使用分布出来
    后校准。责任人：M7 收口。
  · **R-PROD-25（新登记）**：`D-PROD-08` 自己的清理脚本（`account-deletion.py` /
    `attachment-expiry.py`）在生产**无调度接线**——本卡改用进程内任务正是为绕开这个
    缺口，但「7 天内完成物理删除」对**账号**仍不会自动发生。责任人：部署执行者。
  · **R-PROD-26（新登记）**：隐私政策 v1 §3.3「账号存续期间：数据持续保留」未 carve
    out 会话正文的保留期；改文案＝发新版本 + 重新征求同意，须账户所有者决策。
  · 清扫是**单条 DELETE、无分批**（当前规模足够）；表极大时需评估分批与 `time` 列
    索引（现在没有）。未建项，先记录在此。

下一项未阻塞任务：**04c**（摘要压缩与预算）与 **04e**（跨设备会话同步决策门）——
**04b 已于 2026-09-19 交付（`c44b723`），04c 的前置因此解除**。

**收口补记（2026-09-19，04b 落地后核对）**

```text
04b 落地后回到本卡做了两件事：

1. **交汇面核对**——这是本卡与 04b 唯一的真实交互点。04b 的 fork 按**原值**复制前缀
   （含 time），于是保留期对分叉**无需任何特判**：已过期的那段前缀在新会话里同样过期，
   随下一次清扫一起消失。**这个方向是对的、且必须保持**——复制时若把时间盖成「现在」，
   分叉就成了「把 30 天前的正文洗成新的」的绕过手段，保留期承诺当场失效。
   **代价写成口径**（security.md 的「与分叉的关系」一行）：分叉的实际可用历史＝保留期
   窗口内那部分；对已过期的会话分叉得到的是「有标题、无正文」的会话（模型侧无上下文，
   客户端本地投影仍可见）。**用例钉死**：test_ai_conversation_retention.py 的
   TestForkInteraction（2 例：整段分叉也只留窗口内那一轮；窗口内的前缀一分不少）。
   产品面的选择（维持现状 + 文档化 vs 拒绝并回 422）登记为 R-PROD-29，责任人 M7 收口。
2. **共享文件的两处文档级修正补落**——conversation.py 的 EVENT_SECURITY 注释更新与
   保留期指针、架构文档的五处（§6.2 保留期行与 security 行、§12.2 已定表第 9 项与
   未决 #5、§12.1 R-PROD-20）。它们没随 04b 的提交落库（见上文勘误），在本卡的收口
   提交里补上。

验证（此时工作区已自洽：其它会话的在途改动全部提交完毕，只剩本卡这三个文件）：
  cd server && python -m pytest tests/ -q  → **1175 passed / 37 skipped / 0 failed**
  python -m ruff check src/ tests/         → All checks passed
  本卡测试文件 22 例、config 4 例全绿（41 passed 含 config 全量）
  其余三板的基线由 04b 的收口覆盖（服务端改动不影响 app/ 与 JVM 面）
```
```

### T-M7-04e 跨设备会话同步（决策门）（2026-09-19，`5da7364`）

```text
任务编号：T-M7-04e（P3 批次 3，决策门）

完成范围 / 明确未做：
  完成——① **决策**（三案对比 + 建议，**经账户所有者确认**）：新建
  `docs/adr/ADR-008-conversation-cross-device.md`；登记 `D-PROD-14`（APP-CONTEXT §12）；
  架构 §6.1 跨设备行、§6.2 新增「投影 B 的跨设备重建」行、§12.2 已定表第 11 项 +
  未决 #6 划闭。三案取「**正文按需重建**」，否决「仅摘要同步」（`AIConversation.summary`
  至今**无生产者**且只覆盖超窗轮次，本就不回答「打开旧会话看见什么」）与「做成同步实体」
  （投影是派生数据，提升为同步实体等于给它造第二个写入者）。
  ② **落地（纯客户端 + 文档）**：投影 B 的重建模块 `history-projection.ts`（纯函数）、
  切会话路径的三条刷新规则、store v3 的日志游标、空态四态文案。
  **明确未做**（守职责边界）：不改协议 / 不加端点 / **服务端一行未动** / **Kotlin 一行未动**；
  不做摘要同步（归 04c）；不改保留期（04d）、不改写路径安全门（02e）、不引入设备维度
  （P2 的登出粒度结论不变）；**不改变「服务端为会话权威」的模型**（架构 §6.3）。
  决策门签核：**账户所有者确认**（2026-09-19，三选一取方案 C）。

修改的文档、契约与代码：
  文档（先行）：新建 `docs/adr/ADR-008-conversation-cross-device.md`（三案对比 / 四条决策 /
    后果与五条残余）；`docs/APP-CONTEXT.md` §12 新增 `D-PROD-14`；架构 §6.1 / §6.2 / §12.2；
    `docs/architecture/ai-architecture.md` 新增「跨设备正文：按需重建（T-M7-04e）」一节 +
    改写 `conversation.events` 那条（从「有意不加」改为「04b 补上、04e 复用」）；
    `docs/risk-register.md`（R-PROD-19 残余③闭环、R-PROD-20 的 03d 残余②闭环、
    新增 R-PROD-30）；`production-execution-plan.md` 该卡行。
  客户端：新增 `features/assistant/history-projection.ts`（`projectHistoryFromEvents` /
    `maxSeqOf` / `INTERRUPTED_TURN_NOTE`）；`use-conversations.ts`（三条刷新规则 +
    加载态 + `canSyncContent` 单独能力探测 + 迟到批次丢弃）；
    `platform/store/assistant-chat-store.ts`（v3：`cursors` / `setCursor`（**只前进**）/
    `forgetCursor` + v1/v2 迁移 + 登出 / 删除 / 404 三处清理）；`use-assistant-stream.ts`
    （投影新增交接字段 `logCursor`，行为一行未变）；`use-assistant.ts`（三个写入点：
    正常收尾 / 失败收尾 / 取消抹除；中断文案改用共享常量）；`AssistantPage.tsx`
    （`emptyMessagesHint` 四态）。
  契约：**零新增**——消费的是 04b 已交付的 `conversation.events`（桥计数 **83 不变**，
    桥四处计数与 `SecurityConfigTest.kt` 一行未动）。

验证命令与结果：
  cd app && npx vitest run    → **83 files / 1278 passed / 0 failed**（本卡 +1 文件 / +29 用例：
    `assistant-history-projection.test.ts` 11 例、会话集成 9 例、store 游标 6 例、
    流式游标落盘 3 例）
  cd app && npx tsc --noEmit  → **0 错**；`tsc -p tsconfig.node.json` 亦 0 错
  cd app && npx oxlint <本卡文件> → exit 0，**新文件零告警**（`use-assistant.ts` 的既有
    `setMessages` 告警集合不变；本卡新增的 `setCursor` / `forgetCursor` 已补进 deps）
  回退实验 **7/7 变红**：规则①失效 / 规则③失效 / 重建丢中断标注 / 游标回退守卫失效 /
    迟到批次不丢弃 / 失败收尾不写游标 / 取消不抹游标。
  ⚠️ 「迟到批次」最初是**假绿**——用例打在了被前一道序号守卫挡住的路径上（两次都在拉取的
    切换会先命中 `seq !== refreshSeqRef`）。改为「拉取途中切回**本机会话**」（那条路径不发
    请求、不推进序号，才会走到这道守卫）后复验变红。**与 03e 同源的教训：变异要打在真实
    可达路径上。**
  未跑的三板：服务端（一行未改）、JVM（一行未改）、真机走查（属 P3 收口，见下）。

数据/同步/AI/安全影响：
  · 同步：**不进 sync 协议**（这正是本卡的决策）——正文按需重建，客户端**从不把投影写回**，
    冲突策略这一议题因此不存在。新增的 `cursors` 是**本地事实**（本机看到哪儿了），随投影
    落 localStorage、登出一起清，不参与上行；服务端因此看不到任何新增数据面。
  · 数据：无新表、无迁移（服务端零改动）；客户端落盘 schema 2 → 3，新增一个**纯增量字段**
    （旧代码读它无感）。v1 / v2 存量数据一次性迁移，**投影一条不丢**。
  · AI 成本：**不新增任何模型调用**（重建只读日志，不生成摘要）。
  · 安全：读取面沿用 03a 的归属校验（别人的 / 已删除的 / 不存在的 id 同一个 404），本卡
    **未新增端点**，因此没有新的读取面；`security.md` 三条约束口径不变（04d 一行未动）。
    「把正文带到新设备」在 `D-PROD-13` 的「记录完整对话」例外范围内（同一用户、同一份日志
    的另一种呈现），且**不新增副本存储**——投影缓存本来就在本机，本卡只是让它可重建。

兼容与回退方式：
  兼容：落盘 schema 纯增量（多一个 `cursors`）——旧版本读新数据无感（多余字段被忽略），
    新版本读旧数据走 `migrate`（v1/v2 → v3 补空 `cursors`）。桥命令是 04b 已有的；旧 APK
    上没有它 → `canSyncContent` 探测为假 → **整块退化为现状空态**（文案说明「当前版本不支持
    跨设备读取」），不炸页面。
  回退：撤掉 `use-conversations.ts` 的拉取分支即回到 03d 的行为（空消息区 + 提示）；
    `cursors` 字段留着无害。**服务端无需回退**（一行未改）。

开放风险与责任人：
  · **R-PROD-30（新登记）**：重建结果**不含历史只读记账卡**——提案的日志留档
    （`approval_request` / `approval_result`）**至今无生产者**（04a 的未指派项，见 §五 第 11 项）。
    规则「本地有投影就不重建」使它**不构成回归**（本机看过的卡片不会因刷新消失），代价只在
    「在一台从未看过这条会话的设备上打开它」。正确入口是**写路径侧**接生产者。责任人：
    未指派（P4 收口裁决）。
  · **升级前的存量桶不做增量**（游标未知 → 原样保留）：自愈——用户在该会话上再跑一轮，
    `done.seq` 就把游标写上了。
  · **取消过的会话不做增量**（游标抹成未知）：自愈同上；若要在取消后立刻补拉，需服务端在
    取消路径留一个可观测位置（当前 `CancelledError` 不被 `except Exception` 捕获，不写任何
    日志事件，也没有 error 帧）。
  · 事件端点**没有分页**：极长会话的全量重建响应偏大。`after_seq` 已在，真需要时加分页；
    30 天保留期是天然上界。**未建项，先记录在此。**
  · 别的设备**正在跑**的一轮会被拉到半截（正文不全，下一轮补齐）：与服务端「逐事件落库」的
    既定语义一致，**接受**。

下一项未阻塞任务：**P3 收口（批次 4）**——P3 四板全绿 + 真实断连恢复走查（流式中杀进程 /
  切网 / 切后台后再进入，按 `conversation_id` 续跑）+ 出口标准逐条核对。
  **04e 的跨设备路径也应并入那次走查**：清 localStorage（或换设备）后打开旧会话 → 正文应
  被重建出来；`localStorage` 有缓存时不重建、卡片不丢。这是本卡唯一没在真机上验过的面。
```

---

## 七、参考

- `docs/milestones/production-execution-plan.md` M7 章节 — 任务边界与验收（**权威**）
- `docs/architecture/ai-agent-architecture.md` — 目标架构（§6.2 事件日志 + 派生投影、§6.3 上下文窗口、§6.4 Token 预算、§4.4 四条不变式、§12.2 未决问题）
- `docs/architecture/security.md` — 会话事件日志的有条件例外与三条约束（**本阶段的边界依据**）
- `docs/milestones/T-M7-02-P1-implementation-plan.md`、`docs/milestones/T-M7-03-P2-implementation-plan.md` — 上游逐卡细则与中间结果
- `docs/risk-register.md` R-PROD-15 / 19 / 20 — P3 相关风险
- `docs/engineering/需求变更规范.md` — L1/L2/L3 变更级别与文档先行要求
- `docs/engineering/通用任务执行规范.md` — 开发实现 / Review / Bug-fix 三类工作流