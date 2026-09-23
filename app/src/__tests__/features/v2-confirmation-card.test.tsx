/**
 * T18-3（REQ-67②③）：v2 通用确认卡的**只读态**与**回执诚实化**。
 *
 * 这张卡此前没有任何用例（只有 `assistant-v2-lane.test.ts` 守着车道判定与设备分派表）。
 * 本文件钉住三件容易回退的事：
 *
 * 1. **只读态没有写入口**——历史卡是留档，不是安全门；确认/拒绝按钮只属于当轮那张卡。
 * 2. **「已执行」只认执行凭据**（`executions`）。这一条是 REQ-67② 的核心：用户看到
 *    「改好了」却说不清改没改，正是因为客户端拿卡态/模型的话充当回执。**没有凭据一律
 *    报「未执行 · 待确认」**，包括「旧版本落盘的、没有凭据字段的已提交消息」。
 * 3. **三个终态各有各的说法**：已撤销（灰化）/ 执行失败 / 已执行——不合并成一句。
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { V2ConfirmationCard } from '../../features/assistant/V2ConfirmationCard'
import type { ActionProposalV2 } from '../../domain/action-proposal'
import type { CardStatus, ExecutionEvidence } from '../../features/assistant/use-assistant'

const proposal = (over: Partial<ActionProposalV2> = {}): ActionProposalV2 => ({
  contractVersion: 2,
  proposalId: 'p2',
  action: 'transaction_update',
  confidence: 0.9,
  requiresConfirmation: true,
  message: '把「祖传牛肉面」的金额改成 ¥15.00',
  warnings: [],
  payload: { transactionId: 'tx-1', version: 3, changes: { amountCents: 1500 } },
  ...over,
})

const receipt = (
  lane: ExecutionEvidence['lane'], entityIds: string[] = [],
): ExecutionEvidence => ({ lane, entityIds })

interface RenderProps {
  proposal?: ActionProposalV2
  status?: CardStatus | 'undone'
  busy?: boolean
  executions?: ExecutionEvidence[]
  readonly?: boolean
  onConfirm?: () => void
  onReject?: () => void
}

const renderCard = (props: RenderProps = {}) => render(
  <V2ConfirmationCard
    proposal={props.proposal ?? proposal()}
    status={props.status ?? 'pending-confirm'}
    busy={props.busy ?? false}
    executions={props.executions}
    readonly={props.readonly}
    onConfirm={props.onConfirm}
    onReject={props.onReject}
  />,
)

describe('V2ConfirmationCard — 只读态（T18-3 / REQ-67③）', () => {
  it('只读卡：主行 + payload 对照都在，确认/拒绝入口不在', () => {
    renderCard({ readonly: true, status: 'pending-confirm' })
    expect(screen.getByTestId('v2-card-message').textContent)
      .toContain('把「祖传牛肉面」的金额改成 ¥15.00')
    expect(screen.getByTestId('v2-card-payload').textContent).toContain('amountCents')
    expect(screen.queryByTestId('v2-card-confirm')).toBeNull()
    expect(screen.queryByTestId('v2-card-reject')).toBeNull()
  })

  it('当轮那张卡（非只读）仍给出确认/拒绝入口', () => {
    renderCard({ status: 'pending-confirm' })
    expect(screen.getByTestId('v2-card-confirm')).toBeInTheDocument()
    expect(screen.getByTestId('v2-card-reject')).toBeInTheDocument()
  })

  it('已经点过确认的当轮卡（committed + 有凭据）不再给入口——执行后即失效', () => {
    renderCard({ status: 'committed', executions: [receipt('server', ['tx-1'])] })
    expect(screen.queryByTestId('v2-card-confirm')).toBeNull()
    expect(screen.getByTestId('v2-card-done')).toBeInTheDocument()
  })
})

describe('V2ConfirmationCard — 回执诚实化：只认执行凭据（REQ-67②）', () => {
  it('有服务端凭据 → 「已执行」并报出被影响的实体数', () => {
    renderCard({ readonly: true, status: 'committed', executions: [receipt('server', ['tx-1'])] })
    expect(screen.getByTestId('v2-card-done').textContent).toContain('已执行')
    expect(screen.getByTestId('v2-card-done').textContent).toContain('影响 1 项')
    expect(screen.queryByTestId('v2-card-not-executed')).toBeNull()
  })

  it('**凭据为空 → 「未执行 · 待确认」，绝不出现「已执行」**（这就是 REQ-67② 的判据）', () => {
    renderCard({ readonly: true, status: 'committed' })
    expect(screen.getByTestId('v2-card-not-executed').textContent).toBe('未执行 · 待确认')
    expect(screen.queryByTestId('v2-card-done')).toBeNull()
  })

  it('空数组与缺省字段同一条路（旧版本落盘的已提交消息没有 executions）', () => {
    renderCard({ readonly: true, status: 'committed', executions: [] })
    expect(screen.getByTestId('v2-card-not-executed')).toBeInTheDocument()
    expect(screen.queryByTestId('v2-card-done')).toBeNull()
  })

  it('挂起态（未确认）在只读卡上同样报「未执行 · 待确认」——卡态不是回执', () => {
    renderCard({ readonly: true, status: 'pending-confirm' })
    expect(screen.getByTestId('v2-card-not-executed').textContent).toBe('未执行 · 待确认')
    expect(screen.queryByTestId('v2-card-done')).toBeNull()
  })

  it('设备执行的凭据单独一句话——不说「影响 0 项」（那读起来像什么都没发生）', () => {
    renderCard({ readonly: true, status: 'committed', executions: [receipt('device')] })
    expect(screen.getByTestId('v2-card-done').textContent).toContain('已在本机执行')
    expect(screen.getByTestId('v2-card-done').textContent).not.toContain('影响')
  })

  it('凭据与卡态冲突时凭据说了算（服务端真执行过 → 就报已执行）', () => {
    renderCard({ readonly: true, status: 'executing', executions: [receipt('server', ['a', 'b'])] })
    expect(screen.getByTestId('v2-card-done').textContent).toContain('影响 2 项')
  })
})

describe('V2ConfirmationCard — 终态文案（REQ-67③）', () => {
  it('undone → 「已撤销」+ 整卡灰化（与 v1 历史卡同口径）', () => {
    renderCard({ readonly: true, status: 'undone' })
    expect(screen.getByTestId('v2-card-undone').textContent).toBe('已撤销')
    expect(screen.getByTestId('v2-confirmation-card').style.opacity).toBe('0.6')
    expect(screen.queryByTestId('v2-card-done')).toBeNull()
  })

  it('error → 「执行失败」+ 重试引导；**不再指向拒绝入口**（失败态根本没有那个按钮）', () => {
    renderCard({ readonly: true, status: 'error' })
    expect(screen.getByTestId('v2-card-error').textContent).toContain('执行失败')
    expect(screen.getByTestId('v2-card-error').textContent).toContain('重说一次')
    expect(screen.queryByText(/拒绝这张卡/)).toBeNull()
    // 只读卡与当轮卡同一句话（差别只在有无按钮，而失败态两边都没有）
    expect(screen.queryByTestId('v2-card-confirm')).toBeNull()
    expect(screen.queryByTestId('v2-card-reject')).toBeNull()
  })

  it('error 不声称「没有写入」——桥调用失败可能是超时，服务端那时可能已经写了', () => {
    renderCard({ readonly: true, status: 'error' })
    expect(screen.getByTestId('v2-card-error').textContent).not.toContain('没有写入')
  })

  it('undone 优先于凭据：撤销过的卡不说「已执行」', () => {
    renderCard({ readonly: true, status: 'undone', executions: [receipt('server', ['tx-1'])] })
    expect(screen.getByTestId('v2-card-undone')).toBeInTheDocument()
    expect(screen.queryByTestId('v2-card-done')).toBeNull()
  })
})

describe('V2ConfirmationCard — 服务端提醒与流式禁用（既有行为，防回退）', () => {
  it('warnings 原样透出', () => {
    renderCard({ proposal: proposal({ warnings: ['该账户余额可能不足'] }) })
    expect(screen.getByTestId('v2-card-warnings').textContent).toContain('该账户余额可能不足')
  })

  it('流式期间按钮可见但禁用（用户要看得见发生了什么）', () => {
    renderCard({ status: 'pending-confirm', busy: true, onConfirm: vi.fn() })
    expect(screen.getByTestId('v2-card-confirm')).toBeDisabled()
  })
})