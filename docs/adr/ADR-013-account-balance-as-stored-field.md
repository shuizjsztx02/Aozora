# ADR-013 账户余额做成存量字段

**状态：** 已采纳（2026-09-23）
**范围：** 第十八轮 REQ-69（T18-5 地基 / T18-6 设值与 AI / T18-7 记账联动），属 **L3 变更**（数据模型 + 同步契约 + AI 工具面）
**关联文档：** `docs/APP-CONTEXT.md` §5.1（核心实体）、§8.2（账本、分类与账户）；`docs/architecture/data-model.md`（`accounts` 表一节）；`server/src/models/ledger.py`；`server/src/migrations/versions/0022_account_balance_type.py`；`server/src/modules/sync/service.py`；`app/src/main/java/com/aozora/app/db/entity/AccountEntity.kt`；`server/tests/test_sync_cloud_entities.py`（`TestAccountTypeAndBalance`）；`app/src/test/java/com/aozora/app/db/RepositoryAccountBalanceSyncTest.kt`；R-PROD-43

> **为什么需要一份 ADR：** 本决策**推翻一条在册的产品口径**——`APP-CONTEXT.md` 原文「Account 仅支付渠道标记，**不维护真实余额**」与验收项「**账户不计算余额**」。推翻之后有三件事会被后续每一张卡依赖，且**选错时没有任何用例能从内部发现**：①余额到底是「存的值」还是「算出来的值」（决定 REQ-70 是写端联动还是读端推导）；②它与既有只读工具 `account_balances` 现算的净额**必然不一致**，谁来解释这个差额；③上行的是绝对值还是增量（决定重放是否安全）。三条都只在真实同步/真机场景里才暴露。

## 背景

### 七条影响结论的硬事实

| # | 事实 | 依据 |
|---|---|---|
| 1 | **两端都没有余额列**，且这是明写的产品口径 | `server/src/models/ledger.py` Account（id/owner_id/name/icon/archived_at/version）；`AccountEntity`（v11 前 7 列）；`APP-CONTEXT.md:274`「不维护真实余额」、`:464`「账户不计算余额」 |
| 2 | **AI 侧已有的余额是「按交易现算的净额」**，不是存量值 | `ai/tools.py` 的只读工具 `account_balances`：`func.sum(case(...))` 扫 transactions |
| 3 | **「期初余额」概念早就在，但刻意不落库** | v1 config 车道 `initBalance` 进预览行与回执，执行时明确不写：`ai-proposal-builder.ts:792` 注释「只展示会真实写入的效果：budget 的 name 与 account 的 initBalance 无 native 落点」；`use-assistant.ts:1704` 回执「期初余额 … 请再记一笔写入（账户暂无期初字段）」 |
| 4 | **范式现成**：实体自带「余额类」列并上行同步已有先例 | `savings_goals.current_cents`（迁移 `0016_cloud_entities.py`，`>= 0` CHECK + 客户端 `SavingsGoalEntity.currentCents`） |
| 5 | **映射早已登记，无生产者** | `SyncPayloadMapper.kt` 的 `SNAKE_TO_CAMEL` 里 `"balance_cents" to "balanceCents"` 一行在库，但没有任何实体产生它 |
| 6 | **`accountType` 缺两端化，症状与 REQ-67 同源** | 客户端 `accountToJson` **一直**在 payload 里带 `accountType`，服务端 `_apply_insert/_apply_update` 的 account 分支只读 name/icon → 上行静默丢失；pull 侧 `toAccount` 读到 null 又反向冲刷本地 |
| 7 | 用户诉求是**两个子诉求并列**（分类 + 余额），且要求「记账时同步更新余额」 | 第十八轮 REQ-69 / REQ-70 原话 |

### 问题的真正形态

用户要的是一句很朴素的话：**「这张卡现在有多少钱，我来说」**。这句话决定了余额必须以**存量字段**存在——因为它是**用户输入**，不是系统推论：

- 存量值可以承载「人为校准」（账单不全、历史迁移、口径调整）；
- 推导值只能回答「如果所有交易都记全了，应该是多少」，而这恰恰是**用户不能保证**的前提；
- 一旦做成推导值，「我设的 5000」这句话就没有落点——只能反过来存一个「期初偏移」，而那个偏移会在任何一笔历史交易被编辑/删除时**静默漂移**。

同时，余额从「不可见」变成「两端可见」之后，**第二个真相**出现了：`account_balances` 现算的净额与这个存量值必然对不上（差额 = 校准量）。同一句「我的交行卡还有多少钱」将有两个答案，必须当场定一个口径。

## 备选方案

| 方案 | 内容 | 评估 |
|---|---|---|
| **A. 不存列，纯由交易推导**（维持现状口径） | 只保留 `account_balances` 这类现算 | **否决。** 「手动设置余额」在该方案下**不可表达**（事实 2）；且事实 3 已经证明这条路上「期初余额」只能进回执、进不了库 |
| **B. 存「期初余额」，展示 = 期初 + Σ交易** | 用户设的当前余额反算成期初偏移 | **否决。** 用户设的数值会**静默漂移**——任何一笔历史交易被编辑/软删（本轮之前就存在的能力）都会让「我设的 5000」变成别的数；且它要求每次展示都做一次聚合扫描。方案 B 唯一的好处是让 REQ-70 自动成立（无需写端联动），但代价是用户输入不再守恒 |
| **C. 存量字段 + 两端化**（**采纳**） | 客户端 `AccountEntity.balanceCents` + 服务端 `accounts.balance_cents`，与分类 `account_type` **同批**两端化 | 用户设的就是存的；上行/下行复用既有 sync 协议（事实 4 的范式）；代价是 REQ-70 需要写端联动（另立一卡） |
| **D. 存量字段但只存本地**（照抄 icon 照片版） | 不进 sync | **否决。** 换机 / bootstrap 重建即丢；而 AI 改账户是在**服务端**执行的，本地专属值 AI 够不着 |

## 决策

### 决策 1：余额是**存量字段**，两端各一列，允许负值

- 客户端 `AccountEntity.balanceCents: Long`（Room v11，`MIGRATION_10_11`，`NOT NULL DEFAULT 0`）；服务端 `accounts.balance_cents`（`BIGINT NOT NULL DEFAULT 0`，迁移 `0022`）。
- **不加非负 CHECK**，与 `savings_goals.current_cents` 的 `>= 0` **刻意不同**：信用卡欠款是常态，负余额必须能存。服务端校验只拒非整数与超安全整数范围（`_validate_balance_cents`，签名版）。
- `MoneyCents` 的非负契约**不放开**：它保护的是金额主链路；负余额的展示沿用既有 `Math.abs` + 手拼负号口径（趋势卡浮层先例）。这是本列的**唯一例外**，范围仅限本列。

### 决策 2：余额与交易净额**不保证相等**，差额 = 手动校准量

- 这是**正常状态**，不是数据损坏：存量值可被校准，净额不会。
- AI 只读工具 `account_balances` 现算的是净额 → **必须给它统一口径**（同时给「交易净额」与「当前余额」两项，并在工具描述里说明差额来源），否则模型会对同一问题给出两个自相矛盾的答案（**T18-6 的交付内容**）。
- 服务端统计（`statistics`）口径**不动**：仍按交易算，不读余额。

### 决策 3：上行恒传**绝对值**，不传增量

- 账户 mutation 的 payload 携带**更新后的余额本身**（`accountToJson` 的 `balanceCents`）。
- 这是**重放安全的关键**：离线队列重试、pull-push 重放同一 mutation 都不会重复累加。若传「+100」，重放即重复记账。
- **服务端为权威**：客户端本地乐观更新，pull 覆盖（`applyServerChanges` 的 account 分支对 `balanceCents` **不加**本地保护——`0` 是合法值，保护它就永远吃不下云端清零）。本地在途编辑由既有「活跃 mutation 跳过合并」兜住。
- **已知代价**：多设备各自离线记账后同步会互相覆盖（后到者胜）。登记 **R-PROD-43**（由 T18-7 落笔）。

### 决策 4：`account_type` 与 `balance_cents` **必须同批两端化**

- 只改 AI 工具不改存储，会让用户看到「AI 说改好了 → 同步回来又变回去」——**与 REQ-67 同源**（事实 6）。
- 客户端 pull 侧的保护口径按「服务端空值不算权威」：`account_type` 在服务端为 `null` 时**保留本地值**（存量行服务端仍是 NULL，而本地早已存着用户选的分类）；非空时服务端权威。服务端 update 分支按「键在场才改」，使尚未升级的旧客户端（payload 里没有 `balance_cents`）不会把服务端余额清零。

### 决策 5：明确不做的事

不做余额流水 / 对账单、不做按月余额快照、不做多币种、不改账户选择逻辑（`pickDefaultAccount`）、不改 `account.archive` 语义、不让服务端也存默认 emoji 式的「服务端默认值」（服务端保持 NULL / 0 的中性缺省，兜底留在客户端）。

## 后果

### 残余与责任人

1. **bootstrap 重建会丢「从未上行过」的本地 `account_type`**：`SyncEngine.runBootstrap` 先 `clearAllEntities()`（含 accounts）再 `applyBootstrap`，故 pull 侧那层本地保护在 bootstrap 路径上无从生效——服务端仍是 NULL 的行，本地值随清空消失。**本卡不做一次性回填上行**（存量 local-only 分类只在用户下次编辑该账户时随 mutation 上行）。**建议**：若真机上观察到历史分类丢失，另立一张「账户字段回填上行」卡（照 `migratePreferencesUp` 的模式）。责任人：T18-9 收口判定。
2. **多设备并发覆盖**（决策 3 的代价）：登记 **R-PROD-43**，由 T18-7 落笔（`docs/risk-register.md` 由收口统一写）。
3. **负余额的渲染未随本卡落地**：本卡只做「存得下 + 传得准 + 同步得住」；账户页显示、AI 工具参数与 `account_balances` 口径对齐归 **T18-6**。渲染负值必须走 `Math.abs` + 手拼负号（不得放宽 `Money.cents`）。
4. **记账联动归 T18-7**：本卡刻意**不**在 `insertTransaction` / `updateTransaction` / `softDeleteTransaction` 里动余额——那是 REQ-70 的范围，且它依赖本卡的两列先存在。