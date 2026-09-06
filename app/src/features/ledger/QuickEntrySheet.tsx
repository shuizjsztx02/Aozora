/**
 * QuickEntrySheet — 快速记账底部弹层。
 *
 * 收支、金额、分类、账户、日期、备注 → bridge 写入 → pending mutation。
 */

import { useState, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useBridge } from '../../platform/hooks/use-bridge'
import type { CategoryData } from '../../platform/bridge/bridge-types'
import * as Money from '../../domain/money'
import { todayBizDate } from '../../domain/date'

interface QuickEntrySheetProps {
  categories: CategoryData[]
  onClose: () => void
  onSuccess: () => void
}

export function QuickEntrySheet({ categories, onClose, onSuccess }: QuickEntrySheetProps) {
  const bridge = useBridge()
  const [type, setType] = useState<'expense' | 'income'>('expense')
  const [amount, setAmount] = useState('')
  const [categoryId, setCategoryId] = useState<string>('')
  const [note, setNote] = useState('')
  const [bizDate] = useState(() => todayBizDate('Asia/Shanghai'))
  const [submitting, setSubmitting] = useState(false)

  const filteredCats = categories.filter(c => c.kind === type)

  const handleAmountKey = useCallback((key: string) => {
    setAmount(prev => {
      if (key === 'delete') return prev.slice(0, -1)
      if (key === '.' && prev.includes('.')) return prev
      if (key === '.' && prev.length === 0) return '0.'
      // 限制小数位不超过2位
      if (prev.includes('.') && prev.split('.')[1].length >= 2) return prev
      if (prev.length > 10) return prev
      return prev + key
    })
  }, [])

  const handleSubmit = useCallback(async () => {
    if (!amount || !categoryId || submitting) return

    setSubmitting(true)
    try {
      const amountCents = Money.fromString(amount)
      const books = await bridge.call('book.list', {}) as { id: string }[]
      const bookId = books[0]?.id
      if (!bookId) throw new Error('No book found')

      await bridge.call('transaction.insert', {
        id: `tx_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
        bookId,
        type,
        amountCents: amountCents as number,
        categoryId,
        accountId: null,
        note,
        bizDate,
        occurredAt: Date.now(),
        source: 'manual',
      })

      onSuccess()
    } catch (err) {
      console.error('Failed to insert transaction:', err)
    } finally {
      setSubmitting(false)
    }
  }, [amount, categoryId, type, note, bizDate, bridge, submitting, onSuccess])

  const displayAmount = amount || '0'

  return (
    <AnimatePresence>
      <motion.div
        className="fixed inset-0 z-50 flex flex-col justify-end"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.18 }}
      >
        {/* 遮罩 */}
        <div className="absolute inset-0 bg-black/30" onClick={onClose} />

        {/* Sheet 内容 */}
        <motion.div
          className="sheet-glass relative z-10 pb-4"
          initial={{ y: '100%' }}
          animate={{ y: 0 }}
          exit={{ y: '100%' }}
          transition={{ type: 'spring', damping: 34, stiffness: 380 }}
        >
          {/* 拖拽指示器 */}
          <div className="flex justify-center py-2">
            <div className="w-10 h-1 rounded-full" style={{ background: 'var(--ink3)', opacity: 0.3 }} />
          </div>

          {/* 收支切换 */}
          <div className="flex gap-2 px-4 mb-3">
            <button
              onClick={() => { setType('expense'); setCategoryId('') }}
              className={`flex-1 py-2 rounded-lg text-sm font-medium transition-colors ${type === 'expense' ? 'text-white' : ''}`}
              style={{
                background: type === 'expense' ? 'var(--expense)' : 'transparent',
                color: type === 'expense' ? 'white' : 'var(--ink2)',
                border: type === 'expense' ? 'none' : '1px solid var(--glass-border)',
              }}
            >
              支出
            </button>
            <button
              onClick={() => { setType('income'); setCategoryId('') }}
              className={`flex-1 py-2 rounded-lg text-sm font-medium transition-colors ${type === 'income' ? 'text-white' : ''}`}
              style={{
                background: type === 'income' ? 'var(--income)' : 'transparent',
                color: type === 'income' ? 'white' : 'var(--ink2)',
                border: type === 'income' ? 'none' : '1px solid var(--glass-border)',
              }}
            >
              收入
            </button>
          </div>

          {/* 金额显示 */}
          <div className="px-4 mb-3">
            <div className="money-num text-3xl text-center" style={{ color: 'var(--ink)' }}>
              ¥<span className={displayAmount === '0' ? 'opacity-30' : ''}>{displayAmount}</span>
            </div>
          </div>

          {/* 分类选择 */}
          <div className="px-4 mb-3">
            <div className="grid grid-cols-5 gap-2 max-h-32 overflow-y-auto">
              {filteredCats.map(cat => (
                <button
                  key={cat.id}
                  onClick={() => setCategoryId(cat.id)}
                  className={`cat-cell text-xs ${categoryId === cat.id ? 'active' : ''}`}
                  style={{
                    background: categoryId === cat.id ? 'var(--glass)' : 'transparent',
                  }}
                >
                  <span className="text-lg">{getCatEmoji(cat.name)}</span>
                  <span className="truncate max-w-full text-ink2">{cat.name}</span>
                </button>
              ))}
            </div>
          </div>

          {/* 备注 */}
          <div className="px-4 mb-3">
            <input
              type="text"
              value={note}
              onChange={e => setNote(e.target.value)}
              placeholder="备注（可选）"
              className="w-full px-3 py-2 rounded-lg text-sm outline-none"
              style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
            />
          </div>

          {/* 数字键盘 */}
          <div className="px-4 grid grid-cols-3 gap-2 mb-3">
            {['1','2','3','4','5','6','7','8','9','.','0','delete'].map(key => (
              <button
                key={key}
                onClick={() => handleAmountKey(key)}
                className="key-btn"
              >
                {key === 'delete' ? '⌫' : key}
              </button>
            ))}
          </div>

          {/* 确认按钮 */}
          <div className="px-4">
            <button
              onClick={handleSubmit}
              disabled={!amount || !categoryId || submitting}
              className="w-full py-3 rounded-xl text-white font-medium text-base transition-opacity disabled:opacity-40"
              style={{ background: 'var(--accent)' }}
            >
              {submitting ? '保存中...' : '记一笔'}
            </button>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  )
}

function getCatEmoji(name: string): string {
  const map: Record<string, string> = {
    '餐饮': '🍽️', '早餐': '🥐', '午餐': '🍱', '晚餐': '🍜', '零食': '🍪',
    '交通': '🚇', '打车': '🚕', '公交': '🚌',
    '购物': '🛍️', '日用': '🧴', '服饰': '👔',
    '娱乐': '🎮', '电影': '🎬', '游戏': '🎲',
    '居住': '🏠', '房租': '🏢', '水电': '💡',
    '医疗': '🏥', '通讯': '📱', '学习': '📚',
    '工资': '💰', '奖金': '🎁', '理财': '📈',
  }
  return map[name] || '💰'
}
