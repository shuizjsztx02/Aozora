import { describe, it, expect } from 'vitest'
import { extractNote } from '../../../domain/nlu/note'
import { detectIntent } from '../../../domain/nlu/intent'

// ─── 备注抽取 ───

describe('extractNote', () => {
  it('剥离金额和分类后剩余文本', () => {
    // "午餐花了 35" → 剥离 午餐 (0,2) + 35 (5,7)
    const result = extractNote('午餐花了 35', [[0, 2], [5, 7]])
    expect(result.note).toBe('花了')
    expect(result.isEmpty).toBe(false)
  })

  it('全部剥离后返回默认', () => {
    const result = extractNote('午餐', [[0, 2]], '午餐')
    expect(result.note).toBe('午餐')
    expect(result.isEmpty).toBe(true)
  })

  it('空输入返回默认', () => {
    const result = extractNote('', [])
    expect(result.isEmpty).toBe(true)
  })

  it('清理多余空白', () => {
    // '午餐  花了  35  好吃'
    //  01  23 45  67 89  01  23
    // 剥离 [0,2)=午餐, [8,10)=35
    const result = extractNote('午餐  花了  35  好吃', [[0, 2], [8, 10]])
    expect(result.note).toBe('花了 好吃')
  })

  it('剥离范围超出文本长度时安全处理', () => {
    const result = extractNote('35', [[0, 100]])
    expect(result.isEmpty).toBe(true)
  })
})

// ─── 意图分类 ───

describe('detectIntent', () => {
  it('查询：多少', () => {
    const result = detectIntent('本月花了多少')
    expect(result.intent).toBe('query')
    expect(result.confidence).toBe(0.9)
  })

  it('查询：统计', () => {
    const result = detectIntent('统计报表')
    expect(result.intent).toBe('query')
  })

  it('裸时间词不判为查询（R-M1-34 修复）', () => {
    // "这个月"、"本月"、"今年" 等裸时间词不再判为 query
    const result = detectIntent('这个月')
    expect(result.intent).toBe('chat')
  })

  it('查询：有疑问词+时间词', () => {
    // 有疑问词（多少）时仍判为 query
    const result = detectIntent('这个月花了多少')
    expect(result.intent).toBe('query')
  })

  it('记账：花了+金额（R-M1-34 修复）', () => {
    // "花了50元" 应判为 record 而非 query
    const result = detectIntent('花了50元')
    expect(result.intent).toBe('record')
  })

  it('记账：时间词+金额（R-M1-34 修复）', () => {
    // "本月房租3000" 应判为 record
    const result = detectIntent('本月房租3000')
    expect(result.intent).toBe('record')
  })

  it('记账：花', () => {
    const result = detectIntent('花 35')
    expect(result.intent).toBe('record')
  })

  it('记账：有数字', () => {
    const result = detectIntent('35')
    expect(result.intent).toBe('record')
    expect(result.confidence).toBe(0.7) // 有金额时置信度提升
  })

  it('记账：收入', () => {
    const result = detectIntent('收入 8000')
    expect(result.intent).toBe('record')
  })

  it('chat：无数字无关键词', () => {
    const result = detectIntent('今天天气不错')
    expect(result.intent).toBe('chat')
    expect(result.confidence).toBe(0.5)
  })

  it('chat：问候', () => {
    const result = detectIntent('你好')
    expect(result.intent).toBe('chat')
  })

  it('chat：空输入', () => {
    const result = detectIntent('')
    expect(result.intent).toBe('chat')
    expect(result.confidence).toBe(0.3)
  })

  it('query 优先级高于 record', () => {
    const result = detectIntent('本月花了多少')
    expect(result.intent).toBe('query')
  })
})

// ─── 幂等性 ───

describe('幂等性', () => {
  it('extractNote 相同输入返回相同输出', () => {
    const a = extractNote('午餐 35', [[0, 2]])
    const b = extractNote('午餐 35', [[0, 2]])
    expect(a).toEqual(b)
  })

  it('detectIntent 相同输入返回相同输出', () => {
    const inputs = ['本月花了多少', '花 35', '今天天气不错', '']
    for (const input of inputs) {
      const a = detectIntent(input)
      const b = detectIntent(input)
      expect(a).toEqual(b)
    }
  })
})
