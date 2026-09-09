import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import DateField from '../../components/DateField'

// 固定"今天" = 2026-09-09（Asia/Shanghai 中午，避开日期边界抖动）
const TODAY = '2026-09-09'

describe('DateField（第十轮 REQ-18 自绘日历）', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-09T10:00:00+08:00'))
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('显示态：今天/昨天/M月D日/YYYY年M月D日 + data-date', () => {
    const { rerender } = render(<DateField value={TODAY} onChange={() => {}} testId="f" />)
    expect(screen.getByTestId('f').textContent).toContain('今天')
    expect(screen.getByTestId('f')).toHaveAttribute('data-date', TODAY)

    rerender(<DateField value="2026-09-08" onChange={() => {}} testId="f" />)
    expect(screen.getByTestId('f').textContent).toContain('昨天')

    rerender(<DateField value="2026-08-20" onChange={() => {}} testId="f" />)
    expect(screen.getByTestId('f').textContent).toContain('8月20日')

    rerender(<DateField value="2025-12-01" onChange={() => {}} testId="f" />)
    expect(screen.getByTestId('f').textContent).toContain('2025年12月1日')
  })

  it('打开弹层：标题/月份头/周一起始周头', () => {
    render(<DateField value={TODAY} onChange={() => {}} testId="f" />)
    fireEvent.click(screen.getByTestId('f'))
    expect(screen.getByTestId('date-sheet').textContent).toContain('选择日期')
    expect(screen.getByTestId('date-sheet').textContent).toContain('2026年9月')
    // 周头第一格 = 一（周一开始）
    const heads = screen.getAllByText('一')
    expect(heads.length).toBeGreaterThanOrEqual(1)
  })

  it('点日格 → onChange(ISO) 并关闭', () => {
    const onChange = vi.fn()
    render(<DateField value={TODAY} onChange={onChange} testId="f" />)
    fireEvent.click(screen.getByTestId('f'))
    fireEvent.click(screen.getByTestId('date-cell-2026-09-05'))
    expect(onChange).toHaveBeenCalledWith('2026-09-05')
    expect(screen.queryByTestId('date-sheet')).toBeNull()
  })

  it('‹ › 切月跨年：2027年1月可从 2026年9月 翻到', () => {
    render(<DateField value="2026-09-15" onChange={() => {}} testId="f" />)
    fireEvent.click(screen.getByTestId('f'))
    for (let i = 0; i < 4; i++) fireEvent.click(screen.getByLabelText('下个月'))
    expect(screen.getByTestId('date-sheet').textContent).toContain('2027年1月')
    expect(screen.getByTestId('date-cell-2027-01-15')).toBeEnabled()
  })

  it('非本月日格 disabled 且不可触发 onChange', () => {
    const onChange = vi.fn()
    render(<DateField value={TODAY} onChange={onChange} testId="f" />)
    fireEvent.click(screen.getByTestId('f'))
    // 2026-09-01 是周二 → 周一开始的首格为 8月31日（非本月）
    const ghost = screen.getByTestId('date-cell-2026-08-31')
    expect(ghost).toBeDisabled()
    fireEvent.click(ghost)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('今天格带 data-today 标记；选中格 aria-pressed', () => {
    render(<DateField value="2026-09-03" onChange={() => {}} testId="f" />)
    fireEvent.click(screen.getByTestId('f'))
    expect(screen.getByTestId(`date-cell-${TODAY}`)).toHaveAttribute('data-today', 'true')
    expect(screen.getByTestId('date-cell-2026-09-03')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByTestId(`date-cell-${TODAY}`)).toHaveAttribute('aria-pressed', 'false')
  })

  it('「回今天」→ onChange(today)；「取消」/遮罩点击 → 不触发', () => {
    const onChange = vi.fn()
    render(<DateField value="2026-09-03" onChange={onChange} testId="f" />)

    fireEvent.click(screen.getByTestId('f'))
    fireEvent.click(screen.getByTestId('date-today'))
    expect(onChange).toHaveBeenCalledWith(TODAY)

    fireEvent.click(screen.getByTestId('f'))
    fireEvent.click(screen.getByTestId('date-cancel'))
    fireEvent.click(screen.getByTestId('f'))
    fireEvent.click(screen.getByTestId('date-mask'))
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId('date-sheet')).toBeNull()
  })

  it('初始视图跟随 value（非今天所在月）', () => {
    render(<DateField value="2026-03-15" onChange={() => {}} testId="f" />)
    fireEvent.click(screen.getByTestId('f'))
    expect(screen.getByTestId('date-sheet').textContent).toContain('2026年3月')
    expect(screen.getByTestId('date-cell-2026-03-15')).toHaveAttribute('aria-pressed', 'true')
  })
})
