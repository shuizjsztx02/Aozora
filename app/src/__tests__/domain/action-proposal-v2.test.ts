import { describe, it, expect } from 'vitest'
import { V2_OPS, validateProposal, validateProposalAny, validateProposalV2 } from '../../domain/action-proposal'
import Ajv from 'ajv'
import schemaV2 from '../../../../contracts/action-proposal/schema-v2.json'

function schemaV2Validator() {
  return new Ajv({ allErrors: true }).compile(schemaV2)
}

// ─── v2 契约 fixture（唯一事实源：contracts/action-proposal/fixtures/v2/） ───

import validTransactionAdd from '../../../../contracts/action-proposal/fixtures/v2/valid-transaction-add.json'
import validTransactionAddBatch from '../../../../contracts/action-proposal/fixtures/v2/valid-transaction-add-batch.json'
import validBudgetSetCategory from '../../../../contracts/action-proposal/fixtures/v2/valid-budget-set-category.json'
import validMetaSet from '../../../../contracts/action-proposal/fixtures/v2/valid-meta-set.json'
import validSavingsCreate from '../../../../contracts/action-proposal/fixtures/v2/valid-savings-create.json'
import validSyncTrigger from '../../../../contracts/action-proposal/fixtures/v2/valid-sync-trigger.json'
import validAccountDelete from '../../../../contracts/action-proposal/fixtures/v2/valid-account-delete.json'
import invalidMissingContractVersion from '../../../../contracts/action-proposal/fixtures/v2/invalid-missing-contract-version.json'
import invalidContractVersion1 from '../../../../contracts/action-proposal/fixtures/v2/invalid-contract-version-1.json'
import invalidUnknownOp from '../../../../contracts/action-proposal/fixtures/v2/invalid-unknown-op.json'
import invalidV1ActionInV2 from '../../../../contracts/action-proposal/fixtures/v2/invalid-v1-action-in-v2.json'
import invalidMissingPayload from '../../../../contracts/action-proposal/fixtures/v2/invalid-missing-payload.json'
import invalidPayloadNotObject from '../../../../contracts/action-proposal/fixtures/v2/invalid-payload-not-object.json'
import invalidUnknownField from '../../../../contracts/action-proposal/fixtures/v2/invalid-unknown-field.json'
import invalidConfidenceRange from '../../../../contracts/action-proposal/fixtures/v2/invalid-confidence-range.json'
import invalidEmptyProposalId from '../../../../contracts/action-proposal/fixtures/v2/invalid-empty-proposal-id.json'
import invalidMissingWarnings from '../../../../contracts/action-proposal/fixtures/v2/invalid-missing-warnings.json'

// v1 fixture：用来钉住「v1 提案不会被误判成 v2」这条边界
import validAddTx from '../../../../contracts/action-proposal/fixtures/valid-add-tx.json'

const validFixtures: Array<{ name: string; fixture: unknown }> = [
  { name: 'transaction_add', fixture: validTransactionAdd },
  { name: 'transaction_add(batch)', fixture: validTransactionAddBatch },
  { name: 'budget_set_category', fixture: validBudgetSetCategory },
  { name: 'meta_set', fixture: validMetaSet },
  { name: 'savings_create', fixture: validSavingsCreate },
  { name: 'sync_trigger(空 payload)', fixture: validSyncTrigger },
  { name: 'account_delete', fixture: validAccountDelete },
]

const invalidFixtures: Array<{ name: string; fixture: unknown; errorPath: string }> = [
  { name: 'missing-contract-version', fixture: invalidMissingContractVersion, errorPath: 'contractVersion' },
  { name: 'contract-version-1', fixture: invalidContractVersion1, errorPath: 'contractVersion' },
  { name: 'unknown-op', fixture: invalidUnknownOp, errorPath: 'action' },
  { name: 'v1-action-in-v2', fixture: invalidV1ActionInV2, errorPath: 'action' },
  { name: 'missing-payload', fixture: invalidMissingPayload, errorPath: 'payload' },
  { name: 'payload-not-object', fixture: invalidPayloadNotObject, errorPath: 'payload' },
  { name: 'unknown-field', fixture: invalidUnknownField, errorPath: 'transactions' },
  { name: 'confidence-range', fixture: invalidConfidenceRange, errorPath: 'confidence' },
  { name: 'empty-proposal-id', fixture: invalidEmptyProposalId, errorPath: 'proposalId' },
  { name: 'missing-warnings', fixture: invalidMissingWarnings, errorPath: 'warnings' },
]

// ─── 契约 fixture 通过 v2 校验器 ───

describe('ActionProposal v2 — valid fixtures pass validation', () => {
  for (const { name, fixture } of validFixtures) {
    it(`${name} proposal passes validation`, () => {
      const result = validateProposalV2(fixture)
      expect(result.valid).toBe(true)
      expect(result.errors).toEqual([])
    })
  }
})

describe('ActionProposal v2 — invalid fixtures rejected with expected error path', () => {
  for (const { name, fixture, errorPath } of invalidFixtures) {
    it(`${name} fixture rejected at "${errorPath}"`, () => {
      const result = validateProposalV2(fixture)
      expect(result.valid).toBe(false)
      expect(result.errors.some((e) => e.path === errorPath)).toBe(true)
    })
  }
})

// ─── JSON Schema 与 TS 校验器：同输入同结论（三方一致性的 TS 半边） ───

describe('ActionProposal v2 契约 — JSON Schema 与 TS 校验器同输入同结论', () => {
  const validateSchema = schemaV2Validator()
  const cases = [
    ...validFixtures.map(({ name, fixture }) => ({ name, fixture, expected: true })),
    ...invalidFixtures.map(({ name, fixture }) => ({ name, fixture, expected: false })),
  ]

  for (const { name, fixture, expected } of cases) {
    it(`${name}: schema=${expected} 且校验器=${expected}`, () => {
      expect(validateSchema(fixture)).toBe(expected)
      expect(validateProposalV2(fixture).valid).toBe(expected)
    })
  }
})

// ─── 双读：车道判定 ───

describe('ActionProposal — 双读入口按 contractVersion 选车道', () => {
  it('v1 提案走 v1 车道，结论与 validateProposal 逐字一致', () => {
    const result = validateProposalAny(validAddTx)
    expect(result.contractVersion).toBe(1)
    expect(result.valid).toBe(true)
    expect(result).toEqual({ ...validateProposal(validAddTx), contractVersion: 1 })
  })

  it('v2 提案走 v2 车道', () => {
    expect(validateProposalAny(validMetaSet).contractVersion).toBe(2)
    expect(validateProposalAny(validMetaSet).valid).toBe(true)
  })

  it('v1 动作名不会被读成 v2', () => {
    expect(validateProposalAny(invalidV1ActionInV2).contractVersion).toBe(2)
    expect(validateProposalAny(invalidV1ActionInV2).valid).toBe(false)
  })

  it('非对象输入没有车道', () => {
    for (const bad of [null, undefined, 42, 'hello', [1, 2, 3]]) {
      expect(validateProposalAny(bad).valid).toBe(false)
      expect(validateProposalAny(bad).contractVersion).toBeNull()
    }
  })
})

// ─── op 注册表 ───

describe('ActionProposal v2 — op 注册表', () => {
  it('注册表来自 schema 枚举，fixture 用到的 op 全在其中', () => {
    expect(V2_OPS.size).toBe(schemaV2.properties.action.enum.length)
    for (const { fixture } of validFixtures) {
      expect(V2_OPS.has((fixture as { action: string }).action)).toBe(true)
    }
  })

  it('v1 动作名不是 v2 op 成员', () => {
    for (const v1Action of ['add_tx', 'add_tx_batch', 'query', 'report', 'create_category', 'config', 'chat']) {
      expect(V2_OPS.has(v1Action)).toBe(false)
    }
  })

  it('v1 形状的提案（无 contractVersion）在 v2 车道上被拒', () => {
    const v1Shaped = { ...validAddTx, action: 'transaction_add' }
    expect(validateProposalV2(v1Shaped).valid).toBe(false)
    expect(validateProposalV2(v1Shaped).errors.some((e) => e.path === 'contractVersion')).toBe(true)
  })
})

// ─── 内联边界（fixture 之外的补充） ───

describe('ActionProposal v2 — inline edge cases', () => {
  it('payload 为 null 被拒', () => {
    const result = validateProposalV2({ ...validSyncTrigger, payload: null })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.path === 'payload')).toBe(true)
  })

  it('proposalId 超 64 字符被拒', () => {
    const result = validateProposalV2({ ...validSyncTrigger, proposalId: 'x'.repeat(65) })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.path === 'proposalId')).toBe(true)
  })

  it('confidence 边界 0 与 1 合法', () => {
    expect(validateProposalV2({ ...validSyncTrigger, confidence: 0 }).valid).toBe(true)
    expect(validateProposalV2({ ...validSyncTrigger, confidence: 1 }).valid).toBe(true)
  })

  it('confidence NaN 被拒', () => {
    const result = validateProposalV2({ ...validSyncTrigger, confidence: Number.NaN })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.path === 'confidence')).toBe(true)
  })

  it('warnings 含非字符串被拒', () => {
    const result = validateProposalV2({ ...validSyncTrigger, warnings: ['ok', 7] })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.path === 'warnings')).toBe(true)
  })

  it('requiresConfirmation 非布尔被拒', () => {
    const result = validateProposalV2({ ...validSyncTrigger, requiresConfirmation: 'yes' })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.path === 'requiresConfirmation')).toBe(true)
  })

  it('payload 内部不做校验——op 的字段形状归工具注册表', () => {
    const result = validateProposalV2({ ...validSyncTrigger, payload: { anything: [1, { deep: true }] } })
    expect(result.valid).toBe(true)
  })

  it('纯函数：同输入同结论且不改输入', () => {
    const frozen = JSON.parse(JSON.stringify(validTransactionAdd))
    expect(validateProposalV2(validTransactionAdd)).toEqual(validateProposalV2(validTransactionAdd))
    expect(validTransactionAdd).toEqual(frozen)
  })
})