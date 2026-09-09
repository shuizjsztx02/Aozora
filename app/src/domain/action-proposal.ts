/**
 * ActionProposal 契约 — TypeScript 类型与确定性校验器。
 *
 * 依据 APP-CONTEXT.md §6.3 ActionProposal 最小契约。
 * 与 contracts/action-proposal/schema.json 一一对应。
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
