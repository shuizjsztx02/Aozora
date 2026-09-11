/**
 * AiInsightCard — AI 月度洞察卡片。
 *
 * 调用 /ai/report 获取事实包 + AI 叙述，支持：
 * - 离线降级（无网络时不请求）
 * - 客户端缓存（localStorage，TTL 5 分钟；卡头"刷新"可绕过）
 * - 空数据诊断：云端 0 笔但本地有交易 → 提示 + 「去同步」按钮（T10-7 / 决策 #8）
 * - 404 'Book not found'：账本未同步 → 同样给出「去同步」引导（T13-2 / 决策 #5）
 * - 加载/错误状态
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { AiReportResponse } from '../../platform/bridge/bridge-types'
import type { NativeBridge } from '../../platform/bridge/native-bridge'

interface CacheEntry {
  data: AiReportResponse
  timestamp: number
}

const CACHE_KEY = 'ai-insight-cache'
// T10-7（决策 #8）：1h→5min。旧值下本地记账后洞察 1 小时不更新，观感即"链路不通"。
const CACHE_TTL_MS = 5 * 60 * 1000

function readCache(): Record<string, CacheEntry> {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

function writeCache(cache: Record<string, CacheEntry>): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(cache))
  } catch {
    // 忽略存储错误
  }
}

function getCachedReport(bookId: string, period: string): AiReportResponse | null {
  const cache = readCache()
  const key = `${bookId}:${period}`
  const entry = cache[key]
  if (!entry) return null
  if (Date.now() - entry.timestamp > CACHE_TTL_MS) {
    delete cache[key]
    writeCache(cache)
    return null
  }
  return entry.data
}

function setCachedReport(bookId: string, period: string, data: AiReportResponse): void {
  const cache = readCache()
  cache[`${bookId}:${period}`] = { data, timestamp: Date.now() }
  writeCache(cache)
}

function clearCachedReport(bookId: string, period: string): void {
  const cache = readCache()
  delete cache[`${bookId}:${period}`]
  writeCache(cache)
}

function isOnline(): boolean {
  return typeof navigator !== 'undefined' ? navigator.onLine : true
}

interface AiInsightCardProps {
  bridge: NativeBridge
  bookId: string | null
  period: string
  /** 本地该 bookId+period 的交易笔数（>0 且云端 0 笔时显示「去同步」提示，T10-7）。 */
  localCount?: number
}

export function AiInsightCard({ bridge, bookId, period, localCount }: AiInsightCardProps) {
  const [report, setReport] = useState<AiReportResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [syncError, setSyncError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)
  // 递增以丢弃过期响应（bookId/period 切换、重复点刷新/去同步）
  const reqIdRef = useRef(0)

  const loadReport = useCallback(async (force: boolean) => {
    if (!bookId) return

    if (!force) {
      const cached = getCachedReport(bookId, period)
      if (cached) {
        reqIdRef.current++ // 使在途请求作废，缓存结果直接展示
        setLoading(false)
        setError(null)
        setReport(cached)
        return
      }
    }

    // 离线时不请求
    if (!isOnline()) {
      return
    }

    // 检查 bridge 是否可用
    if (!bridge.isAvailable()) {
      return
    }

    const reqId = ++reqIdRef.current
    setLoading(true)
    setError(null)
    try {
      const data = await bridge.call('ai.report', { bookId, period }, { timeoutMs: 30000 }) as AiReportResponse
      if (reqId === reqIdRef.current) {
        setReport(data)
        setCachedReport(bookId, period, data)
      }
    } catch (err) {
      if (reqId === reqIdRef.current) {
        setError(err instanceof Error ? err.message : '加载失败')
        console.error('Failed to load AI insight:', err)
      }
    } finally {
      if (reqId === reqIdRef.current) {
        setLoading(false)
      }
    }
  }, [bridge, bookId, period])

  useEffect(() => { loadReport(false) }, [loadReport])

  // 「去同步」：跑一轮 push/pull，成功后清该 bookId:period 缓存并绕过缓存重查（决策 #8）
  const handleSync = useCallback(async () => {
    if (!bookId || syncing) return
    setSyncing(true)
    setSyncError(null)
    try {
      const { outcome } = await bridge.triggerAuthSync()
      if (outcome === 'OK') {
        clearCachedReport(bookId, period)
        setSyncing(false)
        await loadReport(true)
      } else {
        setSyncing(false)
        setSyncError('同步未完成，请稍后重试')
      }
    } catch (err) {
      setSyncing(false)
      setSyncError(err instanceof Error ? err.message : '同步失败，请稍后重试')
    }
  }, [bridge, bookId, period, syncing, loadReport])

  // 无数据时不渲染
  if (!bookId || (!report && !loading && !error)) {
    return null
  }

  // 空数据诊断：云端 0 笔但本地有交易 → 大概率存量交易还没 push 上去
  const showSyncHint = !!report && report.transaction_count === 0 && (localCount ?? 0) > 0

  return (
    <div className="glass-card p-4">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-base">✨</span>
        <h3 className="text-sm font-medium" style={{ color: 'var(--ink)' }}>AI 洞察</h3>
        <span className="text-[10px] px-1 py-0.5 rounded" style={{ background: 'var(--glass)', color: 'var(--accent)', border: '1px solid var(--glass-border)' }}>AI 生成</span>
        <div className="ml-auto flex items-center gap-2">
          {report?.narrative_cached && (
            <span className="text-[10px] text-ink3">已缓存</span>
          )}
          <button
            onClick={() => loadReport(true)}
            disabled={loading}
            className="text-[10px] px-1.5 py-0.5 rounded disabled:opacity-40"
            style={{ color: 'var(--accent)', background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
          >
            刷新
          </button>
        </div>
      </div>

      {loading && (
        <div className="text-xs text-ink3 py-2">正在生成洞察...</div>
      )}

      {error && (
        error.includes('Book not found') ? (
          // T13-2（决策 #5）：404 'Book not found' = 账本未 push 到云端，
          // 专用引导 + 去同步（复用 handleSync：triggerAuthSync → 清缓存重查）。
          <div className="text-xs text-ink3 py-2">
            该账本尚未同步到云端
            <div className="flex items-center gap-2 mt-2">
              <button
                onClick={handleSync}
                disabled={syncing}
                className="text-xs px-3 py-1.5 rounded-lg font-medium disabled:opacity-40"
                style={{ background: 'var(--accent)', color: 'white' }}
              >
                {syncing ? '同步中...' : '去同步'}
              </button>
              {syncError && <span className="text-xs text-expense">{syncError}</span>}
            </div>
          </div>
        ) : (
          <div className="text-xs text-expense py-2">
            {error}
          </div>
        )
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

          {showSyncHint ? (
            <div className="text-xs text-ink3 py-1">
              云端暂无本期数据（本地 {localCount} 笔待同步时请先同步）
              <div className="flex items-center gap-2 mt-2">
                <button
                  onClick={handleSync}
                  disabled={syncing}
                  className="text-xs px-3 py-1.5 rounded-lg font-medium disabled:opacity-40"
                  style={{ background: 'var(--accent)', color: 'white' }}
                >
                  {syncing ? '同步中...' : '去同步'}
                </button>
                {syncError && <span className="text-xs text-expense">{syncError}</span>}
              </div>
            </div>
          ) : report.narrative ? (
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
