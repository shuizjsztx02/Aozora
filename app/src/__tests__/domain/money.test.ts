import { describe, it, expect } from 'vitest'
import {
  cents,
  fromYuan,
  fromString,
  add,
  sub,
  multiply,
  isZero,
  isValid,
  formatYuan,
  formatDisplay,
  ZERO,
} from '../../domain/money'

describe('MoneyCents — 构造', () => {
  it('fromYuan 拒绝换算后溢出或不再有限的金额（R-M1-19）', () => {
    expect(() => fromYuan(Number.MAX_SAFE_INTEGER)).toThrow()
    expect(() => fromYuan(Number.MAX_VALUE)).toThrow()
  })

  it('cents() 接受非负安全整数', () => {
    expect(cents(0)).toBe(0)
    expect(cents(100)).toBe(100)
    expect(cents(999)).toBe(999)
  })

  it('cents() 拒绝负数', () => {
    expect(() => cents(-1)).toThrow()
  })

  it('cents() 拒绝浮点数', () => {
    expect(() => cents(1.5)).toThrow()
    expect(() => cents(0.1 + 0.2)).toThrow() // 0.30000000000000004
  })

  it('fromYuan() 消除浮点误差', () => {
    // 关键用例：0.1 + 0.2 在浮点下 ≠ 0.3，但在 cents 空间正确
    expect(fromYuan(0.1 + 0.2)).toBe(30)
    // 1.005 * 100 = 100.4999... (浮点) → Math.round = 100，非 101
    expect(fromYuan(1.005)).toBe(100)
    expect(fromYuan(0)).toBe(0)
    expect(fromYuan(99.99)).toBe(9999)
  })

  it('fromYuan() 拒绝负数和 NaN', () => {
    expect(() => fromYuan(-1)).toThrow()
    expect(() => fromYuan(NaN)).toThrow()
    expect(() => fromYuan(Infinity)).toThrow()
  })

  it('fromString() 解析合法金额字符串', () => {
    expect(fromString('12.50')).toBe(1250)
    expect(fromString('0')).toBe(0)
    expect(fromString('100')).toBe(10000)
    expect(fromString('0.01')).toBe(1)
    expect(fromString('99.9')).toBe(9990) // 补零 → 99.90
  })

  it('fromString() 拒绝非法格式', () => {
    expect(() => fromString('abc')).toThrow()
    expect(() => fromString('12.500')).toThrow() // 超过 2 位小数
    expect(() => fromString('-1.00')).toThrow()
    expect(() => fromString('')).toThrow()
    expect(() => fromString('12.')).toThrow() // R-M1-11 回归：小数点后无数字
  })
})

describe('MoneyCents — 运算', () => {
  it('add() 两个金额相加', () => {
    expect(add(cents(100), cents(200))).toBe(300)
    expect(add(cents(0), cents(0))).toBe(0)
  })

  it('add() 结果始终为安全整数', () => {
    const result = add(cents(1000000), cents(2000000))
    expect(Number.isSafeInteger(result)).toBe(true)
  })

  it('sub() 正常减法', () => {
    expect(sub(cents(300), cents(100))).toBe(200)
    expect(sub(cents(100), cents(100))).toBe(0)
  })

  it('sub() 结果不允许为负', () => {
    expect(() => sub(cents(100), cents(200))).toThrow()
  })

  it('multiply() 乘以整数因子', () => {
    expect(multiply(cents(100), 3)).toBe(300)
    expect(multiply(cents(100), 0)).toBe(0)
  })

  it('multiply() 乘以小数因子（取整）', () => {
    expect(multiply(cents(1000), 0.5)).toBe(500)
    expect(multiply(cents(100), 1.5)).toBe(150)
  })

  it('multiply() 拒绝负因子', () => {
    expect(() => multiply(cents(100), -1)).toThrow()
  })

  it('ZERO 常量', () => {
    expect(isZero(ZERO)).toBe(true)
    expect(ZERO).toBe(0)
  })
})

describe('MoneyCents — 浮点误差关键用例', () => {
  it('0.1 + 0.2 的 cents 运算精确', () => {
    // 浮点：0.1 + 0.2 = 0.30000000000000004 ≠ 0.3
    // cents：10 + 20 = 30 ✓
    const a = fromYuan(0.1)
    const b = fromYuan(0.2)
    expect(add(a, b)).toBe(30)
    expect(formatYuan(add(a, b))).toBe('0.30')
  })

  it('多次累加无累积误差', () => {
    let total = ZERO
    for (let i = 0; i < 100; i++) {
      total = add(total, fromYuan(0.01))
    }
    expect(total).toBe(100) // 100 分 = 1.00 元
    expect(formatYuan(total)).toBe('1.00')
  })
})

describe('MoneyCents — 校验与格式化', () => {
  it('isValid() 判断合法值', () => {
    expect(isValid(0)).toBe(true)
    expect(isValid(100)).toBe(true)
    expect(isValid(-1)).toBe(false)
    expect(isValid(1.5)).toBe(false)
    expect(isValid(Number.MAX_SAFE_INTEGER + 1)).toBe(false)
  })

  it('formatYuan() 格式化', () => {
    expect(formatYuan(cents(0))).toBe('0.00')
    expect(formatYuan(cents(100))).toBe('1.00')
    expect(formatYuan(cents(1250))).toBe('12.50')
    expect(formatYuan(cents(99))).toBe('0.99')
  })

  it('formatDisplay() 带货币符号', () => {
    expect(formatDisplay(cents(1250))).toBe('¥12.50')
    expect(formatDisplay(cents(0))).toBe('¥0.00')
  })
})
