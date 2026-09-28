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
//
// REQ-79（T20-5）：新旧种子名并存——老账户仍叫「娱乐 / 居住 / 通讯 / 红包收入」，
// 新账户是「休闲娱乐 / 住房 / 生活服务 / 收红包」。语义同义的两套名用 altNames
// 并列，解析时**谁在用户真实分类里存在就用谁**，两代账户都能命中；旧名列的
// 关键词一律保留（改名会让老账户突然匹配不上）。

interface BuiltinAlias {
  keyword: string
  categoryId: string
  categoryName: string
  /** 语义等价的候选名（按序解析；仅用于新旧种子名并存的两套口径）。 */
  altNames?: readonly string[]
}

const BUILTIN_ALIASES: ReadonlyArray<BuiltinAlias> = [
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
  // REQ-79：新种子一级名的别名（上面那些旧名条目一律保留）
  { keyword: '化妆品', categoryId: 'cat-beauty', categoryName: '穿搭美容' },
  { keyword: '理发', categoryId: 'cat-beauty', categoryName: '穿搭美容' },
  { keyword: '衣服', categoryId: 'cat-beauty', categoryName: '穿搭美容' },
  { keyword: '水果', categoryId: 'cat-snack', categoryName: '水果零食' },
  { keyword: '健身', categoryId: 'cat-sports', categoryName: '运动' },
  { keyword: '瑜伽', categoryId: 'cat-sports', categoryName: '运动' },
  { keyword: '游泳', categoryId: 'cat-sports', categoryName: '运动' },
  { keyword: '猫粮', categoryId: 'cat-pet', categoryName: '宠物' },
  { keyword: '狗粮', categoryId: 'cat-pet', categoryName: '宠物' },
  { keyword: '铲屎', categoryId: 'cat-pet', categoryName: '宠物' },
  { keyword: '奶粉', categoryId: 'cat-child', categoryName: '养娃' },
  { keyword: '尿不湿', categoryId: 'cat-child', categoryName: '养娃' },
  { keyword: '菜市场', categoryId: 'cat-grocery', categoryName: '买菜' },
  { keyword: '保险', categoryId: 'cat-insurance', categoryName: '金融保险' },
  { keyword: '酒店', categoryId: 'cat-travel', categoryName: '酒店旅行' },
  { keyword: '民宿', categoryId: 'cat-travel', categoryName: '酒店旅行' },
  { keyword: '旅游', categoryId: 'cat-travel', categoryName: '酒店旅行' },
  { keyword: '培训', categoryId: 'cat-education', categoryName: '学习教育' },
  { keyword: '课程', categoryId: 'cat-education', categoryName: '学习教育' },
  { keyword: '学费', categoryId: 'cat-education', categoryName: '学习教育' },
  { keyword: '香烟', categoryId: 'cat-tobacco', categoryName: '烟酒' },
  { keyword: '白酒', categoryId: 'cat-tobacco', categoryName: '烟酒' },
  // 居住 → 新种子名「住房」
  { keyword: '房租', categoryId: 'cat-housing', categoryName: '居住', altNames: ['住房'] },
  { keyword: '租金', categoryId: 'cat-housing', categoryName: '居住', altNames: ['住房'] },
  { keyword: '水电', categoryId: 'cat-housing', categoryName: '居住', altNames: ['住房'] },
  { keyword: '电费', categoryId: 'cat-housing', categoryName: '居住', altNames: ['住房'] },
  { keyword: '水费', categoryId: 'cat-housing', categoryName: '居住', altNames: ['住房'] },
  { keyword: '燃气', categoryId: 'cat-housing', categoryName: '居住', altNames: ['住房'] },
  { keyword: '物业', categoryId: 'cat-housing', categoryName: '居住', altNames: ['住房'] },
  { keyword: '房贷', categoryId: 'cat-housing', categoryName: '住房', altNames: ['居住'] },
  // 通讯（新种子清单已无此名，落回「生活服务」）
  { keyword: '话费', categoryId: 'cat-telecom', categoryName: '通讯', altNames: ['生活服务'] },
  { keyword: '网费', categoryId: 'cat-telecom', categoryName: '通讯', altNames: ['生活服务'] },
  // 医疗 → 新种子名「医疗保健」
  { keyword: '药', categoryId: 'cat-medical', categoryName: '医疗', altNames: ['医疗保健'] },
  { keyword: '医院', categoryId: 'cat-medical', categoryName: '医疗', altNames: ['医疗保健'] },
  { keyword: '看病', categoryId: 'cat-medical', categoryName: '医疗', altNames: ['医疗保健'] },
  // 收入类（「奖金 / 红包」两套名并存；工资 / 退款 / 报销 新旧同名）
  { keyword: '工资', categoryId: 'cat-salary', categoryName: '工资' },
  { keyword: '薪水', categoryId: 'cat-salary', categoryName: '工资' },
  { keyword: '奖金', categoryId: 'cat-bonus', categoryName: '奖金', altNames: ['奖金补贴'] },
  { keyword: '补贴', categoryId: 'cat-bonus', categoryName: '奖金补贴', altNames: ['奖金'] },
  { keyword: '津贴', categoryId: 'cat-bonus', categoryName: '奖金补贴', altNames: ['奖金'] },
  { keyword: '红包', categoryId: 'cat-gift-income', categoryName: '红包收入', altNames: ['收红包'] },
  { keyword: '退款', categoryId: 'cat-refund', categoryName: '退款' },
  { keyword: '报销', categoryId: 'cat-reimbursement', categoryName: '报销' },
  { keyword: '基金', categoryId: 'cat-invest', categoryName: '投资理财' },
  { keyword: '股票', categoryId: 'cat-invest', categoryName: '投资理财' },
  { keyword: '理财', categoryId: 'cat-invest', categoryName: '投资理财' },
  { keyword: '分红', categoryId: 'cat-invest', categoryName: '投资理财' },
  { keyword: '副业', categoryId: 'cat-parttime', categoryName: '兼职' },
  { keyword: '外快', categoryId: 'cat-parttime', categoryName: '兼职' },
  { keyword: '零花钱', categoryId: 'cat-allowance', categoryName: '生活费' },
]

/**
 * 别名目标解析：按「主名 → altNames」顺序在用户真实分类里找同名项。
 *
 * 占位 id（如 cat-food）不在服务端授权的分类 UUID 集合内，直接发给
 * /ai/execute 会被判为幻觉拒绝；因此真实分类存在时必须解析为真实 UUID。
 */
function resolveAliasTarget(
  alias: BuiltinAlias,
  categories: Category[],
): Category | undefined {
  for (const name of [alias.categoryName, ...(alias.altNames ?? [])]) {
    const hit = categories.find((c) => c.archivedAt === null && c.name === name)
    if (hit) return hit
  }
  return undefined
}

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
  if (candidates.length === 0) {
    for (const alias of BUILTIN_ALIASES) {
      if (trimmed.includes(alias.keyword)) {
        const realCat = resolveAliasTarget(alias, categories)
        candidates.push({
          categoryId: realCat ? realCat.id : alias.categoryId,
          categoryName: realCat ? realCat.name : alias.categoryName,
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
