/**
 * AiInsightCard 测试 — T-M4-03c 客户端洞察展示。
 *
 * 覆盖：缓存行为、离线降级、加载/错误状态、叙述展开。
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { AiInsightCard } from '../../features/reports/AiInsightCard'
import type { AiReportResponse } from '../../platform/bridge/bridge-types'
import type { NativeBridge } from '../../platform/bridge/native-bridge'

function makeReport(overrides: Partial<AiReportResponse> = {}): AiReportResponse {
  return {
    request_id: 'req-1',
    book_id: 'book-1',
    period: '2026-09',
    data_hash: 'hash-abc',
    total_income: 100000,
    total_expense: 80000,
    balance: 20000,
    transaction_count: 42,
    expense_by_category: [],
    income_by_category: [],
    budget: null,
    largest_expense: null,
    largest_income: null,
    trend: [],
    narrative: null,
    narrative_cached: false,
    usage: null,
    ...overrides,
  }
}

function makeBridge(report?: AiReportResponse, error?: Error): NativeBridge {
  const call = vi.fn()
  if (report) {
    call.mockResolvedValue(report)
  } else if (error) {
    call.mockRejectedValue(error)
  }
  return {
    call,
    isAvailable: () => true,
  } as unknown as NativeBridge
}

describe('AiInsightCard', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('无 bookId 时不渲染', () => {
    const bridge = makeBridge()
    const { container } = render(<AiInsightCard bridge={bridge} bookId={null} period="2026-09" />)
    expect(container.firstChild).toBeNull()
  })

  it('显示加载状态', async () => {
    const bridge = makeBridge(makeReport())
    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" />)
    expect(screen.getByText('正在生成洞察...')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('正在生成洞察...')).not.toBeInTheDocument())
  })

  it('成功加载后显示事实包摘要', async () => {
    const report = makeReport({ transaction_count: 42 })
    const bridge = makeBridge(report)
    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" />)

    await waitFor(() => {
      expect(screen.getByText('42')).toBeInTheDocument()
    })
    expect(bridge.call).toHaveBeenCalledWith('ai.report', { bookId: 'book-1', period: '2026-09' }, { timeoutMs: 30000 })
  })

  it('显示 AI 叙述', async () => {
    const report = makeReport({ narrative: '本月支出较上月下降 10%，表现良好。' })
    const bridge = makeBridge(report)
    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" />)

    await waitFor(() => {
      expect(screen.getByText('本月支出较上月下降 10%，表现良好。')).toBeInTheDocument()
    })
  })

  it('显示错误状态', async () => {
    const bridge = makeBridge(undefined, new Error('网络错误'))
    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" />)

    await waitFor(() => {
      expect(screen.getByText('网络错误')).toBeInTheDocument()
    })
  })

  it('缓存命中时不调用 bridge', async () => {
    const report = makeReport()
    const cache = { 'book-1:2026-09': { data: report, timestamp: Date.now() } }
    localStorage.setItem('ai-insight-cache', JSON.stringify(cache))

    const bridge = makeBridge()
    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" />)

    await waitFor(() => {
      expect(screen.getByText('42')).toBeInTheDocument()
    })
    expect(bridge.call).not.toHaveBeenCalled()
  })

  it('缓存过期后重新请求', async () => {
    const report = makeReport()
    const expiredTimestamp = Date.now() - 2 * 60 * 60 * 1000 // 2 小时前
    const cache = { 'book-1:2026-09': { data: report, timestamp: expiredTimestamp } }
    localStorage.setItem('ai-insight-cache', JSON.stringify(cache))

    const newReport = makeReport({ transaction_count: 99 })
    const bridge = makeBridge(newReport)
    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" />)

    await waitFor(() => {
      expect(screen.getByText('99')).toBeInTheDocument()
    })
    expect(bridge.call).toHaveBeenCalled()
  })

  it('离线时不请求', () => {
    const originalOnLine = navigator.onLine
    Object.defineProperty(navigator, 'onLine', { value: false, configurable: true })

    const bridge = makeBridge()
    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" />)

    expect(bridge.call).not.toHaveBeenCalled()

    Object.defineProperty(navigator, 'onLine', { value: originalOnLine, configurable: true })
  })

  it('bridge 不可用时不请求', () => {
    const bridge = {
      call: vi.fn(),
      isAvailable: () => false,
    } as unknown as NativeBridge

    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" />)
    expect(bridge.call).not.toHaveBeenCalled()
  })

  it('显示已缓存标记', async () => {
    const report = makeReport({ narrative_cached: true })
    const bridge = makeBridge(report)
    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" />)

    await waitFor(() => {
      expect(screen.getByText('已缓存')).toBeInTheDocument()
    })
  })
})
