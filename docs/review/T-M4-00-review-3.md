# T-M4-00 三次审查（终审）

- **审查日期：** 2026-09-06
- **基线：** 二次审查修复后
- **范围：** 验证二次审查 10 项 P1/P2 修复质量 + 回归检查
- **门禁：**
  - `npx tsc --noEmit` → ✅ exit 0，无错误
  - `npx vitest run` → ✅ **348 passed / 18 files / 2.93s**，无回归
  - ESLint → 配置文件缺失（`eslint.config.*` 不存在），为预存环境问题，非本次引入

---

## 一、二次审查修复验证

| # | 原始问题 | 严重度 | 修复方式 | 验证结果 |
|---|---|---|---|---|
| 1 | `useInitSync()` 从未调用，sync listener 仅在 HomePage 生命周期内有效 | 🔴 P1 | `App.tsx` 顶层调用 `useInitSync()`；`HomePage` 移除 `initSyncListener`/`useSyncStore` 引用 | ✅ 正确。Hook 在根组件无条件调用，覆盖全应用生命周期；HomePage 已清理干净 |
| 2 | `safe-bottom` CSS class 未定义 | 🔴 P1 | `index.css:197` 添加 `.safe-bottom { padding-bottom: env(safe-area-inset-bottom, 0px); }` | ✅ 正确。含 `0px` fallback 兼容不支持 `env()` 的环境 |
| 3 | MePage「手动选择」主题模式 onClick 为空 | 🔴 P1 | `MePage.tsx:65` 补充 `else if (opt.value === 'manual') setManual(theme)` | ✅ 正确。传入当前显示主题，避免跳变 |
| 4 | QuickEntrySheet `bizDate` 每次 render 重算 | 🔴 P1 | `QuickEntrySheet.tsx:26` 改为 `const [bizDate] = useState(() => todayBizDate('Asia/Shanghai'))` | ✅ 正确。lazy initializer 确保 mount 时计算一次 |
| 5 | OnboardingPage `localStorage.setItem` 裸调 | 🔴 P1 | `OnboardingPage.tsx:15-19` 包裹 try/catch，catch 为空操作（注释说明），navigate 无条件执行 | ✅ 正确。与 `isOnboardingCompleted()` 的 try/catch 对称 |
| 6 | PageHeader `back` prop 声明但未使用 | 🔴 P1 | `Layout.tsx:28-38` 实现返回按钮：`back` 为真时渲染 `<button onClick={() => navigate(-1)} aria-label="返回">←</button>` | ✅ 正确。当前无页面传 `back={true}`，但能力已就绪 |
| 7 | 路由无 onboarding guard | 🔴 P1 | `Router.tsx:18-23` 新增 `ProtectedRoute` 组件，检查 `isOnboardingCompleted()`，不满足时 redirect 到 `/onboarding`；`/home`/`/detail`/`/reports`/`/me` 均包裹 | ✅ 正确。`*` catch-all → `/home` → 被 guard 拦截 → 新用户正确落地 onboarding |
| 8 | ErrorBoundary 直接展示 `error.message` + 缺 `componentDidCatch` | 🟡 P2 | `ErrorBoundary.tsx`: ① 添加 `componentDidCatch` 打印错误+组件栈；② UI 不再展示 `error.message`，改用通用文案；③ 重试次数限制 3 次，超限显示「💔 需要帮助」并隐藏重试按钮 | ✅ 正确。防御纵深：`getDerivedStateFromError`（UI 恢复）+ `componentDidCatch`（日志上报）双通道 |
| 9 | SyncBadge fallback 显示英文 state 名 | 🟡 P2 | `SyncBadge.tsx:26-33` 新增 `stateLabels` Record 覆盖全部 6 个 `SyncStateValue`，line 52 渲染中文标签 | ✅ 正确。穷举映射，新增 state 时 TypeScript 会提示补全 |
| 10 | `<nav>` 缺 `aria-label` | 🟡 P2 | `BottomNav.tsx:17` 添加 `aria-label="主导航"` | ✅ 正确 |
| 11 | DetailPage 删除交易无确认 | 🟡 P2 | `DetailPage.tsx:89` 添加 `if (!window.confirm('确定要删除这条记录吗？')) return` | ✅ 正确。`window.confirm` 原生弹窗在 WebView 中可工作，后续可替换为自定义 Sheet |

**修复质量总评：11/11 修复均正确，无引入回归。**

---

## 二、回归检查

| 检查项 | 结果 |
|---|---|
| TypeScript 编译 | ✅ 0 errors |
| 测试 | ✅ 348/348 passed（与修复前一致，无回归） |
| Hook 调用规则 | ✅ `useInitSync()` 在 App 组件顶层无条件调用，符合 Rules of Hooks |
| 跨文件引用 | ✅ `useInitSync`（App↔use-bridge）、`isOnboardingCompleted`（Router↔OnboardingPage）、`SyncStateValue`（SyncBadge↔bridge-types，6 变体全覆盖）、`safe-bottom`（index.css↔BottomNav）、`setManual(ThemeName)`（MePage↔theme）全部解析正确 |
| localStorage 一致性 | ✅ 三处 localStorage 访问（`OnboardingPage.handleContinue`、`isOnboardingCompleted`、`theme.ts`）全部有 try/catch 守卫 |
| 同步监听生命周期 | ✅ App mount → `useInitSync` → `initListener` 注册；App unmount → cleanup → `removeEventListener` + `delete window.__aozoraSyncEvent__` |

---

## 三、遗留项（非阻塞，留给后续任务）

| # | 位置 | 问题 | 建议归属 |
|---|---|---|---|
| 1 | `use-bridge.ts:38,49` | `useInsertTransaction` / `useDeleteTransaction` 导出但全项目未使用（死代码） | T-M4-02b（编辑流水）接入或删除 |
| 2 | `DetailPage.tsx:89` | `window.confirm` 原生弹窗与 glass 设计语言不协调 | T-M4-06（视觉打磨）替换为自定义确认 Sheet |
| 3 | `HomePage:180` / `QuickEntrySheet:195` / `DetailPage:198` | `getCatEmoji`/`getEmoji` emoji 映射重复三处 | T-M4-06 顺手提取为共享 util |
| 4 | `DetailPage.tsx:48-69` | 每次切月拉全量交易再客户端过滤 | 后续 bridge 增加 `transaction.listByMonth` |
| 5 | `StateViews.tsx:9` / `MePage.tsx:115` | `React.ReactNode` 未 import React | T-M4-06 统一 import 风格 |
| 6 | `MePage.tsx:108` | 版本号 `v0.1.0` 硬编码 | 构建时注入 |
| 7 | 项目级 | `eslint.config.*` 缺失，lint 命令无法运行 | 工程化任务补配置 |

---

## 四、三次审查演进对比

| 维度 | 初次审查 | 二次审查 | 三次审查 |
|---|---|---|---|
| P1 | 1 | 6 | **0** |
| P2 | 3 | 4 | **0** |
| P3 | 6 | 9 | 7（遗留，非阻塞） |
| 测试 | 348 ✅ | 348 ✅ | 348 ✅ |
| TypeScript | ✅ | ✅ | ✅ |
| 结论 | 有条件通过 | 需修复 | **通过** |

---

## 五、终审结论

**T-M4-00 通过。**

- 初次审查 6 项修复（P1×1 + P2×3 + P3×2）→ 全部正确
- 二次审查 11 项修复（P1×6 + P2×5）→ 全部正确
- 无新引入 bug、无测试回归、无类型错误
- 遗留 7 项低优先级问题已明确归属后续任务（T-M4-01d / T-M4-02b / T-M4-06 / 工程化）

### 评分更新

| 维度 | 初次 | 终审 |
|---|---|---|
| 功能完整度 | A | A |
| 代码质量 | B+ | **A−** |
| 设计一致性 | A | A |
| 测试覆盖 | A | A |
| 安全/数据 | A | A |
| 生命周期管理 | — | A（sync listener 提升到 App 层） |
| 路由安全 | — | B+（onboarding guard 就位，auth guard 留 T-M4-01d） |
