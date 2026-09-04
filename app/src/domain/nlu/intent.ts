/**
 * 意图分类器 — 判断用户输入是记账、查询还是闲聊。
 *
 * 优先级：query > record > chat
 * - query 关键词出现即判为查询（统计类问句）
 * - record 在有金额或收支关键词时判定
 * - 其他判为 chat
 *
 * R-M1-34 修复：
 * - 移除裸时间词（本月/今年/去年等）从 QUERY_KEYWORDS，因其在记账场景常见
 * - "花了50元" 等有金额的句子应判为 record 而非 query
 * - 查询意图应有明确的疑问词（多少/统计/报表等）
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

// 查询关键词 — 仅保留明确的查询意图词
// 移除：花了、本月、这个月、今年、去年、上个月（R-M1-34 修复）
const QUERY_KEYWORDS: ReadonlyArray<string> = [
  '多少', '花了多少', '用了多少',
  '统计', '报表', '月报', '周报', '年报',
  '总共', '合计', '结余', '余额',
  '分类', '排行', '排名', '最大', '最多',
  '查询', '查看', '看看',
]

const RECORD_KEYWORDS: ReadonlyArray<string> = [
  '记', '记账', '记录', '入账',
  '花', '买', '付', '消费', '支出',
  '收入', '收到', '赚', '工资', '退款',
  '花了', // 移到 RECORD，因为有金额时是记账意图
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

  // 检查是否有金额（用于辅助判断）
  const hasAmount = /\d+(\.\d+)?\s*(元|块|¥|￥)?/.test(trimmed)

  // 1. 查询优先 — 但如果有金额，更可能是记账
  for (const keyword of QUERY_KEYWORDS) {
    if (trimmed.includes(keyword)) {
      // 如果有明确的查询词但也有金额，需要进一步判断
      // 例如"这个月花了多少"是查询，"本月房租3000"是记账
      // 通过检查是否有"多少"等疑问词来区分
      const isQuestion = /多少|几|怎么/.test(trimmed)
      if (isQuestion || !hasAmount) {
        return { intent: 'query', confidence: 0.9, keyword }
      }
      // 有金额且无疑问词 → 可能是记账，继续检查 record 关键词
      break
    }
  }

  // 2. 记账
  for (const keyword of RECORD_KEYWORDS) {
    if (trimmed.includes(keyword)) {
      return { intent: 'record', confidence: 0.9, keyword }
    }
  }

  // 3. 如果文本中有金额，可能是隐式记账意图（如"午餐35"）
  if (hasAmount) {
    return { intent: 'record', confidence: 0.7, keyword: null }
  }

  // 4. 其他：chat
  return { intent: 'chat', confidence: 0.5, keyword: null }
}
