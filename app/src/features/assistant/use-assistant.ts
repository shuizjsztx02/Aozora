/**
 * T-M4-02c: 助手对话状态机。
 *
 * 第八轮 Bug-6（用户决策：免确认本地记账）：
 *   输入 → 本地 NLU / AI Gateway 路由 →（在线时 best-effort 服务端验证）
 *        → **直接本地 insertTransaction 写入** → 卡片常驻可编辑 → 5s 撤销窗
 *
 * 写入权威从服务端 /ai/execute 移到本地 + pending_mutations 队列上行最终一致，
 * 离线可用，绕开真机未同步时的 "HTTP 404 Book not found"。
 * ai.execute / ai.validate 桥命令保留（图片批量链路 BatchConfirmationCard 仍在用
 * executeProposal），本 hook 不再调用 executeProposal。
 *
 * 降级策略（ADR-003 §6.4）：
 *   - 断网 → 本地 NLU
 *   - AI 超时/503/429 → 本地 NLU
 *   - 本地 NLU 无法解析 → chat 兜底
 */

import { useState, useCallback, useRef, useEffect } from 'react'
import type { ActionProposal } from '../../domain/action-proposal'
import { parseInput, type ParseContext } from '../../domain/nlu/parser'
import { buildProposalFromAiOutput, type AiParseOutput } from '../../domain/ai-proposal-builder'
import { useBridge } from '../../platform/hooks/use-bridge'
import { newUuid } from '../../platform/uuid'
import { todayBizDate } from '../../domain/date'
import type { CategoryData } from '../../platform/bridge/bridge-types'
import type { AiInterpretContext, AiValidateVerdict } from '../../platform/bridge/bridge-types'
import type { Category } from '../../domain/types'

export interface AssistantMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  proposal?: ActionProposal
  proposalStatus?: 'pending' | 'confirming' | 'executing' | 'committed' | 'undone' | 'error'
  transactionIds?: string[]
  verdict?: AiValidateVerdict
  validationErrors?: string[]
}

/** 第八轮：'confirming' 态随免确认改造移除——解析成功后直接本地提交。 */
export type AssistantPhase = 'idle' | 'processing' | 'executing' | 'undo_window'

/** 常驻编辑卡的展示状态（error 态下卡片仍可编辑，改动即重试保存）。 */
export type CardStatus = 'executing' | 'committed' | 'error'

interface UseAssistantOptions {
  categories: CategoryData[]
  bookId: string | null
  timezone: string
  /** T9-7（REQ-15）：默认账户 id（meta.default_account_id 优先，AssistantPage 经
   * pickDefaultAccount 解析后传入）；提案未指定账户时落库兜底用它。 */
  defaultAccountId: string | null
  /** T11-6（REQ-27 / 决策 #7）：用户画像（meta.user_profile 解析后的非空字段集）；
   * 在线 AI 路径拼入 context.profile，本地 NLU 路径忽略。 */
  profile?: Record<string, string> | null
  /** T11-7（REQ-28 / 决策 #7/#8）：当前聊天角色 {name, text}；非空即拼入
   *  context.persona（服务端 render_messages_with_context 反注入前缀后追加 system 尾）。
   *  离线本地 NLU 路径忽略——人设仅在线 AI 网关生效（已知限制）。 */
  persona?: { name: string; text: string } | null
}

interface UseAssistantReturn {
  messages: AssistantMessage[]
  phase: AssistantPhase
  /** 常驻卡数据；解析成功后设置，撤销后清除。 */
  activeProposal: ActionProposal | null
  cardStatus: CardStatus | null
  undoRemaining: number
  isSlow: boolean
  submitInput: (text: string) => Promise<void>
  /** 卡片编辑保存：softDelete 旧笔 + insert 新（编辑 = 替换写）。 */
  editProposal: (edited: ActionProposal) => Promise<void>
  undoExecution: () => Promise<void>
  /** T9-8（REQ-16）：页面级链路（图片批量卡）完成后追加一条助手文本消息。 */
  appendAssistantMessage: (text: string) => void
}

const CONFIDENCE_THRESHOLD = 0.8
const SLOW_THRESHOLD_MS = 12_000
const UNDO_WINDOW_SECONDS = 5

export function useAssistant({
  categories,
  bookId,
  timezone,
  defaultAccountId,
  profile = null,
  persona = null,
}: UseAssistantOptions): UseAssistantReturn {
  const bridge = useBridge()
  const [messages, setMessages] = useState<AssistantMessage[]>([])
  const [phase, setPhase] = useState<AssistantPhase>('idle')
  const [activeProposal, setActiveProposal] = useState<ActionProposal | null>(null)
  const [cardStatus, setCardStatus] = useState<CardStatus | null>(null)
  const [undoRemaining, setUndoRemaining] = useState(0)
  const [isSlow, setIsSlow] = useState(false)
  const undoTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const undoDeadlineRef = useRef<number>(0)
  const slowTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // C-B3 fix: 并发提交互斥
  const isSubmittingRef = useRef(false)
  // 当前卡对应的已写入交易 id（编辑=替换写 / 撤销=软删 都以此为准）
  const activeTxIdsRef = useRef<string[]>([])

  // C-C2 fix: 组件卸载时清理所有 timer
  useEffect(() => {
    return () => {
      if (undoTimerRef.current) clearInterval(undoTimerRef.current)
      if (slowTimerRef.current) clearTimeout(slowTimerRef.current)
    }
  }, [])

  const buildParseContext = useCallback((): ParseContext => {
    const cats: Category[] = categories.map(c => ({
      id: c.id,
      ownerId: '',
      bookId: null,
      parentId: c.parentId,
      kind: c.kind,
      name: c.name,
      archivedAt: null,
      version: c.version,
    }))
    return {
      now: Date.now(),
      timezone,
      categories: cats,
      accounts: [],
    }
  }, [categories, timezone])

  const stopUndoTimer = useCallback(() => {
    if (undoTimerRef.current) {
      clearInterval(undoTimerRef.current)
      undoTimerRef.current = null
    }
    setUndoRemaining(0)
  }, [])

  /**
   * 第八轮：本地写入提案全部交易，返回生成的交易 id。
   * 聊天提案为单笔 add_tx；若将来出现多笔且中途失败，已写入部分不回滚
   * （与 QuickEntrySheet 手动记账语义一致——逐笔入队、失败可见）。
   */
  const writeTransactions = useCallback(async (proposal: ActionProposal): Promise<string[]> => {
    const ids: string[] = []
    const today = todayBizDate(timezone)
    for (const tx of proposal.transactions ?? []) {
      const id = newUuid()
      // 决策 #5（第七轮）对齐：非今日 → occurredAt 取该日本地 12:00
      let occurredAt = Date.now()
      if (tx.bizDate && tx.bizDate !== today) {
        const noon = new Date(tx.bizDate + 'T12:00:00').getTime()
        if (!Number.isNaN(noon)) occurredAt = noon
      }
      await bridge.insertTransaction({
        id,
        bookId: bookId!,
        type: tx.type,
        amountCents: tx.amountCents,
        categoryId: tx.categoryId || '',
        // T9-7（REQ-15）：提案未点名账户（字段缺失）→ 兜底默认账户；
        // 卡片编辑显式选「不使用账户」（accountId=null）→ 尊重 null，不被默认覆盖。
        accountId: tx.accountId === undefined ? defaultAccountId : tx.accountId,
        note: tx.note,
        bizDate: tx.bizDate,
        occurredAt,
        source: 'nlu_ai',
      }, newUuid()) // mutationId 必须为 UUID（服务端 sync 契约）
      ids.push(id)
    }
    return ids
  }, [bridge, bookId, timezone, defaultAccountId])

  const pushBookMissingError = useCallback((proposalId?: string) => {
    const errMsg: AssistantMessage = {
      id: 'msg_' + Date.now() + '_err',
      role: 'assistant',
      text: '❌ 账本未就绪，无法记账。请返回首页重试。',
      proposalStatus: 'error',
    }
    setMessages(prev => {
      const base = proposalId
        ? prev.map(m => m.proposal?.proposalId === proposalId
            ? { ...m, proposalStatus: 'error' as const } : m)
        : prev
      return [...base, errMsg]
    })
    setActiveProposal(null)
    setCardStatus(null)
    setPhase('idle')
  }, [])

  /** 首次本地提交（解析成功后自动触发，无确认按钮）。 */
  const commitProposalInternal = useCallback(async (proposal: ActionProposal) => {
    if (!bookId) { pushBookMissingError(proposal.proposalId); return }
    setPhase('executing')
    setCardStatus('executing')

    try {
      const ids = await writeTransactions(proposal)
      activeTxIdsRef.current = ids
      setMessages(prev => prev.map(m =>
        m.proposal?.proposalId === proposal.proposalId
          ? {
              ...m, proposal,
              proposalStatus: 'committed' as const,
              transactionIds: ids,
              text: '已记录 ' + ids.length + ' 笔交易',
            }
          : m
      ))
      setCardStatus('committed')

      // 5s 撤销窗（沿用现有 timer / UndoToast）
      undoDeadlineRef.current = Date.now() + UNDO_WINDOW_SECONDS * 1000
      setPhase('undo_window')
      setUndoRemaining(UNDO_WINDOW_SECONDS)
      undoTimerRef.current = setInterval(() => {
        const remaining = Math.ceil((undoDeadlineRef.current - Date.now()) / 1000)
        if (remaining <= 0) {
          if (undoTimerRef.current) clearInterval(undoTimerRef.current)
          undoTimerRef.current = null
          setPhase('idle')
          setUndoRemaining(0)
        } else {
          setUndoRemaining(remaining)
        }
      }, 200)

      try { await bridge.triggerSync() } catch { /* ignore */ }
    } catch (err) {
      // 本地写入失败必须可见；卡片保持 error 态可编辑重试
      const errMsg = err instanceof Error ? err.message : '未知错误'
      activeTxIdsRef.current = []
      setMessages(prev => prev.map(m =>
        m.proposal?.proposalId === proposal.proposalId
          ? { ...m, proposalStatus: 'error' as const, text: `❌ 记账失败：${errMsg}` } : m
      ))
      setCardStatus('error')
      setPhase('idle')
    }
  }, [bridge, bookId, pushBookMissingError, writeTransactions])

  const submitInput = useCallback(async (text: string) => {
    if (!text.trim()) return
    // C-B3 fix: 并发提交互斥 — 非 idle 阶段拒绝新提交
    if (isSubmittingRef.current) return
    isSubmittingRef.current = true

    // C-C1 fix: 新提交时清理旧 undo timer，防止状态机竞争
    stopUndoTimer()

    const userMsg: AssistantMessage = {
      id: 'msg_' + Date.now() + '_user',
      role: 'user',
      text: text.trim(),
    }
    setMessages(prev => [...prev, userMsg])
    setPhase('processing')
    setIsSlow(false)

    // 12s 慢速提示
    if (slowTimerRef.current) clearTimeout(slowTimerRef.current)
    slowTimerRef.current = setTimeout(() => setIsSlow(true), SLOW_THRESHOLD_MS)

    // 1. 本地 NLU
    const parseCtx = buildParseContext()
    const localProposal = parseInput(text.trim(), parseCtx)

    const needsAI = localProposal.confidence < CONFIDENCE_THRESHOLD ||
      localProposal.warnings.length > 0 || localProposal.action === 'chat'

    let finalProposal: ActionProposal

    // 2. 尝试 AI
    if (needsAI && isOnline() && bridge.isAvailable()) {
      // T11-6/T11-7（决策 #7）：画像与人设任一非空才拼 context；两者皆缺 → 传 null，
      // 桥侧省略 context 键，服务端行为与旧版一致。本地 NLU 路径不消费。
      const hasProfile = !!profile && Object.keys(profile).length > 0
      const aiContext: AiInterpretContext | null =
        hasProfile || persona
          ? {
              profile: hasProfile ? profile : null,
              persona: persona ? { name: persona.name, text: persona.text } : null,
            }
          : null
      try {
        const aiResult = await bridge.interpretAI(text.trim(), aiContext)
        if (aiResult?.output) {
          const o = aiResult.output as Record<string, unknown>
          const aiOutput: AiParseOutput = {
            type: String(o.type || ''),
            amountCents: Number(o.amountCents) || 0,
            category: String(o.category || ''),
            note: String(o.note || ''),
            bizDate: String(o.bizDate || ''),
            confidence: Number(o.confidence) || 0.5,
          }
          finalProposal = buildProposalFromAiOutput(aiOutput, {
            now: Date.now(),
            timezone,
            categories,
            bookId: bookId || '',
          })
        } else {
          finalProposal = localProposal
        }
      } catch {
        finalProposal = localProposal
      }
    } else {
      finalProposal = localProposal
    }

    // 3. 服务端验证（仅对 AI 生成的交易提案；best-effort，失败不阻断本地写）
    if (finalProposal !== localProposal &&
        (finalProposal.action === 'add_tx' || finalProposal.action === 'add_tx_batch') && bookId) {
      try {
        const validation = await bridge.validateProposal(
          finalProposal as unknown as Record<string, unknown>, bookId
        )
        if (!validation.valid && validation.verdict !== 'rejected_low_confidence') {
          finalProposal = localProposal
        }
      } catch {
        // 验证请求失败 → 保留 AI 结果
      }
    }

    // 4. 清理慢速提示
    if (slowTimerRef.current) {
      clearTimeout(slowTimerRef.current)
      slowTimerRef.current = null
    }
    setIsSlow(false)

    const isTxAction = finalProposal.action === 'add_tx' || finalProposal.action === 'add_tx_batch'
    const assistantMsg: AssistantMessage = {
      id: 'msg_' + Date.now() + '_assistant',
      role: 'assistant',
      text: finalProposal.message,
      proposal: finalProposal,
      proposalStatus: isTxAction ? 'executing' : 'committed',
    }
    setMessages(prev => [...prev, assistantMsg])

    if (isTxAction) {
      // 第八轮 Bug-6：免确认——解析成功直接本地写入，卡片常驻可编辑
      setActiveProposal(finalProposal)
      isSubmittingRef.current = false
      await commitProposalInternal(finalProposal)
      return
    }
    setPhase('idle')
    isSubmittingRef.current = false
  }, [
    bridge, categories, bookId, timezone, profile, persona, buildParseContext,
    commitProposalInternal, stopUndoTimer,
  ])

  /** 卡片编辑保存：替换写（softDelete 旧 + insert 新），不新增 transaction.update。 */
  const editProposal = useCallback(async (edited: ActionProposal) => {
    if (!bookId) { pushBookMissingError(edited.proposalId); return }
    // 编辑即接管：终止未结束的 5s 撤销窗
    stopUndoTimer()
    const oldIds = activeTxIdsRef.current
    setPhase('executing')
    setCardStatus('executing')
    try {
      for (const id of oldIds) {
        await bridge.softDeleteTransaction(id, newUuid())
      }
      const ids = await writeTransactions(edited)
      activeTxIdsRef.current = ids
      setMessages(prev => prev.map(m =>
        m.proposal?.proposalId === edited.proposalId
          ? {
              ...m, proposal: edited,
              proposalStatus: 'committed' as const,
              transactionIds: ids,
              text: '已更新记录',
            }
          : m
      ))
      setCardStatus('committed')
      setPhase('idle')
      try { await bridge.triggerSync() } catch { /* ignore */ }
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : '未知错误'
      setMessages(prev => prev.map(m =>
        m.proposal?.proposalId === edited.proposalId
          ? { ...m, proposalStatus: 'error' as const, text: `❌ 保存失败：${errMsg}` } : m
      ))
      setCardStatus('error')
      setPhase('idle')
    }
  }, [bridge, bookId, pushBookMissingError, stopUndoTimer, writeTransactions])

  const undoExecution = useCallback(async () => {
    stopUndoTimer()
    const ids = activeTxIdsRef.current
    if (ids.length === 0) { setPhase('idle'); return }
    try {
      for (const txId of ids) {
        await bridge.softDeleteTransaction(txId, newUuid())
      }
      setMessages(prev => prev.map(m =>
        m.transactionIds && m.transactionIds.some(id => ids.includes(id))
          ? { ...m, proposalStatus: 'undone' as const, text: '已撤销' } : m
      ))
      activeTxIdsRef.current = []
      setActiveProposal(null)
      setCardStatus(null)
      try { await bridge.triggerSync() } catch { /* ignore */ }
    } catch {
      // C-B1 fix: undo 失败时通知用户，不静默吞掉
      setMessages(prev => prev.map(m =>
        m.transactionIds && m.transactionIds.some(id => ids.includes(id))
          ? { ...m, proposalStatus: 'error' as const, text: '撤销失败，请手动处理' } : m
      ))
    }
    setPhase('idle')
  }, [bridge, stopUndoTimer])

  /** T9-8：图片批量链路等页面级流程完成后落一条普通助手消息（不进状态机）。 */
  const appendAssistantMessage = useCallback((text: string) => {
    setMessages(prev => [...prev, {
      id: 'msg_' + Date.now() + '_appended',
      role: 'assistant' as const,
      text,
      proposalStatus: 'committed' as const,
    }])
  }, [])

  return {
    messages, phase, activeProposal, cardStatus, undoRemaining, isSlow,
    submitInput, editProposal, undoExecution, appendAssistantMessage,
  }
}

function isOnline(): boolean {
  return typeof navigator !== 'undefined' ? navigator.onLine : true
}
