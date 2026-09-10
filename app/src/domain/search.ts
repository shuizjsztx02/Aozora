/**
 * T12-5（REQ-33 / 决策 #10）：账单搜索过滤 — 全站唯一实现。
 *
 * 匹配维度：分类名 / 备注 / 金额（"55.00" 与分值 "5500" 两种形态）/
 * 日期（bizDate 原样 "2026-09-03" 与中文 "9月3日"），大小写不敏感。
 * 自 DetailPage 内联实现抽出（口径逐字保留），消费方为 /search 子页。
 */

import type { CategoryData, TransactionData } from '../platform/bridge/bridge-types'
import * as Money from './money'

/** "55.00" — 与明细页展示同口径（无 ¥ 符号） */
function fmtAmount(cents: number): string {
  return Money.formatYuan(Money.cents(cents))
}

/** "9月3日" — bizDate 为 "YYYY-MM-DD" */
function dayLabel(bizDate: string): string {
  const [, m, d] = bizDate.split('-').map(Number)
  return `${m}月${d}日`
}

/** query 为空（含纯空白）时原样返回，调用方以此区分"未搜索"态 */
export function filterTx(
  txs: TransactionData[],
  categories: CategoryData[],
  query: string,
): TransactionData[] {
  const q = query.trim().toLowerCase()
  if (!q) return txs
  return txs.filter(tx => {
    const cat = categories.find(c => c.id === tx.categoryId)
    const haystack = [
      cat?.name ?? '',
      tx.note ?? '',
      fmtAmount(tx.amountCents),
      String(tx.amountCents),
      tx.bizDate,               // 2026-09-03
      dayLabel(tx.bizDate),     // 9月3日
    ].join(' ').toLowerCase()
    return haystack.includes(q)
  })
}
