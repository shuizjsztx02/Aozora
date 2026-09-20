# T-M7（APP 产品 AI Agent 化）问题整理与遗留项清理

**建立：** 2026-09-20（T-M7-05 收口轮）
**关系：** 本文是 **M7 收口时对全周期遗留的汇总与裁决记录**。风险条目本身仍在
`docs/risk-register.md`（本文不复制其正文，只给状态与去向）；逐卡交付以
`docs/milestones/T-M7-0{2,3,4,5}-*-implementation-plan.md` §六 中间结果记录为准。

---

## 一、本轮发现并已修复的两个「规划里不可能生效」的缺陷

这两条都不是"某个功能没做完"，而是**按现有配置，规划中的步骤在物理上无法发生**。
它们的共同特征：单看代码都自洽，只有把「部署形态」与「客户端形态」一起看才暴露。

### 1.1 bootstrap checksum 车道被 05j 撑破 → 已同步的客户端会进同步死循环

**已修：** `ab243a2`（服务端），客户端侧随 05j 数据面一并落地。

- 05j 把偏好项并进 `checksum_v2` 的输入（八类 → 九类）。
- `checksum_v2` 是**已构建客户端**的比对基准：`SyncPayloadMapper.parseBootstrapResponse`
  只解析八类、`BootstrapChecksum.computeV2` 只算八类。
- `_compute_checksum` 对**空列表不产出分段**，所以：
  - 账号没有偏好行 → 两边算出同一个值，**问题完全不可见**；
  - 账号一旦有一行偏好（AI 的 `meta_set` 就会写）→ 服务端 hash 变、客户端算不出
    → `SyncOutcome.RETRYABLE` → **bootstrap 死循环，整条同步链路停摆**。
- `schemas.py` 自己的 docstring 早就写着这条规则（「predates T-M7-01 的客户端不得
  被切到会失配的车道」），05j 违反了它；而 05j 的用例只钉住了"偏好为空时兼容"这一半，
  恰好是问题不可见的那一半。

**修法：** 新增 `checksum_v3`（九类），`checksum_v2` 恢复成八类（与 05j 之前逐字节相同），
客户端按 v3 > v2 > legacy 择高。零破坏。

### 1.2 生产 compose 没有接线 AI Agent 的环境变量

**已修：** `0fc2d38`。

- `docker-compose.prod.yml` 与 `docker-compose.staging.yml` **一个 `AI_AGENT_*` 都没写**。
- 后果：容器里恒为 `config.py` 的默认值 → `AI_AGENT_ENABLED` 永远 false →
  `POST /ai/agent` 恒 404。**「部署到 A 机后真机测 AI Agent」在配置层面不可能发生**，
  而 agent 正是 M7 的交付主体。
- 同批打开 `AI_DEVICE_CHANNEL_ENABLED`（ADR-010 决策 7 把开启时点归 P4 收口，
  05f 的客户端执行器已交付，故此刻打开）。

---

## 二、收口动作（本轮执行）

| # | 动作 | 提交 | 说明 |
|---|---|---|---|
| 1 | bootstrap 车道新增 `checksum_v3` | `ab243a2` | 见 §1.1 |
| 2 | `ai_daily_quota_guest` 默认值恢复 20 | `320d9c9` | 2026-09-11 联测临时提额，登记为「M7 收口统一恢复」。两个部署实例由 compose 显式接管（test 100000 / prod 20），测试面由 conftest 钉死，故不影响联测 |
| 3 | 生产 compose 接线 agent 环境变量 | `0fc2d38` | 见 §1.2 |
| 4 | A/B 机更新方案 | `d352475` | `infra/deploy/prod/UPDATE-T-M7-05.md` |

---

## 三、未闭环遗留：按「能否在 P4 收口关闭」分类

### 3.1 本轮**不能**关闭、且已明确去向的

| 编号 | 一句话 | 为什么本轮关不掉 | 去向 |
|---|---|---|---|
| **R-PROD-31** | v2 提案的客户端渲染路径无人认领 | 28 个写 op 的服务端执行体已就位、客户端既**不声明车道**（`ai.agent` 请求体无 `proposal_contract_version`）也**不按 op 渲染/分派**。这是 P4 出口标准「功能覆盖清单逐项对话可完成」的**唯一阻塞面**——服务端这一半做完了，另一半全压在这条上 | **本轮认领**（见 §四） |
| **R-PROD-37** | 设备执行 op 在客户端静默无效 | 与 R-PROD-31 是**同一件事的两半**：卡面按 `proposal.transactions` 渲染，而设备 op 没有 transactions → 确认后显示「已记录 0 笔交易」。05j 把 `meta_set` 移回服务端执行后，`persona_switch` 也随之收窄；**但 `consent_record` / `sync_trigger` 不会上云**（同意与同步天生是设备行为），只能靠客户端补齐 | **本轮认领**（与 §四 同批） |
| **R-PROD-38** | `reminder_set` 没有工具体 | v2 注册表成员、桥命令三条俱在，但没有任何一处表态 | **本轮认领**（三处表态 + 守护用例） |
| **R-PROD-34** | 设备通道挂起态是进程内单例 | 单 worker 下成立；多 worker / 进程重启会丢挂起请求 | **部署执行者**（测试期可接受，见更新方案 §五） |
| **R-PROD-25** | 物理删除清理脚本无调度接线 | 需要部署侧排期，不是代码问题 | **部署执行者 / M7 收口后** |
| **R-PROD-26** | 隐私政策未 carve out 会话日志保留期 | 法务/产品侧，非工程 | **账户所有者** |
| **R-PROD-24** | A 机 nginx 的若干风险点（`location /` 的 deny all、DHCP、access_log、尾斜杠） | 属服务器运维面 | **部署执行者** |

### 3.2 依赖**真实使用数据**才能收敛的（测试期采集）

| 编号 | 一句话 | 采集方式 |
|---|---|---|
| **R-PROD-15** | 多轮 token 成本成倍上升 | 会话级双限（50 轮 / 500k token）与 `ai_agent_step_timeout_seconds=20` 都是**初始值**，按真实分布校准 |
| **R-PROD-17 残余** | 域路由没有提高准确率（05g 已实测），残余是词表覆盖度与「读后不落子」 | 见 R-PROD-39 与「读后不落子」 |
| **R-PROD-39** | 域路由词表覆盖度**没有采集方** | `phase: routing` 帧带 `matched` 字段，但下游无人统计。要在生产收集需先决定采集点 |
| **R-PROD-36** | 设备工具的选择准确率只有 1 个数据点 | 真机走查时扩样 |
| **R-PROD-21** | 混合推理的三档阈值待实测调优 | 同上 |
| **R-PROD-19** | SSE 弱网/后台中断 | P2 的中断提示已落地；续跑与真机走查待做 |
| **R-PROD-22** | SSE 经真实 nginx 的端到端确认 | 更新方案 §二 步骤 5.4 覆盖 health；**agent 流式**仍需真机确认 |
| **R-PROD-32 / 33** | 本地投影与游标未落盘（一次未复现）／重建出的被中断轮没有中断标注 | 真机走查时复认 |

### 3.3 依赖**双设备 / 跨设备**场景的

| 编号 | 一句话 | 状态 |
|---|---|---|
| **R-PROD-18** | 三实体上云引入同步冲突或数据丢失 | 已缓解；双设备真机换机 bootstrap 仍未实机复认 |
| **R-PROD-30** | 跨设备重建出的历史不含历史只读记账卡 | **未指派**——需要一次产品裁决（接受与否） |
| **R-PROD-29** | 对已过保留期的会话分叉会静默得到「无正文的会话」 | 真机走查时一并评估 |

### 3.4 设计取向决定、**不再修**的

| 编号 | 一句话 | 结论 |
|---|---|---|
| **R-PROD-20** | 服务端持有会话日志的隐私边界 | 已由 ADR-007 决策 3 / D-PROD-13 接受；机制全部落地 |
| **R-PROD-23** | DeepSeek 接入的行为差异 | 9 项全通过，已闭环 |
| **R-PROD-28** | 本地 NLU 吞掉复合句的账户/日期字段 | 本地快路径的既有边界（agent 路径不受影响）；**已知限制**，非缺陷 |
| **R-PROD-27** | 「多步强制确认」可被模型 Step 1 并行写绕过 | 已知边界；收紧需改 loop 的并行策略，属后续评估 |
| **R-PROD-40** | 产品帮助语料会随实现漂移 | 语料按当前实现逐条写成、经两遍核对；漂移是**接受的代价**，下一次改入口时同步改语料 |
| **R-PROD-41** | 新域上线忘写 Skill 是静默的 | 已做导入期校验（域写错即拦）；「新域没有域知识」是**正常状态**，不能照抄域路由的守护 |

---

## 四、本轮对 R-PROD-31 / 37 / 38 的裁决

三项都是 P4 出口标准「功能覆盖清单逐项对话可完成」的阻塞面，且**收敛路径是同一次
客户端工作**——分开做会各留下另一半（R-PROD-37 的原文即警告「别只补一处」）。

### 4.1 裁决：**本轮不开 v2 车道**，R-PROD-31 / 37 转为一张有完整设计的尾卡

**理由（这是一次取舍，不是遗漏）：**

- 客户端**声明的契约版本就是灰度旋钮本身**（服务端
  `AIAgentRequest.proposal_contract_version: Literal[1,2] = 1`，缺省即 v1 车道、
  P1 的 15 个工具、行为逐字不变）。不声明 = 今天不构成任何故障。
- 声明 v2 = 服务端开始产出 **v2 信封**（op 在 `action` 上、参数在 `payload` 里）。
  在客户端渲染面就位**之前**声明，会把**连记账都渲染不出来**——比现状（只有设备
  op 静默无效）更糟。**两者必须同时上线，不能只做一半。**
- 而渲染面是一次**完整卡片级**的改动：确认门 `PendingApproval.proposal` 目前是
  `ActionProposal`（v1 形状，`transactions` 是根字段），v2 提案没有 `transactions`
  → 类型面会外溢到 `commitProposalInternal` / `editProposal` / `writeTransactions` /
  `withResolvedAccounts` / `AssistantPage` 的卡片渲染。改到一半的确认门，正好会
  砸在紧接着的真机联调上。

**本轮**验证过、可直接开工的设计（省掉下一张卡的全部调研）：

| 面 | 现状（已核实） | 还差什么 |
|---|---|---|
| 契约层（客户端） | **已就位**：`ActionProposalV2` 类型、`validateProposalV2`、双读入口 `validateProposalAny`、`V2_OPS`（从 `schema-v2.json` 派生，不另抄一份） | 无 |
| 车道声明 | 未做 | Kotlin `AiGatewayClient.buildAgentRequest` 加一个字段（**TS 侧不用改**——HTTP 请求体由 Kotlin 构造，故不触桥命令计数面） |
| 卡面文案 | 未做，但**成本极低**：`tools.py` 里 **37 处** `"message":` ——**每个 v2 工具都产出一句人类可读的中文**（如「把 2026-09 的整体预算设为 ¥3000」） | 一张通用卡：`message` 作主行 + `warnings` + `payload` 键值对照行 + 确认/拒绝 |
| 服务端执行分派 | **已就位**：`ai.execute` 桥命令在（`BatchConfirmationCard` 仍在用 `executeProposal`），`proposal_ops.EXECUTORS` 28 个执行体 + `transaction_add` | 按 `contractVersion` 分流到 `executeProposal` |
| 设备执行分派 | 未做。`DEVICE_EXECUTED_OPS` 现为 **3 个**（`persona_switch` / `consent_record` / `sync_trigger`）——05j 把 `meta_set` 移回服务端后清单已收窄 | **只是一张映射表，不需要新增桥命令**（已核实三条命令都在 `BridgeProtocol.kt` / `native-bridge.ts` 里）：`persona_switch` → `meta.set(active_persona_id)`；`consent_record` → `consent.record`；`sync_trigger` → `sync.trigger`。故**不触桥命令计数面** |
| `account_deletion_request` | 服务端执行体在，且是唯一 `HIGH_RISK_OPS` | 卡面必须能把「即将发生什么、为什么必须确认」讲清（**卡片是它唯一的用户可见面**） |

### 4.2 R-PROD-38（`reminder_set` 没有工具体）——**本轮同样不补**

它与上面**同一条链**：`reminder_set` 是 v2 注册表成员，补上工具体之后它就是第 4 个
设备执行 op ——而设备执行 op 在客户端没有分派路径，**今天的表现正是 R-PROD-37 的
「确认后显示已记录 0 笔交易」**。05d 当初不越界代做是对的判断，但现在另有一条理由：
**补它的正确时点是和 v2 渲染/分派一起**，否则只是给静默无效的清单再加一行。

架构 §3.2 的「提醒」行因此本轮**显式记为「P4 不做」**（范式见 05i 对理财知识的处理），
并把它并入上面那张尾卡。

### 4.3 下一张卡的边界（已定，可直接领取）

**一张卡、四个面、一次上线**：
① 桥声明 `proposal_contract_version: 2`；
② 通用 v2 确认卡（按 `validateProposalAny` 分流，`message` 主行 + payload 对照）；
③ 执行分派（服务端 op → `ai.execute`；设备 op → 三条**已存在**的桥命令，纯映射表）；
④ `reminder_set` 补三处表态 + 守护用例。

**验收**：把 §三 的三项（R-PROD-31 / 37 / 38）一起关掉；真机上「让 AI 改整体预算」
与「让 AI 设提醒」都能出正确的卡并真的生效。

> **设备自管量永不进工具**：`sync_cursor` / `master_book_id` / `personas_seeded` /
> `budget_carryover_mark` / 迁移标记 / `active_persona_id` / `avatar_uri` /
> `display_name` / `budget_carryover` 一个都不上行，也不可由 AI 改。

---

## 五、文档一致性缺口（本轮顺带发现）

`docs/milestones/production-execution-plan.md` 的 T-M7-05 表格存在状态列与实际交付
不符的问题，且**表格里根本没有 05j 这一行**：

| 行 | 问题 |
|---|---|
| 05a / 05b / 05c | 状态列写「⏳ 未开始」，实际均已交付（`ab7b1c3` / `733379e` / `20d6111`） |
| 05f / 05g | 状态列**单元格内部自相矛盾**——起首写「⏳ 未开始」，同一格里又写「已交付 + 提交号」 |
| 05h | 状态列写「⏳ 未开始」，实际已交付（`f42c927`） |
| （缺行） | **没有 05j 的行**——它是 P4 追加项，只在 P4 计划的看板里 |

另：P4 计划 §五 挂账表有**两个第 10 项**（一个讲偏好项上云、一个讲 `meta_set`
客户端执行静默无效），编号重复。两者收敛路径相同（均归 05j），但来源不同。

---

## 六、代码卫生扫描结论

- `server/src/modules/ai/**`、`server/src/modules/sync/**`、
  `app/src/features/assistant/**`、`app/src/platform/bridge/**`、
  `app/src/main/java/com/aozora/app/{ai,bridge,sync}/**` 里
  **没有任何 `TODO` / `FIXME` / `XXX` 字面量**——挂账语义全部用中文长注释表达，
  且绝大多数是**已解释的刻意决策**（「刻意不」「刻意的」）。
- 两条**过时注释**（不影响行为，本轮不动，登记备查）：
  - `server/src/modules/ai/devices.py:70` 仍写域路由是「**将来**接线」，而 05g 已落地；
  - `app/src/features/assistant/use-assistant.ts:48` 仍写取消协议「未接线——P1 无 UI 触发点」，
    而 T-M7-03e 之后已有停止生成入口。