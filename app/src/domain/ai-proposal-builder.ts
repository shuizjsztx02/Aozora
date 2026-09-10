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
  AccountData, BookData, CategoryData, SavingsGoalData,
  TransactionData, TransactionInput,
} from '../platform/bridge/bridge-types'
import { localBizDate, parseBizDate } from './date'
import { fromYuan } from './money'

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

export type AiIntent = 'bookkeeping' | 'edit' | 'delete' | 'query' | 'chat' | 'config'

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

/** T12-11：edit/delete/query/chat 的文本意图；T13-5：intent=config 时的设置操作
 *  （op 不在白名单/坏形状 → null，调用方回落 chat 转述 reply，决策 #6）。 */
export interface AiInterpretV2 {
  intent: AiIntent
  /** 老 6 字段（intent=bookkeeping 时喂 buildProposalFromAiOutput） */
  legacy: AiParseOutput
  reply: string
  filter: AiOpFilter | null
  set: AiOpSet | null
  config: AiConfig | null
}

/** 批量操作上限 v1（REQ-37 ⑤）：单操作最多执行 50 笔，预览卡提示截断。 */
export const MAX_BATCH_OPS = 50
/** OpPreviewCard 预览行数上限（REQ-37 ③）。 */
export const OP_PREVIEW_ROWS = 20
/** context.categories 客户端侧上限（服务端另有独立兜底截断）。 */
export const AI_CATEGORY_MAX_COUNT = 120
export const AI_CATEGORY_NAME_MAX_CHARS = 16

const AI_INTENTS: ReadonlySet<string> = new Set<AiIntent>([
  'bookkeeping', 'edit', 'delete', 'query', 'chat', 'config',
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
  return {
    intent, legacy, reply,
    filter: parseOpFilter(raw.filter), set: parseOpSet(raw.set),
    config: parseAiConfig(raw.config),
  }
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

// ─── 第十三轮 T13-5（REQ-48 / 决策 #6 #7 #8）：config 意图 —— 白名单解析 + 名→id 解析 + 预览组装 ───
//
// 服务端 T13-4（LEDGER_PARSE v3）输出顶层 config 对象（6 op 白名单；模型只报名、
// 永不报 UUID）。本段纯函数负责：① 白名单校验（缺 config/坏 op/必填缺失 → null，
// 调用方回落 chat 转述 reply，决策 #6）；② 名→id 本地解析（trim + 全等，重名取
// 第一个——list 桥侧已滤归档——匹配不到返回引导语，绝不猜测）；③ 预览 rows 组装
// （OpPreviewCard config 模式消费，决策 #8：确认前用户看到即将所写的全部要点）。

export type AiConfigOp =
  | 'book.create' | 'account.create' | 'budget.create'
  | 'savings.create' | 'default.book_set' | 'default.account_set'

/** config 载荷（按 op 取用；金额类字段单位为「元」，执行时转分）。 */
export interface AiConfig {
  op: AiConfigOp
  /** 6 op 公用对象名（trim 后非空才成立） */
  name: string
  /** book.create：单个表情（可选） */
  icon?: string
  /** account.create：契约必填（income|expense） */
  accountType?: 'income' | 'expense'
  /** account.create：期初余额（元，可选；AccountEntity 无余额列，执行不落库，回执引导补记一笔） */
  initBalance?: number
  /** budget.create：分类名（契约必填，解析 expense 口径分类 id） */
  category?: string
  /** budget.create：金额（元，契约必填） */
  amount?: number
  /** budget.create：month|year，缺省 month（决策 #6） */
  period?: 'month' | 'year'
  /** savings.create：目标金额（元，契约必填） */
  targetAmount?: number
  /** savings.create：存款期限（YYYY-MM-DD；native 只有周/月期限，执行时换算） */
  deadline?: string
}

const AI_CONFIG_OPS: ReadonlySet<string> = new Set<AiConfigOp>([
  'book.create', 'account.create', 'budget.create',
  'savings.create', 'default.book_set', 'default.account_set',
])

/** context.books/accounts/budgets/goals 名单上限（决策 #7：≤30 条 × ≤16 字）。 */
export const AI_OBJECT_MAX_COUNT = 30
export const AI_OBJECT_NAME_MAX_CHARS = 16

/** 金额容错（元）：number 或数字字符串，>0 且有限才收。 */
function toPositiveNum(v: unknown): number | undefined {
  const n =
    typeof v === 'number' ? v
      : typeof v === 'string' && v.trim() !== '' ? Number(v)
        : NaN
  if (!Number.isFinite(n) || n <= 0) return undefined
  try {
    if (fromYuan(n) < 1) return undefined
  } catch {
    return undefined
  }
  return n
}

/** initBalance 容错：≥0（期初 0 元合法）。 */
function toNonNegativeNum(v: unknown): number | undefined {
  const n =
    typeof v === 'number' ? v
      : typeof v === 'string' && v.trim() !== '' ? Number(v)
        : NaN
  if (!Number.isFinite(n) || n < 0) return undefined
  return n
}

function trimmedNonEmpty(v: unknown): string | null {
  return typeof v === 'string' && v.trim() ? v.trim() : null
}

/**
 * config 白名单校验（永不抛异常）。op 坏/必填缺失 → null（回落 chat）；
 * 可选字段坏 → 仅丢该字段（与 filter/set 解析同口径）。
 */
export function parseAiConfig(raw: unknown): AiConfig | null {
  if (!isPlainObject(raw)) return null
  const op = trimmedNonEmpty(raw.op)
  if (!op || !AI_CONFIG_OPS.has(op)) return null
  const name = trimmedNonEmpty(raw.name)
  if (!name) return null
  const cfg: AiConfig = { op: op as AiConfigOp, name }
  switch (cfg.op) {
    case 'book.create': {
      const icon = trimmedNonEmpty(raw.icon)
      if (icon) cfg.icon = icon
      return cfg
    }
    case 'account.create': {
      if (raw.accountType !== 'income' && raw.accountType !== 'expense') return null
      cfg.accountType = raw.accountType
      const bal = toNonNegativeNum(raw.initBalance)
      if (bal !== undefined) cfg.initBalance = bal
      return cfg
    }
    case 'budget.create': {
      const category = trimmedNonEmpty(raw.category)
      const amount = toPositiveNum(raw.amount)
      if (!category || amount === undefined) return null
      cfg.category = category
      cfg.amount = amount
      if (raw.period === 'month' || raw.period === 'year') cfg.period = raw.period
      return cfg
    }
    case 'savings.create': {
      const target = toPositiveNum(raw.targetAmount)
      if (target === undefined) return null
      cfg.targetAmount = target
      if (typeof raw.deadline === 'string') {
        try {
          parseBizDate(raw.deadline)
          cfg.deadline = raw.deadline
        } catch {
          // 可选期限形状无效时丢弃，保留其余合法 config（与其他可选字段同口径）。
        }
      }
      return cfg
    }
    // default.book_set / default.account_set：仅 name（必填校验已过）
    default:
      return cfg
  }
}

/** 对象名数组 → context 注入段形状（trim + ≤16 字 + 去重 + ≤30 条；空名丢）。 */
export function buildAiObjectNames(names: string[]): Array<{ name: string }> {
  const out: Array<{ name: string }> = []
  const seen = new Set<string>()
  for (const raw of names) {
    const name = (raw || '').trim().slice(0, AI_OBJECT_NAME_MAX_CHARS)
    if (!name || seen.has(name)) continue
    seen.add(name)
    out.push({ name })
    if (out.length >= AI_OBJECT_MAX_COUNT) break
  }
  return out
}

export interface ConfigResolveContext {
  books: BookData[]
  accounts: AccountData[]
  goals: SavingsGoalData[]
  categories: CategoryData[]
  /** budget.create 落点账本（聊天记账 bookId）；null → 引导语 */
  bookId: string | null
  /** 用户时区今天（YYYY-MM-DD），savings deadline 过期校验用 */
  today: string
}

/** 名→id 解析产物（各 op 只填自己用到的键）。 */
export interface ConfigTargets {
  /** default.book_set → 账本 id */
  bookId?: string
  /** default.account_set → 账户 id */
  accountId?: string
  /** budget.create → 分类 id */
  categoryId?: string
}

export type ConfigResolution =
  | { ok: true; targets: ConfigTargets }
  | { ok: false; message: string }

function findByName<T extends { name: string }>(list: T[], name: string): T[] {
  const n = name.trim()
  return list.filter(o => (o.name || '').trim() === n)
}

/** 引导语里的现有对象名清单（≤10 个，决策 #6「匹配不到 → 引导语不猜测」）。 */
function nameHint(names: string[]): string {
  const shown = names.filter(Boolean).slice(0, 10).join('、')
  return shown ? `现有的：${shown}。` : '目前还没有可选项，请先在设置中创建。'
}

/**
 * config 的名→id 解析 + 本地可执行性校验（纯函数，桥零调用）。
 * create 类：同类重名 → 引导语（重名会让后续 default.* 无法解析）；
 * budget.create：分类解析（与 edit set.category 同口径 matchCategoryByName expense）；
 * default.*：trim+全等、重名取第一个（桥侧 list 已滤归档）；master 账本不可设为默认。
 */
export function resolveConfigTargets(
  cfg: AiConfig, ctx: ConfigResolveContext,
): ConfigResolution {
  switch (cfg.op) {
    case 'book.create':
      if (findByName(ctx.books, cfg.name).length > 0) {
        return { ok: false, message: `账本「${cfg.name}」已经存在了，换一个名字或直接用现有的～` }
      }
      return { ok: true, targets: {} }
    case 'account.create':
      if (findByName(ctx.accounts, cfg.name).length > 0) {
        return { ok: false, message: `账户「${cfg.name}」已经存在了，换一个名字或直接用现有的～` }
      }
      return { ok: true, targets: {} }
    case 'budget.create': {
      if (!ctx.bookId) {
        return { ok: false, message: '账本未就绪，无法创建预算。请返回首页切换账本后重试。' }
      }
      const categoryId = matchCategoryByName(cfg.category || '', ctx.categories, 'expense')
      if (!categoryId) {
        return {
          ok: false,
          message: `没找到分类「${cfg.category}」，请在现有分类里选一个，或先到设置·分类里创建～`,
        }
      }
      return { ok: true, targets: { categoryId } }
    }
    case 'savings.create':
      // ISO 日期字符串可比；deadline 今天之前 = 无法倒着存钱（形状合法但语义坏）
      if (cfg.deadline && cfg.deadline < ctx.today) {
        return {
          ok: false,
          message: `存款期限（${cfg.deadline}）已经过了，给我一个未来的日期再试～`,
        }
      }
      return { ok: true, targets: {} }
    case 'default.book_set': {
      // master（总账本）不可记账（REQ-11 决策 #4），不提供为默认
      const hit = findByName(ctx.books, cfg.name).filter(b => !b.isMaster)
      if (!hit.length) {
        return {
          ok: false,
          message: `没找到叫「${cfg.name}」的账本。${nameHint(ctx.books.map(b => b.name))}`,
        }
      }
      return { ok: true, targets: { bookId: hit[0].id } }
    }
    case 'default.account_set': {
      const hit = findByName(ctx.accounts, cfg.name)
      if (!hit.length) {
        return {
          ok: false,
          message: `没找到叫「${cfg.name}」的账户。${nameHint(ctx.accounts.map(a => a.name))}`,
        }
      }
      return { ok: true, targets: { accountId: hit[0].id } }
    }
  }
}

/**
 * savings deadline（YYYY-MM-DD）→ native 期限（periodValue/periodUnit）。
 * 口径：剩余 ≤12 周给「周」（整数感强），更长给「月」；边界钳进 Kotlin 合法域
 * （周 1-521 / 月 1-120）。deadline 过期由 resolveConfigTargets 拦截，此处仅防御。
 */
export function deadlineToSavingsPeriod(
  deadline: string, today: string,
): { value: number; unit: 'week' | 'month' } {
  const days = Math.round(
    (Date.parse(`${deadline}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86_400_000,
  )
  if (days <= 0) return { value: 1, unit: 'week' }
  const weeks = Math.min(521, Math.ceil(days / 7))
  if (days <= 84) return { value: weeks, unit: 'week' }
  return { value: Math.min(120, Math.max(1, Math.round(days / 30.44))), unit: 'month' }
}

const YUAN = (n: number) => `¥${n.toFixed(2)}`

export interface ConfigPreview {
  title: string
  rows: Array<{ label: string; value: string }>
}

/**
 * config 预览卡内容（决策 #8：2-4 行，操作类型/目标名/关键参数）。
 * 只展示会真实写入的效果：budget 的 name 与 account 的 initBalance 无 native 落点，
 * 前者不进 rows（实体无名列），后者明示「创建后补记一笔」。
 */
export function buildConfigPreview(cfg: AiConfig, today: string): ConfigPreview {
  switch (cfg.op) {
    case 'book.create': {
      const rows = [{ label: '操作', value: '新建账本' }, { label: '名称', value: cfg.name }]
      if (cfg.icon) rows.push({ label: '图标', value: cfg.icon })
      return { title: '⚙️ 新建账本', rows }
    }
    case 'account.create': {
      const rows = [
        { label: '操作', value: '新建账户' },
        { label: '名称', value: cfg.name },
        { label: '类型', value: cfg.accountType === 'income' ? '收入账户' : '支出账户' },
      ]
      if (cfg.initBalance) {
        rows.push({ label: '期初余额', value: `${YUAN(cfg.initBalance)}（创建后请补记一笔收入）` })
      }
      return { title: '⚙️ 新建账户', rows }
    }
    case 'budget.create': {
      const yearly = cfg.period === 'year'
      return {
        title: '⚙️ 新建预算',
        rows: [
          { label: '操作', value: '新建预算' },
          { label: '分类', value: cfg.category || '' },
          { label: '金额', value: YUAN(cfg.amount || 0) },
          { label: '周期', value: yearly ? `年度（${today.slice(0, 4)} 年）` : `月度（${today.slice(0, 7)}）` },
        ],
      }
    }
    case 'savings.create': {
      const rows = [
        { label: '操作', value: '新建存钱目标' },
        { label: '名称', value: cfg.name },
        { label: '目标金额', value: YUAN(cfg.targetAmount || 0) },
      ]
      if (cfg.deadline) {
        const p = deadlineToSavingsPeriod(cfg.deadline, today)
        rows.push({
          label: '存款期限',
          value: `${cfg.deadline} 前（约 ${p.value}${p.unit === 'week' ? '周' : '个月'}）`,
        })
      }
      return { title: '⚙️ 新建存钱目标', rows }
    }
    case 'default.book_set':
      return {
        title: '⚙️ 设置默认账本',
        rows: [{ label: '操作', value: '设为默认账本' }, { label: '目标', value: cfg.name }],
      }
    case 'default.account_set':
      return {
        title: '⚙️ 设置默认账户',
        rows: [{ label: '操作', value: '设为默认账户' }, { label: '目标', value: cfg.name }],
      }
  }
}
