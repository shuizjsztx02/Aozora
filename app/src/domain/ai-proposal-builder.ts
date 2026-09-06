/**
 * T-M4-02c: 从 AI Gateway 的 interpret 输出构造 ActionProposal。
 *
 * AI 返回的是扁平的解析结果（type/amountCents/category/note/bizDate/confidence），
 * 本模块负责：
 * 1. 将分类名匹配到本地分类 ID
 * 2. 补全缺失字段（日期默认今天）
 * 3. 构造完整的 ActionProposal
 * 4. 通过 validateProposal 契约闸
 *
 * 约束：
 * - 纯函数，不依赖 React / bridge / 存储
 * - 任何输入都返回合法 ActionProposal，永不抛异常
 */

import type { ActionProposal, TransactionItem } from './action-proposal'
import { validateProposal } from './action-proposal'
import type { CategoryData } from '../platform/bridge/bridge-types'
import { localBizDate } from './date'

/** AI interpret 输出结构（ledger_parse 任务） */
export interface AiParseOutput {
  type: string
  amountCents: number
  category: string
  note: string
  bizDate: string
  confidence: number
}

export interface BuildProposalContext {
  now: number
  timezone: string
  categories: CategoryData[]
  bookId: string
}

let _aiCounter = 0
function nextAiProposalId(): string {
  _aiCounter += 1
  return `ai-nlu-${Date.now()}-${_aiCounter}`
}

/**
 * 从 AI interpret 输出构造 ActionProposal。
 * 永不抛异常；任何输入都返回合法 proposal。
 */
export function buildProposalFromAiOutput(
  output: AiParseOutput,
  ctx: BuildProposalContext,
): ActionProposal {
  const warnings: string[] = []

  // 1. 金额校验
  if (!output.amountCents || output.amountCents < 1) {
    return buildChatFallback(`无法识别金额，请重新描述~`, 0.2)
  }

  // 2. 收支类型
  const txType = output.type === 'income' ? 'income' : 'expense'

  // 3. 分类匹配
  const categoryId = matchCategoryByName(output.category, ctx.categories, txType)
  if (output.category && !categoryId) {
    warnings.push(`未找到分类"${output.category}"，请手动选择`)
  }

  // 4. 日期
  const bizDate = output.bizDate && /^\d{4}-\d{2}-\d{2}$/.test(output.bizDate)
    ? output.bizDate
    : localBizDate(ctx.now, ctx.timezone)

  // 5. 备注
  const note = output.note || output.category || 'AI 记账'

  // 6. 构造 transaction item
  const tx: TransactionItem = {
    type: txType,
    amountCents: output.amountCents,
    bizDate,
    note: note.slice(0, 200),
  }
  if (categoryId) {
    tx.categoryId = categoryId
  }

  // 7. 置信度
  const confidence = Math.max(0.1, Math.min(1.0, output.confidence || 0.5))
  const requiresConfirmation = confidence < 0.9 || warnings.length > 0 || !categoryId

  // 8. 构造 proposal
  const proposal: ActionProposal = {
    proposalId: nextAiProposalId(),
    action: 'add_tx',
    confidence,
    requiresConfirmation,
    transactions: [tx],
    message: buildAiMessage(tx, output.category, categoryId, ctx.categories),
    warnings,
  }

  // 9. 契约闸
  const validation = validateProposal(proposal)
  if (!validation.valid) {
    warnings.push(`校验失败：${validation.errors.map(e => e.message).join(', ')}`)
    return buildChatFallback('AI 解析结果格式有误，请重试', 0.3)
  }

  return proposal
}

function matchCategoryByName(
  name: string,
  categories: CategoryData[],
  txType: string,
): string | null {
  if (!name) return null
  const kind = txType === 'income' ? 'income' : 'expense'
  const filtered = categories.filter(c => c.kind === kind)

  // 精确匹配
  const exact = filtered.find(c => c.name === name)
  if (exact) return exact.id

  // 包含匹配 — C-M1 fix: 按名称长度降序排序，取最长匹配，避免子串顺序依赖
  const sorted = [...filtered].sort((a, b) => b.name.length - a.name.length)
  const partial = sorted.find(c => name.includes(c.name) || c.name.includes(name))
  if (partial) return partial.id

  return null
}

function buildAiMessage(
  tx: TransactionItem,
  categoryName: string,
  categoryId: string | null,
  categories: CategoryData[],
): string {
  const typeText = tx.type === 'expense' ? '支出' : '收入'
  const yuan = (tx.amountCents / 100).toFixed(2)
  const resolvedName = categoryId
    ? categories.find(c => c.id === categoryId)?.name ?? categoryName
    : categoryName
  const catText = resolvedName ? `（${resolvedName}）` : ''
  return `AI 识别${typeText} ¥${yuan}${catText}：${tx.note}`
}

function buildChatFallback(message: string, confidence: number): ActionProposal {
  return {
    proposalId: nextAiProposalId(),
    action: 'chat',
    confidence,
    requiresConfirmation: false,
    message,
    warnings: [],
  }
}
