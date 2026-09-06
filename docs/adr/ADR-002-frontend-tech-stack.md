# ADR-002 前端技术栈选型

**状态：** 已采纳（2026-09-06）
**范围：** T-M4-00a 前端骨架与核心页面

## 背景

T-M4-00 需要建立生产前端基础：路由、状态管理、样式、动画和图表。
仓库前端此前为空壳（`App.tsx` 仅占位），需要安装技术栈并搭建目录骨架。

## 决策

| 领域 | 选型 | 版本 | 理由 |
|---|---|---|---|
| 路由 | `react-router`（HashRouter） | v7 | WebView 从 `index.html` 加载，HashRouter 无需服务端路由配置；React 生态最成熟 |
| 状态管理 | `zustand` | 最新 | 符合 §4.1 "Zustand 只保存视图态和短生命周期缓存"；轻量、TypeScript 友好 |
| 样式 | `tailwindcss` v4 + `@tailwindcss/vite` | v4 | 任务要求；v4 使用 CSS-first 配置（`@theme` 指令），无需 `tailwind.config.js` |
| 动画 | `framer-motion` | 最新 | 任务要求；声明式 API，适合页面微交互 |
| 图表 | `recharts` | 最新 | 任务要求；React 原生图表库，支持主题定制 |
| 构建 | Vite 8 + `@tailwindcss/vite` 插件 | 已有 + 新增 | 统一构建入口 |

## 目录结构（§7.3）

```
app/src/
├─ app/          # 启动、路由、providers、全局错误边界
├─ domain/       # （已存在）纯函数无 UI 依赖
├─ platform/     # （已存在）bridge + store + hooks
├─ features/     # 按功能模块：ledger/reports/settings/onboarding/auth
├─ components/   # 跨 feature 共享 UI
├─ design/       # token/theme/SkyCanvas/a11y
└─ test/         # fixtures/builders/setup
```

## 后果

- ✅ 路由、状态、样式、动画、图表能力齐备
- ✅ Tailwind v4 的 CSS-first 配置与四时主题 token 自然融合
- ✅ Zustand 薄封装，不复制 domain 数据（Room 通过 bridge 访问）
- ✅ HashRouter 适配 WebView `index.html` 加载模式
- ⚠️ Framer Motion 包体积需关注（仅导入 `motion.div` + `AnimatePresence`）
- ⚠️ Recharts 在低端 WebView 性能需监控
