import { describe, it, expect } from 'vitest'
import { validateProposal } from '../../domain/action-proposal'
import Ajv from 'ajv'
import schema from '../../../../contracts/action-proposal/schema.json'
import { parseBizDate } from '../../domain/date'

function schemaValidator() {
  return new Ajv({ allErrors: true }).addFormat('date', (value: string) => {
    try { parseBizDate(value); return true } catch { return false }
  }).compile(schema)
}

// ─── 契约 fixture（唯一事实源：contracts/action-proposal/fixtures/） ───

import validAddTx from '../../../../contracts/action-proposal/fixtures/valid-add-tx.json'
import validAddTxBatch from '../../../../contracts/action-proposal/fixtures/valid-add-tx-batch.json'
import validQuery from '../../../../contracts/action-proposal/fixtures/valid-query.json'
import validChat from '../../../../contracts/action-proposal/fixtures/valid-chat.json'
import validCreateCategory from '../../../../contracts/action-proposal/fixtures/valid-create-category.json'
import validReport from '../../../../contracts/action-proposal/fixtures/valid-report.json'
import validConfig from '../../../../contracts/action-proposal/fixtures/valid-config.json'
import invalidMissingAction from '../../../../contracts/action-proposal/fixtures/invalid-missing-action.json'
import invalidNegativeAmount from '../../../../contracts/action-proposal/fixtures/invalid-negative-amount.json'
import invalidFloatAmount from '../../../../contracts/action-proposal/fixtures/invalid-float-amount.json'
import invalidZeroAmount from '../../../../contracts/action-proposal/fixtures/invalid-zero-amount.json'
import invalidBadDate from '../../../../contracts/action-proposal/fixtures/invalid-bad-date.json'
import invalidImpossibleDate from '../../../../contracts/action-proposal/fixtures/invalid-impossible-date.json'
import invalidMissingTransactions from '../../../../contracts/action-proposal/fixtures/invalid-missing-transactions.json'
import invalidEmptyTransactions from '../../../../contracts/action-proposal/fixtures/invalid-empty-transactions.json'
import invalidConfidenceRange from '../../../../contracts/action-proposal/fixtures/invalid-confidence-range.json'

const validFixtures: Array<{ name: string; fixture: unknown }> = [
  { name: 'add_tx', fixture: validAddTx },
  { name: 'add_tx_batch', fixture: validAddTxBatch },
  { name: 'query', fixture: validQuery },
  { name: 'chat', fixture: validChat },
  { name: 'create_category', fixture: validCreateCategory },
  { name: 'report', fixture: validReport },
  { name: 'config', fixture: validConfig },
]

const invalidFixtures: Array<{ name: string; fixture: unknown; errorPath: string }> = [
  { name: 'missing-action', fixture: invalidMissingAction, errorPath: 'action' },
  { name: 'negative-amount', fixture: invalidNegativeAmount, errorPath: 'transactions[0].amountCents' },
  { name: 'float-amount', fixture: invalidFloatAmount, errorPath: 'transactions[0].amountCents' },
  { name: 'zero-amount', fixture: invalidZeroAmount, errorPath: 'transactions[0].amountCents' },
  { name: 'bad-date-format', fixture: invalidBadDate, errorPath: 'transactions[0].bizDate' },
  { name: 'impossible-date', fixture: invalidImpossibleDate, errorPath: 'transactions[0].bizDate' },
  { name: 'missing-transactions', fixture: invalidMissingTransactions, errorPath: 'transactions' },
  { name: 'empty-transactions', fixture: invalidEmptyTransactions, errorPath: 'transactions' },
  { name: 'confidence-range', fixture: invalidConfidenceRange, errorPath: 'confidence' },
]

// ─── 契约 fixture 通过 TS 校验器 ───

describe('ActionProposal — valid fixtures pass validation', () => {
  for (const { name, fixture } of validFixtures) {
    it(`${name} proposal passes validation`, () => {
      const result = validateProposal(fixture)
      expect(result.valid).toBe(true)
      expect(result.errors).toEqual([])
    })
  }
})

describe('ActionProposal — invalid fixtures rejected with expected error path', () => {
  for (const { name, fixture, errorPath } of invalidFixtures) {
    it(`${name} fixture rejected at "${errorPath}"`, () => {
      const result = validateProposal(fixture)
      expect(result.valid).toBe(false)
      expect(result.errors.some((e) => e.path === errorPath)).toBe(true)
    })
  }
})

// ─── JSON Schema 与 TS 校验器：同输入同结论（R-M1-07 契约测试） ───

describe('ActionProposal 契约 — JSON Schema 与 TS 校验器同输入同结论', () => {
  const validateSchema = schemaValidator()
  const cases = [
    ...validFixtures.map(({ name, fixture }) => ({ name, fixture, expected: true })),
    ...invalidFixtures.map(({ name, fixture }) => ({ name, fixture, expected: false })),
  ]

  for (const { name, fixture, expected } of cases) {
    it(`${name}: schema=${expected} 且校验器=${expected}`, () => {
      expect(validateSchema(fixture)).toBe(expected)
      expect(validateProposal(fixture).valid).toBe(expected)
    })
  }
})

// ─── Non-object inputs（JSON fixture 无法表达，内联覆盖） ───

describe('R-M1-20 契约漂移回归', () => {
  const validateSchema = schemaValidator()
  const tx = validAddTx.transactions[0]
  const cases = [
    { ...validChat, unexpected: true },
    { ...validAddTx, transactions: [{ ...tx, unexpected: true }] },
    { ...validChat, transactions: [] },
    { ...validAddTx, transactions: [{ ...tx, amountCents: Number.MAX_SAFE_INTEGER + 1 }] },
    ...['2026-00-01', '2026-13-01', '2027-02-29', '2026-02-30'].map(bizDate => ({
      ...validAddTx, transactions: [{ ...tx, bizDate }],
    })),
  ]
  it.each(cases)('拒绝非法提案 %#', proposal => {
    expect(validateSchema(proposal)).toBe(false)
    expect(validateProposal(proposal).valid).toBe(false)
  })
})

describe('R-M1-30 执行前授权上下文', () => {
  const context = { categoryIds: new Set(['food']), accountIds: new Set(['cash']) }
  it('拒绝跨账本分类、未授权账户和未解析分类', () => {
    const base = validAddTx.transactions[0]
    for (const item of [
      { ...base, categoryId: 'foreign' },
      { ...base, categoryId: 'food', accountId: 'foreign' },
      { type: 'expense', amountCents: 100, bizDate: '2026-09-04', note: '', categoryPath: ['餐饮'] },
    ]) {
      expect(validateProposal({ ...validAddTx, transactions: [item] }, context).valid).toBe(false)
    }
  })

  it('授权范围内的提案通过且输入不变', () => {
    const proposal = { ...validAddTx, transactions: [{
      ...validAddTx.transactions[0], categoryId: 'food', accountId: 'cash',
    }] }
    const original = JSON.stringify(proposal)
    expect(validateProposal(proposal, context).valid).toBe(true)
    expect(JSON.stringify(proposal)).toBe(original)
  })
})

describe('ActionProposal — non-object inputs rejected', () => {
  it('null', () => {
    expect(validateProposal(null).valid).toBe(false)
  })

  it('undefined', () => {
    expect(validateProposal(undefined).valid).toBe(false)
  })

  it('string', () => {
    expect(validateProposal('hello').valid).toBe(false)
  })

  it('number', () => {
    expect(validateProposal(42).valid).toBe(false)
  })

  it('array', () => {
    expect(validateProposal([1, 2, 3]).valid).toBe(false)
  })
})

// ─── 内联边界（fixture 之外的补充） ───

describe('ActionProposal — inline edge cases', () => {
  it('invalid action value', () => {
    const result = validateProposal({ ...validChat, action: 'delete_everything' })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.path === 'action')).toBe(true)
  })

  it('add_tx_batch without transactions', () => {
    const result = validateProposal({
      proposalId: 'prop-bad',
      action: 'add_tx_batch',
      confidence: 0.9,
      requiresConfirmation: false,
      message: 'no tx',
      warnings: [],
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.path === 'transactions')).toBe(true)
  })

  it('confidence NaN', () => {
    const result = validateProposal({ ...validChat, confidence: Number.NaN })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.path === 'confidence')).toBe(true)
  })

  it('missing proposalId', () => {
    const result = validateProposal({
      action: 'chat',
      confidence: 0.9,
      requiresConfirmation: false,
      message: 'no id',
      warnings: [],
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.path === 'proposalId')).toBe(true)
  })

  it('empty proposalId', () => {
    const result = validateProposal({ ...validChat, proposalId: '' })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.path === 'proposalId')).toBe(true)
  })

  it('missing message', () => {
    const result = validateProposal({
      proposalId: 'prop-bad',
      action: 'chat',
      confidence: 0.9,
      requiresConfirmation: false,
      warnings: [],
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.path === 'message')).toBe(true)
  })

  it('missing warnings', () => {
    const result = validateProposal({
      proposalId: 'prop-bad',
      action: 'chat',
      confidence: 0.9,
      requiresConfirmation: false,
      message: 'no warnings',
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.path === 'warnings')).toBe(true)
  })

  it('missing requiresConfirmation', () => {
    const result = validateProposal({
      proposalId: 'prop-bad',
      action: 'chat',
      confidence: 0.9,
      message: 'no rc',
      warnings: [],
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.path === 'requiresConfirmation')).toBe(true)
  })

  it('invalid transaction type', () => {
    const result = validateProposal({
      ...validAddTx,
      transactions: [{ type: 'transfer', amountCents: 1000, bizDate: '2026-09-03', note: '转账' }],
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.path.includes('type'))).toBe(true)
  })

  it('missing bizDate in transaction', () => {
    const result = validateProposal({
      ...validAddTx,
      transactions: [{ type: 'expense', amountCents: 1000, note: 'no date' }],
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.path.includes('bizDate'))).toBe(true)
  })

  it('missing note in transaction', () => {
    const result = validateProposal({
      ...validAddTx,
      transactions: [{ type: 'expense', amountCents: 1000, bizDate: '2026-09-03' }],
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.path.includes('note'))).toBe(true)
  })
})

// ─── Confidence boundaries ───

describe('ActionProposal — confidence boundaries', () => {
  it('confidence = 0 is valid', () => {
    expect(validateProposal({ ...validChat, confidence: 0 }).valid).toBe(true)
  })

  it('confidence = 1 is valid', () => {
    expect(validateProposal({ ...validChat, confidence: 1 }).valid).toBe(true)
  })

  it('confidence < 0 is invalid', () => {
    expect(validateProposal({ ...validChat, confidence: -0.1 }).valid).toBe(false)
  })
})

// ─── Amount boundaries ───

describe('ActionProposal — amountCents boundaries', () => {
  it('amountCents = 1 is valid (minimum)', () => {
    const result = validateProposal({
      ...validAddTx,
      transactions: [{ type: 'expense', amountCents: 1, bizDate: '2026-09-03', note: '最小' }],
    })
    expect(result.valid).toBe(true)
  })

  it('amountCents = MAX_SAFE_INTEGER is valid', () => {
    const result = validateProposal({
      ...validAddTx,
      transactions: [{ type: 'expense', amountCents: Number.MAX_SAFE_INTEGER, bizDate: '2026-09-03', note: '最大' }],
    })
    expect(result.valid).toBe(true)
  })

  it('amountCents = MAX_SAFE_INTEGER + 1 is invalid', () => {
    const result = validateProposal({
      ...validAddTx,
      transactions: [{ type: 'expense', amountCents: Number.MAX_SAFE_INTEGER + 1, bizDate: '2026-09-03', note: '溢出' }],
    })
    expect(result.valid).toBe(false)
  })

  it('amountCents = Infinity is invalid', () => {
    const result = validateProposal({
      ...validAddTx,
      transactions: [{ type: 'expense', amountCents: Number.POSITIVE_INFINITY, bizDate: '2026-09-03', note: '无穷' }],
    })
    expect(result.valid).toBe(false)
  })
})

// ─── Pure function (no side effects) ───

describe('ActionProposal — validator is pure', () => {
  it('same input always produces same output', () => {
    const r1 = validateProposal(validAddTx)
    const r2 = validateProposal(validAddTx)
    const r3 = validateProposal(validAddTx)
    expect(r1).toEqual(r2)
    expect(r2).toEqual(r3)
  })

  it('does not mutate input', () => {
    const frozen = JSON.parse(JSON.stringify(validAddTx))
    validateProposal(validAddTx)
    expect(validAddTx).toEqual(frozen)
  })
})
