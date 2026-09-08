/**
 * SavingsPage — 存钱管理（第八轮 REQ-7）。
 *
 * 轻量储蓄目标：新建目标（名称/目标金额/图标）+ 记录存入 + 进度条 + 归档删除。
 * **仅本地存储、不上行同步**（服务端 sync 契约不认该实体，见 Repository 说明）。
 */

import { useState, useEffect, useCallback } from 'react'
import { PageHeader } from '../../components/Layout'
import { useBridge } from '../../platform/hooks/use-bridge'
import { newUuid } from '../../platform/uuid'
import * as Money from '../../domain/money'
import type { SavingsGoalData } from '../../platform/bridge/bridge-types'

const GOAL_ICONS = ['🐷', '✈️', '🏠', '🚗', '📱', '🎓', '💍', '🎯']

function progressPct(g: SavingsGoalData): number {
  if (g.targetCents <= 0) return 0
  return Math.min(100, Math.round((g.currentCents / g.targetCents) * 100))
}

export function SavingsPage() {
  const bridge = useBridge()
  const [goals, setGoals] = useState<SavingsGoalData[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [showAdd, setShowAdd] = useState(false)
  const [newName, setNewName] = useState('')
  const [newTarget, setNewTarget] = useState('')
  const [newIcon, setNewIcon] = useState('🐷')

  // 存入交互：记录正在存入的目标 id 与金额输入
  const [depositId, setDepositId] = useState<string | null>(null)
  const [depositAmount, setDepositAmount] = useState('')

  const load = useCallback(async () => {
    try {
      setGoals(await bridge.listSavingsGoals())
    } catch (err) {
      console.error('Failed to load savings goals:', err)
      setError('加载失败，请重试')
    } finally {
      setLoading(false)
    }
  }, [bridge])

  useEffect(() => { load() }, [load])

  const handleAdd = async () => {
    if (!newName.trim() || !newTarget) return
    setError(null)
    const targetCents = Money.fromString(newTarget) as number
    if (!Number.isFinite(targetCents) || targetCents < 1) {
      setError('目标金额需大于 0')
      return
    }
    try {
      await bridge.insertSavingsGoal({
        id: newUuid(), ownerId: 'local', name: newName.trim(),
        targetCents, currentCents: 0, icon: newIcon, version: 1,
      })
      setNewName(''); setNewTarget(''); setNewIcon('🐷'); setShowAdd(false)
      await load()
    } catch (err) {
      console.error('Failed to add savings goal:', err)
      setError('创建失败，请重试')
    }
  }

  const handleDepositConfirm = async (goal: SavingsGoalData) => {
    const add = Money.fromString(depositAmount) as number
    if (!Number.isFinite(add) || add < 1) { setError('存入金额需大于 0'); return }
    setError(null)
    try {
      await bridge.updateSavingsGoal({
        id: goal.id, name: goal.name, icon: goal.icon,
        targetCents: goal.targetCents,
        currentCents: goal.currentCents + add,
        version: goal.version + 1,
      })
      setDepositId(null); setDepositAmount('')
      await load()
    } catch (err) {
      console.error('Failed to deposit:', err)
      setError('存入失败，请重试')
    }
  }

  const handleArchive = async (goal: SavingsGoalData) => {
    setError(null)
    try {
      await bridge.archiveSavingsGoal(goal.id)
      await load()
    } catch (err) {
      console.error('Failed to archive:', err)
      setError('删除失败，请重试')
    }
  }

  return (
    <div className="min-h-screen">
      <PageHeader back />

      <div className="px-4 py-4 space-y-4 pb-20">
        <div className="text-xs text-ink3 text-center">存钱数据仅保存在本机，不随账号同步</div>

        {error && (
          <div className="text-xs px-3 py-2 rounded-lg" style={{ background: 'rgba(220,38,38,0.1)', color: '#dc2626' }}>
            {error}
          </div>
        )}

        {/* 目标列表 */}
        {loading ? (
          <div className="glass-card p-4 text-xs text-ink3">加载中…</div>
        ) : goals.length === 0 ? (
          <div className="glass-card p-6 text-center text-sm text-ink3">还没有存钱目标，点击下方新建</div>
        ) : (
          goals.map(g => {
            const pct = progressPct(g)
            return (
              <div key={g.id} className="glass-card p-4 space-y-2" data-testid={`savings-card-${g.id}`}>
                <div className="flex items-center gap-2">
                  <span className="text-xl">{g.icon || '🐷'}</span>
                  <span className="text-sm font-medium flex-1 truncate" style={{ color: 'var(--ink)' }}>{g.name}</span>
                  <span className="text-xs money-num" style={{ color: 'var(--ink2)' }}>
                    {Money.formatDisplay(Money.cents(g.currentCents))} / {Money.formatDisplay(Money.cents(g.targetCents))}
                  </span>
                </div>
                <div className="h-2 rounded-full overflow-hidden" style={{ background: 'var(--glass-border)' }}>
                  <div className="h-full rounded-full" style={{ width: `${pct}%`, background: 'var(--income)' }} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-xs" style={{ color: 'var(--ink3)' }}>{pct}% 完成</span>
                  <div className="flex gap-2">
                    <button
                      onClick={() => { setDepositId(depositId === g.id ? null : g.id); setDepositAmount(''); setError(null) }}
                      className="text-xs px-3 py-1.5 rounded-lg font-medium text-white"
                      style={{ background: 'var(--accent)' }}
                      data-testid={`savings-deposit-${g.id}`}
                    >
                      存入
                    </button>
                    <button
                      onClick={() => handleArchive(g)}
                      className="text-xs px-3 py-1.5 rounded-lg"
                      style={{ color: 'var(--ink3)' }}
                      data-testid={`savings-archive-${g.id}`}
                    >
                      删除
                    </button>
                  </div>
                </div>
                {depositId === g.id && (
                  <div className="flex gap-2 pt-1">
                    <input
                      type="text"
                      inputMode="decimal"
                      value={depositAmount}
                      onChange={e => setDepositAmount(e.target.value)}
                      placeholder="存入金额"
                      className="flex-1 px-3 py-2 rounded-lg text-sm outline-none"
                      style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
                      data-testid="savings-deposit-input"
                    />
                    <button
                      onClick={() => handleDepositConfirm(g)}
                      disabled={!depositAmount}
                      className="text-xs px-3 py-2 rounded-lg font-medium text-white disabled:opacity-40"
                      style={{ background: 'var(--accent)' }}
                      data-testid="savings-deposit-confirm"
                    >
                      确认
                    </button>
                  </div>
                )}
              </div>
            )
          })
        )}

        {/* 新建表单 */}
        {!showAdd ? (
          <button
            onClick={() => setShowAdd(true)}
            className="w-full py-3 rounded-lg text-sm font-medium"
            style={{ background: 'var(--accent)', color: 'white' }}
          >
            ＋ 新建目标
          </button>
        ) : (
          <div className="glass-card p-4 space-y-3">
            <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>新建存钱目标</div>
            <input
              type="text"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              placeholder="目标名称（如：旅行基金）"
              className="w-full px-3 py-2 rounded-lg text-sm outline-none"
              style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
              data-testid="savings-new-name"
            />
            <input
              type="text"
              inputMode="decimal"
              value={newTarget}
              onChange={e => setNewTarget(e.target.value)}
              placeholder="目标金额"
              className="w-full px-3 py-2 rounded-lg text-sm outline-none"
              style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
              data-testid="savings-new-target"
            />
            <div className="flex gap-2 flex-wrap">
              {GOAL_ICONS.map(icon => (
                <button
                  key={icon}
                  onClick={() => setNewIcon(icon)}
                  className="w-10 h-10 rounded-lg flex items-center justify-center text-lg"
                  style={{
                    background: newIcon === icon ? 'var(--accent)' : 'var(--glass)',
                    border: newIcon === icon ? '2px solid var(--accent)' : '1px solid var(--glass-border)',
                  }}
                >
                  {icon}
                </button>
              ))}
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => { setShowAdd(false); setNewName(''); setNewTarget(''); setError(null) }}
                className="flex-1 py-2 rounded-lg text-sm"
                style={{ background: 'var(--glass)', color: 'var(--ink2)' }}
              >
                取消
              </button>
              <button
                onClick={handleAdd}
                disabled={!newName.trim() || !newTarget}
                className="flex-1 py-2 rounded-lg text-sm font-medium text-white disabled:opacity-40"
                style={{ background: 'var(--accent)' }}
                data-testid="savings-add-confirm"
              >
                创建
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
