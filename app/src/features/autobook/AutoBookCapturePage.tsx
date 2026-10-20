/**
 * AutoBookCapturePage — 待确认箱（T21-8 / REQ-83）。
 *
 * 展示自动记账采集后进入 `pending_confirm` 的条目，让用户逐条处置：
 *   - 确认：按解析结果直接入账（走 `autobook.resolve_capture` confirm）
 *   - 修改后确认：展开编辑分类 / 金额 / 收支 / 备注 / 日期，再入账
 *   - 忽略：不入账、不留半成品（`autobook.resolve_capture` ignore）
 *
 * L2 合并的条目显示「已由 N 个来源确认」徽标（`multiSource`）；L3 疑似重复
 * 由服务端 / 本地规则在 `parsedJson` 中置 `hint: "possible_duplicate"` 字段，
 * 此处只渲染一句提示，不预选合并也不预选删除（决策 #6）。
 *
 * 自动入账（高置信快速通道）的条目**不**出现在此页面——它们走既有「立即可见
 * + 撤销」通道，在明细页与首页流水可见（决策 #16）。
 */

import { useState, useEffect, useCallback, useMemo } from 'react'
import { PageHeader } from '../../components/Layout'
import { TxListRow } from '../../components/TxListRow'
import { useBridge } from '../../platform/hooks/use-bridge'
import * as Money from '../../domain/money'
import type {
  AutoBookCaptureData,
  AutoBookCaptureChannel,
  AutoBookCaptureOverrides,
  AutoBookStatusData,
  CategoryData,
} from '../../platform/bridge/bridge-types'

function channelLabel(ch: AutoBookCaptureChannel): string {
  return ch === 'accessibility' ? '无障碍' : '通知'
}

/** 从 parsedJson 里读取分类名（`category` 字段，模型/本地规则同形输出） */
function parsedCategory(parsed: Record<string, unknown> | null): string | null {
  const v = parsed?.category
  return typeof v === 'string' && v.trim() ? v.trim() : null
}

/** 从 parsedJson 读取金额（分）。非数值 / 缺失返回 null */
function parsedAmount(parsed: Record<string, unknown> | null): number | null {
  const v = parsed?.amountCents
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

/** 从 parsedJson 读取收支方向（缺省按支出，与既有记账口径一致） */
function parsedType(parsed: Record<string, unknown> | null): 'expense' | 'income' {
  return parsed?.type === 'income' ? 'income' : 'expense'
}

/** 从 parsedJson 读取备注 */
function parsedNote(parsed: Record<string, unknown> | null): string {
  const v = parsed?.note
  return typeof v === 'string' ? v : ''
}

/** 从 parsedJson 读取业务日期（YYYY-MM-DD），缺失返回 null */
function parsedBizDate(parsed: Record<string, unknown> | null): string | null {
  const v = parsed?.bizDate
  return typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : null
}

/** parsedJson 的「疑似重复」提示（来自 L3 标记，仅展示） */
function duplicateHint(parsed: Record<string, unknown> | null): string | null {
  const v = parsed?.hint
  if (v === 'possible_duplicate') return '可能与另一笔同额账目重复'
  return null
}

/** postedAt（epoch ms）→ HH:mm */
function formatTime(epochMs: number): string {
  const d = new Date(epochMs)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${hh}:${mm}`
}

/** 根据分类名（parsedJson.category）匹配分类 id；未命中返回 null */
function matchCategoryId(
  categories: CategoryData[],
  name: string | null,
  direction: 'expense' | 'income',
): string | null {
  if (!name) return null
  // 先按「收支方向 + 名字」精确匹配
  const exact = categories.find(c => c.kind === direction && c.name === name)
  if (exact) return exact.id
  // 退一步只按名字
  const byName = categories.find(c => c.name === name)
  return byName?.id ?? null
}

/** 置信度徽标：低置信（< 0.7）才展示，提示用户「AI 拿不太准」 */
function ConfidenceBadge({ confidence }: { confidence: number | null }) {
  if (confidence == null || confidence >= 0.7) return null
  return (
    <span
      className="text-[10px] px-1.5 py-0.5 rounded"
      style={{
        background: 'rgba(234, 88, 12, 0.1)',
        color: '#c2410c',
        border: '1px solid rgba(234, 88, 12, 0.2)',
      }}
      data-testid="capture-low-confidence"
    >
      AI 拿不太准
    </span>
  )
}

/** 多来源徽标（L2 合并：两个独立通道都抓到了同一笔） */
function MultiSourceBadge({ capture }: { capture: AutoBookCaptureData }) {
  if (!capture.multiSource) return null
  return (
    <span
      className="text-[10px] px-1.5 py-0.5 rounded"
      style={{
        background: 'rgba(34, 197, 94, 0.1)',
        color: '#15803d',
        border: '1px solid rgba(34, 197, 94, 0.2)',
      }}
      data-testid={`capture-multi-source-${capture.captureId}`}
    >
      已由 {capture.sourceCount} 个来源确认
    </span>
  )
}

interface CaptureCardProps {
  capture: AutoBookCaptureData
  categories: CategoryData[]
  onResolve: (
    id: string,
    resolution: 'confirm' | 'confirm_modified',
    overrides?: AutoBookCaptureOverrides,
  ) => Promise<void>
  onIgnore: (id: string) => Promise<void>
}

/** 单条捕获卡片——含展开编辑面板 */
function CaptureCard({ capture, categories, onResolve, onIgnore }: CaptureCardProps) {
  const [expanded, setExpanded] = useState(false)
  const [busy, setBusy] = useState<'confirm' | 'ignore' | null>(null)

  // 编辑面板字段（以 parsedJson 为初值；改后 confirm 时作为 overrides 下发）
  const direction = parsedType(capture.parsedJson)
  const [editType, setEditType] = useState<'expense' | 'income'>(direction)
  const amountInitial = parsedAmount(capture.parsedJson)
  const [editAmount, setEditAmount] = useState<string>(
    amountInitial != null ? Money.formatDisplay(Money.cents(amountInitial)).replace('¥', '').trim() : '',
  )
  const [editCategoryId, setEditCategoryId] = useState<string | null>(
    matchCategoryId(categories, parsedCategory(capture.parsedJson), direction),
  )
  const [editNote, setEditNote] = useState<string>(parsedNote(capture.parsedJson))
  const [editBizDate, setEditBizDate] = useState<string>(
    parsedBizDate(capture.parsedJson) ?? new Date().toISOString().slice(0, 10),
  )

  const amountCents = useMemo(() => {
    const n = Number(editAmount)
    if (!Number.isFinite(n) || n <= 0) return null
    return Money.fromString(editAmount) as number
  }, [editAmount])

  const title = parsedCategory(capture.parsedJson)
    || capture.merchantKey
    || capture.sourceApp
    || (capture.sourceChannel === 'accessibility' ? '页面捕获' : '通知捕获')
  // 分类图标与明细页同源：按分类名在本地分类表里找 icon（找不到时 CategoryIcon 按名回退）
  const categoryIcon = useMemo(
    () => categories.find(c => c.name === title)?.icon,
    [categories, title],
  )

  const hint = duplicateHint(capture.parsedJson)
  const amount = parsedAmount(capture.parsedJson)
  const isResolved = capture.status === 'committed' || capture.status === 'ignored'

  const handleConfirm = async () => {
    setBusy('confirm')
    try {
      await onResolve(capture.captureId, 'confirm')
    } finally {
      setBusy(null)
    }
  }

  const handleConfirmModified = async () => {
    if (amountCents == null) return
    setBusy('confirm')
    const overrides: AutoBookCaptureOverrides = {}
    if (editCategoryId !== matchCategoryId(categories, parsedCategory(capture.parsedJson), direction)) {
      if (editCategoryId) overrides.categoryId = editCategoryId
    }
    if (amountCents !== amountInitial) overrides.amountCents = amountCents
    if (editType !== direction) overrides.type = editType
    if (editNote !== parsedNote(capture.parsedJson)) overrides.note = editNote
    if (editBizDate !== parsedBizDate(capture.parsedJson)) overrides.bizDate = editBizDate
    try {
      await onResolve(capture.captureId, 'confirm_modified', overrides)
      setExpanded(false)
    } finally {
      setBusy(null)
    }
  }

  const handleIgnore = async () => {
    setBusy('ignore')
    try {
      await onIgnore(capture.captureId)
    } finally {
      setBusy(null)
    }
  }

  // 方向变更时重置分类（避免支出分类出现在收入方向上）
  useEffect(() => {
    if (editType !== direction) {
      const matched = matchCategoryId(categories, parsedCategory(capture.parsedJson), editType)
      setEditCategoryId(matched)
    }
    // 只在 editType 变化时重置
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editType])

  return (
    <div className="glass-card p-3 space-y-2" data-testid={`capture-card-${capture.captureId}`}>
      {/* 头部：与明细页流水行共用 TxListRow（T21-10 真机回归：卡片视觉曾与手动记账不一致） */}
      <div data-testid={`capture-amount-${capture.captureId}`}>
        <TxListRow
          icon={categoryIcon}
          iconName={title}
          title={title}
          sub={`${capture.sourceApp ?? channelLabel(capture.sourceChannel)} · ${formatTime(capture.postedAt)}`}
          amountCents={amount ?? 0}
          isExpense={direction === 'expense'}
          amountLabel={amount == null ? '—' : undefined}
        />
      </div>

      {/* 徽标行：低置信 / 多来源 / 疑似重复 */}
      {(capture.confidence != null && capture.confidence < 0.7 || capture.multiSource || hint) && (
        <div className="flex flex-wrap gap-1.5">
          <ConfidenceBadge confidence={capture.confidence} />
          <MultiSourceBadge capture={capture} />
          {hint && (
            <span
              className="text-[10px] px-1.5 py-0.5 rounded"
              style={{
                background: 'rgba(234, 179, 8, 0.1)',
                color: '#a16207',
                border: '1px solid rgba(234, 179, 8, 0.2)',
              }}
              data-testid={`capture-duplicate-hint-${capture.captureId}`}
            >
              {hint}
            </span>
          )}
        </div>
      )}

      {/* 已处置态：仅留一行状态文案 */}
      {isResolved && (
        <div className="text-xs" style={{ color: 'var(--ink3)' }} data-testid={`capture-resolved-${capture.captureId}`}>
          {capture.status === 'committed' ? '已入账' : '已忽略'}
        </div>
      )}

      {/* 未处置：操作按钮行 */}
      {!isResolved && (
        <>
          <div className="flex items-center gap-2 pt-1">
            <button
              onClick={handleConfirm}
              disabled={busy !== null}
              className="flex-1 py-1.5 rounded-lg text-xs font-medium text-white disabled:opacity-40"
              style={{ background: 'var(--accent)' }}
              data-testid={`capture-confirm-${capture.captureId}`}
            >
              {busy === 'confirm' ? '处理中…' : '✓ 确认'}
            </button>
            <button
              onClick={() => setExpanded(e => !e)}
              disabled={busy !== null}
              className="flex-1 py-1.5 rounded-lg text-xs font-medium disabled:opacity-40"
              style={{ background: 'var(--glass)', color: 'var(--ink2)', border: '1px solid var(--glass-border)' }}
              data-testid={`capture-edit-${capture.captureId}`}
            >
              ✎ 修改
            </button>
            <button
              onClick={handleIgnore}
              disabled={busy !== null}
              className="flex-1 py-1.5 rounded-lg text-xs disabled:opacity-40"
              style={{ color: 'var(--ink3)' }}
              data-testid={`capture-ignore-${capture.captureId}`}
            >
              {busy === 'ignore' ? '…' : '× 忽略'}
            </button>
          </div>

          {/* 展开的修改面板 */}
          {expanded && (
            <div
              className="space-y-2 pt-2"
              style={{ borderTop: '1px solid var(--glass-border)' }}
              data-testid={`capture-edit-panel-${capture.captureId}`}
            >
              {/* 收支方向 chip */}
              <div className="flex gap-2">
                {(['expense', 'income'] as const).map(t => (
                  <button
                    key={t}
                    onClick={() => setEditType(t)}
                    className="flex-1 py-1.5 rounded-lg text-xs"
                    style={{
                      background: editType === t
                        ? (t === 'expense' ? 'var(--expense)' : 'var(--income)')
                        : 'var(--glass)',
                      color: editType === t ? 'white' : 'var(--ink2)',
                      border: '1px solid var(--glass-border)',
                    }}
                    data-testid={`capture-edit-type-${t}`}
                  >
                    {t === 'expense' ? '支出' : '收入'}
                  </button>
                ))}
              </div>

              {/* 金额 */}
              <input
                type="text"
                inputMode="decimal"
                value={editAmount}
                onChange={e => setEditAmount(e.target.value)}
                placeholder="金额"
                className="w-full px-3 py-2 rounded-lg text-sm outline-none money-num"
                style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
                data-testid={`capture-edit-amount-${capture.captureId}`}
              />

              {/* 分类 */}
              <select
                value={editCategoryId ?? ''}
                onChange={e => setEditCategoryId(e.target.value || null)}
                className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
                data-testid={`capture-edit-category-${capture.captureId}`}
              >
                <option value="">未分类</option>
                {categories
                  .filter(c => c.kind === editType)
                  .map(c => (
                    <option key={c.id} value={c.id}>
                      {c.icon ? `${c.icon} ` : ''}{c.name}
                    </option>
                  ))}
              </select>

              {/* 备注 */}
              <input
                type="text"
                value={editNote}
                onChange={e => setEditNote(e.target.value)}
                placeholder="备注（可选）"
                maxLength={100}
                className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
                data-testid={`capture-edit-note-${capture.captureId}`}
              />

              {/* 日期 */}
              <input
                type="date"
                value={editBizDate}
                onChange={e => setEditBizDate(e.target.value)}
                className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
                data-testid={`capture-edit-date-${capture.captureId}`}
              />

              {/* 修改后确认 */}
              <button
                onClick={handleConfirmModified}
                disabled={amountCents == null || busy !== null}
                className="w-full py-2 rounded-lg text-sm font-medium text-white disabled:opacity-40"
                style={{ background: 'var(--accent)' }}
                data-testid={`capture-edit-save-${capture.captureId}`}
              >
                {busy === 'confirm' ? '处理中…' : '按修改入账'}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

export function AutoBookCapturePage() {
  const bridge = useBridge()
  const [status, setStatus] = useState<AutoBookStatusData | null>(null)
  const [captures, setCaptures] = useState<AutoBookCaptureData[]>([])
  const [categories, setCategories] = useState<CategoryData[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const [s, list, cats] = await Promise.all([
        bridge.call<AutoBookStatusData>('autobook.get_status'),
        bridge.call<AutoBookCaptureData[]>('autobook.list_captures'),
        bridge.call<CategoryData[]>('category.list', {}),
      ])
      setStatus(s)
      setCaptures(list)
      setCategories(cats)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [bridge])

  useEffect(() => { load() }, [load])

  const handleResolve = useCallback(async (
    id: string,
    resolution: 'confirm' | 'confirm_modified',
    overrides?: AutoBookCaptureOverrides,
  ) => {
    setError(null)
    try {
      await bridge.call('autobook.resolve_capture', {
        captureId: id,
        resolution,
        ...(overrides ? { overrides } : {}),
      })
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : '处置失败')
    }
  }, [bridge, load])

  const handleIgnore = useCallback(async (id: string) => {
    setError(null)
    try {
      await bridge.call('autobook.resolve_capture', { captureId: id, resolution: 'ignore' })
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : '忽略失败')
    }
  }, [bridge, load])

  // 待处置（pending_confirm）排在前面，处置完的（committed/ignored）留在底部渐隐
  const sorted = useMemo(() => {
    const order: Record<string, number> = {
      pending_confirm: 0,
      parsing: 1,
      captured: 2,
      failed: 3,
      committed: 4,
      ignored: 5,
    }
    return [...captures].sort((a, b) => {
      const d = (order[a.status] ?? 99) - (order[b.status] ?? 99)
      if (d !== 0) return d
      return b.postedAt - a.postedAt
    })
  }, [captures])

  const pendingCount = captures.filter(c => c.status === 'pending_confirm').length

  return (
    <div className="min-h-screen">
      <PageHeader title="待确认" back />

      <div className="px-4 py-3 space-y-3 pb-20">
        {error && (
          <div
            className="text-xs px-3 py-2 rounded-lg"
            style={{ background: 'rgba(220, 38, 38, 0.1)', color: '#dc2626' }}
            data-testid="capture-error"
          >
            {error}
          </div>
        )}

        {/* 顶部提示：自动记账未启用或两类授权都未开时给提示（但仍展示历史待确认） */}
        {status && !status.enabled && (
          <div
            className="text-xs px-3 py-2 rounded-lg"
            style={{ background: 'rgba(234, 179, 8, 0.08)', color: '#a16207' }}
            data-testid="capture-disabled-hint"
          >
            自动记账未开启；前往「设置 → 自动记账」开启后，新支付会自动出现在这里
          </div>
        )}

        {/* 顶部小结 */}
        {!loading && captures.length > 0 && (
          <div className="text-xs" style={{ color: 'var(--ink3)' }} data-testid="capture-summary">
            共 {captures.length} 条{pendingCount > 0 ? `，${pendingCount} 条待确认` : ''}
          </div>
        )}

        {loading ? (
          <div className="glass-card p-4 text-xs text-center" style={{ color: 'var(--ink3)' }}>
            加载中…
          </div>
        ) : captures.length === 0 ? (
          <div className="glass-card p-8 text-center space-y-2" data-testid="capture-empty">
            <div className="text-3xl">🌤️</div>
            <div className="text-sm font-medium" style={{ color: 'var(--ink2)' }}>
              没有需要确认的支付
            </div>
            <div className="text-xs" style={{ color: 'var(--ink3)' }}>
              小贝会在收到支付类通知或你在支付页时自动记录
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            {sorted.map(c => (
              <CaptureCard
                key={c.captureId}
                capture={c}
                categories={categories}
                onResolve={handleResolve}
                onIgnore={handleIgnore}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
