/**
 * AiInsightCard — AI 月度洞察卡片。
 *
 * 调用 /ai/report 获取事实包 + AI 叙述，支持：
 * - 离线降级（无网络时不请求）
 * - 客户端缓存（基于 data_hash，TTL 1 小时）
 * - 加载/错误状态
 */

import { useState, useEffect } from 'react'
import type { AiReportResponse } from '../../platform/bridge/bridge-types'
import type { NativeBridge } from '../../platform/bridge/native-bridge'

interface CacheEntry {
  data: AiReportResponse
  timestamp: number
}

const CACHE_KEY = 'ai-insight-cache'
const CACHE_TTL_MS = 60 * 60 * 1000 // 1 小时

function getCache(): Record<string, CacheEntry> {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

function setCache(cache: Record<string, CacheEntry>): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(cache))
  } catch {
    // 忽略存储错误
  }
}

function getCachedReport(bookId: string, period: string): AiReportResponse | null {
  const cache = getCache()
  const key = `${bookId}:${period}`
  const entry = cache[key]
  if (!entry) return null
  if (Date.now() - entry.timestamp > CACHE_TTL_MS) {
    delete cache[key]
    setCache(cache)
    return null
  }
  return entry.data
}

function setCachedReport(bookId: string, period: string, data: AiReportResponse): void {
  const cache = getCache()
  cache[`${bookId}:${period}`] = { data, timestamp: Date.now() }
  setCache(cache)
}

function isOnline(): boolean {
  return typeof navigator !== 'undefined' ? navigator.onLine : true
}

interface AiInsightCardProps {
  bridge: NativeBridge
  bookId: string | null
  period: string
}

export function AiInsightCard({ bridge, bookId, period }: AiInsightCardProps) {
  const [report, setReport] = useState<AiReportResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    if (!bookId) return

    // 检查缓存
    const cached = getCachedReport(bookId, period)
    if (cached) {
      setReport(cached)
      return
    }

    // 离线时不请求
    if (!isOnline()) {
      return
    }

    // 检查 bridge 是否可用
    if (!bridge.isAvailable()) {
      return
    }

    let cancelled = false

    const fetchReport = async () => {
      setLoading(true)
      setError(null)
      try {
        const data = await bridge.call('ai.report', { bookId, period }, { timeoutMs: 30000 }) as AiReportResponse
        if (!cancelled) {
          setReport(data)
          setCachedReport(bookId, period, data)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : '加载失败')
          console.error('Failed to load AI insight:', err)
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    fetchReport()

    return () => { cancelled = true }
  }, [bridge, bookId, period])

  // 无数据时不渲染
  if (!bookId || (!report && !loading && !error)) {
    return null
  }

  return (
    <div className="glass-card p-4">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-base">✨</span>
        <h3 className="text-sm font-medium" style={{ color: 'var(--ink)' }}>AI 洞察</h3>
        <span className="ai-badge text-[10px] px-1 py-0.5 rounded" style={{ background: 'var(--glass)', color: 'var(--accent)', border: '1px solid var(--glass-border)' }}>AI 生成</span>
        {report?.narrative_cached && (
          <span className="text-[10px] text-ink3 ml-auto">已缓存</span>
        )}
      </div>

      {loading && (
        <div className="text-xs text-ink3 py-2">正在生成洞察...</div>
      )}

      {error && (
        <div className="text-xs text-expense py-2">
          {error}
        </div>
      )}

      {report && (
        <>
          {/* 事实包摘要 */}
          <div className="grid grid-cols-2 gap-2 text-xs mb-2">
            <div className="flex justify-between">
              <span className="text-ink3">交易笔数</span>
              <span className="money-num text-ink">{report.transaction_count}</span>
            </div>
            {report.largest_expense && (
              <div className="flex justify-between">
                <span className="text-ink3">最大支出</span>
                <span className="money-num text-expense">
                  ¥{(report.largest_expense.amount_cents / 100).toFixed(0)}
                </span>
              </div>
            )}
          </div>

          {/* AI 叙述 */}
          {report.narrative ? (
            <div
              className={`text-xs text-ink2 leading-relaxed cursor-pointer ${expanded ? '' : 'line-clamp-3'}`}
              onClick={() => setExpanded(!expanded)}
            >
              {report.narrative}
              {report.narrative.length > 100 && !expanded && (
                <span className="text-accent ml-1">...</span>
              )}
            </div>
          ) : (
            !loading && !error && (
              <div className="text-xs text-ink3">
                暂无 AI 分析
              </div>
            )
          )}
        </>
      )}
    </div>
  )
}
