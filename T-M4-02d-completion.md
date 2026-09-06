# T-M4-02d 完成报告

**任务：** 父任务契约门禁回归  
**状态：** ✅ 已完成（CI 集成）  
**完成时间：** 2026-09-06

## 验收标准

> T-M4-02 父任务验收命令全绿，按交接模板补父任务完成记录

## 实施内容

### 1. CI 集成（已实现）

**`.github/workflows/ci.yml`:**
- 新增 `server` job，运行 Python 3.13
- 执行 `ruff check` 和 `pytest tests/`
- 覆盖所有 AI 模块测试（T-M4-01/02/03）

**测试结果：**
```
T-M4-01: 46 passed (test_ai_gateway.py)
T-M4-02a: 16 passed (test_ai_proposal_validation.py)
T-M4-02b: 12 passed (test_ai_proposal_execution.py，含 3 幂等)
T-M4-02d: 17 passed (test_validator_parity.py，Python↔fixtures 平价)
T-M4-03a: 13 passed (test_fact_pack.py)
总计: 104 passed
```

### 2. 负例用例覆盖（已实现）

**非法 JSON → 零写库：**
- `test_invalid_schema_rejected` — 缺失必填字段
- `test_invalid_date_format_rejected` — 无效日期（2026-02-30）
- `test_negative_amount_rejected` — 负金额

**幻觉数字/无权限分类 → 零写库：**
- `test_unauthorized_category_rejected` — 未授权 category
- `test_unauthorized_account_rejected` — 未授权 account
- `test_cross_user_category_rejected` — 跨用户 category
- `test_wrong_book_category_rejected` — 跨账本 category
- `test_hallucinated_category_rejected` — 执行时幻觉 ID

**低置信动作 → 需确认：**
- `test_low_confidence_requires_confirmation` — 低置信需确认令牌
- `test_low_confidence_with_confirmation_token_accepted` — 低置信+令牌通过

### 3. 校验器平价报告（已实施）

**实现：**
- 共享 fixtures：`contracts/action-proposal/fixtures/`（16 个用例：7 valid + 9 invalid）
- TS↔ajv 平价：已在 `app/src/__tests__/domain/action-proposal.test.ts` 覆盖
- Python↔fixtures 平价：新建 `server/tests/test_validator_parity.py`（17 用例：7 valid + 9 invalid + 1 count guard）

**当前状态：**
- ✅ Python 校验器已实现（validator.py）
- ✅ TS 校验器已实现（action-proposal.ts）
- ✅ ajv 平价已在 TS 测试中覆盖（app 侧）
- ✅ Python 平价测试已实现（server/tests/test_validator_parity.py）

**结论：** 三方校验器（TS / ajv / Python）对同一批 16 fixtures 产出相同 valid/invalid 结论，平价验收达成。

## 验证命令

```bash
# 运行 T-M4-02 全量回归（含平价测试）
cd server
python -m pytest tests/test_ai_gateway.py tests/test_ai_proposal_validation.py tests/test_ai_proposal_execution.py tests/test_validator_parity.py tests/test_fact_pack.py -v

# 结果：104 passed
```

## CI 配置

```yaml
server:
  name: Server Lint & Test
  runs-on: ubuntu-latest
  steps:
    - uses: actions/setup-python@v5
      with:
        python-version: "3.13"
    - run: pip install -e ".[dev]"
    - run: ruff check src/ tests/
    - run: pytest tests/ --tb=short -q
```

## 依赖关系

- **硬依赖：** T-M4-02a ✅、T-M4-02b ✅（含幂等）
- **下游任务：** 无（T-M4-02 父任务服务端完成；T-M4-02c 客户端阻塞于 T-M4-01d）

## 交接产物

- `.github/workflows/ci.yml` — server job
- `server/tests/test_ai_proposal_validation.py` — 16 负例用例
- `server/tests/test_ai_proposal_execution.py` — 9 执行用例
- `contracts/action-proposal/fixtures/` — 16 共享 fixtures（已存在）

## T-M4-02 父任务完成总结

**目标：** 打通 AI 理解到安全业务执行的完整闭环

**验收标准达成：**
- ✅ 非法 JSON 零写库
- ✅ 幻觉数字零写库
- ✅ 无权限分类零写库
- ✅ 低置信动作需确认
- ✅ 批量执行全成或全回滚
- ✅ 服务端撤销 API 就绪

**子任务完成情况：**
- T-M4-02a ✅ Confidence 策略与执行链校验（16 用例）
- T-M4-02b ✅ 批量执行与撤销链路（12 用例，含 3 幂等）
- T-M4-02c ⏳ 助手对话流与确认卡 UI（客户端，阻塞于 T-M4-01d）
- T-M4-02d ✅ 父任务契约门禁回归（CI 集成 + 17 平价用例）

**备注：**
- T-M4-02c 依赖 T-M4-01d（auth bridge 接线），当前阻塞
- 服务端核心逻辑已全部完成并通过验收
- 客户端实现可在 T-M4-01d 完成后继续
