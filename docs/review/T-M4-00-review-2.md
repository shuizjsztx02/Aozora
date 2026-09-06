# T-M4-00 二次审查

- **审查日期：** 2026-09-06
- **基线：** 初次审查修复后（P1/P2 已全部处理）
- **范围：** 验证初次审查 6 项修复质量 + 全模块新增代码再审查
- **门禁：** `npm run typecheck` ✅ / `npm test -- --run` 348 passed ✅ / `npm run lint` ✅（11 warnings，0 errors）

---

## 一、初次审查修复验证

| # | 原始问题 | 严重度 | 修复方式 | 验证结果 |
|---|---|---|---|---|
| 1 | HomePage 预算进度分子用了今日支出而非本月累计 | 🔴 P1 | 新增 `monthTxs` 状态，`loadData` 中按月过滤 `bizDate.startsWith(month) && type === 'expense' && deletedAt === null`；进度条/百分比/文字均改用 `monthExpense` | ✅ 正确。语义对齐「本月已花/预算」 |
| 2 | `data-store.ts` 为死代码（BridgeLike 签名不兼容、无页面引用） | 🟡 P2 | 删除文件 + 移除 `use-bridge.ts` 中 `useTransactionsByDate` 及相关导入 | ✅ 正确。全局 grep `data-store` 零匹配，无残留引用 |
| 3 | `as never` 类型强转破坏类型安全 | 🟡 P2 | `domain/types.ts` 新增 `toDomainTransaction()` 辅助函数，内部用 `Money.cents()` 显式升级 `amountCents` 为 `MoneyCents`；`HomePage`/`ReportsPage` 改用 | ✅ 正确。全局 grep `as never` 零匹配。`cents()` 内含非负安全整数校验，兼具运行时防护 |
| 4 | `sync-store.ts` `Object.defineProperty` setter 无清理 | 🟡 P2 | cleanup 函数中增加 `delete (window as any).__aozoraSyncEvent__`，由 `descriptorCleaned` 守卫仅在 store 自行安装时清理 | ✅ 正确 |
| 5 | `theme.ts` useEffect 缺依赖触发 lint 告警 | 🟢 P3 | 添加 `// eslint-disable-next-line react-hooks/exhaustive-deps` | ✅ 正确。mount-only 语义有注释说明 |
| 6 | `LoginPage.tsx` 3 处 `catch (err)` 未使用参数 | 🟢 P3 | 改为 `catch { ... }`（ES2019 optional catch binding） | ✅ 正确 |

**修复质量总评：** 6/6 修复均正确落地，无引入新回归。`toDomainTransaction` 抽象位置合理（`domain/types.ts`），复用性好。

---

## 二、新发现问题

### 🔴 P1 — `useInitSync()` 从未被调用，同步 UI 在非首页下失效

**位置：** `app/src/platform/hooks/use-bridge.ts:29-35`、`app/src/features/ledger/HomePage.tsx:34,68`

**问题：**
- `useInitSync()` 在 `use-bridge.ts` 中导出，但全项目无任何组件调用它。
- 实际同步监听由 `HomePage` 直接调用 `useSyncStore(s => s.initListener)` 并在 `useEffect` 中初始化。
- **后果 1：** `useInitSync` 是死代码——导出了但没人用。
- **后果 2（更严重）：** 同步事件监听绑定在 `HomePage` 生命周期上。用户从 `/detail` 或 `/reports` 进入时，`initListener` 从未执行 → `SyncBadge` 永远停在 `idle`、`PendingSyncState` 拿不到真实计数。从 `/home` 导航到其他页面时，`HomePage` 卸载触发 cleanup → 监听被移除 → 其他页面同样收不到同步事件。

**修复建议（最小改动）：** 在 `App.tsx` 中调用 `useInitSync()`，使同步监听覆盖全应用生命周期，并从 `HomePage` 移除 `initListener` 调用。

---

### 🔴 P1 — 路由无 onboarding / auth 守卫

**位置：** `app/src/app/Router.tsx`

**问题：**
- `/home`、`/detail`、`/reports`、`/me` 无鉴权保护。`OnboardingPage` 已导出 `isOnboardingCompleted()`（line 82-88），但 `Router` 从未调用。
- 用户可直接 URL 访问 `/home` 跳过首启引导；已登录用户也能再次访问 `/onboarding` 和 `/login`。

**影响：** 首启引导可被绕过，产品流程不完整。

**修复建议：** 包裹 `<ProtectedRoute>` 组件，在路由层检查 `isOnboardingCompleted()` 和登录态，不满足条件时 redirect。

> **注：** auth 实际接线留给 T-M4-01d，此处至少应加 onboarding guard。

---

### 🔴 P1 — `PageHeader` 的 `back` prop 声明但从未使用

**位置：** `app/src/components/Layout.tsx:22-27`

**问题：**
- `back?: boolean` 在 `PageHeaderProps` 中声明（line 23）。
- 但 `PageHeader({ title, right })` 解构时丢弃了 `back`（line 27）。
- 传 `back={true}` 永远不会渲染返回按钮——死代码 + 功能缺失。

**修复建议：** 要么实现返回按钮逻辑（`navigate(-1)`），要么删除 `back` prop 声明。

---

### 🔴 P1 — `safe-bottom` CSS class 未定义

**位置：** `app/src/components/BottomNav.tsx:16`

**问题：**
- `className="... safe-bottom"` 引用了 `safe-bottom` 类。
- 全局 grep 显示该类仅在 `BottomNav.tsx` 中出现，`index.css` 中无定义。
- **后果：** iPhone 刘海屏 / 底部指示条设备上，底部导航被系统 UI 遮挡。

**修复建议（一行）：** 在 `index.css` 中添加：
```css
.safe-bottom { padding-bottom: env(safe-area-inset-bottom); }
```

---

### 🔴 P1 — MePage「手动选择」主题模式点击无效

**位置：** `app/src/features/settings/MePage.tsx:62-65`

**问题：**
```js
onClick={() => {
  if (opt.value === 'auto') setAuto()
  else if (opt.value === 'system') setSystem()
  // ← manual 分支为空
}}
```
- 三个模式按钮共享同一个 onClick，但只处理了 `auto` 和 `system`。
- 用户点击「手动选择」无任何反应。`setManual` 只在下方主题格子（line 83）调用，但「手动选择」radio 永远不会被激活高亮。

**修复建议：** 补充 `else if (opt.value === 'manual') setManual(theme)` 或类似逻辑。

---

### 🔴 P1 — QuickEntrySheet `bizDate` 每次 render 重新计算

**位置：** `app/src/features/ledger/QuickEntrySheet.tsx:26`

**问题：**
```js
const bizDate = todayBizDate('Asia/Shanghai')
```
不在 `useState` 初始化或 `useMemo` 中。用户在 23:59 打开 sheet，跨夜后任意 re-render（如输入金额触发 `setAmount`）都会将 `bizDate` 跳到第二天。提交时 `bizDate` 与用户看到的日期不一致。

**修复建议：** 改为 `const [bizDate] = useState(() => todayBizDate('Asia/Shanghai'))`。

---

### 🔴 P1 — OnboardingPage `localStorage.setItem` 无 try/catch

**位置：** `app/src/features/onboarding/OnboardingPage.tsx:15`

**问题：**
```js
localStorage.setItem(ONBOARDING_KEY, 'true')  // 裸调
navigate(login ? '/login' : '/home')
```
- Safari 无痕模式、存储空间满、用户禁用等场景会抛异常。
- 异常导致 `navigate` 不执行 → 按钮点了没反应。
- 对比：同文件 `isOnboardingCompleted()`（line 82-87）读操作有 try/catch，写操作却遗漏了。

**修复建议：** 包裹 try/catch，失败时仍然 navigate（引导标记写入失败不应阻塞用户进入）。

---

### 🟡 P2 — ErrorBoundary 展示原始 `error.message` + 缺少 `componentDidCatch`

**位置：** `app/src/app/ErrorBoundary.tsx:28,31`

**问题：**
- line 28：`this.state.error?.message` 直接展示给用户。`error.message` 可能含内部路径、变量名、堆栈片段，生产环境应只给通用提示。
- line 31：重试按钮仅 `setState({ hasError: false })` 重新渲染 children。若根因未修复（如 required prop 缺失），children 立即再次 throw → 用户陷入「重试→崩溃→重试」循环。
- 缺少 `componentDidCatch`：没有任何错误上报通道（Sentry / console 等）。

**修复建议：**
1. 生产环境用通用文案替换 `error.message`；
2. 加 `componentDidCatch(error, info)` 至少 `console.error`；
3. 重试加计数，超过阈值显示不同文案。

---

### 🟡 P2 — SyncBadge fallback 直接显示英文 state 名

**位置：** `app/src/components/SyncBadge.tsx:43`

**问题：**
```js
{status.pendingCount > 0 ? `${status.pendingCount} 条待同步` : status.state}
```
- 当 `pendingCount === 0` 且非 `idle` 时，fallback 分支直接显示 `"pushing"` / `"pulling"` / `"rebuilding"` / `"auth-lost"` / `"error"` 原始英文字符串。中文用户看不懂。

**修复建议：** 增加 state → 中文映射表：
```ts
const stateLabels: Record<SyncStateValue, string> = {
  idle: '已同步', pushing: '上传中', pulling: '下载中',
  rebuilding: '重建中', 'auth-lost': '登录失效', error: '同步失败',
}
```

---

### 🟡 P2 — `<nav>` 缺少 `aria-label`

**位置：** `app/src/components/BottomNav.tsx:16`

**问题：** `<nav>` 未标 `aria-label`。屏幕阅读器遇到多个 `<nav>` 时无法区分。

**修复建议：** 添加 `aria-label="主导航"`。

---

### 🟡 P2 — DetailPage 删除交易无确认对话框

**位置：** `app/src/features/ledger/DetailPage.tsx:88-95`

**问题：** `handleDelete` 直接调用 `transaction.softDelete`。删除按钮（`✕`）小且紧贴金额，容易误触，无确认提示也无 undo toast。

**修复建议：** 添加 `window.confirm` 或自定义确认 Sheet。软删除虽可恢复，但用户视角是「数据丢了」。

---

### 🟢 P3 — 低优先级（建议后续任务顺手修）

| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| 1 | `use-bridge.ts:29` | `useInitSync()` 死代码——若按 P1-1 修复移入 App.tsx 则解决；否则应删除 | 修复 P1-1 后自然消除 |
| 2 | `HomePage.tsx:180-191` / `QuickEntrySheet.tsx:195-206` / `DetailPage.tsx:198-209` | `getCatEmoji`/`getEmoji` emoji 映射重复三处 | 抽到 `components/` 或 `design/` 作为共享 util |
| 3 | `SkyCanvas.tsx:106-121` | `<style>` 内联在组件里，每次主题切换重复注入同一份 `@keyframes` | 把 keyframes 移到 `index.css` 一次注入 |
| 4 | `DetailPage.tsx:48-69` | 每次切月拉全量交易 + 全量分类再客户端过滤（已记录于初次审查 P3，未修） | 后续 bridge 增加 `transaction.listByMonth` 命令；`category.list` 独立缓存 |
| 5 | `StateViews.tsx:9` / `MePage.tsx:115` | `React.ReactNode` 未 import React，依赖全局命名空间 | 统一改 `import type { ReactNode } from 'react'` |
| 6 | `MePage.tsx:108` | `Aozora v0.1.0` 版本号硬编码 | 从 `package.json` 或构建时注入 |
| 7 | `MePage.tsx:126` | `MenuItem` 的 `<button>` 未设 `type="button"` | 添加 `type="button"` 防未来嵌入 form 时出问题 |
| 8 | `OnboardingPage.tsx:74-76` | 隐私声明纯文本无可点击链接 | 添加 `<a>` 链接到隐私政策和用户协议页面 |
| 9 | `index.css:205-211` | `prefers-reduced-motion` 用 `0.01ms` 而非 `0s` | 改 `animation: none` 或 `0s` |

---

## 三、误报排除

| 来源 | 报告的问题 | 排查结论 |
|---|---|---|
| Agent 1 | ReportsPage 每日趋势未过滤 `deletedAt` | **误报。** `txs` state 在 `loadData` 第 47 行已经过滤了 `deletedAt === null`，每日趋势从已过滤的 `txs` 中读取，不含已删除记录 |
| Agent 1 | QuickEntrySheet 提交成功后不关闭 | **误报。** `HomePage:169-172` 的 `onSuccess` 回调包含 `setShowSheet(false)`，sheet 会正常关闭 |

---

## 四、与初次审查对比

| 维度 | 初次审查 | 二次审查 |
|---|---|---|
| P1 数量 | 1（预算进度语义） | 6（sync 失效、路由守卫、back 死代码、safe-bottom、manual 模式、bizDate 跨夜、localStorage） |
| P2 数量 | 3 | 3（ErrorBoundary、SyncBadge 英文、nav aria-label、删除无确认） |
| P3 数量 | 6 | 9 |
| 测试 | 348 passed | 348 passed（无变化） |
| 初次 P1~P2 修复 | — | ✅ 6/6 全部正确 |

初次审查发现的 P1 预算进度问题已修复。但深度再审查暴露了更多此前未识别的 P1 级问题——主要集中在**生命周期管理**（sync listener 绑定位置）、**路由守卫缺失**、和**声明但未实现的功能**（`back` prop、`manual` 模式）。

---

## 五、总体结论

**T-M4-00 初次修复质量合格，但模块仍存在 6 个 P1 + 4 个 P2 级问题，建议修复后正式关闭。**

### 建议修复优先级

1. **（必修）** `useInitSync` → 移到 `App.tsx`，修复同步状态全页面覆盖
2. **（必修）** `safe-bottom` CSS 定义 → 一行修复
3. **（必修）** MePage `manual` 模式 onClick → 补充处理
4. **（必修）** QuickEntrySheet `bizDate` → 改 `useState` 初始化
5. **（必修）** OnboardingPage `localStorage.setItem` → 加 try/catch
6. **（必修）** `PageHeader.back` → 实现或删除
7. **（建议）** Router onboarding guard
8. **（建议）** ErrorBoundary 安全展示 + componentDidCatch
9. **（建议）** SyncBadge 中文映射
10. **（建议）** DetailPage 删除确认

### 交接核对

- 初次审查 6 项修复全部正确，无引入回归。
- 新发现的 P1 问题集中在组件生命周期和声明/实现不一致——属于快速迭代阶段的典型遗留，修复成本均较低。
- auth 接线（T-M4-01d）、编辑流水（T-M4-02b）、AI 洞察（T-M4-03c）、视觉打磨（T-M4-06）的边界声明不变。
