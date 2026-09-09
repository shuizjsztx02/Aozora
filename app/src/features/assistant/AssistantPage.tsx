/**
 * T-M4-02c: 助手页面 — 对话式记账。
 *
 * 输入 → AI/本地 NLU → 记账卡（T9-7 v2 两态）→ 本地写入 → 撤销
 * T9-8（REQ-16）：键盘 IME inset 顶起输入区；输入行 [🎤][输入][🖼️][发送]；
 * 🖼️ 走既有图片链路（image.pick → image.parse → 批量卡 → ai.execute，服务端权威）
 */

import { useState, useRef, useEffect, useCallback } from 'react'
import { PageHeader } from '../../components/Layout'
import { useBridge } from '../../platform/hooks/use-bridge'
import { useVoice } from '../../platform/hooks/use-voice'
import { useAssistant } from './use-assistant'
import { ConfirmationCard } from './ConfirmationCard'
import { BatchConfirmationCard } from '../ledger/BatchConfirmationCard'
import { UndoToast } from './UndoToast'
import type { ActionProposal } from '../../domain/action-proposal'
import type { AccountData, CategoryData } from '../../platform/bridge/bridge-types'
import { pickDefaultAccount, pickEntryBook } from '../../domain/books'

export function AssistantPage() {
  const bridge = useBridge()
  const {
    isRecording: voiceIsRecording,
    partialText: voicePartialText,
    finalText: voiceFinalText,
    error: voiceError,
    startRecording: voiceStartRecording,
    stopRecording: voiceStopRecording,
    reset: voiceReset,
  } = useVoice()
  const [categories, setCategories] = useState<CategoryData[]>([])
  const [bookId, setBookId] = useState<string | null>(null)
  // T9-7（REQ-15）：卡片账户 chip 数据源 + 提案未带账户时的默认兜底 + 行4 账本名
  const [accounts, setAccounts] = useState<AccountData[]>([])
  const [defaultAccountId, setDefaultAccountId] = useState<string | null>(null)
  const [bookName, setBookName] = useState('')
  const [inputText, setInputText] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const prevFinalTextRef = useRef('')

  // 加载分类和账本
  useEffect(() => {
    bridge.listCategories().then(setCategories).catch(() => {})
    // T9-5b（REQ-11 决策 #3）：聊天记账目标 = default_book_id 优先，否则第一个非 master
    // T9-7（REQ-15）：同批取账户列表 + default_account_id，pickDefaultAccount 整形
    // （默认账户已删除/归档时回退首账户，与 QuickEntrySheet 同源逻辑）
    Promise.all([
      bridge.listBooks(),
      bridge.listAccounts(),
      bridge.getMeta(['default_book_id', 'default_account_id']).catch(() => ({}) as Record<string, string | null>),
    ]).then(([books, accountList, meta]) => {
      const entry = pickEntryBook(books, meta['default_book_id'] ?? null)
      if (entry) {
        setBookId(entry.id)
        setBookName(entry.name || '')
      }
      setAccounts(accountList)
      setDefaultAccountId(pickDefaultAccount(accountList, meta['default_account_id'] ?? null)?.id ?? null)
    }).catch(() => {})
  }, [bridge])

  const {
    messages,
    phase,
    activeProposal,
    cardStatus,
    undoRemaining,
    isSlow,
    submitInput,
    editProposal,
    undoExecution,
    appendAssistantMessage,
  } = useAssistant({
    categories,
    bookId,
    defaultAccountId,
    // C-B4 fix: 使用设备时区替代硬编码
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai',
  })

  // ─── T9-8（REQ-16 / 决策 #11）：聊天图片上传——页面级小状态机 ───
  // idle → picking（选图+上传）→ parsing（AI 解析）→ review（批量卡）→ 确认后 idle。
  // 与文本单卡链路互不干扰：activeProposal 各自独立，批量卡期间文本输入不禁用。
  const [imageState, setImageState] = useState<'idle' | 'picking' | 'parsing' | 'review'>('idle')
  const [imageProposal, setImageProposal] = useState<ActionProposal | null>(null)
  const [imageError, setImageError] = useState<string | null>(null)
  const [imageSubmitting, setImageSubmitting] = useState(false)
  // pick 成功但 parse 失败时留存 attachmentId，重试直接续 parse（无需重选图）
  const imageAttachmentRef = useRef<string | null>(null)

  const runImageFlow = useCallback(async (reuseAttachment: string | null) => {
    if (!bookId) {
      setImageError('账本未就绪，无法图片记账')
      return
    }
    setImageError(null)
    try {
      let attId = reuseAttachment
      if (!attId) {
        setImageState('picking')
        const picked = await bridge.pickImage('gallery')
        attId = picked.attachmentId
        imageAttachmentRef.current = attId
      }
      setImageState('parsing')
      const parsed = await bridge.parseImage(attId, bookId)
      const proposal = parsed.proposal as unknown as ActionProposal
      if (!proposal.transactions || proposal.transactions.length === 0) {
        setImageError('未能从图片识别出记账项')
        setImageState('idle')
        return
      }
      setImageProposal(proposal)
      setImageState('review')
    } catch (e) {
      // 用户取消选择器 → 静默回 idle；其余错误可见可重试（决策 #11）
      const code = e instanceof Error && 'code' in e ? (e as { code: string }).code : null
      if (code === 'cancelled') {
        setImageState('idle')
        return
      }
      setImageError(e instanceof Error && e.message ? e.message : '图片处理失败')
      setImageState('idle')
    }
  }, [bridge, bookId])

  const handleImageUpload = useCallback(() => {
    imageAttachmentRef.current = null
    void runImageFlow(null)
  }, [runImageFlow])

  const handleImageRetry = useCallback(() => {
    void runImageFlow(imageAttachmentRef.current)
  }, [runImageFlow])

  const handleImageCancel = useCallback(() => {
    setImageProposal(null)
    setImageState('idle')
    setImageError(null)
    imageAttachmentRef.current = null
  }, [])

  /** 批量确认 → ai.execute（服务端权威，与 QuickEntrySheet 批量链路同路径；
   *  离线/失败错误可见，卡片保留可重试）。 */
  const handleImageConfirm = useCallback(async (edited: ActionProposal) => {
    if (!bookId || imageSubmitting) return
    setImageSubmitting(true)
    setImageError(null)
    try {
      await bridge.executeProposal(
        edited as unknown as Record<string, unknown>,
        bookId,
        'chat_img_' + Date.now(),
      )
      const n = (edited.transactions ?? []).length
      setImageProposal(null)
      setImageState('idle')
      imageAttachmentRef.current = null
      appendAssistantMessage(`已记录 ${n} 笔交易`)
      bridge.triggerSync().catch(() => { /* best-effort，失败下轮同步自愈 */ })
    } catch (e) {
      setImageError(e instanceof Error && e.message ? e.message : '记账失败')
    } finally {
      setImageSubmitting(false)
    }
  }, [bridge, bookId, imageSubmitting, appendAssistantMessage])

  // 自动滚动到底部
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, phase, imageState])

  // T9-8（REQ-16）：MainActivity 注入 --ime-bottom-px 后派发合成 resize →
  // 消息区随键盘收缩滚动到底（决策 #10）
  useEffect(() => {
    const onResize = () => {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // T-M4-04b: 转写文本 → 填入输入框
  useEffect(() => {
    if (voiceFinalText && voiceFinalText !== prevFinalTextRef.current) {
      prevFinalTextRef.current = voiceFinalText
      setInputText(voiceFinalText)
      voiceReset()
    }
  }, [voiceFinalText, voiceReset])

  const handleSubmit = useCallback(async () => {
    if (!inputText.trim() || phase === 'processing' || phase === 'executing') return
    const text = inputText.trim()
    setInputText('')
    await submitInput(text)
  }, [inputText, phase, submitInput])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }, [handleSubmit])

  // T-M4-04b: 语音输入控制
  const handleMicClick = useCallback(async () => {
    if (voiceIsRecording) {
      await voiceStopRecording()
    } else {
      await voiceStartRecording()
    }
  }, [voiceIsRecording, voiceStartRecording, voiceStopRecording])

  return (
    <div
      className="flex flex-col h-screen safe-top"
      style={{
        background: 'var(--bg)',
        // T9-8（REQ-16）：IME 弹起时整体布局收缩、输入区顶到键盘上方
        //（--ime-bottom-px 由 MainActivity 注入，0 时不影响布局）；下缘安全区并入
        paddingBottom: 'calc(env(safe-area-inset-bottom, 0px) + var(--ime-bottom-px, 0px))',
      }}
    >
      <PageHeader back />

      {/* 消息列表 */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-4 py-3"
      >
        {messages.length === 0 && (
          <div className="flex items-center justify-center h-full">
            <div className="text-center">
              <div className="text-4xl mb-3">💬</div>
              <p className="text-sm" style={{ color: 'var(--ink2)' }}>
                输入记账短句，如「午饭 12.5」
              </p>
            </div>
          </div>
        )}

        {messages.map(msg => (
          <div
            key={msg.id}
            className={`flex mb-3 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className="max-w-[80%] px-3 py-2 rounded-xl text-sm"
              style={{
                background: msg.role === 'user'
                  ? 'var(--accent)'
                  : 'var(--glass)',
                color: msg.role === 'user'
                  ? 'white'
                  : 'var(--ink)',
                border: msg.role === 'user'
                  ? 'none'
                  : '1px solid var(--glass-border)',
              }}
            >
              <div>{msg.text}</div>
              {msg.proposalStatus === 'error' && (
                <div className="text-xs mt-1 opacity-70">执行失败</div>
              )}
              {msg.proposalStatus === 'undone' && (
                <div className="text-xs mt-1 opacity-70">已撤销</div>
              )}
            </div>
          </div>
        ))}

        {/* 慢速提示 */}
        {isSlow && phase === 'processing' && (
          <div className="flex justify-start mb-3">
            <div
              className="px-3 py-2 rounded-xl text-xs"
              style={{
                background: 'var(--glass)',
                color: 'var(--ink3)',
                border: '1px solid var(--glass-border)',
              }}
            >
              ⏳ 正在处理中，请稍候…
            </div>
          </div>
        )}

        {/* 处理中动画 */}
        {phase === 'processing' && !isSlow && (
          <div className="flex justify-start mb-3">
            <div
              className="px-3 py-2 rounded-xl text-sm"
              style={{
                background: 'var(--glass)',
                color: 'var(--ink2)',
                border: '1px solid var(--glass-border)',
              }}
            >
              思考中…
            </div>
          </div>
        )}

        {/* T9-7（REQ-15）：记账卡 v2 两态 — 展示态四行布局 + 编辑态控件全集；
            撤销不限 5s 窗，卡片存续期常驻可用（撤销后本卡随 activeProposal 清空卸载，
            消息流转「已撤销」灰态） */}
        {activeProposal && cardStatus && (
          <ConfirmationCard
            key={activeProposal.proposalId}
            proposal={activeProposal}
            categories={categories}
            status={cardStatus}
            onEditCommit={editProposal}
            accounts={accounts}
            bookName={bookName}
            selectedAccountId={
              activeProposal.transactions?.[0]?.accountId ?? defaultAccountId ?? null
            }
            onUndo={undoExecution}
          />
        )}

        {/* T9-8（REQ-16）：图片批量卡——消息流末尾，与文本单卡链路独立共存 */}
        {imageState === 'review' && imageProposal && (
          <BatchConfirmationCard
            proposal={imageProposal}
            categories={categories}
            submitting={imageSubmitting}
            onConfirm={handleImageConfirm}
            onCancel={handleImageCancel}
          />
        )}
      </div>

      {/* 输入区 */}
      <div
        className="px-4 py-3"
        style={{
          background: 'var(--nav-bg)',
          borderTop: '1px solid var(--glass-border)',
        }}
      >
        {/* T-M4-04b: 实时部分转写提示 */}
        {voiceIsRecording && voicePartialText && (
          <div
            className="mb-2 px-3 py-1.5 rounded-lg text-xs truncate"
            style={{
              background: 'var(--glass)',
              color: 'var(--ink2)',
              border: '1px solid var(--glass-border)',
            }}
            data-testid="voice-partial"
          >
            🎤 {voicePartialText}
          </div>
        )}
        {/* T-M4-04b: 语音错误提示 */}
        {voiceError && !voiceIsRecording && (
          <div
            className="mb-2 px-3 py-1.5 rounded-lg text-xs"
            style={{
              background: '#fef2f2',
              color: '#b91c1c',
              border: '1px solid #fecaca',
            }}
            data-testid="voice-error"
          >
            {voiceError.message}
          </div>
        )}
        {/* T9-8: 图片链路进度提示（picking/parsing 期间 🖼️ 禁用） */}
        {(imageState === 'picking' || imageState === 'parsing') && (
          <div
            className="mb-2 px-3 py-1.5 rounded-lg text-xs"
            style={{
              background: 'var(--glass)',
              color: 'var(--ink2)',
              border: '1px solid var(--glass-border)',
            }}
            data-testid="image-status"
          >
            {imageState === 'picking' ? '🖼️ 正在选择并上传图片…' : '🔍 正在解析图片，约需十几秒…'}
          </div>
        )}
        {/* T9-8: 图片链路错误（红条 + 重试，决策 #11） */}
        {imageError && (
          <div
            className="mb-2 px-3 py-1.5 rounded-lg text-xs flex items-center justify-between gap-2"
            style={{
              background: '#fef2f2',
              color: '#b91c1c',
              border: '1px solid #fecaca',
            }}
            data-testid="image-error"
          >
            <span className="truncate">🖼️ {imageError}</span>
            <button
              type="button"
              onClick={handleImageRetry}
              className="shrink-0 underline font-medium"
              data-testid="image-retry"
            >
              重试
            </button>
          </div>
        )}
        {/* T9-8（REQ-16）：输入行重排为 [🎤语音] [输入框] [🖼️图片] [发送] */}
        <div className="flex gap-2 items-end">
          {/* T-M4-04b: 语音输入按钮（左移） */}
          <button
            onClick={handleMicClick}
            disabled={phase === 'processing' || phase === 'executing'}
            className="px-3 py-2 rounded-xl text-sm transition-all disabled:opacity-40"
            style={{
              background: voiceIsRecording ? '#ef4444' : 'var(--glass)',
              color: voiceIsRecording ? 'white' : 'var(--ink2)',
              border: voiceIsRecording
                ? '1px solid #dc2626'
                : '1px solid var(--glass-border)',
              minWidth: '40px',
            }}
            title={voiceIsRecording ? '停止录音' : '语音输入'}
            data-testid="voice-mic-button"
            aria-label={voiceIsRecording ? '停止录音' : '语音输入'}
          >
            {voiceIsRecording ? '⏹' : '🎤'}
          </button>
          <input
            type="text"
            value={inputText}
            onChange={e => setInputText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={voiceIsRecording ? '正在聆听…' : '输入记账短句…'}
            disabled={phase === 'processing' || phase === 'executing'}
            className="flex-1 min-w-0 px-3 py-2 rounded-xl text-sm outline-none"
            style={{
              background: 'var(--glass)',
              color: 'var(--ink)',
              border: '1px solid var(--glass-border)',
            }}
          />
          {/* T9-8: 图片上传按钮（相册选图 → 批量记账卡） */}
          <button
            onClick={handleImageUpload}
            disabled={
              imageState === 'picking' || imageState === 'parsing' || imageState === 'review' ||
              phase === 'processing' || phase === 'executing'
            }
            className="px-3 py-2 rounded-xl text-sm transition-all disabled:opacity-40"
            style={{
              background: 'var(--glass)',
              color: 'var(--ink2)',
              border: '1px solid var(--glass-border)',
              minWidth: '40px',
            }}
            title="从相册选择图片记账"
            data-testid="image-upload-button"
            aria-label="图片记账"
          >
            🖼️
          </button>
          <button
            onClick={handleSubmit}
            disabled={!inputText.trim() || phase === 'processing' || phase === 'executing'}
            className="px-4 py-2 rounded-xl text-sm font-medium text-white transition-opacity disabled:opacity-40"
            style={{ background: 'var(--accent)' }}
          >
            发送
          </button>
        </div>
      </div>

      {/* 撤销 toast */}
      <UndoToast
        remaining={undoRemaining}
        onUndo={undoExecution}
      />
    </div>
  )
}
