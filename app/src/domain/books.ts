/**
 * T7-2（决策 #2）：记账/展示目标账本选择 — 全站唯一入口，禁止各页面手写 filter。
 *
 * 背景：BookDao.listActive() 按 name 排序，UTF-8 字节序下"总账本"排在
 * "默认账本"之前，凡取 books[0] 当记账目标的调用点都会误选总账本。
 * 与 Kotlin 侧 Repository.getEntryBook() 语义一致。
 *
 * T9-5a（REQ-11 决策 #3）：可选 defaultId（= meta.default_book_id）命中且非
 * master 时优先；缺省行为与旧版逐字一致（向后兼容）。
 */
export function pickEntryBook<T extends { id: string; isMaster?: boolean }>(
  books: T[],
  defaultId?: string | null
): T | undefined {
  if (books.length === 0) return undefined
  if (defaultId) {
    const def = books.find((b) => b.id === defaultId && !b.isMaster)
    if (def) return def
  }
  return books.find((b) => !b.isMaster) ?? books[0]
}

/**
 * 第八轮 REQ-9：默认账户选择。
 * T9-5a（REQ-11 决策 #3）：可选 defaultId（= meta.default_account_id）命中优先；
 * 否则取列表第一个账户（native account.list 已按 account_order 整形）；
 * 无账户时 undefined（记账 accountId 传 null）。
 */
export function pickDefaultAccount<T extends { id: string }>(
  accounts: T[],
  defaultId?: string | null
): T | undefined {
  if (defaultId) {
    const def = accounts.find((a) => a.id === defaultId)
    if (def) return def
  }
  return accounts[0]
}
