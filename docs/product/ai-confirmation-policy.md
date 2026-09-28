# AI 指令确认清单（REQ-78）

> 这份文档回答一个问题：**AI 能做的那些事里，哪些可以直接执行，哪些必须人工点一下确认。**
> 它是 2026-09-28 第二十轮 REQ-78 的成文交付物，也是**给人看的唯一一份**——
> 机器读的那一份在注册表里（见下「单一事实源」）。改动行为时先改注册表，再让这份
> 文档跟上（对账由用例强制，跟不上就红）。

## 单一事实源

风险维度只有一个落点：**`server/src/modules/ai/tools.py` 的 `Tool.risk`**
（设备能力目录 `server/src/modules/ai/devices.py` 的 `DeviceTool.risk` 同款）。
**三档判据的原文写在那两个字段的 docstring 里**——本文档只做摘要与列举，
不另立第二把尺子。

三处自动对账由 `server/tests/test_ai_confirmation_policy.py` 钉住：

1. **注册表逐条有 `risk`**：新加工具忘了标，导入期就 `TypeError`（字段无默认值），
   用例再遍历一遍整个注册表——不是静默降级成 `low` 而悄悄自动执行。
2. **本清单与注册表逐条相等**：下面的表里出现的 op 集合 == 工具注册表 ∪ 设备能力
   目录，且每行的「分级」与注册表一致。
3. **`HIGH_RISK_OPS` 从注册表派生**，不手写第二份（`tools.py` 里由
   `TOOL_RISK_BY_OP` 派生的那个 `frozenset`）。手写一份的后果是 R-PROD-47 记过的
   同族陷阱：「同一把尺子有三层，必须同时是 64」——改一处忘一处，且不报错。

## 三档判据（摘要）

| 分级 | 处置 | 判据 |
|---|---|---|
| `low` | **直接执行** | 新增类写操作、查询类、**可一键改回的设置项**。共同点是**不碰用户已有的数据行**：写坏了也就是多了一条、或换了个默认值，用户自己再点一下就能改回来 |
| `medium` | **需确认** | 会**改动或删除用户既有数据**的写：交易 / 预算 / 账户 / 账本 / 分类 / 定时 / 角色 / 余额；**以及导出域与隐私域的写**（按域判，不逐 op 再议） |
| `high` | **强制确认，且卡片明示高风险** | 后果**不可通过 App 内的任何操作挽回**：注销账号、**清空或批量删除语义**的 op、隐私同意撤回。当前达标两个：`account_deletion_request`（7 天冷静期后数据永久删除）、`book_delete`（账本**连同其中全部流水**一并不可见） |

**这不是「会不会写库」的分级**——写库的 op 有三十多个，全是写库就区分不出来了。
它分的是**后果的形状**：多一条 / 少一条（`low`）、改了用户攒下来的东西（`medium`）、
改完在 App 里找不回来（`high`）。

**为什么不一律确认**：写操作一律出卡会击穿 PR-2 的高置信快速通道——记账那条主路径
（「记一笔 30 块午餐」）要多等一次点击；而一律自动执行正是 REQ-78 的原始问题
（「让 AI 删掉这笔账」不问一声就落了库）。两头的代价都真实，故取分档。

## 判定顺序（理由码）

`agent.confirm_reason(*, step, confidence, op)` 按顺序判定，**顺序即优先级**：

| 序 | 理由码 | 触发条件 |
|---|---|---|
| 1 | `high_risk` | op 的风险 = `high`——不看步数、不看置信度 |
| 2 | `medium_risk` | op 的风险 = `medium`（**REQ-78 新增的第四个理由码**） |
| 3 | `multi_step` | 提案产出时已经是第 ≥2 个 Step（模型先查了别的才写） |
| 4 | `low_confidence` | 置信度 < 0.9（草案带 `warnings`，P1 只有 1.0 / 0.8 两档） |
| — | `None` | 都不命中 → **自动执行**（沿用快路径 + 客户端撤销窗） |

前两条判「**做什么**」（后果的形状，与模型的把握无关），后两条判「**模型自己有多稳**」
——两类正交，同时命中时报前者：用户问「为什么这次问我」时，「这个操作会改掉你已有的
数据」比「这一步是第 2 步」有用得多。**`low` 的 op 完全不受本卡影响**：
`transaction_add` 在两步流程里仍报 `multi_step`、单步高置信下仍是 `None`。

## 逐条清单

覆盖**工具注册表全部 56 条 + 设备能力目录 5 条 = 61 条**（读工具也在内：用户问的是
「哪些指令能直接跑」，只读指令同样是「指令」）。「处置」列的五种取值：

- **直接执行** —— 服务端自动执行（`low`，或读工具）。
- **需确认** —— 服务端出确认卡，用户点了才落库（`medium`）。
- **强制确认** —— 同上，且卡片明示高风险（`high`）。
- **确认后设备执行** —— 服务端**只挂起不执行**（它没有这些 op 的落点，
  见 `tools.DEVICE_EXECUTED_OPS`），确认后由设备经既有桥命令执行。
  这四条（`persona_switch` / `consent_record` / `sync_trigger` / `reminder_set`）
  **无论分级都出卡**：风险值在这里是宣告性的，不参与服务端的审批策略判定。
- **设备指令** —— 走设备能力通道（`device_request` 帧），不产提案、不进审批策略。

| op | 域 | 分级 | 处置 | 理由 |
|---|---|---|---|---|
| `transaction_add` | transaction | `low` | 直接执行 | 新增一笔记录，不碰任何既有数据 |
| `transaction_add_batch` | transaction | `low` | 直接执行 | 一次新增多笔——**批量新增**不是批量删除，同样不碰既有数据 |
| `transaction_update` | transaction | `medium` | 需确认 | 改动用户已经记下的一笔 |
| `transaction_delete` | transaction | `medium` | 需确认 | 删除用户已经记下的一笔 |
| `transaction_list` | transaction | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `transaction_recent` | transaction | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `transaction_summary` | transaction | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `budget_list` | budget | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `budget_status` | budget | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `budget_check` | budget | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `budget_summary` | budget | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `budget_overspent` | budget | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `budget_set` | budget | `medium` | 需确认 | 改动（或覆盖）既有预算：总预算 |
| `budget_set_category` | budget | `medium` | 需确认 | 改动（或覆盖）既有预算：某分类的预算 |
| `budget_delete` | budget | `medium` | 需确认 | 删除既有预算 |
| `account_list` | account | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `account_balances` | account | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `account_create` | account | `low` | 直接执行 | 新建账户 |
| `account_update` | account | `medium` | 需确认 | 改动既有账户的名称 / 图标 / 余额 |
| `account_archive` | account | `medium` | 需确认 | 停用既有账户（已记录的交易保留） |
| `book_list` | book | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `book_detail` | book | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `book_create` | book | `low` | 直接执行 | 新建账本（空账本） |
| `book_update` | book | `medium` | 需确认 | 改动既有账本的名称 / 图标 |
| `book_delete` | book | `high` | 强制确认 | 删账本**连同其中全部流水**一并不可见——本工具集里唯一的批量删除语义 |
| `category_list` | category | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `category_create` | category | `low` | 直接执行 | 新建分类 |
| `category_update` | category | `medium` | 需确认 | 改动既有分类的名称 / 图标 / 上级 |
| `category_archive` | category | `medium` | 需确认 | 停用既有分类（已记录的交易保留原分类） |
| `savings_list` | savings | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `savings_create` | savings | `low` | 直接执行 | 新建存钱目标 |
| `savings_update` | savings | `medium` | 需确认 | 改动既有目标：改名 / 目标额 / 期限，以及**存入或更正已存金额** |
| `savings_archive` | savings | `medium` | 需确认 | 停用既有存钱目标 |
| `scheduled_list` | scheduled | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `scheduled_create` | scheduled | `low` | 直接执行 | 新建定时记账规则 |
| `scheduled_update` | scheduled | `medium` | 需确认 | 改动既有规则：金额 / 周期 / 启停 |
| `scheduled_archive` | scheduled | `medium` | 需确认 | 删掉既有定时规则（已生成的记录不受影响） |
| `persona_list` | persona | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `persona_create` | persona | `low` | 直接执行 | 新建聊天角色 |
| `persona_update` | persona | `medium` | 需确认 | 改动既有角色的人设 / 名称 / 头像 |
| `persona_archive` | persona | `medium` | 需确认 | 删掉既有角色 |
| `persona_switch` | persona | `low` | 确认后设备执行 | 只切**当前用哪个角色**这个指针——一键可切回，不改角色本身 |
| `meta_set` | meta | `low` | 直接执行 | 只改默认值 / 配置（默认账本 / 默认账户 / 小组件 / 用户画像），一键可在设置页改回；**不动任何已有记录** |
| `stats_monthly` | analysis | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `stats_trends` | analysis | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `export_create` | export | `medium` | 需确认 | 导出：当前账本的流水变成文件离开 App |
| `export_list` | export | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `feedback_submit` | privacy | `medium` | 需确认 | 隐私域的写：用户原话外发给开发团队（**边界案例**，见文末「两处刻意取舍」） |
| `consent_record` | privacy | `medium` | 确认后设备执行 | 隐私域的写：记录本设备已同意隐私政策（同意之后同步才可用） |
| `consent_status` | privacy | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `auth_update_profile` | security | `medium` | 需确认 | 改动账号资料里的昵称 |
| `account_deletion_request` | security | `high` | 强制确认 | 注销账号：7 天冷静期后账号与全部数据永久删除，App 内无法挽回 |
| `sync_status` | sync | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `sync_trigger` | sync | `low` | 确认后设备执行 | 只是让设备立刻同步一次，无副作用、可反复执行 |
| `reminder_set` | reminder | `low` | 确认后设备执行 | 提醒配置是设备本地的设置项，一键可改回；**不动账本** |
| `help_search` | help | `low` | 直接执行 | 查询类：只读、不产提案，loop 内直接执行 |
| `device_haptic` | device | `low` | 设备指令 | 一次触感反馈，无副作用 |
| `device_widget_refresh` | device | `low` | 设备指令 | 刷新主屏小组件，无副作用 |
| `device_share_text` | device | `low` | 设备指令 | 去向由用户在**系统分享面板**里自选——系统面板本身就是人工确认点 |
| `device_request_permission` | device | `low` | 设备指令 | 系统权限对话框本身就是人工确认点 |
| `device_pick_image` | device | `low` | 设备指令 | 用户自己选图或取消 |

## 与架构 §7.4 的对应

`docs/architecture/ai-agent-architecture.md` §7.4 只保留**策略**（哪几种形态要确认），
逐条名单一律指向本文档（不在那边复制第二份）。对应关系：

| 本清单的处置 | 架构 §7.4 的说法 |
|---|---|
| 直接执行（`low`） | 「单 Step 完成」行：confidence ≥ 0.9 自动执行 + 撤销窗（PR-2 高置信快速通道） |
| 需确认（`medium`） | 「单 Step 完成」行：低置信 / **中风险** / 高风险出确认卡——`medium` 就是 REQ-78 从旧「高风险」一类里拆出来的那一档 |
| 强制确认（`high`） | 「风险分级」行：`medium` 与 `high` 一律强制确认，不看步数与置信度 |
| 确认后设备执行 | 不在 §7.4 的策略表里——它在 `AgentLoop._handle_proposal` 的第一步就分流了（同 §4.3 设备能力通道） |
| 设备指令 | §4.3 设备能力通道 + ADR-010 |

判定结果落在 `agent.confirm_reason` 的理由码上，**审批四值**（`allowed-once` /
`rejected` / `cancelled` / `unavailable`，只有 `allowed-once` 放行）仍是 §7.4 定死的那套，
本清单不改它。

## 两处刻意取舍

**① `feedback_submit` 归 `medium`。** 它不改任何既有数据，按「改动或删除既有数据」
这把尺子该是 `low`；归 `medium` 是因为判据的第二条按**域**判——导出域与隐私域的写一律
`medium`（「用户内容外发」与「导出」是同一类后果：数据离开 App）。按域判是刻意的：
逐 op 再议一次，等于每个新工具都要重新吵一遍边界，而那正是 REQ-78 要终结的局面。

**② 设备能力 5 条全是 `low`，尽管它们「在设备上做动作」。** `device_share_text`
（分享文本给外部 App）初看像「数据离开 App」，但去向由用户在**系统分享面板**里自选
——系统面板本身就是人工确认点，再叠一张卡是重复询问。同理
`device_request_permission`（系统权限对话框）、`device_pick_image`（用户自己选或取消）。
这三条的「人工确认」由系统 UI 承担，不由 AI 的确认卡承担。

## 不做什么

- **不给读工具分级**。读工具不产提案、不过审批门，一律 `low`——这是「它们的处置恒为
  直接执行」，不是漏标。
- **不改 `multi_step` / `low_confidence` 两条既有判据**。它们救的是「模型自己也没把握」
  的情形，与风险正交（见上「判定顺序」）。
- **不改设备 op 的挂起语义**（ADR-010）、**不动 v1 车道**（v1 的确认卡与队列语义另有一套）、
  **不为「主动提议新增分类」这类纯对话动作改动审批面**——提议是模型在回复里问一句，
  真正的创建仍走 `category_create`（新增类，照上表是 `low`，单步高置信下直接执行），
  `DEVICE_EXECUTED_OPS` 与 `HIGH_RISK_OPS` 的判定都不为它改动。
