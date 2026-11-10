/** 管理后台时间口径：显示一律北京时间（`Asia/Shanghai`），存储/传输仍是 UTC ISO。 */

const BEIJING_TIME_ZONE = 'Asia/Shanghai'

const beijingFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: BEIJING_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

/** ISO 字符串 → 北京时间 `YYYY/MM/DD HH:mm:ss`；空值与非日期原样兜底。 */
export function formatBeijing(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return beijingFormatter.format(date)
}

/**
 * `<input type="datetime-local">` 的值是北京时间墙上时间。中国固定 UTC+8
 * （无夏令时），直接补偏移量即可得到后端能正确比较的 aware ISO。
 */
export function beijingInputToIso(value: string): string | undefined {
  if (!value) return undefined
  return `${value.length === 16 ? `${value}:00` : value}+08:00`
}
