import { describe, it, expect } from 'vitest'
import {
  localBizDate,
  todayBizDate,
  bizDateOfMonth,
  parseBizDate,
  isBizDateInRange,
  bizDateToMonthPeriod,
  bizDateToYearPeriod,
  localHHmm,
} from '../../domain/date'

describe('localHHmm（T14-1 流水行副行共享格式化）', () => {
  it('设备本地时间 "HH:mm"，个位补零', () => {
    // new Date(y,m,d,h,mi) 构造本地时间，断言与时区无关
    expect(localHHmm(new Date(2026, 8, 3, 7, 5).getTime())).toBe('07:05')
    expect(localHHmm(new Date(2026, 8, 3, 23, 59).getTime())).toBe('23:59')
    expect(localHHmm(new Date(2026, 8, 3, 0, 0).getTime())).toBe('00:00')
  })
})

describe('localBizDate — 基本功能', () => {
  it('返回 YYYY-MM-DD 格式', () => {
    // 2026-09-03 12:00:00 UTC = 1756900800000 ms
    // 验证：new Date('2026-09-03T12:00:00Z').getTime()
    const ts = new Date('2026-09-03T12:00:00Z').getTime()
    expect(localBizDate(ts, 'UTC')).toBe('2026-09-03')
  })

  it('Asia/Shanghai 时区 +8 小时', () => {
    // 2026-09-03 00:00:00 UTC → 上海 08:00:00 → 同日 2026-09-03
    const ts = new Date('2026-09-03T00:00:00Z').getTime()
    expect(localBizDate(ts, 'Asia/Shanghai')).toBe('2026-09-03')
  })

  it('America/New_York 时区 -4/-5 小时', () => {
    // 2026-09-03 03:00:00 UTC → 纽约前一天 23:00 EDT → 2026-09-02
    const ts = new Date('2026-09-03T03:00:00Z').getTime()
    expect(localBizDate(ts, 'America/New_York')).toBe('2026-09-02')
  })
})

describe('localBizDate — 23:59/00:01 边界', () => {
  it('UTC 23:59 → 上海次日 07:59 → 同日的 UTC 日期不同', () => {
    // UTC 2026-09-03 23:59:59 → 上海 2026-09-04 07:59:59
    const ts = new Date('2026-09-03T23:59:59Z').getTime()
    expect(localBizDate(ts, 'UTC')).toBe('2026-09-03')
    expect(localBizDate(ts, 'Asia/Shanghai')).toBe('2026-09-04')
  })

  it('UTC 00:01 → 上海 08:01 → 同日', () => {
    const ts = new Date('2026-09-03T00:01:00Z').getTime()
    expect(localBizDate(ts, 'Asia/Shanghai')).toBe('2026-09-03')
  })

  it('UTC 23:59 的上海日期 ≠ UTC 日期', () => {
    // 关键边界：用户在 23:59 记账，UTC 是次日
    const ts = new Date('2026-09-03T15:59:59Z').getTime() // UTC 15:59 → 上海 23:59:59
    expect(localBizDate(ts, 'Asia/Shanghai')).toBe('2026-09-03')
    expect(localBizDate(ts, 'UTC')).toBe('2026-09-03')

    const ts2 = new Date('2026-09-03T16:00:00Z').getTime() // UTC 16:00 → 上海 00:00 次日
    expect(localBizDate(ts2, 'Asia/Shanghai')).toBe('2026-09-04')
    expect(localBizDate(ts2, 'UTC')).toBe('2026-09-03')
  })
})

describe('localBizDate — 跨月边界', () => {
  it('月末 23:59 UTC → 上海次日（下月 1 日）', () => {
    // 2026-09-30 16:00 UTC → 上海 2026-10-01 00:00
    const ts = new Date('2026-09-30T16:00:00Z').getTime()
    expect(localBizDate(ts, 'Asia/Shanghai')).toBe('2026-10-01')
  })

  it('月末最后一天在上海时区仍为当月', () => {
    // 2026-09-30 15:59 UTC → 上海 2026-09-30 23:59
    const ts = new Date('2026-09-30T15:59:00Z').getTime()
    expect(localBizDate(ts, 'Asia/Shanghai')).toBe('2026-09-30')
  })
})

describe('localBizDate — 错误处理', () => {
  it('无效时间戳抛异常', () => {
    expect(() => localBizDate(NaN, 'UTC')).toThrow()
    expect(() => localBizDate(Infinity, 'UTC')).toThrow()
  })

  it('无效时区抛异常', () => {
    const ts = Date.now()
    expect(() => localBizDate(ts, 'Invalid/Timezone')).toThrow()
  })
})

describe('localBizDate — 输出形状断言', () => {
  // R-M1-05 回归：Intl 输出异常形状时必须抛错，而不是静默返回非法日期
  it('Intl 返回非 YYYY-MM-DD 形状时抛异常', () => {
    const proto = Intl.DateTimeFormat.prototype
    const original = Object.getOwnPropertyDescriptor(proto, 'format')!
    Object.defineProperty(proto, 'format', {
      get: () => () => '03/09/2026', // 非 YYYY-MM-DD 形状
      configurable: true,
    })
    try {
      expect(() => localBizDate(Date.now(), 'UTC')).toThrow(/unexpected date shape/)
    } finally {
      Object.defineProperty(proto, 'format', original)
    }
  })
})

describe('todayBizDate', () => {
  it('返回当前日期的 YYYY-MM-DD', () => {
    const result = todayBizDate('UTC')
    expect(result).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })
})

describe('bizDateOfMonth', () => {
  it('构造合法日期', () => {
    expect(bizDateOfMonth(2026, 9, 3)).toBe('2026-09-03')
    expect(bizDateOfMonth(2026, 1, 1)).toBe('2026-01-01')
    expect(bizDateOfMonth(2026, 12, 31)).toBe('2026-12-31')
  })

  it('拒绝非法参数', () => {
    expect(() => bizDateOfMonth(2026, 0, 1)).toThrow()
    expect(() => bizDateOfMonth(2026, 13, 1)).toThrow()
    expect(() => bizDateOfMonth(2026, 1, 0)).toThrow()
    expect(() => bizDateOfMonth(2026, 1, 32)).toThrow()
  })

  // R-M1-01 回归：不得生成不存在的日期
  it('拒绝月内非法日（2 月 30/31、4 月 31、非闰年 2 月 29）', () => {
    expect(() => bizDateOfMonth(2026, 2, 31)).toThrow()
    expect(() => bizDateOfMonth(2026, 2, 30)).toThrow()
    expect(() => bizDateOfMonth(2026, 2, 29)).toThrow() // 2026 非闰年
    expect(() => bizDateOfMonth(2026, 4, 31)).toThrow()
    expect(() => bizDateOfMonth(2026, 6, 31)).toThrow()
  })

  it('接受闰年 2 月 29 日', () => {
    expect(bizDateOfMonth(2024, 2, 29)).toBe('2024-02-29')
  })

  it('拒绝非 4 位年份', () => {
    expect(() => bizDateOfMonth(99, 1, 1)).toThrow()
    expect(() => bizDateOfMonth(10000, 1, 1)).toThrow()
  })

  it('与 parseBizDate 往返一致', () => {
    const cases = [
      [2026, 1, 1],
      [2026, 2, 28],
      [2024, 2, 29],
      [2026, 9, 3],
      [2026, 12, 31],
    ] as const
    for (const [y, m, d] of cases) {
      const s = bizDateOfMonth(y, m, d)
      expect(parseBizDate(s)).toEqual({ year: y, month: m, day: d })
    }
  })
})

describe('parseBizDate', () => {
  it('解析合法日期', () => {
    expect(parseBizDate('2026-09-03')).toEqual({ year: 2026, month: 9, day: 3 })
    expect(parseBizDate('2026-01-01')).toEqual({ year: 2026, month: 1, day: 1 })
  })

  it('拒绝非法格式', () => {
    expect(() => parseBizDate('2026/09/03')).toThrow()
    expect(() => parseBizDate('abc')).toThrow()
    expect(() => parseBizDate('2026-9-3')).toThrow() // 需要补零
  })

  it('拒绝不存在的日期', () => {
    expect(() => parseBizDate('2026-02-30')).toThrow() // 2 月没有 30 日
    expect(() => parseBizDate('2026-04-31')).toThrow() // 4 月没有 31 日
  })
})

describe('isBizDateInRange', () => {
  it('闭区间判断', () => {
    expect(isBizDateInRange('2026-09-15', '2026-09-01', '2026-09-30')).toBe(true)
    expect(isBizDateInRange('2026-09-01', '2026-09-01', '2026-09-30')).toBe(true) // 边界
    expect(isBizDateInRange('2026-09-30', '2026-09-01', '2026-09-30')).toBe(true) // 边界
    expect(isBizDateInRange('2026-08-31', '2026-09-01', '2026-09-30')).toBe(false)
    expect(isBizDateInRange('2026-10-01', '2026-09-01', '2026-09-30')).toBe(false)
  })

  // R-M1-12 回归：非法输入必须先校验再比较
  it('拒绝非法输入', () => {
    expect(() => isBizDateInRange('garbage', '2026-09-01', '2026-09-30')).toThrow()
    expect(() => isBizDateInRange('2026-09-15', 'garbage', '2026-09-30')).toThrow()
    expect(() => isBizDateInRange('2026-09-15', '2026-09-01', 'garbage')).toThrow()
    expect(() => isBizDateInRange('2026-02-30', '2026-02-01', '2026-02-28')).toThrow() // 不存在的日期
  })
})

describe('bizDateToMonthPeriod / bizDateToYearPeriod', () => {
  it('提取月份周期', () => {
    expect(bizDateToMonthPeriod('2026-09-03')).toBe('2026-09')
    expect(bizDateToMonthPeriod('2026-01-15')).toBe('2026-01')
  })

  it('提取年份周期', () => {
    expect(bizDateToYearPeriod('2026-09-03')).toBe('2026')
  })

  it('拒绝非法格式', () => {
    expect(() => bizDateToMonthPeriod('2026/09/03')).toThrow()
    expect(() => bizDateToYearPeriod('abc')).toThrow()
  })
})

describe('localBizDate — UTC 不泄漏到业务日期', () => {
  it('同一 UTC 时刻在不同时区返回不同业务日期', () => {
    // UTC 2026-09-03 02:00 → 上海 10:00（同日）→ 纽约 22:00 前一天（不同日）
    const ts = new Date('2026-09-03T02:00:00Z').getTime()
    const shanghai = localBizDate(ts, 'Asia/Shanghai')
    const newYork = localBizDate(ts, 'America/New_York')
    expect(shanghai).toBe('2026-09-03')
    expect(newYork).toBe('2026-09-02')
    expect(shanghai).not.toBe(newYork)
  })
})