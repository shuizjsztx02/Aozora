/**
 * 尾卡（R-PROD-31 / 37）：**v2 车道的客户端面**。
 *
 * 三条守护，各自对应一个具体的失效形态：
 *
 * 1. **车道判定**（`isV2Proposal`）——判错的后果是 v2 提案掉进 v1 的写路径，
 *    而那条路径读 `transactions`（v2 没有），结果是**静默什么都不写**。
 * 2. **设备分派表齐全**——服务端 `tools.DEVICE_EXECUTED_OPS` 里的每个 op 都必须
 *    在客户端有落点。少一个就是 R-PROD-37 记的形态：卡片点下去、回执说成功、
 *    实际什么都没发生（`meta_set` 曾经就是这样）。
 * 3. **参数形状**——分派函数的入参形状必须与 `tools.py` 里各工具体产出的 payload
 *    逐字对应（`reminder_set` 的 enabled/hour/minute 直接递给桥命令，中间不翻译）。
 *
 * 端到端（真卡片 + 真执行 + 真落库）在模拟器走查，不在单测里。
 */
import { describe, it, expect, vi } from 'vitest'
import { isV2Proposal } from '../../domain/action-proposal'
import { DEVICE_OP_DISPATCH } from '../../features/assistant/use-assistant'
import type { ActionProposal, ActionProposalV2 } from '../../domain/action-proposal'

const v1 = (): ActionProposal => ({
  proposalId: 'p1', action: 'add_tx', confidence: 0.9,
  requiresConfirmation: true, message: '记一笔', warnings: [],
  transactions: [{ type: 'expense', amountCents: 100, categoryId: 'c1', bizDate: '2026-09-20', note: '' }],
})

const v2 = (action: string, payload: Record<string, unknown> = {}): ActionProposalV2 => ({
  contractVersion: 2, proposalId: 'p2', action, confidence: 0.9,
  requiresConfirmation: true, message: '做点什么', warnings: [], payload,
})

describe('尾卡 · 车道判定（isV2Proposal）', () => {
  it('v1 提案没有 contractVersion → 判 v1', () => {
    expect(isV2Proposal(v1())).toBe(false)
  })

  it('v2 提案有 contractVersion: 2 → 判 v2', () => {
    expect(isV2Proposal(v2('budget_set'))).toBe(true)
  })

  it('**显式写成 1 的提案不是 v2**——车道判据是「等于 2」而不是「字段在场」', () => {
    const fake = { ...v1(), contractVersion: 1 } as unknown as ActionProposal
    expect(isV2Proposal(fake)).toBe(false)
  })
})

describe('尾卡 · 设备执行分派表（R-PROD-37）', () => {
  /**
   * 服务端 `tools.DEVICE_EXECUTED_OPS` 的同构副本。
   *
   * **它是硬编码的，且必须硬编码**：客户端拿不到服务端那份常量（不跨进程）。
   * 代价是新增设备 op 时两边都要改——所以这条用例就是那个「两边」的检查点：
   * 服务端加了 op，这里没有 → 用例红，提醒补分派（否则用户看到的是
   * 「确认后什么都没发生」）。
   */
  const SERVER_DEVICE_OPS = ['persona_switch', 'consent_record', 'sync_trigger', 'reminder_set']

  it('服务端每一个设备执行 op 都有客户端分派（少一个 = 静默无效）', () => {
    for (const op of SERVER_DEVICE_OPS) {
      expect(DEVICE_OP_DISPATCH[op], `缺 ${op} 的分派`).toBeTypeOf('function')
    }
  })

  it('分派表里**没有多出**服务端不认的 op（多一个是死代码）', () => {
    expect(Object.keys(DEVICE_OP_DISPATCH).sort()).toEqual([...SERVER_DEVICE_OPS].sort())
  })

  it('persona_switch → meta.set(active_persona_id)（角色指针是设备本地键）', async () => {
    const b = { setMeta: vi.fn().mockResolvedValue({}), recordConsent: vi.fn(), triggerSync: vi.fn(), setReminder: vi.fn() }
    await DEVICE_OP_DISPATCH.persona_switch!({ personaId: 'per-9' }, b)
    expect(b.setMeta).toHaveBeenCalledWith('active_persona_id', 'per-9')
  })

  it('reminder_set → reminder.set(enabled, hour, minute)，参数**逐字透传不翻译**', async () => {
    const b = { setMeta: vi.fn(), recordConsent: vi.fn(), triggerSync: vi.fn(), setReminder: vi.fn().mockResolvedValue({}) }
    await DEVICE_OP_DISPATCH.reminder_set!({ enabled: true, hour: 20, minute: 30 }, b)
    expect(b.setReminder).toHaveBeenCalledWith(true, 20, 30)
  })

  it('reminder_set 关提醒时也照样下发（enabled=false 是一个合法意图，不是缺省）', async () => {
    const b = { setMeta: vi.fn(), recordConsent: vi.fn(), triggerSync: vi.fn(), setReminder: vi.fn().mockResolvedValue({}) }
    await DEVICE_OP_DISPATCH.reminder_set!({ enabled: false, hour: 9, minute: 0 }, b)
    expect(b.setReminder).toHaveBeenCalledWith(false, 9, 0)
  })

  it('sync_trigger / consent_record 不带 payload（服务端的 payload 也是空对象）', async () => {
    const b = { setMeta: vi.fn(), recordConsent: vi.fn().mockResolvedValue({}), triggerSync: vi.fn().mockResolvedValue({}), setReminder: vi.fn() }
    await DEVICE_OP_DISPATCH.sync_trigger!({}, b)
    await DEVICE_OP_DISPATCH.consent_record!({}, b)
    expect(b.triggerSync).toHaveBeenCalled()
    expect(b.recordConsent).toHaveBeenCalled()
  })
})