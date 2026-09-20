/**
 * ActionProposal 契约 — TypeScript 类型与确定性校验器。
 *
 * 依据 APP-CONTEXT.md §6.3 ActionProposal 最小契约。
 * 与 contracts/action-proposal/schema.json 一一对应。
 *
 * T-M7-05a 起本文件承载**两条车道**：
 * - v1（本文件上半部，`validateProposal`）：固定 action 集，行为一字未改；
 * - v2（本文件下半部，`validateProposalV2`）：op 注册表 + 通用 payload，
 *   对应 `contracts/action-proposal/schema-v2.json`。
 * 车道判定本身是「`contractVersion` 字段在场与否」——v1 关闭 additionalProperties
 * 且没有该字段，所以 v2 提案不可能被误读成 v1。`validateProposalAny` 是双读入口。
 *
 * 约束：
 * - 校验器为纯函数，不写任何存储
 * - 校验器不构造 MoneyCents branded type
 * - 复用 domain/money.ts 和 domain/date.ts 的校验工具
 */

import { isValid as isValidMoney } from './money'
import { parseBizDate } from './date'
import type { TransactionType } from './types'
import schema from '../../../contracts/action-proposal/schema.json'
import schemaV2 from '../../../contracts/action-proposal/schema-v2.json'

// ─── 类型定义 ───

export type ProposalAction =
  | 'add_tx'
  | 'add_tx_batch'
  | 'query'
  | 'report'
  | 'create_category'
  | 'config'
  | 'chat'

const VALID_ACTIONS: ReadonlySet<string> = new Set<string>([
  'add_tx', 'add_tx_batch', 'query', 'report',
  'create_category', 'config', 'chat',
])

/** 需要 transactions 字段的 action */
const ACTIONS_REQUIRE_TRANSACTIONS: ReadonlySet<string> = new Set<string>([
  'add_tx', 'add_tx_batch',
])

export interface TransactionItem {
  type: TransactionType
  amountCents: number
  categoryId?: string
  categoryPath?: string[]
  /** T9-7（REQ-15）：null = 显式「不使用账户」；undefined = 未指定（写入侧兜底默认账户）。 */
  accountId?: string | null
  bizDate: string
  note: string
}

export interface ActionProposal {
  proposalId: string
  action: ProposalAction
  confidence: number
  requiresConfirmation: boolean
  transactions?: TransactionItem[]
  message: string
  warnings: string[]
  /** 可选元数据（如 source、attachmentId），T-M4-04e 图片记账用 */
  meta?: Record<string, unknown>
}

// ─── 校验结果 ───

export interface ValidationError {
  path: string
  message: string
}

export interface ValidationResult {
  valid: boolean
  errors: ValidationError[]
}

/** 已由当前账本授权服务解析出的可用实体 ID；仅在执行前传入。 */
export interface ProposalValidationContext {
  categoryIds: ReadonlySet<string>
  accountIds: ReadonlySet<string>
}

function err(path: string, message: string): ValidationError {
  return { path, message }
}

// ─── 校验器 ───

/** 校验 ActionProposal（接受 unknown 以覆盖非结构化输入） */
export function validateProposal(input: unknown, context?: ProposalValidationContext): ValidationResult {
  const errors: ValidationError[] = []

  if (input === null || input === undefined || typeof input !== 'object' || Array.isArray(input)) {
    return { valid: false, errors: [err('$', 'proposal must be a non-null object')] }
  }

  const obj = input as Record<string, unknown>
  for (const key of Object.keys(obj)) {
    if (!Object.hasOwn(schema.properties, key)) errors.push(err(key, 'unknown field'))
  }

  // proposalId — S-H1 fix: maxLength 64 与 DB 列对齐
  if (typeof obj.proposalId !== 'string' || obj.proposalId.length === 0) {
    errors.push(err('proposalId', 'required non-empty string'))
  } else if (obj.proposalId.length > 64) {
    errors.push(err('proposalId', 'must be at most 64 characters'))
  }

  // action
  if (typeof obj.action !== 'string' || !VALID_ACTIONS.has(obj.action)) {
    errors.push(err('action', `must be one of: ${[...VALID_ACTIONS].join(', ')}`))
  }

  // confidence
  if (typeof obj.confidence !== 'number' || Number.isNaN(obj.confidence) ||
      obj.confidence < 0 || obj.confidence > 1) {
    errors.push(err('confidence', 'must be a number in [0, 1]'))
  }

  // requiresConfirmation
  if (typeof obj.requiresConfirmation !== 'boolean') {
    errors.push(err('requiresConfirmation', 'required boolean'))
  }

  // message
  if (typeof obj.message !== 'string') {
    errors.push(err('message', 'required string'))
  }

  // warnings
  if (!Array.isArray(obj.warnings) || !obj.warnings.every((w) => typeof w === 'string')) {
    errors.push(err('warnings', 'required array of strings'))
  }

  // transactions — action 一致性
  const action = typeof obj.action === 'string' ? obj.action : null
  const needsTx = action !== null && ACTIONS_REQUIRE_TRANSACTIONS.has(action)

  if (needsTx) {
    if (!Array.isArray(obj.transactions) || obj.transactions.length === 0) {
      errors.push(err('transactions', `required non-empty array for action "${action}"`))
    }
  }

  if (obj.transactions !== undefined) {
    if (!Array.isArray(obj.transactions)) {
      errors.push(err('transactions', 'must be an array when present'))
    } else {
      if (obj.transactions.length === 0 && !needsTx) {
        errors.push(err('transactions', 'must be non-empty when present'))
      }
      obj.transactions.forEach((tx, i) => {
        validateTransactionItem(tx, i, errors, context)
      })
    }
  }

  return { valid: errors.length === 0, errors }
}

function validateTransactionItem(
  input: unknown,
  index: number,
  errors: ValidationError[],
  context?: ProposalValidationContext
): void {
  const prefix = `transactions[${index}]`

  if (input === null || input === undefined || typeof input !== 'object' || Array.isArray(input)) {
    errors.push(err(prefix, 'must be a non-null object'))
    return
  }

  const tx = input as Record<string, unknown>
  for (const key of Object.keys(tx)) {
    if (!Object.hasOwn(schema.definitions.TransactionItem.properties, key)) {
      errors.push(err(`${prefix}.${key}`, 'unknown field'))
    }
  }

  // type
  if (tx.type !== 'expense' && tx.type !== 'income') {
    errors.push(err(`${prefix}.type`, 'must be "expense" or "income"'))
  }

  // amountCents
  if (typeof tx.amountCents !== 'number' || !isValidMoney(tx.amountCents) || tx.amountCents < 1) {
    errors.push(err(`${prefix}.amountCents`, 'must be a positive safe integer (>= 1)'))
  }

  // bizDate
  if (typeof tx.bizDate !== 'string') {
    errors.push(err(`${prefix}.bizDate`, 'required string'))
  } else {
    try {
      parseBizDate(tx.bizDate)
    } catch {
      errors.push(err(`${prefix}.bizDate`, `invalid date "${tx.bizDate}", expected YYYY-MM-DD`))
    }
  }

  // note
  if (typeof tx.note !== 'string') {
    errors.push(err(`${prefix}.note`, 'required string'))
  }

  // optional fields
  if (tx.categoryId !== undefined && (typeof tx.categoryId !== 'string' || tx.categoryId.length === 0)) {
    errors.push(err(`${prefix}.categoryId`, 'must be non-empty string when present'))
  }

  if (tx.categoryPath !== undefined) {
    if (!Array.isArray(tx.categoryPath) || !tx.categoryPath.every((p) => typeof p === 'string')) {
      errors.push(err(`${prefix}.categoryPath`, 'must be array of strings when present'))
    }
  }

  // T9-7：null = 显式「不使用账户」，合法（与 undefined「未指定」区分）
  if (tx.accountId !== undefined && tx.accountId !== null && (typeof tx.accountId !== 'string' || tx.accountId.length === 0)) {
    errors.push(err(`${prefix}.accountId`, 'must be null or non-empty string when present'))
  }

  if (context) {
    if (typeof tx.categoryId !== 'string' || !context.categoryIds.has(tx.categoryId)) {
      errors.push(err(`${prefix}.categoryId`, 'category is unresolved or not authorized'))
    }
    if (typeof tx.accountId === 'string' && !context.accountIds.has(tx.accountId)) {
      errors.push(err(`${prefix}.accountId`, 'account is not authorized'))
    }
  }
}

// ─── v2 车道（T-M7-05a） ───

/** v1 提案没有该字段；v2 提案必须有且等于 2。 */
export const PROPOSAL_CONTRACT_VERSION_V2 = 2

/** v2 op 注册表。**唯一来源是 `schema-v2.json` 的 action 枚举**，此处不另抄一份。 */
export const V2_OPS: ReadonlySet<string> = new Set<string>(schemaV2.properties.action.enum)

export interface ActionProposalV2 {
  contractVersion: typeof PROPOSAL_CONTRACT_VERSION_V2
  proposalId: string
  /** op 注册表成员（全量 CRUD 的规范 id）。v1 动作名不是成员。 */
  action: string
  confidence: number
  requiresConfirmation: boolean
  message: string
  warnings: string[]
  /** op 的参数（含服务端确定性解析出的 id）。字段形状由 op 的工具注册表拥有。 */
  payload: Record<string, unknown>
}

/**
 * 校验 v2 提案——**信封级**：车道字段、op 成员资格、通用字段形状。
 *
 * **不看 `payload` 内部**：payload 的字段形状由 op 自己的工具注册表
 * （`server/src/modules/ai/tools.py` 的 `parameters`）拥有，契约不复制第二份
 * 定义（复制出来的那份必然与工具漂移）。因此**结构通过不是执行许可**——
 * payload 级的授权（分类 / 账户 / 目标是否属于当前用户与账本）由服务端在
 * 组装与执行时施加，客户端无权也不该解释 payload。
 */
export function validateProposalV2(input: unknown): ValidationResult {
  const errors: ValidationError[] = []

  if (input === null || input === undefined || typeof input !== 'object' || Array.isArray(input)) {
    return { valid: false, errors: [err('$', 'proposal must be a non-null object')] }
  }

  const obj = input as Record<string, unknown>
  for (const key of Object.keys(obj)) {
    if (!Object.hasOwn(schemaV2.properties, key)) errors.push(err(key, 'unknown field'))
  }

  // contractVersion —— 车道字段本身也要校验：显式写成 1 的「v2 提案」不是 v2 提案
  if (obj.contractVersion !== PROPOSAL_CONTRACT_VERSION_V2) {
    errors.push(err('contractVersion', 'must be 2'))
  }

  // proposalId — 与 v1 同规则（上限 64 与 DB 列对齐）
  if (typeof obj.proposalId !== 'string' || obj.proposalId.length === 0) {
    errors.push(err('proposalId', 'required non-empty string'))
  } else if (obj.proposalId.length > 64) {
    errors.push(err('proposalId', 'must be at most 64 characters'))
  }

  // action — op 注册表成员资格
  if (typeof obj.action !== 'string' || !V2_OPS.has(obj.action)) {
    errors.push(err('action', 'must be a v2 op (see contracts/action-proposal/schema-v2.json)'))
  }

  // confidence — 与 v1 同规则
  if (typeof obj.confidence !== 'number' || Number.isNaN(obj.confidence) ||
      obj.confidence < 0 || obj.confidence > 1) {
    errors.push(err('confidence', 'must be a number in [0, 1]'))
  }

  // requiresConfirmation / message / warnings — 与 v1 同规则
  if (typeof obj.requiresConfirmation !== 'boolean') {
    errors.push(err('requiresConfirmation', 'required boolean'))
  }
  if (typeof obj.message !== 'string') {
    errors.push(err('message', 'required string'))
  }
  if (!Array.isArray(obj.warnings) || !obj.warnings.every((w) => typeof w === 'string')) {
    errors.push(err('warnings', 'required array of strings'))
  }

  // payload — 一律必填；无参数 op 写 {}（不设「可省略」特例，规则才不必按 op 分支）
  if (obj.payload === null || obj.payload === undefined ||
      typeof obj.payload !== 'object' || Array.isArray(obj.payload)) {
    errors.push(err('payload', 'required object (use {} for ops without arguments)'))
  }

  return { valid: errors.length === 0, errors }
}

export type ProposalContractVersion = 1 | 2

/**
 * 车道判定（类型守卫）：**有 `contractVersion` 就是 v2 信封**。
 *
 * 与 `validateProposalAny` 用同一把尺子（在场与否）。放在契约层而不是 hook 里，
 * 是因为它只依赖契约本身——任何人拿到一份提案都能判车道，不需要经过 hook。
 */
export function isV2Proposal(p: ActionProposal | ActionProposalV2): p is ActionProposalV2 {
  return (p as { contractVersion?: unknown }).contractVersion === PROPOSAL_CONTRACT_VERSION_V2
}

export interface DualReadResult extends ValidationResult {
  /** 实际走到的车道；输入不是对象时为 null。 */
  contractVersion: ProposalContractVersion | null
}

/**
 * 双读入口：按 `contractVersion` 在场与否选车道。
 *
 * 客户端只会用这一个入口——它可能收到 v1（服务端为一个不认识 v2 的旧客户端
 * 降级产出）也可能收到 v2，两种都必须能判定。`context` 只对 v1 车道有意义
 * （v2 的 payload 授权在服务端，见 `validateProposalV2`）。
 */
export function validateProposalAny(input: unknown, context?: ProposalValidationContext): DualReadResult {
  if (input === null || input === undefined || typeof input !== 'object' || Array.isArray(input)) {
    return { ...validateProposal(input, context), contractVersion: null }
  }

  if (!Object.hasOwn(input as Record<string, unknown>, 'contractVersion')) {
    return { ...validateProposal(input, context), contractVersion: 1 }
  }
  return { ...validateProposalV2(input), contractVersion: 2 }
}
