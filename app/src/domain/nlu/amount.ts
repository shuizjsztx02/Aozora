/**
 * 金额提取器 — 从中文文本中提取金额并转换为 MoneyCents。
 *
 * 支持格式（按优先级）：
 * 1. ¥/￥ + 数字（最高置信）
 * 2. 数字 + 元/块 + 可选角/毛
 * 3. 小数（默认元，排除日期上下文）
 * 4. 整数（最低置信，排除年份/日期/计数）
 *
 * 拒绝：
 * - 0 / 负数
 * - 纯日期（YYYY-MM-DD, YYYY年MM月DD日, N月N日）
 * - 年份（4位数字 + 年）
 * - 负号前缀（-50）
 * - 超过安全范围
 *
 * 约束：
 * - 纯函数，不依赖存储
 * - 返回第一个匹配到的金额
 */

import * as Money from '../money'

export interface AmountMatch {
  /** 金额（MoneyCents） */
  amount: Money.MoneyCents
  /** 原文匹配位置 [start, end] */
  range: [number, number]
  /** 原文字符串 */
  raw: string
}

/**
 * 从文本中提取第一个金额。
 * 返回 null 表示未找到金额。
 */
export function extractAmount(text: string): AmountMatch | null {
  if (!text || typeof text !== 'string') {
    return null
  }

  const trimmed = text.trim()
  if (!trimmed) {
    return null
  }

  // 策略：按优先级尝试不同模式，返回第一个匹配

  const currencyResult = tryCurrencyPrefix(trimmed)
  if (currencyResult) return currencyResult

  const chineseResult = tryChineseUnits(trimmed)
  if (chineseResult) return chineseResult

  const decimalResult = tryDecimal(trimmed)
  if (decimalResult) return decimalResult

  const integerResult = tryInteger(trimmed)
  if (integerResult) return integerResult

  return null
}

function tryCurrencyPrefix(text: string): AmountMatch | null {
  // ¥ 或 ￥ 后跟数字；支持英文逗号或中文逗号作为千分位
  const regex = /[¥￥]\s*(\d{1,10}(?:[,，]\d{3})*(?:\.\d{1,2})?)/
  const match = text.match(regex)
  if (!match || match.index === undefined) return null

  const amountStr = match[1].replace(/[,，]/g, '')
  try {
    const amount = Money.fromString(amountStr)
    if (Money.isZero(amount)) return null
    return {
      amount,
      range: [match.index, match.index + match[0].length],
      raw: match[0],
    }
  } catch {
    return null
  }
}

function tryChineseUnits(text: string): AmountMatch | null {
  // 匹配: 数字 + 元/块 + 可选(数字 + 角/毛)
  // 例如: 20元, 15块, 5元3角, 12块5
  const regex = /(\d{1,10}(?:[,，]\d{3})*(?:\.\d{1,2})?)\s*(?:元|块)(\d{1,2})?\s*(?:角|毛)?/
  const match = text.match(regex)
  if (!match || match.index === undefined) return null

  const yuanStr = match[1].replace(/[,，]/g, '')
  const jiaoStr = match[2] // 可选的角/毛数字

  // R-M1-37 修复：角位 >= 10 是非法金额，拒绝而非静默截断
  if (jiaoStr && parseInt(jiaoStr, 10) >= 10) {
    return null
  }

  try {
    let amountStr: string
    if (jiaoStr && jiaoStr !== '0') {
      // 有角：拼接为 "yuan.jiao"（5元3角 → 5.3）
      amountStr = `${yuanStr}.${jiaoStr.padEnd(1, '0')}`
    } else {
      amountStr = yuanStr
    }

    const amount = Money.fromString(amountStr)
    if (Money.isZero(amount)) return null
    return {
      amount,
      range: [match.index, match.index + match[0].length],
      raw: match[0],
    }
  } catch {
    return null
  }
}

function tryDecimal(text: string): AmountMatch | null {
  // 小数格式，排除日期上下文和负数
  // 负向后行断言：
  //   - 不紧跟在 -/. 之后
  //   - 不处于 -\d+ 序列中（可变长度 lookbehind）
  // 负向先行断言：后面不能是 年/-///月/日/号
  const regex = /(?<![-.])(?<!-\d{0,10})(?<!\d[年/-])(\d{1,3}(?:[,，]\d{3})*\.\d{1,2})(?![年/月日号-]\d?)/
  const match = text.match(regex)
  if (!match || match.index === undefined) return null

  const amountStr = match[1].replace(/[,，]/g, '')
  try {
    const amount = Money.fromString(amountStr)
    if (Money.isZero(amount)) return null
    return {
      amount,
      range: [match.index, match.index + match[0].length],
      raw: match[0],
    }
  } catch {
    return null
  }
}

function tryInteger(text: string): AmountMatch | null {
  // 整数：排除年份、日期、负数、小数片段
  // 负向后行断言：不能紧跟在 -/. 或 digit+年/-// 之后；也不能处于 -\d+ 序列中
  // 负向先行断言：后面不能是 数字/-///年/月/日/号/. （. 表示属于小数的一部分）
  const regex = /(?<![-.])(?<!-\d{0,10})(?<!\d[年/-])(\d{1,6})(?![\d/年月日号.-])/
  const match = text.match(regex)
  if (!match || match.index === undefined) return null

  const amountStr = match[1]
  try {
    const amount = Money.fromString(amountStr)
    if (Money.isZero(amount)) return null
    return {
      amount,
      range: [match.index, match.index + match[0].length],
      raw: match[0],
    }
  } catch {
    return null
  }
}

/**
 * 从文本中提取所有金额（用于多笔交易场景）。
 * 返回去重后的金额列表，按位置排序。
 */
export function extractAllAmounts(text: string): AmountMatch[] {
  if (!text || typeof text !== 'string') {
    return []
  }

  const results: AmountMatch[] = []
  // 用范围去重：如果新匹配与已存在匹配重叠（任一位置落在已有范围内），跳过
  const ranges: Array<[number, number]> = []

  function overlaps(start: number, end: number): boolean {
    return ranges.some(([s, e]) => (start >= s && start < e) || (end > s && end <= e) || (start <= s && end >= e))
  }

  // 4 种模式（与 extractAmount 顺序一致）
  const patterns: Array<{ regex: RegExp; isChineseUnits: boolean }> = [
    { regex: /[¥￥]\s*(\d{1,10}(?:[,，]\d{3})*(?:\.\d{1,2})?)/g, isChineseUnits: false },
    { regex: /(\d{1,10}(?:[,，]\d{3})*(?:\.\d{1,2})?)\s*(?:元|块)(\d{1,2})?\s*(?:角|毛)?/g, isChineseUnits: true },
    { regex: /(?<![-.])(?<!-\d{0,10})(?<!\d[年/-])(\d{1,3}(?:[,，]\d{3})*\.\d{1,2})(?![年/月日号-]\d?)/g, isChineseUnits: false },
    { regex: /(?<![-.])(?<!-\d{0,10})(?<!\d[年/-])(\d{1,6})(?![\d/年月日号.-])/g, isChineseUnits: false },
  ]

  for (const { regex, isChineseUnits } of patterns) {
    let match
    while ((match = regex.exec(text)) !== null) {
      const start = match.index
      const end = start + match[0].length
      if (overlaps(start, end)) continue

      // 提取金额字符串
      let amountStr: string
      if (isChineseUnits) {
        const yuanStr = match[1].replace(/[,，]/g, '')
        const jiaoStr = match[2]
        amountStr = jiaoStr && jiaoStr !== '0' ? `${yuanStr}.${jiaoStr.padEnd(1, '0').slice(0, 1)}` : yuanStr
      } else {
        amountStr = match[1].replace(/[,，]/g, '')
      }

      try {
        const amount = Money.fromString(amountStr)
        if (!Money.isZero(amount)) {
          results.push({ amount, range: [start, end], raw: match[0] })
          ranges.push([start, end])
        }
      } catch {
        // 跳过无效金额
      }
    }
  }

  // 按位置排序
  return results.sort((a, b) => a.range[0] - b.range[0])
}
