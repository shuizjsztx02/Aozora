# ADR-004 截图基线与视觉回归

**状态：** 已采纳（2026-09-06）
**范围：** T-M4-06c

## 背景

AOZORA 四时主题（晨/昼/暮/夜）需要确保视觉稳定性。每次 UI 改动可能影响四主题 × 多页面的视觉效果，需要自动化的视觉回归检测，避免人工逐页逐主题目视检查。

约束：
- 前端运行在 Android WebView 内（T-M4-00a），非标准浏览器
- CI 环境（GitHub Actions）需能运行截图对比
- 截图基线需覆盖关键页面 × 四主题
- 有意变更可按流程更新基线，不误报

## 候选方案

| 方案 | 优点 | 缺点 |
|---|---|---|
| **Playwright + toLocaleCompare screenshot** | 成熟稳定；支持多主题 class 切换；CI 原生支持 | 需 Chromium 安装，CI 镜像约 +200MB |
| BackstopJS | 专注视觉回归；配置简单 | 社区维护，定制性较低 |
| Chromatic (Storybook) | 云对比，低维护 | 需 Storybook 全量搭建，成本高 |
| 自研 Puppeteer 脚本 | 完全可控 | 维护成本大 |

## 决策

采用 **Playwright** 方案，理由：

1. **与 vitest 生态兼容** — 项目已使用 vitest；Playwright 可与 vite 开发服务器集成
2. **多主题截图** — 通过 `page.evaluate(() => applyTheme('dawn'))` 切换主题 class 后截图
3. **CI 友好** — `playwright install --with-deps chromium` 在 GitHub Actions 有官方支持
4. **阈值对比** — 支持 `maxDiffPixelRatio` 容忍渲染差异

## 实现方案

### 目录结构

```text
app/
├─ visual-tests/             # 视觉回归测试
│  ├─ baseline/              # 基线截图（按主题分目录）
│  │  ├─ dawn/
│  │  ├─ day/
│  │  ├─ dusk/
│  │  └─ night/
│  ├─ playwright.config.ts
│  └─ pages.spec.ts          # 关键页面截图用例
```

### 关键页面覆盖

| 页面 | 路由 | 备注 |
|---|---|---|
| 首页 | /home | 含金额、流水、同步状态 |
| 明细 | /detail | 日分组、筛选 |
| AI 助手 | /assistant | 对话气泡、确认卡 |
| 报表 | /reports | 环图、趋势图 |
| 我的 | /me | 主题切换 |
| 快速记账 | Sheet 展开 | 数字键盘、分类选择 |
| 首启 | /onboarding | 品牌、引导 |
| 登录 | /login | 表单 |

### 基线更新流程

1. 开发者修改 UI 后运行 `npm run visual:update` 更新基线
2. 在 PR 描述中注明"视觉变更已更新基线"
3. CI 运行 `npm run visual:test` 对比，差异超阈值则失败
4. 审查者目视确认 diff 合理后合并

### CI 集成

```yaml
# .github/workflows/visual-regression.yml
visual-test:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-node@v4
    - run: npm ci
    - run: npx playwright install --with-deps chromium
    - run: npm run build
    - run: npm run visual:test
```

## 实施时序

- 首批基线：T-M4-06c 完成时生成（四主题 × 8 页面 = 32 张基线）
- CI 门禁：T-M5-01d CI 整合时一并接入
- 后续扩展：新增页面时同步增加截图用例

## 风险

- Chromium 渲染与 Android WebView 可能有细微差异 — 可接受，目标是检测明显回归
- 动画导致截图不稳定 — 截图前注入 `prefers-reduced-motion: reduce`
- 字体渲染跨平台差异 — 使用 `maxDiffPixelRatio: 0.01` 容忍
