/**
 * 意图分类器 — 判断用户输入是记账、查询还是闲聊。
 *
 * 优先级：query > record > chat
 * - query 关键词出现即判为查询（统计类问句）
 * - record 在有金额或收支关键词时判定
 * - 其他判为 chat
 *
 * 约束：
 * - 纯函数，不依赖存储
 */

export type Intent = 'record' | 'query' | 'chat'

export interface IntentResult {
  intent: Intent
  confidence: number
  keyword: string | null
}

const QUERY_KEYWORDS: ReadonlyArray<string> = [
  '多少', '花了多少', '花了', '用了多少',
  '统计', '报表', '月报', '周报', '年报',
  '总共', '合计', '结余', '余额',
  '这个月', '本月', '上个月', '上月', '今年', '去年',
  '分类', '排行', '排名', '最大', '最多',
]

const RECORD_KEYWORDS: ReadonlyArray<string> = [
  '记', '记账', '记录', '入账',
  '花', '买', '付', '消费', '支出',
  '收入', '收到', '赚', '工资', '退款',
]

/**
 * 判断用户输入意图。
 */
export function detectIntent(text: string): IntentResult {
  if (!text || typeof text !== 'string') {
    return { intent: 'chat', confidence: 0.3, keyword: null }
  }

  const trimmed = text.trim()
  if (!trimmed) {
    return { intent: 'chat', confidence: 0.3, keyword: null }
  }

  // 1. 查询优先
  for (const keyword of QUERY_KEYWORDS) {
    if (trimmed.includes(keyword)) {
      return { intent: 'query', confidence: 0.9, keyword }
    }
  }

  // 2. 记账
  for (const keyword of RECORD_KEYWORDS) {
    if (trimmed.includes(keyword)) {
      return { intent: 'record', confidence: 0.9, keyword }
    }
  }

  // 3. 如果文本中有数字，可能是隐式记账意图
  if (/\d/.test(trimmed)) {
    return { intent: 'record', confidence: 0.6, keyword: null }
  }

  // 4. 其他：chat
  return { intent: 'chat', confidence: 0.5, keyword: null }
}
