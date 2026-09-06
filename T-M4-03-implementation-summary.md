# T-M4-03 实现总结：AI 查询与洞察

## 概述
T-M4-03 实现了基于事实包（Fact Pack）的 AI 月度报告生成功能，确保所有 LLM 生成的数字都可追溯到确定性数据源。

## 实现内容

### T-M4-03a: 事实包服务 (Fact Pack Service)
**文件**: `server/src/modules/ai/fact_pack.py`

**核心功能**:
- `FactPack` 数据类：包含月度财务数据的确定性数据包
  - 总收入/支出/结余
  - 分类统计（支出/收入）
  - 预算执行情况
  - 最大单笔交易
  - 6个月趋势数据
  - SHA-256 数据哈希（用于缓存）

- `FactPackService` 服务类：
  - `build()`: 从数据库构建事实包
  - `_query_totals()`: 查询月度汇总
  - `_query_category_breakdown()`: 分类统计
  - `_query_budget()`: 预算执行
  - `_query_largest_tx()`: 最大交易
  - `_query_trend()`: 趋势数据（复用 StatisticsService 的 `_YearMonth`）

- 内存缓存机制：
  - TTL: 300秒
  - 最大条目: 500
  - 基于 `book_id:period:data_hash` 的缓存键

**测试**: `tests/test_fact_pack.py` (13个测试)
- 哈希稳定性测试
- 事实包生成测试
- 缓存测试
- 趋势测试

### T-M4-03b: 叙述生成与数字校验 (Narrative Generation & Number Verification)
**文件**: 
- `server/src/modules/ai/narrative.py`
- `server/src/modules/ai/prompts.py` (修改)
- `server/src/modules/ai/schemas.py` (修改)
- `server/src/modules/ai/service.py` (修改)
- `server/src/modules/ai/routes.py` (修改)

**核心功能**:

1. **数字校验** (`narrative.py`):
   - `verify_narrative()`: 校验 LLM 输出的 `data_references` 是否与事实包一致
   - `_find_undeclared_numbers()`: 检测叙述中未声明的数字（使用 `(?<!\d)(\d{3,})(?!\d)` 正则支持中文）
   - `_build_lookup()`: 从事实包构建键值查找表

2. **上下文构建** (`narrative.py`):
   - `build_context_dict()`: 从事实包提取扁平化键值对，作为 LLM 的唯一数据源
   - 包含：总收入/支出、Top分类、预算信息、最大交易、趋势方向等

3. **Prompt 模板** (`prompts.py`):
   - 添加 `REPORT` 模板（task_id="monthly_report"）
   - 系统提示：要求 LLM 生成 JSON，包含 `narrative`, `highlights`, `data_references`
   - 严格规则：只使用事实数据中的数字，每个数字必须引用事实键

4. **API Schema** (`schemas.py`):
   - `AIReportRequest`: book_id, period (YYYY-MM)
   - `AIReportResponse`: 事实包字段 + 可选的 narrative, narrative_cached, usage
   - `CategoryFactSchema`, `BudgetFactSchema`, `TransactionFactSchema`, `TrendFactSchema`

5. **服务层** (`service.py`):
   - `AIService.generate_report()`: 
     - 构建事实包
     - 如果 provider 可用，调用 LLM 生成叙述
     - 数字校验失败时丢弃叙述，记录安全事件
   - `AIService._generate_narrative()`:
     - 调用 LLM，解析 JSON 输出
     - 调用 `verify_narrative()` 校验
     - 校验失败：记录 warning，不返回叙述
     - 校验成功：记录 usage，返回叙述

6. **路由** (`routes.py`):
   - `POST /ai/report`: 生成月度报告
   - 依赖注入：db, provider, current_user

**测试**: `tests/test_narrative_report.py` (15个测试)
- 叙述校验测试（有效/无效引用、未知键、预算引用、多重错误）
- 未声明数字检测测试
- 上下文构建测试（基础字段、Top分类、预算、最大交易、趋势）

### T-M4-03c: 客户端洞察展示（未实现）
**状态**: 待实现

**计划功能**:
- 在 ReportsPage.tsx 添加 AI 洞察卡片
- 调用 `POST /api/v1/ai/report` 获取报告和叙述
- 离线降级：无网络时显示纯本地统计
- 缓存机制：避免重复请求（基于 data_hash）

## 关键技术决策

1. **事实包模式**: LLM 只能看到事实包中的数据，不能访问原始数据库
2. **数字校验**: 每个 LLM 生成的数字必须通过 `data_references` 追溯到事实包
3. **安全降级**: 校验失败时丢弃叙述，只返回事实包（不暴露错误）
4. **缓存策略**: 基于数据哈希的内存缓存，TTL 5分钟
5. **跨数据库兼容**: 复用 StatisticsService 的 `_YearMonth` 函数，支持 SQLite 和 PostgreSQL

## 测试结果

**总计**: 297 个测试通过
- T-M4-03a (Fact Pack): 13 个测试
- T-M4-03b (Narrative): 15 个测试
- 其他模块: 269 个测试

## 修复的问题

1. **正则表达式兼容中文**: 
   - 问题: `\b` 不匹配中文字符和数字之间的边界
   - 修复: 使用 `(?<!\d)(\d{3,})(?!\d)` 数字边界断言

2. **UUID 类型转换**:
   - 问题: SQLAlchemy 需要 UUID 对象，不是字符串
   - 修复: 在测试中使用 `uuid.UUID()` 转换

3. **API 路径前缀**:
   - 问题: 测试环境中 `API_PREFIX=""`，路由注册在根路径
   - 修复: 使用 `/ai/report` 而不是 `/api/v1/ai/report`

## 安全特性

1. **数字溯源**: 所有 LLM 输出的数字必须引用事实包
2. **未声明数字检测**: 防止 LLM 编造事实包中不存在的数字
3. **安全事件日志**: 校验失败时记录 security-level 事件
4. **降级策略**: 校验失败时不返回叙述，只返回事实包

## 性能优化

1. **数据哈希缓存**: 相同数据不重复调用 LLM
2. **内存缓存**: 5分钟 TTL，最多 500 条目
3. **惰性加载**: 只在需要时构建事实包和生成叙述

## 后续工作

1. **T-M4-03c**: 实现客户端 AI 洞察卡片和离线降级
2. **Redis 缓存**: 多实例部署时替换内存缓存
3. **叙述缓存**: 缓存 LLM 生成的叙述，避免重复生成
4. **监控指标**: 添加叙述校验失败率、缓存命中率等指标

## 文件清单

**新增**:
- `server/src/modules/ai/fact_pack.py` (286行)
- `server/src/modules/ai/narrative.py` (225行)
- `tests/test_fact_pack.py` (280行)
- `tests/test_narrative_report.py` (230行)

**修改**:
- `server/src/modules/ai/prompts.py` (+30行)
- `server/src/modules/ai/schemas.py` (+70行)
- `server/src/modules/ai/service.py` (+90行)
- `server/src/modules/ai/routes.py` (+20行)

**总计**: 约 1200 行代码（含测试）
