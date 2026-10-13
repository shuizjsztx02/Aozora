# Aozora · AI 架构

本地规则 NLU 提供离线兜底，服务端 AI Gateway 提供在线理解。模型密钥只存在服务端密钥管理环境；客户端不得直接调用模型提供商。

AI 只返回通过 JSON Schema 校验的 ActionProposal。金额、日期、权限和写库始终由确定性业务代码负责，低置信结果必须先经用户确认。

## ActionProposal 契约

- **JSON Schema：** `contracts/action-proposal/schema.json`（v1，Draft-07，版本化 URL `$id`）与 `contracts/action-proposal/schema-v2.json`（v2，T-M7-05a 新增；v1 文件一字未改，见下文「ActionProposal v2 契约」）
- **TypeScript 类型 + 校验器：** `app/src/domain/action-proposal.ts`（`validateProposal` = v1，`validateProposalV2` = v2，`validateProposalAny` = 双读入口）
- **Python 校验器：** `server/src/modules/ai/validator.py`（`validate_proposal` / `validate_proposal_v2` / `validate_proposal_any`）
- **v1 ↔ v2 动作名映射：** `contracts/action-proposal/v1-action-aliases.json`
- **Fixtures：** `contracts/action-proposal/fixtures/`（v1 正反例）+ `fixtures/v2/`（v2 正反例）
- **变更规则：** 协议修改必须先更新本文件和 `contracts/action-proposal/schema.json`，并新增 ADR 或协议版本号

## interpret → validate → execute 流水线（R-M4-03 契约澄清）

`POST /ai/interpret` 返回**最小解析结果**（type/amountCents/category/note/bizDate/confidence 等中间表示），它**不是 ActionProposal**，服务端对其只做「是 JSON 对象」解析、不做提案 Schema 校验。ActionProposal 由**客户端组装**，随后必须通过 `POST /ai/validate`（JSON Schema Draft-07 + 账本上下文 categoryIds/accountIds）校验，方可提交 `POST /ai/execute`；执行前服务端做 TOCTOU 重校验。首段「AI 只返回通过 JSON Schema 校验的 ActionProposal」约束的是**写库路径**：任何 AI 来源的数据在写库前必经 Schema 校验，interpret 的输出本身永不直接入库。

## 自动记账的文本解析入口（第二十一轮 REQ-83，口径见 `ADR-016`）

自动记账的采集层（无障碍服务 + 通知监听）在客户端，**只做「预筛 + 落本地 `auto_captures` 表」，不做网络、不做解析**。解析在两步上完成：**本地规则**（`LocalCaptureParser`，纯函数 + 模板，离线可用）先行，未命中的**疑似交易文本**再上行服务端。

**端点选择判据先行（T21-2 取数）：** 先用既有 `POST /ai/interpret` 直接喂**这两条通道的真实语料**（微信支付页 / 微信通知 / 支付宝页 / 支付宝通知 / 云闪付 / 银行 App / 银行短信）取一轮命中率与字段完整率实测——**够用就复用、不新增端点**（最小改动优先）；不够才新增 `POST /ai/parse-notification`（复用 v1 proposal 骨架与 `prompts._render_categories_section` 的分类注入面，**提示词独立**，因为页面 / 通知文案与用户口语差异大）。**判据与实测数字必须写进 T21-2 的交付记录。**

**两条通道共用同一套解析**：两条通道产出的**都是文本**（无障碍按行拼装页面可见文本，通知取标题 + 正文），输入形态统一，因此不为无障碍单独造解析链。

**必须显式处理四类「不是消费」的语义**（否则会记成消费污染收支统计）：**退款 / 转账（含信用卡还款）/ 理财基金买入 / 红包收发**。四类反例各有用例。

**成本与配额：** 无论走哪个端点，解析调用**一律计入 `ai_usage`**（成本可观测），guest 日配额 `check_daily_quota` 照常生效。

**与流水线的关系：** 解析产出的仍是**中间表示**（金额 / 商户 / 方向 / 时间 / 置信度），不是 ActionProposal——它不改 `interpret → validate → execute` 的既有形状，写库仍走既有的提案与确认门（高置信自动入账但**立即可见 + 可撤销**，低置信进待确认箱，PR-2 不放松）。**它不进 agent loop、不进工具注册表**（见 `ai-agent-architecture.md` §4.7）。

## WebView 调用通道

客户端 WebView 经 `AozoraBridge` 的 `ai.*` 命令族调用服务端 `/ai/*`；Kotlin 层负责 token 注入，Bearer token 全程不出 Kotlin 层（详见 `docs/adr/ADR-003-webview-ai-channel.md`）。超时预算遵循 APP-CONTEXT §10.3：12 s 客户端慢速提示 / 25 s 取消并回退本地 NLU。

## /ai/report 限流降级契约

`POST /ai/report` 的限流/配额命中 **不返回 429**，而是降级为 200 + 纯事实包（`narrative=null`, `narrative_cached=false`, `usage=null`）。理由：事实包是确定性数据聚合，不产生 LLM 成本；客户端只需一条降级路径（narrative 缺失 → 本地展示纯数据卡片）。`/ai/interpret` 仍保留 429 行为（纯 LLM 端点，无事实包可降级返回）。

### T-M1-05 v1 约束修复

- 未知字段必须拒绝；金额上限为 9007199254740991 分；出现 transactions 时必须非空。
- bizDate 的 `format: date` 必须以断言模式验证完整公历日期，不能只检查正则或把 format 当注释。此为既有 v1 约束修复，不新增动作或改变合法提案字段。
- `validateProposal(input)` 仅作结构校验；执行前传入 `ProposalValidationContext`（当前账本允许的 categoryIds/accountIds），拒绝未解析分类和未授权 ID。上下文来自可信 repository/API，不能取自模型输出。M2/M4 接线前不能把结构通过当作执行许可。

## ActionProposal v2 契约（T-M7-05a）

**v1 一字未改。** `contracts/action-proposal/schema.json` 保持原样，v2 是**新增文件** `contracts/action-proposal/schema-v2.json`——旧 APK 里内嵌的是 v1 schema，新增文件对它们不可见，所以「旧客户端行为不变」是文件布局本身保证的，不靠运行时判断。

### 为什么要有 v2

v1 是**固定 action 集**（`add_tx` / `add_tx_batch` / `query` / `report` / `create_category` / `config` / `chat`），字段（`transactions`）在根上。P4 要把 §3.2 的全量 CRUD 映射成工具，这套形状立刻不够用：

| v1 的问题 | v2 的解法 |
|---|---|
| 每加一个能力＝改一次契约（枚举 + 字段 + 三方校验器 + fixtures） | **op 注册表 + 通用 `payload`**：新能力＝注册表加一行，信封不变 |
| `config` 一个 action 里塞了 6 个子操作（`book.create` / `account.create` / `budget.create` / `savings.create` / `default.book_set` / `default.account_set`），子操作只在 prompt 里定义、无跨语言校验 | 6 个子操作升为 6 个**一等 op**，各自可校验、可归属 |
| 字段形状写死在契约里，与工具注册表的 `parameters` 是两份 | payload 形状**由工具注册表拥有**，契约不复制第二份 |

### 两条车道与车道判定

```jsonc
// v1（旧客户端、旧提案）：没有 contractVersion 字段
{ "proposalId": "...", "action": "add_tx", "transactions": [ ... ], ... }

// v2：必须有 contractVersion，动作名必须是注册表成员，参数全在 payload 里
{ "contractVersion": 2, "proposalId": "...", "action": "transaction_add",
  "payload": { "transactions": [ ... ] }, ... }
```

**判定＝`contractVersion` 在场与否，不需要猜。** v1 关闭 `additionalProperties` 且没有这个字段，所以：v1 提案**带上**它就是无效 v1，v2 提案**没有**它就是无效 v2。两条车道因此互斥且可判定。TS 侧 `validateProposalAny` / Python 侧 `validate_proposal_any` 是双读入口，返回 `contractVersion`（`1` / `2` / 非对象时 `null`）。

### v2 信封字段（`schema-v2.json`）

| 字段 | 必填 | 规则 |
|---|---|---|
| `contractVersion` | ✓ | 常量 `2` |
| `proposalId` | ✓ | 非空字符串，≤ 64（与 DB 列宽对齐，同 v1） |
| `action` | ✓ | **op 注册表成员**（下表）。v1 动作名不是成员——别名关系见「v1 ↔ v2 动作名」 |
| `confidence` | ✓ | `[0, 1]`，同 v1 |
| `requiresConfirmation` | ✓ | 布尔。**这是策略旋钮**：注册表成员身份不代表必须出确认卡（`sync_trigger` 可自动放行），但高风险 op（`account_delete`）必须为 `true` |
| `message` | ✓ | 面向用户的自然语言回复 |
| `warnings` | ✓ | 字符串数组 |
| `payload` | ✓ | 对象。**无参数 op 写 `{}`**——不设「可省略」特例，规则才不必按 op 分支 |

根上**不新增**任何其它字段；`payload` 之外一律 `additionalProperties: false`（未知字段拒绝，同 v1 的严格口径）。

### op 注册表（全量 CRUD）

命名＝ `域_动词`（下划线，与工具名同构；工具名带点号会让 provider 直接 400，见 02c 收口）。**出处**列是该 op 背后的真实能力面（服务端路由或桥命令），**归属卡**是 P4 里实现它的卡。

| op | 域 | 出处 | 归属卡 |
|---|---|---|---|
| `transaction_add` | 记账 | `POST /transactions`（单条与批量是同一个 op，差别只在 `payload.transactions` 条数） | 05b |
| `transaction_update` | 记账 | `PATCH /transactions/{id}` | 05b |
| `transaction_delete` | 记账 | `DELETE /transactions/{id}`（软删） | 05b |
| `category_create` | 记账 | `POST /categories` | 05b |
| `category_update` | 记账 | `PATCH /categories/{id}` | 05b |
| `category_archive` | 记账 | `DELETE /categories/{id}`（`archive_category`） | 05b |
| `account_create` | 账户账本 | `POST /accounts` | 05b |
| `account_update` | 账户账本 | `PATCH /accounts/{id}` | 05b |
| `account_archive` | 账户账本 | `DELETE /accounts/{id}`（`archive_account`） | 05b |
| `book_create` | 账户账本 | `POST /books` | 05b |
| `book_update` | 账户账本 | `PATCH /books/{id}` | 05b |
| `book_delete` | 账户账本 | `DELETE /books/{id}` | 05b |
| `meta_set` | 账户账本 / 角色 / 用户设定 | `meta.set`（按 payload 的 key 分默认账本 / 默认账户 / 当前角色 / 小组件配置 / 用户画像五个域）。**执行位置＝设备**（见下「设备能力通道不是 ActionProposal」）| 05b / 05c / 05d |
| `budget_set` | 预算存钱 | `PUT /budgets/{period}`（UPSERT） | 05c |
| `budget_set_category` | 预算存钱 | `PUT /budgets/{period}/category/{id}`（UPSERT） | 05c |
| `budget_delete` | 预算存钱 | 桥 `budget.delete` | 05c |
| `savings_create` | 预算存钱 | 桥 `savings.insert`（实体已上云，T-M7-01） | 05c |
| `savings_update` | 预算存钱 | 桥 `savings.update` | 05c |
| `savings_archive` | 预算存钱 | 桥 `savings.archive` | 05c |
| `scheduled_create` | 定时 | 桥 `scheduled.insert`（已上云） | 05c |
| `scheduled_update` | 定时 | 桥 `scheduled.update` | 05c |
| `scheduled_archive` | 定时 | 桥 `scheduled.archive` | 05c |
| `persona_create` | 角色 | 桥 `persona.insert`（已上云） | 05c |
| `persona_update` | 角色 | 桥 `persona.update` | 05c |
| `persona_archive` | 角色 | 桥 `persona.archive` | 05c |
| `persona_switch` | 角色 | 桥 `persona.*` 的当前角色切换（写入侧即 `meta.set(当前角色)`） | 05c |
| `reminder_set` | 提醒 | 桥 `reminder.set`（**执行位置＝设备**）。**工具体本身无人认领**——05f 交付时未含它，05d 的卡面也不含（见「未覆盖的最后一行」）| **未认领** |
| `export_create` | 导出 | `POST /exports`（**服务端执行已落地**，05d）| 05d |
| `feedback_submit` | 反馈 | `POST /telemetry/feedback`（**服务端执行已落地**；设备元信息留空，见 05d 收尾段）| 05d |
| `consent_record` | 隐私 | 桥 `consent.record`——**执行位置＝设备**（同意按 `device_id` 存，服务端认不出设备），05d 已登记进 `DEVICE_EXECUTED_OPS` | 05d |
| `auth_update_profile` | 安全账号 | 服务端 `PATCH /me`（只支持 nickname）——**服务端执行已落地**（客户端另有同义的桥命令）| 05d |
| `account_deletion_request` | 安全账号 | `POST /me/deletion-request` + `DELETE /me`（**执行体在服务端内部走完两步**）——服务端执行已落地 | 05d |
| `account_delete` | 安全账号 | 同上的第二步（**最高危**）。**刻意不接线**：payload 的 `deletionToken` 是凭据，不进模型上下文——AI 路径合并进上一条 | 05d（不接） |
| `sync_trigger` | 同步 | 桥 `sync.trigger`——**执行位置＝设备**（「立刻同步」是设备发起的动作），05d 已登记进 `DEVICE_EXECUTED_OPS` | 05d |

**不在注册表里的，是**读操作（`transaction_list`、`budget_status`、`/stats/*`…：读工具的结果直接回喂模型，**不是提案**）、纯对话（v1 的 `chat`）与**设备指令**（见下）。

**未覆盖的最后一行（T-M7-05d 交付后盘点，2026-09-20）：** 架构 §3.2 的覆盖度
清单里只剩**提醒**一行没有工具体——`reminder_set` 是 v2 注册表成员，但**没有人
认领它**：05f 的卡面产出与验收列的是设备能力通道的 6 个 capability（不含提醒），
05d 的卡面列的是分析 / 导出反馈隐私 / 安全账号 / 同步四块。它需要的三处表态与
`meta_set` 同形（工具 + `DEVICE_EXECUTED_OPS` + 授权规则），但**判给谁是一张
新卡的边界问题**，05d 不越界代做（见计划 §五 挂账项，P4 收口裁决）。

同一盘点里还有两行**部分覆盖**，它们要等 **05j** 的数据面决策：`meta.set` 的
「小组件配置」与「用户画像」两个键仍是纯设备本地量（`meta_set` 的 key 枚举当前
只有 `default_book` / `default_account`）。**该不该上云、以什么形态上云是 05j 的
设计决策**（用户 2026-09-20 指定的目标），05d 只按 `meta_set` 现有的 key 集合
做事——这正是 05c 立下的判据：有专属语义的键给它自己的 op，纯偏好才走 `meta_set`。

### v1 ↔ v2 动作名（`v1-action-aliases.json`）

别名表是唯一映射来源，`server/src/modules/ai/validator.py` 的 `canonical_op()` 是它的可调用形态——**每加一个 v1 名字就要在这里表态**（有规范 op 或显式 `null`）。

| v1 action | v2 规范 op | 说明 |
|---|---|---|
| `add_tx` | `transaction_add` | |
| `add_tx_batch` | `transaction_add` | 批量与单条合并成一个 op |
| `create_category` | `category_create` | |
| `config` | 无单一对应 | 6 个子操作各归各：`book.create` → `book_create`、`account.create` → `account_create`、`budget.create` → `budget_set_category`、`savings.create` → `savings_create`、`default.book_set` / `default.account_set` → `meta_set` |
| `query` / `report` | 无 | 读操作，v2 里不是提案 |
| `chat` | 无 | 纯对话 |

**两套名字不重叠**（v1 枚举 ∩ v2 枚举 = ∅），所以同一串字符不会在两条车道上含义不同；这条由用例钉住。

### 版本协商与降级

**由客户端声明驱动，服务端无全局开关。**

| 环节 | 规则 |
|---|---|
| 声明 | 新客户端在 `POST /ai/agent` 上带 `proposal_contract_version: 2`。**字段缺省＝1**（旧 APK 什么都不带） |
| 产出 | 按声明选车道：v1 → 只产出 v1 可承载的 op（记账一个 op，见下）；v2 → 产出 v2 信封与规范 op |
| **v1 车道产不出 v2-only op** | 这是**实现约束，不是建议**：loop 不得把 v2 提案塞给 v1 客户端（旧校验器会整条判无效，卡片不出、写入静默失败）。05b 落地为**注入面过滤**：v1 车道只注入 `V1_LANE_OPS` 对应的写工具（其余写工具模型**看不见**），组装面另有一道闸（手工注入了也装不进 v1 信封） |
| 回退 | 不产 v2 就是回退——服务端按声明降级即可，**无数据迁移、无契约文件替换**；旧 APK 内嵌 v1 schema，不受影响 |
| 灰度 | 声明字段天然按客户端版本灰度：新 APK 走 v2，未升级的走 v1，服务端不需要第二套开关 |

### 服务端接线（T-M7-05b 落地）

05a 的契约到此**两头接上**：`proposal_contract_version` 有了读取方，两条车道在
校验、授权与执行三处都分流。

| 环节 | 实现 | 口径 |
|---|---|---|
| 声明 | `POST /ai/agent` 的 `proposal_contract_version: Literal[1, 2] = 1` | 缺省 1＝旧 APK；值非法直接 422（**不猜**） |
| 注入面 | `tools.tools_for_domains(domains, contract_version=…)` | **v1 车道 = P1 的 15 个工具**（逐字不变）；v2 = 全量 27 个。读工具与车道无关 |
| 组装面 | `agent.assemble_proposal(draft, contract_version=…)` | 工具产出**规范草案**（`{action: <v2 op>, payload}`），由组装器按车道装信封；v1 只装得进记账 op（payload 的 `transactions` 抬到根上，动作名按条数在 `add_tx` / `add_tx_batch` 之间选） |
| 校验 | `AIService.validate_proposal` 按 `contractVersion` 在场与否分流 | v1 逐字沿用（schema → 幻觉 → 低置信）；v2 只校验信封 + 低置信，**payload 的 id 不在这一层对拍**（要查库） |
| 授权 | `authorization.authorize_payload`（loop 组装、`/ai/validate`、`/ai/execute` **三处同一个函数**） | 每个 id 都必须落在服务端自己的查询里：分类属于本人且在本账本或全局、未归档；账户属于本人且未归档；账本属于本人且未软删；交易未软删**且就在提案的账本里**。**未登记授权规则的 op 一律拒绝**（fail-closed：默认放行会让「忘了登记」变成静默的越权面） |
| 执行 | `proposal_ops.execute_op` 的 op → 执行体表 | **注册表成员身份不是执行许可**：不在表里的 op 一律 400（含最高危的 `account_delete`）；执行位置在设备的 op（`meta_set`）另有自己的拒绝码。每次写入**同事务登一条 sync change**（T14-8 口径） |

**v2 提案走的是与 v1 同一条安全门**：组装 → 校验 → 授权 → 审批四值 → 执行
（含 TOCTOU 重校验），02e/03e 的路径一行未改——v2 换的是**信封与 op 命名空间**，
不是门。

**05c 续接（2026-09-20）：** 预算存钱 / 定时 / 角色三域的 12 个写 op 按**同一范式**
接了进来（工具 → `authorization` 的授权规则 → `proposal_ops` 的执行体，**三处同时
表态**；`test_every_server_executed_op_is_also_authorized` 拦住半截接线）。授权表新增
三类目标：`savings_goal` / `chat_persona`（用户级，按 `owner_id`）与
`scheduled_rule`（**账本级**，额外要求 `book_id` 对得上——与 `transaction` 同口径）。
`persona_switch` 是**设备执行**的 op（当前角色指针是设备本地的 `sync_meta` 键），
与 `meta_set` 同类：工具与授权在服务端，执行在客户端，`/ai/execute` 报
`ai_device_op_not_executable`。**三实体的离线语义**（写入即云端权威 + 同事务登
sync change，设备上线后 pull 到）写在 `proposal_ops.py` 的文件头。

**回执字段的一处语义扩张（诚实记录）：** `AIExecuteProposalResponse.transaction_ids`
在 v2 车道上装的是「本次 op 影响的**实体** id」（改 / 归档是同一 id 的新版本，
新建是新 id），字段名沿用是为了不动既有回执契约。等 R-PROD-31（客户端渲染 v2
提案的路径）落定、真有消费方时再谈改名。

**05d 收尾（2026-09-20）：** 分析之外的四个服务端域按**同一范式**接了进来——
导出 / 反馈 / 昵称 / 注销四条写 op 进 `proposal_ops.EXECUTORS`，六条授权规则进
`authorization`（`export_create` 按 `bookId` 授权，其余五条**空元组＝明确表态**：
它们的授权面不在 payload 里，见下）。工具集到 **54 个 / 14 域**，架构 §3.2 的
覆盖度清单至此只剩**提醒**一行（见「未覆盖的最后一行」）。

**05d 的三条口径（与 05b / 05c 不同，值得单独记）：**

| 决策 | 内容 | 理由 |
|---|---|---|
| **同意与同步触发是设备 op** | `consent_record` / `sync_trigger` 进 `DEVICE_EXECUTED_OPS`，服务端只挂起 | 服务端缺的**不是存储位置而是设备身份**：同意按 `device_id` 存（设计上允许注册前打点），而 `/ai/agent` 的请求里没有设备标识；「立刻同步」是设备发起的行为，服务端没有主动触发的通道。两者客户端都已有桥命令 |
| **注销走 `account_deletion_request`，`account_delete` 不接线** | 执行体在服务端内部走完 `request_deletion_token` → `confirm_deletion` 两步 | `account_delete` 的 payload 要一次性 `deletionToken`——**凭据不进模型上下文**（与 ADR-003 同一类问题）；而两步之间的确认卡可能让 5 分钟的 token 过期。「不绕过既有流程」的落点是**一行删号逻辑都没重写**：账号只到 FROZEN，7 天后的物理删除仍归 `infra/cleanup/account-deletion.py`，撤销入口原样可用 |
| **反馈的设备元信息留空** | `app_version` / `android_version` / `device_model` 一律填 `""` | 服务端不知道这台设备是什么型号、装的哪个版本。**留空是诚实的缺失**（Grafana 上看得出），编一个 `"unknown"` 是往运维数据里掺假 |

**新增第三个审批理由：** `tools.HIGH_RISK_OPS` → `confirm_reason` 的 `high_risk`
分支。架构 §7.4 那张表里的「高风险」一类此前没有信号源，注销是它的第一个——判据
是「**后果不可通过 App 内的任何操作挽回**」，而不是「会写库」（写库的 op 有三十
多个）。它排在 multi_step / low_confidence 之前，与 warnings 带来的低置信构成
**双保险**：两条判据各自独立，任一条被将来的改动放松，另一条仍拦得住。

> **订正（2026-09-28，T20-7 收口）**：以上是 T-M7-05d 当时的记录，**结论不改**。
> 现状是**四档**理由 —— `high_risk` / `medium_risk` / `multi_step` / `low_confidence`
> （逐 op 清单见 `docs/product/ai-confirmation-policy.md`），且 `HIGH_RISK_OPS`
> 已改为**从注册表派生**（唯一事实源 = `Tool.risk == "high"`），不再手写第二份
> （REQ-78 / T20-3）。

**回执字段（`transaction_ids`）的第二次语义扩张：** 05d 的四个执行体返回的
`entity_type` 是 `export` / `feedback` / `user`——都不是 sync 的实体类型。它们的
回执**不登 sync change**（设备 pull 的八类白名单里没有它们），`entity_type` 只是
一个标签。见 `proposal_ops.OpReceipt` 的 docstring。

### payload 的所有权（本契约最重要的一处取舍）

**v2 只校验信封，不校验 payload 内部。** 字段形状由 op 自己的工具注册表条目拥有（`tools.py` 的 `parameters`，已是 Draft-07）。

理由：把 op 的字段再写进契约＝给同一件事造第二个事实源，两份必然漂移；而工具注册表本来就随工具走，是唯一会与实现同步的那一份。

**代价（已知并接受）：** 客户端不做 payload 级授权。**结构通过不是执行许可**——payload 里的分类 / 账户 / 目标是否属于当前用户与账本，由服务端在**组装与执行时**施加（同 §7.4 的 TOCTOU 重校验）。这与 v1 的 `ProposalValidationContext` 并不冲突：v1 的上下文校验存在，是因为 v1 的提案由客户端组装（ADR-003）；v2 的提案由服务端 loop 组装，但 `/ai/validate` 与 `/ai/execute` 是**客户端可达**的端点——授权在那里做，理由不是「谁组装的」，而是「谁能打到这个端点」。**05b 已按此落地**（`authorization.py`，见上「服务端接线」表）。

**fixtures 里的 payload 内容只是信封用法的示例，不是字段契约**——判据在工具注册表。

### 设备能力通道不是 ActionProposal

架构 §4.3 的设备能力通道（刷新小组件、触感、分享、权限申请、图片选择、图标选择）走的是 `device_request` 事件与上行回传，**不产生提案**：它们是「任务里的设备动作」，不是「待审批的写库」。

只有 **`reminder_set`** 两头都沾——它写的是用户的提醒配置（因此要过审批门，进注册表），执行体在设备上（因此 05f 接客户端执行器）。op 的执行位置由**客户端按 op 分派**（05f 的分派表），不写进信封。

**`meta_set` 同理（T-M7-05b 核对实现后的结论）。** 它写的是设备本地的 `sync_meta`——默认账本 / 默认账户 / 小组件配置 / 用户画像都是本地展示元数据，桥命令 `meta.set` 的注释明写「不加实体列、**不上行**」，服务端没有这些键的存储位置（`BridgeProtocol.kt`）。因此：

- 工具（语义侧）在 **05b/05c/05d**：谁拥有那几个 key，谁就写它的枚举与描述；
- 执行（动作侧）在**设备**，由 05f 的执行器按 op 分派，与设备类桥命令同一套；
- 服务端 loop 对它的处置是**只挂起、不执行**（`tools.DEVICE_EXECUTED_OPS`；`execute_op` 另有 `ai_device_op_not_executable` 拒绝码，防止客户端把它投到 `/ai/execute`）。

> **这一条是过渡态，不是终态（用户 2026-09-20 明确的目标）。** 目标形态是**偏好项也上云**：先按 T-M7-01 的做法把 `sync_meta` 里用户级的那几个键做成可同步实体（默认项 / 当前角色 / 用户画像，键的取舍见计划 05j），**再把 `meta_set` 从设备执行移回服务端执行**——AI 因此能改默认项 / 当前角色 / 用户画像，客户端随 pull 应用，不必为它单独做设备分派。**「数据不上云」是历史原因，不是设计意图**：本表右边那列（谁执行）会随 05j 变化，而**左边的工具归属（谁拥有 key 枚举与描述）不变**。

> **对架构 §3.2 的一处订正（05b 落地时发现）：** 该表把「账户账本 → `meta.set(默认项)`」的执行位置标为**服务端**，与实现不符（见上）。同一行的「用户设定 → `meta.set(user_profile)`（注入 AI context）」同样如此：`user_profile` 也在 `sync_meta` 白名单里，注入走的是**客户端**把它带进 `/ai/interpret` 的 `context` 字段（T11-6），不是服务端读取。订正已写入 `ai-agent-architecture.md` §3.2。

### 设备能力通道协议（T-M7-05e）

**服务端侧自 05e 起已就位**（决策全文见 `docs/adr/ADR-010-device-capability-channel.md`），客户端执行器归 05f。

**两类协议**（架构 §4.3 的分法，判据是「结果有没有下游消费者」）：

| 类 | `kind` | 例子 | 流程 |
|---|---|---|---|
| 单向指令 | `one_way` | 触感反馈、刷新小组件、分享文本 | loop 下发即继续，**不回传** |
| 需结果 | `result` | 权限申请、图片选择 | loop **挂起** → 客户端执行 → **HTTP 上行回传** → loop 继续 |

**下行：`device_request` 帧**（八类事件之一，其余七类的载荷见 `server/src/modules/ai/stream.py`）：

```json
{
  "request_id": "dev-<uuid4>",   // 需结果类的回传凭据；单向也给（去重与留档）
  "step": 3,                      // 1 起的 Step 序号，与其它事件的 step 同源
  "kind": "one_way" | "result",
  "capability": "image.pick",     // 协议层能力名（见下）
  "payload": { "source": "gallery" },
  "timeout_seconds": 120          // 仅 result 类
}
```

**上行：`POST /ai/device-result`**（普通 HTTP，与那条 SSE 长连接无关）：

```json
{ "request_id": "dev-…", "status": "ok|denied|unavailable|failed", "data": { … } }
→ { "accepted": true }
```

| 环节 | 口径 |
|---|---|
| **404 覆盖四种情形** | 没有这条请求 / 已经回过（重复回传）/ 已超时注销 / 请求属于别人 —— 一律 `device_request_not_found`，**三者不可分辨**。重试因此是安全的，也不泄漏「这个 id 是否存在」 |
| **归属** | 按挂起时记下的 `user_id` 校验；别人的请求 id 拿到的也是 404，**不是 403** |
| `timeout` 不在 `status` 里 | 那个结局只能由服务端产生（挂起期满），客户端报它没有意义（schema 挡在 422） |
| **挂起态** | **进程内**（`devices.DeviceChannel`，单 worker 前提，架构 §8.4），随 Turn 生随 Turn 灭；不建表、不入库（代价见 R-PROD-32） |
| **超时与失败** | 都是**工具级结果**（安全枚举码回喂模型），**不新增 `StopReason`**：模型据此向用户解释，回合照常往下走 |
| **取消 / 断连** | 沿用连接级取消；`DeviceChannel.wait` 的 `finally` 注销条目——**不留悬挂请求** |

**能力名（`capability`）与工具名（`name`）是两套**：工具名走 provider，必须下划线（点号会让 provider 直接 400，02c 实测）；`capability` 只出现在帧与回传里，用点号。**能力名 → 桥命令的映射在客户端**（05f 的分派表）——协议**不限制**映射的基数（当年 `voice.transcribe` 就是一对 start / stop，见 ADR-010 的注记），服务端因此不需要知道客户端命令表的形状。当前目录里每个能力恰好落一条桥命令。

**服务端能力目录**（`server/src/modules/ai/devices.py`，5 个能力，与架构 §4.3 的例子一一对应；`voice.transcribe` 已随 REQ-75 下架）：

| 工具名 | `capability` | 类 |
|---|---|---|
| `device_haptic` | `haptic.feedback` | 单向 |
| `device_widget_refresh` | `widget.refresh` | 单向 |
| `device_share_text` | `share.text` | 单向 |
| `device_request_permission` | `permissions.request` | 需结果 |
| `device_pick_image` | `image.pick` | 需结果 |

**闸门：** `AI_DEVICE_CHANNEL_ENABLED`（默认 **false**）。关闭时 loop **不注入**设备工具（模型看不到它们，行为与 P1 逐字相同）——设备能力要有人执行，客户端执行器（05f）就位前打开它只会让回合白等一个超时窗。提醒（`reminder.*`）不在本目录：它是写工具（进注册表、过审批门），执行体在设备上属于**执行位置**的分派问题（05f）。

**不是注册表工具：** 设备能力没有 DB handler、执行体在客户端、执行语义分两类，因此单列目录而不是进 `tools.py` 的注册表；`payload` 的形状由能力自己的实现负责，服务端不校验（与 v2 契约「payload 的所有权在 op 侧」同一取舍）。

### 三条铁律在 v2 下的落点（不放松）

1. **模型永不输出 UUID** —— payload 里出现的 id 是**服务端确定性解析的产物**，不是模型输出；回喂给模型前必须剥掉（02c 边界不变）。
2. **金额与统计由确定性代码计算** —— payload 只接受原始参数，模型不给算术结论。
3. **写库必经校验与审批门** —— 客户端确认门的四值（`allowed-once` / `rejected` / `cancelled` / `unavailable`）与 02e 的挂起——放行路径在 v2 下**逐字不变**；v2 改的是提案的形状，不是安全门。

## 会话管理端点（T-M7-03a）

`/ai/agent` 的会话由**服务端持有**（`ai-agent-architecture.md` §6.3），P2 因此补四个管理端点：

| 端点 | 作用 | 关键口径 |
|---|---|---|
| `GET /ai/conversations` | 本人的会话列表（未归档，按活跃时间倒序） | 只回本人；不分页；`last_active_at` 一定有值（还没发言的会话用 `created_at` 兜） |
| `PATCH /ai/conversations/{id}` | 重命名 | **不动活跃时间**（改名字不该让会话在列表里跳位）；纯空白 / 超长 422 |
| `DELETE /ai/conversations/{id}` | 删除 | 行归档（`archived_at`）、事件正文删除；重试安全 |
| `GET /ai/conversations/{id}/events?after_seq=N` | 事件增量读取 | `seq` **严格大于** `after_seq`，按 `seq` 升序；原样返回（Surface 子集归 P3 的 04a） |

**归属校验：** 别人的 id、已删除的 id、不存在的 id 一律 404 `conversation_not_found`，三者不可分辨（读取面的唯一防线）。

**删除语义：** 会话行**归档**而不是物理删除（`ai_usage.conversation_id` 指着它，硬删会把审计行置空；P3 的保留期也只删事件、不删行），而事件正文**立即删除**——用户主动删除后服务端不该再持有它。

**登出粒度（2026-09-19 结论，落地 P1 挂账 1）：** 登出仍按**用户**粒度清理该用户的全部会话，**不引入设备维度**。`ai_conversations` 没有设备列、`/ai/agent` 也不带 `device_id`，加一层设备维度等于同时改请求契约与客户端桥，而 P2 不做跨设备正文同步（归 P3 的 04e 决策门）。代价（已知并接受）：多设备下 A 机登出会让 B 机的服务端会话消失，B 机下一轮带旧 id 会拿到 404——**客户端必须把它当作「该 id 已不是我的会话」，丢弃并开新会话，不得作为错误呈现**；本地投影缓存仍在，历史依然可见。

### 客户端接入（T-M7-03d）

**桥命令（桥计数 79→82）：** `conversation.list` / `conversation.rename` / `conversation.delete`——三个都是请求-响应（走 25 s 单发预算，不是 SSE）。

- **`conversation.events` 是 04b 补的命令**（桥计数 82→83）：P2 时有意不加（无人调用只扩大 APK 契约面），04b 为断连续读引进、**04e 起被切会话路径复用**，见下节。
- **「新建会话」不需要命令**：服务端在首轮不带 `conversation_id` 时自建，`done` 帧回传 id；客户端只是把活动桶换成空的**本地槽**（让位的那条铸键留档，见下）。
- **`AiError.NotFound` 单列成类**（同步路径与 SSE 路径都映射它）：此前 404 与普通网络故障在客户端不可分辨，而服务端对它有一层确定语义。SSE 路径上的 404 表现为一帧 `error{code:"not_found"}`（服务端在**开流之前**回 404）。
- `DELETE` 回 204 无响应体，桥侧造 `{success:true}`——空串会让响应信封序列化直接抛、前端调用悬挂到超时。

**陈旧 `conversation_id` 的降级（终态）：** 客户端拿到 `not_found` → `forgetConversation()`：**丢弃该 id、当前这一屏消息原地保留**（不清空、不切桶）→ 下一轮不带 `conversation_id`，服务端开新会话。**不得作为错误呈现**（多设备登出是已知且接受的代价）。

**删除后的回退目标（确定性）：** 列表里**剩下的第一条**（服务端按活跃倒序 → 最近活跃的另一条）；一条不剩则回退到本地会话桶。删除成功后旧 id 与别人的 id 拿到同一个 404，客户端把它**与成功同等对待**（`routes.py` 的重试契约）。

**`book_id` 联动规则：会话的 `book_id` 只记录不驱动。** 切换会话**不改变**当前记账账本（每轮仍用页面当前账本 `bookId`），避免「切个会话把全局默认账本改了」这类副作用；`book_id` 只作列表展示与会话归属记录。

**流式期间禁止切换会话。** 一轮的输出落在活动会话桶上（客户端 `messages` 是扁平的、`setMessages` 的调用点不带会话目标），流中切换会让这一轮的消息落到「切过去之后」的会话里。根治要把一轮绑定到发起时的会话（十几处调用点），属后续卡；在此之前用「流式期间禁用切换 / 新建 / 重命名 / 删除」守住不变量。

**降级（离线 / 未登录 / 桥不具备命令）：** 列表退化为**本机投影缓存**（`assistant-chat-store` 的会话桶，标题就地取首条用户消息），**允许切换**（只读缓存），**禁用新建与写操作**，不抛错、不弹红条。

**会话桶的键空间（2026-09-19 收口补充，`998d495`）：** 客户端有两类会话，**列表要同时列出**：

| 键 | 是什么 | 有服务端行？ |
|---|---|---|
| `__local__` | **本地槽**：当前那条尚未固化的会话（首轮 `done` 之前、或全程离线） | ✗（UI 上以 `null` 表示） |
| `local:<uuid>` | 让位下来的本机会话（离线轮 / 失败轮留下的，或「新建」让位下来的） | ✗ |
| 服务端 UUID | 服务端会话（权威面） | ✓ |

- **本机会话必须进列表**：它们不在服务端列表里，不列出来就再也切不回去——离线轮 / 失败轮留下的历史会变成不可达的死数据。它们没有 `serverId`，因此**不给重命名 / 删除入口**（那两个动作要打端点），但可点开切换。
- **「新建」= `newConversation()`**，不是 `switchConversation(null)`。后者在活动桶已是本地槽时 `from === to` 直接返回（**按了没反应**），在缓存里还压着一条本地槽时会把旧内容顶上来（「新会话」显示成别的会话）。前者把让位的本地会话**铸成 `local:<uuid>` 留在缓存里**（零删除），再换一个空槽上来——「新建」因此既真的新、也不丢东西。
- **`local:*` / `__local__` 一律不得出现在 `/ai/agent` 的 `conversation_id` 上**（会撞服务端的 UUID 校验，得到 422 而不是新会话）。闸放在**请求组装处**（`native-bridge.runAgent`，同 Kotlin 的 `buildAgentRequest`），谓词 `toServerConversationId` 由 store 提供（单一事实源），调用方绕不过。

### 跨设备正文：按需重建（T-M7-04e，决策见 `ADR-008`）

**结论：正文跨设备可见，但用「按需重建」实现，不做双向同步实体。** 会话正文的权威在服务端（事件日志＝事实来源），客户端投影 B 是**派生数据**；把它提升为同步实体等于给一份不产生新事实的数据造第二个写入者，要凭空引入冲突消解。所以客户端**从不把投影写回**服务端，冲突策略这一议题因此不存在。

**落地：** 切到一条服务端会话时（`use-conversations.selectConversation`），按三条规则决定要不要拉 `conversation.events`：

| # | 本机状态 | 动作 | 为什么 |
|---|---|---|---|
| ① | **无**该会话的投影 | 全量重建（不带 `after_seq`） | 「换设备后打开旧会话」就是这条 |
| ② | 有投影且**游标已知** | 从游标**增量补齐**尾部，本地消息**一条不动** | 重建只认得正文；本地还有日志里没有的东西（历史只读记账卡、离线轮的中断标注），顶替会抹掉它们 |
| ③ | 有投影但**游标未知** | 原样保留，不拉取 | 未知即无从判断从哪儿续；宁可少刷新一次，也不要重复显示一轮 |

**游标（`assistant-chat-store` v3 的 `cursors`）** 是「本地这条投影已覆盖到日志的哪一行」，**本地事实**、随投影落 localStorage、登出一起清。写入点只有三处：正常收尾（`done.seq`，经投影的 `logCursor` 交接）、一次成功的事件拉取、失败收尾（续读已把游标推到日志的真实位置）；**只前进不回退**。取消会把游标**抹成未知**——服务端在断连取消时不写任何日志事件，但这一轮已落库的部分确实在游标之后，推进会漏、不推进会重（下一次正常收尾自愈）。

**重建出的投影含什么：** 用户 / 助手正文 + 失败轮的中断标注（与实时路径共用 `INTERRUPTED_TURN_NOTE`）。**不含：** 历史只读记账卡（提案的日志留档是 `approval_request`，该生产者**未接**，04a 记的未指派项）与步骤卡片（`AssistantMessage` 里本来就没有它，同机重载也没有）。规则 ②③ 使本机看过的卡片不会被刷新抹掉。

**空态的四句话**（`AssistantPage` 的 `emptyMessagesHint`，各对应一个真实原因，不合并——合并就会说谎）：本地槽 → 引导语；拉取中 → 「正在载入这条会话…」；拉取失败 → 「暂时拉不到这条会话的消息」；拉回来是空 → 「这条会话还没有消息」；桥没有该命令（旧 APK）→ 「当前版本不支持跨设备读取」。

**不做的事：** 零新协议、零新端点、零服务端改动；不改保留期（04d）与写路径安全门（02e）；不做摘要同步（摘要的生成归 **04c**，且它只覆盖超窗轮次，本就不回答「打开旧会话看见什么」）。

## 客户端确认门：审批四值、取消与中断（T-M7-03e）

服务端 loop 只**产出**挂起提案（02e 的 `AgentResult.proposals`），从不自己等回答——`stream.py` 不接 `approver` 通道（02g 决策 2：桥只有 `ai.agent` / `ai.cancel`，没有上行审批）。**审批回答因此整个落在客户端**，四值（架构 §7.4）在这里的落点是「用户对这张卡的处置」：

| 值 | 触发 | 效果 |
|---|---|---|
| `allowed-once` | 卡上点「确认」 | 执行写入（唯一放行值）；**执行后即失效**，下一次挂起重新问 |
| `rejected` | 卡上点「拒绝」 | **零写**；本项不改，**任务不因此中止** |
| `cancelled` | 输入区「停止生成」 | **零写**；整个任务中止（架构 §4.4 不变式 3：不留悬挂的确认卡） |
| `unavailable` | 本轮以失败 / 断连收尾时仍挂着未回答的提案；或中途来的提案不在 `done.proposals` 里 | **视为拒绝、零写**（架构 §7.4：不确定时倾向拒绝） |

**事件驱动 + 权威核对（两条一起才成立）。**

- **卡随 `confirm` 帧实时出现**（不再等 `done`）——用户能在 agent 还在跑时就看见它想做什么（架构 §7.4：审批卡同时是安全门与纠偏点）。一轮可以有多张（多步任务的每次写各一张），客户端按队列**逐张**处置，确认完一张顶上下一张。
- **但动作只在收尾后启用**：`done.proposals` 才是权威清单（`stream.py` 口径：`confirm` 是实时信号、`done.proposals` 是权威清单）。收尾时按 `proposalId` 核对队列——
  - 队列里**不在**清单上的（中途来的 / 权威清单没有它的）→ `unavailable`，零写；
  - 清单里**不在**队列上的（`confirm` 帧丢了）→ 补进队列，不因丢帧而漏掉一个提案。
- 因此 02i 决策 ③「不给中途伪造的提案开写入口」**仍然成立**：中途可见不等于中途可执行。UI 上按钮禁用，`editProposal` 的待确认分支还有第二道防线（本轮代理仍在流式中 → 不写库、只提示）。

**「取消」的服务端可观测形态（落地 P1 挂账 3）：** 仍是**断连**，P2 不改这一点——不新增取消端点、不发取消帧，`StopReason.CANCELLED` **仍不可达**。理由：① 服务端 02g 已把「断连即取消」实现为连接级取消（工作协程被取消、未提交即回滚）；② 加显式取消通道要动已冻结的桥命令表与 `/ai/agent` 协议，而取消的用户已经离开，多一次模型往返或一条事件没有价值（02d 决策 8）；③ 真正需要服务端可观测的是**断连恢复**（P3 的 04b 按 `conversation_id` + `seq` 续跑），届时一并评估。**客户端凭本地 `canceled` 标志收敛**（`ai.cancel` → 桥 `cancelAndJoin` → 关 SSE）。

**取消（主动）与中断（被动）不合并。** 取消是用户按的，中断是链路断的——两者在 UI 上是两种说法，恢复路径也不同：

| | 触发 | 表现 | 恢复 |
|---|---|---|---|
| 取消 `cancelled` | 「停止生成」 | 气泡与半截文本 + 「已取消」 | 用户自己重说 |
| 中断 `unavailable` | `error` 帧 / 桥层失败 | 「⚠️ 连接中断，本轮未完成」（挂起提案未记录时另加一句）+ **提示条与「重发」** | 点「重发」原样再发一次（服务端本轮不落库，重发安全） |

中断态（`interrupted`）是**瞬时态**、不落盘；P2 的恢复手段是**原样重发**，按 `conversation_id` + `seq` 的续跑归 P3 的 04b（届时入口换成「继续」）。

**零写保证：** 四值里只有 `allowed-once` 会写库；拒绝、取消、未授权三条路径都有用例断言 `insertTransaction` / `updateTransaction` / `softDeleteTransaction` 零调用。
