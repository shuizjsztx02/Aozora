/** 极简 className 合并——空值丢弃，避免引入额外依赖。 */
export function cn(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(' ')
}
