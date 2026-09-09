/**
 * WidgetSettingsPage 组件测试 — 第十一轮 T11-2（REQ-24 / 决策 #5）。
 *
 * 覆盖（卡验收）：
 * - 恢复：meta.get 解析回填；坏 JSON/缺键回退默认态
 * - 保存即生效：开关切换 → setMeta('widget_config', json)（键序固定 payload 形状）+ refreshWidget 紧随
 * - 点击行为 chip → tapAction 写 'entry'/'home'
 * - "立即刷新"按钮 → refreshWidget 命令（不写 meta）
 * - 读失败回退默认；保存失败显示错误行
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const mockBridge = vi.hoisted(() => ({
  getMeta: vi.fn(),
  setMeta: vi.fn(),
  refreshWidget: vi.fn(),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

const mockNavigate = vi.hoisted(() => vi.fn())
vi.mock('react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

import { WidgetSettingsPage } from '../../features/settings/WidgetSettingsPage'

const renderPage = () => render(<WidgetSettingsPage />)

describe('WidgetSettingsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.getMeta.mockResolvedValue({})
    mockBridge.setMeta.mockResolvedValue({ success: true })
    mockBridge.refreshWidget.mockResolvedValue({ success: true })
  })

  it('无已存配置：默认态（笔数/预算开，本月支出关，点击=首页）', async () => {
    renderPage()
    await waitFor(() => expect(mockBridge.getMeta).toHaveBeenCalledWith(['widget_config']))
    expect(screen.getByTestId('widget-switch-today-count')).toHaveProperty('checked', true)
    expect(screen.getByTestId('widget-switch-month-expense')).toHaveProperty('checked', false)
    expect(screen.getByTestId('widget-switch-budget')).toHaveProperty('checked', true)
    expect(screen.getByTestId('widget-tap-home').textContent).toContain('✓')
  })

  it('已存配置回填', async () => {
    mockBridge.getMeta.mockResolvedValue({
      widget_config: '{"showTodayCount":false,"showMonthExpense":true,"showBudget":true,"tapAction":"entry"}',
    })
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('widget-switch-today-count')).toHaveProperty('checked', false)
      expect(screen.getByTestId('widget-switch-month-expense')).toHaveProperty('checked', true)
      expect(screen.getByTestId('widget-tap-entry').textContent).toContain('✓')
    })
  })

  it('坏 JSON 回退全默认', async () => {
    mockBridge.getMeta.mockResolvedValue({ widget_config: 'not-json' })
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('widget-switch-today-count')).toHaveProperty('checked', true)
      expect(screen.getByTestId('widget-tap-home').textContent).toContain('✓')
    })
  })

  it('meta.get 失败：静默保持默认态，不显示错误', async () => {
    mockBridge.getMeta.mockRejectedValue(new Error('bridge down'))
    renderPage()
    await waitFor(() => expect(mockBridge.getMeta).toHaveBeenCalled())
    expect(screen.getByTestId('widget-switch-budget')).toHaveProperty('checked', true)
    expect(screen.queryByTestId('widget-error')).toBeNull()
  })

  // ─── 保存即生效（决策 #5：保存 = meta.set + widget.refresh） ───

  it('关"预算进度"：setMeta 收到键序固定 payload 并紧随 widget.refresh', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockBridge.getMeta).toHaveBeenCalled())
    await user.click(screen.getByTestId('widget-switch-budget'))
    await waitFor(() => {
      expect(mockBridge.setMeta).toHaveBeenCalledWith(
        'widget_config',
        '{"showTodayCount":true,"showMonthExpense":false,"showBudget":false,"tapAction":"home"}',
      )
      expect(mockBridge.refreshWidget).toHaveBeenCalled()
    })
  })

  it('选"直达记一笔"：payload tapAction=entry，其余键保持当前值', async () => {
    mockBridge.getMeta.mockResolvedValue({
      widget_config: '{"showTodayCount":false,"showMonthExpense":true,"showBudget":true,"tapAction":"home"}',
    })
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockBridge.getMeta).toHaveBeenCalled())
    await user.click(screen.getByTestId('widget-tap-entry'))
    await waitFor(() => {
      expect(mockBridge.setMeta).toHaveBeenCalledWith(
        'widget_config',
        '{"showTodayCount":false,"showMonthExpense":true,"showBudget":true,"tapAction":"entry"}',
      )
    })
  })

  it('"立即刷新"按钮：只发 widget.refresh，不写 meta', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockBridge.getMeta).toHaveBeenCalled())
    await user.click(screen.getByTestId('widget-refresh-button'))
    await waitFor(() => expect(mockBridge.refreshWidget).toHaveBeenCalled())
    expect(mockBridge.setMeta).not.toHaveBeenCalled()
  })

  it('保存失败（meta.set reject）：显示错误行', async () => {
    mockBridge.setMeta.mockRejectedValue(new Error('write failed'))
    const user = userEvent.setup()
    renderPage()
    await waitFor(() => expect(mockBridge.getMeta).toHaveBeenCalled())
    await user.click(screen.getByTestId('widget-switch-today-count'))
    await waitFor(() => {
      expect(screen.getByTestId('widget-error').textContent).toContain('保存失败')
    })
  })
})
