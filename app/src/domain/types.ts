/**
 * 核心业务实体类型定义。
 *
 * 依据 APP-CONTEXT.md §5.1 实体定义。
 * 金额字段使用 MoneyCents branded type。
 * bizDate 为用户时区 YYYY-MM-DD，occurredAt 为 UTC ms timestamp。
 */

import type { MoneyCents } from './money'

// ─── 枚举类型 ───

export type TransactionType = 'expense' | 'income'
export type BudgetType = 'monthly' | 'annual'
export type Source = 'manual' | 'nlu_local' | 'nlu_ai' | 'import'

// ─── 核心实体 ───

export interface Transaction {
  id: string
  bookId: string
  type: TransactionType
  amountCents: MoneyCents
  categoryId: string
  accountId?: string | null
  note: string
  bizDate: string // YYYY-MM-DD，用户时区
  occurredAt: number // UTC ms timestamp
  source: Source
  version: number
  deletedAt: number | null // UTC ms timestamp or null
}

export interface Book {
  id: string
  ownerId: string
  name: string
  icon: string
  version: number
  archivedAt: number | null
}

export interface Category {
  id: string
  ownerId: string
  bookId?: string | null // null/undefined = 系统分类（服务端 JSON 以显式 null 表达）
  parentId: string | null
  kind: TransactionType
  name: string
  archivedAt: number | null
  version: number
}

export interface Account {
  id: string
  ownerId: string
  name: string
  icon: string
  archivedAt: number | null
  version: number
}

export interface Budget {
  id: string
  bookId: string
  budgetType: BudgetType
  categoryId?: string | null // null/undefined = 整体预算（服务端 JSON 以显式 null 表达）
  period: string // "2026-09" (monthly) or "2026" (annual)
  amountCents: MoneyCents
  version: number
}

export interface User {
  id: string
  status: string
  timezone: string // IANA timezone, e.g. "Asia/Shanghai"
  createdAt: number // UTC ms timestamp
}

// ─── 辅助类型 ───

/** 统计汇总 */
export interface MonthlyStats {
  totalExpense: MoneyCents
  totalIncome: MoneyCents
  balance: MoneyCents // max(0, income - expense)；MoneyCents 禁止负值，口径见 docs/architecture/data-model.md
  count: number
}

/** 分类维度汇总 */
export interface CategoryBreakdown {
  categoryId: string
  amount: MoneyCents
  count: number
}
