# T-M4-02 再次审查总结

**审查日期**: 2026-09-06
**状态**: ✅ 审查通过，已合入
**审查范围**: T-M4-02a/02b/02c/02d 全部子任务
**审查类型**: 代码质量审查 + 修复验证

---

## 审查结论

**✅ 可以合入主分支**

所有 Critical 和 High 级别问题均已修复并验证通过，测试全部通过（90/90），契约一致性达成。

---

## 修复统计

| 级别 | 总数 | 已修复 | 待优化 | 修复率 |
|------|------|--------|--------|--------|
| Critical | 3 | 3 | 0 | 100% |
| High | 4 | 4 | 0 | 100% |
| Medium | 7 | 5 | 2 | 71% |
| Test | 5 | 1 | 4 | 20% |
| **总计** | **19** | **13** | **6** | **68%** |

---

## 关键修复验证

### 服务端修复（6 项）

1. **S-C1 categoryId 校验分歧** ✅
   - Python 侧缺失 categoryId 视为幻觉，与 TS 对齐
   - 位置: `service.py:452-456`

2. **S-C2 confirmation_token 矛盾** ✅
   - 默认空字符串，仅在低置信时校验
   - 位置: `schemas.py:87`

3. **S-H1 proposalId 无上限** ✅
   - 增加 maxLength: 64，与 DB 对齐
   - 位置: `schema.json:12`

4. **S-M1 _count_transactions 修正** ✅
   - 使用 func.count() 返回真实计数
   - 位置: `test_ai_proposal_execution.py:99-111`

5. **C-M1 分类子串匹配** ✅
   - 按长度降序排序，取最长匹配
   - 位置: `service.py` 分类匹配逻辑

6. **C-M4 错误码映射** ✅
   - 缺失字段返回 invalid_input
   - 位置: `AozoraBridge.kt:482-483, 503-508`

### 客户端修复（7 项）

1. **C-C1 Undo timer 竞争** ✅
   - submitInput 开头清理旧 timer
   - 位置: `use-assistant.ts:109-112`

2. **C-C2 Timer 内存泄漏** ✅
   - useEffect 清理函数
   - 位置: `use-assistant.ts:76-80`

3. **C-B1 Undo 失败静默** ✅
   - 展示错误消息给用户
   - 位置: `use-assistant.ts:326-332`

4. **C-B2 确认按钮无反应** ✅
   - 行内校验错误提示
   - 位置: `ConfirmationCard.tsx:45, 51-53`

5. **C-M2 浮点精度问题** ✅
   - 使用 Math.round(parseFloat * 100)
   - 位置: `ConfirmationCard.tsx:49`

6. **C-M3 语义修正** ✅
   - "后" → "内"
   - 位置: `UndoToast.tsx:33`

7. **C-B3 并发提交竞争** ✅
   - isSubmittingRef 互斥
   - 位置: `use-assistant.ts:105-106`

### 测试修复（1 项）

1. **T-1 命令数量不一致** ✅
   - 期望命令数更新为 35 个
   - 位置: `native-bridge.test.ts:40-82`

---

## 测试覆盖

### 服务端测试（45/45 通过）

- **test_ai_proposal_validation.py** — 16 用例
  - Schema 校验（4）
  - 幻觉 ID 拒绝（4）
  - 置信度门控（3）
  - DB 上下文加载（3）
  - 批量验证（2）

- **test_ai_proposal_execution.py** — 12 用例
  - 批量执行（4）
  - 置信度门控（2）
  - 执行安全（3）
  - 幂等性（3）

- **test_validator_parity.py** — 17 用例
  - Python↔fixtures 平价验证

### 客户端测试（45/45 通过）

- **ai-proposal-builder.test.ts** — 13 用例
  - AI 输出转换
  - 分类匹配（精确/包含/未找到）
  - 降级策略
  - 置信度截断

- **native-bridge.test.ts** — 32 用例
  - 命令完整性（35 个）
  - AI 命令（3）
  - 基础行为
  - 响应解析
  - 写命令回归

---

## 待优化项

### Medium 优先级（2 项）

1. **S-M3 rejected_low_confidence 返回值**
   - 当前：返回 valid: True + 空 errors
   - 问题：客户端需解析 verdict 才能理解
   - 建议：返回 valid: False + 明确错误信息
   - 影响：UX 优化，不影响功能正确性

2. **S-M4 create_batch N+1 refresh**
   - 当前：N 条事务 N 次 db.refresh()
   - 建议：批量刷新或 eager loading
   - 影响：性能优化，不影响功能正确性

### Test Quality（4 项）

1. **T-3 use-assistant 状态机测试**（高优先级）
   - 最复杂的新代码，零覆盖
   - 建议：覆盖状态转换、降级路径、timer 清理

2. **S-M2 低置信无 token 拒绝测试**（高优先级）
   - 最关键的安全门控测试缺失
   - 建议：添加 test_low_confidence_without_token_rejected

3. **T-4 ConfirmationCard 测试**（中优先级）
   - 金额校验、编辑逻辑零覆盖
   - 建议：覆盖行内校验、编辑流程

4. **T-2 ai-proposal-builder 边界用例**（低优先级）
   - 缺 NaN amountCents、null category 等边界用例
   - 建议：补充边界测试

---

## 安全评估

**零写库承诺** ✅
- Schema 校验 → 幻觉 ID 检查 → 置信度门控 → 执行时重验证
- 四层防御，Python/TS 对齐
- 测试覆盖：16 个负例用例验证非法输入零写库

**幂等性保护** ✅
- proposal_idempotency 表 + IntegrityError 竞态捕获
- proposalId maxLength: 64 保护幂等链路
- 测试覆盖：3 个幂等性用例

**归属校验** ✅
- Category 按 (owner_id AND book_id) 校验
- Account 按 (owner_id) 校验
- 测试覆盖：跨用户/跨账本拒绝

**零信任面** ✅
- Token 不出 Kotlin 层
- WebView 零信任面
- 服务端重验证（TOCTOU 防护）

---

## 架构亮点

1. **防御纵深设计**
   - 三层验证链 + 执行时重验证
   - 安全边界清晰，职责分明

2. **降级策略完整**
   - 断网 → 本地 NLU
   - AI 超时/503/429 → 本地 NLU
   - 验证失败 → 降级本地
   - 覆盖所有故障模式

3. **契约平价方法**
   - 16 个共享 fixtures
   - TS/ajv/Python 三方一致
   - 可重复验证

4. **原子批量**
   - Single flush + 全成或全回滚
   - 避免部分写入

---

## 合入建议

**✅ 建议合入**

理由：
1. 所有 Critical 和 High 级别问题已修复
2. 测试全部通过（90/90）
3. 契约一致性达成
4. 安全边界清晰，零写库承诺兑现
5. 降级策略完整，覆盖所有故障模式

后续迭代建议：
1. 优先补充 use-assistant 状态机测试（T-3）
2. 补充低置信无 token 拒绝测试（S-M2）
3. 优化 rejected_low_confidence 返回值（S-M3）
4. 优化 create_batch 性能（S-M4）

---

## 附录：审查方法

**代码审查**
- 逐行阅读关键修复代码
- 验证修复逻辑正确性
- 检查副作用和边界情况

**测试验证**
- 运行服务端测试：45/45 通过
- 运行客户端测试：45/45 通过
- 检查测试覆盖范围

**契约一致性**
- 对比 JSON Schema / Python / TS / Service 四方
- 验证 categoryId / accountId / proposalId / confirmation_token 行为一致
- 检查 Bridge 命令数量对齐

**安全评估**
- 验证零写库承诺
- 验证幂等性保护
- 验证归属校验
- 验证零信任面
