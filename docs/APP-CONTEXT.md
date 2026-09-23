# Aozora · APP 长期开发上下文

> **文档定位：** 本项目面向人类开发者和 Agent 的长期总纲。任何实现任务开始前，先读根目录 `AGENTS.md`，再读本文件的“当前事实、目标架构、模块边界、任务约束”，最后只加载任务相关的专题文档。
>
> **基线版本：** Production Baseline 1.3
> **基线日期：** 2026-09-03
> **当前阶段：** 从本地 Demo 向可投入生产的完整 Android App 演进
> **生产决策：** D-PROD-01～D-PROD-11 已于 2026-09-03 全部确认
> **文档任务：** T-M0-06 生产化架构与任务框架基线

---

## 1. 本文档解决什么问题

本文档让后续执行任务的 Agent 在较短上下文内回答五个问题：

1. 这个产品为谁解决什么问题，哪些体验不能被破坏？
2. 当前代码已经有什么，目标系统最终应是什么？
3. 前端、Android 原生层、服务端、数据库和 AI 分别负责什么？
4. 当前任务属于哪个模块，会影响哪些接口、数据和测试？
5. 做到什么程度才算完成，如何验证没有破坏生产级约束？

本文档是总纲，不复制全部细节。数据结构、动作协议、安全规则、视觉 Token 等细节仍由对应专题文档维护。

### 1.1 信息优先级

发生冲突时按以下顺序裁决：

1. 用户在当前任务中的明确要求
2. 根目录 `AGENTS.md`
3. 本文件 `docs/APP-CONTEXT.md`
4. `docs/product/`、`docs/architecture/`、`docs/design/`、`docs/engineering/`
5. `docs/adr/` 中仍适用于生产架构的决策

本文件明确废止两项旧假设：

- “数据只存客户端、云同步不做”只适用于原型阶段，不再是生产目标。
- “客户端或 APK 持有 LLM 服务商密钥”不再允许，生产调用必须经过服务端 AI 网关。

### 1.2 状态标记

| 标记 | 含义 |
|---|---|
| `CURRENT` | 当前仓库已经存在且可以从代码或产物验证 |
| `PARTIAL` | 有实现，但功能不完整、未接线或未达到生产验收 |
| `TARGET` | 已纳入生产架构，应由对应 Task 实现 |
| `DECISION` | 需要用户集中决策；在决策前使用本文推荐默认值设计，不擅自部署 |

---

## 2. 产品核心

### 2.1 产品定义

**产品名称：** Aozora，这是唯一正式项目名和 App 名
**AI 伙伴角色：** 小贝，仅作为角色名，不作为 App 名或项目名
**Android 命名空间：** `com.aozora.app`
**一句话价值：** 给年轻职场人使用的 AI 记账 App——不用认真记账，也知道钱花哪了。
**核心用户：** 20–30 岁、一二线城市、对财务管理有需求但无法坚持传统手动记账的职场新人。
**辅助用户：** 18–24 岁学生，不为其增加独立重型功能。

### 2.2 产品目标

生产版本需要同时满足：

- 用户可通过文字、语音、图片或手动入口完成记账。
- AI 负责理解与陪伴，确定性业务代码负责金额、日期、统计和写库。
- 登录用户的数据可安全存入服务端并跨设备恢复。
- 客户端短时断网仍可查看近期数据和继续记账，联网后可靠同步。
- 用户始终知道系统记录了什么，并能确认、修改、撤销或删除。
- 数据正确性、安全性、可恢复性和可审计性达到生产要求。

### 2.3 不可破坏的产品原则

| ID | 原则 | 实施含义 |
|---|---|---|
| PR-1 | AI-native，无表单主路径 | 记账、查询、设置和分类操作都应支持自然语言；手动表单是可靠兜底 |
| PR-2 | 准确高于省事 | 高置信可自动执行并立即展示撤销；低置信必须确认；不得静默产生账目 |
| PR-3 | 云端可靠、离线可用 | 服务端保存权威业务数据；客户端保留本地副本和待同步操作，不以断网阻塞核心记账 |
| PR-4 | 共情不说教 | 超支时安慰、节省时庆祝，禁止评判和羞辱性文案 |
| PR-5 | 10 秒完成记账 | 对话或语音路径从进入到完成的中位数不超过 10 秒 |
| PR-6 | 高颜值是功能 | Aozora 四时天空是品牌体验，不是可随意移除的装饰 |
| PR-7 | 用户拥有数据 | 支持导出、恢复、删除账号与服务端数据；数据用途透明 |
| PR-8 | 可观测且可恢复 | 生产故障必须可发现、可定位、可回滚，数据库必须有备份与恢复演练 |

### 2.4 当前非目标

在首个生产版本中不做：

- 多币种与汇率换算
- 情侣、家庭和企业共享账本
- 投资组合、资产负债和证券行情
- 发票报销工作流
- iOS、网页版、小程序
- 广告变现
- 银行卡、支付宝或微信自动抓取流水

“用户账号、云端保存、跨设备恢复”已经进入生产范围，不再属于非目标。

### 2.5 核心用户旅程

1. **首次使用：** 安装 → 隐私说明 → 游客体验或登录 → 创建默认账本 → 完成首笔记账。
2. **日常记账：** 输入文字/语音/图片 → 规则或 AI 解析 → 确认或快速入账 → 立即看到结果与撤销入口。
3. **断网记账：** 本地规则解析 → 写入本地数据库和同步队列 → UI 标记待同步 → 联网后自动提交。
4. **看懂本月：** 首页概览 → 明细 → 分类/趋势/预算 → AI 月度洞察。
5. **换机恢复（首发预留）：** 登录 → 拉取云端账本 → 校验汇总一致；首发不实现多设备同时同步。
6. **数据退出：** 导出数据 → 删除账号确认 → 服务端执行删除或法定保留策略 → 返回可审计结果。

---

## 3. 当前仓库事实

### 3.1 技术现状

| 层 | 当前实现 | 状态 |
|---|---|---|
| Android | Kotlin 单 Activity + 受限 WebView，minSdk 26 / targetSdk 36 | `CURRENT`，仅生产基线空壳 |
| Web UI | React 19、Vite 8、Tailwind v4、HashRouter、Zustand、Framer Motion、Recharts；四时主题系统、核心页面（首页/明细/报表/我的/首启/登录） | `PARTIAL`，T-M4-00 ✅ |
| 页面、手动记账与统计 | 首页、明细、报表、快速记账 Sheet、我的、首启、登录；四时设计系统；状态组件 | `PARTIAL`，T-M4-00 ✅ |
| AI、规则 NLU 与动作协议 | 本地 NLU 与 ActionProposal 基础已实现；服务端 AI/执行闭环未接入 | `PARTIAL` |
| 本地数据与离线队列 | Room v2 schema（6 实体 + `sync_meta`），`pending_mutations` 携带 `expectedVersion`/`lastError`；Repository 单事务合并路径 `applyServerChanges`；WorkManager 同步调度打通；设备可靠性已通过 | `PARTIAL`，T-M3-02 ✅ |
| 原生桥、提醒、小组件与快捷方式 | 版本化 Bridge v1 25 命令（含 `sync.*` + `reminder.*` 3 个）；ReminderScheduler + AlarmManager + BootReceiver；AozoraWidgetProvider（Room 聚合，三状态）；静态 shortcut + deep link 路由 | `PARTIAL`，T-M4-05 ✅ |
| 服务端 | FastAPI 骨架、配置校验、健康检查、OpenAPI 3.1、Dockerfile；11 实体模型 + Alembic 迁移（0001_initial → 0006_budget_deleted_at）；auth 模块；账本核心 API（Books/Categories/Accounts/Transactions/Budgets CRUD）；统计与导出 API（月度/趋势聚合、JSON/CSV 导出，对账不变量）；同步模块（push/pull/bootstrap、幂等 mutation、cursor 变更流、budget 软删支持）| `PARTIAL`，T-M2-01/02/03/04/05 ✅；T-M3-01 ✅；T-M3-02 服务端 budget 扩展 ✅ |
| 测试 | 前端 480 + Android JVM（含 SyncPayloadMapperTest 11 + BridgeProtocolTest 25 命令检查 + ReminderSchedulerTest）+ 设备 Room（含 Migration1To2 + SyncEngineTest + 手工 FlightMode）+ 服务端 426 项通过 | `PARTIAL` |
| 发布 | 无签名材料和可分发产物 | `TARGET` |

### 3.2 当前不能视为已交付的能力

- 真实 AI 调用与提案确认/业务执行闭环
- 安全的登录、鉴权和会话管理（服务端 auth 模块已实现；SMS 供应商未接入，当前开发模式用固定验证码）
- 云端同步与换机恢复（服务端 T-M3-01 ✅；Android 同步引擎 T-M3-02 ✅；bootstrap checksum T-M3-03 ✅）
- 数据库版本迁移器（Room MIGRATION_1_2 已就绪，Alembic 0001 → 0006 流水线已就绪）
- 可靠自动备份、数据库恢复和账号删除
- 多账本、二级分类、支付渠道（Schema 已支持，业务 API 待实施）
- 生产监控、CI/CD、灰度发布和安全审计
- 服务端自动化测试门禁（服务端 106 项测试本地通过；前端/Android CI 已通过；服务端 CI 门禁尚未建立）

---

## 4. 目标生产架构

### 4.1 默认架构结论

在集中决策项未另行修改前，按以下基线设计：

- Android WebView + React 继续作为客户端架构，不重写 Flutter 或全原生 UI。
- 客户端使用 Kotlin Room 作为可靠本地数据库；Zustand 只保存视图态和短生命周期缓存。
- 服务端数据库是登录用户业务数据的权威存储；Room 是本地副本和离线工作区。
- 服务端提供版本化 HTTPS JSON API、认证、账本业务、增量同步、AI 网关和运维接口。
- LLM 密钥只存在服务端密钥管理环境，客户端永远不接触服务商密钥。
- 所有金额使用整数分；所有服务端时间使用 UTC 时间戳，交易业务日期单独保存为用户时区下的 `YYYY-MM-DD`。

### 4.2 系统关系

```text
┌──────────────────── Android 客户端 ────────────────────┐
│ React UI                                                │
│ ├─ pages / features / design system                     │
│ ├─ domain actions + validators                          │
│ ├─ Zustand view state                                   │
│ └─ API client + sync status                             │
│                │ versioned JS bridge                    │
│ Kotlin shell                                            │
│ ├─ Room local database                                  │
│ ├─ offline mutation queue                               │
│ ├─ token storage（Android Keystore）                    │
│ ├─ Vosk / camera / notification / widget               │
│ └─ network and background sync                          │
└───────────────────────┬─────────────────────────────────┘
                        │ HTTPS / JSON / bearer token
┌───────────────────────▼─────────────────────────────────┐
│ Production API                                          │
│ ├─ Auth & Session                                       │
│ ├─ Ledger / Book / Category / Budget                    │
│ ├─ Sync / Idempotency / Conflict                        │
│ ├─ Statistics / Export / Account lifecycle              │
│ └─ AI Gateway / Prompt / Validation / Usage             │
└───────────────┬─────────────────┬───────────────────────┘
                │                 │
       ┌────────▼────────┐  ┌────▼─────────────────────┐
       │ SQL Database    │  │ External capabilities    │
       │ authoritative   │  │ LLM / object storage /   │
       │ business data   │  │ SMS-email / observability│
       └─────────────────┘  └──────────────────────────┘
```

### 4.3 层级职责

| 层 | 必须负责 | 不得负责 |
|---|---|---|
| React UI | 展示、交互、确认卡、输入态、同步状态、无障碍 | 直接保存权威账目、持有密钥、自行计算服务端统计真值 |
| Client domain | 动作类型、输入校验、金额/日期工具、乐观更新模型 | 调用具体 UI、绕过 repository 写存储 |
| Kotlin platform | Room、网络、同步、Keystore、原生能力、后台任务 | 生成 AI 业务结论、复制页面业务逻辑 |
| Backend API | 鉴权、权限、业务校验、事务、幂等、审计、限流 | 信任客户端金额汇总、允许模型直接写数据库 |
| Database | 约束、事务、版本、软删除、审计时间 | 存储客户端无法解释的浮点金额 |
| AI gateway | 模型选择、提示词、结构化输出、校验、用量与降级 | 持有账本写权限、绕过业务 API |

### 4.4 已确认的服务端基线

已确认基础设施为 Linux + Docker + PostgreSQL 18。其余服务端技术按用户默许的推荐方案正式纳入生产基线；没有新的 ADR 时不得偏离：

- Runtime：Python 3.12+
- Framework：FastAPI
- Database：PostgreSQL 18
- ORM / migration：SQLAlchemy + Alembic
- Cache / distributed rate limit：Redis；单实例首发可先不启用业务缓存
- Object storage：S3 兼容服务，用于票据图片，首发可只保留短期解析文件
- API documentation：OpenAPI 3.1
- Observability：OpenTelemetry Collector + Prometheus + Loki + Tempo + Grafana + Alertmanager
- Deployment：Docker Compose 起步，生产至少区分 staging / production

服务器容量、入口网络和 PostgreSQL 18 实际部署参数仍由 `T-M0-07` 盘点。任何技术栈替换必须先通过 ADR，禁止 Agent 私自替换。

### 4.5 目标仓库结构

服务端与基础设施继续放在同一仓库，保证客户端、OpenAPI 和动作协议可以做原子变更与契约测试：

```text
aozora/
├─ app/                         # 现有 Android + React 客户端
│  ├─ src/                     # React 源码
│  └─ src/main/                # Kotlin、Room、原生资源与 Web 产物
├─ server/                      # 新增生产 API
│  ├─ src/
│  │  ├─ common/               # 配置、错误、日志、鉴权依赖、请求 ID
│  │  ├─ modules/
│  │  │  ├─ auth/
│  │  │  ├─ users/
│  │  │  ├─ ledger/            # books/categories/accounts/transactions
│  │  │  ├─ budgets/
│  │  │  ├─ statistics/
│  │  │  ├─ sync/
│  │  │  ├─ assistant/
│  │  │  ├─ attachments/
│  │  │  └─ exports/
│  │  ├─ models/               # SQLAlchemy 模型
│  │  ├─ migrations/           # Alembic 迁移脚本
│  │  └─ main.py
│  └─ test/                     # integration、contract、fixtures
├─ contracts/                   # OpenAPI、ActionProposal、共享 JSON Schema
├─ infra/                       # Docker、staging/prod 模板、监控和 runbook
├─ docs/                        # 产品、架构、ADR、设计、工程与里程碑
└─ tools/                       # 一次性迁移、校验与发布辅助脚本
```

`contracts/` 是客户端与服务端接口的唯一机器可读事实源。生成代码可以由它产生，但不得手工维护两份含义不同的 DTO。

### 4.6 日志、监控与告警方案

首发采用自建、Docker 可部署的 Grafana 可观测栈：

- 服务端输出带 `requestId`、`userIdHash`、module、latency 和 errorCode 的结构化 JSON 日志，禁止记录 token、原始票据、完整对话和账本明细。
- OpenTelemetry Collector 统一接收 traces、metrics 和 logs，避免业务代码绑定具体存储。
- Prometheus 保存 API、数据库连接池、同步队列、AI 延迟/错误率/用量等指标。
- Loki 保存脱敏日志，Tempo 保存分布式 trace，Grafana 提供统一查询和仪表盘。
- Alertmanager 负责 S0/S1 告警；通知渠道在部署阶段绑定，不在代码中硬编码。
- Android 首发使用本地脱敏崩溃摘要 + 下次启动上报到自有 telemetry API；上传前遵守隐私同意，release mapping 与版本产物一起归档。
- 默认保留：应用日志 14 天、指标 30 天、trace 7 天；实际容量由 T-M0-07 压测后调整。

最低仪表盘包括 API SLO、PostgreSQL、同步队列、AI Gateway、Android 版本健康度和业务漏斗六类。

---

## 5. 数据与同步规则

### 5.1 核心实体

| 实体 | 核心字段 | 关键规则 |
|---|---|---|
| User | id、status、timezone、createdAt | PII 最小化；支持游客升级或正式账号 |
| DeviceSession | id、userId、deviceId、refreshTokenHash、expiresAt | Refresh token 不明文入库，可单设备撤销 |
| Book | id、ownerId、name、icon、version、archivedAt | 首版仅单人所有权，预留成员模型但不实现共享 |
| Category | id、ownerId、bookId?、parentId、kind、name、archivedAt、version | 支持系统分类和二级自定义分类，删除为归档 |
| Account | id、ownerId、name、icon、accountType?、balanceCents、archivedAt、version | 支付渠道标记 + **用户可手动设置的当前余额**（存量字段，允许负值 = 信用卡欠款）；余额与「按交易现算的净额」不保证相等，差额 = 手动校准量（第十八轮 REQ-69 / ADR-013） |
| Transaction | id、bookId、type、amountCents、categoryId、accountId?、note、bizDate、occurredAt、source、version、deletedAt | 金额为正整数分；收支由 type 表达；软删除 |
| Budget | id、bookId、budgetType (monthly|annual)、categoryId?、period、amountCents、version | `(bookId, budgetType, period, categoryId)` 唯一；categoryId 为 null 时代表该周期整体预算；整体预算与分类预算在同一周期内可共存 |
| Attachment | id、transactionId?、objectKey、mimeType、status、expiresAt? | 私有访问，上传类型和大小受限 |
| AIConversation | id、userId、bookId、summary、updatedAt | 服务端只保留实现功能所需内容 |
| AIUsage | userId、provider、model、tokens、cost、requestId | 支持限额、审计和成本分析 |
| SyncChange | cursor、userId、entityType、entityId、operation、version、changedAt | 为客户端增量拉取提供单调游标 |

### 5.2 数据不变量

- `amountCents` 必须是安全范围内的整数，禁止浮点金额进入 domain、API 或数据库。
- `expense` 和 `income` 都保存正金额，由 `type` 决定方向。
- 统计值只能从未删除流水聚合，预算不改变流水总额。
- 流水汇总必须满足：客户端明细合计 = 客户端统计 = 服务端统计 = 导出文件合计。
- 所有写操作携带 `mutationId`，服务端对同一用户的 mutation 幂等。
- 所有可修改实体携带 `version`；版本冲突返回 `409 CONFLICT`，不得静默覆盖金额或日期。
- 删除采用软删除并进入同步变更流；真正物理删除仅由数据生命周期任务执行。
- 服务端保存 `occurredAt` UTC 时间和 `bizDate` 用户业务日期，二者不可互相替代。

### 5.3 同步协议

默认采用“服务端权威 + 本地副本 + 操作队列”：

1. 客户端先把操作写入 Room 事务，同时写入 `pending_mutations`。
2. UI 乐观展示，并明确标记 pending / syncing / failed。
3. 后台同步按顺序向 `/api/v1/sync/push` 提交 mutation。
4. 服务端执行鉴权、版本校验和幂等写入，返回服务端版本。
5. 客户端使用 cursor 调 `/api/v1/sync/pull` 拉取其他设备及服务端变更。
6. 同步结果与 pending 操作在同一 Room 事务内合并。

冲突策略：

- 新增流水：由 `mutationId` 去重，不产生重复账目。
- 修改或删除流水：版本不一致时停止自动覆盖，保留本地待处理版本并提示用户选择。
- 昵称、主题等非财务设置：允许服务端最新更新时间胜出。
- 分类同名：按同一账本、同一父级、同一类型做唯一性校验，返回可解释冲突。
- 统计不参与双向同步，始终由流水和预算重算。

### 5.4 API 基线

所有接口前缀为 `/api/v1`，错误返回稳定的 `code`、`message`、`requestId`，用户可读文案由客户端映射。

| 模块 | 主要接口 |
|---|---|
| Auth | `POST /auth/guest`、`POST /auth/login`、`POST /auth/refresh`、`POST /auth/logout` |
| User | `GET /me`、`PATCH /me`、`POST /me/export`、`DELETE /me` |
| Books | `GET/POST /books`、`GET/PATCH/DELETE /books/:id` |
| Categories | `GET/POST /categories`、`PATCH/DELETE /categories/:id` |
| Accounts | `GET/POST /accounts`、`PATCH/DELETE /accounts/:id` |
| Transactions | `GET/POST /transactions`、`GET/PATCH/DELETE /transactions/:id`、`POST /transactions/:id/undo` |
| Budgets | `GET /budgets`、`PUT /budgets/:period`、`PUT /budgets/:period/category/:categoryId` |
| Statistics | `GET /stats/monthly`、`GET /stats/trends` |
| Sync | `POST /sync/push`、`GET /sync/pull?cursor=`、`POST /sync/bootstrap` |
| AI | `POST /ai/interpret`、`POST /ai/report`、`POST /ai/feedback` |
| Attachments | `POST /attachments/upload-ticket`、`POST /attachments/:id/parse` |

---

## 6. AI 架构与动作协议

### 6.1 基本原则

- 本地规则 NLU 负责离线和常见短句，响应快且免费。
- 服务端 AI 网关负责复杂理解、图片、多笔记录和叙述式洞察。
- LLM 只返回候选动作，不获得数据库连接或业务写权限。
- 所有候选动作经过 JSON Schema、金额一致性、日期范围、分类权限和业务规则校验。
- 最终入账通过普通 Transactions API 或同步 mutation 执行。
- AI 返回的统计数字必须来自服务端确定性聚合输入；模型只负责组织语言。

### 6.2 标准调用链

```text
用户输入
  ├─ 离线/简单句 → 本地规则 NLU → ActionProposal
  └─ 在线/复杂句 → Backend AI Gateway → LLM → ActionProposal
                                  ↓
                        schema + business validation
                                  ↓
               高置信快速执行 / 低置信确认卡 / 无效则追问
                                  ↓
                     normal ledger API / sync mutation
```

### 6.3 ActionProposal 最小契约

```ts
type ActionProposal = {
  proposalId: string
  action: 'add_tx' | 'add_tx_batch' | 'query' | 'report' |
          'create_category' | 'config' | 'chat'
  confidence: number
  requiresConfirmation: boolean
  transactions?: Array<{
    type: 'expense' | 'income'
    amountCents: number
    categoryId?: string
    categoryPath?: string[]
    accountId?: string
    bizDate: string
    note: string
  }>
  message: string
  warnings: string[]
}
```

协议变更必须先更新 `docs/architecture/ai-architecture.md` 并新增 ADR 或协议版本，客户端和服务端必须共享契约测试。

### 6.4 AI 失败处理

| 情况 | 用户体验 |
|---|---|
| 断网 | 自动使用本地规则；无法处理图片时保留草稿并提示联网后继续 |
| 超时 | 12 秒显示慢速提示；25 秒取消远端请求并尝试本地规则 |
| 非法 JSON | 服务端拒绝，重试一次；仍失败则降级或追问 |
| 429 / 配额 | 不暴露服务商错误，回退本地能力并记录监控事件 |
| 认证失效 | 刷新会话；失败后保留本地待同步数据并引导重新登录 |
| 数字不一致 | 拒绝动作，绝不入账，记录安全级 AI 校验事件 |

### 6.5 Agent 化目标架构（M7）

本节 6.1–6.4 描述**当前已实现**的单发制契约。M7 起向 **agent 化目标架构**演进，对标 DeepSeek App 的对话框体验：**服务端 Agent Loop + SSE 流式 + 客户端瘦渲染**，配合**设备能力通道**覆盖永远无法上云的设备类能力（权限/语音/分享/提醒等），使用户通过一个对话框完成 App 内全部已有功能。含多轮会话、工具调用、统一智能模式（路径对用户透明）、域路由与 Skill 封装。

- **目标架构全文：** `docs/architecture/ai-agent-architecture.md`
- **关键决策与替代方案：** `docs/adr/ADR-006-ai-agent-architecture.md`
- **硬前提：** `savings` / `scheduled` / `personas` 三实体上云（`D-PROD-03` 云端权威的既定需求，非 agent 专属成本）
- **不变的三条红线（agent 化不放松）：** ① 模型永不输出 UUID，名→id 由确定性代码解析；② 金额与统计由确定性代码计算，写库必经 ActionProposal 校验与审批门（审批四值：只有 `allowed-once` 放行）；③ 快路径（记账/查账）保持 12 s / 25 s SLO 不变，多步任务单步独立计时。

---

## 7. 前端与原型设计框架

### 7.1 页面地图

| 页面/入口 | 核心职责 | 生产补齐 |
|---|---|---|
| 首次启动 | 品牌、隐私、权限解释、游客/登录选择 | 当前缺失 |
| 首页 | 今日支出、本月进度、近期流水、AI 入口、同步状态 | 修复对比度；增加空态/错误态/同步态 |
| 明细 | 月份、日分组、筛选、详情、编辑、删除 | 增加账户/来源/同步状态和分页 |
| 快速记账 Sheet | 收支、金额、分类、账户、日期、备注 | 当前只有收支/金额/分类 |
| AI 对话 | 文本、语音、图片、确认卡、批量结果、撤销 | 当前仅 mock 文本 |
| 报表 | 分类、趋势、预算、环比、AI 洞察 | 当前只有当月基础统计 |
| 我的 | 账户、账本、分类、预算、提醒、主题、导出、安全、账号 | 当前仅昵称与预算可操作 |
| 登录与设备 | 游客升级、登录、退出、设备会话、换机恢复 | 当前缺失 |

### 7.2 AOZORA 视觉约束

- 晨、昼、暮、夜四时主题继续使用 `docs/design/tokens.md` 的 Token。
- 天空只作为背景层，装饰不得遮挡内容或接收点击。
- 每个主题的正文、辅助文字、卡面和按钮均需达到 WCAG AA；正文对比度至少 4.5:1。
- 金额是首页视觉主角，但不得牺牲同步状态、错误提示和可访问性。
- 动效遵循 reduced motion；低端 Android 设备可关闭云、星、彗尾等氛围层。
- 当前原型白天主题存在文字对比度不足，属于 M4 视觉验收阻塞项。**T-M4-06a 已修复**（2026-09-06）：四主题 ink3 均经 WCAG AA 对比度验证，自动化测试 `contrast.test.ts` 覆盖。

### 7.3 目标前端目录

```text
app/src/
├─ app/                 # 启动、路由、providers、全局错误边界
├─ domain/              # money、date、actions、entities，纯函数无 UI 依赖
├─ platform/            # JS bridge、API、auth、sync 适配器
├─ features/
│  ├─ onboarding/
│  ├─ ledger/
│  ├─ books/
│  ├─ budgets/
│  ├─ statistics/
│  ├─ assistant/
│  └─ settings/
├─ components/          # 跨 feature 的稳定 UI 组件
├─ design/              # token、theme、图标、可访问性工具
└─ test/                # fixtures、builders、测试环境
```

迁移按功能任务逐步进行，不允许一次性重构所有页面。现有 `pages/`、`stores/`、`utils/` 在对应模块迁移完成前继续可用。

---

## 8. 功能模块拆解

### 8.1 身份与账号

**目标：** 支持低摩擦首次体验、可靠登录、换机恢复、会话撤销和账号删除。
**范围：** 游客身份、账号绑定、access/refresh token、设备列表、退出、删除账号。
**验收：** token 不进入 Web localStorage；服务端可撤销 refresh token；换机登录后数据完整；删除流程有二次确认和审计结果。

### 8.2 账本、分类与账户

**目标：** 为所有流水提供稳定组织结构。
**范围：** 多账本、内置/自定义二级分类、支付渠道标记、账户分类与当前余额、归档。
**验收：** 账本数据隔离；分类归档不破坏历史流水；账户余额为**用户可手动设置的存量值**（可为负），编辑后同步与重启均保持；名称冲突可解释。

### 8.3 交易记账

**目标：** 通过文字、语音、图片和手动四种入口可靠完成收支记录。
**范围：** 单笔/多笔、确认、快速通道、编辑、软删除、撤销、离线 pending。
**验收：** 金额整数分；重复请求不重复入账；编辑后所有统计一致；低置信不自动写入；离线数据最终可同步。

### 8.4 预算与统计

**目标：** 让用户不用整理也能理解本月花销。
**范围：** 月/年预算、分类预算、分类占比、每日/月份趋势、环比、最大单笔、连续记账、AI 洞察。
**验收：** 服务端与客户端同 fixture 输出一致；跨月和时区边界正确；AI 报告中每个数字能映射到聚合数据。

### 8.5 AI 助手

**目标：** 小贝能理解复杂表达、生成候选动作、回答账本问题并保持共情语气。
**范围：** prompt、动作协议、上下文裁剪、确认卡、模型降级、用量、反馈。
**验收：** 密钥仅服务端；非法输出零写库；标注集达到既定准确率；所有远端异常不阻塞手动与规则记账。

### 8.6 语音与图片

**目标：** 提供低摩擦输入，同时保证隐私、权限和失败恢复。
**范围：** Vosk 离线语音、相机/相册、图片压缩、临时上传、多笔识别。
**验收：** 权限按需申请；拒绝权限仍可手动输入；图片格式/大小受控；解析失败保留草稿；临时图片按策略删除。

### 8.7 同步与离线

**目标：** 断网可用、联网不重复、跨设备可恢复、冲突不静默。
**范围：** Room 副本、mutation queue、push/pull cursor、后台任务、冲突 UI。
**验收：** 网络闪断和重复提交不产生重复流水；进程被杀后队列仍在；双设备修改同一流水能检测冲突；全量重建后对账一致。

### 8.8 设置、通知与原生集成

**目标：** 将已有 Android 能力接入统一数据和账号体系。
**范围：** 提醒、快捷方式、小组件、主题、搭子设置、触感、分享。
**验收：** 重启后提醒恢复；小组件读取 Room 聚合而非旧 JSON；快捷入口路由正确；系统深色和四时主题规则一致。

### 8.9 数据导出、备份与生命周期

**目标：** 用户可带走数据，平台可恢复数据，也能按请求删除数据。
**范围：** JSON/CSV 导出、数据库备份、恢复演练、账号删除、附件清理。
**验收：** 导出合计与流水一致；备份可在隔离环境恢复；默认 RPO ≤24 小时、RTO ≤4 小时；删除任务有状态和完成证明。

### 8.10 生产运维与合规

**目标：** 服务可部署、监控、报警、升级和回滚。
**范围：** 环境隔离、CI/CD、迁移、日志、指标、告警、限流、隐私政策、权限说明。
**验收：** staging 可重复部署；生产密钥不进代码和镜像层；数据库迁移有回滚或前向修复方案；S0/S1 告警可触达维护者。

---

## 9. 生产化 Task 任务树

每个 Task 是可独立评审、可独立验证的交付单元。实施某一 Task 前，应再创建该 Task 的详细执行计划；本节提供目标、依赖、实施方案和验收边界。

### M0：生产架构基线与决策

#### T-M0-06 长期上下文与生产架构基线

- **目标：** 建立本文件，使所有后续工作有统一产品、架构和验收上下文。
- **实施：** 抽取现有文档和代码事实，区分 CURRENT / TARGET / LEGACY，定义模块、任务和决策清单。
- **验收：** 不存在未定义的占位内容；能定位每个核心模块、权威文档、目标架构和首批任务；`AGENTS.md` 有入口。

#### T-M0-07 服务器与数据库现状盘点

- **目标：** 把用户已有服务器、域名、数据库和部署能力映射到目标架构。
- **依赖：** T-M0-06。
- **实施：** 记录 OS、CPU/内存、网络、域名/证书、数据库类型和版本、备份、容器能力、地域与合规状态；验证 staging 可用性。
- **验收：** 产出不含密码的基础设施清单；所有 `D-PROD-*` 决策完成；实际技术栈写入 ADR。

#### T-M0-08 文档与生产化重基线

- **目标：** 消除旧“纯本地”架构与新生产架构之间的文档冲突。
- **依赖：** T-M0-07。
- **实施：** 更新 product principles、non-goals、architecture overview/data/security、milestones；后续架构变更按需新增 ADR。
- **验收：** `rg` 不再把“云同步不做”“客户端持有生产模型密钥”描述为现行要求。

#### T-M0-09 生产仓库重基线

- **目标：** 清洗原型实现、密钥和产物，建立可长期迭代的 Aozora Git 基线。
- **依赖：** 无。
- **实施：** 删除旧运行时和签名材料，重建最小 Android/React 空壳，创建边界目录、基线校验与首个 Git 提交。
- **验收：** 工作区无原型密钥、签名材料和发布物；最小壳可构建；Git 历史从干净基线开始。

#### T-M0-10 执行治理与长期规划

- **目标：** 将完成的生产重基线收口为可由不同 Agent 连续执行的任务图、阶段闸门和唯一交接入口。
- **依赖：** T-M0-09。
- **实施：** 更新里程碑状态、风险登记册和本任务树；创建 `docs/milestones/production-execution-plan.md`；列出实施参数的确认节点与任务交接模板。
- **验收：** T-M0-09 证据准确；所有任务依赖均指向已定义任务；后续 Agent 可按执行规划选择下一个未阻塞任务。

#### 功能开发前入口闸门

- T-M0-08 的文档冲突验收、T-M0-09 与 T-M0-10 必须完成；历史服务商密钥已在外部控制台撤销或轮换后，才可分发构建产物或接入真实 AI。
- 每位 Android 执行者须以 JDK 17、Android SDK 34 和已提交的 Gradle 8.9 Wrapper 运行 `./gradlew.bat :app:assembleDebug`。
- T-M0-07 仅在 T-M2-01 前必须完成；T-M1-01 是建立 CI 的唯一启动任务，T-M1-02 及其后的 M1 任务验收前必须启用该门禁。
### M1：客户端可靠数据底座 ✅

> **M1 验收结论（2026-09-04）：✅ M1 全部通过，阶段完成。** 前端测试 327/327（含 R-M1-34/35/36/37/43/48/49 全部回归用例）；Android 设备测试 27 自动化 + 3 手工（Room 27 自动化，含 +7 引用完整性回归 R-P1-RefInt；杀进程 2 手工 force-stop 协议、WebView 1 单独跑通过）；GitHub Actions CI 验证成功。NLU 修复闭环：R-M1-34/35/36/37/43/44/45/46/47/48/49/50 全部 ✅。

#### T-M1-01 前端 TypeScript 与测试门禁

- **目标：** 为财务领域代码建立静态类型和可执行测试基础。
- **依赖：** T-M0-08。
- **实施：** 配置 TypeScript、Vitest、React Testing Library；先迁移 domain 和新增模块，不要求一次性迁移全部 UI。
- **验收：** `npm run lint`、`npm run typecheck`、`npm test -- --run` 全绿；CI 执行同一组命令。
- **状态：** ✅ **通过**。交接产物：TypeScript strict、Vitest/RTL、GitHub Actions CI。GitHub Actions 验证成功（lint/typecheck/test 309 tests）。

#### T-M1-02 金额、日期与领域模型

- **目标：** 消除浮点金额和 UTC 业务日期错误。
- **依赖：** T-M1-01。
- **实施：** 新建 `MoneyCents`、`localBizDate()`、Transaction/Book/Category 类型和纯函数聚合器；替换相关 store 计算。
- **验收：** 浮点误差、23:59/00:01、跨月、收入/支出用例全绿；业务代码禁止 `toISOString().slice/split` 生成业务日期。
- **状态：** ✅ **通过**。交接产物：money/date/types/aggregator；业务日期不使用 `toISOString().slice/split`。

#### T-M1-03 Room 数据库与版本化 JS Bridge

- **目标：** 用 Room 替换 localStorage/单 JSON 作为生产本地存储。
- **依赖：** T-M1-02。
- **实施：** Kotlin Room entities/DAO/repository；JS Bridge 使用 `requestId + callback` 的版本化命令与查询接口；Zustand 只保存 UI 状态。
- **验收：** 杀进程重启数据完整；写入和 pending mutation 同事务；WebView 清缓存不丢 Room 数据；桥接口有契约测试。
- **状态：** ✅ **通过**。6 实体/DAO、Repository 原子写（transaction/book/category/account/budget）、v1 20 命令桥（含 account/budget 写命令）、TS 客户端、Vite 入 APK 与本地 HTTPS 加载。设备测试 30/30 通过（Room 27 + WebView 1 + 杀进程 2）。杀进程持久化验证通过。

#### T-M1-04 本地规则 NLU 重建

- **目标：** 恢复断网文本记账和查询能力。
- **依赖：** T-M1-02、T-M1-05。
- **实施：** 编写独立纯函数 parser，输出 ActionProposal；覆盖金额、收支、分类和相对日期。
- **验收：** 新建的核心与边界例进入 Vitest；非法或模糊输入不写账；parser 不依赖页面或存储。
- **状态：** ✅ **通过**。交接产物：`app/src/domain/nlu/`（七模块，纯函数，零 IO 依赖）；158 个 NLU 测试通过（R-M1-34/35/36/37/43/48/49 回归闭环）；核心语料通过契约闸；非法输入零写账。

#### T-M1-05 ActionProposal 契约与校验基础

- **目标：** 为本地规则 NLU、服务端 AI Gateway 与确认卡建立唯一、可版本化的动作提案契约。
- **依赖：** T-M1-01、T-M1-02。
- **实施：** 在 `contracts/` 定义 ActionProposal JSON Schema；新增 TypeScript 类型、确定性校验器与正反例 fixture；禁止校验器或提案直接写账本。
- **验收：** 合法提案可通过；金额、日期、权限或字段缺失的非法提案被拒绝；本地 NLU 与后续 AI 任务只消费同一契约。
- **状态：** ✅ **通过**。Draft-07 schema、16 fixture（7 valid + 9 invalid）、TS 校验器；67 项提案测试通过。
### M2：服务端与数据库基础

#### T-M2-01 Server 工程、配置与健康检查

- **目标：** 建立可在本地、staging、production 一致运行的服务端骨架。
- **依赖：** T-M0-07。
- **实施：** 创建 `server/`、环境配置校验、Dockerfile、health/readiness、OpenAPI、请求 ID、结构化日志。
- **验收：** 缺少必需环境变量时拒绝启动；health 和 readiness 可区分；镜像不包含密钥；staging 可重复部署。
- **状态：** ✅ 已完成。FastAPI 骨架（Python 3.12+）、pydantic-settings 配置校验（缺 `DATABASE_URL` 拒绝启动）、`/health` + `/health/ready`（含数据库连通检查）、请求 ID 中间件、结构化 JSON 日志、统一错误处理、OpenAPI 3.1、SQLAlchemy async 引擎、多阶段 Dockerfile（非 root）、docker-compose 本地开发栈；33 项 pytest 全部通过。详细交接记录见 `docs/milestones/production-execution-plan.md`。

#### T-M2-02 数据库 Schema 与迁移流水线

- **目标：** 实现第 5 节实体、约束和可审计迁移。
- **依赖：** T-M2-01。
- **实施：** SQLAlchemy 模型、唯一约束、外键、软删除、version、Alembic 迁移命令、seed 仅限开发环境。
- **验收：** 空库可一键迁移；生产迁移不执行破坏性 reset；金额字段为整数；迁移在 staging 备份副本演练通过。
- **状态：** ✅ 已完成。11 核心实体模型（User/DeviceSession/Book/Category/Account/Transaction/Budget/Attachment/AIConversation/AIUsage/SyncChange）全部实现；amount_cents 为 BigInteger + CHECK > 0；可修改实体均带 version 列；books/transactions 支持软删除；Alembic async 配置 + 手写初始迁移（0001_initial）；迁移与 ORM 一致性 7 项验证通过；12 项模型约束测试通过；累计 52 项 pytest 全部通过。详细交接记录见 `docs/milestones/production-execution-plan.md`。

#### T-M2-03 认证、会话与设备安全

- **目标：** 实现游客/正式账号、短期 access token 和可撤销 refresh token。
- **依赖：** T-M2-02、D-PROD-02。
- **实施：** auth module、token rotation、refresh hash、设备会话、频率限制；客户端 token 进入 Android Keystore。
- **验收：** 过期、重放、撤销和多设备用例全绿；token 不进入日志、Web localStorage 或数据库明文字段。

#### T-M2-04 账本核心 API

- **目标：** 提供 Books、Categories、Accounts、Transactions、Budgets 的事务型 API。
- **依赖：** T-M2-02、T-M2-03。
- **实施：** controller/service/repository 分层、DTO 校验、ownership guard、version 和 mutationId 幂等。
- **验收：** 越权访问全部返回受控错误；重复 mutation 只产生一条流水；软删除、版本冲突和分页测试通过。

#### T-M2-05 服务端统计与导出

- **目标：** 提供权威聚合和用户数据导出。
- **依赖：** T-M2-04。
- **实施：** 月度/趋势 SQL 聚合、JSON/CSV 异步导出、下载授权和过期清理。
- **验收：** 同一 fixture 的 API 统计、流水求和和导出合计严格一致；导出链接不可跨用户访问。
- **状态：** ✅ 已完成。`GET /stats/monthly`（月度聚合含分类明细）、`GET /stats/trends`（趋势折线含空月填充）；`POST /exports`（JSON/CSV 生成）、`GET /exports`（列表）、`GET /exports/{id}/download`（鉴权下载，24h 过期）；Export 实体 + Alembic 迁移 `0002_exports`；`_YearMonth` 跨数据库方言分发（SQLite/PG）；对账不变量测试（统计 API 合计 == 导出求和 == 流水求和）；ownership guard 验证统计/导出/下载均拦截跨用户访问；22 项新增测试，服务端累计 122 项全部通过。详细交接记录见 `docs/milestones/production-execution-plan.md`。

### M3：客户端—服务端同步与恢复

#### T-M3-01 同步契约与服务端变更流

- **目标：** 实现幂等 push、cursor pull 和首次 bootstrap。
- **依赖：** T-M2-04。
- **实施：** mutation registry、SyncChange、稳定 cursor、批量限制、版本冲突响应；发布共享 OpenAPI/JSON Schema。
- **验收：** 重复、乱序、分页拉取和 cursor 恢复测试通过；任何响应中断后可安全重试。

#### T-M3-02 Android 同步引擎

- **目标：** 自动同步 Room pending mutations，并在后台可靠重试。
- **依赖：** T-M1-03、T-M3-01。
- **实施：** WorkManager、指数退避、网络约束、token refresh、Room 原子合并、前端同步状态事件。
- **验收：** 飞行模式记账后恢复网络可同步；进程/设备重启队列不丢；连续点击不会产生重复流水。

#### T-M3-03 单设备恢复与多设备兼容预留 ✅

- **目标：** 首发支持单设备数据重建，并在协议层保留未来多设备同步能力。
- **依赖：** T-M3-02。
- **实施：** bootstrap checksum、Room 重建流程、deviceId/version/cursor 字段；服务端保留冲突响应，首发不开发多设备入口、实时推送和冲突 Sheet。
- **验收：** 清空 Room 后可从服务端重建并对账一致；服务端版本冲突不会静默覆盖；首发 UI 不承诺多设备同时使用。
- **结果：** 服务端 72 项测试 + 客户端 JVM 单元测试 + androidTest 编译通过。T-M3-03 审查闸门通过（P1-1/P1-2 已修复）。

### M4：AI、原生能力与完整产品体验

> 2026-09-06 起 M4 各任务按实现工程量拆解为子任务（编号如 T-M4-00a、T-M4-01a），并新增 T-M4-00 前端骨架与核心页面。子任务范围、依赖、建议顺序与状态的权威来源为 `docs/milestones/production-execution-plan.md` M4 章节；本节维持父任务目标与验收边界。

#### T-M4-00 前端骨架与核心页面

- **目标：** 建立生产前端基础：应用骨架、四时设计系统与核心页面数据流，为 M4 各功能任务提供页面承载。
- **依赖：** T-M1-03、T-M3-02。
- **实施：** Tailwind/Zustand/Framer Motion/Recharts/路由落地与 §7.3 目录搭建；四时主题 token、状态组件与可访问性基础；首页、快速记账 Sheet、明细、我的、报表基础、首启与登录页全部经 bridge 读 Room；bridge 扩展 auth.* 命令，token 保持 Kotlin 层。
- **验收：** 核心页面金额与 Room 一致；手动记账主链路（记账 → mutation → 同步）真机可用；WebView 存储无 token；既有前端测试不回归。视觉对比度验收与截图基线归 T-M4-06。

#### T-M4-01 服务端 AI Gateway

- **目标：** 安全代理模型调用并控制成本、超时、模型和日志。
- **依赖：** T-M2-03、D-PROD-05。
- **实施：** provider adapter、prompt registry、模型路由、限流、用量、超时、重试、脱敏日志；首发模型配置为 `qwen3.7-flash`；删除客户端生产密钥。
- **验收：** APK 和 Web 产物中无服务商密钥；用户限额有效；provider 故障可降级；请求可用 requestId 追踪。

#### T-M4-02 动作协议、校验与确认卡

- **目标：** 打通 AI 理解到安全业务执行的完整闭环。
- **依赖：** T-M1-05、T-M2-04、T-M4-00、T-M4-01。
- **实施：** ActionProposal Schema、服务端/客户端契约测试、confidence 策略、确认卡、批量编辑、5 秒撤销。
- **验收：** 非法 JSON、幻觉数字、无权限分类、低置信动作零写库；确认后通过普通业务 API 入账。

#### T-M4-03 AI 查询与月度洞察

- **目标：** 基于确定性数据生成“钱去哪了”的解释。
- **依赖：** T-M2-05、T-M4-00、T-M4-01。
- **实施：** 服务端生成事实包，LLM 只生成叙述，数字引用校验，同数据缓存，离线纯统计降级。
- **验收：** 报告数字全部可追溯；数据未变不重复计费；断网仍可查看基础报告。

#### T-M4-04 语音、图片与原生桥接

- **目标：** 把已有 Vosk、相机和图片输入接入 ActionProposal 流程。
- **依赖：** T-M4-02。
- **实施：** React platform adapter、权限状态机、语音草稿、图片压缩/上传、批量确认。
- **验收：** 权限拒绝和离线不阻塞手动记账；短句语音达到性能门槛；图片失败可恢复草稿。

#### T-M4-05 通知、快捷方式与小组件迁移

- **目标：** 原生入口统一读取 Room/同步后的数据。
- **范围澄清（2026-09-06）：** 提交 `3e1607b` 的 commit message 误标为"数据导出与统计服务完成"，该服务实属 T-M2-05（已完成），从未在本任务中实现。T-M4-05 的正确范围为通知、快捷方式与小组件迁移，详见 `docs/milestones/production-execution-plan.md` T-M4-05 完成记录中的 Commit 勘误。
- **依赖：** T-M1-03、T-M3-02、T-M4-00。
- **实施：** ReminderScheduler、Widget Provider 和 shortcuts 改用 repository；增加登录/无数据/待同步状态。
- **验收：** 重启后提醒有效；小组件与首页金额一致；快捷入口打开正确页面；无旧 JSON 依赖。

#### T-M4-06 AOZORA 全页面视觉与可访问性

- **目标：** 将当前视觉原型打磨为可生产使用的四时设计系统。
- **依赖：** T-M4-00、T-M4-02、T-M4-03（核心页面数据流稳定由其产出）。
- **实施：** token 化页面、状态组件、对比度修复、字体放大、reduced motion、低性能模式、截图基线。
- **验收：** 四主题正文对比度 ≥4.5:1；Android 小屏与字体 1.3× 无遮挡；关键页面视觉回归通过。

### M5：安全、质量、合规与内测

> 2026-09-06 起 M5 各任务按实现工程量拆解为子任务（编号如 T-M5-01a、T-M5-02a），子任务范围、依赖、实现要点、文件路径与验证命令的权威来源为 `docs/milestones/production-execution-plan.md` M5 章节；本节维持父任务目标与验收边界。四条线并行——质量线 `T-M5-01a→01d`、安全线 `T-M5-02a→02d`、备份线 `T-M5-03a→03c`、隐私线 `T-M5-04a→04c`；可观测栈 `T-M5-05a→05b` 建议提前部署。

#### T-M5-01 对账不变量与端到端测试

- **目标：** 建立数据正确性一票否决门禁，覆盖 domain/API/Room 三端等价的 fixture 与断言、固定种子 fuzz、同步异常注入、真机 E2E 核心路径。
- **依赖：** M1–M4 核心链路。
- **实施：** `contracts/fixtures/reconciliation/` 共享 fixture 集 + 四方对账断言（TS/Python/Kotlin 三端等价）；`Random(42)` 固定种子 fuzz 套件（vitest 100 轮 + pytest 50 轮）；MockWebServer 五态异常注入（断网/重复/乱序/冲突/杀进程）；E2E 脚本（登录→记账→离线→同步→冲突→恢复→删除）。
- **验收：** 同一 fixture 三端聚合输出一致；对账断言捕获故意篡改；fuzz 同种子可重现；CI 含服务端 job 且为 required check。
- **子任务：** T-M5-01a 共享 fixture 与对账不变量基础 → 01b 固定种子对账 fuzz → 01c 同步异常注入 → 01d E2E 核心路径与 CI 门禁。

#### T-M5-02 安全加固

- **目标：** 关闭已知客户端和服务端高风险项，建立密钥治理、依赖扫描与日志脱敏的持续门禁。
- **依赖：** T-M4-01。
- **实施：** `AndroidManifest.xml` 禁用明文/备份 + `network_security_config.xml` TLS 锁定 + WebView 关闭 file/content access + Bridge 白名单复核；服务端 Starlette 安全头中间件（HSTS/CSP/XFO/RP/PP） + 请求体 10MB 限制 + CORS 细化；`gitleaks` + `truffleHog` CI 密钥扫描 + `npm audit`/`pip-audit`/OWASP DependencyCheck 依赖扫描；服务端/客户端日志脱敏正则过滤。
- **验收：** Manifest/WebView 配置断言进 JVM 测试；安全头存在性断言 + 413 限流 + CORS 预检正确；CI 密钥/依赖门禁生效；日志审计无敏感字段。
- **子任务：** T-M5-02a 客户端安全基线 → 02b 服务端安全加固 → 02c 密钥治理与产物扫描 → 02d 依赖扫描与日志脱敏。

#### T-M5-03 备份恢复与灾难演练

- **目标：** 证明生产数据可恢复，RPO ≤24h、RTO ≤4h；建立自动化备份、定期演练、备份完整性校验的闭环。
- **依赖：** T-M2-02、D-PROD-06。
- **实施：** pg_dump 定时备份 + 异地副本脚本（`infra/backup/`）；恢复 runbook 含步骤/校验/失败处置（`docs/runbooks/disaster-recovery.md`）；隔离环境演练记录实测 RPO/RTO；备份完整性校验 cron + Alertmanager 告警。
- **验收：** 备份产物含全部 13 张业务表；隔离环境恢复成功且四方对账一致；RPO ≤24h、RTO ≤4h 实测达标；备份失败告警触发。
- **子任务：** T-M5-03a 备份策略与自动化 → 03b 恢复 runbook 与隔离演练 → 03c 备份校验与告警。

#### T-M5-04 隐私、权限与账号生命周期

- **目标：** 满足上架和真实用户数据治理要求：隐私同意流、权限按需申请、数据导出、账号删除与保留策略，全部通过审计。
- **依赖：** T-M2-03、T-M2-05。
- **实施：** 隐私政策版本化 + `UserConsent` 表入库（Alembic `0007_user_consents`）；我的页导出入口接入服务端 exports（JSON/CSV）；`DELETE /me` 二次确认→冻结→7 天物理删除→`deletion_audit` 完成证明；附件解析后 24h 清理（`infra/cleanup/`）。
- **验收：** 未同意前无业务数据上行；导出合计与流水一致；冻结期 403/空数据；删除后 Room/Keystore 清理断言。
- **子任务：** T-M5-04a 隐私政策与首启同意 → 04b 用户数据导出 → 04c 账号删除与数据保留。

#### T-M5-05 生产监控与种子内测

- **目标：** 建立 staging 可观测栈（OTel/Prometheus/Loki/Tempo/Grafana/Alertmanager），六类看板就绪，S0/S1 告警可到达维护者；完成 20–50 人种子内测。
- **依赖：** T-M5-01 至 T-M5-04（05a/05b 可提前）。
- **实施：** `docker-compose.staging.yml` 一站部署可观测栈 + 服务端 OTel 埋点；六类 Grafana 看板 + S0/S1 告警规则（`infra/monitoring/`）；Android `CrashReporter` 脱敏崩溃上报；内测构建（内测签名 + staging 基址）+ 两轮反馈-修复-回归。
- **验收：** staging 从零可重复部署且三类数据可查；六类看板有真实数据；crash-free ≥99.5%、记账成功率 ≥95%；无开放 S0/S1。
- **子任务：** T-M5-05a staging 环境与可观测栈部署 → 05b 看板、告警与崩溃上报 → 05c 种子内测与反馈闭环。

### M6：生产发布

> 2026-09-06 起 M6 各任务按实现工程量拆解为子任务（编号如 T-M6-01a、T-M6-02a），子任务范围、依赖、实现要点、文件路径与验证命令的权威来源为 `docs/milestones/production-execution-plan.md` M6 章节；本节维持父任务目标与验收边界。严格串行 `T-M6-01a→01d` → `T-M6-02a→02c` → `T-M6-03a→03c`。D-EXEC-01/02 必须在 T-M6-01a/01c 前回填 ADR-001。

#### T-M6-01 Production 基础设施与发布流水线

- **目标：** 可审核地部署 API、迁移数据库并回滚应用版本，完成从干净环境到生产服务的全自动化部署。
- **依赖：** M5 全部出口。
- **实施：** `docker-compose.prod.yml` + nginx + Let's Encrypt certbot 自动续期；GitHub Actions 五阶段流水线（build→test→image→sign→deploy）；Alembic 迁移审批流（PR + CI 验证 + 自动备份）；蓝绿部署 ADR；回滚 runbook（应用回退 + 数据库 `downgrade`/前向修复）。
- **验收：** 从干净环境可重复部署；密钥来自 secret manager；发布失败可回退（10min 内）；迁移前自动备份。
- **子任务：** T-M6-01a 生产环境、域名与 TLS → 01b CI/CD 与镜像签名 → 01c 迁移审批与发布策略 → 01d 回滚 runbook 与演练。

#### T-M6-02 Android 上架包与渠道验收

- **目标：** 产出与生产 API 配套的签名 APK/AAB 和商店材料，完成 Android 8/12/15 真机矩阵走查，至少一个渠道提交成功。
- **依赖：** T-M6-01。
- **实施：** `versionCode` 时间戳 + `versionName` 语义化；`buildConfigField` 三环境隔离（debug/staging/release）；R8 混淆 + `proguard-rules.pro` 保留序列化路径；外部 keystore 签名；包体预算检查（含 Vosk 模型，≤上一版 110%）；隐私清单与数据安全表单；三台真机走查 + 商店截图提交。
- **验收：** staging/prod 不串环境；签名和版本号正确；Android 8/12/15 真机矩阵通过；至少一个渠道提交成功。
- **子任务：** T-M6-02a Release 构建与签名 → 02b 包体与隐私清单 → 02c 真机矩阵与渠道提交。

#### T-M6-03 上线观察与回滚门槛

- **目标：** 小流量上线并用明确阈值决定扩量或回滚，建立 S0 自动止损、事故复盘和客户端兼容策略。
- **依赖：** T-M6-02。
- **实施：** 四阶梯放量（5%→25%→50%→100%）+ 晋级门槛（API 可用性 ≥99.9%、crash-free ≥99.5% 等连续达标 12h）；S0 自动监测（重复入账/金额错误/数据丢失/crash-free 骤降）→ 自动停止扩量 + 告警回滚；客户端兼容策略（新旧版本共存矩阵）；事故复盘模板（时间线/根因/守护用例）。
- **验收：** S0 自动停止扩量；核心 SLO 连续达标后才扩大范围；每次事故有时间线、根因和守护用例；M6 收口记录归档全部发布复盘。
- **子任务：** T-M6-03a 分阶段放量与值守 → 03b S0 自动止损与回滚阈值 → 03c 兼容策略与事故复盘。

---

### M7：AI Agent 能力升级

> 2026-09-16 立项，同日 v2 修订（编排位置改为服务端 Host + SSE，对标 DeepSeek 模式）。目标架构全文与分期依据见 `docs/architecture/ai-agent-architecture.md`，关键决策见 `docs/adr/ADR-006-ai-agent-architecture.md`。本节维持父任务目标与验收边界；子任务范围、文件路径与验证命令在 T-M7-01 开工时补录至 `docs/milestones/production-execution-plan.md` M7 章节。
> **总目标：** 用户通过一个对话框完成 App 内全部已有功能（25 个页面 / 约 70 个 bridge 命令覆盖的表单任务），新增功能不改动 prompt 主体。
> **依赖（2026-09-18 修订）：** 原「M6 收口（T-M6-03）后启动」**已撤销**——M6 属发布/推广/验证，M7 是对 App 功能的重大新增，须在正式发布前完成，故 **M7 优先推进、M6 实现滞后**。T-M7-02 自 02a 实测闸门起可直接开工。**不变红线：** ① 模型永不输出 UUID（名→id 由确定性代码解析）；② 金额与统计由确定性代码计算，写库必经 ActionProposal 校验与审批门（审批四值，只有 `allowed-once` 放行）；③ 快路径保持 12 s / 25 s SLO。

#### T-M7-01 三实体上云（P0）

- **目标：** `savings` / `scheduled` / `personas` 由「仅本地 Room」升级为「云端权威 + 本地副本」，使服务端可访问全部用户数据。
- **依赖：** 无。**独立价值：** 多设备、备份、换机恢复本就需要（`D-PROD-03` 云端权威）。
- **实施：** 服务端建表 + Alembic 迁移；接入现有 sync 模块（增量，不新协议）；客户端 Room 保留为副本（离线可读）；本地既有数据的一次性上行迁移策略；登出/删号的数据生命周期对齐。
- **验收：** 三实体可同步（双端一致）；离线可读、恢复后上行；迁移用旧版本 fixture 验证；对账不变量全绿；本地既有数据不丢失；旧客户端行为不变。
- **子任务：** 开工时拆分（savings → scheduled → personas，逐个交付）。

#### T-M7-02 服务端 Agent Loop 与 SSE 流式通道（P1）

- **目标：** 建立服务端 agent loop 与流式通道，使多步任务、多轮澄清（当前痛点）成为可能。**P1 做到能力闭环、客户端最小接通，不追求观感**（步骤可见与多会话归 T-M7-03）。
- **依赖：** T-M7-01。
- **P1 / P2 边界（2026-09-18 定型）：** P1 客户端只做 SSE 接收、桥事件转发、`ai.agent` / `ai.cancel` 命令、终态按既有 v2 intent 分发渲染；步骤卡片流、多会话、统一智能模式归 P2。
- **实施：** 详见 `docs/milestones/production-execution-plan.md` T-M7-02——已拆为 02a–02i 九张子卡，含现状基线（四项基建全空白）、已知前提（thinking 延迟、nginx SSE 配置缺失、无 feature flag 基建）与执行顺序闸门。
- **验收：** 多步用例通过（查预算 → 改预算 → 审批 → 执行）；多轮澄清用例通过（问 → 追问 → 简答「500」→ 正确解析）；四条不变式各有对应测试；退出边界触发时优雅终止并说明已完成部分；写工具参数一律经校验，无绕过路径；模型输出 UUID 被拒；审批四值行为正确（`unavailable` 视为拒绝）；多步场景下写操作必出确认卡；旧客户端不调 `/ai/agent` 行为不变；Token 不出 Kotlin 层。
- **子任务：** 已拆分，见生产执行规划（02a 实测闸门 → 02b / 02c → 02d → 02e / 02f → 02g → 02h → 02i）。

#### T-M7-03 客户端瘦渲染与多会话管理（P2）

- **目标：** 客户端从「状态机」瘦身为「流渲染器 + 会话列表」，UX 对标 DeepSeek。
- **依赖：** T-M7-02。
- **范围（2026-09-18 定型）：** P1 已交付流式通道与最小消费；P2 做观感与结构。
- **实施：** `use-assistant.ts` 拆分重构（编排逻辑与渲染态分离。P1 起客户端本就不含 loop / 工具选择逻辑——v2 为服务端 Host 方案，不存在「移除 loop」动作）；步骤卡片流（`phase` / `tool_call` / `tool_result` 事件可见化）；统一智能模式（路径对用户透明，无模式选择）；多会话列表（新建/切换/重命名/删除），`assistant-chat-store` 由单 `messages[]` 扩为按 `conversationId` 分桶并降级为 UI 投影缓存；确认卡对接审批四值；取消/打断交互。
- **实施推进计划：** `docs/milestones/T-M7-03-P2-implementation-plan.md`——已拆为 **03a–03e 五张子卡**，含现状基线（服务端无会话 CRUD 端点、客户端无多会话痕迹、`use-assistant.ts` 1272 行）、批次与并行切分点、承接自 P1 的六项挂账。逐卡细则与验收以 `production-execution-plan.md` T-M7-03 为准。
- **验收：** 步骤卡片流正确渲染（含完成/失败态）；简单任务无步骤展示（统一智能模式）；多会话可新建/切换/删除且互不串扰；取消收敛到一致状态；快路径 12 s / 25 s 体验回归不变。
- **子任务：** 已拆分，见生产执行规划（03a 会话索引服务端化 ∥ 03b 客户端流消费重构 → 03c 步骤卡片流 → 03d 多会话列表 ∥ 03e 确认卡四值与取消收敛 → 收口）。

#### T-M7-04 会话事件日志与摘要压缩（P3）

- **目标：** 会话从「聊天记录」升级为「事件日志 + 派生投影」，获得恢复、分叉、压缩无损与审计能力。
- **依赖：** T-M7-03。
- **实施：** SessionEvent 日志（`type`/`seq`/`time`/`data`/`ignorable`）；Surface 事件子集定义（只有其进模型上下文）；`replace` 压缩操作（原文保留）；摘要生成与注入；会话恢复与分叉；日志保留期与隐私边界（对齐 `D-PROD-08`）。
- **实施推进计划：** `docs/milestones/T-M7-04-P3-implementation-plan.md`——已拆为 **04a–04e 五张子卡**，含现状基线（`ignorable` / `time` / 事件类型全集 / Surface 子集 / 压缩 / 恢复 / 保留期**全部空白**）与承接自 P1 / P2 的十项挂账。逐卡细则与验收以 `production-execution-plan.md` T-M7-04 为准。
- **前置阻塞：已解除（2026-09-19 修正）。** 原登记「`security.md` 绝对禁令须先解决方可开工」——该条款已于 2026-09-18 修订为**有条件例外**（见 `security.md` 修订记录与 `D-PROD-13`）；三条约束中两条已由 02f 落地，残余的**保留期机制**即 04d 的交付内容。
- **验收：** 断连后可按 `conversation_id` 恢复续跑；压缩后原文仍可查（审计）；结构性事件不占 token（实测验证）；摘要不含 ID；日志含用户自然语言的隐私边界有明确结论。
- **子任务：** 已拆分，见生产执行规划（04a 事件日志完整化与 Surface 子集 → 04b 恢复与分叉 ∥ 04d 保留期与隐私边界 → 04c 摘要压缩 ∥ 04e 跨设备同步决策门 → 收口）。

#### T-M7-05 全功能覆盖：域路由、Skill 与设备能力通道（P4）

- **目标：** 达成总目标——对话覆盖 App 全部已有功能（含设备类能力），且新增能力不改 prompt 主体。
- **依赖：** T-M7-04。
- **实施：** 预路由（域选择，**已定为规则词表**——方案对比见 `D-PROD-17` 与 `ADR-011`，T-M7-05g 已落地）；Skill 封装（复用 `PromptTemplate` 机制，按需加载）；设备能力通道（单向指令 / 需结果两类，见架构 §4.3）；`ActionProposal` v2 覆盖全量 CRUD（先改 `ai-architecture.md` + schema + 前后端校验器 + fixtures）；帮助文档检索（可选，仅产品帮助/理财知识）。
- **实施推进计划：** `docs/milestones/T-M7-05-P4-implementation-plan.md`——已拆为 **05a–05i 九张子卡 + 收口**，含现状基线（15 个工具 / 3 域，`device_request` 与 `routing` **均无生产者**，ActionProposal 仍是 v1）、批次与并行切分点，以及**跨期提前开工的切分点**（05a 契约与 05e 协议部分不依赖 P2/P3，可提前开工）。逐卡细则与验收以 `production-execution-plan.md` T-M7-05 为准。
- **验收：** 功能覆盖清单（架构 §3.2）逐项可用；设备类能力（提醒/权限/分享/小组件/语音/图片/图标）在对话内可达；新增一个表单能力不改 prompt 主体的验证用例；ActionProposal v2 三方一致性通过；v1 契约与旧客户端兼容。
- **子任务：** 已拆分，见生产执行规划（05a v2 契约 → 05b 记账/账户账本工具 ∥ 05e 设备通道服务端 → 05c 预算存钱/定时/角色工具 ∥ 05f 设备通道客户端 → 05d 分析与其余域 ∥ 05h Skill 封装 → 05g 域路由 ∥ 05i 帮助检索（可选）→ 收口）。

---

## 10. 全局验收与质量门禁

任何 Task 的局部验收之外，还必须遵守：

### 10.1 数据正确性

- 金额、统计、导出、同步相关任务必须运行对账不变量测试。
- 数据迁移和同步协议变更必须使用旧版本 fixture。
- 任何可能重复入账、金额错误或数据丢失的问题都是 S0，阻塞发布。

### 10.2 测试层级

| 层 | 最低要求 |
|---|---|
| Domain unit | money、date、parser、validators、aggregators |
| Frontend component | 确认卡、快速记账、同步状态、错误和空态 |
| Kotlin unit/instrumented | Room migration、DAO、Bridge、WorkManager、Keystore |
| Backend unit | service、authorization、validation、idempotency |
| Integration | API + real test database + migrations |
| Contract | OpenAPI、ActionProposal、JS Bridge |
| Fuzz/property | 流水、统计、导出、重复/乱序同步 |
| E2E | 登录、记账、离线同步、编辑冲突、换机恢复、删除账号 |

### 10.3 默认生产 SLO

- API 可用性：月度 ≥99.9%。
- 普通业务 API：服务端 p95 ≤500ms，不含 AI。
- 本地保存：p95 ≤300ms。
- AI 快路径（记账 / 查账等单发）：12 秒慢速提示，25 秒远端超时并降级。
- AI agent 路径（多步，M7 起）：**单步** 25 秒**静默**超时并取消——计的是「多久没收到任何数据」，不是「这一步总共跑了多久」（稳定输出的长回答不该被掐掉）；整体不设上限，以步骤可见性与用户随时取消兜底（见 `docs/architecture/ai-agent-architecture.md` §8.3 / §8.5）。
- 同步：网络恢复后 60 秒内开始处理 pending 队列。
- crash-free：≥99.5%。
- 数据备份：RPO ≤24 小时，RTO ≤4 小时。

### 10.4 Task 完成定义

- 需求和架构变化已先更新相关文档。
- 实现只触及任务范围，未顺手重构无关模块。
- 新逻辑有自动化测试，修复有回归用例。
- lint、typecheck、unit、integration 和相关 E2E 全绿。
- 涉及数据时，对账不变量全绿。
- 涉及 AI 时，断网、关闭 AI、超时、非法响应四态通过。
- 涉及 API 时，鉴权、越权、幂等、限流和错误契约通过。
- 涉及 UI 时，晨/昼/暮/夜、小屏、深色、字体 1.3× 完成走查。
- 风险登记册、Task 状态、版本说明同步更新。

---

## 11. Agent 执行任务的上下文加载规则

后续 Agent 不应一次加载全部文档。按以下流程：

1. 阅读 `AGENTS.md`。
2. 阅读本文件第 1–4、9–10 节，以及当前 Task 所属模块。
3. 根据任务类型加载专题文档：

| 任务类型 | 必读文档 |
|---|---|
| 产品需求 | `docs/product/` |
| 数据/API/同步 | 本文件第 5 节、`docs/architecture/data-model.md`、相关 ADR（如已创建） |
| AI（当前契约） | 本文件第 6.1–6.4 节、`docs/architecture/ai-architecture.md` |
| AI（agent 化，M7+） | 本文件第 6.5 节与第 9 节 M7、`docs/architecture/ai-agent-architecture.md`、`docs/adr/ADR-006-ai-agent-architecture.md` |
| 前端/UI | 本文件第 7 节、`docs/design/tokens.md`、视觉提案 HTML |
| Android 原生 | `docs/architecture/overview.md`、相关 Kotlin 文件 |
| 安全/生产 | `docs/architecture/security.md`、`docs/engineering/`、风险登记册 |

4. 开工前明确：Task ID、目标、非目标、依赖、接口、验收命令。
5. 若实现需要改变本文件中的架构、协议或数据不变量，先停止编码，更新文档并记录 ADR。

### 11.1 模块聚焦约束

- Frontend Task 不直接修改数据库 schema；通过契约提出变更。
- Backend Task 不重新设计 AOZORA UI；只提供稳定数据和错误语义。
- AI Task 不直接写账本表；只产出经过验证的 ActionProposal。
- Sync Task 不复制统计结果；只同步源数据和必要设置。
- Native Task 不在 Kotlin 中复制 React 页面业务规则。
- 测试 Task 不通过降低断言、跳过用例或删除 fixture 获得“全绿”。

---

## 12. 已确认的生产决策

用户已于 2026-09-03 确认明确答复的项目，并默许其余项目采用推荐方案。以下决策均为正式生产基线；改变任何一项必须更新本文件和相关 ADR。服务器容量、域名、证书和备案编号属于实施参数，由对应 Task 盘点补录，不视为架构决策未完成。

| ID | 状态 | 决策主题 | 已确认结论 | 影响 Task |
|---|---|---|---|---|
| D-PROD-01 | 已确认 | 服务器、部署方式、数据库 | Linux + Docker + PostgreSQL 18；容量和网络参数由 T-M0-07 盘点 | M0、M2、M6 |
| D-PROD-02 | 已确认 | 首发登录方式 | 游客账号 + 手机验证码绑定；不强制首次登录 | M2-03、首次启动 UI |
| D-PROD-03 | 已确认 | 云端与本地数据权威关系 | 云端权威，Room 本地副本和离线队列 | M1、M3 |
| D-PROD-04 | 已确认 | 票据图片是否保存及保存周期 | 解析后 24 小时删除原图，用户主动关联账单时长期保存 | M4-04、隐私政策 |
| D-PROD-05 | 已确认 | AI 模型与配置位置 | 模型由服务端配置，首发使用 `qwen3.7-flash`；通过 provider adapter 接入 | M4-01 |
| D-PROD-06 | 已确认 | 域名、部署地域、备案状态 | 面向中国大陆；域名、证书和备案作为后续发布任务处理 | M5、M6 |
| D-PROD-07 | 已确认 | 日志、监控与告警平台 | 使用本文件第 4.6 节自建 Grafana 可观测栈方案 | M5-05 |
| D-PROD-08 | 已确认 | 数据保留与账号删除等待期 | 请求后立即冻结，7 天内完成物理删除；法定日志最小化保留 | M5-04 |
| D-PROD-09 | 已确认 | 商业化是否进入首发 | 首发不接支付，只预留 entitlement 和 AI 配额字段 | M2、M4、M6 |
| D-PROD-10 | 已确认 | 首发多设备范围 | 只做协议和数据结构预留；首发不实现多设备同步和实时推送 | M3 |
| D-PROD-11 | 已确认 | 项目与 App 名称 | 唯一正式名称为 `Aozora`；历史名称全部废弃；`小贝`仅保留为 AI 伙伴角色 | M0、M4、M6 |
| D-PROD-12 | 已确认（2026-09-16） | AI Agent 编排位置 | **服务端 Agent Loop + SSE 流式 + 客户端瘦渲染**，配合设备能力通道覆盖设备类能力；三实体（savings/scheduled/personas）上云为硬前提；写操作安全边界不变（ActionProposal + 审批四值） | M7 |
| D-PROD-13 | 已确认（2026-09-18） | Agent 路径的 provider 与推理策略 | agent 路径引入 **DeepSeek `deepseek-flash`**（1M 上下文、tool calls、`reasoning_effort` 三档），现有快路径暂留 DashScope `qwen3.7-flash`；推理按任务复杂度分档（简单关闭、中等 `low`、复杂 `high`，loop 内按 Step 数自动升级）；**会话事件日志记录完整数据**（`security.md` 相应条款放宽，token/票据/账本明细禁令不变）；离线能力收窄为「仅记账可用」**已确认接受**。详见 `docs/adr/ADR-007-deepseek-provider.md` | M7 |
| D-PROD-14 | 已确认（2026-09-19） | 会话正文的跨设备呈现方式 | **正文按需重建，不做双向同步实体**：切到本机无投影的服务端会话时从**服务端事件日志**（`GET /ai/conversations/{id}/events`）重建投影 B；有投影时按落盘的日志游标**增量补齐尾部**，游标未知则原样保留。客户端**从不把投影写回**服务端，因此**不存在冲突策略**。零新协议、零新端点、零服务端改动。「仅摘要同步」被否决（摘要无生产者，且只覆盖超窗轮次）。已知代价：重建结果不含历史只读记账卡（`approval_request` 生产者未接，未指派）。详见 `docs/adr/ADR-008-conversation-cross-device.md` | M7 |
| D-PROD-15 | 已确认（2026-09-19） | ActionProposal v2 的契约版本策略 | **新增文件而非就地改写**：v1 的 `schema.json` 一字不改，v2 是新文件 `schema-v2.json`；**车道判定＝ `contractVersion` 字段在场与否**（v1 关闭 additionalProperties 且无此字段，两条车道互斥且可判定）；v2 用 **op 注册表 + 通用 payload**，**payload 的字段形状由 op 的工具注册表拥有、契约不复制第二份**（代价：客户端不做 payload 级授权，结构通过不是执行许可）；**版本协商由客户端声明驱动**（`/ai/agent` 的 `proposal_contract_version`，缺省 1），服务端无全局开关，回退＝不产 v2。服务端接线与「v1 车道产不出 v2-only op」的落地归 05b。详见 `docs/adr/ADR-009-action-proposal-v2-contract-versioning.md` | M7 |
| D-PROD-16 | 已确认（2026-09-19） | 设备能力通道的服务端协议与 loop 暂停/恢复 | **两类协议**（架构 §4.3）：单向（`kind=one_way`，下发即继续、不回传）与需结果（`kind=result`，loop **挂起** → 客户端执行 → `POST /ai/device-result` 上行回传 → 恢复）。**挂起态进程内**（`devices.DeviceChannel`，单 worker 前提，不建表；代价见 R-PROD-34）；**上行端点的 404 覆盖四种「回不去」的情形**（不存在 / 已回传 / 已超时 / 别人的）——重试安全且不泄漏存在性，别人的 id 也是 404 不是 403；**设备失败是工具级结果**（安全枚举码回喂模型），**不新增 `StopReason`**；**日志事件 `device_request` 仍不接生产者**（下发与结果已由配对的 tool 结果消息留档）；**设备能力单列目录**（`devices.py`，6 个能力）不进 `tools.py` 注册表，`capability`（点号）与工具名（下划线）是两套；**开关默认关闭**（`AI_DEVICE_CHANNEL_ENABLED`），客户端执行器与分派表归 05f。详见 `docs/adr/ADR-010-device-capability-channel.md` | M7 |
| D-PROD-17 | 已确认（2026-09-20） | 域路由（选择注入哪些工具域）的形态与开关策略 | **规则词表，不额外调用模型**（`routing.py`；否决轻量分类器：会给每一轮加一次模型调用，而记账 App 的绝大多数输入是简单输入，且它自己会错、错了仍要兜底）；**判据只有本轮用户输入**（不看历史——拿历史路由会让决策非确定）；**未命中任何信号 = 注入全部域**（= P1 的注入面，误差方向刻意选「宁可多注入、不可少注入」：漏注入＝能力消失，误注入＝选择面变宽）；**一个信号可指向多个域**（`transaction_summary` 与 `stats_monthly` 在「这个月花了多少」上都是正确答案，只给一域就是漏注入）；**`phase: routing` 帧的 `step` 恒为 0**（用 1 会被随后的 `thinking` 覆盖，03c 的占位行就永远不出现），载荷另带 `domains` / `matched`（后者的生产比例就是词表覆盖率）；**不设开关、默认生效**（与设备通道相反：域路由不需要客户端做任何事，且兜底路径就是 P1 的行为——回退＝把返回值固定成全量，一行）；**与入口推理分级是两件事**（架构 §12.2 的易混淆点）：域路由决定**注入哪些工具**，`classify_reasoning` 决定**推理开销**，两者各写各的判据。实测（两臂同会话对跑 58 例）：**选择准确率无差异**（69%/91% 对 69%/91%，逐例 52/58 工具序列逐字相同），收益是**每步工具载荷小 85%**（32,418 → 4,982 字符）与域选择在步骤卡上可见。详见 `docs/adr/ADR-011-domain-routing.md` | M7 |

后续决策变更记录格式：

```text
D-PROD-XX：选择项 / 实际参数
原因：一句话
生效日期：YYYY-MM-DD
影响文档：列出需要更新的文件
```

---

## 13. 文档维护规则

- 本文件只维护长期稳定的产品、架构、模块边界、任务树和全局门禁。
- 单个接口字段、SQL、页面像素和测试步骤放入对应专题规格或 Task 计划，不持续堆入本文件。
- 架构、协议、Schema 或安全边界变化：先更新本文件相关摘要，再更新专题文档并新增 ADR。
- Task 状态变化：在里程碑文档维护，不在本文件逐日记录进度。
- 每个生产版本发布前复查第 3 节当前事实、第 9 节任务树、第 10 节门禁和第 12 节决策。

## 14. 相关权威文档

- 产品愿景：`docs/product/vision.md`
- 产品原则：`docs/product/principles.md`
- 成功指标：`docs/product/success-metrics.md`
- 架构总览：`docs/architecture/overview.md`
- 数据模型：`docs/architecture/data-model.md`
- AI 架构：`docs/architecture/ai-architecture.md`
- 安全设计：`docs/architecture/security.md`
- AOZORA Token：`docs/design/tokens.md`
- 视觉唯一事实源：`docs/uiux-redesign-proposal.html`
- 测试策略：`docs/engineering/testing.md`
- 风险登记册：`docs/risk-register.md`

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-09-03 | Production Baseline 1.0 | 将项目目标从本地 Demo 调整为生产级客户端 + 服务端 + 数据库 + AI 网关架构；建立模块化 Task 与集中决策清单 |
| 2026-09-03 | Production Baseline 1.1 | 正式项目名统一为 Aozora；废弃历史 App 名称 |
| 2026-09-03 | Production Baseline 1.2 | Android namespace/applicationId、Kotlin package、Intent action 与 JS Bridge 统一迁移到 `com.aozora.app` / `AozoraBridge`；明确旧 Demo 数据不自动继承 |
| 2026-09-03 | Production Baseline 1.3 | 服务端技术栈从 Node.js/NestJS/Prisma 切换为 Python/FastAPI/SQLAlchemy+Alembic；项目目录名统一为 `aozora`；Budget 实体扩展为支持年度预算和分类预算 |
