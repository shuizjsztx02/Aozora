# T-M4-00 代码审查

- **审查日期：** 2026-09-06
- **基线：** 工作区当前状态（T-M4-00 子任务 00a 至 00g 全部完成）
- **范围：**
  - 新增代码：`app/src/app/`（Router.tsx、ErrorBoundary.tsx）、`app/src/design/`（theme.ts、SkyCanvas.tsx、a11y.ts）、`app/src/components/`（StateViews.tsx、Layout.tsx、BottomNav.tsx、SyncBadge.tsx）、`app/src/platform/store/`（sync-store.ts、data-store.ts）、`app/src/platform/hooks/`（use-bridge.ts）、`app/src/features/{ledger,reports,settings,onboarding,auth}/`（各页面组件）
  - 更新：`app/src/App.tsx`、`app/src/index.css`、`app/vite.config.ts`、`app/src/test/setup.ts`、`app/package.json`
  - 新增测试：`app/src/__tests__/design/`（theme.test.ts、SkyCanvas.test.tsx）、`app/src/__tests__/components/`（StateViews.test.tsx、BottomNav.test.tsx）
  - 新增 ADR：`docs/adr/ADR-002-frontend-tech-stack.md`
- **门禁：**
  - `npm run lint` → exit 0（11 warnings，无 errors）
  - `npm run typecheck` → exit 0
  - `npm test -- --run` → **348 passed / 18 files / 3.5s**（原 327 + 新增 21：theme ×5、SkyCanvas ×2、StateViews ×11、BottomNav ×2、App ×1）
- **方法：** 全量阅读 design/、components/、features/ 全部页面与布局组件、platform 层新增 store/hook、ADR-002、index.css 逐值比对 tokens.md V5.2 表、验证命令实测

## 一、交付范围逐项确认

| 子任务 | 关键交付物 | 审查结论 |
|---|---|---|
| 00a 技术栈落地 | `react-router`/`zustand`/`framer-motion`/`recharts`/`tailwindcss` v4 + `@tailwindcss/vite`；§7.3 目录骨架（`app/`/`design/`/`components/`/`features/`/`platform/`）；ADR-002 | ✅ 齐全 |
| 00b 四时主题 | 4 主题 CSS 变量（`.t-dawn/.t-day/.t-dusk/.t-night`）逐值比对 tokens.md V5.2 表——全部精确匹配；`useTheme()` 三模式（auto/system/manual）；`SkyCanvas`（z-index ≤ 1 / pointer-events:none / aria-hidden=true）；全部基础类（glass-card / sheet-glass / nav-glass / row-line / cat-cell / key-btn / money-num / serif-date / sky-fixed） | ✅ Token 一一对应 |
| 00c 状态组件 | `EmptyState`/`LoadingState`/`ErrorState`/`PendingSyncState`；`AppLayout`+`PageHeader`+`BottomNav`（nav-glass）；`useReducedMotion()` | ✅ 四类齐全 |
| 00d 首页+记账 | `HomePage`（今日支出/本月进度/近期流水/SyncBadge）；`QuickEntrySheet`（底部弹层/收支切换/金额输入/分类选择/bridge 写入） | ✅ |
| 00e 明细+我的 | `DetailPage`（月份切换/筛选/日分组/删除）；`MePage`（主题切换/功能占位/用户信息） | ✅ |
| 00f 报表 | `ReportsPage`（月度概览/Recharts PieChart 环图/LineChart 趋势/预算进度/收支切换）；纯本地数据（Room+aggregator） | ✅ |
| 00g 首启+登录 | `OnboardingPage`（品牌/隐私/游客+登录）；`LoginPage`（手机验证码表单/游客入口） | ✅ |

明确未做项：auth bridge 命令实际接线（T-M4-01d）、编辑流水（T-M4-02b）、提醒/导出接线（T-M4-05/T-M4-04）、AI 洞察卡（T-M4-03c）——与完成记录声明一致。

## 二、发现的问题

### 🔴 P1 — HomePage 预算进度语义错误

**位置：** `app/src/features/ledger/HomePage.tsx:66-76` + `HomePage.tsx:110-112`

**问题：** 进度条标签写"本月预算"，但分子用的是 `todayTxs`（仅今日流水），分母是月度预算额 `budget.amountCents`。下方辅助文字 `formatCents(todayExpense.totalExpense)` 也显示今日支出，与"本月预算"标题矛盾。

**复现：**
1. 用户设置月度预算 ¥3000
2. 本月已花 ¥2900，但今日只记了一笔 ¥30
3. 进度条显示 1%（30/3000），而非实际的 97%（2900/3000）
4. 用户误以为预算充裕，实际已接近超支

**修复建议（最小改动）：**
- 进度条改为拉取本月全部支出（`transaction.listByBook` + 客户端按月过滤），或新增一个本地累计状态；
- 下方文字显示「已花/预算」，分子使用本月累计支出。

### 🟡 P2-1 — `data-store.ts` 实质为死代码

**位置：** `app/src/platform/store/data-store.ts`

- `refreshAll(bridge: BridgeLike)` 中 `BridgeLike` 接口用 camelCase 方法名（`bookList()`/`categoryList()`…），与实际 `NativeBridge.call('book.list', ...)` 点号 API **签名不兼容**——无法直接传入；
- 没有任何页面 import `useDataStore` 的数据；各页面全部直接 `useBridge().call()` 读数据；
- `data-store` 的 transactions/categories/accounts/books/budgets 状态恒为 `[]`，`refreshAll` 从未被调用。

**建议：** 删除死代码，或在后续任务中确定数据缓存策略后统一接入。

### 🟡 P2-2 — `as never` 类型强转破坏类型安全

**位置：** `app/src/features/ledger/HomePage.tsx:67-72`、`app/src/features/reports/ReportsPage.tsx:60-68`

根因：`TransactionData.amountCents: number`（bridge 返回）vs `Transaction.amountCents: MoneyCents`（aggregator 期望），二者因 branded type 不兼容，只能 `as never` 绕过检查。

**风险：** 完全绕过类型检查——后续字段一旦错位，编译期不会报错。

**修复建议：** 写一个辅助函数 `toDomain(tx: TransactionData): Transaction`，在 bridge 返回后通过 `Money.cents()` 显式升级，避免在调用点用 `as never`。

### 🟡 P2-3 — `sync-store.ts` `Object.defineProperty` 无清理

**位置：** `app/src/platform/store/sync-store.ts:52-73`

- `initListener()` 通过 `Object.defineProperty(window, '__aozoraSyncEvent__', { set })` 安装 setter 监听；
- cleanup 函数只 `removeEventListener`，不清理 `defineProperty` 的 setter；
- 若 listener unmount 后再次 mount（HMR / 路由切换），旧 setter 仍捕获闭包引用。

**实际影响低**（单生命周期），但建议在 cleanup 中 `delete window.__aozoraSyncEvent__` 或恢复原 descriptor。

### 🟢 P3 — 小问题（不阻塞，建议后续顺手修）

| 位置 | 问题 | 建议 |
|---|---|---|
| `app/src/design/theme.ts:92-94` | `useEffect(() => applyTheme(theme), [])` 缺 `theme` 依赖（故意只 mount 运行，但触发 lint 告警） | 加 `// eslint-disable-next-line` 注释，或用 ref 保存最新 theme |
| `app/src/features/ledger/DetailPage.tsx:55` | `transaction.listByBook` 拉全量再客户端按月过滤，数据量大时性能差 | 后续可引入 `transaction.listByMonth` bridge 命令 |
| `app/src/features/auth/LoginPage.tsx:30,47,59` | 3 处 `catch (err)` 未使用参数 | 改 `catch` 或 `catch (_e)` |
| `app/src/features/reports/ReportsPage.tsx` | 无 loading 态，数据加载时图表短暂空白 | 用 `LoadingState` 组件包装 |
| `app/src/index.css` | `--expense`/`--glass-border`/`--sky-from`/`--sky-to`/`--nav-bg` 不在 tokens.md | 补到 tokens.md 的扩展 token 节 |
| `app/src/features/ledger/QuickEntrySheet.tsx:195` + `HomePage.tsx:178` | `getCatEmoji` 硬编码映射重复两处 | 提到 `components/` 作为共享 util |

## 三、设计/质量/安全亮点

- ✅ Token 精确匹配 tokens.md V5.2（包括 rgba 透明度值），CSS 基础类完整（glass-card/sheet-glass/nav-glass/row-line/cat-cell/key-btn/money-num/serif-date/sky-fixed）
- ✅ `MoneyCents` 整数分贯穿全链路——`fromString` → bridge insert → Room；**无浮点引入**
- ✅ WebView `localStorage` 仅存 `aozora-theme` 和 `aozora-onboarding-completed`——**无 token 泄露**
- ✅ auth bridge 命令仅保留类型占位，`LoginPage` 为纯 UI 骨架（符合"明确未做"声明）
- ✅ `SkyCanvas` 装饰层严格遵守 z-index ≤ 1 / pointer-events:none / aria-hidden=true（不遮挡、不接收点击、对辅助技术不可见）
- ✅ 新增 21 项测试覆盖 theme / SkyCanvas / StateViews / BottomNav / App，全部通过
- ✅ HashRouter 选型记录 ADR-002，决策理由清晰

## 四、总体结论

**T-M4-00 有条件通过，需修复 P1（HomePage 预算进度语义错误）后正式关闭。**

| 维度 | 评分 |
|---|---|
| 功能完整度 | A（7 个子任务全部有对应实现） |
| 代码质量 | B+（有死代码和类型强转） |
| 设计一致性 | A（Token/CSS 精确匹配 tokens.md V5.2） |
| 测试覆盖 | A（348 项，新增 21，无回归） |
| 安全/数据 | A（无 token 泄露，无浮点金额） |

### 建议动作

1. **（必修）** 修复 HomePage 预算进度计算——改成本月累计支出，避免误导用户。
2. **（建议）** 删除或重构 `data-store.ts` 死代码，避免后续任务误用。
3. **（建议）** 把 `as never` 替换为显式的 `toDomain()` 辅助函数，恢复聚合器调用的类型安全。
4. 其余 P3 可留给 T-M4-06（视觉打磨）或 T-M4-02b（编辑流水）顺手处理。

### 交接核对

- T-M4-00 完成记录中声明的"开放风险"与本次审查发现一致：auth 接线留给 T-M4-01d、编辑流水留给 T-M4-02b、Recharts 低端机性能留给 T-M4-06b。
- 本次审查未发现完成记录遗漏的关键问题。
- 既有 327 项测试无回归，新增 21 项覆盖 theme / SkyCanvas / 状态组件 / 布局组件 / App 路由入口。
