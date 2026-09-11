/**
 * T-M4-06a: 四时主题 WCAG AA 对比度验证。
 *
 * 验证所有正文 Token 在各主题玻璃卡面上的对比度 ≥4.5:1。
 *
 * T13-1（REQ-45 决策#3）分层模型更新：背景图 → body::after scrim → 玻璃卡面。
 * 测量方法：
 * 1. 取各主题 sky gradient 中点色作为图亮度代理（图片本身不可静态测亮度）
 * 2. 压 --bg-scrim 黑纱（scrim 常量与 index.css 同步断言，防回归）
 * 3. 按 --glass alpha 混合得到卡面有效背景色（glass 不透明度下限断言）
 * 4. 按 WCAG 2.1 relative luminance 公式计算对比度
 */

import { describe, it, expect } from 'vitest'

// ─── WCAG 2.1 相对亮度与对比度 ───

function srgbToLinear(c: number): number {
  const s = c / 255
  return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4)
}

function relativeLuminance(hex: string): number {
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  return 0.2126 * srgbToLinear(r) + 0.7152 * srgbToLinear(g) + 0.0722 * srgbToLinear(b)
}

function contrastRatio(fg: string, bg: string): number {
  const l1 = relativeLuminance(fg)
  const l2 = relativeLuminance(bg)
  const lighter = Math.max(l1, l2)
  const darker = Math.min(l1, l2)
  return (lighter + 0.05) / (darker + 0.05)
}

/** 将前景色 alpha 混合到背景上 */
function blend(fgR: number, fgG: number, fgB: number, alpha: number, bgR: number, bgG: number, bgB: number): [number, number, number] {
  return [
    Math.round(fgR * alpha + bgR * (1 - alpha)),
    Math.round(fgG * alpha + bgG * (1 - alpha)),
    Math.round(fgB * alpha + bgB * (1 - alpha)),
  ]
}

function toHex(r: number, g: number, b: number): string {
  return '#' + [r, g, b].map(c => c.toString(16).padStart(2, '0')).join('')
}

function hexToRgb(hex: string): [number, number, number] {
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ]
}

/** 计算 mid-gradient + scrim + glass blend 后的有效背景色 */
function effectiveGlassBg(skyFrom: string, skyTo: string, scrimAlpha: number, glassR: number, glassG: number, glassB: number, glassAlpha: number): string {
  const [r1, g1, b1] = hexToRgb(skyFrom)
  const [r2, g2, b2] = hexToRgb(skyTo)
  // mid gradient（背景图亮度代理）
  const midR = (r1 + r2) / 2
  const midG = (g1 + g2) / 2
  const midB = (b1 + b2) / 2
  // scrim：黑色纱层压暗
  const [sR, sG, sB] = blend(0, 0, 0, scrimAlpha, midR, midG, midB)
  // blend with glass
  const [eR, eG, eB] = blend(glassR, glassG, glassB, glassAlpha, sR, sG, sB)
  return toHex(eR, eG, eB)
}

// ─── 主题 Token ───

interface ThemeTokens {
  skyFrom: string
  skyTo: string
  /** T13-1 决策#3：图与内容间的可读性纱层（黑，alpha 值） */
  scrimAlpha: number
  glassRgb: [number, number, number]
  glassAlpha: number
  ink: string
  ink2: string
  ink3: string
}

const themes: Record<string, ThemeTokens> = {
  dawn: {
    skyFrom: '#FFB88C', skyTo: '#FFE4D6', scrimAlpha: 0.10,
    glassRgb: [255, 253, 250], glassAlpha: 0.6,
    ink: '#1E2733', ink2: '#46536B', ink3: '#526078',
  },
  day: {
    skyFrom: '#87CEEB', skyTo: '#E0F4FF', scrimAlpha: 0.08,
    glassRgb: [255, 255, 255], glassAlpha: 0.52,
    ink: '#122031', ink2: '#3C5069', ink3: '#4A6380',
  },
  dusk: {
    skyFrom: '#E8756A', skyTo: '#2D1B4E', scrimAlpha: 0.20,
    glassRgb: [255, 249, 242], glassAlpha: 0.68,
    ink: '#241C36', ink2: '#4E4066', ink3: '#554575',
  },
  night: {
    skyFrom: '#0F1628', skyTo: '#1A1F3A', scrimAlpha: 0.28,
    glassRgb: [14, 19, 40], glassAlpha: 0.55,
    ink: '#EFF1F8', ink2: '#B6BCCF', ink3: '#7A83A3',
  },
}

const MIN_CONTRAST = 4.5

describe('T-M4-06a: WCAG AA 对比度验证（图 → scrim → glass 分层）', () => {
  for (const [name, tokens] of Object.entries(themes)) {
    const bg = effectiveGlassBg(
      tokens.skyFrom, tokens.skyTo, tokens.scrimAlpha,
      ...tokens.glassRgb, tokens.glassAlpha,
    )

    describe(`${name} 主题 (有效卡面背景 ${bg})`, () => {
      it(`--ink 主文字对比度 ≥${MIN_CONTRAST}:1`, () => {
        const ratio = contrastRatio(tokens.ink, bg)
        expect(ratio).toBeGreaterThanOrEqual(MIN_CONTRAST)
      })

      it(`--ink2 次文字对比度 ≥${MIN_CONTRAST}:1`, () => {
        const ratio = contrastRatio(tokens.ink2, bg)
        expect(ratio).toBeGreaterThanOrEqual(MIN_CONTRAST)
      })

      it(`--ink3 辅助文字对比度 ≥${MIN_CONTRAST}:1`, () => {
        const ratio = contrastRatio(tokens.ink3, bg)
        expect(ratio).toBeGreaterThanOrEqual(MIN_CONTRAST)
      })
    })
  }
})

describe('T-M4-06a: 直接天空背景对比度（无玻璃卡面）', () => {
  // 部分文字直接出现在天空背景上（如 PageHeader 的 nav-glass 区域）
  // nav-glass 有更高的不透明度，需要额外验证
  it('nav 区域在各主题上 ink 可读', () => {
    // nav-bg 在各主题的不透明度较高（0.65-0.8），有效背景更接近 glass color
    // 简化验证：直接使用 sky gradient 中点 + scrim + nav glass blend
    for (const [name, tokens] of Object.entries(themes)) {
      const navAlpha = name === 'night' ? 0.8 : name === 'dusk' ? 0.65 : 0.7
      const bg = effectiveGlassBg(
        tokens.skyFrom, tokens.skyTo, tokens.scrimAlpha,
        ...tokens.glassRgb, navAlpha,
      )
      const ratio = contrastRatio(tokens.ink, bg)
      expect(ratio).toBeGreaterThanOrEqual(4.5)
    }
  })
})

// ─── T13-1（REQ-45）：index.css 分层落地同步断言 ───
// 需读 CSS 原文，落在 src/__tests__/fixtures/theme-css-sync.test.ts
// （该目录 tsconfig 排除、可用 node fs；vitest 下 ?raw 与 css.include 均被桩化）
