import { describe, it, expect } from 'vitest'
import { extractDate } from '../../../domain/nlu/date'

// 固定时间：2026-09-04 周五 12:00 CST (UTC+8)
const NOW = Date.UTC(2026, 8, 4, 4, 0, 0) // 2026-09-04T04:00Z = 2026-09-04T12:00+08
const TZ = 'Asia/Shanghai'

// ─── 核心用例：相对日期 ───

describe('extractDate — 相对日期', () => {
  it('今天', () => {
    const result = extractDate('今天午餐', NOW, TZ)
    expect(result).not.toBeNull()
    expect(result!.bizDate).toBe('2026-09-04')
    expect(result!.raw).toBe('今天')
    expect(result!.confidence).toBe(0.95)
  })

  it('今日', () => {
    const result = extractDate('今日消费', NOW, TZ)
    expect(result!.bizDate).toBe('2026-09-04')
  })

  it('昨天', () => {
    const result = extractDate('昨天打车', NOW, TZ)
    expect(result!.bizDate).toBe('2026-09-03')
  })

  it('明日', () => {
    const result = extractDate('明日午餐', NOW, TZ)
    expect(result!.bizDate).toBe('2026-09-05')
  })

  it('前天', () => {
    const result = extractDate('前天晚餐', NOW, TZ)
    expect(result!.bizDate).toBe('2026-09-02')
  })

  it('后天', () => {
    const result = extractDate('后天出发', NOW, TZ)
    expect(result!.bizDate).toBe('2026-09-06')
  })
})

// ─── 核心用例：跨月边界 ───

describe('extractDate — 跨月边界', () => {
  it('9月1日的昨天是8月31日', () => {
    // 2026-09-01T04:00Z = 2026-09-01T12:00+08
    const sep1 = Date.UTC(2026, 8, 1, 4, 0, 0)
    const result = extractDate('昨天', sep1, TZ)
    expect(result!.bizDate).toBe('2026-08-31')
  })

  it('8月31日的明天是9月1日', () => {
    // 2026-08-31T04:00Z = 2026-08-31T12:00+08
    const aug31 = Date.UTC(2026, 7, 31, 4, 0, 0)
    const result = extractDate('明天', aug31, TZ)
    expect(result!.bizDate).toBe('2026-09-01')
  })

  it('跨年：1月1日昨天是去年12月31日', () => {
    // 2027-01-01T04:00Z = 2027-01-01T12:00+08
    const jan1 = Date.UTC(2027, 0, 1, 4, 0, 0)
    const result = extractDate('昨天', jan1, TZ)
    expect(result!.bizDate).toBe('2026-12-31')
  })
})

// ─── 星期 ───

describe('extractDate — 星期', () => {
  // 2026-09-04 是周五

  it('周一（最近过去的周一 = 8月31日）', () => {
    const result = extractDate('周一', NOW, TZ)
    expect(result).not.toBeNull()
    expect(result!.bizDate).toBe('2026-08-31')
  })

  it('周三（最近过去的周三 = 9月2日）', () => {
    const result = extractDate('周三', NOW, TZ)
    expect(result!.bizDate).toBe('2026-09-02')
  })

  it('周五（今天就是周五）', () => {
    const result = extractDate('周五', NOW, TZ)
    expect(result!.bizDate).toBe('2026-09-04')
  })

  it('本周三', () => {
    const result = extractDate('本周三', NOW, TZ)
    expect(result!.bizDate).toBe('2026-09-02')
  })

  it('上周三', () => {
    const result = extractDate('上周三', NOW, TZ)
    expect(result!.bizDate).toBe('2026-08-26')
  })

  it('星期天', () => {
    const result = extractDate('星期天', NOW, TZ)
    expect(result).not.toBeNull()
    // 最近的周日 = 2026-08-30
    expect(result!.bizDate).toBe('2026-08-30')
  })

  it('周天（同星期天）', () => {
    const result = extractDate('周天', NOW, TZ)
    expect(result!.bizDate).toBe('2026-08-30')
  })
})

// ─── 绝对日期 ───

describe('extractDate — 绝对日期', () => {
  it('9月3日', () => {
    const result = extractDate('9月3日午餐', NOW, TZ)
    expect(result).not.toBeNull()
    expect(result!.bizDate).toBe('2026-09-03')
    expect(result!.raw).toBe('9月3日')
  })

  it('3月5号', () => {
    const result = extractDate('3月5号', NOW, TZ)
    expect(result!.bizDate).toMatch(/202[56]-03-05/) // 今年或去年
  })

  it('12月25日', () => {
    const result = extractDate('12月25日', NOW, TZ)
    // 9月看到 12月，应视为去年（因为 12 > 9）
    expect(result!.bizDate).toBe('2025-12-25')
  })
})

// ─── 非法输入 ───

describe('extractDate — 拒绝非法', () => {
  it('空字符串', () => {
    expect(extractDate('', NOW, TZ)).toBeNull()
  })

  it('无日期关键词', () => {
    expect(extractDate('午餐 35', NOW, TZ)).toBeNull()
  })

  it('纯数字', () => {
    expect(extractDate('35', NOW, TZ)).toBeNull()
  })
})

// ─── 幂等性 ───

describe('extractDate — 幂等性', () => {
  it('相同输入返回相同输出', () => {
    const inputs = ['今天', '昨天', '周一', '9月3日']
    for (const input of inputs) {
      const a = extractDate(input, NOW, TZ)
      const b = extractDate(input, NOW, TZ)
      expect(a).toEqual(b)
    }
  })
})
