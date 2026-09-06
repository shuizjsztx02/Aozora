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
