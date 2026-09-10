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
import { newUuid } from '../../platform/uuid'
import { useImageDraftStore, useImageDraft } from '../../platform/store/image-draft-store'
import { ImagePickerSheet } from '../image/ImagePickerSheet'
import { ImageUploadView } from '../image/ImageUploadView'
import { BatchConfirmationCard } from './BatchConfirmationCard'
import type { CategoryData, BookData, AccountData } from '../../platform/bridge/bridge-types'
import type { ActionProposal } from '../../domain/action-proposal'
import * as Money from '../../domain/money'
import { todayBizDate } from '../../domain/date'
import DateField from '../../components/DateField'
import { pickEntryBook, pickDefaultAccount } from '../../domain/books'
import { CategoryPager, EntryChipRow } from './entry-controls'

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
  const [bizDate, setBizDate] = useState(() => todayBizDate('Asia/Shanghai'))
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [showImagePicker, setShowImagePicker] = useState(false)
  const image = useImage()
  const { saveDraft, clearDraft } = useImageDraftStore()
  const draft = useImageDraft()

  // T-M4-04e: 解析状态
  const [parseState, setParseState] = useState<'idle' | 'parsing' | 'done' | 'error'>('idle')
  const [parseProposal, setParseProposal] = useState<ActionProposal | null>(null)
  const [parseError, setParseError] = useState<string | null>(null)
  const [batchSubmitting, setBatchSubmitting] = useState(false)
  // NEW-E5 fix: 批量确认校验失败时展示给用户
  const [batchError, setBatchError] = useState<string | null>(null)

  // 防止重复触发解析（React strict mode 或 image.attachment 引用变化）
  const parsedAttachmentRef = useRef<string | null>(null)

  // R-M4-55 fix: 缓存 bookId，挂载时调用一次 book.list
  // T7-3（决策 #2）：记账目标 = 第一个非 master 账本，不能取 books[0]
  // T9-5b（REQ-11 决策 #3）：default_book_id（meta）命中且非 master 时优先
  const bookIdRef = useRef<string | null>(null)
  const defaultIdsRef = useRef<{ book: string | null; account: string | null }>({ book: null, account: null })
  const getBookId = useCallback(async () => {
    if (bookIdRef.current) return bookIdRef.current
    const books = await bridge.call('book.list', {}) as { id: string; isMaster?: boolean }[]
    const id = pickEntryBook(books, defaultIdsRef.current.book)?.id
    if (!id) throw new Error('No book found')
    bookIdRef.current = id
    return id
  }, [bridge])

  // 第八轮 REQ-9：账本 / 账户选择器。不设即按默认填充（默认账本/账户 =
  // meta default_*_id 优先，未设则 pickEntryBook / 第一个账户，无则 null）。
  // getBookId 仅供图片解析/batch 路径用默认。
  const [books, setBooks] = useState<BookData[]>([])
  const [accounts, setAccounts] = useState<AccountData[]>([])
  const [selectedBookId, setSelectedBookId] = useState<string | null>(null)
  const [selectedAccountId, setSelectedAccountId] = useState<string | null>(null)
  // T12-6：openPicker 单开态随 EntryChipRow 迁入组件内。
  // 第十轮 REQ-17（决策 #1）：手动记账初始"不计入预算"（用户明确要求），可切换；
  // 聊天/图片批量路径不传该字段 → native 缺省计入。
  const [includeInBudget, setIncludeInBudget] = useState(false)

  useEffect(() => {
    let cancelled = false
    // T9-5b：列表与默认 meta 一次并发取回（native list 已整形顺序，这里只补 defaultId）
    Promise.all([
      bridge.listBooks().catch(() => null),
      bridge.listAccounts().catch(() => null),
      bridge.getMeta(['default_book_id', 'default_account_id']).catch(() => ({}) as Record<string, string | null>),
    ]).then(([bookList, accountList, meta]) => {
      if (cancelled) return
      const defBookId = meta['default_book_id'] ?? null
      const defAccountId = meta['default_account_id'] ?? null
      defaultIdsRef.current = { book: defBookId, account: defAccountId }
      if (bookList) {
        setBooks(bookList)
        setSelectedBookId(pickEntryBook(bookList, defBookId)?.id ?? null)
      }
      if (accountList) {
        setAccounts(accountList)
        setSelectedAccountId(pickDefaultAccount(accountList, defAccountId)?.id ?? null)
      }
    })
    return () => { cancelled = true }
  }, [bridge])

  // T12-6：chip 文案/所选实体推导随 EntryChipRow 迁入组件。
  // selectedBookId/selectedAccountId/includeInBudget 仍留在宿主：handleSubmit 写库要用。
  const filteredCats = categories.filter(c => c.kind === type)

  // T12-6：分页/横滑迁入 CategoryPager；页码受控保留（切收支归零策略在本层）。
  const [catPage, setCatPage] = useState(0)

  // ─── T-M4-04e: 图片上传成功后自动触发解析 ───────────────────────────

  // 初始化：若草稿中已有 proposal（上次解析成功但用户关闭了），恢复批量确认卡
  // NEW-D2 note: 依赖 zustand persist 的同步 localStorage 水合——draft 在首次渲染时
  // 已就绪。若未来切换为 AsyncStorage（异步水合），需改用 zustand 的 onRehydrateStorage
  // 或 hasHydrated() 守卫，否则此处会读到空草稿、静默跳过恢复。
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
  }, []) // eslint-disable-line react-hooks/exhaustive-deps -- 仅在挂载时检查一次

  // 图片上传完成 → 自动触发 AI 解析
  useEffect(() => {
    const att = image.attachment
    if (!att?.attachmentId) return
    if (att.attachmentId === parsedAttachmentRef.current) return
    parsedAttachmentRef.current = att.attachmentId

    // NEW-D1 fix: 防止 stale parse — 用户放弃或换图后，过期结果不再覆盖状态
    let cancelled = false

    const doParse = async () => {
      setParseState('parsing')
      setParseError(null)
      setParseProposal(null)
      try {
        const bookId = await getBookId()
        if (cancelled) return
        const result = await bridge.parseImage(att.attachmentId, bookId)
        if (cancelled) return
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
        // NEW-R6-2 fix: 被取消的 parse 不写 error 状态，避免 UI 闪烁
        const code = e instanceof Error && 'code' in e ? (e as { code: string }).code : 'unknown'
        if (code === 'cancelled' || cancelled) return
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

    // NEW-1(R6) + NEW-R6-2 fix: 换图/卸载时标记 cancelled 并取消旧 parse，
    // 防止旧结果覆盖新状态 + 旧请求继续消耗 tokens
    return () => {
      cancelled = true
      bridge.cancelImageParse()
    }
  }, [image.attachment]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleImagePick = useCallback(async (source: 'camera' | 'gallery') => {
    setShowImagePicker(false)
    setParseState('idle')
    setParseProposal(null)
    setParseError(null)
    setBatchError(null)
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
    // P3-8 fix: 与主解析 effect 一致的 cancelled 守卫，防止用户换图/卸载后旧结果覆盖状态
    let cancelled = false
    try {
      const bookId = await getBookId()
      if (cancelled) return
      const result = await bridge.parseImage(attId, bookId)
      if (cancelled) return
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
      // P3-8 fix: 与主解析 effect 一致 — 被取消的 parse 不写 error 状态
      if (code === 'cancelled' || cancelled) return
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
    setBatchError(null)
    parsedAttachmentRef.current = null
    clearDraft()
    // 不清除 image.attachment，用户可以继续手动记账或选择新图片
  }, [clearDraft])

  // T-M4-04e: 批量确认执行
  const handleBatchConfirm = useCallback(async (editedProposal: ActionProposal) => {
    setBatchSubmitting(true)
    setBatchError(null)
    try {
      const bookId = await getBookId()

      // R-M4-58 fix: 先 validate，提前拦截无效提案
      const proposalRecord = editedProposal as unknown as Record<string, unknown>
      const validation = await bridge.validateProposal(proposalRecord, bookId)
      if (!validation.valid) {
        // NEW-E5 fix: 将校验失败反馈给用户，而非仅 console.warn
        console.warn('Proposal validation failed:', validation.errors)
        setBatchError(validation.errors.map(e => e.message).join('; ') || 'validation_failed')
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
      const msg = err instanceof Error ? err.message : 'unknown'
      setBatchError(msg)
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
    setBatchError(null)
    parsedAttachmentRef.current = null
  }, [image, clearDraft])

  const handleImagePickAgain = useCallback(() => {
    image.clear()
    setParseState('idle')
    setParseProposal(null)
    setParseError(null)
    setBatchError(null)
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
    setSubmitError(null)
    try {
      const amountCents = Money.fromString(amount)
      // REQ-9：优先用户所选账本，未设（列表未加载）则回退默认账本
      const bookId = selectedBookId ?? await getBookId()

      // T7-3（决策 #5）：input 清空时兜底今天；非今日 → occurredAt 取该日本地 12:00
      const safeBizDate = /^\d{4}-\d{2}-\d{2}$/.test(bizDate) ? bizDate : todayBizDate('Asia/Shanghai')
      const occurredAt = safeBizDate === todayBizDate('Asia/Shanghai')
        ? Date.now()
        : new Date(`${safeBizDate}T12:00:00`).getTime()

      // 服务端 sync/ai 契约要求 UUID（第六轮 Bug-5.2）
      const id = newUuid()
      // 必须走 insertTransaction 封装：native 侧期望 {tx, mutationId} 包裹结构
      await bridge.insertTransaction({
        id,
        bookId,
        type,
        amountCents: amountCents as number,
        categoryId,
        accountId: selectedAccountId,
        note,
        bizDate: safeBizDate,
        occurredAt,
        source: 'manual',
        includeInBudget,
      }, newUuid())

      // FIX-002 / BUG-014: 记账成功短震
      await bridge.hapticFeedback('success')
      onSuccess()
    } catch (err) {
      // FIX-002 / BUG-014: 记账失败长震
      await bridge.hapticFeedback('error')
      const msg = err instanceof Error ? err.message : '未知错误'
      setSubmitError(msg)
    } finally {
      setSubmitting(false)
    }
  }, [amount, categoryId, type, note, bizDate, bridge, getBookId, selectedBookId, selectedAccountId, includeInBudget, submitting, onSuccess])

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

              {/* NEW-E5 fix: 校验失败反馈 */}
              {batchError && !batchSubmitting && (
                <div
                  className="mx-4 mt-2 text-center text-xs py-1.5 rounded-lg"
                  style={{ color: 'var(--expense)', background: 'var(--glass)' }}
                  data-testid="batch-validation-error"
                >
                  ❌ {batchError}
                </div>
              )}
            </>
          ) : (
            <>
              {/* 收支切换 */}
              <div className="flex gap-2 px-4 mb-3">
                <button
                  onClick={() => { setType('expense'); setCategoryId(''); setCatPage(0) }}
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
                  onClick={() => { setType('income'); setCategoryId(''); setCatPage(0) }}
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

              {/* 第八轮 REQ-9：账本 / 账户 / 预算 chip 行（T12-6：抽入 EntryChipRow，openPicker 态组件内持有） */}
              <div className="px-4 mb-3">
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

              {/* 分类选择（T12-6：抽入 CategoryPager；REQ-3 每页 10 格 2×5 等高，>10 类横滑翻页 + 指示点） */}
              <div className="px-4 mb-3">
                <CategoryPager
                  categories={filteredCats}
                  selectedId={categoryId}
                  onSelect={setCategoryId}
                  page={catPage}
                  onPageChange={setCatPage}
                />
              </div>

              {/* T7-3（REQ-2 / 决策 #5）：日期左、添加凭证右同行分布；有凭证/草稿时凭证块整块占行 */}
              <div className="px-4 mb-3">
                <div className="flex items-center gap-2">
                  <DateField
                    value={bizDate}
                    onChange={v => setBizDate(v)}
                    testId="entry-date"
                    className="shrink-0"
                  />
                  {!(image.uploading || image.attachment || (image.error && image.error !== 'cancelled')) &&
                    !(draft && !draft.parseError && !draft.proposal) && (
                    <button
                      onClick={() => setShowImagePicker(true)}
                      disabled={!bridge.isAvailable()}
                      className="ml-auto flex items-center gap-2 py-2 px-3 rounded-lg text-xs transition-colors disabled:opacity-40"
                      style={{ background: 'var(--glass)', color: 'var(--ink2)', border: '1px solid var(--glass-border)' }}
                      data-testid="attach-image-btn"
                    >
                      <span>📎</span>
                      <span>添加凭证</span>
                    </button>
                  )}
                </div>
                {image.uploading || image.attachment || (image.error && image.error !== 'cancelled') ? (
                  <div className="mt-2">
                    <ImageUploadView
                      uploading={image.uploading}
                      progress={image.progress}
                      attachment={image.attachment}
                      error={image.error}
                      onRetry={image.retry}
                      onClear={handleImageClear}
                      onPickAgain={handleImagePickAgain}
                    />
                  </div>
                ) : draft && !draft.parseError && !draft.proposal ? (
                  /* R-M4-47 fix: 草稿恢复 UI（仅上传失败时显示） */
                  <div
                    className="mt-2 rounded-xl p-3"
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
                ) : null}
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
                        : parseError === 'no_categories' ? '未找到匹配的分类'
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

              {/* 错误提示 */}
              {submitError && (
                <div
                  className="mx-4 mb-3 text-center text-xs py-1.5 rounded-lg"
                  style={{ color: 'var(--expense)', background: 'var(--glass)' }}
                  data-testid="submit-error"
                >
                   {submitError}
                </div>
              )}

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
