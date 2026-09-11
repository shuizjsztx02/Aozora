/**
 * AiInsightCard 测试 — T-M4-03c 客户端洞察展示 + T10-7 修复（决策 #8）。
 *
 * 覆盖：缓存行为（TTL 5min）、刷新按钮 bypass 缓存、离线降级、加载/错误状态、
 * 叙述展开、空数据诊断（去同步按钮调用链、localCount=0 不显示提示）。
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
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
    triggerAuthSync: vi.fn().mockResolvedValue({ outcome: 'OK' }),
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

  // ─── T10-7 / 决策 #8：TTL 5min + 刷新 + 空数据诊断 ───

  it('TTL 5 分钟：过期 6 分钟的缓存重新请求', async () => {
    const cache = { 'book-1:2026-09': { data: makeReport(), timestamp: Date.now() - 6 * 60 * 1000 } }
    localStorage.setItem('ai-insight-cache', JSON.stringify(cache))

    const bridge = makeBridge(makeReport({ transaction_count: 99 }))
    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" />)

    await waitFor(() => {
      expect(screen.getByText('99')).toBeInTheDocument()
    })
    expect(bridge.call).toHaveBeenCalled()
  })

  it('TTL 5 分钟：3 分钟内的缓存仍命中', async () => {
    const cache = { 'book-1:2026-09': { data: makeReport(), timestamp: Date.now() - 3 * 60 * 1000 } }
    localStorage.setItem('ai-insight-cache', JSON.stringify(cache))

    const bridge = makeBridge(makeReport({ transaction_count: 99 }))
    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" />)

    await waitFor(() => {
      expect(screen.getByText('42')).toBeInTheDocument()
    })
    expect(bridge.call).not.toHaveBeenCalled()
  })

  it('刷新按钮 bypass 新鲜缓存重查', async () => {
    const cache = { 'book-1:2026-09': { data: makeReport(), timestamp: Date.now() } }
    localStorage.setItem('ai-insight-cache', JSON.stringify(cache))

    const bridge = makeBridge(makeReport({ transaction_count: 99 }))
    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" />)
    await waitFor(() => expect(screen.getByText('42')).toBeInTheDocument())
    expect(bridge.call).not.toHaveBeenCalled()

    fireEvent.click(screen.getByText('刷新'))

    await waitFor(() => {
      expect(screen.getByText('99')).toBeInTheDocument()
    })
    expect(bridge.call).toHaveBeenCalledWith('ai.report', { bookId: 'book-1', period: '2026-09' }, { timeoutMs: 30000 })
  })

  it('云端 0 笔且本地有交易 → 提示 + 去同步按钮', async () => {
    const bridge = makeBridge(makeReport({ transaction_count: 0 }))
    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" localCount={5} />)

    await waitFor(() => {
      expect(screen.getByText('去同步')).toBeInTheDocument()
    })
    expect(screen.getByText('云端暂无本期数据（本地 5 笔待同步时请先同步）')).toBeInTheDocument()
    expect(screen.queryByText('暂无 AI 分析')).not.toBeInTheDocument()
  })

  it('localCount=0 时不显示去同步提示', async () => {
    const bridge = makeBridge(makeReport({ transaction_count: 0 }))
    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" localCount={0} />)

    await waitFor(() => {
      expect(screen.getByText('暂无 AI 分析')).toBeInTheDocument()
    })
    expect(screen.queryByText('去同步')).not.toBeInTheDocument()
  })

  it('去同步成功 → triggerAuthSync → 清缓存重查', async () => {
    const bridge = makeBridge(makeReport({ transaction_count: 0 }))
    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" localCount={5} />)
    await waitFor(() => expect(screen.getByText('去同步')).toBeInTheDocument())
    expect(bridge.call).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByText('去同步'))

    await waitFor(() => {
      expect(bridge.triggerAuthSync).toHaveBeenCalledTimes(1)
    })
    await waitFor(() => {
      expect(bridge.call).toHaveBeenCalledTimes(2)
    })
    // 重查结果写回缓存
    const cache = JSON.parse(localStorage.getItem('ai-insight-cache')!)
    expect(cache['book-1:2026-09']).toBeTruthy()
  })

  it('同步未完成（非 OK）→ 红条提示且不重查', async () => {
    const bridge = makeBridge(makeReport({ transaction_count: 0 }))
    bridge.triggerAuthSync = vi.fn().mockResolvedValue({ outcome: 'RETRYABLE' })
    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" localCount={5} />)
    await waitFor(() => expect(screen.getByText('去同步')).toBeInTheDocument())

    fireEvent.click(screen.getByText('去同步'))

    await waitFor(() => {
      expect(screen.getByText('同步未完成，请稍后重试')).toBeInTheDocument()
    })
    expect(bridge.call).toHaveBeenCalledTimes(1)
  })

  it('去同步抛错 → 显示错误信息', async () => {
    const bridge = makeBridge(makeReport({ transaction_count: 0 }))
    bridge.triggerAuthSync = vi.fn().mockRejectedValue(new Error('网络错误'))
    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" localCount={5} />)
    await waitFor(() => expect(screen.getByText('去同步')).toBeInTheDocument())

    fireEvent.click(screen.getByText('去同步'))

    await waitFor(() => {
      expect(screen.getByText('网络错误')).toBeInTheDocument()
    })
    expect(bridge.call).toHaveBeenCalledTimes(1)
  })

  // ─── T13-2 / 决策 #5：404 'Book not found' → 同步引导出口 ───

  it("404 'Book not found' → 专用引导行 + 去同步按钮，不显示原始英文错误", async () => {
    const bridge = makeBridge(
      undefined,
      new Error('Server error: HTTP 404: Book not found'),
    )
    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" />)

    await waitFor(() => {
      expect(screen.getByText('该账本尚未同步到云端')).toBeInTheDocument()
    })
    expect(screen.getByText('去同步')).toBeInTheDocument()
    expect(screen.queryByText(/Book not found/)).not.toBeInTheDocument()
  })

  it('非 Book-not-found 错误仍显示原始红字文案', async () => {
    const bridge = makeBridge(undefined, new Error('Server error: HTTP 500: boom'))
    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" />)

    await waitFor(() => {
      expect(screen.getByText('Server error: HTTP 500: boom')).toBeInTheDocument()
    })
    expect(screen.queryByText('该账本尚未同步到云端')).not.toBeInTheDocument()
    expect(screen.queryByText('去同步')).not.toBeInTheDocument()
  })

  it('404 分支去同步：triggerAuthSync OK → 绕过缓存重查成功 → 渲染报告', async () => {
    const report = makeReport({ transaction_count: 7 })
    const call = vi.fn()
      .mockRejectedValueOnce(new Error('Server error: HTTP 404: Book not found'))
      .mockResolvedValue(report)
    const bridge = {
      call,
      isAvailable: () => true,
      triggerAuthSync: vi.fn().mockResolvedValue({ outcome: 'OK' }),
    } as unknown as NativeBridge

    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" />)
    await waitFor(() => expect(screen.getByText('去同步')).toBeInTheDocument())
    expect(call).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByText('去同步'))

    await waitFor(() => {
      expect(screen.getByText('7')).toBeInTheDocument()
    })
    expect(bridge.triggerAuthSync).toHaveBeenCalledTimes(1)
    expect(call).toHaveBeenCalledTimes(2)
    expect(screen.queryByText('该账本尚未同步到云端')).not.toBeInTheDocument()
  })

  it('404 分支同步未完成（非 OK）→ 提示稍后重试，不重查', async () => {
    const bridge = makeBridge(
      undefined,
      new Error('Server error: HTTP 404: Book not found'),
    )
    bridge.triggerAuthSync = vi.fn().mockResolvedValue({ outcome: 'RETRYABLE' })
    render(<AiInsightCard bridge={bridge} bookId="book-1" period="2026-09" />)
    await waitFor(() => expect(screen.getByText('去同步')).toBeInTheDocument())

    fireEvent.click(screen.getByText('去同步'))

    await waitFor(() => {
      expect(screen.getByText('同步未完成，请稍后重试')).toBeInTheDocument()
    })
    expect(bridge.call).toHaveBeenCalledTimes(1)
  })
})
