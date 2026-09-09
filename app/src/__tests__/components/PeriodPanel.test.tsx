/**
 * PeriodPanel 组件测试（T10-5 / REQ-21 — 从 DetailPage 抽共享面板的行为锁定）。
 *
 * 覆盖：open=false 不渲染；open=true 渲染年份 + 12 月宫格；
 * 点月份回调 onPick('YYYY-MM')；点 ‹ › 回调 onYearChange；点遮罩回调 onClose。
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { PeriodPanel } from '../../components/PeriodPanel'

const base = {
  onClose: vi.fn(),
  year: 2026,
  onYearChange: vi.fn(),
  period: '2026-09',
  onPick: vi.fn(),
}

describe('PeriodPanel', () => {
  it('open=false 时不渲染任何内容', () => {
    const { container } = render(<PeriodPanel {...base} open={false} />)
    expect(container).toBeEmptyDOMElement()
    expect(screen.queryByText('2026年')).not.toBeInTheDocument()
  })

  it('open=true 时渲染当前面板年份与 12 个月份按钮', () => {
    render(<PeriodPanel {...base} open />)
    expect(screen.getByText('2026年')).toBeInTheDocument()
    // 12 个月宫格
    for (let m = 1; m <= 12; m++) {
      expect(screen.getByRole('button', { name: `${m}月` })).toBeInTheDocument()
    }
  })

  it('当前 period 对应的月份按钮高亮（accent 背景）', () => {
    render(<PeriodPanel {...base} open />)
    const sep = screen.getByRole('button', { name: '9月' })
    expect(sep.getAttribute('style') || '').toContain('var(--accent)')
  })

  it('点月份按钮回调 onPick 传 YYYY-MM', () => {
    const onPick = vi.fn()
    render(<PeriodPanel {...base} open onPick={onPick} />)
    fireEvent.click(screen.getByRole('button', { name: '3月' }))
    expect(onPick).toHaveBeenCalledWith('2026-03')
  })

  it('点 ‹ / › 年份按钮回调 onYearChange 增减年份', () => {
    const onYearChange = vi.fn()
    render(<PeriodPanel {...base} open onYearChange={onYearChange} />)
    fireEvent.click(screen.getByLabelText('上一年'))
    expect(onYearChange).toHaveBeenCalledWith(2025)
    fireEvent.click(screen.getByLabelText('下一年'))
    expect(onYearChange).toHaveBeenCalledWith(2027)
  })

  it('点遮罩回调 onClose', () => {
    const onClose = vi.fn()
    render(<PeriodPanel {...base} open onClose={onClose} />)
    // 遮罩是 z-10 的 fixed inset-0 空 div
    const overlay = document.querySelector('.fixed.inset-0.z-10') as HTMLElement
    expect(overlay).not.toBeNull()
    fireEvent.click(overlay)
    expect(onClose).toHaveBeenCalled()
  })
})
