/**
 * T9-5a（REQ-11 / 决策 #1）：sync_meta 展示元数据键与解析 helper。
 *
 * 排序与默认选择一律存 sync_meta（不加实体列、不上行）：实体列会在
 * bootstrap 重建时被 serverChangeMapTo* 冲刷，而 sync_meta 在
 * clearAllEntities 中存活；服务端不认这些字段 → 不污染 push payload。
 * native 侧白名单/AozoraBridge.kt MetaWhitelist 与本表保持一致（4 键）。
 */

export const META_KEY_DEFAULT_BOOK_ID = 'default_book_id'
export const META_KEY_DEFAULT_ACCOUNT_ID = 'default_account_id'
export const META_KEY_BOOK_ORDER = 'book_order'
export const META_KEY_ACCOUNT_ORDER = 'account_order'

/** meta.get 返回的键集合（调用方一次批量取回）。 */
export const META_KEYS = [
  META_KEY_DEFAULT_BOOK_ID,
  META_KEY_DEFAULT_ACCOUNT_ID,
  META_KEY_BOOK_ORDER,
  META_KEY_ACCOUNT_ORDER,
] as const

export type MetaKey = (typeof META_KEYS)[number]

/**
 * 解析 order 键（JSON id 数组）。非 JSON、非数组、元素非 string
 * 都回退空数组——UI 层以"未排序"处理；实际排序在 native 出口已整形，
 * 前端解析主要用于排序模式内本地预览。
 */
export function parseIdOrder(raw: string | null): string[] {
  if (!raw) return []
  try {
    const v: unknown = JSON.parse(raw)
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
  } catch {
    return []
  }
}

/** 序列化 order 键（写入 meta.set 前的统一形状）。 */
export function serializeIdOrder(ids: string[]): string {
  return JSON.stringify(ids)
}
