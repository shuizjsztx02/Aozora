/**
 * ScheduledPage — 定时记账（第十一轮 T11-5 / REQ-26 / 决策 #4）。
 *
 * 管理周期性自动记账规则：列表卡（周期文案 + 上次/下次执行 + 开关 + 删除）
 * + 新建/编辑表单（字段序参考截图 #17）。规则仅存本机不上行同步（同存钱
 * 目标，见 Repository 说明）；引擎按规则生成的交易走正常 mutation 上行。
 * 表单写入 = scheduled.insert/update（无 mutationId，update 局部合并、
 * version+1）；enabled 快捷开关只传 {id, enabled, version+1}。
 */

import { useState, useEffect, useCallback, useMemo } from 'react'
import { PageHeader } from '../../components/Layout'
import { useBridge } from '../../platform/hooks/use-bridge'
import { newUuid } from '../../platform/uuid'
import * as Money from '../../domain/money'
import { todayBizDate } from '../../domain/date'
import { pickEntryBook, pickDefaultAccount } from '../../domain/books'
import { CategoryIcon } from '../../components/CategoryIcon'
import DateField from '../../components/DateField'
import type {
  ScheduledRuleData,
  BookData,
  CategoryData,
  AccountData,
} from '../../platform/bridge/bridge-types'

const TZ = 'Asia/Shanghai'
type Repeat = 'daily' | 'weekly' | 'monthly' | 'yearly'
const REPEAT_OPTIONS: { value: Repeat; label: string }[] = [
  { value: 'daily', label: '每天' },
  { value: 'weekly', label: '每周' },
  { value: 'monthly', label: '每月' },
  { value: 'yearly', label: '每年' },
]
const WEEKDAY_CN = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
const MAX_NAME_LEN = 32

// ─── 日期小工具（本地正午基准，与 DateField 同一 TZ 安全口径） ───

function parseYmd(s: string): { y: number; m: number; d: number } | null {
  const mm = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s)
  if (!mm) return null
  return { y: Number(mm[1]), m: Number(mm[2]), d: Number(mm[3]) }
}

function fmtYmd(y: number, m: number, d: number): string {
  return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`
}

function addDaysYmd(iso: string, days: number): string {
  const p = parseYmd(iso)!
  const dt = new Date(p.y, p.m - 1, p.d + days, 12)
  return fmtYmd(dt.getFullYear(), dt.getMonth() + 1, dt.getDate())
}

function dayOfWeek(iso: string): number {
  const p = parseYmd(iso)!
  return new Date(p.y, p.m - 1, p.d, 12).getDay()
}

function daysInMonth(y: number, m: number): number {
  return new Date(y, m, 0).getDate()
}

/** 周期命中判定 — 镜像 ScheduledTxEngine.isDue（weekly 同星期 / monthly 短月钳月末 / yearly 同月同日）。 */
function matchesCycle(rule: Pick<ScheduledRuleData, 'repeat' | 'startDate'>, iso: string): boolean {
  const start = parseYmd(rule.startDate)
  const day = parseYmd(iso)
  if (!start || !day) return false
  switch (rule.repeat) {
    case 'daily': return true
    case 'weekly': return dayOfWeek(rule.startDate) === dayOfWeek(iso)
    case 'monthly': return day.d === Math.min(start.d, daysInMonth(day.y, day.m))
    case 'yearly': return day.m === start.m && day.d === start.d
    default: return false
  }
}

/**
 * 下次执行日（含今天，自 from 起逐日扫描）；超 endDate 或扫完 4000 天（闰年
 * 2/29 年规则最长 4 年+）无命中 → null=已结束。enabled=false 由调用方先挡。
 */
function computeNextDue(
  rule: Pick<ScheduledRuleData, 'repeat' | 'startDate' | 'endDate'>,
  from: string
): string | null {
  const start = parseYmd(rule.startDate)
  if (!start) return null
  let cursor = from
  if (cursor < rule.startDate) cursor = rule.startDate
  const end = rule.endDate
  for (let i = 0; i < 4000; i++) {
    if (end && cursor > end) return null
    if (matchesCycle(rule, cursor)) return cursor
    cursor = addDaysYmd(cursor, 1)
  }
  return null
}

/** 'YYYY-MM-DD' → '9月12日'（跨年带年份；今天/明天给语义词）。 */
function dateLabel(iso: string, today: string): string {
  const p = parseYmd(iso)
  if (!p) return iso
  if (iso === today) return '今天'
  if (iso === addDaysYmd(today, 1)) return '明天'
  const label = `${p.m}月${p.d}日`
  return p.y === Number(today.slice(0, 4)) ? label : `${p.y}年${label}`
}

function repeatLabel(rule: Pick<ScheduledRuleData, 'repeat' | 'startDate'>): string {
  const start = parseYmd(rule.startDate)
  if (!start) return '—'
  switch (rule.repeat) {
    case 'daily': return '每天'
    case 'weekly': return `每${WEEKDAY_CN[dayOfWeek(rule.startDate)]}`
    case 'monthly': return `每月 ${start.d} 日`
    case 'yearly': return `每年 ${start.m}月${start.d}日`
    default: return '—'
  }
}

// ─── 表单 ───

interface RuleForm {
  enabled: boolean
  type: 'expense' | 'income'
  name: string
  amount: string
  bookId: string
  categoryId: string
  accountId: string | null
  includeInBudget: boolean
  note: string
  repeat: Repeat
  startDate: string
  endUnlimited: boolean
  endDate: string
}

function emptyForm(bookId: string, accountId: string | null): RuleForm {
  const today = todayBizDate(TZ)
  return {
    enabled: true, type: 'expense', name: '', amount: '',
    bookId, categoryId: '', accountId, includeInBudget: true, note: '',
    repeat: 'daily', startDate: today, endUnlimited: true, endDate: today,
  }
}

function formFromRule(r: ScheduledRuleData): RuleForm {
  return {
    enabled: r.enabled,
    type: r.type,
    name: r.name,
    amount: String(r.amountCents / 100),
    bookId: r.bookId,
    categoryId: r.categoryId,
    accountId: r.accountId,
    includeInBudget: r.includeInBudget,
    note: r.note,
    repeat: r.repeat,
    startDate: r.startDate,
    endUnlimited: r.endDate == null,
    endDate: r.endDate ?? r.startDate,
  }
}

export function ScheduledPage() {
  const bridge = useBridge()
  const [rules, setRules] = useState<ScheduledRuleData[]>([])
  const [books, setBooks] = useState<BookData[]>([])
  const [categories, setCategories] = useState<CategoryData[]>([])
  const [accounts, setAccounts] = useState<AccountData[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // 表单：null = 关闭；editingId=null 表示新建，非 null 表示编辑该规则
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingVersion, setEditingVersion] = useState(0)
  const [form, setForm] = useState<RuleForm>(() => emptyForm('', null))
  const [openPicker, setOpenPicker] = useState<'book' | 'account' | null>(null)

  const today = todayBizDate(TZ)

  const load = useCallback(async () => {
    try {
      const [ruleList, bookList, catList, accList, meta] = await Promise.all([
        bridge.listScheduledRules(),
        bridge.listBooks().catch(() => null),
        bridge.listCategories().catch(() => null),
        bridge.listAccounts().catch(() => null),
        bridge.getMeta(['default_book_id', 'default_account_id']).catch(() => ({}) as Record<string, string | null>),
      ])
      setRules(ruleList)
      if (bookList) setBooks(bookList)
      if (catList) setCategories(catList)
      if (accList) setAccounts(accList)
      // 新建表单默认值跟随 meta 默认账本/账户（列表加载完成后回填一次）
      setForm(prev => bookList && prev.bookId === ''
        ? { ...prev, bookId: pickEntryBook(bookList, meta['default_book_id'] ?? null)?.id ?? '', accountId: pickDefaultAccount(accList ?? [], meta['default_account_id'] ?? null)?.id ?? null }
        : prev)
    } catch (err) {
      console.error('Failed to load scheduled rules:', err)
      setError('加载失败，请重试')
    } finally {
      setLoading(false)
    }
  }, [bridge])

  useEffect(() => { load() }, [load])

  const selectableBooks = useMemo(() => books.filter(b => !b.isMaster), [books])
  const currentBook = books.find(b => b.id === form.bookId)
  const currentAccount = form.accountId ? accounts.find(a => a.id === form.accountId) : undefined
  const formCats = useMemo(() => categories.filter(c => c.kind === form.type), [categories, form.type])

  const openNew = () => {
    setEditingId(null)
    setForm(emptyForm(
      pickEntryBook(selectableBooks)?.id ?? '',
      pickDefaultAccount(accounts)?.id ?? null,
    ))
    setError(null)
    setShowForm(true)
  }

  const openEdit = (r: ScheduledRuleData) => {
    setEditingId(r.id)
    setEditingVersion(r.version)
    setForm(formFromRule(r))
    setError(null)
    setShowForm(true)
    setOpenPicker(null)
  }

  const closeForm = () => {
    setShowForm(false)
    setEditingId(null)
    setOpenPicker(null)
    setError(null)
  }

  const handleSubmit = async () => {
    const name = form.name.trim()
    if (!name) { setError('请填写任务名称'); return }
    const amountCents = Money.fromString(form.amount) as number
    if (!Number.isFinite(amountCents) || amountCents < 1) { setError('金额需大于 0'); return }
    if (!form.bookId) { setError('请选择账本'); return }
    if (!form.categoryId) { setError('请选择分类'); return }
    const endDate = form.endUnlimited ? null : form.endDate
    if (endDate && endDate < form.startDate) { setError('结束日期不能早于开始日期'); return }

    setError(null)
    try {
      if (editingId) {
        await bridge.updateScheduledRule({
          id: editingId,
          type: form.type, name, amountCents,
          bookId: form.bookId, categoryId: form.categoryId,
          accountId: form.accountId,
          includeInBudget: form.includeInBudget,
          note: form.note.trim(),
          repeat: form.repeat, startDate: form.startDate, endDate,
          enabled: form.enabled,
          version: editingVersion + 1,
        })
      } else {
        await bridge.insertScheduledRule({
          id: newUuid(), ownerId: 'local',
          type: form.type, name, amountCents,
          bookId: form.bookId, categoryId: form.categoryId,
          accountId: form.accountId,
          includeInBudget: form.includeInBudget,
          note: form.note.trim(),
          repeat: form.repeat, startDate: form.startDate, endDate,
          enabled: form.enabled,
          version: 1,
        })
      }
      closeForm()
      await load()
    } catch (err) {
      console.error('Failed to save scheduled rule:', err)
      setError('保存失败，请重试')
    }
  }

  const handleToggleEnabled = async (r: ScheduledRuleData, next: boolean) => {
    setError(null)
    try {
      await bridge.updateScheduledRule({ id: r.id, enabled: next, version: r.version + 1 })
      await load()
    } catch (err) {
      console.error('Failed to toggle rule:', err)
      setError('切换失败，请重试')
    }
  }

  const handleArchive = async (r: ScheduledRuleData) => {
    if (!window.confirm(`确定要删除定时任务「${r.name}」吗？`)) return
    setError(null)
    try {
      await bridge.archiveScheduledRule(r.id)
      if (editingId === r.id) closeForm()
      await load()
    } catch (err) {
      console.error('Failed to archive rule:', err)
      setError('删除失败，请重试')
    }
  }

  return (
    <div className="min-h-screen">
      <PageHeader back />

      <div className="px-4 py-4 space-y-4 pb-20">
        <div className="text-xs text-ink3 text-center">定时规则仅保存在本机，不随账号同步；App 未打开的日期不补记</div>

        {error && (
          <div className="text-xs px-3 py-2 rounded-lg" style={{ background: 'rgba(220,38,38,0.1)', color: '#dc2626' }} data-testid="scheduled-error">
            {error}
          </div>
        )}

        {/* 规则列表 */}
        {loading ? (
          <div className="glass-card p-4 text-xs text-ink3">加载中…</div>
        ) : rules.length === 0 ? (
          <div className="glass-card p-6 text-center text-sm text-ink3">还没有定时任务，点击下方新建</div>
        ) : (
          rules.map(r => {
            const book = books.find(b => b.id === r.bookId)
            const cat = categories.find(c => c.id === r.categoryId)
            // 今天已跑过（lastRunDate=今天）则下次从明天起算
            const next = r.enabled
              ? computeNextDue(r, r.lastRunDate === today ? addDaysYmd(today, 1) : today)
              : null
            return (
              <div
                key={r.id}
                className="glass-card p-4 space-y-2"
                data-testid={`scheduled-card-${r.id}`}
                onClick={() => openEdit(r)}
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium flex-1 truncate" style={{ color: 'var(--ink)' }}>{r.name}</span>
                  <span className="text-xs shrink-0 px-2 py-0.5 rounded-full" style={{ background: 'var(--glass)', color: 'var(--ink2)' }} data-testid={`scheduled-repeat-${r.id}`}>
                    {repeatLabel(r)}
                  </span>
                  <span className="text-sm money-num shrink-0" style={{ color: r.type === 'expense' ? 'var(--expense)' : 'var(--income)' }}>
                    {r.type === 'expense' ? '-' : '+'}{Money.formatDisplay(Money.cents(r.amountCents))}
                  </span>
                  <span onClick={e => e.stopPropagation()}>
                    <RuleSwitch
                      testId={`scheduled-toggle-${r.id}`}
                      checked={r.enabled}
                      onChange={v => void handleToggleEnabled(r, v)}
                    />
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-xs truncate flex items-center gap-1 min-w-0" style={{ color: 'var(--ink3)' }} data-testid={`scheduled-subline-${r.id}`}>
                    <CategoryIcon icon={book?.icon} name={book?.name || '账本'} fallback="📒" />
                    <span className="truncate">{book?.name || '账本已删除'} · {cat?.name || '分类已删除'}</span>
                  </span>
                  <button
                    onClick={e => { e.stopPropagation(); void handleArchive(r) }}
                    className="text-xs px-2 py-1 rounded-lg shrink-0"
                    style={{ color: 'var(--ink3)' }}
                    data-testid={`scheduled-archive-${r.id}`}
                  >
                    删除
                  </button>
                </div>
                <div className="text-xs" style={{ color: 'var(--ink3)' }} data-testid={`scheduled-runtime-${r.id}`}>
                  上次 {r.lastRunDate ? dateLabel(r.lastRunDate, today) : '未执行'} · 下次{r.enabled ? (next ? ` ${dateLabel(next, today)}` : ' 已结束') : ' 已暂停'}
                </div>
              </div>
            )
          })
        )}

        {/* 新建/编辑表单 */}
        {!showForm ? (
          <button
            onClick={openNew}
            className="w-full py-3 rounded-lg text-sm font-medium"
            style={{ background: 'var(--accent)', color: 'white' }}
            data-testid="scheduled-new-btn"
          >
            ＋ 新建定时任务
          </button>
        ) : (
          <div className="glass-card p-4 space-y-3">
            <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>
              {editingId ? '编辑定时任务' : '新建定时任务'}
            </div>

            {/* 任务状态开关 */}
            <div className="flex items-center justify-between">
              <span className="text-sm" style={{ color: 'var(--ink)' }}>任务状态</span>
              <RuleSwitch
                testId="scheduled-form-enabled"
                checked={form.enabled}
                onChange={v => setForm(f => ({ ...f, enabled: v }))}
              />
            </div>

            {/* 类型两态 */}
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setForm(f => ({ ...f, type: 'expense', categoryId: '' }))}
                className="flex-1 py-2 rounded-lg text-sm font-medium"
                style={{
                  background: form.type === 'expense' ? 'var(--expense)' : 'transparent',
                  color: form.type === 'expense' ? 'white' : 'var(--ink2)',
                  border: form.type === 'expense' ? 'none' : '1px solid var(--glass-border)',
                }}
                data-testid="scheduled-form-type-expense"
              >
                支出
              </button>
              <button
                type="button"
                onClick={() => setForm(f => ({ ...f, type: 'income', categoryId: '' }))}
                className="flex-1 py-2 rounded-lg text-sm font-medium"
                style={{
                  background: form.type === 'income' ? 'var(--income)' : 'transparent',
                  color: form.type === 'income' ? 'white' : 'var(--ink2)',
                  border: form.type === 'income' ? 'none' : '1px solid var(--glass-border)',
                }}
                data-testid="scheduled-form-type-income"
              >
                收入
              </button>
            </div>

            <input
              type="text"
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              placeholder="任务名称（如：早餐）"
              maxLength={MAX_NAME_LEN}
              className="w-full px-3 py-2 rounded-lg text-sm outline-none"
              style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
              data-testid="scheduled-form-name"
            />

            <div>
              <div className="text-xs text-ink2 mb-1">金额 (¥)</div>
              <input
                type="number"
                inputMode="decimal"
                min="0.01"
                step="0.01"
                value={form.amount}
                onChange={e => setForm(f => ({ ...f, amount: e.target.value }))}
                placeholder="0.00"
                className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
                data-testid="scheduled-form-amount"
              />
            </div>

            {/* 账本下拉（过滤 master） */}
            <div>
              <button
                type="button"
                onClick={() => setOpenPicker(openPicker === 'book' ? null : 'book')}
                className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs"
                style={{ background: 'var(--glass)', color: 'var(--ink2)', border: '1px solid var(--glass-border)' }}
                data-testid="scheduled-form-book-btn"
              >
                <CategoryIcon icon={currentBook?.icon} name={currentBook?.name || '账本'} fallback="📒" />
                <span className="max-w-[9rem] truncate">{currentBook?.name || '选择账本'}</span>
                <span aria-hidden style={{ opacity: 0.6 }}>▾</span>
              </button>
              {openPicker === 'book' && (
                <div
                  className="mt-2 rounded-xl p-1 max-h-48 overflow-y-auto"
                  style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
                  data-testid="scheduled-form-book-list"
                >
                  {selectableBooks.length === 0 ? (
                    <div className="px-3 py-2 text-xs text-ink3">暂无可用账本</div>
                  ) : (
                    selectableBooks.map(b => (
                      <button
                        key={b.id}
                        type="button"
                        onClick={() => { setForm(f => ({ ...f, bookId: b.id })); setOpenPicker(null) }}
                        className="w-full text-left px-3 py-2 rounded-lg text-sm flex items-center justify-between"
                        style={{ color: form.bookId === b.id ? 'var(--accent)' : 'var(--ink2)' }}
                        data-testid={`scheduled-form-book-${b.id}`}
                      >
                        <span className="flex items-center gap-1.5 min-w-0">
                          <CategoryIcon icon={b.icon} name={b.name || '账本'} fallback="📒" />
                          <span className="truncate">{b.name || '账本'}</span>
                        </span>
                        {form.bookId === b.id && <span>✓</span>}
                      </button>
                    ))
                  )}
                </div>
              )}
            </div>

            {/* 分类宫格（CategoryIcon 复用，随类型过滤） */}
            <div>
              <div className="text-xs text-ink2 mb-1">分类</div>
              <div className="grid grid-cols-5 gap-2" data-testid="scheduled-form-category-grid">
                {formCats.map(cat => (
                  <button
                    key={cat.id}
                    type="button"
                    onClick={() => setForm(f => ({ ...f, categoryId: cat.id }))}
                    className={`cat-cell text-xs ${form.categoryId === cat.id ? 'active' : ''}`}
                    style={{ background: form.categoryId === cat.id ? 'var(--glass)' : 'transparent' }}
                    data-testid={`scheduled-form-category-${cat.id}`}
                  >
                    <CategoryIcon icon={cat.icon} name={cat.name} className="text-lg" />
                    <span className="truncate max-w-full text-ink2">{cat.name}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* 账户下拉（含"不使用账户"） */}
            <div>
              <button
                type="button"
                onClick={() => setOpenPicker(openPicker === 'account' ? null : 'account')}
                className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs"
                style={{ background: 'var(--glass)', color: 'var(--ink2)', border: '1px solid var(--glass-border)' }}
                data-testid="scheduled-form-account-btn"
              >
                <CategoryIcon icon={currentAccount?.icon} name={currentAccount?.name || '不使用账户'} fallback="💳" />
                <span className="max-w-[9rem] truncate">{currentAccount?.name || '不使用账户'}</span>
                <span aria-hidden style={{ opacity: 0.6 }}>▾</span>
              </button>
              {openPicker === 'account' && (
                <div
                  className="mt-2 rounded-xl p-1 max-h-48 overflow-y-auto"
                  style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
                  data-testid="scheduled-form-account-list"
                >
                  <button
                    type="button"
                    onClick={() => { setForm(f => ({ ...f, accountId: null })); setOpenPicker(null) }}
                    className="w-full text-left px-3 py-2 rounded-lg text-sm flex items-center justify-between"
                    style={{ color: form.accountId === null ? 'var(--accent)' : 'var(--ink2)' }}
                    data-testid="scheduled-form-account-none"
                  >
                    <span>不使用账户</span>
                    {form.accountId === null && <span>✓</span>}
                  </button>
                  {accounts.map(a => (
                    <button
                      key={a.id}
                      type="button"
                      onClick={() => { setForm(f => ({ ...f, accountId: a.id })); setOpenPicker(null) }}
                      className="w-full text-left px-3 py-2 rounded-lg text-sm flex items-center justify-between"
                      style={{ color: form.accountId === a.id ? 'var(--accent)' : 'var(--ink2)' }}
                      data-testid={`scheduled-form-account-${a.id}`}
                    >
                      <span className="flex items-center gap-1.5 min-w-0">
                        <CategoryIcon icon={a.icon} name={a.name} fallback="💰" />
                        <span className="truncate">{a.name}</span>
                      </span>
                      {form.accountId === a.id && <span>✓</span>}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* 是否计入预算（是/否，默认是） */}
            <div className="flex items-center justify-between">
              <span className="text-sm" style={{ color: 'var(--ink)' }}>计入预算</span>
              <div className="flex gap-2">
                {[true, false].map(v => (
                  <button
                    key={String(v)}
                    type="button"
                    onClick={() => setForm(f => ({ ...f, includeInBudget: v }))}
                    className="px-4 py-1.5 rounded-lg text-xs"
                    style={{
                      background: form.includeInBudget === v ? 'var(--accent)' : 'var(--glass)',
                      color: form.includeInBudget === v ? 'white' : 'var(--ink2)',
                      border: '1px solid var(--glass-border)',
                    }}
                    data-testid={v ? 'scheduled-form-budget-yes' : 'scheduled-form-budget-no'}
                  >
                    {v ? '是' : '否'}
                  </button>
                ))}
              </div>
            </div>

            <input
              type="text"
              value={form.note}
              onChange={e => setForm(f => ({ ...f, note: e.target.value }))}
              placeholder="备注（可选）"
              className="w-full px-3 py-2 rounded-lg text-sm outline-none"
              style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
              data-testid="scheduled-form-note"
            />

            {/* 重复周期四 chip */}
            <div>
              <div className="text-xs text-ink2 mb-1">重复周期</div>
              <div className="flex gap-2">
                {REPEAT_OPTIONS.map(opt => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setForm(f => ({ ...f, repeat: opt.value }))}
                    className="flex-1 py-1.5 rounded-lg text-xs"
                    style={{
                      background: form.repeat === opt.value ? 'var(--accent)' : 'var(--glass)',
                      color: form.repeat === opt.value ? 'white' : 'var(--ink2)',
                      border: '1px solid var(--glass-border)',
                    }}
                    data-testid={`scheduled-form-repeat-${opt.value}`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            {/* 开始日期 */}
            <div className="flex items-center justify-between">
              <span className="text-sm" style={{ color: 'var(--ink)' }}>开始日期</span>
              <DateField
                value={form.startDate}
                onChange={v => setForm(f => ({ ...f, startDate: v }))}
                testId="scheduled-form-start-date"
              />
            </div>

            {/* 结束日期：无限制 / 按日期 */}
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm shrink-0" style={{ color: 'var(--ink)' }}>结束日期</span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setForm(f => ({ ...f, endUnlimited: true }))}
                  className="px-3 py-1.5 rounded-lg text-xs"
                  style={{
                    background: form.endUnlimited ? 'var(--accent)' : 'var(--glass)',
                    color: form.endUnlimited ? 'white' : 'var(--ink2)',
                    border: '1px solid var(--glass-border)',
                  }}
                  data-testid="scheduled-form-end-unlimited"
                >
                  无限制
                </button>
                <button
                  type="button"
                  onClick={() => setForm(f => ({ ...f, endUnlimited: false }))}
                  className="px-3 py-1.5 rounded-lg text-xs"
                  style={{
                    background: !form.endUnlimited ? 'var(--accent)' : 'var(--glass)',
                    color: !form.endUnlimited ? 'white' : 'var(--ink2)',
                    border: '1px solid var(--glass-border)',
                  }}
                  data-testid="scheduled-form-end-dated"
                >
                  按日期
                </button>
                {!form.endUnlimited && (
                  <DateField
                    value={form.endDate}
                    onChange={v => setForm(f => ({ ...f, endDate: v }))}
                    testId="scheduled-form-end-date"
                  />
                )}
              </div>
            </div>

            <div className="flex gap-2 pt-1">
              <button
                onClick={closeForm}
                className="flex-1 py-2 rounded-lg text-sm"
                style={{ background: 'var(--glass)', color: 'var(--ink2)' }}
                data-testid="scheduled-form-cancel"
              >
                取消
              </button>
              <button
                onClick={() => void handleSubmit()}
                disabled={!form.name.trim() || !form.amount}
                className="flex-1 py-2 rounded-lg text-sm font-medium text-white disabled:opacity-40"
                style={{ background: 'var(--accent)' }}
                data-testid="scheduled-form-save"
              >
                {editingId ? '保存' : '创建'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

/** 视觉开关（样式对齐 WidgetSettingsPage ConfigSwitch；input.sr-only 保留可访问性与点击热区）。 */
function RuleSwitch({ testId, checked, onChange }: {
  testId: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <label className="relative inline-flex items-center cursor-pointer">
      <input
        type="checkbox"
        data-testid={testId}
        checked={checked}
        onChange={e => onChange(e.target.checked)}
        className="sr-only peer"
        aria-label="任务开关"
      />
      <div
        className="w-11 h-6 rounded-full peer transition-colors"
        style={{ background: checked ? 'var(--accent)' : 'var(--glass-border)' }}
      >
        <div
          className="w-5 h-5 rounded-full bg-white shadow transition-transform mt-0.5"
          style={{ transform: checked ? 'translateX(22px)' : 'translateX(2px)' }}
        />
      </div>
    </label>
  )
}
