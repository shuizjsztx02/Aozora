# T-M4-03 实现总结 - AI 查询与洞察

## 项目概述

T-M4-03 是 Aozora 记账应用的 AI 查询与洞察功能模块，实现了基于事实包（Fact Pack）的月度财务洞察生成。该模块确保所有 LLM 生成的内容都基于确定性数据，并通过数字校验防止 AI 编造数据。

## 已完成功能

### T-M4-03a: 事实包服务（Fact Pack Service）

**目标**：构建确定性的数据聚合层，为 LLM 提供可信的财务数据上下文

**核心实现**：
- `FactPackService`：聚合账本数据，生成结构化的事实包
- 包含字段：总收入、总支出、净额、交易数、分类明细、Top 消费、预算执行、最大交易、趋势数据
- 数据哈希：基于事实内容生成 SHA256 哈希，用于缓存和版本控制
- 时间范围：支持按月查询，包含 6 个月趋势数据

**文件**：
- `server/src/modules/ai/fact_pack.py`（287 行）
- `server/tests/test_fact_pack.py`（17 个测试）

**测试结果**：17/17 通过
- 哈希稳定性（4 个测试）
- 事实包生成（5 个测试）
- 缓存行为（2 个测试）
- 趋势数据（2 个测试）
- 统计对账守卫（4 个测试：monthly totals / deficit balance / trend / category sum）

---

### T-M4-03b: 叙述生成与数字校验（Narrative Generation & Number Verification）

**目标**：基于事实包生成自然语言叙述，并验证所有数字引用的准确性

**核心实现**：
1. **叙述生成**：
   - LLM 接收事实包作为上下文
   - 生成包含数字引用的 JSON 格式叙述
   - 支持重试机制（JSON 解析失败时）

2. **数字校验**：
   - `verify_narrative()`：验证 LLM 输出的 data_references 与事实包一致
   - `_find_undeclared_numbers()`：检测叙述中未声明的数字（启发式检查）
   - `_build_lookup()`：复用 `build_context_dict()` 确保一致性

3. **安全机制**：
   - 校验失败时丢弃叙述，仅返回事实包
   - 记录安全事件日志
   - 支持降级处理（无 LLM 时返回纯事实包）

**文件**：
- `server/src/modules/ai/narrative.py`（242 行，含代码审查改进）
- `server/src/modules/ai/prompts.py`（新增 REPORT 模板）
- `server/src/modules/ai/schemas.py`（新增报告相关 Schema）
- `server/src/modules/ai/service.py`（新增 `generate_report()` 方法）
- `server/src/modules/ai/routes.py`（新增 `/ai/report` 端点）
- `server/tests/test_narrative_report.py`（20 个测试）

**测试结果**：20/20 通过
- 叙述校验（6 个测试）
- 未声明数字检测（3 个测试）
- 上下文构建（6 个测试）
- 叙述缓存（2 个测试）
- 降级路径 usage 记录（2 个测试：校验失败 + invalid_json 耗尽）
- 限流降级（1 个测试：rate limit → 200 + null narrative）

**代码审查改进**（详见 `T-M4-03b-code-review-improvements.md`）：
- ✅ 消除 `build_context_dict` 和 `_build_lookup` 的代码重复
- ✅ 明确 `_find_undeclared_numbers` 的行为说明
- ✅ 支持负余额（赤字场景），允许 LLM 生成超支洞察
- ✅ 文档化已知局限（年份误报）

---

### T-M4-03c: 客户端洞察展示（Client Insight Display）

**状态**：✅ 已完成（2026-09-06）

**已实现功能**：
1. **AiInsightCard 组件**：
   - 位置：报表页面顶部（月度概览下方）
   - 展示：事实包摘要（交易笔数、最大支出）+ AI 叙述
   - 交互：点击展开/收起完整叙述

2. **API 调用**：
   - 通过 bridge.call('ai.report', { bookId, period }) 调用
   - 超时：30 秒
   - 错误处理：网络错误、provider 不可用

3. **离线降级**：
   - 检测 navigator.onLine 状态
   - 离线时：不发起请求，不渲染卡片
   - bridge 不可用时：静默跳过

4. **缓存机制**：
   - localStorage 存储
   - 键：`${bookId}:${period}`
   - TTL：1 小时（3600000ms）
   - 命中缓存：不调用 bridge，直接渲染

5. **状态管理**：
   - loading：加载中提示
   - error：错误信息展示
   - narrative_cached：已缓存标记

**文件**：
- `app/src/features/reports/AiInsightCard.tsx`（170 行）
- `app/src/__tests__/features/AiInsightCard.test.tsx`（10 个测试）
- `app/src/features/reports/ReportsPage.tsx`（集成）

**测试结果**：10/10 通过
- 无 bookId 不渲染
- 加载状态
- 事实包摘要展示
- AI 叙述展示
- 错误状态
- 缓存命中不请求
- 缓存过期重新请求
- 离线不请求
- bridge 不可用不请求
- 已缓存标记

---

## API 设计

### POST /api/v1/ai/report

生成月度财务洞察报告

**请求**：
```json
{
  "book_id": "uuid-string",
  "period": "2024-06"
}
```

**响应**（有 LLM）：
```json
{
  "request_id": "uuid-string",
  "book_id": "uuid-string",
  "period": "2024-06",
  "data_hash": "sha256-string",
  "total_income": 1000000,
  "total_expense": 800000,
  "balance": 200000,
  "transaction_count": 42,
  "expense_by_category": [...],
  "income_by_category": [...],
  "budget": {...},
  "largest_expense": {...},
  "largest_income": {...},
  "trend": [...],
  "narrative": "当月收支情况良好...",
  "narrative_cached": false,
  "usage": {
    "prompt_tokens": 500,
    "completion_tokens": 200
  }
}
```

**响应**（无 LLM 或校验失败）：
```json
{
  "request_id": "uuid-string",
  "book_id": "uuid-string",
  "period": "2024-06",
  "data_hash": "sha256-string",
  "total_income": 1000000,
  ...
  "narrative": null,
  "narrative_cached": false,
  "usage": null
}
```

---

## 数据流

```
用户请求
  ↓
[API Route] POST /ai/report
  ↓
[AIService.generate_report()]
  ├─ [FactPackService.build()]
  │   └─ 聚合账本数据 → FactPack
  │   └─ 计算 data_hash
  │   └─ 检查缓存
  │
  ├─ [LLM Provider 可用?]
  │   ├─ 是 → [AIService._generate_narrative()]
  │   │       ├─ build_context_dict(fact_pack)
  │   │       ├─ LLM 调用（JSON 格式）
  │   │       ├─ verify_narrative(llm_output, fact_pack)
  │   │       │   ├─ 检查 data_references
  │   │       │   └─ 检查未声明数字
  │   │       ├─ 校验通过 → 返回叙述
  │   │       └─ 校验失败 → 丢弃叙述，记录安全事件
  │   │
  │   └─ 否 → 返回 null 叙述
  │
  └─ 返回 AIReportResponse
      ├─ 事实包数据（始终返回）
      └─ 叙述（可选）
```

---

## 安全特性

1. **数据隔离**：LLM 仅接收事实包，无法访问原始数据库
2. **数字溯源**：所有 LLM 输出的数字必须通过 data_references 声明
3. **校验失败降级**：数字不匹配时丢弃叙述，仅返回事实包
4. **速率限制**：复用 T-M4-01 的速率限制机制
5. **安全日志**：校验失败记录 WARNING 级别日志

---

## 测试覆盖

### 单元测试

| 模块 | 测试数 | 覆盖点 |
|------|--------|--------|
| 事实包生成 | 17 | 哈希稳定性(4)、数据聚合(5)、缓存(2)、趋势(2)、统计对账守卫(4) |
| 叙述校验 | 6 | 有效/无效引用、未知键、预算引用、多重错误、空引用 |
| 未声明数字 | 3 | 无未声明、检测到未声明、小数字忽略 |
| 上下文构建 | 6 | 基础字段、Top分类、预算、最大交易、趋势、无预算 |
| 叙述缓存 | 2 | 同数据不重复调用、不同数据触发新调用 |
| 降级路径 usage | 2 | 校验失败记录 usage、invalid_json 耗尽记录 usage |
| 限流降级 | 1 | rate limit → 200 + null narrative |
| **总计** | **37** | **全部通过（第 8 轮实测）** |

### 集成测试

- `test_fact_pack.py::test_budget_execution`：端到端测试 POST /ai/report
- 验证事实包生成和 API 响应

---

## 性能优化

1. **事实包缓存**：
   - 内存缓存，TTL 5 分钟
   - 基于 `data_hash` 去重
   - 最大缓存条目：500

2. **数据库查询**：
   - 使用 SQLAlchemy 异步查询
   - 批量聚合，减少查询次数
   - 索引优化（book_id, biz_date）

3. **LLM 调用**：
   - JSON 格式约束，减少 token 消耗
   - 重试机制（最多 2 次）
   - 校验失败时快速降级

---

## 文件清单

### 新增文件

```
server/src/modules/ai/
├─ fact_pack.py              (287 行)
└─ narrative.py              (242 行)

server/tests/
├─ test_fact_pack.py         (280 行)
└─ test_narrative_report.py  (230 行)

docs/
└─ T-M4-03-implementation-summary.md
```

### 修改文件

```
server/src/modules/ai/
├─ prompts.py       (+30 行, REPORT 模板)
├─ schemas.py       (+70 行, 报告 Schema)
├─ service.py       (+90 行, generate_report)
└─ routes.py        (+20 行, /ai/report 端点)
```

### 总代码量

- **新增代码**：约 1040 行
- **测试代码**：约 510 行
- **文档**：约 200 行
- **总计**：约 1750 行

---

## 依赖关系

```
T-M4-03 (AI 查询与洞察)
├─ T-M4-01 (AI Gateway) ✅
│   ├─ LLM Provider 抽象
│   ├─ 速率限制
│   └─ 使用量追踪
│
├─ T-M2-05 (统计服务) ✅
│   └─ _YearMonth 跨数据库方言函数
│
└─ T-M4-03a (事实包) ✅
    └─ T-M4-03b (叙述生成) ✅
        └─ T-M4-03c (客户端展示) ✅
```

---

## 已知问题与限制

### 已解决

1. **P2 - 代码重复**：✅ 已重构，`_build_lookup` 复用 `build_context_dict`
2. **P2 - 未声明数字检测行为**：✅ 已文档化，明确说明设计意图
3. **P3 - 负余额支持**：✅ 已修复，允许 LLM 生成超支洞察

### 已知局限

1. **年份误报**：`_find_undeclared_numbers` 可能将年份（如 2024）标记为未声明数字
   - 影响：低，LLM 被指示使用 data_references
   - 缓解：主检查（data_references 验证）不受影响

2. **客户端缓存**：✅ 已实现（T-M4-03c）
   - localStorage 存储，键 `${bookId}:${period}`，TTL 1 小时
   - 离线降级：`navigator.onLine` 检测，离线不请求

---

## 后续工作

### 其他优化

1. **监控指标**：
   - 叙述生成成功率
   - 数字校验失败率
   - 缓存命中率

2. **性能优化**：
   - 事实包缓存切换到 Redis（多实例部署）
   - LLM 调用批处理

3. **功能增强**：
   - 支持自定义洞察模板
   - 支持多维度分析（周、季度、年）
   - 支持洞察分享功能

---

## 验证清单

### 功能验证

- [x] 事实包正确聚合账本数据
- [x] 数据哈希稳定且唯一
- [x] LLM 生成符合 JSON 格式要求
- [x] 数字校验检测错误引用
- [x] 未声明数字检测生效
- [x] 校验失败时降级处理
- [x] API 端点返回正确响应
- [x] 无 LLM 时返回纯事实包

### 安全验证

- [x] LLM 无法访问原始数据库
- [x] 数字溯源验证生效
- [x] 速率限制正常工作
- [x] 安全日志记录完整

### 性能验证

- [x] 事实包缓存生效
- [x] 数据库查询优化
- [x] LLM 调用重试机制

### 测试验证

- [x] 37 个任务级单元测试全部通过（第 8 轮实测）
- [x] 426 个全量服务端测试通过（第 8 轮实测，2 skipped / 0 error）
- [x] 473 个客户端测试通过（第 8 轮实测）
- [x] 无回归问题

---

## 总结

T-M4-03（T-M4-03a + T-M4-03b）已成功实现，提供了基于事实包的 AI 财务洞察生成功能。该模块具有以下特点：

1. **数据可靠性**：所有 LLM 输出都基于确定性事实包，并通过数字校验
2. **安全性**：数据隔离、速率限制、安全日志
3. **可维护性**：代码结构清晰，测试覆盖完整
4. **可扩展性**：为 T-M4-03c 客户端展示预留接口

**完成度**：100%

**质量评估**：✅ 可交付

**后续优化方向**：
- 监控指标：叙述生成成功率、缓存命中率
- 性能优化：事实包缓存切换到 Redis（多实例部署）
- 功能增强：支持自定义洞察模板、多维度分析
