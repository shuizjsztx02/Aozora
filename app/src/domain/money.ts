/**
 * MoneyCents — 整数分金额的构造、运算、格式化。
 *
 * 约束：
 * - amountCents 必须是 Number.isSafeInteger() 且 >= 0
 * - 所有运算结果始终校验安全范围
 * - 禁止浮点中间态：fromYuan 内部使用 Math.round(yuan * 100)
 */

export type MoneyCents = number & { readonly __brand: 'MoneyCents' }

const MAX_SAFE = Number.MAX_SAFE_INTEGER

/** 从整数分构造（参数必须是安全整数且 >= 0） */
export function cents(amount: number): MoneyCents {
  if (!Number.isSafeInteger(amount) || amount < 0) {
    throw new Error(
      `cents: expected non-negative safe integer, got ${amount}`
    )
  }
  return amount as MoneyCents
}

/**
 * 从元转分（内部 Math.round 消除浮点误差）。
 * 注意：float 入参存在半分陷阱，如 fromYuan(1.005) → 100（1.005 的浮点表示约为 1.00499…）。
 * 用户输入等精确十进制场景应使用 fromString，本函数仅用于无小数歧义的换算。
 */
export function fromYuan(yuan: number): MoneyCents {
  if (!Number.isFinite(yuan) || yuan < 0) {
    throw new Error(`fromYuan: expected non-negative finite number, got ${yuan}`)
  }
  return cents(Math.round(yuan * 100))
}

/** 从字符串解析 "12.50" → 1250；也接受 "12" → 1200、"99.9" → 9990（拒绝 "12." 等空小数点） */
export function fromString(s: string): MoneyCents {
  const trimmed = s.trim()
  if (!/^\d+(\.\d{1,2})?$/.test(trimmed)) {
    throw new Error(`fromString: invalid amount string "${s}"`)
  }
  const dotIdx = trimmed.indexOf('.')
  if (dotIdx === -1) {
    return cents(parseInt(trimmed, 10) * 100)
  }
  const intPart = trimmed.slice(0, dotIdx)
  const fracPart = trimmed.slice(dotIdx + 1).padEnd(2, '0')
  return cents(parseInt(intPart, 10) * 100 + parseInt(fracPart, 10))
}

/** 零值 */
export const ZERO: MoneyCents = 0 as MoneyCents

/** 加法 */
export function add(a: MoneyCents, b: MoneyCents): MoneyCents {
  const result = (a as number) + (b as number)
  if (result > MAX_SAFE) {
    throw new Error('MoneyCents overflow')
  }
  return result as MoneyCents
}

/** 减法（结果不允许为负） */
export function sub(a: MoneyCents, b: MoneyCents): MoneyCents {
  const result = (a as number) - (b as number)
  if (result < 0) {
    throw new Error('MoneyCents underflow: result would be negative')
  }
  return result as MoneyCents
}

/** 乘法（factor 为非负整数或有限小数，结果取整） */
export function multiply(c: MoneyCents, factor: number): MoneyCents {
  if (!Number.isFinite(factor) || factor < 0) {
    throw new Error(`multiply: factor must be non-negative finite, got ${factor}`)
  }
  const result = Math.round((c as number) * factor)
  if (result > MAX_SAFE) {
    throw new Error('MoneyCents overflow after multiply')
  }
  return result as MoneyCents
}

/** 是否为零 */
export function isZero(c: MoneyCents): boolean {
  return (c as number) === 0
}

/** 校验一个 number 是否为合法 MoneyCents 值 */
export function isValid(n: number): boolean {
  return Number.isSafeInteger(n) && n >= 0
}

/** 格式化为元字符串 "12.50" */
export function formatYuan(c: MoneyCents): string {
  const n = c as number
  const yuan = Math.floor(n / 100)
  const fen = n % 100
  return `${yuan}.${String(fen).padStart(2, '0')}`
}

/** 格式化为显示字符串 "¥12.50" */
export function formatDisplay(c: MoneyCents): string {
  return `¥${formatYuan(c)}`
}
