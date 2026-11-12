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
| **8-12** | 分步 token 用量观测（REQ-85 追加） | ✅ 完成 | 2026-11-12 | `c815ac8` |
| **8-13** | 模型输入快照（REQ-86，用户反馈追加） | ✅ 完成 | 2026-11-12 | `fe8cfc6` | `usage` 事件补 `call` + `request`：system（逐调用）/ 增量 `append` / 工具名与 `tools_def`（工具集变化才存），回放页「模型输入」面板可展开逐步查看。验证：服务端 **1891 passed / 37 skipped**、ruff 全绿；admin tsc/build 通过；**已部署测试实例**（A 机 `fe8cfc6`），真实会话 PG 取证：call1 system 969 字符 / 8 工具 / 含 tools_def，call2 base_call=1 / append 2 条 / 无重复 schema。残余 R-PROD-73 | 新结构性事件 `usage`：**每次模型调用一条**（Step / 收尾 / 摘要压缩），随步写入（断连也留档）；回放台显示每步输入/输出 token、档位与该步发起的工具调用，工具卡按 `tool_call_id` 标注「本步用量」。轮合计仍以 `quota` 为准；不新增 `ai_usage` 行、无迁移、无桥变更。验证：新增 6 条专项用例（分步 / 收尾 / 失败轮 / 摘要用量 ×2）+ 结构性事件参数化用例随白名单扩面；服务端 **1888 passed / 37 skipped**、ruff 全绿；vitest **1509 passed**；admin tsc/build 通过。残余 R-PROD-72 |

| **8-14** | prompt cache 命中观测（REQ-87，成本分析追加） | ✅ 完成 | 2026-11-12 | `61eb52b` | provider 解析 `prompt_cache_hit_tokens` → `usage` 事件 `cache_hit_tokens` → 回放台展示；服务端全量 **1906 passed / 37 skipped**、ruff 全绿、admin tsc/build 通过 |
| **8-15** | 收尾步不带工具（REQ-88，成本分析追加） | ✅ 完成 | 2026-11-12 | `19aff44` | 工具调用全部终态后，下一步只剩复述结果：请求 `tools=[]` + 收尾指令 + FAST 档，省整套工具 schema（实测 ~4.1k tokens/步） |
| **8-16** | 路由增强：金额形态 + sticky 兜底 + 补词（REQ-89，成本分析追加） | ✅ 完成 | 2026-11-12 | `15153bf` | 词表未命中时先判「商品名+金额」形态（→ transaction 单域），再考虑最近调用域；补「密码」；offline dry-run 99 条：兜底 5 条（SEC-NEG-01 移出、NEG-04 指代追问按预期兜底） |
| **8-17** | 记账路由全面覆盖（REQ-90，用户要求追加） | ✅ 完成 | 2026-11-12 | `9b0f8ba` | 记账意图判定器（L1 动作/祈使 + L2 金额形态含中文数字与货币符号 + L3 品类+金额）+ `parse_cn_amount`；语料 +10 条（109 条 dry-run 兜底不增）；**已部署 A 机测试实例并真实验证**：路由 transaction 单域、两步、收尾带 0 工具，「瑞幸九块九」整轮 **4,582 tokens**（原同形态 12,389，降 63%），cache_hit 1,408/384，交易落库 |

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
          ↓
批次10 8-13（依赖 8-12 的 usage 事件与回放页）
          ↓
批次11 8-14（依赖 8-12/8-13 的 usage 事件与回放页；只加观测字段）
          ↓
批次12 8-15（依赖 8-12；改 agent loop 每步请求形态）
          ↓
批次13 8-16（依赖 8-14 的观测；改 routing + agent 注入面）
          ↓
批次14 8-17（记账意图判定器 + 语料扩充；交付即部署 A 机测试实例）
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
| 10 | 8-13 | — | 2026-11-12 用户反馈追加（REQ-86）；依赖 8-12 的 `usage` 事件；交付即部署测试实例 |
| 11 | 8-14 | ✅ | 2026-11-12 成本分析追加（REQ-87）；依赖 8-12/8-13 的事件与回放页 |
| 12 | 8-15 | ✅ | 2026-11-12 成本分析追加（REQ-88）；依赖 8-12；与 8-14/8-16 文件面无冲突 |
| 13 | 8-16 | ✅ | 2026-11-12 成本分析追加（REQ-89）；依赖 8-14 的观测数据 |
| 14 | 8-17 | ✅ | 2026-11-12 用户要求追加（REQ-90）；把 8-16 的形态规则升级为记账意图判定器，交付即部署 |

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

### 8-13 模型输入快照（REQ-86，2026-11-12 用户反馈追加）

- **背景：** 8-12 只给了「每次调用多少 token」，用户反馈仍是黑盒——看不到**每次进模型的输入 prompt**（system / 历史 / 本轮消息 / 工具定义），无法判断 token 花在哪。另：8-12 尚未部署，测试实例停在 `46cf6c9`，本次一并部署。
- **内容：**
  1. `usage` 事件增加两个字段：
     - `call`：轮内第几次模型调用（1 起）——链式重建的引用键。
     - `request`：该次调用的输入快照，**增量编码**（不重复存历史）：
       `{base_call: int|null, system: str, append: [消息字典…], tools: [名称…], tools_chars: int, tools_def?: [完整 schema…]}`。
  2. 语义：
     - `system`：本次实际发送的系统提示（含当日日期、对象名单、域知识——`_load_skills` 在步与步之间会改写它，因此**按调用逐条记录**）。
     - `append`：自上一次调用以来**新增**的消息（首次调用＝历史 + 本轮用户输入；后续＝上一步的 assistant + tool 结果；收尾＝收尾指令）。
     - `base_call`：上一次调用的 `call`；`null` 表示从零开始。管理端按 `(turn, call)` 链式拼出**完整且精确**的 messages 数组。
     - `tools` / `tools_chars`：本次注入的工具名与 schema 序列化字符数；`tools_def` 仅在该次调用的工具集与本轮上一次**不同**时存（首步 / 换集），避免每步重复存 10–35 KB。
     - 摘要压缩：独立全量 `{base_call: null, system, append: [渲染后的 user 输入], tools: []}`。
  3. 管理端回放页：
     - `usage` 行常显「call N · 工具 M 个（X 字符）」；新增「模型输入」折叠面板：system（单独折叠）+ 逐条消息（role 徽标 + 内容）+ 工具定义（有 `tools_def` 时展开，否则注明「同 call N」）。
     - 消息内容长文本可滚动；`reveal=false` 时递归打码（复用 `mask_payload`），查看原文走既有审计。
     - 旧事件没有 `request` 字段 → 面板不显示，页面不回归。
  4. 客户端零改动（`usage` 仍不在重建白名单内）；无迁移（`data` 为 JSONB）。
- **职责边界：**
  - **不存 provider 原始 HTTP 请求体**（头、密钥、流参数、`stream_options` 等）——只存模型语义输入（messages + 工具定义）。
  - 不改 `quota` / `ai_usage` 口径；不改配额与成本聚合（压缩用量仍只进事件，R-PROD-72）。
  - 不做逐 token 的 prompt 回放或 diff 视图（本卡只给完整输入与增量条数）。
  - 不覆盖快路径与图片解析（非会话车道）。
- **验收：**
  - **精确性用例**：一轮 2 Step + 工具调用，管理端重建的 messages + tools **逐字节等于** provider 实际收到的载荷（脚本化 provider 直接对比）。
  - system 在步间变化（域知识加载）时，后一步记录的是新 system；`append` 只含新增消息。
  - `wrap_up` 的 `request.append` 是收尾指令；`kind=summary` 的 `request` 独立全量。
  - `tools_def` 只在本轮首次调用出现一次；后续调用可指回首步。
  - `reveal=false` 时 prompt 里手机号被 `mask_payload` 打码；`reveal=true` 写 reveal 审计。
  - 旧会话（无 `request`）页面不回归；服务端全量 + vitest + admin tsc/build 通过。
- **存储口径：** 每轮新增 ≈ 首次调用的完整 prompt + 各步增量（O(轮内消息) 而非 O(步数 × 历史)）；事件随保留期（30 天）整轮清理。风险册登记 **R-PROD-73**（事件体积/导出体积放大，尤其 `tools_def` 与长 system）。
- **依赖：** 8-12（已收口）。**交付即部署**：A 机 `git pull` → admin 构建产物同步 → `a-deploy.sh test` → 线上真实会话走查。
- **交付（2026-11-12）：** 已部署 A 机测试实例（`fe8cfc6`；admin 产物 `index-CrhTa_07.js`）。真实会话取证：两次模型调用的 `usage` 分别为 `call=1/base=null/append=1/tools_def=是` 与 `call=2/base=1/append=2/tools_def=否`，system 969 字符与代码量级一致。**反向验证已做**：`append` 置空后 3 条快照用例全红。

### 8-14 prompt cache 命中观测（REQ-87，2026-11-12 成本分析追加）

- **背景：** 用户对「喜茶24」一轮（12,389 tokens）做成本归因，发现 69% 花在工具 schema 上。但架构 §成本影响记录：`deepseek-flash` 输入 cache hit 单价 $0.003/1M、miss $0.15/1M（差 50 倍），agent loop 每轮重发历史与工具定义**正是 Design for cache**。然而 `provider.py` 只解析 `prompt_tokens` / `completion_tokens`，把 DeepSeek 返回的 `prompt_cache_hit_tokens` 丢弃——账面 token 与实际计费的差无法被观测，成本决策全凭猜测。8-12 曾明确将此项列为职责边界外（「不采集 prompt cache hit/miss（provider 未解析）」），本卡补上。
- **内容：**
  1. `ProviderResult` 增加 `cached_prompt_tokens: int = 0`（默认 0，快路径与非 DeepSeek provider 的构造不受影响）。
  2. `DeepSeekProvider` 两处解析读取 `usage.prompt_cache_hit_tokens`（非流式 `_result_from_body` 与流式 `_StreamAccumulator.feed`）；字段缺失/非整数按 0。**只认 hit，不存 miss**（miss = prompt − hit，可推导）。
  3. `agent._usage_payload` 增加 `cache_hit_tokens`（取 `result.cached_prompt_tokens`）。`quota` 事件与 `ai_usage` **一字不改**（配额与混合价成本聚合口径不变）。
  4. 管理端回放页 `UsageBody`：`cache_hit_tokens > 0` 时在输入 token 旁显示「缓存命中 N（M%）」；旧事件无字段不显示。
  5. **观测记录**：在同轮两步之间（`_load_skills` 改写 system）与跨轮之间（`compact`/`bare` 档跳变、system 内日期变化）比较命中率，把结论写进交付记录——本卡只观测，**不据此改压缩档或 system 结构**。
- **职责边界：**
  - 不改前缀结构（system 顺序、工具集、large payload 形态）——观测先行。
  - 不改 `quota` / `ai_usage` / 成本聚合（`AI_PRICE_PER_1M_TOKENS` 仍是混合价）。
  - 不覆盖快路径与非会话车道。
- **验收：**
  - provider 用例：usage 带 `prompt_cache_hit_tokens` → `cached_prompt_tokens` 正确；缺失 → 0；非整数 → 0；流式与非流式两条路径都覆盖。
  - `usage` 事件载荷含 `cache_hit_tokens`，且**现有 8-12/8-13 用例不回归**（字段为新增，旧断言不受影响；参数化断言若枚举键需同步）。
  - 回放台展示正确、旧会话不回归；admin tsc/build 通过。
  - 真实链路上跑一轮两步会话，PG 里 `usage` 事件能看到 `cache_hit_tokens`（部署测试实例后取证；本机无法直连 DeepSeek 时以脚本化 provider 用例为准并注明）。
- **依赖：** 8-12 / 8-13（已收口）。
- **交付（2026-11-12）：** `provider.py`（三处解析：非流式 `_result_from_body`、流式尾块；字段排末位保持位置参数构造兼容）、`agent.py`（usage 载荷）、`EventTimeline.tsx`（缓存命中徽标）。验证：provider 65 passed、agent_stream 用量用例 13 passed、服务端全量 **1906 passed / 37 skipped**、ruff 全绿、admin tsc/build 通过。**反向验证已做**（载荷值置 0 → 用例红）。**待观测**：真实会话的命中率与「system 步间改写 / compact-bare 档跳变是否打断前缀」——部署测试实例后用 `cache_hit_tokens` 取证，再决定是否调整前缀结构（本卡不做）。

### 8-15 收尾步不带工具（REQ-88，2026-11-12 成本分析追加）

- **背景：** 「喜茶24」一轮的**第二次模型调用**（`deepseek-flash`，2 Step）只为说一句「记好了」，却完整重发了 24 个工具的 schema（11,710 字符 / ≈4,100 tokens），且因为 `_level_for_step`（`agent.py:1359`）在 `steps >= 1` 时强制 `DEEP` 档而启用 thinking。工具执行完、下一步只需复述结果时，工具定义是纯浪费。
- **内容：**
  1. `AgentLoop` 记录上一步工具结果；`step()` 在**发请求前**判定「收尾步」（`_should_finalize`）：
     - `self._steps >= 1`（不是第一步）；
     - 上一步**有**工具调用，且**全部**结果为终态（`executed` / `proposal_pending` / 只读成功；**不含** `error`）；
     - `self._queued` 为空（没有中途输入——用户又说话时必须保留工具）。
  2. 收尾步请求形态：
     - `tools=[]`（provider 不下发 `tools` 字段）；`_advertised` 同步为空集（模型若仍返回工具名会被隔离边界拒绝，不执行）；
     - 追加一条**收尾指令**（`_FINALIZE_INSTRUCTION`，语义：「工具调用已完成，用一句话告诉用户结果，不要再调工具」）。指令**只进本次请求的 messages**，不落库（同 `_wrap_up` 的既有做法）；
     - 推理档用 `FAST`（只复述结果，不需要 thinking）。
  3. `usage` 事件的 `request.tools` 自然为 `[]`，回放台可据此显示「收尾（不带工具）」；`kind` 仍为 `step`（不新增枚举，避免事件白名单/前端映射扩散）。
  4. 客户端零改动、无迁移、无桥变更。
- **职责边界：**
  - 不改 `_wrap_up`（撞硬边界路径）与其 `DEEP` 档。
  - 不改 `run()` 的注入面（`_inject`）——只影响每步请求传给 provider 的 `tools` 参数。
  - 不改变「模型自主决定不再调工具」的既有收尾路径（那是 `if not calls` 分支，保持原样）。
- **验收：**
  - 用例：单工具 `executed` → 第二次请求 `tools == []`、档位 `fast`；`usage` 快照 `tools == []`。
  - 用例：上一步含 `error` → 第二步仍带工具；上一步 `proposal_pending` → 收尾（说「等你确认」）。
  - 用例：`submit()` 中途输入后 → 下一步仍带工具（输入不能被丢掉）。
  - **反向验证**：把 `_should_finalize` 改为恒 `False`，上述用例变红。
  - 服务端全量 pytest + ruff 通过。
- **交付（2026-11-12）：** `agent.py`（`_should_finalize` + `_FINALIZE_INSTRUCTION` + `_request_payload` 的 `transient` 参数——收尾指令只进本次请求、不推进快照游标）。行为变更同步了三处既有断言（第二步从 deep/带工具改为 fast/不带工具；`test_empty_input_is_ignored` 排除收尾指令）。验证：agent+stream 166 passed、AI 子集 1296 passed、服务端全量 **1906 passed / 37 skipped**、ruff 全绿。**反向验证已做**：判据恒 `False` → 成功路径红；终态恒 `True` → error 保留工具用例红。

### 8-16 路由增强：金额形态 + sticky 兜底 + 补词（REQ-89，2026-11-12 成本分析追加）

- **背景：** 「喜茶24」未命中任何词表信号 → 兜底四域 21 工具（≈4,100 tokens/步）。对 96 条语料的实测：词表覆盖 89/96，剩余缺口分三类——①**纯名词+金额**短句（「喜茶24」，词表无商品名、检索同样救不了）；②**指代性追问**（「那改成 6000 吧」）；③纯补词缺口（「密码」不在 security 信号里）。三项都是确定性问题，**不引入 embedding / 工具检索**（结论与依据见本轮分析，替代方案在本卡「非目标」）。
- **内容：**
  1. **金额形态规则**（`routing.py`）：词表未命中时，若输入匹配「≤12 个非数字字符 + 金额数字 +（元/块/块钱）?」且**不含**疑问词/问号，返回 `domains=(transaction,)`、`signals=("<金额短句>",)`、`matched=True`。用于把「喜茶24」「瑞幸9.9」「24」这类极简记账收窄到 transaction 单域（7 个工具）。
  2. **sticky 兜底**（`agent.run()`）：`matched=False` 且形态规则也未命中时，从 `history` 最近 **4 条**工具结果消息的 `name` 反查注册表域；非空则注入 `recent_domains ∪ {transaction}`（记账是核心能力，指代追问多为改账），否则维持 `FALLBACK_DOMAINS`。路由决策的 `routed` 帧仍上报**词表原始判定**（`domains`/`matched`），注入面用 sticky 结果——与现有「事件报原始决策、注入用兜底面」的结构一致。
  3. **补词**：`密码` → `security`（「帮我把密码改一下」当前 matched=False）。
  4. **语料**：`scripts/ai_tool_matrix/corpus.json` 增加「喜茶24」「瑞幸9.9」「那改成 6000 吧」三条；`report.json` 重跑。
- **非目标：** 不做工具检索（BM25/向量）、不做 embedding 分类器、不做渐进式披露——依据：工具描述不含商品名，文本检索对「喜茶24」同样无效；56 工具 / 16 域规模下域先验已足够，检索的复杂度不划算（触发条件见交付记录）。
- **职责边界：**
  - 不改 `FALLBACK_DOMAINS` 常量本身；不改域注册表与工具定义。
  - 形态规则只做**收窄**（matched=False → transaction），不改变已命中输入的域选择；sticky 只在兜底时生效。
  - sticky 只读「最近工具结果」，不读用户输入文本，保持确定性、可离线测。
- **验收：**
  - 离线用例：「喜茶24」「瑞幸9.9」「24」→ transaction 单域；「你好呀」「帮我订机票」→ 兜底；「预算里包含房租吗」→ 词表命中不变；含问号/疑问词的金额句不命中形态。
  - sticky 用例：history 含 `transaction_add` 工具结果 + 本轮「那改成 6000 吧」→ 注入 transaction（而非四域）；history 为空 → 兜底页不变。
  - 「帮我把密码改一下」→ security 域。
  - `probe_tool_selection.py --routing` 全量语料回归：fallback 数下降且**无新增漏注入**（`test_every_domain_has_a_signal` 仍绿）。
  - 服务端全量 pytest + ruff 通过；语料报告同步。
- **交付（2026-11-12）：** `routing.py`（`AMOUNT_FORM_SIGNAL` + `_matches_amount_form` + 补词「密码」；顺带修正两处「兜底全量」的过期注释）、`agent.py`（`_recent_tool_domains` + `run()` 的 sticky 分支）、语料 `TX-ADD-06/07` + `NEG-04`。**离线 dry-run 实测**：99 条用例，漏注入 2 条（TX-ADD-04 / BUD-NEG-01，既有边界），兜底 5 条（SEC-NEG-01 移出、NEG-04 按预期）；TX-ADD-06/07 均命中 `transaction` 单域（`signals=["<金额短句>"]`）。验证：routing 45 passed、服务端全量 **1906 passed / 37 skipped**、ruff 全绿。**反向验证已做**：形态判定禁用 → 4 条红；sticky 置空 → 指代用例红。**残余**：真实用户商品名的长尾仍靠形态规则兜住（不是语义识别），「中文数字金额」（「喜茶二十四」）不命中形态，退回四域兜底——登记风险册见下。

### 8-17 记账路由全面覆盖（REQ-90，2026-11-12 用户要求追加）

- **背景：** 8-16 的金额形态规则只覆盖「阿拉伯数字」，且动作/品类仍靠零散补词——用户明确要求「记账的路由描述需要兼容性更强，不能只是简单新增少量，需要覆盖全面」。口语记账的表达多样性在**形式**而非关键词：中文数字（「喜茶二十四」「吃饭三十」）、货币符号（「¥30」「30r」）、动作变体（「付了」「下单」「AA」「到账」）、品类名词（「奶茶」「外卖」「地铁」）词表都够不着，一律退回四域兜底（≈4,100 tokens/步）。
- **内容：**
  1. **记账意图判定器**（`routing.py`，`_looks_like_bookkeeping`）：只在**词表未命中**时接管（词表优先，任何已有信号的输入行为不变），三层判定任一命中即收窄到 transaction 单域：
     - **L1 动作/祈使**：`付了/支付/消费/下单/点了/请客/AA/充了/交了/续费/到账/收到/退款/入账/帮我记/记一下/记个账…`；
     - **L2 金额形态**：整句形如「≤12 字符前缀 + 金额」，金额支持**阿拉伯数字、中文数字、货币符号/单位**（`¥/￥/rmb/r`、`元/块/毛/角/分`）；
     - **L3 品类 + 条件**：命中消费品类名词（奶茶/咖啡/外卖/早餐/午饭/晚饭/夜宵/零食/水果/超市/地铁/公交/高铁/机票/酒店/房租/水电/话费/会员/购物/衣服/鞋/化妆品/理发/电影/游戏/挂号/药/宠物/学费/书…）**且**句中另有金额或动作词。
  2. **中文数字解析**（`_parse_cn_amount`）：十/十五/三十/八十八/一百五/两百五十/两千五/一千二 等常见口语形态（省略「十」的读法按口语惯例：`一百五`=150、`两千五`=2500）。
  3. **排除项**：`删除/删掉/清空/导出/备份/密码/注销` 出现时不进入判定器（这些有别的域信号，词表优先；此处是双保险）。
  4. **词表作为数据**：三层词表以常量分组定义并各配注释（不是散装 if），便于后续增补与用例守护。
  5. **语料扩充**：`corpus.json` 新增 20+ 条记账变体（中文数字、货币符号、收入、支付方式、品类、祈使、时间前缀），断言全部收窄到 transaction；词表未命中之外的负例（闲聊、能力外请求、指代删除）保持兜底。
- **职责边界：**
  - 只在词表未命中时接管；不改 `FALLBACK_DOMAINS`、不改 sticky 兜底、不改域注册表与工具定义。
  - 不引入模型 / embedding / 检索；判定全部是纯函数、可离线测。
  - 不改客户端、不改桥、无迁移。
- **验收：**
  - 参数化用例：20+ 条记账变体全部返回 `domains == (transaction,)`、`matched=True`；「你好呀」「帮我订机票」仍 `matched=False`；「帮我把密码改一下」仍是 security；「预算里包含房租吗」仍命中 budget（词表优先）。
  - 中文数字解析单测：`十/十五/三十/八十八/一百五/两百五十/两千五/一千二/两百` 各自数值正确。
  - 语料 dry-run：漏注入不增加、兜底条目不增加（新增条目全部命中）。
  - 服务端全量 pytest + ruff 通过；**反向验证**：判定器禁用后新用例变红。
- **依赖：** 8-16（已收口）。**交付即部署**：A 机 `git pull` → `admin` 构建 → `a-deploy.sh test` → 公网验证一条真实会话，用户可直接在 app 测试并在运营后台看到新数据。
- **交付（2026-11-12）：** `routing.py`（`_looks_like_bookkeeping` 三层判定 + `parse_cn_amount` + 排除项；「结账」刻意不收——「我习惯月底结账」指定时记账）、语料 `TX-ADD-08…17`。验证：routing 84 passed、服务端全量 **1945 passed / 37 skipped**、ruff 全绿；**反向验证已做**（判定器禁用 → 23 条红）；dry-run 109 条：漏注入 2 条不变、兜底 5 条不增。
- **部署与真实取证（2026-11-12，A 机测试实例）：** `9b0f8ba` 已上线（中转裸仓同步 → `git pull` → admin `npm ci + build` → `a-deploy.sh test`，`/health/ready` 通过，admin 页 200）。
  - 「喜茶二十四」：routing `domains=["transaction"], matched=true`；2 Step；Step 2 为 `fast` 且 `tools=[]`。
  - 「瑞幸九块九」（v2 车道）：Step 1 prompt **3,668** / tools 10 个（8,184 字符）/ cache_hit **1,408**（38%）；Step 2 prompt **782** / tools `[]` / cache_hit **384**（49%）。整轮 **4,582 tokens**——同形态输入原为 12,389（工具 24 个 + 收尾全量重发），**降 63%**。交易落库 990 分（`source=ai`）。
  - **缓存观察（8-14 的首份真实数据）**：system 两步间未变（md5 相同），Step 1 的 38% 命中来自跨会话公共前缀；Step 2 只命中 384——**收尾步去掉 `tools` 段后，其后的消息位置全部前移，工具段之后的缓存前缀作废**。这是 8-15 的已知代价：省下 8,184 字符 schema 的计费，丢掉工具段之后的缓存折扣，净收益仍为正（见上）。
- **真机缺陷与修复（M8-17b，2026-11-12）：** 用户真机测「支付宝到账500」→ 模型调用 `transaction_add`，被隔离边界以 `tool_unavailable` 拒绝（「这个工具本轮不可用」）。**根因**：支付方式词「支付宝」命中 account 域后，`route_domains` 直接返回单域——记账工具被一并收走；判定器只在 `matched=False` 时接管，根本没机会。**记账意图与数据域是并集，不是互斥**。修法三处：
  1. 词表命中后**再跑一次记账判定**，命中则把 transaction 并入已选域；
  2. 动作词移除「支付」——它是「支付宝」的子串（「支付宝里还有多少钱」会误并入记账域）；
  3. 整句形态的前缀由 12 收紧到 **6 字符**——「给餐饮设个月度预算 1500」这类长句是别的域的操作，不该并入（既有注入面用例随即回归绿）。
  回归用例 7 条（4 条记账并入 + 3 条纯查询反例）；服务端全量 **1952 passed / 37 skipped**；反向验证（并入分支禁用 → 4 条红）。**残余**：存钱/转账类表达（「我存了500块到支付宝」）仍可能收窄到 account 单域，归 R-PROD-74 覆盖债。
  **部署复测（`70fb901`）：** 同句「支付宝到账500」→ routing `["transaction","account"]`，Step 1 调 `transaction_add` 成功、Step 2 `fast` 收尾（`tools=[]`），落库 income 50,000 分（`source=ai`），回复「已经记好了：今天支付宝收入 500 元，记在「收转账」分类下」。工具不可用不再出现。

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
