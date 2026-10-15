/**
 * HomePage — 首页。
 *
 * 展示：日期问候、今日支出、本月预算进度、近期流水、快捷记账入口。
 * UI 根据四时主题（晨/昼/暮/夜）呈现不同氛围装饰。
 *
 * 第十四轮 REQ-38（T14-1）：今日流水行换共享组件 TxListRow，与明细行同构
 * （title=备注优先、副行「时间 · 分类」、金额 17px bold）。
 *
 * 第十五轮 REQ-50（T15-1）：问候行拼昵称（meta display_name 本地镜像）；
 * 预算匹配口径扩为当月或当年（对齐 BudgetPage）；预算行下新增存钱目标
 * 内联行（≤2 行，点行跳存钱管理）；删空态引导句。
 */

import { useState, useEffect, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { SyncBadge } from '../../components/SyncBadge'
import { TxListRow } from '../../components/TxListRow'
import { useBridge } from '../../platform/hooks/use-bridge'
import { useRefreshOnSync } from '../../platform/hooks/use-refresh-on-sync'
import { useTheme } from '../../design/theme'
import { QuickEntrySheet } from './QuickEntrySheet'
import { TxEditSheet } from './TxEditSheet'
import type { TransactionData, CategoryData, SavingsGoalData, AutoBookStatusData } from '../../platform/bridge/bridge-types'
import * as Money from '../../domain/money'
import { todayBizDate, localHHmm } from '../../domain/date'
import { sumTransactions } from '../../domain/aggregator'
import { toDomainTransaction } from '../../domain/types'
import { pickEntryBook } from '../../domain/books'

function formatCents(cents: number): string {
  return Money.formatDisplay(Money.cents(cents))
}

/** 根据当前小时返回问候语 */
function getGreeting(): string {
  const h = new Date().getHours()
  if (h >= 5 && h < 12) return '早上好'
  if (h >= 12 && h < 14) return '中午好'
  if (h >= 14 && h < 18) return '下午好'
  return '晚上好'
}

/** 格式化日期：9月7日 · 星期一 */
function formatBizDate(): string {
  const now = new Date()
  const weekdays = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六']
  return `${now.getMonth() + 1}月${now.getDate()}日 · ${weekdays[now.getDay()]}`
}

/** 昼间/夜间空状态装饰 */
function DayEmptyDecor() {
  return (
    <div className="relative w-full h-28 overflow-hidden" aria-hidden="true">
      {/* 漂浮云 */}
      <div className="absolute bottom-6 left-[10%] w-20 h-7 rounded-full opacity-50"
        style={{ background: 'rgba(255,255,255,0.7)', animation: 'homeFloat 18s ease-in-out infinite' }} />
      <div className="absolute bottom-10 right-[15%] w-14 h-5 rounded-full opacity-40"
        style={{ background: 'rgba(255,255,255,0.6)', animation: 'homeFloat 22s ease-in-out infinite reverse' }} />
      {/* 波浪线 */}
      <svg className="absolute bottom-0 left-0 w-full h-8 opacity-20" viewBox="0 0 200 20" preserveAspectRatio="none">
        <path d="M0,10 Q25,0 50,10 T100,10 T150,10 T200,10" fill="none" stroke="var(--ink3)" strokeWidth="1.5" />
        <path d="M0,14 Q25,6 50,14 T100,14 T150,14 T200,14" fill="none" stroke="var(--ink3)" strokeWidth="1" />
      </svg>
    </div>
  )
}

function NightEmptyDecor() {
  const stars = Array.from({ length: 12 }, (_, i) => ({
    left: `${(i * 31 + 7) % 100}%`,
    top: `${(i * 19 + 3) % 80}%`,
    size: i % 4 === 0 ? 3 : 2,
    delay: i * 0.4,
  }))
  return (
    <div className="relative w-full h-28 overflow-hidden" aria-hidden="true">
      {stars.map((s, i) => (
        <div key={i} className="absolute rounded-full"
          style={{
            left: s.left, top: s.top,
            width: s.size, height: s.size,
            background: 'rgba(255,255,255,0.7)',
            animation: `homeTwinkle ${2 + s.delay}s ease-in-out infinite`,
            animationDelay: `${s.delay}s`,
          }} />
      ))}
      {/* 小月亮 */}
      <div className="absolute top-2 right-8 w-8 h-8 rounded-full opacity-30"
        style={{ background: 'rgba(255,255,230,0.5)', boxShadow: '0 0 12px rgba(255,255,230,0.2)' }} />
    </div>
  )
}

export function HomePage() {
  const [showSheet, setShowSheet] = useState(false)
  const [todayTxs, setTodayTxs] = useState<TransactionData[]>([])
  const [categories, setCategories] = useState<CategoryData[]>([])
  // T15-1（REQ-50）：matched = 当月 monthly 或当年 annual 预算，展示数据随口径一并算好
  const [budget, setBudget] = useState<{
    amountCents: number
    isAnnual: boolean
    totalCents: number
    inBudgetCents: number
  } | null>(null)
  const [savingsGoals, setSavingsGoals] = useState<SavingsGoalData[]>([])
  const [nick, setNick] = useState('')
  const [loadError, setLoadError] = useState<string | null>(null)
  // T21-8（REQ-83）：待确认箱入口——`pendingConfirmCount > 0` 时才出现
  const [pendingConfirmCount, setPendingConfirmCount] = useState(0)
  // T12-10（REQ-36）：今日流水行点击开编辑弹层
  const [editTx, setEditTx] = useState<TransactionData | null>(null)
  const bridge = useBridge()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const { theme } = useTheme()

  const isNight = theme === 'night' || theme === 'dusk'

  // R-M4-28 修复：深链 entry=1 时自动打开快速记账面板
  useEffect(() => {
    if (searchParams.get('entry') === '1') {
      setShowSheet(true)
      searchParams.delete('entry')
      setSearchParams(searchParams, { replace: true })
    }
  }, [searchParams, setSearchParams])

  const loadData = useCallback(async () => {
    setLoadError(null)
    try {
      // 决策 #2 / T7-6：展示目标 = 非 master 账本；T9-5b（决策 #3）default_book_id 优先
      // T15-1（REQ-50）：顺路多取 display_name（SecurityPage 镜像写，离线可读）
      const [books, meta] = await Promise.all([
        bridge.call('book.list', {}) as Promise<{ id: string; isMaster?: boolean }[]>,
        bridge.getMeta(['default_book_id', 'display_name']).catch(() => ({}) as Record<string, string | null>),
      ])
      setNick(meta['display_name'] || '')
      const bookId = pickEntryBook(books, meta['default_book_id'] ?? null)?.id
      if (!bookId) {
        setLoadError('账本未就绪')
        return
      }

      const today = todayBizDate('Asia/Shanghai')
      const month = today.slice(0, 7)
      const year = today.slice(0, 4)

      const [allTxs, cats, budgets, goals, autobookStatus] = await Promise.all([
        bridge.call('transaction.listByBook', { bookId }) as Promise<TransactionData[]>,
        bridge.call('category.list', {}) as Promise<CategoryData[]>,
        bridge.call('budget.list', { bookId }) as Promise<{ amountCents: number; period: string; budgetType: 'monthly' | 'annual' }[]>,
        // 存钱目标加载失败不挡首页其余渲染（与 AssistantPage 同款回落）
        bridge.listSavingsGoals().catch(() => [] as SavingsGoalData[]),
        // T21-8（REQ-83）：待确认箱角标。失败/不可用时归零，不挡主流程
        bridge.call<AutoBookStatusData>('autobook.get_status').catch(() => ({ pendingConfirmCount: 0 } as AutoBookStatusData)),
      ])

      const todayExpenses = allTxs.filter(tx => tx.bizDate === today && tx.type === 'expense' && tx.deletedAt === null)
      const monthExpenses = allTxs.filter(tx => tx.bizDate.startsWith(month) && tx.type === 'expense' && tx.deletedAt === null)

      setTodayTxs(todayExpenses)
      setCategories(cats)
      setSavingsGoals(goals)
      setPendingConfirmCount(autobookStatus.pendingConfirmCount ?? 0)

      // T15-1（REQ-50）：匹配口径对齐 BudgetPage——当月(monthly)优先，否则当年(annual)，一条命中即显
      const matched =
        budgets.find(b => b.budgetType === 'monthly' && b.period === month) ??
        budgets.find(b => b.budgetType === 'annual' && b.period === year)
      if (matched) {
        const isAnnual = matched.budgetType === 'annual'
        const scoped = isAnnual
          ? allTxs.filter(tx => tx.bizDate.startsWith(year) && tx.type === 'expense' && tx.deletedAt === null)
          : monthExpenses
        // 第十轮 REQ-17（决策 #1）：已用% 只统计"计入预算"交易（!== false 口径，
        // 缺省=计入，兼容旧数据）；期间支出总额不过滤。
        setBudget({
          amountCents: matched.amountCents,
          isAnnual,
          totalCents: sumTransactions(scoped.map(toDomainTransaction)).totalExpense as number,
          inBudgetCents: sumTransactions(
            scoped.filter(tx => tx.includeInBudget !== false).map(toDomainTransaction),
          ).totalExpense as number,
        })
      } else {
        setBudget(null)
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : '加载失败'
      setLoadError(msg)
    }
  }, [bridge])

  useEffect(() => {
    loadData()
  }, [loadData])

  // T18-2（REQ-67①）：同步完成 / 页面重新可见 → 重取（消除「只有切页重挂才看见新值」）
  useRefreshOnSync(loadData)

  const todayExpense = sumTransactions(todayTxs.map(toDomainTransaction))

  const budgetProgress = budget
    ? Math.min(100, Math.round(budget.inBudgetCents / budget.amountCents * 100))
    : 0

  return (
    <div className="min-h-screen">
      {/* ─── 头部：日期 + 问候 + 同步 ─── */}
      <div className="px-5 pt-5 pb-2 flex items-start justify-between">
        <div>
          <div className="text-sm opacity-60 serif-date" style={{ color: 'var(--ink2)' }}>
            {formatBizDate()}
          </div>
          <div className="text-2xl font-bold mt-1" style={{ color: 'var(--ink)' }}>
            {getGreeting()}{nick ? `，${nick}` : ''}
          </div>
        </div>
        <SyncBadge />
      </div>

      {/* ─── T21-8（REQ-83）：待确认箱入口（仅当有待确认条目时出现） ─── */}
      {pendingConfirmCount > 0 && (
        <button
          onClick={() => navigate('/autobook/captures')}
          className="mx-5 mb-3 px-3 py-2.5 rounded-xl flex items-center gap-2.5 w-[calc(100%-2.5rem)] text-left active:opacity-70 transition-opacity"
          style={{
            background: 'linear-gradient(135deg, rgba(59,130,246,0.1), rgba(16,185,129,0.08))',
            border: '1px solid rgba(59,130,246,0.2)',
          }}
          data-testid="home-autobook-entry"
        >
          <span className="text-xl">🔔</span>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium truncate" style={{ color: 'var(--ink)' }}>
              小贝发现了 {pendingConfirmCount} 笔待确认
            </div>
            <div className="text-[11px] truncate" style={{ color: 'var(--ink3)' }}>
              点进去确认或修改，才会计入账本
            </div>
          </div>
          <span className="text-xs" style={{ color: 'var(--accent)' }}>查看 ›</span>
        </button>
      )}

      {/* ─── 今日支出 ── */}
      <div className="px-5 pt-2 pb-4">
        <div className="text-xs mb-1" style={{ color: 'var(--ink2)' }}>今日支出</div>
        <div className="flex items-baseline gap-1">
          <span className="text-lg font-medium" style={{ color: 'var(--ink)' }}>¥</span>
          <span className="money-num text-5xl font-bold" style={{ color: 'var(--ink)' }}>
            {formatCents(todayExpense.totalExpense as number)}
          </span>
        </div>
      </div>

      {/* ─── 预算（内联行；当月/当年口径见 loadData T15-1） ─── */}
      {budget && (
        <div className="px-5 pb-4">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-xs" style={{ color: 'var(--ink2)' }}>
              {budget.isAnnual ? '本年' : '本月'} {formatCents(budget.totalCents)} · 已用 {budgetProgress}%
            </span>
            <span className="text-xs font-medium" style={{ color: 'var(--ink2)' }}>
              {budget.isAnnual ? '年度预算' : '预算'} {formatCents(budget.amountCents)}
            </span>
          </div>
          <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--glass-border)' }}>
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{
                width: `${budgetProgress}%`,
                background: budgetProgress > 80 ? 'var(--expense)' : 'var(--accent)',
              }}
            />
          </div>
        </div>
      )}

      {/* ─── 存钱目标（T15-1/REQ-50：有目标才渲染，≤2 行，点行跳存钱管理） ─── */}
      {savingsGoals.length > 0 && (
        <div className="px-5 pb-4 space-y-2">
          {savingsGoals.slice(0, 2).map(g => {
            const pct = g.targetCents > 0 ? Math.min(100, Math.round((g.currentCents / g.targetCents) * 100)) : 0
            return (
              <button
                key={g.id}
                type="button"
                onClick={() => navigate('/settings/savings')}
                className="block w-full text-left"
                data-testid="home-savings-row"
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs" style={{ color: 'var(--ink2)' }}>
                    {g.icon ?? '🐷'} · {g.name}
                  </span>
                  <span className="text-xs font-medium" style={{ color: 'var(--ink2)' }}>
                    {formatCents(g.currentCents)}/{formatCents(g.targetCents)}
                  </span>
                </div>
                <div className="h-1 rounded-full overflow-hidden" style={{ background: 'var(--glass-border)' }}>
                  <div
                    className="h-full rounded-full"
                    style={{ width: `${pct}%`, background: 'var(--accent)' }}
                  />
                </div>
              </button>
            )
          })}
        </div>
      )}

      {/* ── 分隔线 ─── */}
      <div className="mx-5 h-px" style={{ background: 'var(--glass-border)' }} />

      {/* ─── 今日流水 ── */}
      <div className="px-5 pt-4 pb-2">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold" style={{ color: 'var(--ink)' }}>今天</h2>
          {todayTxs.length > 0 && (
            <button onClick={() => navigate('/detail')} className="text-xs" style={{ color: 'var(--accent)' }}>
              查看全部
            </button>
          )}
        </div>

        {loadError ? (
          <div className="text-center py-8">
            <div className="text-3xl mb-2">⚠️</div>
            <p className="text-sm mb-3" style={{ color: 'var(--ink2)' }}>{loadError}</p>
            <button
              onClick={loadData}
              className="text-xs px-4 py-2 rounded-lg font-medium text-white"
              style={{ background: 'var(--accent)' }}
            >
              重试
            </button>
          </div>
        ) : todayTxs.length === 0 ? (
          <div className="py-6">
            {/* 氛围装饰 */}
            {isNight ? <NightEmptyDecor /> : <DayEmptyDecor />}
            <div className="text-center -mt-2">
              <p className="text-base font-medium" style={{ color: 'var(--ink2)' }}>
                今天还没有记录
              </p>
            </div>
          </div>
        ) : (
          <div className="divide-y" style={{ borderColor: 'var(--glass-border)' }}>
            {todayTxs.slice(0, 10).map(tx => {
              const cat = categories.find(c => c.id === tx.categoryId)
              const note = tx.note?.trim()
              return (
                <TxListRow
                  key={tx.id}
                  icon={cat?.icon}
                  iconName={cat?.name ?? ''}
                  title={note || cat?.name || '未分类'}
                  sub={`${localHHmm(tx.occurredAt)} · ${cat?.name || '未分类'}`}
                  amountCents={tx.amountCents}
                  isExpense={tx.type === 'expense'}
                  aiBadge={tx.source === 'nlu_ai'}
                  borderBottom
                  onClick={() => setEditTx(tx)}
                />
              )
            })}
          </div>
        )}
      </div>

      {/* ─── 快捷记账（右下） ─── */}
      <button
        onClick={() => setShowSheet(true)}
        className="fixed bottom-24 right-4 z-20 w-12 h-12 rounded-full shadow-lg flex items-center justify-center text-xl text-white transition-transform active:scale-95"
        style={{ background: 'var(--accent)' }}
        data-testid="quick-entry-btn"
        aria-label="快速记账"
      >
        ＋
      </button>

      {/* ─── 快速记账 Sheet ─── */}
      {showSheet && (
        <QuickEntrySheet
          categories={categories}
          onClose={() => setShowSheet(false)}
          onSuccess={() => {
            setShowSheet(false)
            loadData()
          }}
        />
      )}

      {/* T12-10（REQ-36）：今日流水行点击编辑，保存/删除后刷新 */}
      {editTx && (
        <TxEditSheet
          tx={editTx}
          categories={categories}
          onClose={() => setEditTx(null)}
          onSaved={loadData}
        />
      )}

      {/* 空状态装饰动画 */}
      <style>{`
        @keyframes homeFloat {
          0%, 100% { transform: translateX(0); }
          50% { transform: translateX(12px); }
        }
        @keyframes homeTwinkle {
          0%, 100% { opacity: 0.3; }
          50% { opacity: 1; }
        }
      `}</style>
    </div>
  )
}
