/**
 * T7-2（决策 #2）：记账/展示目标账本选择 — 全站唯一入口，禁止各页面手写 filter。
 *
 * 背景：BookDao.listActive() 按 name 排序，UTF-8 字节序下"总账本"排在
 * "默认账本"之前，凡取 books[0] 当记账目标的调用点都会误选总账本。
 * 与 Kotlin 侧 Repository.getEntryBook() 语义一致。
 */
export function pickEntryBook<T extends { id: string; isMaster?: boolean }>(
  books: T[]
): T | undefined {
  if (books.length === 0) return undefined
  return books.find((b) => !b.isMaster) ?? books[0]
}
