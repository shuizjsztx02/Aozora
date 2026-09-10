/**
 * 第十三轮 T13-5（REQ-48 / 决策 #6 #7 #8）：config 意图纯函数层测试。
 *
 * 覆盖：
 * - parseAiConfig：6 op 白名单逐一对齐 T13-4 prompt 契约；必填缺失/坏 op/非对象 → null
 *   （回落 chat 的判据）；坏可选字段仅丢该字段（filter/set 同口径）
 * - parseAiInterpretOutput：intent=config 携带 config；intent 坏值回落 bookkeeping
 * - resolveConfigTargets：名→id（trim+全等、重名取第一个、master 排除）、create 同类
 *   重名拒写、budget 分类 expense 口径、savings deadline 过期、匹配不到 → 引导语
 * - buildAiObjectNames：trim + ≤16 字 + 去重 + ≤30 条（context 名单段）
 * - deadlineToSavingsPeriod：deadline → native 周/月期限（含钳制）
 * - buildConfigPreview：各 op 的预览行（只展示会真实写入的效果）
 */
import { describe, it, expect } from 'vitest'
import {
  parseAiInterpretOutput, parseAiConfig, resolveConfigTargets,
  buildAiObjectNames, buildConfigPreview, deadlineToSavingsPeriod,
  type AiConfig, type ConfigResolveContext,
} from '../../domain/ai-proposal-builder'
import type {
  AccountData, BookData, CategoryData, SavingsGoalData,
} from '../../platform/bridge/bridge-types'

const cats: CategoryData[] = [
  { id: 'c-food', kind: 'expense', name: '餐饮', parentId: null, version: 1 },
  { id: 'c-food-early', kind: 'expense', name: '早餐', parentId: 'c-food', version: 1 },
  { id: 'c-car', kind: 'expense', name: '交通', parentId: null, version: 1 },
  { id: 'c-salary', kind: 'income', name: '工资', parentId: null, version: 1 },
]

const cfgBooks: BookData[] = [
  { id: 'master-1', ownerId: 'o', name: '总账本', icon: '📒', version: 1, isMaster: true },
  { id: 'book-a', ownerId: 'o', name: '我的账本', icon: '📒', version: 1 },
  { id: 'book-b', ownerId: 'o', name: '旅行', icon: '✈️', version: 1 },
]
const cfgAccounts: AccountData[] = [
  { id: 'acc-1', name: '现金', icon: '💵', version: 1 },
  { id: 'acc-2', name: '招商银行', icon: '🏦', version: 1, accountType: '储蓄卡' },
]
const cfgGoals: SavingsGoalData[] = [
  {
    id: 'goal-1', name: '新手机', targetCents: 800000, currentCents: 0, icon: '📱',
    periodValue: null, periodUnit: null, createdAt: 1, updatedAt: 1, version: 1,
  },
]

function resolveCtx(partial: Partial<ConfigResolveContext> = {}): ConfigResolveContext {
  return {
    books: cfgBooks, accounts: cfgAccounts, goals: cfgGoals, categories: cats,
    bookId: 'book-a', today: '2026-09-10', ...partial,
  }
}

describe('parseAiConfig（6 op 白名单，决策 #6）', () => {
  it('book.create：name 必填、icon 可选透传', () => {
    expect(parseAiConfig({ op: 'book.create', name: '旅行' })).toEqual({ op: 'book.create', name: '旅行' })
    expect(parseAiConfig({ op: 'book.create', name: ' 旅行 ', icon: '✈️' })).toEqual({ op: 'book.create', name: '旅行', icon: '✈️' })
    // icon 空白 → 丢可选字段，不整体拒
    expect(parseAiConfig({ op: 'book.create', name: '旅行', icon: '  ' })).toEqual({ op: 'book.create', name: '旅行' })
  })

  it('account.create：accountType 必为 income|expense；initBalance ≥0（含 0）', () => {
    expect(parseAiConfig({ op: 'account.create', name: '工资卡', accountType: 'income', initBalance: 5000 }))
      .toEqual({ op: 'account.create', name: '工资卡', accountType: 'income', initBalance: 5000 })
    expect(parseAiConfig({ op: 'account.create', name: 'A', accountType: 'expense', initBalance: 0 }))
      .toEqual({ op: 'account.create', name: 'A', accountType: 'expense', initBalance: 0 })
    expect(parseAiConfig({ op: 'account.create', name: 'A' })).toBeNull()
    expect(parseAiConfig({ op: 'account.create', name: 'A', accountType: '储蓄卡' })).toBeNull()
    expect(parseAiConfig({ op: 'account.create', name: 'A', accountType: 'income', initBalance: -1 }))
      .toEqual({ op: 'account.create', name: 'A', accountType: 'income' })
  })

  it('budget.create：category/amount 必填（amount>0，接受数字字符串）；period 坏值仅丢键', () => {
    expect(parseAiConfig({ op: 'budget.create', name: 'B', category: '餐饮', amount: 2000, period: 'year' }))
      .toEqual({ op: 'budget.create', name: 'B', category: '餐饮', amount: 2000, period: 'year' })
    expect(parseAiConfig({ op: 'budget.create', name: 'B', category: '餐饮', amount: '99.5' }))
      .toEqual({ op: 'budget.create', name: 'B', category: '餐饮', amount: 99.5 })
    expect(parseAiConfig({ op: 'budget.create', name: 'B', category: '餐饮', amount: 200, period: 'week' }))
      .toEqual({ op: 'budget.create', name: 'B', category: '餐饮', amount: 200 })
    expect(parseAiConfig({ op: 'budget.create', name: 'B', amount: 200 })).toBeNull()
    expect(parseAiConfig({ op: 'budget.create', name: 'B', category: '餐饮', amount: 0 })).toBeNull()
  })

  it('savings.create：targetAmount 必填 >0；deadline 仅收 YYYY-MM-DD', () => {
    expect(parseAiConfig({ op: 'savings.create', name: 'S', targetAmount: 5000, deadline: '2026-12-31' }))
      .toEqual({ op: 'savings.create', name: 'S', targetAmount: 5000, deadline: '2026-12-31' })
    expect(parseAiConfig({ op: 'savings.create', name: 'S', targetAmount: 5000, deadline: '12月31日' }))
      .toEqual({ op: 'savings.create', name: 'S', targetAmount: 5000 })
    expect(parseAiConfig({ op: 'savings.create', name: 'S', targetAmount: 5000, deadline: '2026-02-31' }))
      .toEqual({ op: 'savings.create', name: 'S', targetAmount: 5000 })
    expect(parseAiConfig({ op: 'savings.create', name: 'S' })).toBeNull()
  })

  it('金额转换为分后必须仍为正安全整数，防止写入 0 分或溢出', () => {
    expect(parseAiConfig({ op: 'budget.create', name: 'B', category: '餐饮', amount: 0.001 })).toBeNull()
    expect(parseAiConfig({ op: 'savings.create', name: 'S', targetAmount: 0.001 })).toBeNull()
    expect(parseAiConfig({ op: 'budget.create', name: 'B', category: '餐饮', amount: 90_071_992_547_410 })).toBeNull()
    expect(parseAiConfig({ op: 'savings.create', name: 'S', targetAmount: 90_071_992_547_410 })).toBeNull()
  })

  it('default.book_set / default.account_set：仅 name', () => {
    expect(parseAiConfig({ op: 'default.book_set', name: '旅行' })).toEqual({ op: 'default.book_set', name: '旅行' })
    expect(parseAiConfig({ op: 'default.account_set', name: '现金' })).toEqual({ op: 'default.account_set', name: '现金' })
  })

  it('白名单外 op / 非对象 / name 缺失 → null（回落 chat）', () => {
    expect(parseAiConfig({ op: 'book.update', name: 'X' })).toBeNull()
    expect(parseAiConfig({ op: 'book.create' })).toBeNull()
    expect(parseAiConfig(null)).toBeNull()
    expect(parseAiConfig(undefined)).toBeNull()
    expect(parseAiConfig('book.create')).toBeNull()
    expect(parseAiConfig([{ op: 'book.create', name: 'X' }])).toBeNull()
  })

  it('parseAiInterpretOutput：intent=config 携带 config；config 坏形状 → 键在但 null', () => {
    const good = parseAiInterpretOutput({
      intent: 'config', type: '', amountCents: 0, category: '', note: '', bizDate: '', confidence: 0.9,
      reply: '好的，帮你建', config: { op: 'book.create', name: '旅行' },
    })
    expect(good.intent).toBe('config')
    expect(good.config).toEqual({ op: 'book.create', name: '旅行' })

    const missing = parseAiInterpretOutput({
      intent: 'config', reply: '好的', type: '', amountCents: 0, category: '', note: '', bizDate: '', confidence: 0.9,
    })
    expect(missing.intent).toBe('config')
    expect(missing.config).toBeNull()
  })
})

describe('resolveConfigTargets（名→id + 本地可执行性，决策 #6）', () => {
  const cfg = (partial: Partial<AiConfig> & { op: AiConfig['op']; name: string }): AiConfig => ({ ...partial })

  it('book.create：全新名 ok；同类重名 → 引导语不写', () => {
    expect(resolveConfigTargets(cfg({ op: 'book.create', name: '健身' }), resolveCtx())).toEqual({ ok: true, targets: {} })
    const dup = resolveConfigTargets(cfg({ op: 'book.create', name: '旅行' }), resolveCtx())
    expect(dup.ok).toBe(false)
    if (!dup.ok) expect(dup.message).toContain('旅行')
    // master 同名也拒（它是账本）
    expect(resolveConfigTargets(cfg({ op: 'book.create', name: '总账本' }), resolveCtx()).ok).toBe(false)
  })

  it('account.create：重名（trim+全等）→ 引导语', () => {
    expect(resolveConfigTargets(cfg({ op: 'account.create', name: '证券账户', accountType: 'expense' }), resolveCtx()).ok).toBe(true)
    expect(resolveConfigTargets(cfg({ op: 'account.create', name: ' 现金 ', accountType: 'income' }), resolveCtx()).ok).toBe(false)
  })

  it('budget.create：分类名解析 expense 口径 id；无 bookId / 分类不匹配 → 引导语', () => {
    const r = resolveConfigTargets(cfg({ op: 'budget.create', name: 'B', category: '餐饮', amount: 2000 }), resolveCtx())
    expect(r).toEqual({ ok: true, targets: { categoryId: 'c-food' } })
    // 二级分类名（早餐）也能命中
    expect(resolveConfigTargets(cfg({ op: 'budget.create', name: 'B', category: '早餐', amount: 200 }), resolveCtx()))
      .toEqual({ ok: true, targets: { categoryId: 'c-food-early' } })
    expect(resolveConfigTargets(cfg({ op: 'budget.create', name: 'B', category: '不存在的分类名', amount: 200 }), resolveCtx()).ok).toBe(false)
    // income 分类不走 expense 口径
    expect(resolveConfigTargets(cfg({ op: 'budget.create', name: 'B', category: '工资', amount: 200 }), resolveCtx()).ok).toBe(false)
    expect(resolveConfigTargets(cfg({ op: 'budget.create', name: 'B', category: '餐饮', amount: 200 }), resolveCtx({ bookId: null })).ok).toBe(false)
  })

  it('savings.create：deadline 在今天之前 → 引导语；今天/未来/无期限 ok', () => {
    expect(resolveConfigTargets(cfg({ op: 'savings.create', name: 'S', targetAmount: 5000, deadline: '2026-09-09' }), resolveCtx()).ok).toBe(false)
    expect(resolveConfigTargets(cfg({ op: 'savings.create', name: 'S', targetAmount: 5000, deadline: '2026-09-10' }), resolveCtx()).ok).toBe(true)
    expect(resolveConfigTargets(cfg({ op: 'savings.create', name: 'S', targetAmount: 5000 }), resolveCtx()).ok).toBe(true)
  })

  it('default.book_set：全等命中；重名取第一个；master 不可设默认；匹配不到 → 引导语带现有名单', () => {
    expect(resolveConfigTargets(cfg({ op: 'default.book_set', name: '我的账本' }), resolveCtx()))
      .toEqual({ ok: true, targets: { bookId: 'book-a' } })
    // 同名的两个非 master → 取第一个
    const dupCtx = resolveCtx({ books: [...cfgBooks, { id: 'book-b2', ownerId: 'o', name: '旅行', icon: '✈️', version: 1 }] })
    expect(resolveConfigTargets(cfg({ op: 'default.book_set', name: '旅行' }), dupCtx))
      .toEqual({ ok: true, targets: { bookId: 'book-b' } })
    // master 被排除 → 引导语带名单
    const r = resolveConfigTargets(cfg({ op: 'default.book_set', name: '总账本' }), resolveCtx())
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.message).toContain('我的账本')
    // 大小写敏感全等：英文场景匹配不到
    expect(resolveConfigTargets(cfg({ op: 'default.book_set', name: 'Travel' }), resolveCtx()).ok).toBe(false)
  })

  it('default.account_set：命中 id；匹配不到 → 引导语带账户名单', () => {
    expect(resolveConfigTargets(cfg({ op: 'default.account_set', name: '招商银行' }), resolveCtx()))
      .toEqual({ ok: true, targets: { accountId: 'acc-2' } })
    const r = resolveConfigTargets(cfg({ op: 'default.account_set', name: '支付宝' }), resolveCtx())
    expect(r.ok).toBe(false)
    if (!r.ok) {
      expect(r.message).toContain('现金')
      expect(r.message).toContain('招商银行')
    }
  })
})

describe('buildAiObjectNames（context 名单段，决策 #7）', () => {
  it('trim + ≤16 字 + 去重 + 空名剔除', () => {
    expect(buildAiObjectNames([' 旅行 ', '旅行', '', '   ', '超长超长超长超长超长超长超长超长超长超长']))
      .toEqual([{ name: '旅行' }, { name: '超长超长超长超长超长超长超长超长' }])
  })
  it('cap 30 条', () => {
    const many = Array.from({ length: 50 }, (_, i) => `账本${i}`)
    expect(buildAiObjectNames(many)).toHaveLength(30)
  })
  it('空输入 → 空数组（调用方折 null 段）', () => {
    expect(buildAiObjectNames([])).toEqual([])
  })
})

describe('deadlineToSavingsPeriod（deadline → native 周/月期限）', () => {
  const d2p = deadlineToSavingsPeriod
  it('≤84 天给「周」（向上取整）', () => {
    expect(d2p('2026-09-17', '2026-09-10')).toEqual({ value: 1, unit: 'week' })
    expect(d2p('2026-10-01', '2026-09-10')).toEqual({ value: 3, unit: 'week' })   // 21 天
    expect(d2p('2026-10-03', '2026-09-10')).toEqual({ value: 4, unit: 'week' })   // 23 天 → 4 周
    expect(d2p('2026-12-03', '2026-09-10')).toEqual({ value: 12, unit: 'week' })  // 84 天
  })
  it('>84 天给「月」（约 30.44 天折算，四舍五入）', () => {
    expect(d2p('2026-12-10', '2026-09-10')).toEqual({ value: 3, unit: 'month' })
    expect(d2p('2027-09-10', '2026-09-10')).toEqual({ value: 12, unit: 'month' })
  })
  it('月上限 120 钳制；过期防御给最小周期限', () => {
    expect(d2p('2060-01-01', '2026-09-10')).toEqual({ value: 120, unit: 'month' })
    expect(d2p('2026-09-01', '2026-09-10')).toEqual({ value: 1, unit: 'week' })
  })
})

describe('buildConfigPreview（预览 rows，决策 #8）', () => {
  it('book.create：操作+名称（+图标可选行）', () => {
    const p = buildConfigPreview({ op: 'book.create', name: '旅行', icon: '✈️' }, '2026-09-10')
    expect(p.title).toBe('⚙️ 新建账本')
    expect(p.rows).toEqual([
      { label: '操作', value: '新建账本' },
      { label: '名称', value: '旅行' },
      { label: '图标', value: '✈️' },
    ])
    expect(buildConfigPreview({ op: 'book.create', name: '旅行' }, '2026-09-10').rows).toHaveLength(2)
  })

  it('account.create：类型折中文标签；initBalance 明示「补记一笔」', () => {
    const p = buildConfigPreview({ op: 'account.create', name: '工资卡', accountType: 'income', initBalance: 5000 }, '2026-09-10')
    expect(p.rows).toEqual([
      { label: '操作', value: '新建账户' },
      { label: '名称', value: '工资卡' },
      { label: '类型', value: '收入账户' },
      { label: '期初余额', value: '¥5000.00（创建后请补记一笔收入）' },
    ])
  })

  it('budget.create：周期锚定创建当期；不含 name 行（实体无名列）', () => {
    const p = buildConfigPreview({ op: 'budget.create', name: 'B', category: '餐饮', amount: 2000 }, '2026-09-10')
    expect(p.rows).toEqual([
      { label: '操作', value: '新建预算' },
      { label: '分类', value: '餐饮' },
      { label: '金额', value: '¥2000.00' },
      { label: '周期', value: '月度（2026-09）' },
    ])
    const y = buildConfigPreview({ op: 'budget.create', name: 'B', category: '餐饮', amount: 2000, period: 'year' }, '2026-09-10')
    expect(y.rows[3].value).toBe('年度（2026 年）')
  })

  it('savings.create：deadline 行附换算期限', () => {
    const p = buildConfigPreview({ op: 'savings.create', name: 'S', targetAmount: 3000, deadline: '2026-12-10' }, '2026-09-10')
    expect(p.rows[2]).toEqual({ label: '目标金额', value: '¥3000.00' })
    expect(p.rows[3].value).toContain('2026-12-10 前')
    expect(p.rows[3].value).toContain('3个月')
  })

  it('default.*：两行（操作 + 目标名）', () => {
    expect(buildConfigPreview({ op: 'default.book_set', name: '旅行' }, '2026-09-10').rows).toEqual([
      { label: '操作', value: '设为默认账本' },
      { label: '目标', value: '旅行' },
    ])
    expect(buildConfigPreview({ op: 'default.account_set', name: '现金' }, '2026-09-10').title).toBe('⚙️ 设置默认账户')
  })
})
