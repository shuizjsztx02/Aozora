/**
 * T-M4-06c: 关键页面 × 四主题视觉基线。
 *
 * 每个页面在四个主题下各截一张图，作为视觉回归基线。
 * 基线路径：visual-tests/baseline/{theme}/{page}.png
 *
 * R-M4-13 修复：认证页面通过 injectMockBridge 注入 fixture 数据，
 * 确保截图包含有意义的 UI 内容（流水、图表、金额），而非空态/loading。
 */

import { test, expect } from '@playwright/test'

const themes = ['dawn', 'day', 'dusk', 'night'] as const
const themeNames: Record<string, string> = {
  dawn: '晨',
  day: '昼',
  dusk: '暮',
  night: '夜',
}

// 需要登录/引导跳过的页面
const authenticatedPages = [
  { name: 'home', path: '/home' },
  { name: 'detail', path: '/detail' },
  { name: 'reports', path: '/reports' },
  { name: 'me', path: '/me' },
  { name: 'assistant', path: '/assistant' },
]

const publicPages = [
  { name: 'onboarding', path: '/onboarding' },
  { name: 'login', path: '/login' },
]

/** 切换主题并等待过渡完成 */
async function applyTheme(page: any, theme: string) {
  await page.evaluate((t: string) => {
    localStorage.setItem('aozora-theme', JSON.stringify({ mode: 'manual', manual: t }))
    const root = document.documentElement
    root.classList.remove('t-dawn', 't-day', 't-dusk', 't-night')
    root.classList.add(`t-${t}`)
  }, theme)
  // 等待主题切换渲染
  await page.waitForTimeout(300)
}

/** 跳过首启引导（在已加载的页面上执行） */
async function skipOnboarding(page: any) {
  await page.evaluate(() => {
    localStorage.setItem('aozora-onboarding-completed', 'true')
  })
}

/** 禁用动画确保截图稳定 */
async function disableAnimations(page: any) {
  await page.addStyleTag({
    content: `*, *::before, *::after {
      animation-duration: 0s !important;
      animation-delay: 0s !important;
      transition-duration: 0s !important;
    }`,
  })
}

/**
 * R-M4-13: 注入 mock AozoraBridge，为各页面提供 fixture 数据。
 * addInitScript 在页面任何脚本执行前运行，确保 NativeBridge 初始化后能正常通信。
 * 须在首次 page.goto 之前调用；addInitScript 会跨导航持久化。
 */
function injectMockBridge(page: any) {
  return page.addInitScript(() => {
    // 动态计算"今天"的 bizDate（YYYY-MM-DD，Asia/Shanghai 时区）
    const today = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(new Date())
    const month = today.slice(0, 7) // "YYYY-MM"
    const dayNum = parseInt(today.slice(8), 10)
    const d = (offset: number) => {
      const n = Math.max(1, dayNum - offset)
      return `${month}-${String(n).padStart(2, '0')}`
    }

    const CATEGORIES = [
      { id: 'cat_food',   name: '餐饮', icon: '🍽️', version: 1, createdAt: 1000 },
      { id: 'cat_trans',  name: '交通', icon: '🚇', version: 1, createdAt: 1000 },
      { id: 'cat_shop',   name: '购物', icon: '🛍️', version: 1, createdAt: 1000 },
      { id: 'cat_salary', name: '工资', icon: '💰', version: 1, createdAt: 1000 },
    ]

    const TRANSACTIONS = [
      { id: 'tx_1', bookId: 'book_1', categoryId: 'cat_food',   amountCents: 3500,   type: 'expense', note: '午餐',   bizDate: today, occurredAt: Date.now() - 3_600_000,   deletedAt: null, version: 1, createdAt: 1000 },
      { id: 'tx_2', bookId: 'book_1', categoryId: 'cat_trans',  amountCents: 800,    type: 'expense', note: '地铁',   bizDate: today, occurredAt: Date.now() - 7_200_000,   deletedAt: null, version: 1, createdAt: 1000 },
      { id: 'tx_3', bookId: 'book_1', categoryId: 'cat_shop',   amountCents: 12900,  type: 'expense', note: '日用品', bizDate: today, occurredAt: Date.now() - 10_800_000,  deletedAt: null, version: 1, createdAt: 1000 },
      { id: 'tx_4', bookId: 'book_1', categoryId: 'cat_food',   amountCents: 2800,   type: 'expense', note: '早餐',   bizDate: d(1),  occurredAt: Date.now() - 86_400_000,   deletedAt: null, version: 1, createdAt: 1000 },
      { id: 'tx_5', bookId: 'book_1', categoryId: 'cat_salary', amountCents: 1500000, type: 'income',  note: '月薪',   bizDate: d(3),  occurredAt: Date.now() - 259_200_000,  deletedAt: null, version: 1, createdAt: 1000 },
    ]

    const HANDLERS: Record<string, () => unknown> = {
      'bridge.getVersion':     () => ({ version: 1 }),
      'book.list':              () => [{ id: 'book_1', name: '默认账本', currency: 'CNY', version: 1, createdAt: 1000 }],
      'category.list':          () => CATEGORIES,
      'transaction.listByBook': () => TRANSACTIONS,
      'budget.list':            () => [{ id: 'budget_1', bookId: 'book_1', budgetType: 'monthly', categoryId: null, period: month, amountCents: 200000, version: 1, createdAt: 1000 }],
      'sync.getStatus':         () => ({ state: 'idle', lastSyncedAt: Date.now() - 60_000 }),
      'reminder.getStatus':     () => ({ enabled: false, hour: 21, minute: 0, permissionGranted: false }),
    }

    // Mock AozoraBridge — 与 NativeBridge 协议一致（Kotlin 接口形状）
    // R-M4-06-新: 队列化响应，消除 10ms setTimeout 竞争窗口
    // 若 __aozoraBridgeRespond 尚未注册，响应排队等待 flush
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const pendingResponses: string[] = []
    const tryFlush = () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const respond = (window as any).__aozoraBridgeRespond
      if (typeof respond === 'function') {
        while (pendingResponses.length > 0) {
          respond(pendingResponses.shift()!)
        }
      }
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(window as any).AozoraBridge = {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      call(_version: number, requestId: string, command: string, _payloadJson: string) {
        const handler = HANDLERS[command]
        const data = handler ? handler() : []
        pendingResponses.push(JSON.stringify({ requestId, success: true, data }))
        tryFlush()
      },
      getBridgeVersion: () => 1,
      ping: () => 'pong',
    }
    // 当 NativeBridge constructor 注册 __aozoraBridgeRespond 时，自动 flush 队列
    Object.defineProperty(window, '__aozoraBridgeRespond', {
      configurable: true,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      get() { return (window as any).___aozoraRespondFn ?? tryFlush },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      set(fn) { (window as any).___aozoraRespondFn = fn; tryFlush() },
    })
  })
}

// 公开页面（无需登录，不需要 fixture 数据）
for (const theme of themes) {
  test.describe(`${themeNames[theme]} (${theme})`, () => {
    for (const pg of publicPages) {
      test(`${pg.name} 页面`, async ({ page }) => {
        await disableAnimations(page)
        await page.goto(pg.path)
        await applyTheme(page, theme)
        await expect(page).toHaveScreenshot(`${theme}-${pg.name}.png`, {
          fullPage: true,
        })
      })
    }

    // 认证页面 — 注入 mock bridge + 跳过引导后访问
    for (const pg of authenticatedPages) {
      test(`${pg.name} 页面`, async ({ page }) => {
        // R-M4-13: 首次 goto 前注入 mock bridge（addInitScript 跨导航持久化）
        await injectMockBridge(page)
        await disableAnimations(page)
        await page.goto('/onboarding')
        await skipOnboarding(page)
        await page.goto(pg.path)
        await applyTheme(page, theme)
        // 等待数据加载（mock bridge 响应 10ms + React 渲染）
        await page.waitForTimeout(500)
        await expect(page).toHaveScreenshot(`${theme}-${pg.name}.png`, {
          fullPage: true,
        })
      })
    }

    // 快速记账 Sheet 展开状态
    test(`quick-entry-sheet 展开`, async ({ page }) => {
      await injectMockBridge(page)
      await disableAnimations(page)
      await page.goto('/onboarding')
      await skipOnboarding(page)
      await page.goto('/home')
      await applyTheme(page, theme)
      await page.waitForTimeout(500)
      // 点击快速记账按钮
      await page.click('[data-testid="quick-entry-btn"]')
      await page.waitForTimeout(300)
      await expect(page).toHaveScreenshot(`${theme}-quick-entry-sheet.png`, {
        fullPage: false,
      })
    })
  })
}
