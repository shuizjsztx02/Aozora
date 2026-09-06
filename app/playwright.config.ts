/**
 * T-M4-06c: Playwright 视觉回归测试配置。
 *
 * 通过 ADR-004 决策，使用 Playwright 截图对比实现视觉回归检测。
 * 运行方式：npm run visual:test（对比） / npm run visual:update（更新基线）
 */

import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './visual-tests',
  snapshotPathTemplate: '{testDir}/baseline/{arg}{ext}',
  fullyParallel: false, // 逐主题截图避免状态冲突
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [['html', { open: 'never' }]],
  use: {
    baseURL: 'http://localhost:5173',
    viewport: { width: 390, height: 844 }, // iPhone 14 尺寸
    // deviceScaleFactor 由 projects[].use.devices['iPhone 14'] 提供（值为 3），此处不重复设置
    // T-M4-06b: 禁用动画确保截图稳定
    extraHTTPHeaders: { 'Accept-Language': 'zh-CN' },
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['iPhone 14'] },
    },
  ],
  webServer: {
    command: 'npm run dev -- --port 5173',
    port: 5173,
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
  expect: {
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.01, // 容忍微小渲染差异
    },
  },
})
