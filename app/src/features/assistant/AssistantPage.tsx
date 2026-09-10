/**
 * T-M4-02c: 助手页面 — 对话式记账。
 *
 * 输入 → AI/本地 NLU → 记账卡（T9-7 v2 两态）→ 本地写入 → 撤销
 * T9-8（REQ-16）：键盘 IME inset 顶起输入区；🖼️ 走既有图片链路（image.pick → image.parse → 批量卡 → ai.execute，服务端权威）
 * T12-1（REQ-29 / 决策 #8 #9）：语音按需授权（点 mic 才请求 RECORD_AUDIO，结果经 usePermissions 状态推送续链）；
 * 语音/图片桥错误中文本地化；输入行重排 [🎤][🖼️][输入][发送]
 * T12-11（REQ-37）：分类名白名单组装（≤120 截断，喂 interpret context.categories）；
 * edit/delete 意图 → OpPreviewCard 预览确认；chat/query → 文本气泡（hook 层分发）
 */

import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router'
import { PageHeader } from '../../components/Layout'
import { useBridge } from '../../platform/hooks/use-bridge'
import { usePermissions } from '../../platform/hooks/use-permissions'
import { useVoice } from '../../platform/hooks/use-voice'
import { useAssistant } from './use-assistant'
import { ConfirmationCard } from './ConfirmationCard'
import { OpPreviewCard } from './OpPreviewCard'
import { buildAiCategoryNames } from '../../domain/ai-proposal-builder'
import { BatchConfirmationCard } from '../ledger/BatchConfirmationCard'
import { UndoToast } from './UndoToast'
import type { ActionProposal } from '../../domain/action-proposal'
import type { AccountData, BookData, CategoryData, PersonaData } from '../../platform/bridge/bridge-types'
import { pickDefaultAccount, pickEntryBook } from '../../domain/books'
import { resolveActivePersona } from '../../domain/personas'

// T12-1（REQ-29 / 决策 #8）：语音桥错误码 → 中文文案（原生 message 为英文，不透出）；
// 未知码回退原生 message。码源：AozoraBridge.handleVoiceStartRecording / VoiceRecognitionService.onError
const VOICE_ERROR_ZH: Record<string, string> = {
  permission_denied: '麦克风权限未授予，请授权后重试',
  model_not_loaded: '语音模型尚未就绪，请稍后重试',
  start_failed: '录音启动失败，请重试',
  audio_init_failed: '录音启动失败，请重试',
  start_error: '录音启动失败，请重试',
  already_recording: '已在录音中',
  not_recording: '当前没有进行中的录音',
  unavailable: '语音服务不可用',
  timeout: '语音操作超时，请重试',
}

// T12-1（REQ-29 / 决策 #9）：图片链路错误码 → 中文文案。503 被 Kotlin 统一映射为
// no_ai_provider（AozoraBridge:1406-1412），按决策给配置指引专属文案；未知码回退原生 message
const IMAGE_ERROR_ZH: Record<string, string> = {
  no_ai_provider: 'AI 服务未配置：需在服务器 .env 填 AI_PROVIDER_API_KEY 并重启',
  timeout: '图片解析超时，请检查网络后重试',
  network_error: '网络错误，请检查网络连接后重试',
  rate_limited: 'AI 请求过于频繁，请稍后重试',
  server_error: 'AI 服务暂时不可用，请稍后重试',
  auth_lost: '登录已失效，请重新登录',
}

/** T11-6（REQ-27 / 决策 #7）：meta.user_profile → 注入管线画像。
 *  只保留非空字符串字段；坏 JSON/非对象/全空 → null（interpretAI 不带 context）。 */
function parseProfile(raw: string | null | undefined): Record<string, string> | null {
  if (!raw) return null
  try {
    const v: unknown = JSON.parse(raw)
    if (typeof v !== 'object' || v === null || Array.isArray(v)) return null
    const out: Record<string, string> = {}
    for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
      if (typeof val === 'string' && val.trim()) out[k] = val.trim()
    }
    return Object.keys(out).length > 0 ? out : null
  } catch {
    return null
  }
}

export function AssistantPage() {
  const bridge = useBridge()
  const navigate = useNavigate()
  const {
    isRecording: voiceIsRecording,
    partialText: voicePartialText,
    finalText: voiceFinalText,
    error: voiceError,
    startRecording: voiceStartRecording,
    stopRecording: voiceStopRecording,
    reset: voiceReset,
  } = useVoice()
  // T12-1（REQ-29 / 决策 #8）：语音按需授权——不在启动时轰炸权限，点 mic 才请求
  const {
    canRecord,
    status: permStatus,
    requestPermission: requestPerm,
    openSettings,
  } = usePermissions()
  const [micPermissionHint, setMicPermissionHint] = useState<'denied' | 'permanently_denied' | null>(null)
  const [pendingPermission, setPendingPermission] = useState(false)
  const [categories, setCategories] = useState<CategoryData[]>([])
  const [bookId, setBookId] = useState<string | null>(null)
  // T9-7（REQ-15）：卡片账户 chip 数据源 + 提案未带账户时的默认兜底 + 行4 账本名
  const [accounts, setAccounts] = useState<AccountData[]>([])
  const [defaultAccountId, setDefaultAccountId] = useState<string | null>(null)
  const [bookName, setBookName] = useState('')
  // T12-9（REQ-35）：卡内 EntryChipRow 账本 chip 数据源
  const [books, setBooks] = useState<BookData[]>([])
  // T11-6（REQ-27）：用户画像（meta.user_profile 解析后），喂给在线 AI context 注入。
  const [profile, setProfile] = useState<Record<string, string> | null>(null)
  // T11-7（REQ-28 / 决策 #8）：聊天角色——header 显示当前角色名 + context.persona 注入；
  // active_persona_id 缺失/失效回退"小贝"→第一条（resolveActivePersona 统一口径）。
  const [activePersona, setActivePersona] = useState<PersonaData | null>(null)
  const [inputText, setInputText] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const prevFinalTextRef = useRef('')

  // T12-11（REQ-37 / 决策 #3）：分类名白名单——prompt v2 约束 category/filter/set 从
  // 列表选择（根治「喝酒→未分类」类云端瞎猜）；≤120 个、各 ≤16 字（buildAiCategoryNames）
  const aiCategories = useMemo(() => buildAiCategoryNames(categories), [categories])

  // 加载分类和账本
  useEffect(() => {
    bridge.listCategories().then(setCategories).catch(() => {})
    // T9-5b（REQ-11 决策 #3）：聊天记账目标 = default_book_id 优先，否则第一个非 master
    // T9-7（REQ-15）：同批取账户列表 + default_account_id，pickDefaultAccount 整形
    // （默认账户已删除/归档时回退首账户，与 QuickEntrySheet 同源逻辑）
    Promise.all([
      bridge.listBooks(),
      bridge.listAccounts(),
      // T11-6/T11-7：同批读 user_profile + active_persona_id → 在线 AI 注入画像与角色
      bridge.getMeta(['default_book_id', 'default_account_id', 'user_profile', 'active_persona_id'])
        .catch(() => ({}) as Record<string, string | null>),
      // T11-7（REQ-28）：加载全部角色，按 active_persona_id 解析当前角色
      bridge.listPersonas().catch(() => [] as PersonaData[]),
    ]).then(([books, accountList, meta, personas]) => {
      const entry = pickEntryBook(books, meta['default_book_id'] ?? null)
      if (entry) {
        setBookId(entry.id)
        setBookName(entry.name || '')
      }
      setBooks(books)
      setAccounts(accountList)
      setDefaultAccountId(pickDefaultAccount(accountList, meta['default_account_id'] ?? null)?.id ?? null)
      setProfile(parseProfile(meta['user_profile']))
      setActivePersona(resolveActivePersona(personas, meta['active_persona_id'] ?? null))
    }).catch(() => {})
  }, [bridge])

  const {
    messages,
    phase,
    activeProposal,
    cardStatus,
    undoRemaining,
    isSlow,
    pendingOp,
    submitInput,
    editProposal,
    undoExecution,
    confirmOp,
    cancelOp,
    appendAssistantMessage,
  } = useAssistant({
    categories,
    bookId,
    defaultAccountId,
    profile,
    // T11-7（REQ-28）：当前角色 → context.persona（离线本地 NLU 不消费，仅在线生效）
    persona: activePersona ? { name: activePersona.name, text: activePersona.persona } : null,
    // T12-11（REQ-37）：分类名白名单 → context.categories（仅在线 AI 路径消费）
    aiCategories,
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
      // 用户取消选择器 → 静默回 idle；其余错误可见可重试（决策 #11）；
      // T12-1（决策 #9）：已知错误码映射中文，no_ai_provider 给配置指引专属文案
      const code = e instanceof Error && 'code' in e ? (e as { code: string }).code : null
      if (code === 'cancelled') {
        setImageState('idle')
        return
      }
      const rawMessage = e instanceof Error && e.message ? e.message : '图片处理失败'
      setImageError((code ? IMAGE_ERROR_ZH[code] : null) ?? rawMessage)
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
  }, [messages, phase, imageState, pendingOp])

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

  // T12-1（REQ-29 / 决策 #8）：语音按需授权——
  // 已授予 → 直接录音；permanently_denied → 引导系统设置；其余 → 弹系统框，
  // 结果经 onPermissionResult 推送 → usePermissions 刷新 status → 下方 effect 续链
  const requestMicPermission = useCallback(() => {
    setMicPermissionHint(null)
    setPendingPermission(true)
    void requestPerm('RECORD_AUDIO')
  }, [requestPerm])

  const handleMicClick = useCallback(async () => {
    if (voiceIsRecording) {
      await voiceStopRecording()
      return
    }
    if (canRecord) {
      setMicPermissionHint(null)
      await voiceStartRecording()
      return
    }
    if (pendingPermission) return // 系统弹框在途，防重入
    if (permStatus.permissions.RECORD_AUDIO === 'permanently_denied') {
      setMicPermissionHint('permanently_denied')
      return
    }
    requestMicPermission()
  }, [
    voiceIsRecording,
    voiceStopRecording,
    canRecord,
    pendingPermission,
    permStatus,
    voiceStartRecording,
    requestMicPermission,
  ])

  // 权限异步结果续链（primitive dep：usePermissions 推送刷新后本 effect 感知变化）
  const recordAudioStatus = permStatus.permissions.RECORD_AUDIO
  useEffect(() => {
    if (!pendingPermission) return
    if (recordAudioStatus === 'granted') {
      setPendingPermission(false)
      setMicPermissionHint(null)
      void voiceStartRecording()
    } else if (recordAudioStatus === 'permanently_denied') {
      setPendingPermission(false)
      setMicPermissionHint('permanently_denied')
    } else if (recordAudioStatus === 'denied') {
      setPendingPermission(false)
      setMicPermissionHint('denied')
    }
  }, [pendingPermission, recordAudioStatus, voiceStartRecording])

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
      {/* T11-7（REQ-28）：header 显示当前角色名 + 右上角色管理入口 → /settings/personas */}
      <PageHeader
        back
        title={activePersona?.name}
        right={
          <button
            type="button"
            onClick={() => navigate('/settings/personas')}
            className="text-sm px-2 py-1 rounded-lg"
            style={{ color: 'var(--accent)', border: '1px solid var(--accent)' }}
            data-testid="persona-manage-button"
            aria-label="角色管理"
          >
            👤 角色
          </button>
        }
      />

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
            books={books}
            bookId={bookId}
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

        {/* T12-11（REQ-37 / 决策 #5）：edit/delete 意图预览确认卡——破坏性操作必过确认 */}
        {pendingOp && (
          <OpPreviewCard
            op={pendingOp}
            categories={categories}
            onConfirm={confirmOp}
            onCancel={cancelOp}
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
        {/* T-M4-04b: 语音错误提示（T12-1：错误码映射中文，未知码回退原生 message） */}
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
            {VOICE_ERROR_ZH[voiceError.code] ?? voiceError.message}
          </div>
        )}
        {/* T12-1（REQ-29 / 决策 #8）：未授权红条——denied 内联「授权」重发请求，
            permanently_denied 内联「去设置」跳系统设置页 */}
        {micPermissionHint && !voiceIsRecording && (
          <div
            className="mb-2 px-3 py-1.5 rounded-lg text-xs flex items-center justify-between gap-2"
            style={{
              background: '#fef2f2',
              color: '#b91c1c',
              border: '1px solid #fecaca',
            }}
            data-testid="voice-permission-bar"
          >
            <span>
              {micPermissionHint === 'permanently_denied'
                ? '麦克风权限已被拒绝，需到系统设置中开启后重试'
                : '未获得麦克风权限，无法语音输入'}
            </span>
            {micPermissionHint === 'permanently_denied' ? (
              <button
                type="button"
                onClick={() => void openSettings()}
                className="shrink-0 underline font-medium"
                data-testid="voice-permission-settings"
              >
                去设置
              </button>
            ) : (
              <button
                type="button"
                onClick={requestMicPermission}
                className="shrink-0 underline font-medium"
                data-testid="voice-permission-grant"
              >
                授权
              </button>
            )}
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
        {/* T12-1（REQ-29 ②）：输入行重排 [🎤语音][🖼️图片][输入框][发送]——图片钮从输入框右移到左、紧挨语音；样式不动 */}
        <div className="flex gap-2 items-end">
          {/* T-M4-04b: 语音输入按钮 */}
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
