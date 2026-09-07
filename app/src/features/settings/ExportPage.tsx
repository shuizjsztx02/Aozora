/**
 * ExportPage — 数据导出页。
 *
 * 功能：
 * - 选择账本 + 格式 → 生成导出
 * - 下载文件到本地（Downloads/Aozora/）
 * - JSON 格式下载后解析对账（合计与本地统计一致）
 * - 历史导出列表（最近 5 条）
 *
 * 状态流转：idle → creating → ready → downloading → downloaded (含对账结果) / failed
 *
 * 数据流：前端 → bridge → Kotlin ExportClient → 服务端 /exports
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { PageHeader } from '../../components/Layout'
import { useBridge } from '../../platform/hooks/use-bridge'
import type { ExportData, ExportFormat } from '../../platform/bridge/bridge-types'
import * as Money from '../../domain/money'

function formatCents(cents: number): string {
  return Money.formatDisplay(Money.cents(cents))
}

type ViewState =
  | { kind: 'idle' }
  | { kind: 'creating' }
  | { kind: 'ready'; exportData: ExportData }
  | { kind: 'downloading'; exportData: ExportData }
  | { kind: 'downloaded'; exportData: ExportData; localPath: string; filename: string; reconciliation: ReconciliationResult | null; contentTruncated?: boolean }
  | { kind: 'failed'; error: string; exportData?: ExportData }

interface ReconciliationResult {
  expectedCents: number
  exportedCents: number
  exportedCount: number
  localCount: number
  matched: boolean
}

interface Book {
  id: string
  name: string
}

export function ExportPage() {
  const bridge = useBridge()

  const [books, setBooks] = useState<Book[]>([])
  const [selectedBookId, setSelectedBookId] = useState<string>('')
  const [format, setFormat] = useState<ExportFormat>('json')
  const [viewState, setViewState] = useState<ViewState>({ kind: 'idle' })
  const [history, setHistory] = useState<ExportData[]>([])
  const [loadingBooks, setLoadingBooks] = useState(true)

  // 加载账本列表（仅在 bridge 变化时重跑；selectedBookId 走 ref 避免切换账本时重复请求）
  const selectedBookIdRef = useRef(selectedBookId)
  selectedBookIdRef.current = selectedBookId

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const list = (await bridge.call('book.list', {})) as Book[]
        if (cancelled) return
        setBooks(list)
        if (list.length > 0 && !selectedBookIdRef.current) {
          setSelectedBookId(list[0].id)
        }
      } catch (err) {
        console.error('Failed to load books:', err)
      } finally {
        if (!cancelled) setLoadingBooks(false)
      }
    })()
    return () => { cancelled = true }
  }, [bridge])

  // 加载历史导出
  const loadHistory = useCallback(async () => {
    try {
      const list = await bridge.listExports()
      setHistory(list.slice(0, 5))
    } catch (err) {
      console.error('Failed to load exports:', err)
    }
  }, [bridge])

  useEffect(() => {
    loadHistory()
  }, [loadHistory])

  // 生成导出
  const handleCreate = async () => {
    if (!selectedBookId) return
    setViewState({ kind: 'creating' })
    try {
      const exportData = await bridge.createExport({ bookId: selectedBookId, format })
      setViewState({ kind: 'ready', exportData })
      await loadHistory()
    } catch (err) {
      setViewState({ kind: 'failed', error: errorMessage(err) })
    }
  }

  // 下载 + 对账
  const handleDownload = async (exportData: ExportData) => {
    setViewState({ kind: 'downloading', exportData })
    try {
      const result = await bridge.downloadExport(exportData.id)
      let reconciliation: ReconciliationResult | null = null
      // F-M5-04b-F3: > 20MB 文件内容被截断，不做对账（解析必然不完整）
      const truncated = result.contentTruncated === true
      if (exportData.format === 'json' && result.content && !truncated) {
        reconciliation = await reconcile(bridge, selectedBookId, result.content)
      }
      setViewState({
        kind: 'downloaded',
        exportData,
        localPath: result.localPath,
        filename: result.filename,
        reconciliation,
        contentTruncated: truncated,
      })
    } catch (err) {
      setViewState({ kind: 'failed', error: errorMessage(err), exportData })
    }
  }

  return (
    <div className="min-h-screen">
      <PageHeader title="数据导出" />

      <div className="px-4 py-4 space-y-4 pb-20">
        {/* 账本选择 */}
        <div className="glass-card p-4">
          <div className="text-sm font-medium mb-2" style={{ color: 'var(--ink)' }}>
            选择账本
          </div>
          {loadingBooks ? (
            <div className="text-xs text-ink3">加载中...</div>
          ) : books.length === 0 ? (
            <div className="text-xs text-ink3">暂无账本，请先创建</div>
          ) : (
            <div className="space-y-1">
              {books.map(book => (
                <button
                  key={book.id}
                  onClick={() => setSelectedBookId(book.id)}
                  className="w-full text-left px-3 py-2 rounded-lg text-sm transition-colors flex items-center justify-between"
                  style={{
                    background: selectedBookId === book.id ? 'var(--glass)' : 'transparent',
                    color: selectedBookId === book.id ? 'var(--accent)' : 'var(--ink2)',
                  }}
                >
                  <span>{book.name || '默认账本'}</span>
                  {selectedBookId === book.id && <span>✓</span>}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* 格式选择 */}
        <div className="glass-card p-4">
          <div className="text-sm font-medium mb-2" style={{ color: 'var(--ink)' }}>
            导出格式
          </div>
          <div className="flex gap-2">
            {(['json', 'csv'] as ExportFormat[]).map(f => (
              <button
                key={f}
                onClick={() => setFormat(f)}
                className="flex-1 px-3 py-2 rounded-lg text-sm transition-colors"
                style={{
                  background: format === f ? 'var(--accent)' : 'var(--glass)',
                  color: format === f ? 'white' : 'var(--ink2)',
                }}
              >
                {f.toUpperCase()}
                {f === 'json' && <span className="ml-1 text-[10px] opacity-70">（含对账）</span>}
              </button>
            ))}
          </div>
          <div className="text-xs text-ink3 mt-2">
            文件保存到 Downloads/Aozora/ 目录
          </div>
        </div>

        {/* 操作按钮 */}
        <button
          onClick={handleCreate}
          disabled={!selectedBookId || viewState.kind === 'creating'}
          className="w-full py-3 rounded-lg text-sm font-medium transition-colors"
          style={{
            background: !selectedBookId || viewState.kind === 'creating' ? 'var(--glass-border)' : 'var(--accent)',
            color: 'white',
          }}
        >
          {viewState.kind === 'creating' ? '生成中...' : '生成导出'}
        </button>

        {/* 状态卡片 */}
        <StatusCard state={viewState} onDownload={handleDownload} onRetry={handleCreate} />

        {/* 历史导出 */}
        {history.length > 0 && (
          <div className="glass-card overflow-hidden">
            <div className="px-4 py-2 text-xs text-ink3 font-medium border-b" style={{ borderColor: 'var(--glass-border)' }}>
              最近导出（{history.length}）
            </div>
            {history.map(exp => (
              <div key={exp.id} className="px-4 py-3 row-line flex items-center gap-3">
                <span className="text-lg">{exp.format === 'json' ? '📄' : '📊'}</span>
                <div className="flex-1 min-w-0">
                  <div className="text-sm truncate" style={{ color: 'var(--ink)' }}>
                    {exp.format.toUpperCase()} · {formatFileSize(exp.fileSize)}
                  </div>
                  <div className="text-xs text-ink3">
                    {formatTime(exp.createdAt)} · {statusLabel(exp.status)}
                  </div>
                </div>
                {exp.status === 'completed' && viewState.kind !== 'downloading' && (
                  <button
                    onClick={() => handleDownload(exp)}
                    className="text-xs px-2 py-1 rounded"
                    style={{ background: 'var(--glass)', color: 'var(--accent)' }}
                  >
                    下载
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function StatusCard({
  state,
  onDownload,
  onRetry,
}: {
  state: ViewState
  onDownload: (exp: ExportData) => void
  onRetry: () => void
}) {
  switch (state.kind) {
    case 'idle':
      return null
    case 'creating':
      return (
        <div className="glass-card p-4 flex items-center gap-3">
          <span className="text-lg animate-spin">⏳</span>
          <div>
            <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>正在生成导出...</div>
            <div className="text-xs text-ink3">服务端同步写入文件</div>
          </div>
        </div>
      )
    case 'ready':
      return (
        <div className="glass-card p-4">
          <div className="flex items-center gap-3 mb-3">
            <span className="text-lg">✅</span>
            <div>
              <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>导出已就绪</div>
              <div className="text-xs text-ink3">
                {formatFileSize(state.exportData.fileSize)} · 24 小时后过期
              </div>
            </div>
          </div>
          <button
            onClick={() => onDownload(state.exportData)}
            className="w-full py-2 rounded-lg text-sm font-medium"
            style={{ background: 'var(--accent)', color: 'white' }}
          >
            下载文件
          </button>
        </div>
      )
    case 'downloading':
      return (
        <div className="glass-card p-4 flex items-center gap-3">
          <span className="text-lg animate-spin">⬇️</span>
          <div className="text-sm" style={{ color: 'var(--ink)' }}>正在下载...</div>
        </div>
      )
    case 'downloaded':
      return (
        <div className="glass-card p-4">
          <div className="flex items-center gap-3 mb-2">
            <span className="text-lg">📥</span>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium truncate" style={{ color: 'var(--ink)' }}>
                {state.filename}
              </div>
              <div className="text-xs text-ink3 truncate">{state.localPath}</div>
            </div>
          </div>
          {state.contentTruncated && (
            <div className="text-xs text-ink3 mt-2 py-1.5 px-2 rounded" style={{ background: 'var(--glass)' }}>
              ⚠️ 文件较大（超过 20MB），对账已跳过。文件已完整保存，可通过文件管理器查看。
            </div>
          )}
          {state.reconciliation && <ReconciliationView result={state.reconciliation} />}
          {state.exportData.format === 'csv' && !state.contentTruncated && (
            <div className="text-xs text-ink3 mt-2">
              CSV 文件已下载，可通过文件管理器查看
            </div>
          )}
        </div>
      )
    case 'failed':
      return (
        <div className="glass-card p-4 border-l-4" style={{ borderColor: 'var(--expense)' }}>
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>导出失败</div>
              <div className="text-xs text-ink3 mt-1">{state.error}</div>
            </div>
            <button
              onClick={onRetry}
              className="text-xs px-3 py-1.5 rounded-lg"
              style={{ background: 'var(--accent)', color: 'white' }}
            >
              重试
            </button>
          </div>
        </div>
      )
  }
}

function ReconciliationView({ result }: { result: ReconciliationResult }) {
  const matchColor = result.matched ? 'var(--income)' : 'var(--expense)'
  return (
    <div className="mt-3 pt-3 border-t text-xs space-y-1" style={{ borderColor: 'var(--glass-border)' }}>
      <div className="flex justify-between">
        <span className="text-ink3">本地合计</span>
        <span className="money-num" style={{ color: 'var(--ink)' }}>
          {formatCents(result.expectedCents)}
        </span>
      </div>
      <div className="flex justify-between">
        <span className="text-ink3">导出合计（{result.exportedCount} 笔）</span>
        <span className="money-num" style={{ color: 'var(--ink)' }}>
          {formatCents(result.exportedCents)}
        </span>
      </div>
      <div className="flex justify-between pt-1 font-medium">
        <span style={{ color: matchColor }}>
          {result.matched ? '✓ 对账一致' : '✗ 对账不一致'}
        </span>
        {!result.matched && (
          <span style={{ color: matchColor }}>
            差额 {Money.formatSigned(result.exportedCents - result.expectedCents)}
          </span>
        )}
      </div>
    </div>
  )
}

// ─── 辅助函数 ───

async function reconcile(
  bridge: ReturnType<typeof useBridge>,
  bookId: string,
  content: string,
): Promise<ReconciliationResult> {
  // 解析导出 JSON
  let exported: { amount_cents: number; type: string }[]
  try {
    exported = JSON.parse(content)
    if (!Array.isArray(exported)) throw new Error('not array')
  } catch {
    return {
      expectedCents: 0,
      exportedCents: 0,
      exportedCount: 0,
      localCount: 0,
      matched: false,
    }
  }

  // 计算导出合计（仅 expense 计为支出，与账本统计口径一致）
  const exportedCents = exported.reduce(
    (sum, row) => sum + (row.type === 'expense' ? row.amount_cents : 0),
    0,
  )

  // 查本地交易
  let localTxs: { amountCents: number; type: string; deletedAt: number | null }[]
  try {
    localTxs = (await bridge.call('transaction.listByBook', { bookId })) as typeof localTxs
  } catch {
    localTxs = []
  }
  const activeTxs = localTxs.filter(tx => tx.deletedAt === null)
  const expectedCents = activeTxs.reduce(
    (sum, tx) => sum + (tx.type === 'expense' ? tx.amountCents : 0),
    0,
  )

  // matched 口径与金额对齐：仅比较 expense 笔数，避免 income 缺失触发"差额 ¥0.00"的迷惑提示
  const activeExpenseCount = activeTxs.filter(tx => tx.type === 'expense').length
  const exportedExpenseCount = exported.filter(row => row.type === 'expense').length

  return {
    expectedCents,
    exportedCents,
    exportedCount: exported.length,
    localCount: activeTxs.length,
    matched: expectedCents === exportedCents && activeExpenseCount === exportedExpenseCount,
  }
}

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message
  if (typeof err === 'string') return err
  return '未知错误'
}

function formatFileSize(bytes?: number | null): string {
  if (bytes == null) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso)
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  } catch {
    return iso
  }
}

function statusLabel(status: string): string {
  switch (status) {
    case 'pending': return '生成中'
    case 'completed': return '已完成'
    case 'failed': return '失败'
    case 'expired': return '已过期'
    default: return status
  }
}
