/**
 * T13-1（REQ-45 决策#3）：index.css 背景图分层落地同步断言。
 *
 * 与 design/contrast.test.ts 的分层常量（scrim alpha / glass）保持镜像：
 * 那边验证"分层数学上可读"，这边验证"CSS 真按这套分层写了"。
 * 任一改动不同步即红。
 *
 * 说明：放 fixtures/ 目录是既有约定（tsconfig 排除、vitest 正常运行），
 * 因项目无 @types/node 且 vitest 将 CSS import（含 ?raw）桩化为空串。
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

const cssText = readFileSync(resolve(__dirname, '../../index.css'), 'utf-8')

/** 决策#3 分层契约：scrim 黑纱 alpha + glass 不透明度下限 */
const SCRIM: Record<string, number> = {
  dawn: 0.10,
  day: 0.08,
  dusk: 0.20,
  night: 0.28,
}
const MIN_GLASS_ALPHA = 0.5

/** token 定义块 `.t-<name> { … }`（`.t-<name> body,` 规则不匹配：类名后不直接跟 {） */
function themeBlock(name: string): string {
  const m = cssText.match(new RegExp(`\\.t-${name}\\s*\\{[^}]*\\}`))
  expect(m, `index.css 缺少 .t-${name} token 块`).not.toBeNull()
  return m![0]
}

/** 定位含指定声明的 CSS 规则，返回选择器与声明体 */
function ruleFor(declNeedle: string): { selector: string; decls: string } {
  const i = cssText.indexOf(declNeedle)
  expect(i, `CSS 中找不到 ${declNeedle}`).toBeGreaterThan(-1)
  const open = cssText.lastIndexOf('{', i)
  const close = cssText.indexOf('}', i)
  const selStart = cssText.lastIndexOf('}', open)
  return {
    selector: cssText.slice(selStart + 1, open).trim(),
    decls: cssText.slice(open + 1, close),
  }
}

describe('T13-1: 四主题背景图 + scrim 已按决策#3 接入 index.css', () => {
  for (const [name, scrimAlpha] of Object.entries(SCRIM)) {
    const block = themeBlock(name)

    it(`${name}: body 图层 = url('/theme/${name}.webp') 首层 + 渐变兜底层`, () => {
      const layerRe = new RegExp(
        `\\.t-${name}\\s+body[^{]*\\{[^}]*background-image:\\s*url\\('/theme/${name}\\.webp'\\),\\s*linear-gradient`,
      )
      expect(cssText, `${name} 背景图/兜底渐变层缺失`).toMatch(layerRe)
    })

    it(`${name}: --bg-scrim = rgba(0,0,0,${scrimAlpha})（决策#3 常量）`, () => {
      const m = block.match(/--bg-scrim:\s*rgba\(\s*0,\s*0,\s*0,\s*([0-9.]+)\s*\)/)
      expect(m, `${name} 缺 --bg-scrim`).not.toBeNull()
      expect(Number(m![1])).toBeCloseTo(scrimAlpha, 6)
    })

    it(`${name}: --glass 不透明度 ≥ ${MIN_GLASS_ALPHA}（图作背景后的可读性下限）`, () => {
      const m = block.match(/--glass:\s*rgba\([^,]+,[^,]+,[^,]+,\s*([0-9.]+)\s*\)/)
      expect(m, `${name} 缺 --glass`).not.toBeNull()
      expect(Number(m![1])).toBeGreaterThanOrEqual(MIN_GLASS_ALPHA)
    })
  }

  it('图层几何：cover / center top / 不重复 / fixed（长页不拉伸文档高），四主题全覆盖', () => {
    const rule = ruleFor('background-attachment')
    for (const name of Object.keys(SCRIM)) {
      expect(rule.selector, `几何规则选择器缺 .t-${name} body`).toContain(`.t-${name} body`)
    }
    expect(rule.decls).toMatch(/background-size:\s*cover,\s*auto/)
    expect(rule.decls).toMatch(/background-position:\s*center top,\s*center/)
    expect(rule.decls).toMatch(/background-repeat:\s*no-repeat,\s*no-repeat/)
    expect(rule.decls).toMatch(/background-attachment:\s*fixed,\s*fixed/)
  })

  it('scrim 纱层 = body::after fixed 层，压在图上、内容之下（z=-1，低于内容层）', () => {
    const m = cssText.match(/body::after\s*\{[^}]*\}/)
    expect(m, '缺 body::after 规则').not.toBeNull()
    expect(m![0]).toContain('var(--bg-scrim')
    expect(m![0]).toMatch(/z-index:\s*-1/)
    expect(m![0]).toMatch(/position:\s*fixed/)
    expect(m![0]).toMatch(/pointer-events:\s*none/)
  })

  it('T17-1（REQ-63）：淡出纱 = body::before，主题底色渐变 + --bg-veil，且先于 body::after 绘制', () => {
    const m = cssText.match(/body::before\s*\{[^}]*\}/)
    expect(m, '缺 body::before 规则').not.toBeNull()
    const rule = m![0]
    expect(rule).toContain('linear-gradient(180deg, var(--sky-from), var(--sky-to))')
    expect(rule).toContain('opacity: var(--bg-veil')
    expect(rule).toMatch(/position:\s*fixed/)
    expect(rule).toMatch(/inset:\s*0/)
    expect(rule).toMatch(/z-index:\s*-1/)
    expect(rule).toMatch(/pointer-events:\s*none/)
    // 同层（z=-1）内按文档序绘制：纱必须写在黑纱之前
    expect(cssText.search(/body::before\s*\{/)).toBeLessThan(cssText.search(/body::after\s*\{/))
  })
})
