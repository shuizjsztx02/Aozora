/**
 * 分类 emoji 映射。
 *
 * HomePage / DetailPage 共用，避免重复定义。
 */

const MAP: Record<string, string> = {
  '餐饮': '\u{1F37D}\u{FE0F}', '早餐': '\u{1F950}', '午餐': '\u{1F371}', '晚餐': '\u{1F37C}', '零食': '\u{1F36A}',
  '交通': '\u{1F687}', '打车': '\u{1F695}', '公交': '\u{1F68C}',
  '购物': '\u{1F6CD}\u{FE0F}', '日用': '\u{1F9F4}', '服饰': '\u{1F454}',
  '娱乐': '\u{1F3AE}', '电影': '\u{1F3AC}', '游戏': '\u{1F3B2}',
  '居住': '\u{1F3E0}', '房租': '\u{1F3E2}', '水电': '\u{1F4A1}',
  '医疗': '\u{1F3E5}', '通讯': '\u{1F4F1}', '学习': '\u{1F4DA}',
  '工资': '\u{1F4B0}', '奖金': '\u{1F381}', '理财': '\u{1F4C8}',
}

/** 根据分类名返回 emoji；未匹配时返回钱包默认值 */
export function getCategoryEmoji(name: string): string {
  return MAP[name] ?? '\u{1F4B0}'
}
