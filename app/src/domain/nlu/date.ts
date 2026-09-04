/**
 * 日期解析器 — 从中文文本中识别相对/绝对日期并转换为 bizDate (YYYY-MM-DD)。
 *
 * 支持格式：
 * - 今天 / 今日 → todayBizDate
 * - 昨天 / 昨日 → today - 1
 * - 明天 / 明日 → today + 1
 * - 前天 / 后天 → today -/+ 2
 * - 周X / 星期X → 最近的该星期几（过去 7 天内）
 * - 这周X / 本周X → 本周的该星期几
 * - 上周X → 上周的该星期几
 * - X月X日 / X月X号 → 绝对日期（当年或上一年）
 * - X月 → 当月 1 日
 * - 月初 / 月底 → 当月 1 日 / 当月最后一天
 *
 * 默认：未识别日期时返回 null（由 parser 层决定是否用 todayBizDate）。
 *
 * 约束：
 * - 纯函数，不依赖存储
 * - 使用 localBizDate 和 bizDateOfMonth 推导，避免 toISOString
 * - 使用 ctx.now 推导当前月份，不使用宿主墙钟（R-M1-35 修复）
 */

import { localBizDate, bizDateOfMonth, parseBizDate } from '../date'

export interface DateMatch {
  /** 业务日期 YYYY-MM-DD */
  bizDate: string
  /** 原文匹配位置 [start, end] */
  range: [number, number]
  /** 原文字符串 */
  raw: string
  /** 置信度 */
  confidence: number
}

// 星期关键词 → JS 星期几（0=周日, 1=周一, ..., 6=周六）
const WEEKDAY_MAP: Readonly<Record<string, number>> = {
  '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '日': 0, '天': 0,
}

/**
 * 从文本中提取日期。
 *
 * @param text 用户输入文本
 * @param now 当前 UTC 时间戳（ms），用于推导相对日期
 * @param timezone 用户时区（IANA）
 */
export function extractDate(text: string, now: number, timezone: string): DateMatch | null {
  if (!text || typeof text !== 'string') {
    return null
  }

  const trimmed = text.trim()
  if (!trimmed) return null

  const today = localBizDate(now, timezone)
  const todayParsed = parseBizDate(today)

  // 按优先级尝试不同模式

  // 模式 1: 今天 / 今日 / 昨天 / 明天 / 前天 / 后天
  const relativeResult = tryRelativeDay(trimmed, todayParsed)
  if (relativeResult) return relativeResult

  // 模式 2: 周X / 这周X / 上周X
  const weekdayResult = tryWeekday(trimmed, todayParsed)
  if (weekdayResult) return weekdayResult

  // 模式 3: X月X日 / X月X号
  const absoluteResult = tryAbsoluteDate(trimmed, todayParsed.year, todayParsed.month)
  if (absoluteResult) return absoluteResult

  // 模式 4: X月（当月 1 日）
  const monthResult = tryMonthOnly(trimmed, todayParsed.year, todayParsed.month)
  if (monthResult) return monthResult

  return null
}

function tryRelativeDay(text: string, todayParsed: { year: number; month: number; day: number }): DateMatch | null {
  const patterns: Array<{ keyword: string; offset: number; confidence: number }> = [
    { keyword: '今天', offset: 0, confidence: 0.95 },
    { keyword: '今日', offset: 0, confidence: 0.95 },
    { keyword: '昨天', offset: -1, confidence: 0.95 },
    { keyword: '昨日', offset: -1, confidence: 0.95 },
    { keyword: '明天', offset: 1, confidence: 0.95 },
    { keyword: '明日', offset: 1, confidence: 0.95 },
    { keyword: '前天', offset: -2, confidence: 0.95 },
    { keyword: '后天', offset: 2, confidence: 0.95 },
  ]

  for (const { keyword, offset, confidence } of patterns) {
    const idx = text.indexOf(keyword)
    if (idx !== -1) {
      const target = new Date(todayParsed.year, todayParsed.month - 1, todayParsed.day + offset)
      const bizDate = bizDateOfMonth(target.getFullYear(), target.getMonth() + 1, target.getDate())
      return {
        bizDate,
        range: [idx, idx + keyword.length],
        raw: keyword,
        confidence,
      }
    }
  }

  return null
}

function tryWeekday(text: string, todayParsed: { year: number; month: number; day: number }): DateMatch | null {
  // 匹配: (上|这|本)?(周|星期)(一|二|...|日|天)
  // 例：周一、上周三、本周四、星期日
  const regex = /(上|这|本)?(周|星期)([一二三四五六日天])/
  const match = text.match(regex)
  if (!match || match.index === undefined) return null

  const prefix = match[1] // '上' | '这' | '本' | undefined
  const dayChar = match[3]
  const targetWeekday = WEEKDAY_MAP[dayChar]
  if (targetWeekday === undefined) return null

  const todayDate = new Date(todayParsed.year, todayParsed.month - 1, todayParsed.day)
  const currentWeekday = todayDate.getDay() // 0=Sun, 1=Mon, ...

  let targetDate: Date

  if (prefix === '上') {
    // 上周的该星期几：先回到本周一，再减 7 天得到上周一，再加目标偏移
    const thisMonday = new Date(todayDate)
    thisMonday.setDate(todayDate.getDate() - ((currentWeekday + 6) % 7)) // 本周一
    targetDate = new Date(thisMonday)
    // targetWeekday: 0=Sun, 1=Mon, 2=Tue, ..., 6=Sat
    // 周一偏移 0，周二偏移 1，...，周日偏移 6
    const offsetFromMonday = ((targetWeekday - 1) + 7) % 7
    targetDate.setDate(thisMonday.getDate() - 7 + offsetFromMonday)
  } else {
    // 默认（无 prefix 或 这/本）：最近的该星期几（过去 7 天内）
    // 如果是今天，返回今天；否则返回过去 7 天内最近的
    let diff = targetWeekday - currentWeekday
    if (diff > 0) diff -= 7 // 未来的改成过去的
    targetDate = new Date(todayDate)
    targetDate.setDate(todayDate.getDate() + diff)
  }

  const bizDate = bizDateOfMonth(targetDate.getFullYear(), targetDate.getMonth() + 1, targetDate.getDate())
  return {
    bizDate,
    range: [match.index, match.index + match[0].length],
    raw: match[0],
    confidence: prefix === '上' ? 0.9 : 0.85,
  }
}

function tryAbsoluteDate(text: string, currentYear: number, currentMonth: number): DateMatch | null {
  // X月X日 / X月X号
  const regex = /(\d{1,2})月(\d{1,2})[日号]/
  const match = text.match(regex)
  if (!match || match.index === undefined) return null

  const month = parseInt(match[1], 10)
  const day = parseInt(match[2], 10)

  // 年份：如果月份 > 当前月份（从 ctx.now 推导），视为上一年；否则视为今年
  // 使用 currentMonth 而非 new Date()，避免宿主墙钟问题（R-M1-35）
  const year = month > currentMonth ? currentYear - 1 : currentYear

  try {
    const bizDate = bizDateOfMonth(year, month, day)
    return {
      bizDate,
      range: [match.index, match.index + match[0].length],
      raw: match[0],
      confidence: 0.9,
    }
  } catch {
    return null // 非法日期
  }
}

function tryMonthOnly(text: string, currentYear: number, currentMonth: number): DateMatch | null {
  // X月（当月 1 日）— 仅在文本中没有"日/号"后缀时匹配
  const regex = /(\d{1,2})月(?!\d+[日号])/
  const match = text.match(regex)
  if (!match || match.index === undefined) return null

  const month = parseInt(match[1], 10)
  // 使用 currentMonth 而非 new Date()，避免宿主墙钟问题（R-M1-35）
  const year = month > currentMonth ? currentYear - 1 : currentYear

  try {
    const bizDate = bizDateOfMonth(year, month, 1)
    return {
      bizDate,
      range: [match.index, match.index + match[0].length],
      raw: match[0],
      confidence: 0.7,
    }
  } catch {
    return null
  }
}
