 # Aozora · 项目地图
 
 **产品摘要：** Aozora 是面向 20-30 岁一二线城市职场新人的 AI-native 记账 App。"不用认真记账，也知道钱花哪了。" 核心差异：AI 对话即操作（AI 率 > 40%）、情感陪伴人格（小贝）、离线可用的云端生产架构、四时天空视觉体系。
 
 **技术栈：** Kotlin WebView 壳 + React 19 (Vite 8 + Tailwind 4 + Zustand + Framer Motion + Recharts) + Python 3.12 / FastAPI / PostgreSQL 18 / SQLAlchemy + Alembic（`server/`，T-M2-02 Schema 已就绪）+ 服务端 AI Gateway
 
 **关键架构原则：**
 - 一切操作由 AI 驱动，手动表单仅为兜底（PR-1）
 - 双引擎设计：服务端 LLM 理解 + 本地规则 NLU 兜底，断网核心记账可用
 - JSON 动作协议：AI 只输出约定 JSON，金额与统计永远由客户端或服务端的确定性业务代码执行
 - 金额以整数「分」存储运算（NFR-7）
 - 登录用户数据以服务端数据库为权威，客户端 Room 保存本地副本与离线队列
 - LLM 服务商密钥仅保存在服务端，禁止写入前端资源或 APK
 
 **关键工程约束：**
 - 数据正确性一票否决：对账不变量 fuzz 必须全绿
 - 每次修复附带回归用例（vitest / NLU 用例集）
 - 架构/协议/数据结构变更必须先改对应文档再动代码
 - 任务必须有里程碑编号（T-M*-**）
 - 修改 `.yml` 文件时定位精准、最小改动，修改后必须做 YAML 语法校验
 
 **文档地图：**
 | 位置 | 内容 | 谁在什么时候读 |
 |---|---|---|
 | `docs/APP-CONTEXT.md` | 生产级产品目标、总体架构、模块、Task、验收和集中决策 | **所有开发任务在 AGENTS.md 后优先阅读** |
 | `docs/product/` | 愿景、用户、原则、非目标、指标 | 立项、需求评估、对外沟通 |
 | `docs/architecture/` | 架构总览、数据模型、AI 协议、安全设计 | 任何架构/数据结构改动前必读 |
 | `docs/design/` | 青空设计令牌、组件规则、交互原则 | M2 视觉实施、UI 任何改动 |
 | `docs/engineering/` | 约束、测试策略、构建流水线 | 动工具链/测试框架/发版流程时 |
 | `docs/milestones/` | 里程碑计划（M0–M7）、生产执行规划与各期实施推进计划 | 每个开发阶段开工前 |
 | `docs/adr/` | 架构决策记录 | 理解历史决策原因时 |
 | `docs/risk-register.md` | 已知风险/缺陷与状态 | 每次发版前对照检查 |
 
 **当前阶段：** **M7（AI Agent 能力升级）优先推进，M6（生产发布）滞后**至 M7 收口之后（2026-09-18 决策——M7 是对 App 功能的重大新增，须在正式发布前完成）。M1–M5 已完成。M7 内部：**T-M7-01**（三实体上云）、**T-M7-02**（P1 服务端 Agent Loop 与 SSE 流式通道）、**T-M7-03**（P2 客户端瘦渲染与多会话管理）、**T-M7-04**（P3 会话事件日志与摘要压缩——04a–04e 五张子卡 + 批次 4 收口，含真实断连恢复走查）、**T-M7-05**（P4 全功能覆盖——05a–05j 十张子卡，批次 6 于 2026-09-20 收口）**均已交付并收口**；M7 唯一未闭合的出口面条目是一张尾卡，覆盖 R-PROD-31 / R-PROD-37 / R-PROD-38，边界与验收见 `docs/review/T-M7-ai-agent-issues-2026-09-20.md` §四。**并行需求线：第二十一轮 REQ-83（自动记账）T21-1…T21-9 已全部交付并收口**（2026-10-19 收口）——双通道采集（无障碍只读 + 通知监听）、Room v12 `auto_captures`、桥 83→87、设置页双授权、待确认箱均落地；收口时真机回归查出并修复两处缺陷（无障碍只读事件文本导致零采集、无障碍 `postedAt` 用 `uptimeMillis` 致 L2 跨通道合并永不命中）。残余挂账见 `docs/人工测试和需求变更-v3.md`〈第二十一轮修复说明〉§5 与 `docs/risk-register.md` R-PROD-58…63。任务边界与验收以 `docs/APP-CONTEXT.md` 第 9 节和 `docs/milestones/production-execution-plan.md` 为准，逐期进度看板见 `docs/milestones/T-M7-0*-implementation-plan.md`。

**M8 已交付并收口（2026-11-10）：** 运营可观测后台（AI 交互回放台），需求来源第二十二轮 REQ-84。十一卡全部完成，已上线测试实例：`http://116.62.196.255/aozora-api/aozora-admin/`。**M8 排在 M6 之前。** 架构见 `docs/architecture/admin-console.md`（§9 为已锁定决策），看板见 `docs/milestones/T-M8-admin-console-plan.md`。**不走 OTel/Prometheus/Grafana**，数据面为 PostgreSQL 事件日志。运行约束：**A 机 nginx 属服务器管理员、不得改动**——后台由 FastAPI `StaticFiles` 托管并复用 `/aozora-api/` 反代；全部迁移集中在 8-03；后台暂停**不得复用** `UserStatus.FROZEN`；新增写端点必须显式挂 `require_not_suspended`（R-PROD-70）。
 
 **上下文加载指令：** 不要预加载所有项目文档。先读本文件和 `docs/APP-CONTEXT.md` 的相关章节，然后仅加载与当前任务相关的文档。修改涉及需求 → 读 `docs/product/`；涉及架构/数据 → 读 `docs/architecture/` 和 `docs/adr/`；涉及 UI → 读 `docs/design/`；涉及工程流程 → 读 `docs/engineering/`。

 **需求变更规范：** 处理任何新增需求、已有需求修改或改版前，先遵循 `docs/engineering/需求变更规范.md`：建立带里程碑编号的需求卡，按变更级别完成影响分析、必要的文档/ADR 更新、兼容与回退设计、回归验证和交付记录。小型单点改动可简化记录，但不得跳过相关验证。

 **通用任务执行规范：** 开始开发实现、代码审查或审查问题修复前，必须读取并遵循 `docs/engineering/通用任务执行规范.md`。以下指令属于执行型口令，默认执行对应流程直至文档更新、提交、推送和 CI 状态确认，不需要用户重复完整步骤：

 - `开发实现 T-Mx-xx`、`执行 T-Mx-xx`：使用“开发实现模式”。
 - `审查 T-Mx-xx`、`Review T-Mx-xx`：使用“Review 模式”；只分析和验证，不修改项目代码、测试或配置，审查记录及状态文档仍需提交并推送。
 - `修复 R-Mx-xx`、`修复 Mx review`、`bug-fix`：使用“Bug-fix 模式”。

 当前用户指令始终优先；若用户明确要求只分析、不要提交、不要推送或缩小范围，按本次要求执行。仅询问方案、解释、状态或建议时，不得自动进入执行模式。
 
 **AI 协作稳定性守则：** 本仓库由 Agent 承担主要编码工作。开始任何改动前，除上述两份规范外还须遵循 `docs/engineering/AI协作稳定性守则.md`——它规定稳定性护栏（四层防线、跨层契约逐处守护、回归用例反向验证、清理通道、多会话并行纪律）与开工 / 交付检查清单。三份规范的分工：稳定性守则回答「为什么不会退化」，需求变更规范回答「变更多大要走多重流程」，通用任务执行规范回答「具体怎么执行」。
 
 **品牌与命名：** 唯一正式项目名和 App 名为 `Aozora`，Android namespace/applicationId 为 `com.aozora.app`，JS Bridge 为 `AozoraBridge`。`BestBills`、`best-bills`、`青空记账`、`小贝账本`、`小贝记账` 均为废弃历史名称，不得用于新增代码、文档或用户界面；`小贝`仅保留为 AI 伙伴角色名。旧数据键和签名标识仅允许用于迁移兼容。
