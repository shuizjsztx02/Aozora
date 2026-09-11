/**
 * T-M4-02c: ai-proposal-builder 验收测试。
 *
 * 覆盖：
 * - AI 输出 → ActionProposal 转换
 * - 分类名匹配（精确/包含/未找到）
 * - 金额无效 → chat 兜底
 * - 契约闸（validateProposal）
 */

import { describe, it, expect } from 'vitest'
import { buildProposalFromAiOutput, type AiParseOutput } from '../../domain/ai-proposal-builder'
import type { CategoryData } from '../../platform/bridge/bridge-types'

const categories: CategoryData[] = [
  { id: 'cat-food', kind: 'expense', name: '餐饮', parentId: null, version: 1 },
  { id: 'cat-transport', kind: 'expense', name: '交通', parentId: null, version: 1 },
  { id: 'cat-salary', kind: 'income', name: '工资', parentId: null, version: 1 },
]

const ctx = {
  now: new Date('2026-09-06T12:00:00+08:00').getTime(),
  timezone: 'Asia/Shanghai',
  categories,
  bookId: 'book-1',
}

describe('buildProposalFromAiOutput', () => {
  it('正常解析：支出 + 精确分类匹配', () => {
    const output: AiParseOutput = {
      type: 'expense',
      amountCents: 1250,
      category: '餐饮',
      note: '午饭',
      bizDate: '2026-09-06',
      confidence: 0.95,
    }
    const proposal = buildProposalFromAiOutput(output, ctx)

    expect(proposal.action).toBe('add_tx')
    expect(proposal.confidence).toBe(0.95)
    expect(proposal.transactions).toHaveLength(1)
    expect(proposal.transactions![0].type).toBe('expense')
    expect(proposal.transactions![0].amountCents).toBe(1250)
    expect(proposal.transactions![0].categoryId).toBe('cat-food')
    expect(proposal.transactions![0].bizDate).toBe('2026-09-06')
    expect(proposal.transactions![0].note).toBe('午饭')
    expect(proposal.requiresConfirmation).toBe(false)
    expect(proposal.message).toContain('12.50')
  })

  it('正常解析：收入', () => {
    const output: AiParseOutput = {
      type: 'income',
      amountCents: 500000,
      category: '工资',
      note: '9月工资',
      bizDate: '2026-09-01',
      confidence: 0.99,
    }
    const proposal = buildProposalFromAiOutput(output, ctx)

    expect(proposal.transactions![0].type).toBe('income')
    expect(proposal.transactions![0].categoryId).toBe('cat-salary')
    expect(proposal.requiresConfirmation).toBe(false)
  })

  it('分类包含匹配：「交通费」→「交通」', () => {
    const output: AiParseOutput = {
      type: 'expense',
      amountCents: 2000,
      category: '交通费',
      note: '',
      bizDate: '',
      confidence: 0.85,
    }
    const proposal = buildProposalFromAiOutput(output, ctx)

    expect(proposal.transactions![0].categoryId).toBe('cat-transport')
  })

  it('分类未找到 → warning + requiresConfirmation', () => {
    const output: AiParseOutput = {
      type: 'expense',
      amountCents: 1000,
      category: '宠物',
      note: '猫粮',
      bizDate: '',
      confidence: 0.92,
    }
    const proposal = buildProposalFromAiOutput(output, ctx)

    expect(proposal.transactions![0].categoryId).toBeUndefined()
    expect(proposal.warnings).toContain('未找到分类"宠物"，请手动选择')
    expect(proposal.requiresConfirmation).toBe(true)
  })

  it('空分类名 → 无 categoryId，无 warning', () => {
    const output: AiParseOutput = {
      type: 'expense',
      amountCents: 1000,
      category: '',
      note: '买东西',
      bizDate: '',
      confidence: 0.9,
    }
    const proposal = buildProposalFromAiOutput(output, ctx)

    expect(proposal.transactions![0].categoryId).toBeUndefined()
    expect(proposal.requiresConfirmation).toBe(true)
  })

  it('日期为空 → 默认今天', () => {
    const output: AiParseOutput = {
      type: 'expense',
      amountCents: 500,
      category: '餐饮',
      note: '早餐',
      bizDate: '',
      confidence: 0.95,
    }
    const proposal = buildProposalFromAiOutput(output, ctx)

    expect(proposal.transactions![0].bizDate).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })

  it('金额无效（0）→ chat 兜底', () => {
    const output: AiParseOutput = {
      type: 'expense',
      amountCents: 0,
      category: '',
      note: '',
      bizDate: '',
      confidence: 0.1,
    }
    const proposal = buildProposalFromAiOutput(output, ctx)

    expect(proposal.action).toBe('chat')
    expect(proposal.confidence).toBeLessThan(0.5)
  })

  it('金额为负 → chat 兜底', () => {
    const output: AiParseOutput = {
      type: 'expense',
      amountCents: -100,
      category: '',
      note: '',
      bizDate: '',
      confidence: 0.1,
    }
    const proposal = buildProposalFromAiOutput(output, ctx)

    expect(proposal.action).toBe('chat')
  })

  it('低置信度 → requiresConfirmation', () => {
    const output: AiParseOutput = {
      type: 'expense',
      amountCents: 1000,
      category: '餐饮',
      note: '午饭',
      bizDate: '',
      confidence: 0.6,
    }
    const proposal = buildProposalFromAiOutput(output, ctx)

    expect(proposal.confidence).toBe(0.6)
    expect(proposal.requiresConfirmation).toBe(true)
  })

  it('置信度截断到 [0.1, 1.0]', () => {
    const output: AiParseOutput = {
      type: 'expense',
      amountCents: 1000,
      category: '餐饮',
      note: '',
      bizDate: '',
      confidence: 1.5,
    }
    const proposal = buildProposalFromAiOutput(output, ctx)

    expect(proposal.confidence).toBe(1.0)
  })

  it('输出 proposal 通过 validateProposal 契约闸', () => {
    const output: AiParseOutput = {
      type: 'expense',
      amountCents: 1250,
      category: '餐饮',
      note: '午饭',
      bizDate: '2026-09-06',
      confidence: 0.95,
    }
    const proposal = buildProposalFromAiOutput(output, ctx)

    // 所有必要字段存在
    expect(proposal.proposalId).toBeTruthy()
    expect(proposal.action).toBe('add_tx')
    expect(typeof proposal.confidence).toBe('number')
    expect(typeof proposal.requiresConfirmation).toBe('boolean')
    expect(typeof proposal.message).toBe('string')
    expect(Array.isArray(proposal.warnings)).toBe(true)
    expect(proposal.transactions).toHaveLength(1)
  })

  it('收支类型无效 → 默认为支出', () => {
    const output: AiParseOutput = {
      type: 'transfer',
      amountCents: 1000,
      category: '餐饮',
      note: '',
      bizDate: '',
      confidence: 0.8,
    }
    const proposal = buildProposalFromAiOutput(output, ctx)

    expect(proposal.transactions![0].type).toBe('expense')
  })

  it('note 截断到 200 字符', () => {
    const longNote = 'a'.repeat(300)
    const output: AiParseOutput = {
      type: 'expense',
      amountCents: 1000,
      category: '餐饮',
      note: longNote,
      bizDate: '',
      confidence: 0.9,
    }
    const proposal = buildProposalFromAiOutput(output, ctx)

    expect(proposal.transactions![0].note.length).toBeLessThanOrEqual(200)
  })
})

// ─── 第十二轮 T12-11（REQ-37 / 决策 #2 #3 #5）：interpret v2 + 操作圈定 ───────

import {
  parseAiInterpretOutput, hasOpConditions, applyOpFilter, buildEditUpdates,
  sumByFilter, buildAiCategoryNames, MAX_BATCH_OPS,
  matchAccountByName, matchBookByName,
  type AiOpFilter,
} from '../../domain/ai-proposal-builder'
import type {
  AccountData, BookData, TransactionData,
} from '../../platform/bridge/bridge-types'

const cats: CategoryData[] = [
  { id: 'c-food', kind: 'expense', name: '餐饮', parentId: null, version: 1 },
  { id: 'c-food-early', kind: 'expense', name: '早餐', parentId: 'c-food', version: 1 },
  { id: 'c-car', kind: 'expense', name: '交通', parentId: null, version: 1 },
  { id: 'c-salary', kind: 'income', name: '工资', parentId: null, version: 1 },
]

const TZ = 'Asia/Shanghai'

function tx(partial: Partial<TransactionData> & { id: string }): TransactionData {
  return {
    bookId: 'book-1',
    type: 'expense',
    amountCents: 1000,
    categoryId: 'c-food',
    note: '',
    bizDate: '2026-09-05',
    occurredAt: 1,
    source: 'manual',
    version: 3,
    deletedAt: null,
    ...partial,
  }
}

const txs: TransactionData[] = [
  tx({ id: 't1', bizDate: '2026-09-08', categoryId: 'c-car', note: '打车', amountCents: 2300 }),
  tx({ id: 't2', bizDate: '2026-09-07', categoryId: 'c-food-early', note: '早午餐', amountCents: 4700 }),
  tx({ id: 't3', bizDate: '2026-09-01', categoryId: 'c-salary', type: 'income', note: '月薪', amountCents: 800000 }),
  tx({ id: 't4', bizDate: '2026-08-20', categoryId: 'c-car', note: '地铁', amountCents: 500 }),
]

describe('parseAiInterpretOutput（v2 容错解析）', () => {
  it('intent 缺失/坏值 → bookkeeping（老服务端向前兼容）', () => {
    expect(parseAiInterpretOutput({ type: 'expense', amountCents: 100 }).intent).toBe('bookkeeping')
    expect(parseAiInterpretOutput({ intent: 'nonsense', amountCents: 500 }).intent).toBe('bookkeeping')
  })

  // T13-6（REQ-49 / 决策 #10）：人设守卫 —— bookkeeping ∧ amountCents≤0 → chat
  it('守卫：bookkeeping 无金额（闲聊误判形状）→ 改判 chat，reply/legacy 原样保留', () => {
    const v2 = parseAiInterpretOutput({
      intent: 'bookkeeping', type: '', amountCents: 0, category: '', note: '',
      bizDate: '', confidence: 0.9, reply: '哈哈，这个梗有意思',
    })
    expect(v2.intent).toBe('chat')
    expect(v2.reply).toBe('哈哈，这个梗有意思')
    expect(v2.legacy).toEqual({
      type: '', amountCents: 0, category: '', note: '', bizDate: '', confidence: 0.9,
    })
  })

  it('守卫：金额为负 / intent 缺失且无金额 也改判 chat', () => {
    expect(parseAiInterpretOutput({ intent: 'bookkeeping', amountCents: -100 }).intent).toBe('chat')
    expect(parseAiInterpretOutput({ type: 'expense' }).intent).toBe('chat')
  })

  it('守卫不越界：bookkeeping 有金额 / 其余 intent 零金额均不改判', () => {
    expect(parseAiInterpretOutput({ intent: 'bookkeeping', amountCents: 1 }).intent).toBe('bookkeeping')
    for (const intent of ['query', 'chat', 'edit', 'delete', 'config']) {
      expect(parseAiInterpretOutput({ intent, amountCents: 0 }).intent).toBe(intent)
    }
  })

  it('legacy 六字段照常提取', () => {
    const v2 = parseAiInterpretOutput({
      intent: 'bookkeeping', type: 'expense', amountCents: 1250,
      category: '餐饮', note: '午饭', bizDate: '2026-09-08', confidence: 0.9,
    })
    expect(v2.legacy).toEqual({
      type: 'expense', amountCents: 1250, category: '餐饮',
      note: '午饭', bizDate: '2026-09-08', confidence: 0.9,
    })
  })

  it('filter/set 逐字段剔除非法：坏日期/负数/非字符串', () => {
    const v2 = parseAiInterpretOutput({
      intent: 'edit',
      filter: {
        dateFrom: '2026-9-1', dateTo: '2026-09-09',  // dateFrom 坏格式 → 丢
        keyword: '  房租 ', category: '住 房',
        amountMinCents: -5, amountMaxCents: '20000',  // 负 → 丢；数字字符串 → 收
        recentN: 0,                                    // <1 → 丢
      },
      set: { amountCents: 650000, bizDate: 'nope', note: '', includeInBudget: 'yes', category: '  ' },
    })
    expect(v2.filter).toEqual({
      dateTo: '2026-09-09', keyword: '房租', category: '住 房', amountMaxCents: 20000,
    })
    expect(v2.set).toEqual({ amountCents: 650000, note: '' })
  })

  it('filter/set 非对象/全空 → null', () => {
    for (const bad of ['x', 5, [], null, undefined, {}]) {
      const v2 = parseAiInterpretOutput({ intent: 'delete', filter: bad, set: bad })
      expect(v2.filter).toBeNull()
      expect(v2.set).toBeNull()
    }
  })

  it('reply 非字符串 → ""', () => {
    expect(parseAiInterpretOutput({ intent: 'chat', reply: 123 }).reply).toBe('')
  })
})

describe('hasOpConditions', () => {
  it('null → false；有任一键 → true', () => {
    expect(hasOpConditions(null)).toBe(false)
    expect(hasOpConditions({ keyword: 'x' } as AiOpFilter)).toBe(true)
  })
})

describe('applyOpFilter（本地圈定）', () => {
  it('dateFrom/dateTo 闭区间（ISO 字典序）', () => {
    const rows = applyOpFilter(txs, cats, { dateFrom: '2026-09-01', dateTo: '2026-09-07' })
    expect(rows.map(t => t.id)).toEqual(['t2', 't3'])
  })

  it('keyword 大小写不敏感子串命中 note', () => {
    expect(applyOpFilter(txs, cats, { keyword: '打车' }).map(t => t.id)).toEqual(['t1'])
    expect(applyOpFilter(txs, cats, { keyword: 'TAXI' })).toEqual([])
  })

  it('category 可命中一级名/父名/双向子串（早餐→餐饮）', () => {
    expect(applyOpFilter(txs, cats, { category: '交通' }).map(t => t.id)).toEqual(['t1', 't4'])
    // 早餐 是 餐饮 的子命中（"餐饮".includes("早餐") 假 → "早餐".includes? 需 name 互相含）
    expect(applyOpFilter(txs, cats, { category: '早餐' }).map(t => t.id)).toEqual(['t2'])
    // LLM 给父名「餐饮」命中二级「早餐」行
    expect(applyOpFilter(txs, cats, { category: '餐饮' }).map(t => t.id)).toEqual(['t2'])
  })

  it('金额范围 + recentN 取最新 N 笔（bizDate DESC tie occurredAt DESC）', () => {
    expect(applyOpFilter(txs, cats, { amountMinCents: 1000, amountMaxCents: 5000 }).map(t => t.id))
      .toEqual(['t1', 't2'])
    expect(applyOpFilter(txs, cats, { recentN: 2 }).map(t => t.id)).toEqual(['t1', 't2'])
    // 乱序输入也按 bizDate/occurredAt 重排（不依赖 DAO 顺序）
    expect(applyOpFilter([...txs].reverse(), cats, { recentN: 1 }).map(t => t.id)).toEqual(['t1'])
  })

  it('组合条件求交集', () => {
    expect(applyOpFilter(txs, cats, { category: '交通', dateFrom: '2026-09-01' }).map(t => t.id))
      .toEqual(['t1'])
  })
})

describe('sumByFilter', () => {
  it('按 type 分列合计', () => {
    expect(sumByFilter(txs)).toEqual({ count: 4, expenseCents: 2300 + 4700 + 500, incomeCents: 800000 })
    expect(sumByFilter([])).toEqual({ count: 0, expenseCents: 0, incomeCents: 0 })
  })
})

describe('buildEditUpdates（单笔全字段 update payload）', () => {
  it('只覆盖 set 点名字段，保 id/账本/账户/source；version = 当前+1', () => {
    const base = tx({ id: 't1', categoryId: 'c-car', accountId: 'a1', note: '打车', amountCents: 2300, version: 7 })
    const input = buildEditUpdates(base, { note: '地铁', amountCents: 500 }, cats, TZ, Date.now())!
    expect(input).toMatchObject({
      id: 't1', bookId: 'book-1', type: 'expense', categoryId: 'c-car',
      accountId: 'a1', note: '地铁', amountCents: 500, bizDate: '2026-09-05',
      source: 'manual', includeInBudget: true, version: 8,
    })
  })

  it('set.category 新值按 tx 类型匹配本地分类名', () => {
    const input = buildEditUpdates(
      tx({ id: 't2', categoryId: 'c-food-early' }), { category: '交通' }, cats, TZ, Date.now(),
    )!
    expect(input.categoryId).toBe('c-car')
  })

  it('分类名匹配不到 / 原行缺分类且未点名 → null（绝不硬写）', () => {
    expect(buildEditUpdates(tx({ id: 't1' }), { category: '不存在类' }, cats, TZ, Date.now())).toBeNull()
    expect(buildEditUpdates(tx({ id: 't1', categoryId: '' }), null, cats, TZ, Date.now())).toBeNull()
  })

  it('includeInBudget 保留既有语义（false 保持 false；缺省视为 true）', () => {
    expect(buildEditUpdates(tx({ id: 't1', includeInBudget: false }), {}, cats, TZ, Date.now())!.includeInBudget).toBe(false)
    expect(buildEditUpdates(tx({ id: 't1' }), { includeInBudget: true }, cats, TZ, Date.now())!.includeInBudget).toBe(true)
  })

  it('改期重算 occurredAt（非今日→当日12:00；未改期→保留原值）', () => {
    const now = new Date('2026-09-10T08:00:00+08:00').getTime()
    const kept = buildEditUpdates(tx({ id: 't1', occurredAt: 42 }), { note: 'x' }, cats, TZ, now)!
    expect(kept.occurredAt).toBe(42)
    const moved = buildEditUpdates(tx({ id: 't1' }), { bizDate: '2026-08-01' }, cats, TZ, now)!
    expect(moved.bizDate).toBe('2026-08-01')
    expect(moved.occurredAt).toBe(new Date('2026-08-01T12:00:00').getTime())
  })

  it('金额 set 与既有坏值防御：amountCents<1 → null', () => {
    expect(buildEditUpdates(tx({ id: 't1', amountCents: 0 }), null, cats, TZ, Date.now())).toBeNull()
    expect(buildEditUpdates(tx({ id: 't1' }), { amountCents: 1 }, cats, TZ, Date.now())!.amountCents).toBe(1)
  })
})

describe('buildAiCategoryNames（context.categories 组装）', () => {
  it('去重 + 每项 ≤16 字 + 空白剔除', () => {
    const names = buildAiCategoryNames([
      { id: '1', kind: 'expense', name: '餐饮', parentId: null, version: 1 },
      { id: '2', kind: 'expense', name: '餐饮', parentId: null, version: 1 },   // 同名去重
      { id: '3', kind: 'expense', name: '   ', parentId: null, version: 1 },    // 空白剔除
      { id: '4', kind: 'expense', name: '超长超长超长超长超长超长超长超长超长', parentId: null, version: 1 },
    ])
    expect(names).toEqual(['餐饮', '超长超长超长超长超长超长超长超长'])
    expect(names[1]).toHaveLength(16)
  })

  it('cap 120 条', () => {
    const many = Array.from({ length: 150 }, (_, i) =>
      ({ id: `c${i}`, kind: 'expense' as const, name: `分类${i}`, parentId: null, version: 1 }))
    expect(buildAiCategoryNames(many)).toHaveLength(120)
  })
})

describe('批量上限常量', () => {
  it('MAX_BATCH_OPS=50（REQ-37 ⑤ v1）', () => {
    expect(MAX_BATCH_OPS).toBe(50)
  })
})

// ─── 第十五轮 T15-7（REQ-56 / 决策 #8）：edit 契约 v4 —— 全字段 ────────────────

const accounts: AccountData[] = [
  { id: 'a-wx', name: '微信零钱', icon: '💬', version: 1 },
  { id: 'a-test', name: '测试', icon: '🧪', version: 1 },
  { id: 'a-bank', name: '招商银行储蓄卡', icon: '🏦', version: 1 },
]
const books: BookData[] = [
  { id: 'book-1', ownerId: 'local', name: '日常', icon: '📒', version: 1 },
  { id: 'book-2', ownerId: 'local', name: '旅行', icon: '✈️', version: 1 },
  { id: 'book-m', ownerId: 'local', name: '总账本', icon: '📚', version: 1, isMaster: true },
]

describe('parseOpFilter/parseOpSet 新键白名单（T15-7）', () => {
  it('filter/set：type 只认两值；对象名 trim 后收', () => {
    const v2 = parseAiInterpretOutput({
      intent: 'edit',
      filter: { type: 'income', accountName: ' 微信 ', bookName: '旅行', recentN: 1 },
      set: { type: 'expense', accountName: '测试', bookName: ' 日常 ' },
    })
    expect(v2.filter).toEqual({ type: 'income', accountName: '微信', bookName: '旅行', recentN: 1 })
    expect(v2.set).toEqual({ type: 'expense', accountName: '测试', bookName: '日常' })
  })

  it('坏形状逐键丢弃：type 非两值 / 名单空串或非字符串（决策 #8 parse 层口径）', () => {
    const v2 = parseAiInterpretOutput({
      intent: 'edit',
      filter: { type: '转账', accountName: '   ', bookName: 42, recentN: 1 },
      set: { type: true, accountName: {}, bookName: [], bizDate: '2026-09-09' },
    })
    expect(v2.filter).toEqual({ recentN: 1 })
    expect(v2.set).toEqual({ bizDate: '2026-09-09' })
  })
})

describe('matchAccountByName / matchBookByName（T15-7）', () => {
  it('账户：精确优先；双向子串取最长命中；匹配不到 null', () => {
    expect(matchAccountByName('测试', accounts)?.id).toBe('a-test')
    expect(matchAccountByName('微信', accounts)?.id).toBe('a-wx')       // needle ⊂ 微信零钱
    expect(matchAccountByName('储蓄卡', accounts)?.id).toBe('a-bank')   // 最长命中优先
    expect(matchAccountByName('支付宝', accounts)).toBeNull()
    expect(matchAccountByName('', accounts)).toBeNull()
  })

  it('账本：master 总账本不可作 edit 目标（口径同 default.book_set）', () => {
    expect(matchBookByName('旅行', books)?.id).toBe('book-2')
    expect(matchBookByName('总账本', books)).toBeNull()
    expect(matchBookByName('账本', books)).toBeNull()
  })
})

describe('applyOpFilter type/账户/账本分支 + keyword 语义扩（T15-7）', () => {
  it('keyword 命中分类名（事故①「打牌」类：备注没有、分类命中）', () => {
    const rows = [
      tx({ id: 'k1', categoryId: 'c-food-early', note: '' }),        // 早餐←父级餐饮
      tx({ id: 'k2', categoryId: 'c-car', note: '打牌' }),
    ]
    expect(applyOpFilter(rows, cats, { keyword: '餐饮' }).map(t => t.id)).toEqual(['k1'])
    expect(applyOpFilter(rows, cats, { keyword: '早餐' }).map(t => t.id)).toEqual(['k1'])
    expect(applyOpFilter(rows, cats, { keyword: '打牌' }).map(t => t.id)).toEqual(['k2'])
  })

  it('type 圈定收支', () => {
    expect(applyOpFilter(txs, cats, { type: 'income' }).map(t => t.id)).toEqual(['t3'])
    expect(applyOpFilter(txs, cats, { type: 'expense' }).map(t => t.id))
      .toEqual(['t1', 't2', 't4'])
  })

  it('accountName：名单提供 → 命中保留/不中排除；名单未提供 → 分支跳过（向前兼容）', () => {
    const rows = [
      tx({ id: 'a1', accountId: 'a-wx' }),
      tx({ id: 'a2', accountId: 'a-test' }),
      tx({ id: 'a3', accountId: null }),
    ]
    expect(applyOpFilter(rows, cats, { accountName: '微信' }, accounts).map(t => t.id))
      .toEqual(['a1'])
    expect(applyOpFilter(rows, cats, { accountName: '不存在的账户' }, accounts)).toEqual([])
    expect(applyOpFilter(rows, cats, { accountName: '微信' })).toEqual(rows)
  })

  it('bookName：按 tx.bookId 对应账本名圈定', () => {
    const rows = [tx({ id: 'b1' }), tx({ id: 'b2', bookId: 'book-2' })]
    expect(applyOpFilter(rows, cats, { bookName: '旅行' }, undefined, books).map(t => t.id))
      .toEqual(['b2'])
    expect(applyOpFilter(rows, cats, { bookName: '旅行' })).toEqual(rows)
  })
})

describe('buildEditUpdates 全字段消费（T15-7）', () => {
  it('type 切换 + 旧分类名在新类型下同名平移', () => {
    const cats2: CategoryData[] = [
      ...cats,
      { id: 'c-food-in', kind: 'income', name: '餐饮', parentId: null, version: 1 },
    ]
    const flipped = buildEditUpdates(
      tx({ id: 'f1', categoryId: 'c-food', type: 'expense' }),
      { type: 'income' }, cats2, TZ, Date.now(),
    )!
    expect(flipped.type).toBe('income')
    expect(flipped.categoryId).toBe('c-food-in')
  })

  it('type 切换无新类型同名分类 → 保留原分类，不因此丢类型切换', () => {
    const flipped = buildEditUpdates(
      tx({ id: 'f2', categoryId: 'c-food', type: 'expense' }),
      { type: 'income' }, cats, TZ, Date.now(),
    )!
    expect(flipped.type).toBe('income')
    expect(flipped.categoryId).toBe('c-food')
  })

  it('set.category 按「改后类型」的分类集匹配（事故句：改成收入 → 工资）', () => {
    const input = buildEditUpdates(
      tx({ id: 'f3', categoryId: 'c-car', type: 'expense' }),
      { type: 'income', category: '工资' }, cats, TZ, Date.now(),
    )!
    expect(input.type).toBe('income')
    expect(input.categoryId).toBe('c-salary')
    // 点名分类不在新类型集 → null（绝不硬写跨类分类）
    expect(buildEditUpdates(
      tx({ id: 'f4', type: 'expense' }),
      { type: 'income', category: '交通' }, cats, TZ, Date.now(),
    )).toBeNull()
  })

  it('accountName→accountId、bookName→bookId（跨账本改单）', () => {
    const input = buildEditUpdates(
      tx({ id: 'g1', accountId: 'a-wx' }),
      { accountName: '测试', bookName: '旅行' }, cats, TZ, Date.now(), accounts, books,
    )!
    expect(input.accountId).toBe('a-test')
    expect(input.bookId).toBe('book-2')
    expect(input.version).toBe(4)
  })

  it('名→id 匹配不到 → 该字段保留原值不回写（宁缺勿错双保险）；其余字段照常', () => {
    const base = tx({ id: 'g2', accountId: 'a-wx' })
    const input = buildEditUpdates(
      base,
      { accountName: '不存在的账户', bookName: '总账本', amountCents: 900 },
      cats, TZ, Date.now(), accounts, books,
    )!
    expect(input.accountId).toBe('a-wx')
    expect(input.bookId).toBe('book-1')
    expect(input.amountCents).toBe(900)
  })

  it('名单未提供（老调用方）→ 点名的账户/账本不回写，type/金额照常', () => {
    const input = buildEditUpdates(
      tx({ id: 'g3', accountId: 'a-wx' }),
      { type: 'income', accountName: '测试' }, cats, TZ, Date.now(),
    )!
    expect(input.accountId).toBe('a-wx')
    expect(input.type).toBe('income')
  })
})
