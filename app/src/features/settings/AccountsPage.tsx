/**
 * AccountsPage — 账户管理页。
 *
 * 列表展示 + 新增账户 + 编辑已有账户（T7-5 / REQ-5：名称 + 分类 + 图标）。
 */

import { useState, useEffect, useCallback, useMemo } from 'react'
import { PageHeader } from '../../components/Layout'
import { useBridge } from '../../platform/hooks/use-bridge'
import { newUuid } from '../../platform/uuid'
import type { AccountData } from '../../platform/bridge/bridge-types'

/** REQ-5：预置账户分类；用户可经"+ 自定义"追加，取消选择即不填（accountType null）。 */
const DEFAULT_ACCOUNT_TYPES = ['储蓄卡', '信用卡', '花呗', '微信', '支付宝', '现金', '余额宝', '零钱通']
const ACCOUNT_ICONS = ['🏦', '💳', '📱', '🪙', '💵', '🧧', '🥡', '💰']

export function AccountsPage() {
  const bridge = useBridge()
  const [accounts, setAccounts] = useState<AccountData[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // T7-5：新建/编辑共用一个表单 — editing 非空即编辑态
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<AccountData | null>(null)
  const [name, setName] = useState('')
  const [icon, setIcon] = useState('💰')
  const [customIcon, setCustomIcon] = useState('')
  const [accountType, setAccountType] = useState('')
  const [addedTypes, setAddedTypes] = useState<string[]>([])
  const [showTypeInput, setShowTypeInput] = useState(false)
  const [typeInput, setTypeInput] = useState('')

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
    setError(null)
    setEditing(acc)
    setName(acc?.name ?? '')
    if (acc && acc.icon && !ACCOUNT_ICONS.includes(acc.icon)) {
      // 非预置图标（自定义数据）回填到自定义文本框
      setIcon('')
      setCustomIcon(acc.icon)
    } else {
      setIcon(acc?.icon || '💰')
      setCustomIcon('')
    }
    setAccountType(acc?.accountType ?? '')
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
    const finalIcon = customIcon.trim() || icon
    try {
      // 必须走 NativeBridge 封装：native 侧期望 {account, mutationId} 包裹结构，且 ownerId 必填
      if (editing) {
        // 决策 #4：account.update 局部合并，version 必须递增
        await bridge.updateAccount(
          { id: editing.id, ownerId: 'local', name: trimmed, icon: finalIcon, accountType: accountType || null, version: editing.version + 1 },
          newUuid()
        )
      } else {
        // 服务端 sync/ai 契约要求 UUID（第六轮 Bug-5.2）
        await bridge.insertAccount(
          { id: newUuid(), ownerId: 'local', name: trimmed, icon: finalIcon, accountType: accountType || null, version: 1 },
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

  return (
    <div className="min-h-screen">
      <PageHeader back />

      <div className="px-4 py-4 space-y-4 pb-20">
        {/* 账户列表 — 点击行进入编辑（REQ-5） */}
        <div className="glass-card overflow-hidden">
          {accounts.length === 0 && !loading ? (
            <div className="px-4 py-8 text-center text-sm text-ink3">暂无账户，点击下方添加</div>
          ) : accounts.length > 0 ? (
            accounts.map((acc, i) => (
              <button
                key={acc.id}
                type="button"
                onClick={() => openForm(acc)}
                className={`w-full flex items-center gap-3 px-4 py-3 text-left ${i < accounts.length - 1 ? 'row-line' : ''}`}
              >
                <span className="text-lg">{acc.icon || '💰'}</span>
                <span className="text-sm flex-1" style={{ color: 'var(--ink)' }}>
                  {acc.name}
                  {acc.accountType ? <span className="text-ink3"> ({acc.accountType})</span> : null}
                </span>
              </button>
            ))
          ) : (
            <div className="px-4 py-3 text-xs text-ink3">加载中…</div>
          )}
        </div>

        {/* 添加按钮 */}
        {!showForm && (
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
            {/* 账户图标：预设 + 自定义文本框（≤4 字符，非空时优先生效） */}
            <div className="flex flex-wrap gap-2">
              {ACCOUNT_ICONS.map(ic => {
                const selected = !customIcon && icon === ic
                return (
                  <button
                    key={ic}
                    type="button"
                    onClick={() => { setCustomIcon(''); setIcon(ic) }}
                    className="w-10 h-10 rounded-lg flex items-center justify-center text-lg transition-colors"
                    style={{
                      background: selected ? 'var(--accent)' : 'var(--glass)',
                      border: selected ? '2px solid var(--accent)' : '1px solid var(--glass-border)',
                    }}
                  >
                    {ic}
                  </button>
                )
              })}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs shrink-0" style={{ color: 'var(--ink3)' }}>自定义图标</span>
              <input
                type="text"
                value={customIcon}
                onChange={e => setCustomIcon(e.target.value.slice(0, 4))}
                maxLength={4}
                placeholder="≤4 字符"
                data-testid="custom-icon-input"
                className="w-20 px-3 py-1.5 rounded-lg text-sm outline-none"
                style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
              />
            </div>
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
