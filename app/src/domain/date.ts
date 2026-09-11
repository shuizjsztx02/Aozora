/**
 * 业务日期工具 — 用户时区下的 YYYY-MM-DD 计算。
 *
 * 约束：
 * - 使用 Intl.DateTimeFormat + timeZone，不使用 toISOString().slice
 * - 输入 UTC 时间戳（ms）+ 用户时区，输出 YYYY-MM-DD 字符串
 * - 同一 UTC 时刻在不同时区可能返回不同日期（23:59 vs 00:01 边界）
 */

const DATE_REGEX = /^\d{4}-\d{2}-\d{2}$/

// Intl.DateTimeFormat 构造昂贵，聚合热路径按（locale+时区）缓存实例
const formatterCache = new Map<string, Intl.DateTimeFormat>()

function getFormatter(timezone: string): Intl.DateTimeFormat {
  let formatter = formatterCache.get(timezone)
  if (!formatter) {
    formatter = new Intl.DateTimeFormat('en-CA', {
      timeZone: timezone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    })
    formatterCache.set(timezone, formatter)
  }
  return formatter
}

/** 从 UTC 时间戳 + 时区推导用户业务日期 "YYYY-MM-DD" */
export function localBizDate(utcTimestamp: number, timezone: string): string {
  if (!Number.isFinite(utcTimestamp)) {
    throw new Error(`localBizDate: invalid timestamp ${utcTimestamp}`)
  }
  const date = new Date(utcTimestamp)
  // Intl.DateTimeFormat 在 timeZone 选项下返回该时区的本地日期
  const parts = getFormatter(timezone).format(date)
  // 依赖 en-CA 输出 "YYYY-MM-DD"；断言形状，防止运行时 ICU 数据变更后静默损坏
  if (!DATE_REGEX.test(parts)) {
    throw new Error(
      `localBizDate: unexpected date shape "${parts}" for timezone "${timezone}"`
    )
  }
  return parts
}

/** 获取当前时刻在指定时区的业务日期 */
export function todayBizDate(timezone: string): string {
  return localBizDate(Date.now(), timezone)
}

/** 从 UTC 时间戳按设备本地时区显示 "HH:mm"（T14-1/REQ-38：首页/明细流水行副行共用） */
export function localHHmm(utcTimestamp: number): string {
  const d = new Date(utcTimestamp)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** 构造指定日期的 bizDate 字符串，校验格式与日历合法性（与 parseBizDate 往返一致） */
export function bizDateOfMonth(year: number, month: number, day: number): string {
  if (
    !Number.isInteger(year) || !Number.isInteger(month) || !Number.isInteger(day) ||
    year < 1000 || year > 9999
  ) {
    throw new Error(`bizDateOfMonth: invalid date ${year}-${month}-${day}`)
  }
  // 往返校验：日必须是该月的合法日（与 parseBizDate 同款），否则 2 月 31 日等会生成不存在的日期
  const roundTrip = new Date(Date.UTC(year, month - 1, day))
  if (
    roundTrip.getUTCFullYear() !== year ||
    roundTrip.getUTCMonth() !== month - 1 ||
    roundTrip.getUTCDate() !== day
  ) {
    throw new Error(`bizDateOfMonth: invalid date ${year}-${month}-${day}`)
  }
  const m = String(month).padStart(2, '0')
  const d = String(day).padStart(2, '0')
  return `${year}-${m}-${d}`
}

/** 解析 bizDate 字符串为 { year, month, day } */
export function parseBizDate(s: string): { year: number; month: number; day: number } {
  if (!DATE_REGEX.test(s)) {
    throw new Error(`parseBizDate: invalid format "${s}", expected YYYY-MM-DD`)
  }
  const [yearStr, monthStr, dayStr] = s.split('-')
  const year = parseInt(yearStr, 10)
  const month = parseInt(monthStr, 10)
  const day = parseInt(dayStr, 10)
  // 公历校验不依赖宿主时区，避免历史跳日影响。
  const d = new Date(Date.UTC(year, month - 1, day))
  if (
    d.getUTCFullYear() !== year ||
    d.getUTCMonth() !== month - 1 ||
    d.getUTCDate() !== day
  ) {
    throw new Error(`parseBizDate: invalid date "${s}"`)
  }
  return { year, month, day }
}

/** 判断 bizDate 是否在 [start, end] 闭区间内（三个参数均须为合法日历日期） */
export function isBizDateInRange(date: string, start: string, end: string): boolean {
  parseBizDate(date)
  parseBizDate(start)
  parseBizDate(end)
  return date >= start && date <= end
}

/** 从 bizDate 提取月份周期 "YYYY-MM" */
export function bizDateToMonthPeriod(date: string): string {
  parseBizDate(date)
  return date.slice(0, 7) // "YYYY-MM"
}

/** 从 bizDate 提取年份周期 "YYYY" */
export function bizDateToYearPeriod(date: string): string {
  parseBizDate(date)
  return date.slice(0, 4)
}
