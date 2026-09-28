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
 *
 * T20-2（REQ-77 上层）追加**业务卡**面（文件末那个 describe）：记账三 op 出
 * 记账卡 / 修改卡（新旧对照）/ 删除卡，其余 op 与「拿不到旧值」的改删卡回落通用卡。
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { V2ConfirmationCard } from '../../features/assistant/V2ConfirmationCard'
import type { ActionProposalV2 } from '../../domain/action-proposal'
import type { AccountData, BookData, CategoryData } from '../../platform/bridge/bridge-types'
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

const CATS: CategoryData[] = [
  { id: 'cat-food', kind: 'expense', name: '餐饮', parentId: null, icon: '🥟', version: 1 },
  { id: 'cat-lunch', kind: 'expense', name: '午饭', parentId: 'cat-food', version: 1 },
  { id: 'cat-traffic', kind: 'expense', name: '交通', parentId: null, icon: '🚌', version: 1 },
]

const ACCOUNTS: AccountData[] = [
  { id: 'acc-bank', name: '招商银行卡', icon: '💳', version: 1 },
]

const BOOKS: BookData[] = [
  { id: 'book-1', ownerId: 'o', name: '默认账本', icon: '', version: 1 },
]

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
    categories={CATS}
    accounts={ACCOUNTS}
    books={BOOKS}
    bookId="book-1"
    bookName="默认账本"
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

/**
 * T20-2（REQ-77 上层）：**记账三 op 的业务卡**。
 *
 * 用户要的「历史里出现过的那种记账卡」= v1 的四行口径（分类 / 收支 / 金额 / 日期 /
 * 备注 / 账本），改 / 删另出新旧对照。这里钉住四件事：
 *
 * ① 三 op 各出对应形态（记账卡 / 修改卡 / 删除卡），且**不再重复列 payload**——
 *    业务卡自带四行快照，再列一遍是同一件事说两遍；
 * ② 修改卡的两侧都来自服务端：旧 = 回执 `before`，新 = `before` 叠 payload 的
 *    **增量**，差异字段高亮、未改字段弱化（与 v1 对照区同一份 `diffMask`）；
 * ③ **拿不到 `before` 就不画**（挂起态就是这种情形）——回落通用卡，不编假旧卡；
 * ④ 其余 op 的通用卡一字未改。
 */
describe('V2ConfirmationCard — 记账三 op 的业务卡（T20-2 / REQ-77 上层）', () => {
  /** 执行前的那份快照（服务端 `_snapshot_before` 的形状，键名与 payload 逐字对齐）。 */
  const beforeTx = {
    type: 'expense', amountCents: 3000, categoryId: 'cat-lunch', accountId: null,
    note: '午饭', bizDate: '2026-09-28', bookId: 'book-1',
  }

  const addProposal = (over: Partial<ActionProposalV2> = {}) => proposal({
    action: 'transaction_add',
    requiresConfirmation: false,
    message: '记一笔支出 ¥30.00 · 午饭',
    payload: {
      transactions: [{
        type: 'expense', amountCents: 3000, categoryId: 'cat-lunch',
        accountId: 'acc-bank', bizDate: '2026-09-28', note: '午餐',
      }],
    },
    ...over,
  })

  it('transaction_add → 记账卡：四行口径齐（分类 / 收支 / 金额 / 日期 / 备注 / 账本）', () => {
    renderCard({
      proposal: addProposal(),
      status: 'committed',
      executions: [{ lane: 'server', entityIds: ['tx-1'], action: 'transaction_add', before: null }],
    })
    // 主行仍是服务端那句人类可读的文案（历史卡与当轮卡同一份）
    expect(screen.getByTestId('v2-card-message').textContent).toContain('记一笔支出 ¥30.00')

    const tx = within(screen.getByTestId('v2-card-tx-0'))
    expect(tx.getByTestId('v2-card-tx-0-cat').textContent).toBe('🥟餐饮 · 午饭')
    expect(tx.getByTestId('v2-card-tx-0-type').textContent).toContain('支出')
    expect(tx.getByTestId('v2-card-tx-0-amount').textContent).toContain('30.00')
    expect(tx.getByTestId('v2-card-tx-0-date').textContent).toBe('2026-09-28')
    expect(tx.getByTestId('v2-card-tx-0-note').textContent).toBe('午餐')
    expect(tx.getByTestId('v2-card-tx-0-account').textContent).toContain('招商银行卡')
    expect(tx.getByTestId('v2-card-tx-0-book').textContent).toBe('默认账本')

    // 业务卡与 payload 表**互斥**：同一件事不说两遍
    expect(screen.queryByTestId('v2-card-payload')).toBeNull()
    // 预算位不显示：`before` / `payload` 都没有这一位，画出来就是编的
    expect(screen.queryByTestId('v2-card-tx-0-budget')).toBeNull()
  })

  it('一次记多笔（batch 的 payload）逐笔各出一张，不漏', () => {
    renderCard({
      proposal: addProposal({
        payload: {
          transactions: [
            { type: 'expense', amountCents: 1200, categoryId: 'cat-lunch', bizDate: '2026-09-28', note: '早饭' },
            { type: 'expense', amountCents: 2500, categoryId: 'cat-traffic', bizDate: '2026-09-28', note: '打车' },
          ],
        },
      }),
      status: 'committed',
      executions: [{ lane: 'server', entityIds: ['a', 'b'], action: 'transaction_add', before: null }],
    })
    expect(screen.getByTestId('v2-card-tx-0-note').textContent).toBe('早饭')
    expect(screen.getByTestId('v2-card-tx-1-note').textContent).toBe('打车')
    expect(within(screen.getByTestId('v2-card-tx-1')).getByTestId('v2-card-tx-1-cat').textContent)
      .toBe('🚌交通')
  })

  it('transaction_update → 修改卡：旧 = 回执 before，新 = before 叠 payload 增量，差异字段高亮', () => {
    renderCard({
      proposal: proposal({
        action: 'transaction_update',
        payload: { transactionId: 'tx-1', version: 3, amountCents: 3500, note: '午饭（改）' },
      }),
      status: 'committed',
      executions: [{
        lane: 'server', entityIds: ['tx-1'], action: 'transaction_update', before: beforeTx,
      }],
    })
    expect(screen.getByTestId('v2-card-compare-label').textContent).toContain('新旧对照')

    const oldCard = within(screen.getByTestId('v2-card-before'))
    const newCard = within(screen.getByTestId('v2-card-after'))
    expect(oldCard.getByTestId('v2-card-before-amount').textContent).toContain('30.00')
    // 「新」不是裸 payload（那只有改过的那两项）——是旧值叠增量，字段齐
    expect(newCard.getByTestId('v2-card-after-amount').textContent).toContain('35.00')
    expect(newCard.getByTestId('v2-card-after-note').textContent).toBe('午饭（改）')
    expect(newCard.getByTestId('v2-card-after-cat').textContent).toBe('🥟餐饮 · 午饭')
    expect(newCard.getByTestId('v2-card-after-date').textContent).toBe('2026-09-28')

    // 逐字段差异高亮：改过的亮、没改的弱化（两侧共用同一份掩码，与 v1 对照区同口径）
    expect(newCard.getByTestId('v2-card-after-amount').style.background).toContain('rgba(255, 208, 92')
    expect(newCard.getByTestId('v2-card-after-note').style.background).toContain('rgba(255, 208, 92')
    expect(newCard.getByTestId('v2-card-after-cat').style.opacity).toBe('0.55')
    expect(newCard.getByTestId('v2-card-after-date').style.opacity).toBe('0.55')
    expect(newCard.getByTestId('v2-card-after-account').style.opacity).toBe('0.55')
    expect(oldCard.getByTestId('v2-card-before-amount').style.background).toContain('rgba(255, 208, 92')

    expect(screen.queryByTestId('v2-card-payload')).toBeNull()
  })

  it('**缺 before 就不画修改卡**（挂起态拿不到旧值）——回落通用卡，不编一张假旧卡', () => {
    renderCard({ status: 'pending-confirm' })   // 默认夹具即 transaction_update，executions 为空
    expect(screen.queryByTestId('v2-card-ledger')).toBeNull()
    expect(screen.queryByTestId('v2-card-before')).toBeNull()
    expect(screen.getByTestId('v2-card-payload').textContent).toContain('amountCents')
    // 挂起态该有的确认入口照旧
    expect(screen.getByTestId('v2-card-confirm')).toBeInTheDocument()
  })

  it('transaction_delete → 删除卡：显示将被删的那笔（回执 before）', () => {
    renderCard({
      proposal: proposal({
        action: 'transaction_delete',
        message: '删除一笔支出记录（今天 30 块 午饭）',
        payload: { transactionId: 'tx-1' },
      }),
      status: 'committed',
      executions: [{
        lane: 'server', entityIds: ['tx-1'], action: 'transaction_delete', before: beforeTx,
      }],
    })
    expect(screen.getByTestId('v2-card-message').textContent).toContain('删除一笔支出记录')
    const del = within(screen.getByTestId('v2-card-delete'))
    expect(del.getByTestId('v2-card-delete-note').textContent).toBe('午饭')
    expect(del.getByTestId('v2-card-delete-amount').textContent).toContain('30.00')
    expect(del.getByTestId('v2-card-delete-cat').textContent).toBe('🥟餐饮 · 午饭')
    // 删除没有「新」一侧，所以没有对照区
    expect(screen.queryByTestId('v2-card-before')).toBeNull()
    expect(screen.queryByTestId('v2-card-payload')).toBeNull()
  })

  it('只读态同样出业务卡，但只有留档：没有确认 / 拒绝入口（T18-3 口径未变）', () => {
    renderCard({
      readonly: true,
      proposal: addProposal(),
      status: 'committed',
      executions: [{ lane: 'server', entityIds: ['tx-1'], action: 'transaction_add', before: null }],
    })
    expect(screen.getByTestId('v2-card-tx-0')).toBeInTheDocument()
    expect(screen.getByTestId('v2-card-done').textContent).toContain('已执行')
    expect(screen.queryByTestId('v2-card-confirm')).toBeNull()
    expect(screen.queryByTestId('v2-card-reject')).toBeNull()
  })

  it('其余 op 一律回落通用卡——payload 键值对照一字未改', () => {
    renderCard({
      proposal: proposal({
        action: 'category_update',
        message: '把「餐饮」的图标改成 🍜',
        payload: { categoryId: 'cat-food', icon: '🍜' },
      }),
    })
    expect(screen.queryByTestId('v2-card-ledger')).toBeNull()
    expect(screen.getByTestId('v2-card-payload').textContent).toContain('icon')
    expect(screen.getByTestId('v2-card-message').textContent).toContain('把「餐饮」的图标改成 🍜')
  })
})