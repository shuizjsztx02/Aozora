/**
 * 本地规则 NLU 主编排器 — 把自然语言输入转换为 ActionProposal。
 *
 * 职责：
 * 1. 检测意图（record / query / chat）
 * 2. 对 record 意图：抽取金额、收支、分类、日期、备注
 * 3. 构造 ActionProposal（带置信度和确认策略）
 * 4. 契约闸：输出必须通过 validateProposal
 * 5. 降级：无法识别 → chat 兜底
 *
 * 约束：
 * - 纯函数，不依赖 React / Zustand / 存储 / bridge
 * - 任何输入都返回合法 ActionProposal，永不抛异常
 * - 同输入 + 同上下文 = 同输出（幂等，给定 now 固定）
 */

import type { ActionProposal, TransactionItem } from '../action-proposal'
import type { Category, Account } from '../types'
import { validateProposal } from '../action-proposal'
import { extractAmount } from './amount'
import type { AmountMatch } from './amount'
import { detectTxType } from './tx-type'
import { matchCategory } from './category'
import { extractDate } from './date'
import type { DateMatch } from './date'
import { extractNote } from './note'
import { detectIntent } from './intent'
import { todayBizDate } from '../date'

// ─── 上下文 ───

export interface ParseContext {
  /** 当前 UTC 时间戳（ms），生产由调用方注入，测试可固定 */
  now: number
  /** 用户时区（IANA，如 "Asia/Shanghai"） */
  timezone: string
  /** 用户分类列表（只读） */
  categories: Category[]
  /** 用户账户列表（只读，首版仅用于名称匹配） */
  accounts: Account[]
}

// ─── proposalId 生成 ───

let _counter = 0
function nextProposalId(): string {
  _counter += 1
  // 用 counter + 时间戳保证唯一性，但不保证跨进程一致（仅用于 UI 展示）
  return `local-nlu-${Date.now()}-${_counter}`
}

// ─── 主入口 ───

/**
 * 解析用户输入，返回 ActionProposal。
 * 永不抛异常；任何输入都返回合法 proposal。
 */
export function parseInput(text: string, ctx: ParseContext): ActionProposal {
  try {
    return parseInputUnsafe(text, ctx)
  } catch {
    // 任何未预期异常都降级为 chat
    return buildChatProposal('抱歉，我没理解你的意思，能再描述一遍吗？', 0.2)
  }
}

function parseInputUnsafe(text: string, ctx: ParseContext): ActionProposal {
  if (!text || typeof text !== 'string') {
    return buildChatProposal('你想聊点什么呢？', 0.3)
  }

  const trimmed = text.trim()
  if (!trimmed) {
    return buildChatProposal('你想聊点什么呢？', 0.3)
  }

  // 1. 意图检测
  const intentResult = detectIntent(trimmed)

  // 2. 按意图分支
  switch (intentResult.intent) {
    case 'query':
      return buildQueryProposal(trimmed, intentResult)
    case 'record':
      return buildRecordProposal(trimmed, ctx, intentResult)
    case 'chat':
    default:
      return buildChatProposal(`你说的是"${trimmed}"吗？我主要负责记账哦~`, intentResult.confidence)
  }
}

// ─── query 分支 ───

function buildQueryProposal(_text: string, intentResult: { confidence: number; keyword: string | null }): ActionProposal {
  const message = intentResult.keyword
    ? `你想查询关于"${intentResult.keyword}"的统计，请稍后查看报表页面。`
    : `你想查看统计，请前往报表页面。`

  const proposal: ActionProposal = {
    proposalId: nextProposalId(),
    action: 'query',
    confidence: intentResult.confidence,
    requiresConfirmation: false,
    message,
    warnings: ['本地规则暂不支持直接查询，请前往报表页面'],
  }

  // 契约闸
  if (!validateProposal(proposal).valid) {
    return buildChatProposal('查询暂时不可用', 0.2)
  }
  return proposal
}

// ─── record 分支（核心） ───

function buildRecordProposal(text: string, ctx: ParseContext, intentResult: { confidence: number; keyword: string | null }): ActionProposal {
  const warnings: string[] = []
  const stripRanges: Array<[number, number]> = []

  // 1. 抽取金额（首要字段）
  const amountMatch = extractAmount(text)
  if (!amountMatch) {
    // 无金额 → 无法记账，降级为 chat
    return buildChatProposal(`你想记"${text}"吗？请告诉我金额~`, 0.4)
  }
  stripRanges.push(amountMatch.range)

  // 2. 检测收支类型
  const txTypeResult = detectTxType(text)

  // 3. 匹配分类
  const catResult = matchCategory(text, ctx.categories)
  warnings.push(...catResult.warnings)

  // 4. 解析日期（可选，默认今天）
  const dateMatch = extractDate(text, ctx.now, ctx.timezone)
  const bizDate = dateMatch ? dateMatch.bizDate : todayBizDate(ctx.timezone)
  if (dateMatch) {
    stripRanges.push(dateMatch.range)
  }

  // 5. 抽取备注
  const noteResult = extractNote(text, stripRanges, catResult.match?.keyword ?? '')
  const note = noteResult.isEmpty ? (catResult.match?.keyword ?? text.slice(0, 20)) : noteResult.note

  // 6. 构造 transaction item
  const tx: TransactionItem = {
    type: txTypeResult.type,
    amountCents: amountMatch.amount as number,
    bizDate,
    note: note.slice(0, 200), // 限制 note 长度
  }

  if (catResult.match) {
    tx.categoryId = catResult.match.categoryId
  }

  // 7. 计算综合置信度
  const confidence = computeConfidence(amountMatch, txTypeResult, catResult, dateMatch, intentResult)

  // 8. 判断是否需要确认
  const requiresConfirmation = confidence < 0.9 || warnings.length > 0

  // 9. 构造 proposal
  const proposal: ActionProposal = {
    proposalId: nextProposalId(),
    action: 'add_tx',
    confidence,
    requiresConfirmation,
    transactions: [tx],
    message: buildRecordMessage(tx, catResult.match?.categoryName),
    warnings,
  }

  // 10. 契约闸（最后防线）
  const validation = validateProposal(proposal)
  if (!validation.valid) {
    // 校验失败 → 降级为 chat
    warnings.push(`校验失败：${validation.errors.map((e) => e.message).join(', ')}`)
    return buildChatProposal(`记录失败：${text}`, 0.3)
  }

  return proposal
}

// ─── 置信度计算 ───

function computeConfidence(
  amountMatch: AmountMatch | null,
  txTypeResult: { confidence: number },
  catResult: { match: { confidence: number } | null },
  dateMatch: DateMatch | null,
  intentResult: { confidence: number }
): number {
  // 基础：各字段置信度加权
  const scores: number[] = []

  // 金额（最重要）：有即满分，无则 0
  scores.push(amountMatch ? 1.0 : 0.0)

  // 收支类型
  scores.push(txTypeResult.confidence)

  // 分类
  scores.push(catResult.match ? catResult.match.confidence : 0.5)

  // 日期（无日期默认今天，不影响置信度太多）
  scores.push(dateMatch ? dateMatch.confidence : 0.7)

  // 意图
  scores.push(intentResult.confidence)

  // 加权平均
  const weights = [0.4, 0.2, 0.15, 0.1, 0.15]
  let sum = 0
  let weightSum = 0
  for (let i = 0; i < scores.length; i++) {
    sum += scores[i] * weights[i]
    weightSum += weights[i]
  }
  const final = sum / weightSum

  // 边界截断
  return Math.max(0.1, Math.min(1.0, final))
}

// ─── 消息生成 ───

function buildRecordMessage(tx: TransactionItem, categoryName?: string): string {
  const typeText = tx.type === 'expense' ? '支出' : '收入'
  const yuan = (tx.amountCents / 100).toFixed(2)
  const catText = categoryName ? `（${categoryName}）` : ''
  return `已记录${typeText} ¥${yuan}${catText}：${tx.note}`
}

// ─── chat 兜底 ───

function buildChatProposal(message: string, confidence: number): ActionProposal {
  const proposal: ActionProposal = {
    proposalId: nextProposalId(),
    action: 'chat',
    confidence,
    requiresConfirmation: false,
    message,
    warnings: [],
  }
  // 契约闸（防御性）
  if (!validateProposal(proposal).valid) {
    // 最极端的兜底
    return {
      proposalId: nextProposalId(),
      action: 'chat',
      confidence: 0.1,
      requiresConfirmation: false,
      message: '抱歉，我没理解。',
      warnings: ['fallback'],
    }
  }
  return proposal
}

// ─── 测试辅助：重置 ID 计数器（仅测试用） ───

export function _resetProposalCounter(): void {
  _counter = 0
}
