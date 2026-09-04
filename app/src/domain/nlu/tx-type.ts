/**
 * 收支检测器 — 从中文文本中识别收入/支出意图。
 *
 * 基于关键词匹配：
 * - 支出关键词：花、买、付、消费、支出、吃、喝、缴、充、打车、购物...
 * - 收入关键词：收入、收到、赚、工资、退款、红包、报销、返利...
 *
 * 默认策略：无明显收入关键词时，默认为支出（统计上更常见）。
 *
 * 置信度：
 * - 1.0: 明确的收入/支出关键词
 * - 0.7: 推测（根据上下文或分类推断）
 * - 0.5: 无关键词，默认支出
 *
 * 约束：
 * - 纯函数，不依赖存储
 * - 不解析金额（由 amount 模块负责）
 */

import type { TransactionType } from '../types'

export interface TxTypeResult {
  type: TransactionType
  confidence: number
  /** 匹配的关键词（null 表示无关键词，默认支出） */
  keyword: string | null
}

// 收入关键词（高置信）
// R-M1-36 修复：移除 "转账"、"收款"，因为这些词可以是双向的
// "转账给房东" 是支出，"收到转账" 是收入，需要结合上下文
const INCOME_KEYWORDS: ReadonlyArray<string> = [
  '收入', '收到', '赚到', '进账', '入账',
  '工资', '薪水', '奖金', '年终奖',
  '退款', '退费', '返还', '报销', '返利',
  '红包', // 红包通常是收入
  '利息', '分红', '稿费', '兼职',
]

// 支出关键词（高置信）
const EXPENSE_KEYWORDS: ReadonlyArray<string> = [
  '花', '花了', '花费', '消费', '支出',
  '买', '买了', '购买',
  '付', '付了', '付款', '支付',
  '缴', '缴了', '缴费', '交费',
  '充', '充了', '充值',
  '交', '交了',
  '打车', '滴滴',
  '吃', '吃了', '喝', '喝了',
  '购物', '逛街',
  '租金', '房租', '水电', '电费', '水费', '燃气',
  '话费', '网费',
]

/**
 * 检测文本的收支类型。
 */
export function detectTxType(text: string): TxTypeResult {
  if (!text || typeof text !== 'string') {
    // 空输入默认支出，低置信
    return { type: 'expense', confidence: 0.3, keyword: null }
  }

  const trimmed = text.trim()
  if (!trimmed) {
    return { type: 'expense', confidence: 0.3, keyword: null }
  }

  // 先检查收入关键词（优先级高，因为收入场景更明确）
  for (const keyword of INCOME_KEYWORDS) {
    if (trimmed.includes(keyword)) {
      return { type: 'income', confidence: 0.95, keyword }
    }
  }

  // 再检查支出关键词
  for (const keyword of EXPENSE_KEYWORDS) {
    if (trimmed.includes(keyword)) {
      return { type: 'expense', confidence: 0.95, keyword }
    }
  }

  // 无关键词：默认支出（中等置信）
  return { type: 'expense', confidence: 0.5, keyword: null }
}

/**
 * 检测文本是否包含明确的收支关键词。
 */
export function hasExplicitTxType(text: string): boolean {
  if (!text || typeof text !== 'string') return false
  const trimmed = text.trim()
  if (!trimmed) return false

  return (
    INCOME_KEYWORDS.some((k) => trimmed.includes(k)) ||
    EXPENSE_KEYWORDS.some((k) => trimmed.includes(k))
  )
}
