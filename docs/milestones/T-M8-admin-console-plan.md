# T-M8 运营可观测后台 · 实施推进计划

**建立：** 2026-11-10
**关系：** 本文件是 **M8（运营可观测后台：AI 交互回放台）的执行细则与进度看板**。架构事实源见 [`docs/architecture/admin-console.md`](../architecture/admin-console.md)；决策已由用户 2026-11-10 逐项锁定（架构 §9），实施中不再重议。
**需求来源：** 第二十二轮 REQ-84。两条硬需求：① **审批过程必须进入事件流**；② **支持按用户 ID + 会话关键词 + 时间检索定位会话**。
**用法：** 领取任一子卡先读架构文档对应章节，再读本卡小节。**职责边界是防范围蔓延的关键。**

---

## 一、状态看板

| 卡 | 内容 | 状态 | 完成日 | 提交 | 备注 |
|---|---|---|---|---|---|
| **8-01** | 文档与决策锁定 | ✅ 完成 | 2026-11-10 | `c9c8210` | 24 项决策落盘；架构 §9 + 本文件 + APP-CONTEXT + overview + AGENTS + v4 同步 |
| **8-02** | 审批留档（硬需求 ①） | ✅ 完成 | 2026-11-10 | `7e4c27e` | **服务端**：`approval_request` 同轮落库、`ai_proposal_links`、`/ai/execute` 写 `allowed-once(source=server)`、`POST /ai/approval-result`（四值、幂等、越权 404、忽略与 server 行冗余的 allowed-once）。**客户端四值上报**见 8-02b |
| **8-02b** | 审批四值桥上报（8-02 的客户端部分） | ✅ 完成 | 2026-11-10 | `644c06e` | 桥 **87→88**：`ai.approval_result`（BridgeProtocol / SecurityConfigTest / bridge-types / native-bridge 四处同步）+ Kotlin `submitApprovalResult` + TS `reportApprovalResult` + 四值收敛点接线（cancelled / rejected / unavailable / allowed-once）；旧桥缺席时只少审计、UI 不变 |
| **8-03** | **M8 数据面基线**（全部迁移 + 会话检索，硬需求 ②） | ✅ 完成 | 2026-11-10 | `b2d7e0b` | 迁移 `0024` 一次落六处；`search_text` 四路同寿（写入/删除/保留期/分叉）；`request_id` 进全部写入路径。验证：pytest **1777 passed / 37 skipped**、ruff 全绿、迁移 upgrade/downgrade 离线 SQL 正确；**本机 Docker 未运行，真实 PG 未实测** |
| **8-04** | admin 鉴权与只读 API | ✅ 完成 | 2026-11-10 | `7e4c27e` | `modules/admin/*` 5 文件 + `scripts/create_admin.py`；9 个读端点、独立 JWT（`ADMIN_JWT_SECRET` / `aud=aozora-admin`）、只读事务、默认脱敏与 reveal 审计。验证：`tests/test_m8_admin_api.py` **51 passed**、关联回归 90 passed、ruff 全绿；**本机 Docker 未运行，PG 的 `SET TRANSACTION READ ONLY` 未实测**（SQLite 后端无等价语句，机制由单测钉住）；生产必须设置 `ADMIN_JWT_SECRET`（M8-10） |
| **8-05** | 管理写操作与写审计 | ✅ 完成 | 2026-11-10 | `98dab26` | 4 个写端点（suspend / resume / ai-quota / sessions）+ 写审计同事务；`auth/suspended.py` 守卫覆盖 12 个模块的数据面写路径；**不复用 `FROZEN`**（反向用例：暂停 8 天后删除任务不动数据）；配额覆盖仅 guest。验证：新增 13 条、服务端全量 **1853 passed**、ruff 全绿。残余 R-PROD-70（新增写端点须显式挂守卫） |
| **8-06** | admin 前端壳 + 检索页 | ✅ 完成 | 2026-11-10 | `5c12f8f` | 独立 `admin/` 包（36 文件）：登录、用户检索、全局会话检索、用户详情；React 19 + Vite 8 + Tailwind 4 + TanStack Query/Table；HashRouter、北京时间、客户端游标栈、表格容器内横滚。验证：`npm install` / `tsc` / `vite build` 全过 + mock 浏览器冒烟 9/9 |
| **8-07** | **会话回放页**（核心） | ✅ 完成 | 2026-11-10 | `e853465` | 轮次列表 + 事件时间轴 + `tool_calls` 配对 + 推理折叠 + 结构性事件语义化；审批三态（服务端确认 / 客户端上报 / 未上报）；默认脱敏 + 「查看原文」触发 reveal 审计；保留期空态。路由 `/replay/:conversationId`，会话列表加「回放」入口 |
| **8-08** | 聚合统计与反查页 | ✅ 完成 | 2026-11-10 | `e853465` | 用量聚合（user/model/day 三组 + 合计行 + CSS 条形）与 `request_id` 反查（事件精确命中 / 仅 usage / 404 三态）；**按天日界已改为北京时间**（`queries._day_key`，PG `timezone('Asia/Shanghai', …)`）。**成功率与 P95 后端接口无字段，未展示**（记入已知限制） |
| **8-09** | 导出 | ✅ 完成 | 2026-11-10 | `4086edc` | `POST /admin/export`：3 范围 × 2 内容、流式 NDJSON、keyset 分批（500/批）、上限 100,000 事件 → 422；结束时独立 session 写 `action="export"` 审计（范围/模式/条数/耗时）。前端 `ExportPage` + 会话列表「导出当前筛选」+ 回放页「导出本会话」。验证：新增 21 条（6 组合全绿）、服务端全量 **1876 passed**、ruff 全绿、tsc/build 通过 |
| **8-10** | 部署、PII 与隐私政策 | ✅ 完成 | 2026-11-10 | 待提交 | **已上线 A 机**：`http://116.62.196.255/aozora-api/aozora-admin/`。**零 nginx 变更**（用户约束）——FastAPI `StaticFiles` 挂载 + 复用 `/aozora-api/` 反代；新增审计查询 `GET /admin/audit` + 前端页；隐私政策 §2.5 + `LegalPage` 同步（R-PROD-63 的「麦克风」一并移除）；`ADMIN_JWT_SECRET` 已在服务器 .env 配置；迁移 0024 已在 aozora_test 执行 |
| **8-11** | 收口 | ✅ 完成 | 2026-11-10 | 待提交 | 四板：pytest **1878 passed / 37 skipped**、ruff 全绿、vitest **91 files / 1509 passed**、tsc 0 错、JVM **BUILD SUCCESSFUL**；admin build 通过；**线上真实数据走查**（会话「你好」→ 轮次 → 事件，`seq`/`turn` 与 PG 一致）。**M8 首期十一卡全部交付**（8-12 为 2026-11-12 追加跟进卡） |
| **8-12** | 分步 token 用量观测（REQ-85 追加） | ✅ 完成 | 2026-11-12 | `c815ac8` | 新结构性事件 `usage`：**每次模型调用一条**（Step / 收尾 / 摘要压缩），随步写入（断连也留档）；回放台显示每步输入/输出 token、档位与该步发起的工具调用，工具卡按 `tool_call_id` 标注「本步用量」。轮合计仍以 `quota` 为准；不新增 `ai_usage` 行、无迁移、无桥变更。验证：新增 6 条专项用例（分步 / 收尾 / 失败轮 / 摘要用量 ×2）+ 结构性事件参数化用例随白名单扩面；服务端 **1888 passed / 37 skipped**、ruff 全绿；vitest **1509 passed**；admin tsc/build 通过。残余 R-PROD-72 |

**状态取值：** ⏳ 未开始 / 🔄 进行中 / ✅ 完成 / ⛔ 阻塞

---

## 二、批次与依赖

```
批次1  8-01
          ↓
批次2  8-03（全部迁移一次落）
          ↓
批次3  8-02 ∥ 8-04          ← 不同文件面，可并行
          ↓
批次4  8-05 ∥ 8-06 ∥ 8-08   ← API / 前端 / 统计页，可并行
          ↓
批次5  8-07（依赖 8-06 的壳与 8-02 的事件字段）
          ↓
批次6  8-09（导出入口依赖 8-07）
          ↓
批次7  8-10
          ↓
批次8  8-11
          ↓
批次9  8-12（独立跟进，无前置）
```

| 批次 | 卡 | 可并行 | 说明 |
|---|---|---|---|
| 1 | 8-01 | — | 决策已锁定，本卡只做文档落盘 |
| 2 | 8-03 | — | **全部迁移集中在此卡**：`search_text`、`request_id`、`admin_accounts`、`admin_audit_log`、`users.admin_suspended_at/reason`、`users.ai_daily_quota_override`、`ai_proposal_links` |
| 3 | 8-02、8-04 | ✅ | 8-02 改 `modules/ai/*`；8-04 新建 `modules/admin/*` |
| 4 | 8-05、8-06、8-08 | ✅ | 8-05 改账户校验点；8-06 新建前端包；8-08 加统计端点 |
| 5 | 8-07 | — | 回放页需要前端壳与审批事件 |
| 6 | 8-09 | — | 导出需要回放页的入口 |
| 7 | 8-10 | — | 隐私政策涉及客户端 `LegalPage` |
| 8 | 8-11 | — | 收口 |
| 9 | 8-12 | — | 2026-11-12 追加（REQ-85）；独立跟进卡，无前置，可与其它线并行 |

**为什么把迁移集中到 8-03：** Alembic 是单链（`down_revision`），四个卡各带迁移必然分叉。把「schema 先行」统一到一张卡，后续卡只写代码，并行度最高，且迁移失败只影响一处。

---

## 三、逐卡细则

### 8-01 文档与决策锁定

- **内容：** 架构文档 §9 决策清单、本文件 11 卡、`APP-CONTEXT` M8 章节、`overview` 排序、`v4` 轮次登记。
- **职责边界：** **零代码改动**。
- **验收：** 24 项决策（17 项主决策 + 7 项补充确认）全部落盘且编号可追溯；文档间无矛盾。

### 8-02 审批留档

- **内容：**
  1. `approval_request`：`stream.py` 发 `confirm` 帧时写，载荷 `proposalId / op / risk / confirmReason / step`，`same_turn=True`。
  2. 同事务写 `ai_proposal_links`。
  3. 服务端确认：`/ai/execute` 成功按 `proposal_id` 写 `approval_result(source="server")`。
  4. 客户端上报：新增 `POST /ai/approval-result`，写 `approval_result(source="client")`；幂等键 `(user_id, proposal_id)`。
  5. 客户端：确认卡四值收敛处调用一次上报。
- **职责边界：** 不改 `/ai/execute` 请求体；不改审批策略与 zero-write 语义；不加桥命令（桥计数 87 不变）；不接 `device_request` / `security`。
- **验收：** 四值可落档且 `source` 正确；重复上报不追加行；`conversation_id` 越权被拒；既有审批用例全绿；老客户端不调用时不报错。
- **依赖：** 8-03（表已存在）。

### 8-03 M8 数据面基线（迁移 + 会话检索）

- **内容：**
  1. **一次迁移**落六处 schema：
     - `ai_conversations.search_text text NOT NULL DEFAULT ''`
     - `ai_conversation_events.request_id varchar(64) NULL` + 索引
     - `admin_accounts`
     - `admin_audit_log`
     - `users.admin_suspended_at / admin_suspended_reason`
     - `users.ai_daily_quota_override`
     - `ai_proposal_links`
  2. `search_text` 维护（`append_events` 同事务追加 user/assistant 文本）、上限截断、保留期清理重算。
  3. `request_id` 写入：`TurnWriter` 构造加 `request_id` 参数（`stream.py:713` 调用点同步），历史行留 `NULL`。
  4. 回填脚本：`search_text` 从历史事件聚合。
- **职责边界：** 不改事件表既有列；不引入 `pg_trgm`；`search_text` 不进同步协议。
- **验收：** PG 18 上迁移成功、可回滚；SQLite 测试后端通过；`search_text` 不含 tool/推理内容；**反向验证**：删事件前断言含、删后断言不含；`request_id` 新事件非空、历史为 NULL。
- **依赖：** 8-01。

### 8-04 admin 鉴权与只读 API

- **内容：** `modules/admin/`（`routes` / `dependencies` / `schemas` / `queries`）；独立 JWT（`ADMIN_JWT_SECRET`、`aud=aozora-admin`）；§5.1 全部读端点；`reveal` 参数与脱敏输出；只读事务。
- **职责边界：** 不写业务表；列表不返回正文；不复用 C 端限流器；不改 `modules/*` 既有路由。
- **验收：** 双向 401；只读事务下误写报错；keyset 分页无重复遗漏；`reveal=false` 手机号打码；`reveal=true` 写审计。
- **依赖：** 8-03（表）、8-02（审批字段可选）。

### 8-05 管理写操作与写审计

- **内容：** 暂停/恢复（`admin_suspended_at`）、AI 配额覆盖、强制登出；`admin_audit_log(action=write)` 同事务。
- **硬约束：** **不得复用 `UserStatus.FROZEN`**——`infra/cleanup/account-deletion.py:168-175` 会物理删除 `FROZEN AND frozen_at<=now-7d` 的用户。
- **覆盖点：** `get_current_user`、`_require_active`、sync push 入口逐一审查；暂停期间返回 403 `account_suspended`。
- **职责边界：** 不提供任何改业务数据行的能力；不做角色分级。
- **验收：** 暂停后写操作与同步被拒、数据保留；恢复后一切正常；**用例：暂停 8 天后跑删除任务，用户数据仍在**（反向验证不复用 FROZEN）；配额覆盖对 guest 生效、ACTIVE 不受影响；强制登出后 refresh token 失效。
- **依赖：** 8-04。

### 8-06 admin 前端壳 + 检索页

- **内容：** 独立 `admin/` 包；登录、左菜单、通用筛选栏 + 宽表格 + 分页 + 详情抽屉；用户检索、会话检索、用户详情；时间统一北京时间。
- **职责边界：** 不进 `app/`、不改 Android 构建；不做回放页（8-07）；不做导出（8-09）。
- **验收：** 手机号 → 用户 → 会话全链走通；「用户 + 关键词 + 时间」组合筛选正确；空/加载/失败态齐备。
- **依赖：** 8-04。

### 8-07 会话回放页（核心）

- **内容：** 左侧轮次列表 + 右侧事件时间轴；`tool_calls` 与结果配对；推理折叠；结构性事件着色；审批事件显示来源标签；默认脱敏 + 「查看明文」按钮（触发 reveal 审计）；保留期空态。
- **职责边界：** 只读；不提供导出入口（8-09 统一）；不做跨会话对比。
- **验收：** 真实会话逐轮与 PG 直查一致（`seq`/`turn` 顺序、token）；错误轮显示错误码；审批轮显示「服务端确认 / 客户端上报 / 未上报」；超出保留期显示空态。
- **依赖：** 8-06、8-02。

### 8-08 聚合统计与反查页

- **内容：** token/成本/成功率按 user、model、day 聚合；P95 轮耗时；`request_id` 精确反查。
- **职责边界：** 只读；不引入 Prometheus。
- **验收：** 数字与 PG 同口径一致；`request_id` 反查在并发下命中正确（事件表已有列，不再近似）。
- **依赖：** 8-04。

### 8-09 导出

- **内容：** `POST /admin/export`；范围（筛选结果 / 全库 / 单用户）与内容（明文 / 打码）自由组合；流式 JSONL；每次写 `action=export` 审计（含范围、内容模式、条数、耗时）；前端入口放在回放页与列表页。
- **职责边界：** 单次上限 100,000 事件，超出提示分批；不提供定时导出。
- **验收：** 六种组合各走通；导出内容与页面一致；审计行字段完整；超限提示正确；无二次确认（按用户决策）。
- **依赖：** 8-07（入口）、8-04（API）。

### 8-10 部署、PII 与隐私政策

- **内容：** nginx 公网子域 + TLS + 独立限流；IP 白名单（部署侧可选）；脱敏规则回归；`admin_audit_log` 查询页；隐私政策新增条款 + 客户端 `LegalPage` 内联副本同步（R-PROD-63）；`POLICY_VERSION` 不动。
- **职责边界：** 不改用户端 AI 行为；不动 `UserConsent` 版本。
- **验收：** `nginx -t` 通过；未登录/越权 401；脱敏规则覆盖手机号/邮箱/身份证/银行卡；隐私条款与客户端文案一致。
- **依赖：** 8-07、8-09。

### 8-11 收口

- **内容：** 四板全量 + 真实数据走查（检索 → 定位 → 回放 → 归因）+ 文档回填 + 风险册登记。
- **验收：** 四板全绿；走查覆盖两条硬需求；风险册新增条目编号连续（起点 R-PROD-69）。

### 8-12 分步 token 用量观测（REQ-85，2026-11-12 追加）

- **背景：** 回放台目前只有每轮一条 `quota`（合计）。每步用量在 loop 里其实拿得到（`AgentObserver.step_finished`，`agent.py:1019-1026`），但只被累加进轮合计（`agent.py:1349-1350`）；撞边界的收尾调用只进合计、无法归属；摘要压缩那次模型调用的用量被直接丢弃（`conversation.py:650-673`）。用户要求把观测细化到「每次模型推理 / 每次工具调用步骤」的输入输出 token。
- **内容：**
  1. **新结构性事件 `usage`**（`EVENT_USAGE`，加入 `STRUCTURAL_EVENT_TYPES` / `EVENT_TYPES`），**每次模型调用一条**：
     `{kind: "step" | "wrap_up" | "summary", step, level, provider, model, prompt_tokens, completion_tokens, tokens, tool_calls: [{id, name}]}`。
     - `step`：loop 的 Step 序号（1 起）；`summary` 为 `null`。
     - `level`：该次调用的推理档（`fast` / `medium` / `deep`）；`wrap_up` 恒 `deep`。
     - `tool_calls`：该次调用产出的工具调用，`id` 与 assistant 事件 `data.tool_calls[].id` **同源**（`_assistant_tool_calls` 定格之后取），管理端可直接配对；`wrap_up` / `summary` 为空数组。
  2. **写点：**
     - Step：`AgentLoop.step()` 在模型调用返回、id 定格之后，**先写 `usage` 再写该步的 assistant 落库批**——`seq` 顺序因此是「用量 → 它产出的消息」，且随批提交，断连/失败时已完成步骤的用量已留档。
     - `_wrap_up`：收尾调用成功返回后写一条（`kind="wrap_up"`）。
     - `summarize_overflow`：`_generate_summary` 改为返回用量；provider 调用成功即写 `kind="summary"`，**摘要被拒也写**（钱已经花掉），`request_id` 一并带上。
     - `MessageSink` 增补 `structural()` 能力（`TurnWriter.structural` 已存在）；loop 对 sink 按「有该方法才写」调用，既有测试替身不改也能跑。
     - 既有按「事件序列」断言的用例要同步纳入 `usage` 行；`_paired_prefix_len` / `history()` 等按 Surface 白名单工作的路径**不受影响**（结构性事件不改变配对与上下文判据）。
  3. **口径不动：** `quota` 事件与 `ai_usage` 行一字不改，仍是每轮一条合计。`usage` 只做观测，**不参与配额**——guest 日配额按 `ai_usage` **行数**计数（`service.py:227-252`），新增行会改变配额语义。
  4. **白名单同步点：** `conversation.py` 的三个常量 → `ai-agent-architecture.md` §6.2 事件表 → `admin/src/components/EventTimeline.tsx` 的 `TYPE_META`。`type` 列 `varchar(32)` 足够，**无迁移**。
  5. **管理端：**
     - 回放页时间轴把 `usage` 渲染为紧凑一行（Step / 档位 / 输入 / 输出 / 触发的工具），并按 `tool_calls[].id` 在工具卡上标注「本步用量」。
     - 轮次合计、`/admin/ai-usage/summary` 聚合、`request_id` 反查口径**均不变**；反查页的事件类型列表自动多出 `usage`。
     - 载荷不含正文，`reveal` 脱敏无新增面。
- **职责边界：**
  - **不新增 `ai_usage` 行**、不改 `quota` / `replace` 载荷形状、不改 `check_daily_quota`。
  - 不改 `/admin` API 形状（`/events` 原样透传）；不做图表/告警；**不加桥命令、客户端零改动**（结构性事件不在重建白名单内）。
  - 不采集 prompt cache hit/miss（provider 未解析）、不拆分 reasoning tokens、不做输入/输出差异定价（`ai_price_per_1m_tokens` 是混合价）。
  - 不覆盖快路径（`/ai/interpret` 等非会话车道没有事件日志）。
  - 摘要压缩用量进事件但**不进** `ai_usage` / 成本聚合——已知限制，交付时登记风险册（R-PROD-72 起）。
- **验收：**
  - 一轮 2 Step + 1 次工具调用：事件序列为 `user` → `usage(step1, 含工具 id)` → `assistant+tool` → `usage(step2)` → `assistant` → `quota`；不变量 `quota.tokens == Σ(kind=step).tokens + Σ(kind=wrap_up).tokens`。
  - 撞硬边界轮：存在一条 `kind="wrap_up"` 且计入合计；失败轮：已完成步骤的 `usage` 在库，`quota` 合计与已写 `usage` 之和相等（`_record_failure` 的 observer 口径）。
  - 摘要轮：`replace` 照旧 + `usage(kind="summary")` 一条；**反向验证** `ai_usage` 行数不因压缩增加。
  - 客户端：历史重建遇到 `usage` 不产消息、不抛错（vitest）；`usage` 不出现在 `history()`（结构性不进上下文）。
  - 管理端：回放页与 PG 直查一致；旧会话（无 `usage`）不回归；用量聚合页数字不变。
- **依赖：** 无（8-02 / 8-03 / 8-07 / 8-08 均已收口），可独立开工。
- **交付（2026-11-12）：** 服务端 `agent.py`（Step / 收尾写点）、`conversation.py`（事件常量 + 摘要用量）、`stream.py`（`request_id` 透传）；管理端 `EventTimeline.tsx`（`usage` 行 + 工具卡标注）；客户端仅补重建用例。**反向验证已做**：把 `_emit_structural` 改成空操作后，4 条分步用例全部变红。

---

## 四、领卡通用红线

1. **全部迁移只在 8-03**；后续卡不得新增 revision，缺列先改 8-03。
2. **不得复用 `UserStatus.FROZEN`**；暂停用 `admin_suspended_at`。
3. **不改业务数据行**；写能力仅限账户三项。
4. **列表不返回事件正文**；正文只在 `/events`。
5. **审批四值不合并**；区分服务端确认与客户端上报，未上报不得推断为拒绝。
6. **`search_text` 与事件同寿**；清理必须重算。
7. **不碰 `app/` 的 Android 构建**；8-02 的 TS 改动与 8-10 的 `LegalPage` 文案除外。
8. **分步用量只进事件日志**（8-12）：不得新增 `ai_usage` 行、不得改变 `quota` 的轮合计口径——配额按行计数，改口径等于改产品行为。
