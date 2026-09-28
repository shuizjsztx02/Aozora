/**
 * CategoryIcon 容器几何测试（T12-2 / REQ-32）。
 *
 * jsdom 测不了渲染高度，断言两分支外层容器的 style 对象同构
 * （inline-flex 1em×1em 居中）——同构即等高，宫格名称行基线不再错位。
 * 内部视觉（img 1.25em 圆形 / emoji 字号继承）保持现状。
 */
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { CategoryIcon } from '../../components/CategoryIcon'
import { getCategoryEmoji } from '../../components/category-emoji'

const CONTAINER_PROPS = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: '1em',
  height: '1em',
  verticalAlign: 'middle',
} as const

function containerOf(ui: Parameters<typeof render>[0]): HTMLElement {
  const { container } = render(ui)
  return container.firstElementChild as HTMLElement
}

function expectContainerGeometry(el: HTMLElement) {
  expect(el.style.display).toBe(CONTAINER_PROPS.display)
  expect(el.style.alignItems).toBe(CONTAINER_PROPS.alignItems)
  expect(el.style.justifyContent).toBe(CONTAINER_PROPS.justifyContent)
  expect(el.style.width).toBe(CONTAINER_PROPS.width)
  expect(el.style.height).toBe(CONTAINER_PROPS.height)
  expect(el.style.verticalAlign).toBe(CONTAINER_PROPS.verticalAlign)
}

describe('CategoryIcon 双分支等高容器（REQ-32）', () => {
  it('emoji 分支：外层容器 = inline-flex 1em×1em 居中，className 落在容器上', () => {
    const el = containerOf(<CategoryIcon icon="🥟" name="多美" className="text-xl" />)
    expect(el.tagName).toBe('SPAN')
    expect(el.className).toBe('text-xl')
    expectContainerGeometry(el)
  })

  it('照片分支：外层容器与 emoji 分支同构，img 内嵌且视觉尺寸不动', () => {
    const el = containerOf(
      <CategoryIcon icon="data:image/png;base64,AAAA" name="多美" className="text-xl" />,
    )
    expect(el.tagName).toBe('SPAN')
    expect(el.className).toBe('text-xl')
    expectContainerGeometry(el)
    const img = el.querySelector('img')!
    expect(img.style.width).toBe('1.25em')
    expect(img.style.height).toBe('1.25em')
    expect(img.style.borderRadius).toBe('50%')
  })

  it('两分支容器 style 完全一致（等高保证）', () => {
    const emoji = containerOf(<CategoryIcon icon="🥟" name="多美" className="text-lg" />)
    const photo = containerOf(
      <CategoryIcon icon="data:image/png;base64,AAAA" name="多美" className="text-lg" />,
    )
    expect(photo.getAttribute('style')).toBe(emoji.getAttribute('style'))
  })

  it('icon 为空走名映射回退，容器几何不变', () => {
    const el = containerOf(<CategoryIcon icon={null} name="餐饮" className="text-lg" />)
    expect(el.textContent).toBe(getCategoryEmoji('餐饮'))
    expectContainerGeometry(el)
  })
})

describe('CategoryIcon 存量兜底裸词容错（REQ-68 / T18-4）', () => {
  // 旧兜底把 "default"/"book"/"account" 当图标写进库；它们不是 emoji，
  // 「非空即原样文本」会把英文单词画进圆形框 → 视同未设置。
  it.each(['default', 'book', 'account'])('icon="%s" 视同未设置，走 fallback', poison => {
    const el = containerOf(<CategoryIcon icon={poison} name="交行卡" fallback="💰" />)
    expect(el.textContent).toBe('💰')
  })

  it('裸词无 fallback 时继续走名映射回退', () => {
    const el = containerOf(<CategoryIcon icon="default" name="餐饮" />)
    expect(el.textContent).toBe(getCategoryEmoji('餐饮'))
  })

  it('非裸词的自定义文本照旧原样渲染（容错不得放宽到吞掉真自定义图标）', () => {
    const el = containerOf(<CategoryIcon icon="旅行" name="交行卡" fallback="💰" />)
    expect(el.textContent).toBe('旅行')
  })
})

// ─── REQ-79（T20-5）：新种子分类的兜底映射 ───

describe('getCategoryEmoji — 新种子分类兜底（REQ-79）', () => {
  it.each([
    ['宠物', '🐱'],
    ['养娃', '🍼'],
    ['买菜', '🥬'],
    ['住房', '🏠'],
    ['烟酒', '🚬'],
    ['人情社交', '🎁'],
    ['休闲娱乐', '🎮'],
    ['交通', '🚇'],
    ['打车', '🚕'],
    ['地铁', '🚇'],
    ['投资理财', '📈'],
  ])('%s → %s', (name, emoji) => {
    expect(getCategoryEmoji(name)).toBe(emoji)
  })

  it('旧种子名一条不丢（老账户的分类仍有图标）', () => {
    for (const legacy of ['餐饮', '交通', '购物', '娱乐', '居住', '医疗', '通讯', '学习']) {
      expect(getCategoryEmoji(legacy)).not.toBe('💰')
    }
    expect(getCategoryEmoji('居住')).toBe('🏠')
  })

  it('未知分类名回退钱包 emoji', () => {
    expect(getCategoryEmoji('某个自建分类')).toBe('💰')
  })
})
