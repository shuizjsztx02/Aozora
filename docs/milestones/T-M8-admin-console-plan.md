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
| **8-02** | 审批留档（硬需求 ①） | ⛔ 阻塞（仅客户端部分） | | `7e4c27e` | **服务端全交付**：`approval_request` 同轮落库、`ai_proposal_links`、`/ai/execute` 写 `allowed-once(source=server)`、`POST /ai/approval-result` 接收 client 四值（幂等、越权 404）。**客户端上报阻塞**：WebView 无 base URL / token（全在 Kotlin），`fetch` 不可达；需桥命令 `ai.approval_result`（87→88），卡面禁止 → 另立 **8-02b**，待用户决策 |
| **8-03** | **M8 数据面基线**（全部迁移 + 会话检索，硬需求 ②） | ✅ 完成 | 2026-11-10 | `b2d7e0b` | 迁移 `0024` 一次落六处；`search_text` 四路同寿（写入/删除/保留期/分叉）；`request_id` 进全部写入路径。验证：pytest **1777 passed / 37 skipped**、ruff 全绿、迁移 upgrade/downgrade 离线 SQL 正确；**本机 Docker 未运行，真实 PG 未实测** |
| **8-04** | admin 鉴权与只读 API | ✅ 完成 | 2026-11-10 | `7e4c27e` | `modules/admin/*` 5 文件 + `scripts/create_admin.py`；9 个读端点、独立 JWT（`ADMIN_JWT_SECRET` / `aud=aozora-admin`）、只读事务、默认脱敏与 reveal 审计。验证：`tests/test_m8_admin_api.py` **51 passed**、关联回归 90 passed、ruff 全绿；**本机 Docker 未运行，PG 的 `SET TRANSACTION READ ONLY` 未实测**（SQLite 后端无等价语句，机制由单测钉住）；生产必须设置 `ADMIN_JWT_SECRET`（M8-10） |
| **8-05** | 管理写操作与写审计 | ⏳ 未开始 | | | 冻结/解冻、配额、强制登出；**不得复用 `FROZEN`** |
| **8-06** | admin 前端壳 + 检索页 | ⏳ 未开始 | | | 独立 `admin/` 包 |
| **8-07** | **会话回放页**（核心） | ⏳ 未开始 | | | 轮次 + 时间轴 + 配对渲染 + reveal |
| **8-08** | 聚合统计与反查页 | ⏳ 未开始 | | | token / 成本 / `request_id` |
| **8-09** | 导出 | ⏳ 未开始 | | | 3 范围 × 2 内容 + 导出审计 |
| **8-10** | 部署、PII 与隐私政策 | ⏳ 未开始 | | | nginx + 脱敏 + LegalPage 同步 |
| **8-11** | 收口 | ⏳ 未开始 | | | 四板 + 真实数据走查 |

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

---

## 四、领卡通用红线

1. **全部迁移只在 8-03**；后续卡不得新增 revision，缺列先改 8-03。
2. **不得复用 `UserStatus.FROZEN`**；暂停用 `admin_suspended_at`。
3. **不改业务数据行**；写能力仅限账户三项。
4. **列表不返回事件正文**；正文只在 `/events`。
5. **审批四值不合并**；区分服务端确认与客户端上报，未上报不得推断为拒绝。
6. **`search_text` 与事件同寿**；清理必须重算。
7. **不碰 `app/` 的 Android 构建**；8-02 的 TS 改动与 8-10 的 `LegalPage` 文案除外。
