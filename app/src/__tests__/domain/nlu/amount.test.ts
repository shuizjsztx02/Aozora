import { describe, it, expect } from 'vitest'
import { extractAmount, extractAllAmounts } from '../../../domain/nlu/amount'
import * as Money from '../../../domain/money'

// ─── 核心用例（常见输入） ───

describe('extractAmount — 核心用例', () => {
  it('¥ 前缀（半角）', () => {
    const result = extractAmount('午餐 ¥35')
    expect(result).not.toBeNull()
    expect(Money.formatYuan(result!.amount)).toBe('35.00')
    expect(result!.raw).toBe('¥35')
  })

  it('￥ 前缀（全角）', () => {
    const result = extractAmount('午餐￥12.50')
    expect(result).not.toBeNull()
    expect(Money.formatYuan(result!.amount)).toBe('12.50')
  })

  it('数字 + 元', () => {
    const result = extractAmount('午饭 20元')
    expect(result).not.toBeNull()
    expect(Money.formatYuan(result!.amount)).toBe('20.00')
  })

  it('数字 + 块', () => {
    const result = extractAmount('打车 15块')
    expect(result).not.toBeNull()
    expect(Money.formatYuan(result!.amount)).toBe('15.00')
  })

  it('N块M（元角连读）', () => {
    const result = extractAmount('早餐 5块3')
    expect(result).not.toBeNull()
    expect(Money.formatYuan(result!.amount)).toBe('5.30')
  })

  it('N元M角', () => {
    const result = extractAmount('早餐 5元3角')
    expect(result).not.toBeNull()
    expect(Money.formatYuan(result!.amount)).toBe('5.30')
  })

  it('N元M毛', () => {
    const result = extractAmount('5元5毛')
    expect(result).not.toBeNull()
    expect(Money.formatYuan(result!.amount)).toBe('5.50')
  })

  it('小数金额（默认元）', () => {
    const result = extractAmount('咖啡 28.50')
    expect(result).not.toBeNull()
    expect(Money.formatYuan(result!.amount)).toBe('28.50')
  })

  it('整数金额（明确记账场景）', () => {
    const result = extractAmount('午饭 35')
    expect(result).not.toBeNull()
    expect(Money.formatYuan(result!.amount)).toBe('35.00')
  })

  it('千分位 ¥1,200.50', () => {
    const result = extractAmount('工资 ¥1,200.50')
    expect(result).not.toBeNull()
    expect(Money.formatYuan(result!.amount)).toBe('1200.50')
  })

  it('千分位（中文逗号）¥1，200', () => {
    const result = extractAmount('工资 ￥1，200')
    expect(result).not.toBeNull()
    expect(Money.formatYuan(result!.amount)).toBe('1200.00')
  })
})

// ─── 边界用例（日期、年份、计数等干扰） ───

describe('extractAmount — 拒绝日期/年份干扰', () => {
  it('YYYY-MM-DD 日期不识别为金额', () => {
    // "2024-01-01" 应该不识别出金额，或识别为 null
    const result = extractAmount('2024-01-01 的记录')
    // 关键不变量：绝不能把年份 2024 识别为 2024 元的金额
    // 保守策略：整个字符串无金额 → null
    if (result !== null) {
      expect(Money.formatYuan(result.amount)).not.toBe('2024.00')
    }
  })

  it('YYYY年MM月DD日 不识别年份为金额', () => {
    const result = extractAmount('2024年9月3日午餐')
    if (result !== null) {
      expect(Money.formatYuan(result.amount)).not.toBe('2024.00')
    }
  })

  it('N月N日 不识别月份为金额（歧义场景）', () => {
    const result = extractAmount('9月3号')
    // 纯日期不识别金额
    expect(result).toBeNull()
  })

  it('纯年份不识别', () => {
    const result = extractAmount('2024年总结')
    expect(result).toBeNull()
  })
})

// ─── 负面用例（拒绝非法输入） ───

describe('extractAmount — 拒绝非法输入', () => {
  it('空字符串', () => {
    expect(extractAmount('')).toBeNull()
  })

  it('纯文字', () => {
    expect(extractAmount('午餐吃了麦当劳')).toBeNull()
  })

  it('金额 0 拒绝', () => {
    expect(extractAmount('¥0')).toBeNull()
    expect(extractAmount('0元')).toBeNull()
    expect(extractAmount('0.00')).toBeNull()
  })

  it('小数点悬空拒绝（12.）', () => {
    // "12." 不是合法金额，但 "12" 本身可识别
    const result = extractAmount('消费 12.')
    // 如果识别，应是 12 元（忽略悬空小数点）
    if (result !== null) {
      expect(Money.formatYuan(result.amount)).toBe('12.00')
    }
  })

  it('负数拒绝', () => {
    // 负数不直接识别；收支方向由 tx-type 模块判定
    expect(extractAmount('-50')).toBeNull()
    expect(extractAmount('¥-12.5')).toBeNull()
  })
})

// ─── 位置信息 ───

describe('extractAmount — 位置信息', () => {
  it('返回正确的 range', () => {
    const result = extractAmount('午饭 ¥35 好吃')
    expect(result).not.toBeNull()
    expect(result!.raw).toBe('¥35')
    const [start, end] = result!.range
    expect('午饭 ¥35 好吃'.slice(start, end)).toBe('¥35')
  })

  it('返回正确的 raw', () => {
    const result = extractAmount('20块')
    expect(result).not.toBeNull()
    expect(result!.raw).toBe('20块')
  })
})

// ─── extractAllAmounts（多笔） ───

describe('extractAllAmounts — 多笔金额', () => {
  it('单笔', () => {
    const results = extractAllAmounts('午餐 ¥35')
    expect(results).toHaveLength(1)
    expect(Money.formatYuan(results[0].amount)).toBe('35.00')
  })

  it('多笔（和/还有 连接）', () => {
    const results = extractAllAmounts('午餐 35 还有地铁 5元')
    expect(results.length).toBeGreaterThanOrEqual(2)
    const amounts = results.map((r) => Money.formatYuan(r.amount))
    expect(amounts).toContain('35.00')
    expect(amounts).toContain('5.00')
  })

  it('按位置排序', () => {
    const results = extractAllAmounts('咖啡 28 打车 15')
    expect(results).toHaveLength(2)
    expect(results[0].range[0]).toBeLessThan(results[1].range[0])
  })

  it('空输入', () => {
    expect(extractAllAmounts('')).toEqual([])
    expect(extractAllAmounts('没有金额')).toEqual([])
  })
})

// ─── 幂等性 ───

describe('extractAmount — 幂等性', () => {
  it('相同输入返回相同输出', () => {
    const inputs = ['午餐 ¥35', '20元5角', '工资 ¥1,200.50', '9月3号']
    for (const input of inputs) {
      const a = extractAmount(input)
      const b = extractAmount(input)
      expect(a).toEqual(b)
    }
  })
})
