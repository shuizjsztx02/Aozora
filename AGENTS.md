 # Aozora · 项目地图
 
 **产品摘要：** Aozora 是面向 20-30 岁一二线城市职场新人的 AI-native 记账 App。"不用认真记账，也知道钱花哪了。" 核心差异：AI 对话即操作（AI 率 > 40%）、情感陪伴人格（小贝）、离线可用的云端生产架构、四时天空视觉体系。
 
 **技术栈：** Kotlin WebView 壳 + React 19 (Vite 8 + Tailwind 4 + Zustand + Framer Motion + Recharts) + Python 3.12 / FastAPI / PostgreSQL 18 / SQLAlchemy + Alembic（`server/`，T-M2-02 Schema 已就绪）+ 服务端 AI Gateway + Vosk 离线语音
 
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
 - 修改 `.yml` 文件（Dify 智能体）时定位精准、最小改动，修改后必须做 YAML 语法 + Dify DSL 结构校验
 
 **文档地图：**
 | 位置 | 内容 | 谁在什么时候读 |
 |---|---|---|
 | `docs/APP-CONTEXT.md` | 生产级产品目标、总体架构、模块、Task、验收和集中决策 | **所有开发任务在 AGENTS.md 后优先阅读** |
 | `docs/product/` | 愿景、用户、原则、非目标、指标 | 立项、需求评估、对外沟通 |
 | `docs/architecture/` | 架构总览、数据模型、AI 协议、安全设计 | 任何架构/数据结构改动前必读 |
 | `docs/design/` | 青空设计令牌、组件规则、交互原则 | M2 视觉实施、UI 任何改动 |
 | `docs/engineering/` | 约束、测试策略、构建流水线 | 动工具链/测试框架/发版流程时 |
 | `docs/milestones/` | 里程碑计划（M0-M6） | 每个开发阶段开工前 |
 | `docs/adr/` | 架构决策记录 | 理解历史决策原因时 |
 | `docs/risk-register.md` | 已知风险/缺陷与状态 | 每次发版前对照检查 |
 
 **当前阶段：** 生产化架构重基线。先执行 `docs/APP-CONTEXT.md` 的 M0 决策与文档任务，再进入客户端、服务端、同步和 AI 实施；旧 `docs/milestones/overview.md` 在 T-M0-08 中同步重写。
 
 **上下文加载指令：** 不要预加载所有项目文档。先读本文件和 `docs/APP-CONTEXT.md` 的相关章节，然后仅加载与当前任务相关的文档。修改涉及需求 → 读 `docs/product/`；涉及架构/数据 → 读 `docs/architecture/` 和 `docs/adr/`；涉及 UI → 读 `docs/design/`；涉及工程流程 → 读 `docs/engineering/`。

 **需求变更规范：** 处理任何新增需求、已有需求修改或改版前，先遵循 `docs/engineering/需求变更规范.md`：建立带里程碑编号的需求卡，按变更级别完成影响分析、必要的文档/ADR 更新、兼容与回退设计、回归验证和交付记录。小型单点改动可简化记录，但不得跳过相关验证。
 
 **品牌与命名：** 唯一正式项目名和 App 名为 `Aozora`，Android namespace/applicationId 为 `com.aozora.app`，JS Bridge 为 `AozoraBridge`。`BestBills`、`best-bills`、`青空记账`、`小贝账本`、`小贝记账` 均为废弃历史名称，不得用于新增代码、文档或用户界面；`小贝`仅保留为 AI 伙伴角色名。旧数据键和签名标识仅允许用于迁移兼容。
