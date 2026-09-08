import { describe, it, expect } from 'vitest'
import { matchCategory } from '../../../domain/nlu/category'
import type { Category } from '../../../domain/types'

// ─── 测试用分类数据 ───

const userCategories: Category[] = [
  { id: 'u-cat-travel', ownerId: 'u1', name: '旅行', parentId: null, kind: 'expense', archivedAt: null, version: 1 },
  { id: 'u-cat-pet', ownerId: 'u1', name: '宠物', parentId: null, kind: 'expense', archivedAt: null, version: 1 },
  { id: 'u-cat-study', ownerId: 'u1', name: '学习', parentId: null, kind: 'expense', archivedAt: null, version: 1 },
  { id: 'u-cat-archived', ownerId: 'u1', name: '已归档', parentId: null, kind: 'expense', archivedAt: 1000, version: 1 },
]

// ─── 核心用例：内置别名匹配 ───

describe('matchCategory — 内置别名', () => {
  it('餐饮：午餐', () => {
    const result = matchCategory('午餐 35', [])
    expect(result.match).not.toBeNull()
    expect(result.match!.categoryId).toBe('cat-food')
    expect(result.match!.confidence).toBe(0.8)
    expect(result.match!.keyword).toBe('午餐')
  })

  it('餐饮：咖啡', () => {
    const result = matchCategory('咖啡 28', [])
    expect(result.match!.categoryId).toBe('cat-food')
  })

  it('交通：打车', () => {
    const result = matchCategory('打车 25', [])
    expect(result.match!.categoryId).toBe('cat-transport')
  })

  it('交通：地铁', () => {
    const result = matchCategory('地铁 5', [])
    expect(result.match!.categoryId).toBe('cat-transport')
  })

  it('居住：房租', () => {
    const result = matchCategory('房租 3000', [])
    expect(result.match!.categoryId).toBe('cat-housing')
  })

  it('收入：工资', () => {
    const result = matchCategory('工资 8000', [])
    expect(result.match!.categoryId).toBe('cat-salary')
  })

  it('收入：退款', () => {
    const result = matchCategory('退款 50', [])
    expect(result.match!.categoryId).toBe('cat-refund')
  })
})

// ─── 别名解析到用户真实分类 UUID（服务端契约：提案须携带真实 UUID） ───

describe('matchCategory — 别名解析为真实分类', () => {
  it('午餐 → 用户"餐饮"分类的真实 UUID，而非占位 cat-food', () => {
    const food: Category = { id: '86d7ca76-b69b-4aa6-94a8-dce4eb73184e', ownerId: 'local', name: '餐饮', parentId: null, kind: 'expense', archivedAt: null, version: 1 }
    const result = matchCategory('午餐 35', [food])
    expect(result.match!.categoryId).toBe('86d7ca76-b69b-4aa6-94a8-dce4eb73184e')
    expect(result.match!.categoryName).toBe('餐饮')
  })

  it('别名命中但用户无同名分类 → 回退占位 id（保持离线/空分类兼容）', () => {
    const result = matchCategory('午餐 35', [])
    expect(result.match!.categoryId).toBe('cat-food')
  })
})

// ─── 核心用例：用户分类匹配 ───

describe('matchCategory — 用户分类（优先于内置别名）', () => {
  it('精确匹配：旅行', () => {
    const result = matchCategory('旅行了 5 天花了 3000', userCategories)
    expect(result.match!.categoryId).toBe('u-cat-travel')
    expect(result.match!.confidence).toBe(0.95)
  })

  it('精确匹配：宠物', () => {
    const result = matchCategory('宠物用品 200', userCategories)
    expect(result.match!.categoryId).toBe('u-cat-pet')
    expect(result.match!.keyword).toBe('宠物')
  })
})

// ─── 无匹配 ───

describe('matchCategory — 无匹配', () => {
  it('完全无关文本', () => {
    const result = matchCategory('今天天气真好', [])
    expect(result.match).toBeNull()
    expect(result.warnings).toContain('未识别到分类')
  })

  it('空字符串', () => {
    const result = matchCategory('', [])
    expect(result.match).toBeNull()
  })

  it('纯数字', () => {
    const result = matchCategory('35', [])
    expect(result.match).toBeNull()
  })
})

// ─── 多匹配降级 ───

describe('matchCategory — 多匹配', () => {
  it('内置别名指向同一分类：不视为多匹配', () => {
    // "午餐饭" 同时包含 "午餐" 和 "饭"，但都指向 cat-food
    const result = matchCategory('午餐饭 35', [])
    expect(result.match).not.toBeNull()
    expect(result.match!.categoryId).toBe('cat-food')
    // 不应出现多匹配 warning
    expect(result.warnings.some((w) => w.includes('多个分类匹配'))).toBe(false)
  })

  it('用户分类多匹配：降置信度 + warning', () => {
    // 构造一个场景：两个分类名都出现在文本中
    const cats: Category[] = [
      { id: 'c1', ownerId: 'u1', name: '旅行', parentId: null, kind: 'expense', archivedAt: null, version: 1 },
      { id: 'c2', ownerId: 'u1', name: '学习', parentId: null, kind: 'expense', archivedAt: null, version: 1 },
    ]
    const result = matchCategory('旅行学习 100', cats)
    expect(result.match).not.toBeNull()
    expect(result.match!.confidence).toBeLessThan(0.95)
    expect(result.warnings.some((w) => w.includes('多个分类匹配'))).toBe(true)
  })
})

// ─── 已归档分类 ───

describe('matchCategory — 已归档分类不参与匹配', () => {
  it('已归档分类不匹配', () => {
    const result = matchCategory('已归档 50', userCategories)
    expect(result.match).toBeNull()
  })
})

// ─── 幂等性 ───

describe('matchCategory — 幂等性', () => {
  it('相同输入返回相同输出', () => {
    const a = matchCategory('午餐 35', userCategories)
    const b = matchCategory('午餐 35', userCategories)
    expect(a).toEqual(b)
  })
})
