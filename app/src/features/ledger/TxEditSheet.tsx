/**
 * TxEditSheet — 交易行点击编辑底部弹层（第十二轮 T12-10 / REQ-36 主体）。
 *
 * 表单与 REQ-35 v3 聊天卡同款（entry-controls 共享组件）：行1 分类图标钮（卡内
 * 展开 CategoryPager）+ 收支柱子 + ¥金额；行2 日期 + 备注；行3 EntryChipRow。
 * 预填既有交易；保存 = bridge.updateTransaction 真 update（保 id 保附件，决策 #4，
 * version = 当前 +1 乐观锁），失败可见可重试（错误走 T12-8 localizeBridgeError）；
 * 「删除此笔」复用既有 softDelete 确认文案。账本 chip = 跨账本移动（payload 带新
 * bookId），保存/删除成功后 triggerSync（入队，失败不阻断，同 use-assistant 口径）
 * + 通知宿主刷新。
 *
 * 切收支清空分类需重选（v3 同款；编辑不产生新的缺分类笔——REQ-36 ④）。
 * occurredAt：bizDate 未变保留原值；改期按 QuickEntry 口径重算（今天=now，否则当日 12:00）。
 */

import { useCallback, useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useBridge } from '../../platform/hooks/use-bridge'
import { newUuid } from '../../platform/uuid'
import { localizeBridgeError } from '../assistant/use-assistant'
import { CategoryIcon } from '../../components/CategoryIcon'
import DateField from '../../components/DateField'
import { CategoryPager, EntryChipRow } from './entry-controls'
import { todayBizDate } from '../../domain/date'
import type { AccountData, BookData, CategoryData, TransactionData } from '../../platform/bridge/bridge-types'

interface TxEditSheetProps {
  tx: TransactionData
  categories: CategoryData[]
  onClose: () => void
  /** 保存/删除成功：宿主刷新列表 */
  onSaved: () => void
}

/** ConfirmationCard 同款：字符串解析避免浮点精度问题。 */
function parseAmountCents(s: string): number | null {
  const cents = Math.round(parseFloat(s) * 100)
  if (isNaN(cents) || cents < 1) return null
  return cents
}

export function TxEditSheet({ tx, categories, onClose, onSaved }: TxEditSheetProps) {
  const bridge = useBridge()
  const [txType, setTxType] = useState<'expense' | 'income'>(tx.type)
  const [amountYuan, setAmountYuan] = useState((tx.amountCents / 100).toFixed(2))
  const [categoryId, setCategoryId] = useState(tx.categoryId)
  const [bizDate, setBizDate] = useState(tx.bizDate)
  const [note, setNote] = useState(tx.note)
  // T12-8 表注：桥错误中文化复用
  const [saveError, setSaveError] = useState<string | null>(null)
  const [amountError, setAmountError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  // v3 宫格展开/受控页码
  const [catOpen, setCatOpen] = useState(false)
  const [catPage, setCatPage] = useState(0)
  // EntryChipRow 三值：预填交易自身（账本/账户/预算），编辑即移动/改挂
  const [selectedBookId, setSelectedBookId] = useState<string>(tx.bookId)
  const [selectedAccountId, setSelectedAccountId] = useState<string | null>(tx.accountId ?? null)
  const [includeInBudget, setIncludeInBudget] = useState<boolean>(tx.includeInBudget !== false)

  const [books, setBooks] = useState<BookData[]>([])
  const [accounts, setAccounts] = useState<AccountData[]>([])
  useEffect(() => {
    let cancelled = false
    Promise.all([
      bridge.listBooks().catch(() => null),
      bridge.listAccounts().catch(() => null),
    ]).then(([bs, as]) => {
      if (cancelled) return
      if (bs) setBooks(bs)
      if (as) setAccounts(as)
    })
    return () => { cancelled = true }
  }, [bridge])

  const pagerCats = categories.filter(c => c.kind === txType)

  /** 分类 id →（图标, `一级 · 二级` 文字），ConfirmationCard catInfo 同口径。 */
  function catInfo(catId: string) {
    const cat = categories.find(c => c.id === catId)
    if (!cat) return { cat: null, icon: null, label: '未分类' }
    if (cat.parentId) {
      const parent = categories.find(c => c.id === cat.parentId)
      return {
        cat,
        icon: cat.icon ?? parent?.icon ?? null,
        label: parent ? `${parent.name} · ${cat.name}` : cat.name,
      }
    }
    return { cat, icon: cat.icon ?? null, label: cat.name }
  }
  const info = catInfo(categoryId)

  const handleTypeChange = (t: 'expense' | 'income') => {
    if (t === txType) return
    setTxType(t)
    setCategoryId('') // 类型切换后需重选分类再保存（编辑不产生缺分类笔）
    setCatPage(0)
  }

  const triggerSyncQuiet = useCallback(async () => {
    try { await bridge.triggerSync() } catch { /* 同步入队失败不阻断编辑（use-assistant 同口径） */ }
  }, [bridge])

  const handleSave = useCallback(async () => {
    if (saving) return
    const cents = parseAmountCents(amountYuan)
    if (cents === null) { setAmountError('金额必须大于 0'); return }
    if (!categoryId) { setSaveError('请选择分类'); return }
    setAmountError(null)
    setSaveError(null)
    setSaving(true)
    try {
      // bizDate 未变保留原 occurredAt；改期按 QuickEntry 口径（今天=now，否则当日 12:00）
      const occurredAt = bizDate === tx.bizDate
        ? tx.occurredAt
        : (bizDate === todayBizDate('Asia/Shanghai') ? Date.now() : new Date(`${bizDate}T12:00:00`).getTime())
      await bridge.updateTransaction({
        id: tx.id,
        bookId: selectedBookId,
        type: txType,
        amountCents: cents,
        categoryId,
        // 键显式存在：null = 不使用账户（Kotlin optionalString 按 null 清列）
        accountId: selectedAccountId,
        note,
        bizDate,
        occurredAt,
        source: tx.source,
        includeInBudget,
        version: tx.version + 1, // Kotlin 乐观锁：必须 > 当前版本
      }, newUuid())
      await triggerSyncQuiet()
      onSaved()
      onClose()
    } catch (err) {
      setSaveError(localizeBridgeError(err instanceof Error ? err.message : '保存失败'))
      setSaving(false)
    }
  }, [
    saving, amountYuan, categoryId, bizDate, tx, selectedBookId, txType,
    selectedAccountId, note, includeInBudget, bridge, triggerSyncQuiet, onSaved, onClose,
  ])

  const handleDelete = useCallback(async () => {
    if (deleting) return
    if (!window.confirm('确定要删除这条记录吗？')) return
    setDeleting(true)
    try {
      await bridge.softDeleteTransaction(tx.id, newUuid())
      await triggerSyncQuiet()
      onSaved()
      onClose()
    } catch (err) {
      setSaveError(localizeBridgeError(err instanceof Error ? err.message : '删除失败'))
      setDeleting(false)
    }
  }, [deleting, bridge, tx.id, triggerSyncQuiet, onSaved, onClose])

  return (
    <AnimatePresence>
      <motion.div
        className="fixed inset-0 z-50 flex flex-col justify-end"
        // T15-3（REQ-52 / 决策#3）：键盘弹起整卡顶到键盘上方——复用 T9-8 通道，
        // --ime-bottom-px 由 MainActivity 注入 :root，未注入环境回落 0px 布局不变；
        // 公式与 AssistantPage 根容器同款
        style={{ paddingBottom: 'calc(env(safe-area-inset-bottom, 0px) + var(--ime-bottom-px, 0px))' }}
        data-testid="tx-edit-root"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.18 }}
      >
        {/* 遮罩：保存/删除中禁点关，防半途丢编辑 */}
        <div
          className="absolute inset-0 bg-black/30"
          onClick={saving || deleting ? undefined : onClose}
        />

        <motion.div
          className="sheet-glass relative z-10 pb-4"
          initial={{ y: '100%' }}
          animate={{ y: 0 }}
          transition={{ type: 'spring', damping: 34, stiffness: 380 }}
          data-testid="tx-edit-sheet"
        >
          {/* 拖拽指示器 */}
          <div className="flex justify-center py-2">
            <div className="w-10 h-1 rounded-full" style={{ background: 'var(--ink3)', opacity: 0.3 }} />
          </div>

          <div className="px-4">
            {/* 行1：分类图标钮（点击展开/收起宫格）+ 收支 + 金额 —— v3 同款 */}
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setCatOpen(o => !o)}
                className="flex items-center gap-1 shrink-0 px-1.5 py-1 rounded-lg"
                style={{ background: catOpen ? 'var(--glass)' : 'transparent' }}
                data-testid="tx-edit-cat-toggle"
                aria-expanded={catOpen}
              >
                <CategoryIcon
                  icon={info.icon}
                  name={info.cat?.name || '💰'}
                  className="text-[26px] leading-none shrink-0"
                />
                <span className="text-xs max-w-[5.5rem] truncate" style={{ color: 'var(--ink2)' }}>
                  {info.label}
                </span>
                <span aria-hidden style={{ opacity: 0.6 }}>▾</span>
              </button>
              <div className="flex gap-1 shrink-0">
                {(['expense', 'income'] as const).map(t => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => handleTypeChange(t)}
                    className="px-2 py-1 rounded-lg text-xs font-medium transition-colors"
                    style={{
                      background: txType === t
                        ? (t === 'expense' ? 'var(--expense)' : 'var(--income)')
                        : 'transparent',
                      color: txType === t ? 'white' : 'var(--ink2)',
                      border: txType === t ? 'none' : '1px solid var(--glass-border)',
                    }}
                    data-testid={`tx-edit-type-${t}`}
                  >
                    {t === 'expense' ? '支出' : '收入'}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-1 flex-1 min-w-0">
                <span className="text-base shrink-0" style={{ color: 'var(--ink2)' }}>¥</span>
                <input
                  type="number"
                  value={amountYuan}
                  onChange={e => { setAmountYuan(e.target.value); setAmountError(null) }}
                  className="w-full min-w-0 bg-transparent text-right text-lg font-bold outline-none"
                  style={{ color: 'var(--ink)' }}
                  min="0.01"
                  step="0.01"
                  data-testid="tx-edit-amount"
                />
              </div>
            </div>

            {/* 卡内展开的分类宫格（CategoryPager 共享组件） */}
            {catOpen && (
              <div className="mt-2">
                <CategoryPager
                  categories={pagerCats}
                  selectedId={categoryId}
                  onSelect={setCategoryId}
                  page={catPage}
                  onPageChange={setCatPage}
                />
              </div>
            )}

            {/* 行2：日期 + 备注 同行 */}
            <div className="flex gap-2 mt-2">
              <DateField
                value={bizDate}
                onChange={setBizDate}
                testId="tx-edit-date"
                className="shrink-0"
              />
              <input
                type="text"
                value={note}
                onChange={e => setNote(e.target.value)}
                placeholder="备注"
                className="flex-1 min-w-0 px-2 py-1 rounded-lg text-xs outline-none"
                style={{
                  background: 'var(--glass)',
                  color: 'var(--ink)',
                  border: '1px solid var(--glass-border)',
                }}
                data-testid="tx-edit-note"
              />
            </div>

            {/* 行3：账本 / 账户 / 预算 chip 行（账本切换 = 跨账本移动） */}
            <div className="mt-2">
              <EntryChipRow
                books={books}
                accounts={accounts}
                selectedBookId={selectedBookId}
                selectedAccountId={selectedAccountId}
                includeInBudget={includeInBudget}
                onBookSelect={setSelectedBookId}
                onAccountSelect={setSelectedAccountId}
                onBudgetSelect={setIncludeInBudget}
              />
            </div>

            {/* 错误行（金额校验 + 保存/删除桥错误，可改可重试） */}
            {(amountError || saveError) && (
              <div className="text-xs mt-2" style={{ color: 'var(--expense)' }} data-testid="tx-edit-error">
                {amountError || saveError}
              </div>
            )}

            {/* 保存 */}
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="w-full mt-3 py-2.5 rounded-lg text-sm font-medium text-white active:opacity-80"
              style={{ background: 'var(--accent)', opacity: saving ? 0.6 : 1 }}
              data-testid="tx-edit-save"
            >
              {saving ? '保存中…' : '保存'}
            </button>

            {/* 删除此笔（复用明细页行内删除同文案确认） */}
            <button
              type="button"
              onClick={handleDelete}
              disabled={deleting || saving}
              className="w-full mt-2 py-1.5 text-xs active:opacity-60"
              style={{ color: 'var(--expense)' }}
              data-testid="tx-edit-delete"
            >
              {deleting ? '删除中…' : '删除此笔'}
            </button>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  )
}
