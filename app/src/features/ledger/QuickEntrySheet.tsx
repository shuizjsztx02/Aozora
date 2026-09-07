/**
 * QuickEntrySheet — 快速记账底部弹层。
 *
 * 收支、金额、分类、账户、日期、备注 → bridge 写入 → pending mutation。
 * T-M4-04e: 图片上传成功后自动触发 AI 解析 → 批量确认卡 → 执行 add_tx_batch。
 */

import { useState, useCallback, useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useBridge } from '../../platform/hooks/use-bridge'
import { useImage } from '../../platform/hooks/use-image'
import { useImageDraftStore, useImageDraft } from '../../platform/store/image-draft-store'
import { ImagePickerSheet } from '../image/ImagePickerSheet'
import { ImageUploadView } from '../image/ImageUploadView'
import { BatchConfirmationCard } from './BatchConfirmationCard'
import type { CategoryData } from '../../platform/bridge/bridge-types'
import type { ActionProposal } from '../../domain/action-proposal'
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
  const [showImagePicker, setShowImagePicker] = useState(false)
  const image = useImage()
  const { saveDraft, clearDraft } = useImageDraftStore()
  const draft = useImageDraft()

  // T-M4-04e: 解析状态
  const [parseState, setParseState] = useState<'idle' | 'parsing' | 'done' | 'error'>('idle')
  const [parseProposal, setParseProposal] = useState<ActionProposal | null>(null)
  const [parseError, setParseError] = useState<string | null>(null)
  const [batchSubmitting, setBatchSubmitting] = useState(false)

  // 防止重复触发解析（React strict mode 或 image.attachment 引用变化）
  const parsedAttachmentRef = useRef<string | null>(null)

  // R-M4-55 fix: 缓存 bookId，挂载时调用一次 book.list
  const bookIdRef = useRef<string | null>(null)
  const getBookId = useCallback(async () => {
    if (bookIdRef.current) return bookIdRef.current
    const books = await bridge.call('book.list', {}) as { id: string }[]
    const id = books[0]?.id
    if (!id) throw new Error('No book found')
    bookIdRef.current = id
    return id
  }, [bridge])

  const filteredCats = categories.filter(c => c.kind === type)

  // ─── T-M4-04e: 图片上传成功后自动触发解析 ───────────────────────────

  // 初始化：若草稿中已有 proposal（上次解析成功但用户关闭了），恢复批量确认卡
  useEffect(() => {
    if (draft?.proposal && draft.attachmentId) {
      const restored = draft.proposal as unknown as ActionProposal
      setParseProposal(restored)
      setParseState('done')
      // 恢复 image.attachment 状态以便 ImageUploadView 能显示缩略图
      if (draft.attachment) {
        image.restore(draft.attachment)
      }
    } else if (draft?.parseError && draft.attachmentId) {
      setParseError(draft.parseError)
      setParseState('error')
    }
  }, []) // 仅在挂载时检查一次

  // 图片上传完成 → 自动触发 AI 解析
  useEffect(() => {
    const att = image.attachment
    if (!att?.attachmentId) return
    if (att.attachmentId === parsedAttachmentRef.current) return
    parsedAttachmentRef.current = att.attachmentId

    const doParse = async () => {
      setParseState('parsing')
      setParseError(null)
      setParseProposal(null)
      try {
        const bookId = await getBookId()
        const result = await bridge.parseImage(att.attachmentId, bookId)
        const proposal = result.proposal as unknown as ActionProposal
        const txCount = (proposal.transactions ?? []).length

        // R-M4-54 fix: 零交易 → 设为 error 状态，避免 UI 死区
        if (txCount === 0) {
          setParseError('no_transactions')
          setParseState('error')
          // R-M4-57 fix: 零交易 proposal 不写草稿，避免跨会话死区
          saveDraft({
            attachmentId: att.attachmentId,
            attachment: att,
            source: draft?.source ?? 'gallery',
            error: null,
            parseError: 'no_transactions',
            proposal: null,
            createdAt: Date.now(),
          })
          return
        }

        setParseProposal(proposal)
        setParseState('done')
        // 保存 proposal 到草稿（用户关闭后可恢复）
        saveDraft({
          attachmentId: att.attachmentId,
          attachment: att,
          source: draft?.source ?? 'gallery',
          error: null,
          parseError: null,
          proposal: result.proposal,
          createdAt: Date.now(),
        })
      } catch (e) {
        const code = e instanceof Error && 'code' in e ? (e as { code: string }).code : 'unknown'
        setParseError(code)
        setParseState('error')
        saveDraft({
          attachmentId: att.attachmentId,
          attachment: att,
          source: draft?.source ?? 'gallery',
          error: null,
          parseError: code,
          proposal: null,
          createdAt: Date.now(),
        })
      }
    }
    doParse()
  }, [image.attachment]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleImagePick = useCallback(async (source: 'camera' | 'gallery') => {
    setShowImagePicker(false)
    setParseState('idle')
    setParseProposal(null)
    setParseError(null)
    parsedAttachmentRef.current = null
    try {
      await image.pickImage(source)
    } catch (e) {
      const code = e instanceof Error && 'code' in e ? (e as { code: string }).code : 'unknown'
      if (code !== 'cancelled') {
        saveDraft({
          attachmentId: image.attachment?.attachmentId ?? null,
          source,
          error: code,
          createdAt: Date.now(),
        })
      }
    }
  }, [image, saveDraft])

  // R-M4-47 fix: 草稿恢复 — 重新选择（沿用上次 source）
  const handleDraftRetry = useCallback(() => {
    if (!draft) return
    const source = draft.source
    clearDraft()
    setParseState('idle')
    setParseProposal(null)
    setParseError(null)
    parsedAttachmentRef.current = null
    handleImagePick(source)
  }, [draft, clearDraft, handleImagePick])

  // R-M4-47 fix: 草稿恢复 — 放弃草稿
  const handleDraftDismiss = useCallback(() => {
    clearDraft()
    setParseState('idle')
    setParseProposal(null)
    setParseError(null)
  }, [clearDraft])

  // T-M4-04e: 解析失败 — 重试解析（不重新上传）
  const handleParseRetry = useCallback(async () => {
    const attId = image.attachment?.attachmentId ?? draft?.attachmentId
    if (!attId) return
    const currentAttachment = image.attachment ?? draft?.attachment ?? null
    setParseState('parsing')
    setParseError(null)
    try {
      const bookId = await getBookId()
      const result = await bridge.parseImage(attId, bookId)
      const proposal = result.proposal as unknown as ActionProposal
      const txCount = (proposal.transactions ?? []).length

      if (txCount === 0) {
        setParseError('no_transactions')
        setParseState('error')
        saveDraft({
          attachmentId: attId,
          attachment: currentAttachment,
          source: draft?.source ?? 'gallery',
          error: null,
          parseError: 'no_transactions',
          proposal: null,
          createdAt: Date.now(),
        })
        return
      }

      setParseProposal(proposal)
      setParseState('done')
      saveDraft({
        attachmentId: attId,
        attachment: currentAttachment,
        source: draft?.source ?? 'gallery',
        error: null,
        parseError: null,
        proposal: result.proposal,
        createdAt: Date.now(),
      })
    } catch (e) {
      const code = e instanceof Error && 'code' in e ? (e as { code: string }).code : 'unknown'
      setParseError(code)
      setParseState('error')
      saveDraft({
        attachmentId: attId,
        attachment: currentAttachment,
        source: draft?.source ?? 'gallery',
        error: null,
        parseError: code,
        proposal: null,
        createdAt: Date.now(),
      })
    }
  }, [image.attachment, draft, bridge, getBookId, saveDraft])

  // T-M4-04e: 放弃解析结果
  const handleParseDismiss = useCallback(() => {
    setParseState('idle')
    setParseProposal(null)
    setParseError(null)
    parsedAttachmentRef.current = null
    clearDraft()
    // 不清除 image.attachment，用户可以继续手动记账或选择新图片
  }, [clearDraft])

  // T-M4-04e: 批量确认执行
  const handleBatchConfirm = useCallback(async (editedProposal: ActionProposal) => {
    setBatchSubmitting(true)
    try {
      const bookId = await getBookId()

      // R-M4-58 fix: 先 validate，提前拦截无效提案
      const proposalRecord = editedProposal as unknown as Record<string, unknown>
      const validation = await bridge.validateProposal(proposalRecord, bookId)
      if (!validation.valid) {
        console.warn('Proposal validation failed:', validation.errors)
        return
      }

      // 执行提案（服务端自动链接附件，因 proposal.meta.attachmentId 存在）
      await bridge.executeProposal(
        proposalRecord,
        bookId,
        'confirm_' + Date.now(),
      )

      // R-M4-59 fix: execute 成功后清除图片状态
      await image.clear()
      // 清除草稿，关闭弹层
      clearDraft()
      parsedAttachmentRef.current = null
      onSuccess()
    } catch (err) {
      console.error('Failed to execute batch proposal:', err)
      // 保留 parseProposal 状态，用户可以重试
    } finally {
      setBatchSubmitting(false)
    }
  }, [bridge, getBookId, image, clearDraft, onSuccess])

  const handleImageClear = useCallback(async () => {
    await image.clear()
    clearDraft()
    setParseState('idle')
    setParseProposal(null)
    setParseError(null)
    parsedAttachmentRef.current = null
  }, [image, clearDraft])

  const handleImagePickAgain = useCallback(() => {
    image.clear()
    setParseState('idle')
    setParseProposal(null)
    setParseError(null)
    parsedAttachmentRef.current = null
    setShowImagePicker(true)
  }, [image])

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
      const bookId = await getBookId()

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
  }, [amount, categoryId, type, note, bizDate, bridge, getBookId, submitting, onSuccess])

  const displayAmount = amount || '0'

  // 是否显示批量确认卡（替代普通表单）
  const showBatchCard = parseState === 'done' && parseProposal

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
        <div className="absolute inset-0 bg-black/30" onClick={batchSubmitting ? undefined : onClose} />

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

          {showBatchCard ? (
            /* T-M4-04e: 批量确认卡替代普通表单 */
            <>
              {/* 附件缩略图 */}
              <div className="px-4 mb-2">
                <ImageUploadView
                  uploading={false}
                  progress={100}
                  attachment={image.attachment}
                  error={null}
                  onRetry={() => {}}
                  onClear={handleImageClear}
                  onPickAgain={handleImagePickAgain}
                />
              </div>

              <BatchConfirmationCard
                proposal={parseProposal!}
                categories={categories}
                submitting={batchSubmitting}
                onConfirm={handleBatchConfirm}
                onCancel={handleParseDismiss}
              />

              {batchSubmitting && (
                <div className="text-center text-xs py-2" style={{ color: 'var(--ink3)' }}>
                  正在记账…
                </div>
              )}
            </>
          ) : (
            <>
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

              {/* T-M4-04d: 附件区域 */}
              <div className="px-4 mb-3">
                {image.uploading || image.attachment || (image.error && image.error !== 'cancelled') ? (
                  <ImageUploadView
                    uploading={image.uploading}
                    progress={image.progress}
                    attachment={image.attachment}
                    error={image.error}
                    onRetry={image.retry}
                    onClear={handleImageClear}
                    onPickAgain={handleImagePickAgain}
                  />
                ) : draft && !draft.parseError && !draft.proposal ? (
                  /* R-M4-47 fix: 草稿恢复 UI（仅上传失败时显示） */
                  <div
                    className="rounded-xl p-3"
                    style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
                    data-testid="image-draft-recovery"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs" style={{ color: 'var(--expense)' }}>
                        ❌ 上次上传失败
                      </span>
                    </div>
                    <div className="flex gap-2">
                      <button
                        onClick={handleDraftRetry}
                        className="text-xs px-3 py-1.5 rounded-lg font-medium text-white"
                        style={{ background: 'var(--accent)' }}
                        data-testid="draft-retry"
                      >
                        重试
                      </button>
                      <button
                        onClick={handleDraftDismiss}
                        className="text-xs px-3 py-1.5 rounded-lg"
                        style={{ color: 'var(--ink3)' }}
                        data-testid="draft-dismiss"
                      >
                        放弃
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    onClick={() => setShowImagePicker(true)}
                    disabled={!bridge.isAvailable()}
                    className="flex items-center gap-2 py-2 px-3 rounded-lg text-xs transition-colors disabled:opacity-40"
                    style={{ background: 'var(--glass)', color: 'var(--ink2)', border: '1px solid var(--glass-border)' }}
                    data-testid="attach-image-btn"
                  >
                    <span>📎</span>
                    <span>添加凭证</span>
                  </button>
                )}
              </div>

              {/* T-M4-04e: AI 解析状态 */}
              {parseState === 'parsing' && (
                <div className="px-4 mb-3 text-center">
                  <div
                    className="inline-flex items-center gap-2 px-3 py-2 rounded-lg text-xs"
                    style={{ background: 'var(--glass)', color: 'var(--ink2)', border: '1px solid var(--glass-border)' }}
                    data-testid="image-parsing"
                  >
                    <span className="animate-pulse">🔍</span>
                    <span>正在识别小票…</span>
                  </div>
                </div>
              )}

              {parseState === 'error' && (
                <div
                  className="mx-4 mb-3 rounded-xl p-3"
                  style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
                  data-testid="image-parse-error"
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs" style={{ color: 'var(--expense)' }}>
                      ❌ {parseError === 'no_transactions'
                        ? '未识别到消费记录'
                        : parseError === 'no_ai_provider' ? '识别失败（AI 服务不可用）' : '识别失败'}
                    </span>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={handleParseRetry}
                      className="text-xs px-3 py-1.5 rounded-lg font-medium text-white"
                      style={{ background: 'var(--accent)' }}
                      data-testid="parse-retry"
                    >
                      重试识别
                    </button>
                    <button
                      onClick={handleParseDismiss}
                      className="text-xs px-3 py-1.5 rounded-lg"
                      style={{ color: 'var(--ink3)' }}
                      data-testid="parse-dismiss"
                    >
                      手动记账
                    </button>
                  </div>
                </div>
              )}

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
            </>
          )}
        </motion.div>

        {/* T-M4-04d: 图片来源选择弹层 */}
        {showImagePicker && (
          <ImagePickerSheet
            onPick={handleImagePick}
            onClose={() => setShowImagePicker(false)}
          />
        )}
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
