package com.aozora.app.db.entity

/**
 * 实体默认图标（第十八轮 REQ-68 / T18-4）。
 *
 * 服务端 book/account 的 icon 列可空且无默认值（出网即 null；AI 走 v2 车道新建账户
 * 时也常常不带 icon），客户端必须自己兜底。此前的兜底值是裸词 `"default"`
 * （bridge 两处则是 `"book"` / `"account"`）——而 CategoryIcon 的渲染优先级是
 * 「icon 非空即原样文本」，于是这几个英文字母被直接画进了圆形图标框（用户诉求 REQ-68）。
 *
 * 常量集中在此，取值与两张表单的初值一致（TS 侧 `AccountsPage.tsx` 的 `'💰'`、
 * `BooksPage.tsx` 的 `'📒'`）。**用户确认附件里圈选的具体图标后，改这里 + 那两处。**
 */
object DefaultIcons {
    /** 账本默认图标 —— 与 BooksPage 表单初值一致。 */
    const val BOOK = "📒"

    /** 账户默认图标 —— 与 AccountsPage 表单初值一致。 */
    const val ACCOUNT = "💰"
}