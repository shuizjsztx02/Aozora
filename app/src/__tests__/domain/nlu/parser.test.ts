import { describe, it, expect, beforeEach } from 'vitest'
import { parseInput, _resetProposalCounter } from '../../../domain/nlu/parser'
import { validateProposal } from '../../../domain/action-proposal'
import type { ParseContext } from '../../../domain/nlu/parser'
import type { Category, Account } from '../../../domain/types'

// 固定时间：2026-09-04 周五 12:00 CST
const NOW = Date.UTC(2026, 8, 4, 4, 0, 0)
const TZ = 'Asia/Shanghai'

const categories: Category[] = [
  { id: 'u-cat-travel', ownerId: 'u1', name: '旅行', parentId: null, kind: 'expense', archivedAt: null, version: 1 },
  { id: 'u-cat-pet', ownerId: 'u1', name: '宠物', parentId: null, kind: 'expense', archivedAt: null, version: 1 },
]

const accounts: Account[] = []

function makeCtx(): ParseContext {
  return { now: NOW, timezone: TZ, categories, accounts }
}

// 关键不变量：parseInput 永不抛异常，且输出必过 validateProposal
function assertValidProposal(input: string, ctx: ParseContext = makeCtx()) {
  const proposal = parseInput(input, ctx)
  const validation = validateProposal(proposal)
  if (!validation.valid) {
    throw new Error(`Invalid proposal for input "${input}": ${JSON.stringify(validation.errors)}`)
  }
  return proposal
}

beforeEach(() => {
  _resetProposalCounter()
})

// ─── 核心用例：高置信 add_tx ───

describe('parseInput — 核心记账（高置信）', () => {
  it('午餐 35 → add_tx expense ¥35 餐饮', () => {
    const p = assertValidProposal('午餐 35')
    expect(p.action).toBe('add_tx')
    expect(p.confidence).toBeGreaterThan(0.7)
    expect(p.transactions).toHaveLength(1)
    const tx = p.transactions![0]
    expect(tx.type).toBe('expense')
    expect(tx.amountCents).toBe(3500)
    expect(tx.categoryId).toBe('cat-food')
    expect(tx.bizDate).toBe('2026-09-04')
  })

  it('打车 ¥25 → 交通', () => {
    const p = assertValidProposal('打车 ¥25')
    expect(p.transactions![0].categoryId).toBe('cat-transport')
    expect(p.transactions![0].amountCents).toBe(2500)
  })

  it('工资到了 8000 → income', () => {
    const p = assertValidProposal('工资到了 8000')
    expect(p.action).toBe('add_tx')
    expect(p.transactions![0].type).toBe('income')
    expect(p.transactions![0].amountCents).toBe(800000)
    expect(p.transactions![0].categoryId).toBe('cat-salary')
  })

  it('昨天午餐 28.5 → 昨天日期', () => {
    const p = assertValidProposal('昨天午餐 28.5')
    expect(p.transactions![0].bizDate).toBe('2026-09-03')
    expect(p.transactions![0].amountCents).toBe(2850)
  })

  it('用户分类：旅行 3000', () => {
    const p = assertValidProposal('旅行 3000')
    expect(p.transactions![0].categoryId).toBe('u-cat-travel')
  })
})

// ─── 边界用例 ───

describe('parseInput — 边界', () => {
  it('只有金额（无分类）→ 低置信，无 categoryId', () => {
    const p = assertValidProposal('35')
    expect(p.action).toBe('add_tx')
    expect(p.confidence).toBeLessThan(0.9)
    expect(p.transactions![0].categoryId).toBeUndefined()
  })

  it('跨月边界：9月1日昨天是 8月31日', () => {
    const aug31Ctx: ParseContext = {
      now: Date.UTC(2026, 8, 1, 4, 0, 0), // 2026-09-01 12:00 CST
      timezone: TZ,
      categories,
      accounts,
    }
    const p = assertValidProposal('昨天午餐 20', aug31Ctx)
    expect(p.transactions![0].bizDate).toBe('2026-08-31')
  })

  it('中文逗号千分位：¥1，200', () => {
    const p = assertValidProposal('工资 ¥1，200')
    expect(p.transactions![0].amountCents).toBe(120000)
  })
})

// ─── 负面用例（非法/模糊输入 → 降级 chat） ───

describe('parseInput — 降级为 chat', () => {
  it('无金额：无法记账，降级 chat', () => {
    const p = assertValidProposal('今天天气真好')
    expect(p.action).toBe('chat')
  })

  it('空输入 → chat', () => {
    const p = assertValidProposal('')
    expect(p.action).toBe('chat')
  })

  it('纯日期（无金额）→ chat', () => {
    const p = assertValidProposal('9月3号')
    expect(p.action).toBe('chat')
  })

  it('null/undefined → chat', () => {
    const p = assertValidProposal(null as unknown as string)
    expect(p.action).toBe('chat')
  })
})

// ─── 查询意图 ───

describe('parseInput — 查询意图', () => {
  it('本月花了多少 → query', () => {
    const p = assertValidProposal('本月花了多少')
    expect(p.action).toBe('query')
  })

  it('统计报表 → query', () => {
    const p = assertValidProposal('统计报表')
    expect(p.action).toBe('query')
  })
})

// ─── 契约闸不变量 ───

describe('parseInput — 契约闸不变量', () => {
  const corpus = [
    '午餐 35',
    '打车 ¥25',
    '工资到了 8000',
    '昨天午餐 28.5',
    '35',
    '今天天气真好',
    '',
    '9月3号',
    '本月花了多少',
    '工资 ¥1，200',
    '旅行 3000',
    '买咖啡 18.5',
    '退款 50',
    '房租 3000',
  ]

  for (const input of corpus) {
    it(`"${input}" → 输出必过 validateProposal`, () => {
      const p = parseInput(input, makeCtx())
      const result = validateProposal(p)
      expect(result.valid).toBe(true)
    })
  }
})

// ─── 幂等性 ───

describe('parseInput — 幂等性（除 proposalId 外）', () => {
  it('同输入 + 同上下文 → 同结构（忽略 proposalId）', () => {
    const input = '午餐 35'
    const ctx = makeCtx()
    const a = parseInput(input, ctx)
    const b = parseInput(input, ctx)
    // 比较除 proposalId 外的所有字段
    const stripId = (p: typeof a) => {
      const { proposalId: _proposalId, ...rest } = p
      return rest
    }
    expect(stripId(a)).toEqual(stripId(b))
  })
})

// ─── 永不抛异常 ───

describe('parseInput — 永不抛异常', () => {
  const pathological = [
    '',
    '  ',
    'null',
    'undefined',
    '¥',
    '-50',
    '¥-12.5',
    '2024年',
    '2024-01-01',
    '啊啊啊啊',
    '💰💰💰',
    'a'.repeat(10000),
  ]

  for (const input of pathological) {
    it(`"${input.slice(0, 20)}${input.length > 20 ? '...' : ''}" 不抛异常`, () => {
      expect(() => parseInput(input, makeCtx())).not.toThrow()
    })
  }
})
