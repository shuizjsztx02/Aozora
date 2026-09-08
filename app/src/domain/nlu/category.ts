/**
 * 分类匹配器 — 从文本中识别最可能的分类。
 *
 * 策略：
 * 1. 内置别名表：高频分类的关键词映射（如"饭/吃/午餐" → 餐饮）
 * 2. 用户分类名称精确匹配：与 context.categories 中的 name 比对
 * 3. 用户分类名称子串匹配：分类名包含在文本中，或文本包含在分类名中
 * 4. 多匹配：降置信度 + 加 warning
 * 5. 无匹配：categoryId 省略，加 warning
 *
 * 约束：
 * - 纯函数，不依赖存储
 * - 只读 categories，不修改
 */

import type { Category } from '../types'

export interface CategoryMatch {
  categoryId: string
  categoryName: string
  confidence: number
  /** 匹配的关键词（可能是别名或分类名） */
  keyword: string
}

export interface CategoryMatchResult {
  match: CategoryMatch | null
  warnings: string[]
}

// ─── 内置别名表 ───
// 键 = 别名（中文关键词），值 = 默认分类 id
// 仅覆盖最通用的场景；用户自定义分类通过 context 覆盖

const BUILTIN_ALIASES: ReadonlyArray<{ keyword: string; categoryId: string; categoryName: string }> = [
  // 餐饮
  { keyword: '早餐', categoryId: 'cat-food', categoryName: '餐饮' },
  { keyword: '午餐', categoryId: 'cat-food', categoryName: '餐饮' },
  { keyword: '晚饭', categoryId: 'cat-food', categoryName: '餐饮' },
  { keyword: '晚餐', categoryId: 'cat-food', categoryName: '餐饮' },
  { keyword: '饭', categoryId: 'cat-food', categoryName: '餐饮' },
  { keyword: '餐', categoryId: 'cat-food', categoryName: '餐饮' },
  { keyword: '咖啡', categoryId: 'cat-food', categoryName: '餐饮' },
  { keyword: '奶茶', categoryId: 'cat-food', categoryName: '餐饮' },
  { keyword: '饮料', categoryId: 'cat-food', categoryName: '餐饮' },
  { keyword: '外卖', categoryId: 'cat-food', categoryName: '餐饮' },
  // 交通
  { keyword: '打车', categoryId: 'cat-transport', categoryName: '交通' },
  { keyword: '地铁', categoryId: 'cat-transport', categoryName: '交通' },
  { keyword: '公交', categoryId: 'cat-transport', categoryName: '交通' },
  { keyword: '滴滴', categoryId: 'cat-transport', categoryName: '交通' },
  { keyword: '出租车', categoryId: 'cat-transport', categoryName: '交通' },
  { keyword: '加油', categoryId: 'cat-transport', categoryName: '交通' },
  { keyword: '停车', categoryId: 'cat-transport', categoryName: '交通' },
  // 购物
  { keyword: '购物', categoryId: 'cat-shopping', categoryName: '购物' },
  { keyword: '淘宝', categoryId: 'cat-shopping', categoryName: '购物' },
  { keyword: '京东', categoryId: 'cat-shopping', categoryName: '购物' },
  { keyword: '拼多多', categoryId: 'cat-shopping', categoryName: '购物' },
  // 居住
  { keyword: '房租', categoryId: 'cat-housing', categoryName: '居住' },
  { keyword: '租金', categoryId: 'cat-housing', categoryName: '居住' },
  { keyword: '水电', categoryId: 'cat-housing', categoryName: '居住' },
  { keyword: '电费', categoryId: 'cat-housing', categoryName: '居住' },
  { keyword: '水费', categoryId: 'cat-housing', categoryName: '居住' },
  { keyword: '燃气', categoryId: 'cat-housing', categoryName: '居住' },
  { keyword: '物业', categoryId: 'cat-housing', categoryName: '居住' },
  // 通讯
  { keyword: '话费', categoryId: 'cat-telecom', categoryName: '通讯' },
  { keyword: '网费', categoryId: 'cat-telecom', categoryName: '通讯' },
  // 医疗
  { keyword: '药', categoryId: 'cat-medical', categoryName: '医疗' },
  { keyword: '医院', categoryId: 'cat-medical', categoryName: '医疗' },
  { keyword: '看病', categoryId: 'cat-medical', categoryName: '医疗' },
  // 收入类
  { keyword: '工资', categoryId: 'cat-salary', categoryName: '工资' },
  { keyword: '薪水', categoryId: 'cat-salary', categoryName: '工资' },
  { keyword: '奖金', categoryId: 'cat-bonus', categoryName: '奖金' },
  { keyword: '红包', categoryId: 'cat-gift-income', categoryName: '红包收入' },
  { keyword: '退款', categoryId: 'cat-refund', categoryName: '退款' },
  { keyword: '报销', categoryId: 'cat-reimbursement', categoryName: '报销' },
]

/**
 * 从文本中匹配分类。
 *
 * @param text 用户输入文本
 * @param categories 用户自定义分类列表（来自 context）
 */
export function matchCategory(text: string, categories: Category[]): CategoryMatchResult {
  const warnings: string[] = []

  if (!text || typeof text !== 'string') {
    return { match: null, warnings: ['未识别到分类'] }
  }

  const trimmed = text.trim()
  if (!trimmed) {
    return { match: null, warnings: ['未识别到分类'] }
  }

  const candidates: CategoryMatch[] = []

  // 1. 用户分类精确匹配（最高优先级）
  for (const cat of categories) {
    if (cat.archivedAt !== null) continue // 跳过已归档
    if (trimmed.includes(cat.name)) {
      candidates.push({
        categoryId: cat.id,
        categoryName: cat.name,
        confidence: 0.95,
        keyword: cat.name,
      })
    }
  }

  // 2. 内置别名匹配
  //    命中别名后，用目标分类名在用户真实分类里查同名项，取其 UUID。
  //    占位 id（如 cat-food）不在服务端授权的分类 UUID 集合内，直接发给
  //    /ai/execute 会被判为幻觉拒绝；因此真实分类存在时必须解析为真实 UUID。
  if (candidates.length === 0) {
    for (const alias of BUILTIN_ALIASES) {
      if (trimmed.includes(alias.keyword)) {
        const realCat = categories.find(
          (c) => c.archivedAt === null && c.name === alias.categoryName,
        )
        candidates.push({
          categoryId: realCat ? realCat.id : alias.categoryId,
          categoryName: alias.categoryName,
          confidence: 0.8,
          keyword: alias.keyword,
        })
      }
    }
  }

  // 3. 用户分类子串匹配（仅在无精确匹配时）
  if (candidates.length === 0) {
    for (const cat of categories) {
      if (cat.archivedAt !== null) continue
      // 双向子串：分类名包含在文本中，或文本包含在分类名中
      // 但要求分类名长度 >= 2 避免过短误匹配
      if (cat.name.length >= 2) {
        if (trimmed.includes(cat.name) || (trimmed.length >= 2 && cat.name.includes(trimmed))) {
          candidates.push({
            categoryId: cat.id,
            categoryName: cat.name,
            confidence: 0.6,
            keyword: cat.name,
          })
        }
      }
    }
  }

  // 处理匹配结果：先去重（同一 categoryId 只保留最高置信）
  const deduped = new Map<string, CategoryMatch>()
  for (const c of candidates) {
    const existing = deduped.get(c.categoryId)
    if (!existing || c.confidence > existing.confidence) {
      deduped.set(c.categoryId, c)
    }
  }
  const unique = Array.from(deduped.values())

  if (unique.length === 0) {
    warnings.push('未识别到分类')
    return { match: null, warnings }
  }

  if (unique.length === 1) {
    return { match: unique[0], warnings }
  }

  // 多匹配：降置信度 + 加 warning
  unique.sort((a, b) => b.confidence - a.confidence)
  const best = unique[0]
  warnings.push(`多个分类匹配：${unique.map((c) => c.categoryName).join('、')}，已选择 ${best.categoryName}`)
  return {
    match: { ...best, confidence: Math.max(0.3, best.confidence - 0.1 * (unique.length - 1)) },
    warnings,
  }
}
