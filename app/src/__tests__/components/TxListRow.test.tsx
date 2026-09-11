/**
 * TxListRow 组件测试（T14-1 / REQ-38 — 首页/明细共享流水行的视觉锁）。
 *
 * 覆盖：标题 15px semibold / 副行 12px / 金额 17px bold 无¥按类型 +/- 前缀 /
 * 40×40 玻璃方格 + 19px CategoryIcon / AI 徽标开关 / right 槽位序 /
 * borderBottom 开关 / 行点击回调。
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { TxListRow } from '../../components/TxListRow'

const base = {
  icon: '🍜',
  iconName: '餐饮',
  title: '午饭',
  sub: '12:30 · 餐饮',
  amountCents: 5500,
  isExpense: true,
}

describe('TxListRow', () => {
  it('标题 15px semibold、副行 12px、金额 17px bold 负号前缀（支出不带¥）', () => {
    const { container } = render(<TxListRow {...base} />)
    const title = screen.getByText('午饭')
    expect(title).toHaveClass('text-[15px]', 'font-semibold', 'truncate')
    const sub = screen.getByText('12:30 · 餐饮')
    expect(sub).toHaveClass('text-xs', 'truncate', 'mt-0.5')
    const amount = screen.getByText('-55.00')
    expect(amount).toHaveClass('money-num', 'text-[17px]', 'font-bold')
    // 40×40 玻璃方格 + 19px 图标
    const box = container.querySelector('div.rounded-xl') as HTMLElement
    expect(box.getAttribute('style')).toContain('width: 40px')
    expect(box.getAttribute('style')).toContain('height: 40px')
    expect(box.querySelector('.text-\\[19px\\]')).not.toBeNull()
  })

  it('收入金额前缀 + 号', () => {
    render(<TxListRow {...base} isExpense={false} amountCents={800000} />)
    expect(screen.getByText('+8000.00')).toBeInTheDocument()
  })

  it('aiBadge=true 才渲染 9px 直角 AI 徽标', () => {
    const { rerender } = render(<TxListRow {...base} />)
    expect(screen.queryByText('AI')).not.toBeInTheDocument()
    rerender(<TxListRow {...base} aiBadge />)
    expect(screen.getByText('AI')).toHaveClass('text-[9px]')
  })

  it('right 槽渲染在金额之后（✕ 删除钮场景）', () => {
    const { container } = render(
      <TxListRow {...base} right={<button aria-label="删除">✕</button>} />,
    )
    const row = container.firstChild as HTMLElement
    const del = screen.getByLabelText('删除')
    expect(del).toBeInTheDocument()
    expect(row.lastElementChild).toBe(del)
  })

  it('borderBottom 开关控制底部分割线', () => {
    const { container, rerender } = render(<TxListRow {...base} borderBottom />)
    const row = () => container.firstChild as HTMLElement
    expect(row().getAttribute('style')).toContain('border-bottom')
    rerender(<TxListRow {...base} />)
    expect(row().getAttribute('style') || '').not.toContain('border-bottom')
  })

  it('点击行回调 onClick', () => {
    const onClick = vi.fn()
    render(<TxListRow {...base} onClick={onClick} />)
    fireEvent.click(screen.getByText('午饭'))
    expect(onClick).toHaveBeenCalledTimes(1)
  })
})
