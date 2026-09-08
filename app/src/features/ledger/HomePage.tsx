/**
 * HomePage — 首页。
 *
 * 展示：日期问候、今日支出、本月预算进度、近期流水、快捷记账入口。
 * UI 根据四时主题（晨/昼/暮/夜）呈现不同氛围装饰。
 */

import { useState, useEffect, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { SyncBadge } from '../../components/SyncBadge'
import { getCategoryEmoji } from '../../components/category-emoji'
import { useBridge } from '../../platform/hooks/use-bridge'
import { useTheme } from '../../design/theme'
import { QuickEntrySheet } from './QuickEntrySheet'
import type { TransactionData, CategoryData } from '../../platform/bridge/bridge-types'
import * as Money from '../../domain/money'
import { todayBizDate } from '../../domain/date'
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
  const [monthTxs, setMonthTxs] = useState<TransactionData[]>([])
  const [categories, setCategories] = useState<CategoryData[]>([])
  const [budget, setBudget] = useState<{ amountCents: number } | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
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
      // 决策 #2 / T7-6：展示目标 = 第一个非 master 账本（总账本自身不存流水）
      const books = await bridge.call('book.list', {}) as { id: string; isMaster?: boolean }[]
      const bookId = pickEntryBook(books)?.id
      if (!bookId) {
        setLoadError('账本未就绪')
        return
      }

      const today = todayBizDate('Asia/Shanghai')
      const month = today.slice(0, 7)

      const [allTxs, cats, budgets] = await Promise.all([
        bridge.call('transaction.listByBook', { bookId }) as Promise<TransactionData[]>,
        bridge.call('category.list', {}) as Promise<CategoryData[]>,
        bridge.call('budget.list', { bookId }) as Promise<{ amountCents: number; period: string }[]>,
      ])

      const todayExpenses = allTxs.filter(tx => tx.bizDate === today && tx.type === 'expense' && tx.deletedAt === null)
      const monthExpenses = allTxs.filter(tx => tx.bizDate.startsWith(month) && tx.type === 'expense' && tx.deletedAt === null)

      setTodayTxs(todayExpenses)
      setMonthTxs(monthExpenses)
      setCategories(cats)

      const monthBudget = budgets.find(b => b.period === month)
      setBudget(monthBudget ? { amountCents: monthBudget.amountCents } : null)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '加载失败'
      setLoadError(msg)
    }
  }, [bridge])

  useEffect(() => {
    loadData()
  }, [loadData])

  const todayExpense = sumTransactions(todayTxs.map(toDomainTransaction))
  const monthExpense = sumTransactions(monthTxs.map(toDomainTransaction))

  const budgetProgress = budget
    ? Math.min(100, Math.round((monthExpense.totalExpense as number) / budget.amountCents * 100))
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
            {getGreeting()}
          </div>
        </div>
        <SyncBadge />
      </div>

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

      {/* ─── 本月预算（内联行） ─── */}
      {budget && (
        <div className="px-5 pb-4">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-xs" style={{ color: 'var(--ink2)' }}>
              本月 {formatCents(monthExpense.totalExpense as number)} · 已用 {budgetProgress}%
            </span>
            <span className="text-xs font-medium" style={{ color: 'var(--ink2)' }}>
              预算 {formatCents(budget.amountCents)}
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
              <p className="text-xs mt-1" style={{ color: 'var(--ink3)' }}>
                点下面的 ＋ 记第一笔，或者问问小贝
              </p>
            </div>
          </div>
        ) : (
          <div className="divide-y" style={{ borderColor: 'var(--glass-border)' }}>
            {todayTxs.slice(0, 10).map(tx => {
              const cat = categories.find(c => c.id === tx.categoryId)
              return (
                <div key={tx.id} className="flex items-center justify-between py-3"
                  style={{ borderBottom: '1px solid var(--glass-border)' }}>
                  <div className="flex items-center gap-3">
                    <span className="text-xl">{cat ? getCategoryEmoji(cat.name) : '💰'}</span>
                    <div>
                      <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>
                        {cat?.name || '未分类'}
                        {tx.source === 'nlu_ai' && (
                          <span className="ml-1.5 text-[10px] px-1.5 py-0.5 rounded-full"
                            style={{ background: 'var(--glass)', color: 'var(--accent)', border: '1px solid var(--glass-border)' }}>
                            AI
                          </span>
                        )}
                      </div>
                      {tx.note && <div className="text-xs mt-0.5" style={{ color: 'var(--ink3)' }}>{tx.note}</div>}
                    </div>
                  </div>
                  <span className={`money-num text-sm font-medium ${tx.type === 'expense' ? 'text-expense' : 'text-income'}`}>
                    {tx.type === 'expense' ? '-' : '+'}{formatCents(tx.amountCents)}
                  </span>
                </div>
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
