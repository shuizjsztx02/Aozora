/**
 * T-M4-02c: 助手页面 — 对话式记账。
 *
 * 输入 → AI/本地 NLU → 记账卡（T9-7 v2 两态）→ 本地写入 → 撤销
 * T9-8（REQ-16）：键盘 IME inset 顶起输入区；🖼️ 走既有图片链路（image.pick → image.parse → 批量卡 → ai.execute，服务端权威）
 * T12-1（REQ-29 / 决策 #8 #9）：语音按需授权（点 mic 才请求 RECORD_AUDIO，结果经 usePermissions 状态推送续链）；
 * 语音/图片桥错误中文本地化；输入行重排 [🎤][🖼️][输入][发送]
 * T12-11（REQ-37）：分类名白名单组装（≤120 截断，喂 interpret context.categories）；
 * edit/delete 意图 → OpPreviewCard 预览确认；chat/query → 文本气泡（hook 层分发）
 * T16-2（REQ-58）：带 proposal 的历史消息在气泡下渲染只读账单卡（常驻，无编辑/撤销）
 * T16-4（REQ-60）：消息行改「头像 + 气泡」两列——助手头像 = 当前角色头像，
 * 用户头像 = meta['avatar_uri']（同批顺路读），均走 CategoryIcon、圆形带玻璃外框
 */

import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router'
import { PageHeader } from '../../components/Layout'
import { CategoryIcon } from '../../components/CategoryIcon'
import { useBridge } from '../../platform/hooks/use-bridge'
import { usePermissions } from '../../platform/hooks/use-permissions'
import { useVoice } from '../../platform/hooks/use-voice'
import { useAssistant, type AssistantMessage, type CardStatus } from './use-assistant'
import { StepCardStream } from './StepCardStream'
import { ConversationList } from './ConversationList'
import { useConversations } from './use-conversations'
import { ConfirmationCard } from './ConfirmationCard'
import { OpPreviewCard } from './OpPreviewCard'
import { buildAiCategoryNames } from '../../domain/ai-proposal-builder'
import { BatchConfirmationCard } from '../ledger/BatchConfirmationCard'
import type { ActionProposal } from '../../domain/action-proposal'
import type {
  AccountData, BookData, BudgetData, CategoryData, PersonaData, SavingsGoalData,
} from '../../platform/bridge/bridge-types'
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
  // T14-6（REQ-41 / 决策 #6）：code 缺失/未知且 message 无法反推时的中文兜底，
  // 原生英文 message 一律不透出
  voice_generic: '录音操作未成功，请重试',
}

/**
 * T14-6（REQ-41 / 决策 #6）：语音错误码归一。
 * WebView 边界 code 丢失/未知时按原生 message 子串反推（如
 * "RECORD_AUDIO permission not granted" → permission_denied），
 * 使恢复流（refresh + 「授权」内联钮）与中文文案不依赖 code 完好。
 */
function normalizeVoiceCode(err: { code?: string; message?: string } | null): string {
  if (!err) return ''
  if (err.code && VOICE_ERROR_ZH[err.code]) return err.code
  const m = (err.message ?? '').toLowerCase()
  if (m.includes('permission') || m.includes('not granted') || m.includes('securityexception')) return 'permission_denied'
  if (m.includes('model')) return 'model_not_loaded'
  if (m.includes('already recording')) return 'already_recording'
  return 'voice_generic'
}

// T12-1（REQ-29 / 决策 #9）：图片链路错误码 → 中文文案。503 被 Kotlin 统一映射为
// no_ai_provider（AozoraBridge:1406-1412），按决策给配置指引专属文案；未知码回退原生 message。
// T14-3（REQ-40）：补 execute 分支（handleAiExecute 的码集与 handleImageParse 不同：
// 'network'/'429'/'503'）与服务端 400 ai_missing_category、500 database_error 防御键
const IMAGE_ERROR_ZH: Record<string, string> = {
  no_ai_provider: 'AI 服务未配置：需在服务器 .env 填 AI_PROVIDER_API_KEY 并重启',
  timeout: '图片解析超时，请检查网络后重试',
  network_error: '网络错误，请检查网络连接后重试',
  network: '网络错误，请检查网络后重试',
  rate_limited: 'AI 请求过于频繁，请稍后重试',
  '429': 'AI 请求过于频繁，请稍后重试',
  server_error: 'AI 服务暂时不可用，请稍后重试',
  '503': 'AI 服务暂时不可用，请稍后重试',
  auth_lost: '登录已失效，请重新登录',
  ai_missing_category: '有记录未识别到分类，请在卡片中选择分类后重试',
  database_error: '服务处理失败，请稍后重试',
}

// T14-3（REQ-40 / 决策 #5）：提案行缺分类的确认前拦截文案（与服务端 400 同语义）
const IMG_NO_CATEGORY_MSG = '有记录未识别到分类，请在卡片中选择后再确认'

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

/**
 * T16-2（REQ-58）：历史消息 proposalStatus → 只读账单卡状态。
 * executing/confirming（瞬时态：重启后不存在在途写入）与缺失态一律按「已记录」呈现，
 * 只有终态语义（undone/error/pending）如实透出。
 */
function historyCardStatus(s: AssistantMessage['proposalStatus']): CardStatus | 'undone' {
  if (s === 'undone') return 'undone'
  if (s === 'error') return 'error'
  if (s === 'pending') return 'pending-category'
  return 'committed'
}

/**
 * T16-4（REQ-60）：消息头像——角色/用户统一走 CategoryIcon（emoji/data URI 双形态
 * 已内建），28px 圆形 + 玻璃外框；无头像时 👤 兜底（与 header 角色钮同口径）。
 * 数据源零新增：助手 = activePersona.avatar，用户 = meta['avatar_uri']。
 */
function MessageAvatar({ role, icon, name }: {
  role: 'user' | 'assistant'
  icon?: string | null
  name: string
}) {
  return (
    <span
      className="w-7 h-7 rounded-full overflow-hidden flex items-center justify-center shrink-0 text-[22px]"
      style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
      data-testid={`msg-avatar-${role}`}
    >
      <CategoryIcon icon={icon} name={name} fallback="👤" />
    </span>
  )
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
    refresh: refreshPermissions,
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
  // T13-5（REQ-48）：现有对象名单 context 注入 + config 名→id 解析数据源
  const [budgets, setBudgets] = useState<BudgetData[]>([])
  const [goals, setGoals] = useState<SavingsGoalData[]>([])
  // T11-6（REQ-27）：用户画像（meta.user_profile 解析后），喂给在线 AI context 注入。
  const [profile, setProfile] = useState<Record<string, string> | null>(null)
  // T11-7（REQ-28 / 决策 #8）：聊天角色——header 显示当前角色名 + context.persona 注入；
  // active_persona_id 缺失/失效回退"小贝"→第一条（resolveActivePersona 统一口径）。
  const [activePersona, setActivePersona] = useState<PersonaData | null>(null)
  // T16-4（REQ-60）：用户头像（meta['avatar_uri']，仅本地）——与 display_name 同批顺路读，
  // 零新增桥命令；未设置 → 「👤」兜底
  const [avatarUri, setAvatarUri] = useState<string | null>(null)
  const [inputText, setInputText] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const prevFinalTextRef = useRef('')

  // T12-11（REQ-37 / 决策 #3）：分类名白名单——prompt v2 约束 category/filter/set 从
  // 列表选择（根治「喝酒→未分类」类云端瞎猜）；≤120 个、各 ≤16 字（buildAiCategoryNames）
  const aiCategories = useMemo(() => buildAiCategoryNames(categories), [categories])

  // T13-5（REQ-48）：对象名单装载——挂载时拉；config 执行成功后由
  // useAssistant.onConfigApplied 回调重拉（本轮新建的账本/账户下一轮可被
  // 点名与避重；default.book_set 也经此刷新记账落点 bookId/bookName）。
  const loadObjects = useCallback(() => {
    // T9-5b（REQ-11 决策 #3）：聊天记账目标 = default_book_id 优先，否则第一个非 master
    // T9-7（REQ-15）：同批取账户列表 + default_account_id，pickDefaultAccount 整形
    // （默认账户已删除/归档时回退首账户，与 QuickEntrySheet 同源逻辑）
    Promise.all([
      bridge.listBooks(),
      bridge.listAccounts(),
      // T11-6/T11-7：同批读 user_profile + active_persona_id → 在线 AI 注入画像与角色
      // T16-4（REQ-60）：再加 avatar_uri（用户头像，消息行用户侧渲染用）
      bridge.getMeta(['default_book_id', 'default_account_id', 'user_profile', 'active_persona_id', 'avatar_uri'])
        .catch(() => ({}) as Record<string, string | null>),
      // T11-7（REQ-28）：加载全部角色，按 active_persona_id 解析当前角色
      bridge.listPersonas().catch(() => [] as PersonaData[]),
      // T13-5（REQ-48）：存钱目标名单（savings.create 避重名/解析数据源）
      bridge.listSavingsGoals().catch(() => [] as SavingsGoalData[]),
    ]).then(([books, accountList, meta, personas, goalList]) => {
      const entry = pickEntryBook(books, meta['default_book_id'] ?? null)
      if (entry) {
        setBookId(entry.id)
        setBookName(entry.name || '')
        // budget.create 成功重拉时 bookId 不变、下方 effect 不会触发，随本批补拉
        bridge.listBudgets(entry.id).then(setBudgets).catch(() => {})
      }
      setBooks(books)
      setAccounts(accountList)
      setDefaultAccountId(pickDefaultAccount(accountList, meta['default_account_id'] ?? null)?.id ?? null)
      setProfile(parseProfile(meta['user_profile']))
      setActivePersona(resolveActivePersona(personas, meta['active_persona_id'] ?? null))
      setAvatarUri(meta['avatar_uri'] ?? null)
      setGoals(goalList)
    }).catch(() => {})
  }, [bridge])

  // 加载分类和账本
  useEffect(() => {
    bridge.listCategories().then(setCategories).catch(() => {})
    loadObjects()
  }, [bridge, loadObjects])

  // T13-5（REQ-48）：预算名单随记账账本走（budget.list 按 bookId；bookId 在上方
  // effect 解析后此 effect 自动补拉）。仅作 context 注入段，解析失败 → 空段。
  useEffect(() => {
    if (!bookId) {
      setBudgets([])
      return
    }
    let cancelled = false
    bridge.listBudgets(bookId)
      .then(list => { if (!cancelled) setBudgets(list) })
      .catch(() => { if (!cancelled) setBudgets([]) })
    return () => { cancelled = true }
  }, [bridge, bookId])

  const {
    messages,
    phase,
    activeProposal,
    cardStatus,
    isSlow,
    pendingOp,
    pendingConfig,
    streaming,
    streamProjection,
    submitInput,
    editProposal,
    undoExecution,
    confirmOp,
    cancelOp,
    confirmConfig,
    cancelConfig,
    cancelStream,
    rejectApproval,
    interrupted,
    retryInterrupted,
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
    // T13-5（REQ-48 / 决策 #7）：现有对象全列表 → context 名单段 + config 名→id 解析
    books,
    accounts,
    budgets,
    goals,
    // T13-5：config 写入成功 → 重拉名单（本轮新建对象下一轮可点名）
    onConfigApplied: loadObjects,
    // C-B4 fix: 使用设备时区替代硬编码
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai',
  })

  // T-M7-03d：多会话抽屉——数据面（列表 / 降级 / 删除回退）在 use-conversations。
  // `streaming` 非空即「本轮进行中」：切换与写操作一律禁用（一轮仍绑定在当前会话上，
  // 见 use-conversations 文件头）。
  const [conversationListOpen, setConversationListOpen] = useState(false)
  const conversations = useConversations({
    open: conversationListOpen,
    streaming: streaming !== null,
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
   *  离线/失败错误可见，卡片保留可重试）。
   *  T14-3（REQ-40 / 决策 #5）：服务端「宁缺勿错」——未匹配行不带 categoryId，
   *  提交前任一行缺失即引导卡内手选（不发请求，避免旧链路 500 英文错误）；
   *  catch 与 runImageFlow 同口径走 IMAGE_ERROR_ZH 映射，未知码回退原生 message。 */
  const handleImageConfirm = useCallback(async (edited: ActionProposal) => {
    if (!bookId || imageSubmitting) return
    if ((edited.transactions ?? []).some(tx => !tx.categoryId)) {
      setImageError(IMG_NO_CATEGORY_MSG)
      return
    }
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
      const code = e instanceof Error && 'code' in e ? (e as { code: string }).code : null
      const rawMessage = e instanceof Error && e.message ? e.message : '记账失败'
      setImageError((code ? IMAGE_ERROR_ZH[code] : null) ?? rawMessage)
    } finally {
      setImageSubmitting(false)
    }
  }, [bridge, bookId, imageSubmitting, appendAssistantMessage])

  // 自动滚动到底部
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, phase, imageState, pendingOp, pendingConfig])

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
    // T-M7-03e：语音打断的语义同「取消」（架构 §9.2 交互对照）——开口即打断本轮生成，
    // 再进入录音。与输入区的「停止生成」收敛到同一个状态（§4.4 不变式 3）。
    if (streaming) await cancelStream()
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
    streaming,
    cancelStream,
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

  // T14-6（REQ-41 / 决策 #6）：桥返回 permission_denied = 前端 canRecord 滞后真实权限
  //（系统设置撤销授权后不会推送结果）→ refresh 拉真实态并接入 T12-1 恢复条
  //（「授权」内联钮重发请求 / permanently_denied「去设置」），不再只是红条死路。
  useEffect(() => {
    if (!voiceError || normalizeVoiceCode(voiceError) !== 'permission_denied') return
    let cancelled = false
    void refreshPermissions().then(s => {
      if (cancelled) return
      setMicPermissionHint(
        s.permissions.RECORD_AUDIO === 'permanently_denied' ? 'permanently_denied' : 'denied',
      )
    })
    return () => { cancelled = true }
  }, [voiceError, refreshPermissions])

  return (
    <div
      className="flex flex-col h-screen safe-top"
      style={{
        // T13-1（REQ-45 决策#3）：原 var(--bg) 不透明覆盖删除，transparent 透出全局背景图
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
          <div className="flex items-center gap-2">
            {/* T-M7-03d（架构 §9.2 会话管理）：会话列表抽屉入口 */}
            <button
              type="button"
              onClick={() => setConversationListOpen(true)}
              className="flex items-center justify-center rounded-full shrink-0"
              style={{
                width: 24, height: 24, fontSize: 13,
                color: 'var(--ink2)',
                border: '1px solid var(--glass-border)',
              }}
              data-testid="conversation-list-button"
              aria-label="会话列表"
            >
              ☰
            </button>
            {/* T13-6（REQ-49）：文字钮 → 24px 当前角色头像圆钮（CategoryIcon 现成
                emoji/base64 双形态渲染；无头像/未选角色 → 👤 兜底），点击仍进角色管理。 */}
            <button
              type="button"
              onClick={() => navigate('/settings/personas')}
              className="flex items-center justify-center rounded-full shrink-0"
              style={{
                width: 24, height: 24, fontSize: 15, overflow: 'hidden',
                border: '1px solid var(--accent)',
              }}
              data-testid="persona-manage-button"
              aria-label="角色管理"
            >
              <CategoryIcon
                icon={activePersona?.avatar}
                name={activePersona?.name ?? '角色'}
                fallback="👤"
              />
            </button>
          </div>
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
                {/* T-M7-03d：切到一条服务端会话而本机没有它的消息时，别假装是新会话
                    （正文跨设备同步归 P3 的 04e） */}
                {conversations.activeConversationId
                  ? '这条会话的消息不在这台设备上（跨设备同步将在后续版本支持）'
                  : '输入记账短句，如「午饭 12.5」'}
              </p>
            </div>
          </div>
        )}

        {messages.map(msg => {
          // T16-2（REQ-58）：已记账单卡作为消息常驻——气泡下渲染只读卡（无编辑/撤销，
          // 撤销依赖内存态 activeTxIdsRef，切页/重启后不可用）；当轮那张卡仍由下方
          // activeProposal 链路渲染，同一提案不重复出卡。
          const p = msg.proposal
          const historyProposal = p && (p.transactions?.length ?? 0) > 0 &&
            p.proposalId !== activeProposal?.proposalId ? p : null
          // T16-4（REQ-60）：助手侧左上、用户侧右上（flex-row-reverse 将头像与气泡整体镜像）
          const isUser = msg.role === 'user'
          return (
            <div key={msg.id} className="mb-3">
              <div className={`flex items-start gap-2 ${isUser ? 'flex-row-reverse' : ''}`}>
                <MessageAvatar
                  role={msg.role}
                  icon={isUser ? avatarUri : activePersona?.avatar}
                  name={isUser ? '我' : (activePersona?.name ?? '角色')}
                />
                <div
                  className="max-w-[80%] px-3 py-2 rounded-xl text-sm"
                  style={{
                    background: isUser
                      ? 'var(--accent)'
                      : 'var(--glass)',
                    color: isUser
                      ? 'white'
                      : 'var(--ink)',
                    border: isUser
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
                {historyProposal && (
                  <div className="w-full">
                    <ConfirmationCard
                      readonly
                      proposal={historyProposal}
                      categories={categories}
                      status={historyCardStatus(msg.proposalStatus)}
                      accounts={accounts}
                      books={books}
                      bookId={bookId}
                      bookName={bookName}
                      selectedAccountId={
                        historyProposal.transactions?.[0]?.accountId ?? defaultAccountId ?? null
                      }
                    />
                  </div>
                )}
            </div>
          )
        })}

        {/* T-M7-02i：agent 流式气泡——正文按**追加**渲染（一轮会收到多帧 delta，
            不假设「一轮一帧」）；推理走灰字折叠区（架构 §8.5），绝不混进正文。
            流式期间只出气泡：v2 intent 在流式中无法预知，卡与操作预览等 done 之后
            由 hook 按产物分发（用户感知为「思考完毕展开卡」）。
            T-M7-03c：气泡内自上而下 = 步骤卡片流 → 深度思考 → 正文——
            先让用户看见「在做什么」，再是思考细节，最后是答案；本回合无工具调用时
            StepCardStream 自行不渲染（统一智能模式：简单任务无步骤展示）。 */}
        {streaming && (
          <div className="flex items-start gap-2 mb-3">
            <MessageAvatar
              role="assistant"
              icon={activePersona?.avatar}
              name={activePersona?.name ?? '角色'}
            />
            <div
              className="max-w-[80%] px-3 py-2 rounded-xl text-sm"
              style={{
                background: 'var(--glass)',
                color: 'var(--ink)',
                border: '1px solid var(--glass-border)',
              }}
              data-testid="streaming-bubble"
            >
              {streamProjection && <StepCardStream steps={streamProjection.steps} />}
              {streaming.reasoning && (
                <details className="mb-1">
                  <summary
                    className="text-xs cursor-pointer select-none"
                    style={{ color: 'var(--ink3)' }}
                    data-testid="streaming-reasoning"
                  >
                    {/* T-M7-03c 打磨：折叠区在场即「还在思考」——给一个进行中的说法，
                        否则用户分不清它是「思考完了收起来」还是「正在想」 */}
                    深度思考中…
                  </summary>
                  {/* T-M7-03c 打磨：推理可能很长，限高内滚，不把气泡撑成一屏 */}
                  <div
                    className="text-xs mt-1 whitespace-pre-wrap max-h-40 overflow-y-auto"
                    style={{ color: 'var(--ink3)' }}
                  >
                    {streaming.reasoning}
                  </div>
                </details>
              )}
              <div className="whitespace-pre-wrap">{streaming.text || '思考中…'}</div>
            </div>
          </div>
        )}

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

        {/* 处理中动画（T-M7-02i：流式气泡自带「思考中…」，两者不同时出现） */}
        {phase === 'processing' && !isSlow && !streaming && (
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
            // T-M7-03e：确认门（agent 挂起的提案）的拒绝入口；流式期间动作未启用
            // （卡在 confirm 帧到达时就出现，但 done.proposals 才是权威清单）
            onReject={rejectApproval}
            approvalBusy={streaming !== null}
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

        {/* T12-11（REQ-37 / 决策 #5）：edit/delete 意图预览确认卡——破坏性操作必过确认；
            T15-7（REQ-56 ④）：accounts/books 下传，OpRow 补账户/账本/收支/预算呈现 */}
        {pendingOp && (
          <OpPreviewCard
            op={pendingOp}
            categories={categories}
            accounts={accounts}
            books={books}
            onConfirm={confirmOp}
            onCancel={cancelOp}
          />
        )}

        {/* T13-5（REQ-48 / 决策 #8）：config 设置意图预览确认卡——防呆，确认才写 */}
        {pendingConfig && (
          <OpPreviewCard
            variant="config"
            title={pendingConfig.preview.title}
            rows={pendingConfig.preview.rows}
            onConfirm={confirmConfig}
            onCancel={cancelConfig}
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
        {/* T-M4-04b: 语音错误提示（T12-1：错误码映射中文；T14-6 / 决策 #6：code 缺失
            按 message 反推，英文一律不透出；permission_denied 由下方恢复条承载，
            此处不重复渲染） */}
        {voiceError && !voiceIsRecording && normalizeVoiceCode(voiceError) !== 'permission_denied' && (
          <div
            className="mb-2 px-3 py-1.5 rounded-lg text-xs"
            style={{
              background: '#fef2f2',
              color: '#b91c1c',
              border: '1px solid #fecaca',
            }}
            data-testid="voice-error"
          >
            {VOICE_ERROR_ZH[normalizeVoiceCode(voiceError)]}
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
        {/* T-M7-03e（R-PROD-19）：agent 轮以失败 / 断连收尾 → 明确提示中断状态 + 重连入口。
            与「已取消」是两种说法（那里是用户主动按的停止，这里是被动断的），故不合并。
            P2 的恢复手段是原样重发；按 conversation_id 续跑归 P3 的 04b。 */}
        {interrupted && (
          <div
            className="mb-2 px-3 py-1.5 rounded-lg text-xs flex items-center justify-between gap-2"
            style={{
              background: 'rgba(255, 208, 92, 0.18)',
              color: 'var(--ink)',
              border: '1px solid var(--glass-border)',
            }}
            data-testid="interrupted-banner"
          >
            <span className="truncate">⚠️ 连接中断，这一步没做完</span>
            <button
              type="button"
              onClick={retryInterrupted}
              className="shrink-0 underline font-medium"
              data-testid="interrupted-retry"
            >
              重发
            </button>
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
            /* T-M7-03e：流式期间**可用**——按住它即打断（语义同取消，架构 §9.2）；
             * 执行写库中（executing）与快路径处理中仍禁用（后者没有可打断的流） */
            disabled={phase === 'executing' || (phase === 'processing' && !streaming)}
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
          {/* T-M7-03e：流式期间发送位换成「停止」——`ai.cancel` 的 UI 入口（审批四值的
              `cancelled`：零写、整个任务中止；架构 §4.4 不变式 3 收敛到一致状态）。
              快路径（无 streaming）没有可打断的流，保持既有的 12 s / 25 s 体验。 */}
          {streaming ? (
            <button
              onClick={() => { void cancelStream() }}
              className="px-4 py-2 rounded-xl text-sm font-medium transition-opacity"
              style={{
                background: 'var(--glass)',
                color: 'var(--ink2)',
                border: '1px solid var(--glass-border)',
              }}
              data-testid="stop-generating"
            >
              停止
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={!inputText.trim() || phase === 'processing' || phase === 'executing'}
              className="px-4 py-2 rounded-xl text-sm font-medium text-white transition-opacity disabled:opacity-40"
              style={{ background: 'var(--accent)' }}
            >
              发送
            </button>
          )}
        </div>
      </div>

      {/* T-M7-03d：会话列表抽屉（数据与动作见 use-conversations） */}
      <ConversationList
        open={conversationListOpen}
        onClose={() => setConversationListOpen(false)}
        rows={conversations.rows}
        loading={conversations.loading}
        degraded={conversations.degraded}
        busy={conversations.busy}
        error={conversations.error}
        onCreate={conversations.createConversation}
        onSelect={conversations.selectConversation}
        onRename={(id, title) => { void conversations.renameConversation(id, title) }}
        onDelete={id => { void conversations.deleteConversation(id) }}
        onDismissError={conversations.dismissError}
      />
    </div>
  )
}
