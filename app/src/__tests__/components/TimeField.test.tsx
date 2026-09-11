/**
 * TimeField 组件测试 — 第十四轮 T14-5（REQ-43）。
 * 覆盖：受控渲染（HH:00 两位补齐）、▲▼ 步进、进退位回绕（23→0 / 0→23 / 59→0 / 0→59）、
 * onChange 双参联动（动时不改分、动分不改时）。
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import TimeField from '../../components/TimeField'

const P = 'tf'

function setup(hour: number, minute: number) {
  const onChange = vi.fn()
  render(<TimeField hour={hour} minute={minute} onChange={onChange} testId={P} />)
  return onChange
}

describe('TimeField', () => {
  it('渲染补零两位 + 时/分说明', () => {
    setup(8, 5)
    expect(screen.getByTestId(`${P}-hour`)).toHaveTextContent('08')
    expect(screen.getByTestId(`${P}-minute`)).toHaveTextContent('05')
  })

  it('▲ 时/分各 +1，互不串扰', () => {
    const onChange = setup(8, 30)
    fireEvent.click(screen.getByTestId(`${P}-hour-up`))
    expect(onChange).toHaveBeenLastCalledWith(9, 30)
    fireEvent.click(screen.getByTestId(`${P}-minute-up`))
    expect(onChange).toHaveBeenLastCalledWith(8, 31)
  })

  it('▼ 时/分各 -1，互不串扰', () => {
    const onChange = setup(8, 30)
    fireEvent.click(screen.getByTestId(`${P}-hour-down`))
    expect(onChange).toHaveBeenLastCalledWith(7, 30)
    fireEvent.click(screen.getByTestId(`${P}-minute-down`))
    expect(onChange).toHaveBeenLastCalledWith(8, 29)
  })

  it('回绕：时 23→0（上）、0→23（下）', () => {
    const onChange = vi.fn()
    const { rerender } = render(<TimeField hour={23} minute={0} onChange={onChange} testId={P} />)
    fireEvent.click(screen.getByTestId(`${P}-hour-up`))
    expect(onChange).toHaveBeenLastCalledWith(0, 0)
    rerender(<TimeField hour={0} minute={0} onChange={onChange} testId={P} />)
    fireEvent.click(screen.getByTestId(`${P}-hour-down`))
    expect(onChange).toHaveBeenLastCalledWith(23, 0)
  })

  it('回绕：分 59→0（上）、0→59（下）', () => {
    const onChange = vi.fn()
    const { rerender } = render(<TimeField hour={8} minute={59} onChange={onChange} testId={P} />)
    fireEvent.click(screen.getByTestId(`${P}-minute-up`))
    expect(onChange).toHaveBeenLastCalledWith(8, 0)
    rerender(<TimeField hour={8} minute={0} onChange={onChange} testId={P} />)
    fireEvent.click(screen.getByTestId(`${P}-minute-down`))
    expect(onChange).toHaveBeenLastCalledWith(8, 59)
  })
})
