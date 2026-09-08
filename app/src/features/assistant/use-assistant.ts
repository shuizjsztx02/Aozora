/**
 * T-M4-02c: 助手对话状态机。
 *
 * 编排流程：
 *   输入 → 本地 NLU / AI Gateway 路由 → 服务端验证 → 确认卡 → 执行 → 撤销窗口
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
import type { CategoryData } from '../../platform/bridge/bridge-types'
import type { AiValidateVerdict } from '../../platform/bridge/bridge-types'
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

export type AssistantPhase = 'idle' | 'processing' | 'confirming' | 'executing' | 'undo_window'

interface UseAssistantOptions {
  categories: CategoryData[]
  bookId: string | null
  timezone: string
}

interface UseAssistantReturn {
  messages: AssistantMessage[]
  phase: AssistantPhase
  pendingProposal: ActionProposal | null
  undoRemaining: number
  isSlow: boolean
  submitInput: (text: string) => Promise<void>
  confirmProposal: () => Promise<void>
  confirmEditedProposal: (edited: ActionProposal) => Promise<void>
  cancelProposal: () => void
  undoExecution: () => Promise<void>
}

const CONFIDENCE_THRESHOLD = 0.8
const SLOW_THRESHOLD_MS = 12_000
const UNDO_WINDOW_SECONDS = 5

export function useAssistant({
  categories,
  bookId,
  timezone,
}: UseAssistantOptions): UseAssistantReturn {
  const bridge = useBridge()
  const [messages, setMessages] = useState<AssistantMessage[]>([])
  const [phase, setPhase] = useState<AssistantPhase>('idle')
  const [pendingProposal, setPendingProposal] = useState<ActionProposal | null>(null)
  const [undoRemaining, setUndoRemaining] = useState(0)
  const [isSlow, setIsSlow] = useState(false)
  const undoTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const undoDeadlineRef = useRef<number>(0)
  const slowTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // C-B3 fix: 并发提交互斥
  const isSubmittingRef = useRef(false)

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

  const submitInput = useCallback(async (text: string) => {
    if (!text.trim()) return
    // C-B3 fix: 并发提交互斥 — 非 idle 阶段拒绝新提交
    if (isSubmittingRef.current) return
    isSubmittingRef.current = true

    // C-C1 fix: 新提交时清理旧 undo timer，防止状态机竞争
    if (undoTimerRef.current) {
      clearInterval(undoTimerRef.current)
      undoTimerRef.current = null
    }
    setUndoRemaining(0)

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
      try {
        const aiResult = await bridge.interpretAI(text.trim())
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

    // 3. 服务端验证（仅对 AI 生成的交易提案）
    if (finalProposal !== localProposal &&
        (finalProposal.action === 'add_tx' || finalProposal.action === 'add_tx_batch') && bookId) {
      try {
        const validation = await bridge.validateProposal(
          finalProposal as unknown as Record<string, unknown>, bookId
        )
        if (!validation.valid && validation.verdict !== 'rejected_low_confidence') {
          finalProposal = localProposal
        } else {
          finalProposal = {
            ...finalProposal,
            requiresConfirmation: validation.verdict === 'rejected_low_confidence' || finalProposal.requiresConfirmation,
          }
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
      proposalStatus: isTxAction ? 'pending' : 'committed',
    }
    setMessages(prev => [...prev, assistantMsg])

    if (isTxAction) {
      setPendingProposal(finalProposal)
      setPhase('confirming')
    } else {
      setPhase('idle')
    }
    isSubmittingRef.current = false
  }, [bridge, categories, bookId, timezone, buildParseContext])

  const executeProposalInternal = useCallback(async (proposal: ActionProposal) => {
    if (!bookId) {
      // bookId 缺失时给用户明确反馈，而非静默 return
      const errMsg: AssistantMessage = {
        id: 'msg_' + Date.now() + '_err',
        role: 'assistant',
        text: '❌ 账本未就绪，无法记账。请返回首页重试。',
        proposalStatus: 'error',
      }
      setMessages(prev => [...prev, errMsg])
      setPhase('idle')
      setPendingProposal(null)
      return
    }
    setPhase('executing')
    setMessages(prev => prev.map(m =>
      m.proposal?.proposalId === proposal.proposalId
        ? { ...m, proposalStatus: 'executing' as const } : m
    ))

    try {
      const result = await bridge.executeProposal(
        proposal as unknown as Record<string, unknown>,
        bookId, 'confirm_' + Date.now()
      )

      // 写入本地 Room
      if (proposal.transactions) {
        for (let i = 0; i < proposal.transactions.length && i < result.transaction_ids.length; i++) {
          const tx = proposal.transactions[i]
          const txId = result.transaction_ids[i]
          try {
            await bridge.insertTransaction({
              id: txId,
              bookId,
              type: tx.type,
              amountCents: tx.amountCents,
              categoryId: tx.categoryId || '',
              accountId: tx.accountId || null,
              note: tx.note,
              bizDate: tx.bizDate,
              occurredAt: Date.now(),
              source: 'nlu_ai',
            }, newUuid()) // mutationId 必须为 UUID（服务端 sync 契约）
          } catch {
            // 本地写入失败不影响主流程
          }
        }
      }

      setMessages(prev => prev.map(m =>
        m.proposal?.proposalId === proposal.proposalId
          ? { ...m, proposalStatus: 'committed' as const,
              transactionIds: result.transaction_ids,
              text: '已记录 ' + result.transaction_ids.length + ' 笔交易' }
          : m
      ))

      const windowSeconds = result.undo_window_seconds || UNDO_WINDOW_SECONDS
      undoDeadlineRef.current = Date.now() + windowSeconds * 1000
      setPhase('undo_window')
      setUndoRemaining(windowSeconds)

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
      setPendingProposal(null)

      // 触发同步
      try { await bridge.triggerSync() } catch { /* ignore */ }
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : '未知错误'
      setMessages(prev => prev.map(m =>
        m.proposal?.proposalId === proposal.proposalId
          ? { ...m, proposalStatus: 'error' as const, text: `❌ 记账失败：${errMsg}` } : m
      ))
      setPhase('idle')
      setPendingProposal(null)
    }
  }, [bridge, bookId])

  const confirmProposal = useCallback(async () => {
    if (!pendingProposal) return
    await executeProposalInternal(pendingProposal)
  }, [pendingProposal, executeProposalInternal])

  const confirmEditedProposal = useCallback(async (edited: ActionProposal) => {
    await executeProposalInternal(edited)
  }, [executeProposalInternal])

  const cancelProposal = useCallback(() => {
    setPendingProposal(null)
    setPhase('idle')
    setMessages(prev => prev.map(m =>
      m.proposal?.proposalId === pendingProposal?.proposalId
        ? { ...m, proposalStatus: 'undone' as const, text: '已取消' } : m
    ))
  }, [pendingProposal])

  const undoExecution = useCallback(async () => {
    if (undoTimerRef.current) {
      clearInterval(undoTimerRef.current)
      undoTimerRef.current = null
    }
    const lastExecuted = [...messages].reverse().find(
      m => m.role === 'assistant' && m.transactionIds && m.transactionIds.length > 0
    )
    if (!lastExecuted?.transactionIds) { setPhase('idle'); return }

    try {
      for (const txId of lastExecuted.transactionIds) {
        await bridge.softDeleteTransaction(txId, newUuid())
      }
      setMessages(prev => prev.map(m =>
        m.proposal?.proposalId === lastExecuted.proposal?.proposalId
          ? { ...m, proposalStatus: 'undone' as const, text: '已撤销' } : m
      ))
      try { await bridge.triggerSync() } catch { /* ignore */ }
    } catch {
      // C-B1 fix: undo 失败时通知用户，不静默吞掉
      setMessages(prev => prev.map(m =>
        m.proposal?.proposalId === lastExecuted.proposal?.proposalId
          ? { ...m, proposalStatus: 'error' as const, text: '撤销失败，请手动处理' } : m
      ))
    }
    setPhase('idle')
    setUndoRemaining(0)
  }, [bridge, messages])

  return {
    messages, phase, pendingProposal, undoRemaining, isSlow,
    submitInput, confirmProposal, confirmEditedProposal, cancelProposal, undoExecution,
  }
}

function isOnline(): boolean {
  return typeof navigator !== 'undefined' ? navigator.onLine : true
}
