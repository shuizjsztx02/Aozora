import { describe, expect, it } from 'vitest'
import { bizDateOfMonth, bizDateToMonthPeriod, bizDateToYearPeriod, parseBizDate } from '../../domain/date'

describe('R-M1-31 日历与周期边界', () => {
  it.each(['2026-02-30', '2026-13-01', '2026-00-01'])('周期提取拒绝 %s', value => {
    expect(() => bizDateToMonthPeriod(value)).toThrow()
    expect(() => bizDateToYearPeriod(value)).toThrow()
  })

  it('日历日期不依赖宿主时区的跳日', () => {
    // 在 TZ=Pacific/Apia 下复跑；当地 2011-12-30 被跳过，仍是合法公历业务日期。
    expect(parseBizDate('2011-12-30')).toEqual({ year: 2011, month: 12, day: 30 })
    expect(bizDateOfMonth(2011, 12, 30)).toBe('2011-12-30')
  })
})
