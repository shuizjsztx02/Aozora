/**
 * AccountsPage — 账户管理页。
 *
 * 列表展示 + 新增账户 + 编辑已有账户（T7-5 / REQ-5：名称 + 分类 + 图标）
 * + T9-5b（REQ-11）：默认勾选（radio 语义）与长按 ↑↓ 排序模式（决策 #4）。
 * 顺序/默认写 sync_meta（meta.set）；列表顺序与 isDefault 由 native
 * account.list 出口统一整形（决策 #2），前端不再重排。排序模式下禁用进编辑。
 */

import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { PageHeader } from '../../components/Layout'
import { useBridge } from '../../platform/hooks/use-bridge'
import { newUuid } from '../../platform/uuid'
import { CategoryIcon } from '../../components/CategoryIcon'
import { IconPicker } from '../../components/IconPicker'
import type { AccountData } from '../../platform/bridge/bridge-types'
import { META_KEY_ACCOUNT_ORDER, META_KEY_DEFAULT_ACCOUNT_ID, serializeIdOrder } from '../../domain/meta'
import { formatSigned, fromYuan } from '../../domain/money'

/** REQ-5：预置账户分类；用户可经"+ 自定义"追加，取消选择即不填（accountType null）。 */
const DEFAULT_ACCOUNT_TYPES = ['储蓄卡', '信用卡', '花呗', '微信', '支付宝', '现金', '余额宝', '零钱通']

/**
 * REQ-69（T18-6）：「当前余额」输入（元）→ 分。余额**允许负值**（信用卡欠款），
 * 而 `money.ts` 的 fromYuan 只收非负值——故取符号后仍复用它（沿用其四舍五入与
 * 安全整数校验）。空串 = 0（未校准）；返回 null = 格式非法，提交时报错、不静默改值。
 */
function parseBalanceYuan(raw: string): number | null {
  const s = raw.trim()
  if (!s) return 0
  if (!/^-?\d+(\.\d{1,2})?$/.test(s)) return null
  try {
    const cents = fromYuan(Math.abs(Number(s)))
    return s.startsWith('-') ? -cents : cents
  } catch {
    return null
  }
}

/** 分 → 元输入串（无 ¥ 前缀；整数余额不带小数尾巴，便于编辑）。 */
function balanceYuanInput(cents: number): string {
  const abs = Math.abs(cents)
  const body = abs % 100 === 0
    ? String(abs / 100)
    : `${Math.trunc(abs / 100)}.${String(abs % 100).padStart(2, '0')}`
  return cents < 0 ? `-${body}` : body
}

export function AccountsPage() {
  const bridge = useBridge()
  const [accounts, setAccounts] = useState<AccountData[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // T7-5：新建/编辑共用一个表单 — editing 非空即编辑态
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<AccountData | null>(null)
  const [name, setName] = useState('')
  // T9-6（REQ-13）：图标值并入共享 IconPicker — emoji/自定义文本/照片 data URI 单一受控值
  const [icon, setIcon] = useState<string | null>('💰')
  const [accountType, setAccountType] = useState('')
  // REQ-69（T18-6）：当前余额（元，**可负** = 信用卡欠款）；'' 视同 0
  const [balanceYuan, setBalanceYuan] = useState('0')
  const [addedTypes, setAddedTypes] = useState<string[]>([])
  const [showTypeInput, setShowTypeInput] = useState(false)
  const [typeInput, setTypeInput] = useState('')

  // T9-5b（REQ-11）：长按 500ms 进入排序模式（决策 #4）
  const [reorderMode, setReorderMode] = useState(false)

  const load = useCallback(async () => {
    try {
      const list = await bridge.call('account.list', {}) as AccountData[]
      setAccounts(list)
    } catch (err) {
      console.error('Failed to load accounts:', err)
    } finally {
      setLoading(false)
    }
  }, [bridge])

  useEffect(() => { load() }, [load])

  // 分类 chip = 预置 8 种 ∪ 已有账户用过的自定义分类 ∪ 本次新增
  const typeOptions = useMemo(() => {
    const used = accounts
      .map(a => a.accountType)
      .filter((t): t is string => !!t && !DEFAULT_ACCOUNT_TYPES.includes(t))
    return [...DEFAULT_ACCOUNT_TYPES, ...Array.from(new Set([...used, ...addedTypes]))]
  }, [accounts, addedTypes])

  const openForm = (acc: AccountData | null) => {
    if (reorderMode) return // 排序模式禁用进编辑（决策 #4）
    setError(null)
    setEditing(acc)
    setName(acc?.name ?? '')
    // 非预置值（旧自定义文本 / 照片 data URI）由 IconPicker 自动回填对应控件
    setIcon(acc?.icon ?? '💰')
    setAccountType(acc?.accountType ?? '')
    setBalanceYuan(balanceYuanInput(acc?.balanceCents ?? 0))
    setAddedTypes([])
    setShowTypeInput(false)
    setTypeInput('')
    setShowForm(true)
  }

  const closeForm = () => {
    setShowForm(false)
    setEditing(null)
    setError(null)
  }

  const commitCustomType = () => {
    const t = typeInput.trim()
    if (t) {
      if (!typeOptions.includes(t)) setAddedTypes(prev => [...prev, t])
      setAccountType(t)
    }
    setTypeInput('')
    setShowTypeInput(false)
  }

  const handleSubmit = async () => {
    const trimmed = name.trim()
    if (!trimmed) return
    setError(null)
    // FIX-006 / BUG-007: 同名检测（编辑态排除自身）
    const lower = trimmed.toLowerCase()
    if (accounts.some(a => a.id !== editing?.id && a.name.toLowerCase() === lower)) {
      setError('该账户名已存在')
      return
    }
    const finalIcon = icon || '💰'
    // REQ-69（T18-6）：余额恒传**绝对值**（决策 #4 的幂等铁律），不是增量
    const balanceCents = parseBalanceYuan(balanceYuan)
    if (balanceCents === null) {
      setError('当前余额请填数字（最多两位小数，欠款填负数）')
      return
    }
    try {
      // 必须走 NativeBridge 封装：native 侧期望 {account, mutationId} 包裹结构，且 ownerId 必填
      if (editing) {
        // 决策 #4：account.update 局部合并，version 必须递增
        await bridge.updateAccount(
          { id: editing.id, ownerId: 'local', name: trimmed, icon: finalIcon, accountType: accountType || null, balanceCents, version: editing.version + 1 },
          newUuid()
        )
      } else {
        // 服务端 sync/ai 契约要求 UUID（第六轮 Bug-5.2）
        await bridge.insertAccount(
          { id: newUuid(), ownerId: 'local', name: trimmed, icon: finalIcon, accountType: accountType || null, balanceCents, version: 1 },
          newUuid()
        )
      }
      closeForm()
      await load()
    } catch (err) {
      console.error('Failed to save account:', err)
      setError(editing ? '更新账户失败，请重试' : '创建账户失败，请重试。若多次失败请通过意见反馈报告问题')
      // 失败时表单与输入保留，供用户改后重试
    }
  }

  /** 勾默认（单选语义=覆写同键）。 */
  const handleSetDefault = async (acc: AccountData) => {
    setError(null)
    try {
      await bridge.setMeta(META_KEY_DEFAULT_ACCOUNT_ID, acc.id)
      await load()
    } catch (err) {
      console.error('Failed to set default account:', err)
      setError('设置默认账户失败，请重试')
    }
  }

  /** ↑↓ 一次移一格：交换相邻两项后全量写 order。 */
  const handleMove = async (index: number, dir: -1 | 1) => {
    const target = index + dir
    if (target < 0 || target >= accounts.length) return
    const ids = accounts.map(a => a.id)
    ;[ids[index], ids[target]] = [ids[target], ids[index]]
    setError(null)
    try {
      await bridge.setMeta(META_KEY_ACCOUNT_ORDER, serializeIdOrder(ids))
      await load()
    } catch (err) {
      console.error('Failed to persist account order:', err)
      setError('保存顺序失败，请重试')
    }
  }

  // ─── 长按手势（决策 #4）；本页面行有 click→编辑，长按触发后需吞掉同手势的 click ───
  const pressRef = useRef<{ timer: number; x: number; y: number } | null>(null)
  const suppressClickRef = useRef(false)
  const onRowPointerDown = (e: React.PointerEvent) => {
    if (reorderMode || showForm) return
    const startX = e.clientX
    const startY = e.clientY
    const timer = window.setTimeout(() => {
      suppressClickRef.current = true
      setReorderMode(true)
    }, 500)
    pressRef.current = { timer, x: startX, y: startY }
  }
  const onRowPointerMove = (e: React.PointerEvent) => {
    const p = pressRef.current
    if (!p) return
    if (Math.abs(e.clientX - p.x) > 10 || Math.abs(e.clientY - p.y) > 10) {
      window.clearTimeout(p.timer)
      pressRef.current = null
    }
  }
  const onRowPointerUp = () => {
    if (pressRef.current) {
      window.clearTimeout(pressRef.current.timer)
      pressRef.current = null
    }
  }
  const takeSuppressedClick = (): boolean => {
    if (suppressClickRef.current) {
      suppressClickRef.current = false
      return true
    }
    return false
  }
  useEffect(() => () => {
    if (pressRef.current) window.clearTimeout(pressRef.current.timer)
  }, [])

  return (
    <div className="min-h-screen">
      <PageHeader back />

      <div className="px-4 py-4 space-y-4 pb-20">
        {/* 排序/默认说明（决策 #4 文案） */}
        {!reorderMode
          ? <div className="text-xs text-center" style={{ color: 'var(--ink3)' }}>勾选的行为默认选择，长按可行调整顺序</div>
          : (
            <div className="flex items-center justify-between">
              <span className="text-xs" style={{ color: 'var(--ink3)' }}>排序模式：↑↓ 调整顺序</span>
              <button
                onClick={() => setReorderMode(false)}
                className="text-xs px-3 py-1.5 rounded-lg font-medium text-white"
                style={{ background: 'var(--accent)' }}
                data-testid="account-reorder-done"
              >
                完成
              </button>
            </div>
          )}

        {/* 账户列表 — 普通模式点击行进入编辑（REQ-5）；排序模式点不动（决策 #4） */}
        <div className="glass-card overflow-hidden">
          {accounts.length === 0 && !loading ? (
            <div className="px-4 py-8 text-center text-sm text-ink3">暂无账户，点击下方添加</div>
          ) : accounts.length > 0 ? (
            accounts.map((acc, i) => (
              <div
                key={acc.id}
                role="button"
                onClick={() => { if (!takeSuppressedClick()) openForm(acc) }}
                className={`w-full flex items-center gap-3 px-4 py-3 text-left ${i < accounts.length - 1 ? 'row-line' : ''}`}
                onPointerDown={onRowPointerDown}
                onPointerMove={onRowPointerMove}
                onPointerUp={onRowPointerUp}
                onPointerCancel={onRowPointerUp}
                data-testid={`account-row-${acc.id}`}
              >
                {/* T9-6（决策 #6）：照片 icon 走 CategoryIcon，防 data URI 裸文本乱码 */}
                <CategoryIcon icon={acc.icon} name={acc.name} fallback="💰" className="text-lg" />
                <span className="text-sm flex-1" style={{ color: 'var(--ink)' }}>
                  {acc.name}
                  {acc.accountType ? <span className="text-ink3"> ({acc.accountType})</span> : null}
                  {/* T9-5b：默认徽标 */}
                  {acc.isDefault && (
                    <span
                      className="text-[10px] px-1.5 py-0.5 rounded-full ml-2"
                      style={{ background: 'var(--glass)', color: 'var(--accent)' }}
                      data-testid={`account-default-badge-${acc.id}`}
                    >
                      默认
                    </span>
                  )}
                </span>
                {/* REQ-69（T18-6）：当前余额（可负）。0 也照显，用户能核对写入结果 */}
                <span
                  className="text-sm shrink-0 tabular-nums"
                  style={{ color: 'var(--ink2)' }}
                  data-testid={`account-balance-${acc.id}`}
                >
                  {formatSigned(acc.balanceCents ?? 0)}
                </span>
                {/* 排序模式：↑↓；普通模式：默认勾选（radio 语义） */}
                {reorderMode ? (
                  <div className="flex gap-1 shrink-0">
                    <button
                      type="button"
                      onClick={e => { e.stopPropagation(); void handleMove(i, -1) }}
                      disabled={i === 0}
                      className="w-8 h-8 rounded-lg text-sm disabled:opacity-30"
                      style={{ background: 'var(--glass)', color: 'var(--ink2)', border: '1px solid var(--glass-border)' }}
                      data-testid={`account-move-up-${acc.id}`}
                      aria-label={`上移 ${acc.name}`}
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      onClick={e => { e.stopPropagation(); void handleMove(i, 1) }}
                      disabled={i === accounts.length - 1}
                      className="w-8 h-8 rounded-lg text-sm disabled:opacity-30"
                      style={{ background: 'var(--glass)', color: 'var(--ink2)', border: '1px solid var(--glass-border)' }}
                      data-testid={`account-move-down-${acc.id}`}
                      aria-label={`下移 ${acc.name}`}
                    >
                      ↓
                    </button>
                  </div>
                ) : (
                  <label className="flex items-center shrink-0" onClick={e => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={!!acc.isDefault}
                      onChange={() => { void handleSetDefault(acc) }}
                      className="w-4 h-4"
                      data-testid={`account-default-${acc.id}`}
                      aria-label={`设为默认账户 ${acc.name}`}
                    />
                  </label>
                )}
              </div>
            ))
          ) : (
            <div className="px-4 py-3 text-xs text-ink3">加载中…</div>
          )}
        </div>

        {/* 添加按钮（排序模式下隐藏，防混写） */}
        {!showForm && !reorderMode && (
          <button
            onClick={() => openForm(null)}
            className="w-full py-3 rounded-lg text-sm font-medium transition-colors"
            style={{ background: 'var(--accent)', color: 'white' }}
          >
            ＋ 添加账户
          </button>
        )}

        {/* 新建 / 编辑表单 */}
        {showForm && (
          <div className="glass-card p-4 space-y-3" data-testid="account-form">
            <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>
              {editing ? '编辑账户' : '新建账户'}
            </div>
            <input
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="账户名称"
              className="w-full px-3 py-2 rounded-lg text-sm outline-none"
              style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
              autoFocus
            />
            {/* 账户分类 chip 宫格：再点一次取消选择（=不填） */}
            <div className="flex flex-wrap gap-2">
              {typeOptions.map(t => {
                const selected = accountType === t
                return (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setAccountType(selected ? '' : t)}
                    className="px-3 py-1.5 rounded-full text-xs transition-colors"
                    style={selected
                      ? { background: 'var(--accent)', color: 'white' }
                      : { background: 'var(--glass)', color: 'var(--ink2)', border: '1px solid var(--glass-border)' }}
                  >
                    {t}
                  </button>
                )
              })}
              <button
                type="button"
                onClick={() => setShowTypeInput(v => !v)}
                className="px-3 py-1.5 rounded-full text-xs"
                style={{ background: 'var(--glass)', color: 'var(--ink3)', border: '1px dashed var(--glass-border)' }}
              >
                ＋ 自定义
              </button>
            </div>
            {showTypeInput && (
              <input
                type="text"
                value={typeInput}
                onChange={e => setTypeInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') commitCustomType() }}
                onBlur={commitCustomType}
                placeholder="自定义分类，回车添加"
                data-testid="custom-type-input"
                className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
              />
            )}
            {/* REQ-69（T18-6）：当前余额（元）。手动设置后随账户上行同步（T18-5 两端化） */}
            <input
              type="text"
              inputMode="decimal"
              value={balanceYuan}
              onChange={e => setBalanceYuan(e.target.value)}
              placeholder="当前余额（元）；欠款填负数，留空为 0"
              className="w-full px-3 py-2 rounded-lg text-sm outline-none"
              style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
              data-testid="account-balance-input"
            />
            {/* T9-6（REQ-13）：图标区换共享 IconPicker（emoji 宫格 + 照片 + ≤4 字符自定义文本） */}
            <IconPicker icon={icon} onChange={setIcon} customText name={name} fallback="💰" />
            {error && (
              <div className="text-xs" style={{ color: 'var(--expense)' }}>{error}</div>
            )}
            <div className="flex gap-2">
              <button
                onClick={closeForm}
                className="flex-1 py-2 rounded-lg text-sm"
                style={{ background: 'var(--glass)', color: 'var(--ink2)' }}
              >
                取消
              </button>
              <button
                onClick={handleSubmit}
                disabled={!name.trim()}
                className="flex-1 py-2 rounded-lg text-sm font-medium text-white transition-opacity disabled:opacity-40"
                style={{ background: 'var(--accent)' }}
              >
                {editing ? '保存' : '添加'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
