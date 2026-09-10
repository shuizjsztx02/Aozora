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
import type {
  CategoryData, TransactionData, TransactionInput,
} from '../platform/bridge/bridge-types'
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

// ─── 第十二轮 T12-11（REQ-37 / 决策 #2 #3）：interpret 输出 v2 解析 + 操作圈定 ───
//
// prompt v2 单 JSON：老 6 字段原样 + intent/reply/filter/set。LLM 永不出
// transaction id（服务端无客户端全量数据，出 id 必幻觉，决策 #3）——edit/delete
// 的目标由 filter 条件在本模块纯函数里于本地 listByBook 结果上圈定。
// 本模块保持纯函数：不触 React / bridge / 存储。

export type AiIntent = 'bookkeeping' | 'edit' | 'delete' | 'query' | 'chat'

/** edit/delete/query 的目标圈定条件（仅显式字段，坏字段解析时已剔除）。 */
export interface AiOpFilter {
  /** YYYY-MM-DD，含端点 */
  dateFrom?: string
  dateTo?: string
  /** 分类名（可能一级/二级，本地匹配名字或父名） */
  category?: string
  /** 备注关键词（大小写不敏感子串） */
  keyword?: string
  amountMinCents?: number
  amountMaxCents?: number
  /** 最近 N 笔（按 bizDate+occurredAt 降序取前 N） */
  recentN?: number
}

/** edit 的新值集（LLM 只回显用户明确要求改的键）。 */
export interface AiOpSet {
  category?: string
  note?: string
  includeInBudget?: boolean
  amountCents?: number
  bizDate?: string
}

export interface AiInterpretV2 {
  intent: AiIntent
  /** 老 6 字段（intent=bookkeeping 时喂 buildProposalFromAiOutput） */
  legacy: AiParseOutput
  reply: string
  filter: AiOpFilter | null
  set: AiOpSet | null
}

/** 批量操作上限 v1（REQ-37 ⑤）：单操作最多执行 50 笔，预览卡提示截断。 */
export const MAX_BATCH_OPS = 50
/** OpPreviewCard 预览行数上限（REQ-37 ③）。 */
export const OP_PREVIEW_ROWS = 20
/** context.categories 客户端侧上限（服务端另有独立兜底截断）。 */
export const AI_CATEGORY_MAX_COUNT = 120
export const AI_CATEGORY_NAME_MAX_CHARS = 16

const AI_INTENTS: ReadonlySet<string> = new Set<AiIntent>([
  'bookkeeping', 'edit', 'delete', 'query', 'chat',
])
const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/

/** 分类名数组组装/截断（AssistantPage → use-assistant → context.categories）。 */
export function buildAiCategoryNames(categories: CategoryData[]): string[] {
  const out: string[] = []
  const seen = new Set<string>()
  for (const c of categories) {
    const name = c.name.trim().slice(0, AI_CATEGORY_NAME_MAX_CHARS)
    if (!name || seen.has(name)) continue
    seen.add(name)
    out.push(name)
    if (out.length >= AI_CATEGORY_MAX_COUNT) break
  }
  return out
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/** 数值容错：LLM 偶尔把整数输出成字符串（"1000"）；非正/非有限/NaN 一律丢。 */
function toPositiveInt(v: unknown): number | undefined {
  const n =
    typeof v === 'number' ? v
      : typeof v === 'string' && v.trim() !== '' ? Number(v)
        : NaN
  if (!Number.isFinite(n) || n < 1) return undefined
  return Math.floor(n)
}

/**
 * interpret 输出 → v2 结构（永不抛异常）。
 * intent 缺失/坏值 → 'bookkeeping'（老服务端/老形状向前兼容，走现链路）。
 */
export function parseAiInterpretOutput(raw: Record<string, unknown>): AiInterpretV2 {
  const intent: AiIntent =
    typeof raw.intent === 'string' && AI_INTENTS.has(raw.intent)
      ? (raw.intent as AiIntent)
      : 'bookkeeping'
  const legacy: AiParseOutput = {
    type: String(raw.type || ''),
    amountCents: Number(raw.amountCents) || 0,
    category: String(raw.category || ''),
    note: String(raw.note || ''),
    bizDate: String(raw.bizDate || ''),
    confidence: Number(raw.confidence) || 0.5,
  }
  const reply = typeof raw.reply === 'string' ? raw.reply.trim() : ''
  return { intent, legacy, reply, filter: parseOpFilter(raw.filter), set: parseOpSet(raw.set) }
}

function parseOpFilter(raw: unknown): AiOpFilter | null {
  if (!isPlainObject(raw)) return null
  const f: AiOpFilter = {}
  if (typeof raw.dateFrom === 'string' && ISO_DATE_RE.test(raw.dateFrom)) f.dateFrom = raw.dateFrom
  if (typeof raw.dateTo === 'string' && ISO_DATE_RE.test(raw.dateTo)) f.dateTo = raw.dateTo
  if (typeof raw.category === 'string' && raw.category.trim()) f.category = raw.category.trim()
  if (typeof raw.keyword === 'string' && raw.keyword.trim()) f.keyword = raw.keyword.trim()
  const min = toPositiveInt(raw.amountMinCents)
  if (min !== undefined) f.amountMinCents = min
  const max = toPositiveInt(raw.amountMaxCents)
  if (max !== undefined) f.amountMaxCents = max
  const n = toPositiveInt(raw.recentN)
  if (n !== undefined) f.recentN = n
  return Object.keys(f).length > 0 ? f : null
}

function parseOpSet(raw: unknown): AiOpSet | null {
  if (!isPlainObject(raw)) return null
  const s: AiOpSet = {}
  if (typeof raw.category === 'string' && raw.category.trim()) s.category = raw.category.trim()
  // note 允许空串 = 显式清空备注（用户说「备注去掉」场景）
  if (typeof raw.note === 'string') s.note = raw.note.trim()
  const amt = toPositiveInt(raw.amountCents)
  if (amt !== undefined) s.amountCents = amt
  if (typeof raw.bizDate === 'string' && ISO_DATE_RE.test(raw.bizDate)) s.bizDate = raw.bizDate
  if (typeof raw.includeInBudget === 'boolean') s.includeInBudget = raw.includeInBudget
  return Object.keys(s).length > 0 ? s : null
}

/** 圈定条件是否为空——edit/delete 全无条件时不可开预览卡（防"一键删全账本"）。 */
export function hasOpConditions(filter: AiOpFilter | null): boolean {
  return !!filter && Object.keys(filter).length > 0
}

/** tx 的分类名（自身 + 父级名，一级/二级都能被 filter.category 命中）。 */
function txCategoryNames(tx: TransactionData, categories: CategoryData[]): string[] {
  const cat = categories.find(c => c.id === tx.categoryId)
  if (!cat) return []
  const names = [cat.name]
  const parent = cat.parentId ? categories.find(c => c.id === cat.parentId) : undefined
  if (parent) names.push(parent.name)
  return names
}

function categoryMatches(needle: string, names: string[]): boolean {
  if (names.includes(needle)) return true
  // 子串双向匹配与 matchCategoryByName 同口径（LLM 给「餐饮」命中二级「早餐」等）
  return names.some(n => needle.includes(n) || n.includes(needle))
}

/**
 * filter 在本地交易集上圈定候选（决策 #3：目标全在客户端算）。
 * 交易集来自 listByBook（已按 bizDate DESC 排序）；recentN 再显式排序兜底。
 */
export function applyOpFilter(
  txs: TransactionData[],
  categories: CategoryData[],
  filter: AiOpFilter,
): TransactionData[] {
  const out = txs.filter(tx => {
    if (filter.dateFrom && tx.bizDate < filter.dateFrom) return false
    if (filter.dateTo && tx.bizDate > filter.dateTo) return false
    if (filter.amountMinCents !== undefined && tx.amountCents < filter.amountMinCents) return false
    if (filter.amountMaxCents !== undefined && tx.amountCents > filter.amountMaxCents) return false
    if (filter.keyword !== undefined &&
        !(tx.note || '').toLowerCase().includes(filter.keyword.toLowerCase())) return false
    if (filter.category !== undefined &&
        !categoryMatches(filter.category, txCategoryNames(tx, categories))) return false
    return true
  })
  if (filter.recentN !== undefined) {
    out.sort((a, b) => b.bizDate.localeCompare(a.bizDate) || b.occurredAt - a.occurredAt)
    return out.slice(0, filter.recentN)
  }
  return out
}

/** edit 汇总口径：圈定集的支出/收入/净额（分）。 */
export function sumByFilter(rows: TransactionData[]): {
  count: number; expenseCents: number; incomeCents: number
} {
  let expenseCents = 0
  let incomeCents = 0
  for (const tx of rows) {
    if (tx.type === 'expense') expenseCents += tx.amountCents
    else incomeCents += tx.amountCents
  }
  return { count: rows.length, expenseCents, incomeCents }
}

/**
 * 单笔 edit 的完整 update payload（updateTransaction 是全字段 update）。
 * 保 id / 保 bookId / 保账户 / 保 source；只覆盖 set 点名且能本地解析的字段。
 * 分类名匹配不到 / 结果缺分类 / 金额坏 → null（调用方记为失败笔，绝不硬写）。
 */
export function buildEditUpdates(
  tx: TransactionData,
  set: AiOpSet | null,
  categories: CategoryData[],
  timezone: string,
  now: number,
): (TransactionInput & { version: number }) | null {
  const amountCents = set?.amountCents ?? tx.amountCents
  if (!Number.isInteger(amountCents) || amountCents < 1) return null
  let categoryId = tx.categoryId
  if (set?.category) {
    const id = matchCategoryByName(set.category, categories, tx.type)
    if (!id) return null
    categoryId = id
  }
  if (!categoryId) return null // Kotlin require 同款守卫：编辑不产生缺分类笔（REQ-36 ④）
  const bizDate = set?.bizDate ?? tx.bizDate
  let occurredAt = tx.occurredAt
  if (bizDate !== tx.bizDate) {
    // 改期重算 occurredAt：新今天=now，否则当日 12:00（决策 #5 / TxEditSheet 同口径）
    const today = localBizDate(now, timezone)
    occurredAt = bizDate === today ? now : new Date(`${bizDate}T12:00:00`).getTime()
    if (Number.isNaN(occurredAt)) return null
  }
  return {
    id: tx.id,
    bookId: tx.bookId,
    type: tx.type,
    amountCents,
    categoryId,
    accountId: tx.accountId ?? null,
    note: set?.note ?? tx.note,
    bizDate,
    occurredAt,
    source: tx.source,
    includeInBudget: set?.includeInBudget ?? tx.includeInBudget !== false,
    version: tx.version + 1, // Kotlin 乐观锁：必须 > 当前版本（T12-7）
  }
}
