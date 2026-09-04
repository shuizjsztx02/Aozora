import { describe, it, expect } from 'vitest'
import { detectTxType, hasExplicitTxType } from '../../../domain/nlu/tx-type'

// ─── 核心用例：支出 ───

describe('detectTxType — 支出（高置信）', () => {
  it('花', () => {
    const result = detectTxType('午餐花了 35')
    expect(result.type).toBe('expense')
    expect(result.confidence).toBe(0.95)
    expect(result.keyword).toBe('花')
  })

  it('买', () => {
    const result = detectTxType('买了杯咖啡 28')
    expect(result.type).toBe('expense')
    expect(result.keyword).toBe('买')
  })

  it('付', () => {
    const result = detectTxType('付了 100 块')
    expect(result.type).toBe('expense')
    expect(result.keyword).toBe('付')
  })

  it('打车', () => {
    const result = detectTxType('打车 25')
    expect(result.type).toBe('expense')
    expect(result.keyword).toBe('打车')
  })

  it('吃', () => {
    const result = detectTxType('吃了碗面 18')
    expect(result.type).toBe('expense')
    expect(result.keyword).toBe('吃')
  })

  it('充值', () => {
    const result = detectTxType('充值话费 50')
    expect(result.type).toBe('expense')
    expect(result.keyword).toBe('充')
  })
})

// ─── 核心用例：收入 ───

describe('detectTxType — 收入（高置信）', () => {
  it('工资', () => {
    const result = detectTxType('工资到了 8000')
    expect(result.type).toBe('income')
    expect(result.confidence).toBe(0.95)
    expect(result.keyword).toBe('工资')
  })

  it('收到', () => {
    const result = detectTxType('收到红包 200')
    expect(result.type).toBe('income')
    expect(result.keyword).toBe('收到')
  })

  it('退款', () => {
    const result = detectTxType('退款 50')
    expect(result.type).toBe('income')
    expect(result.keyword).toBe('退款')
  })

  it('报销', () => {
    const result = detectTxType('报销 300')
    expect(result.type).toBe('income')
    expect(result.keyword).toBe('报销')
  })

  // R-M1-36 修复：转账是双向词，不再无条件判为收入
  it('转账（双向词，默认支出）', () => {
    // "转账给房东" 是支出，"收到转账" 是收入
    // 单独的 "转账" 无明确方向，默认支出
    const result = detectTxType('转账 500')
    expect(result.type).toBe('expense') // 默认支出
    expect(result.keyword).toBeNull() // 无明确关键词
  })

  it('收到转账（明确收入）', () => {
    const result = detectTxType('收到转账 500')
    expect(result.type).toBe('income')
    expect(result.keyword).toBe('收到')
  })
})

// ─── 默认策略 ───

describe('detectTxType — 无关键词默认支出', () => {
  it('纯金额：默认支出', () => {
    const result = detectTxType('35')
    expect(result.type).toBe('expense')
    expect(result.confidence).toBe(0.5)
    expect(result.keyword).toBeNull()
  })

  it('金额 + 分类词：默认支出', () => {
    const result = detectTxType('午餐 35')
    expect(result.type).toBe('expense')
    expect(result.confidence).toBe(0.5)
  })

  it('空字符串：默认支出，低置信', () => {
    const result = detectTxType('')
    expect(result.type).toBe('expense')
    expect(result.confidence).toBe(0.3)
  })
})

// ─── 边界用例 ───

describe('detectTxType — 边界', () => {
  it('null 输入', () => {
    const result = detectTxType(null as unknown as string)
    expect(result.type).toBe('expense')
    expect(result.confidence).toBe(0.3)
  })

  it('收入关键词优先级高于支出（"收到退款" 不应被 "款" 误判）', () => {
    // "收到退款" — 收入关键词 "收到" 先匹配
    const result = detectTxType('收到退款 100')
    expect(result.type).toBe('income')
  })
})

// ─── hasExplicitTxType ───

describe('hasExplicitTxType', () => {
  it('有支出关键词', () => {
    expect(hasExplicitTxType('午餐花了 35')).toBe(true)
  })

  it('有收入关键词', () => {
    expect(hasExplicitTxType('工资到了')).toBe(true)
  })

  it('无关键词', () => {
    expect(hasExplicitTxType('午餐 35')).toBe(false)
  })

  it('空输入', () => {
    expect(hasExplicitTxType('')).toBe(false)
  })
})

// ─── 幂等性 ───

describe('detectTxType — 幂等性', () => {
  it('相同输入返回相同输出', () => {
    const inputs = ['午餐花了 35', '工资到了', '35', '', null as unknown as string]
    for (const input of inputs) {
      const a = detectTxType(input)
      const b = detectTxType(input)
      expect(a).toEqual(b)
    }
  })
})
