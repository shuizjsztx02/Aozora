/**
 * 第十二轮 T12-11（REQ-37 ③ / 决策 #5）：OpPreviewCard 预览确认卡测试。
 *
 * 覆盖：delete/edit 标题与执行数、预览 ≤20 行 + 行数截断提示、
 * 命中 >50 的批量上限提示、执行/取消回调与执行中禁用。
 * 第十三轮 T13-5（REQ-48 / 决策 #8）：泛化双形态——config rows 模式
 * （标题 + label/value 行、「确认」钮、执行中禁用、取消回调）。
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { OpPreviewCard } from '../../features/assistant/OpPreviewCard'
import type { PendingOp } from '../../features/assistant/use-assistant'
import type { CategoryData, TransactionData } from '../../platform/bridge/bridge-types'

const cats: CategoryData[] = [
  { id: 'c-food', kind: 'expense', name: '餐饮', parentId: null, version: 1 },
  { id: 'c-food-early', kind: 'expense', name: '早餐', parentId: 'c-food', icon: '🥐', version: 1 },
]

function tx(partial: Partial<TransactionData> & { id: string }): TransactionData {
  return {
    bookId: 'book-1', type: 'expense', amountCents: 2300, categoryId: 'c-food-early',
    note: '打车', bizDate: '2026-09-08', occurredAt: 1, source: 'manual',
    version: 3, deletedAt: null, ...partial,
  }
}

function op(partial: Partial<PendingOp> = {}): PendingOp {
  return {
    kind: 'delete', reply: '', set: null,
    candidates: [tx({ id: 'a' }), tx({ id: 'b', note: '午饭', amountCents: 4700, type: 'income' })],
    ...partial,
  }
}

describe('OpPreviewCard（T12-11）', () => {
  it('delete 标题=将删除 N 笔；行渲染 日期/分类（父·子）/金额符号/备注', () => {
    render(<OpPreviewCard op={op()} categories={cats} onConfirm={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.getByTestId('op-preview-title').textContent).toBe('🗑 将删除 2 笔记录')
    expect(screen.getAllByTestId('op-row')).toHaveLength(2)
    const rows = screen.getAllByTestId('op-row')
    expect(rows[0].textContent).toContain('2026-09-08')
    expect(rows[0].textContent).toContain('餐饮 · 早餐')
    expect(rows[0].textContent).toContain('-¥23.00')
    expect(rows[0].textContent).toContain('打车')
    // 收入行：+ 号
    expect(rows[1].textContent).toContain('+¥47.00')
    // 按钮
    expect(screen.getByTestId('op-confirm').textContent).toBe('执行 (2)')
  })

  it('edit 标题走修改文案', () => {
    render(
      <OpPreviewCard
        op={op({ kind: 'edit', set: { amountCents: 100 } })}
        categories={cats} onConfirm={vi.fn()} onCancel={vi.fn()}
      />,
    )
    expect(screen.getByTestId('op-preview-title').textContent).toBe('✏️ 将修改 2 笔记录')
  })

  it('预览截断：30 笔只渲 20 行 + 「预览仅显示前 20 笔」提示，执行数仍 30', () => {
    const candidates = Array.from({ length: 30 }, (_, i) => tx({ id: `t${i}` }))
    render(<OpPreviewCard op={op({ candidates })} categories={cats} onConfirm={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.getAllByTestId('op-row')).toHaveLength(20)
    expect(screen.getByTestId('op-preview-title').textContent).toBe('🗑 将删除 30 笔记录')
    expect(screen.getByText('预览仅显示前 20 笔')).toBeInTheDocument()
    // 未超 50 上限 → 不出现截断提示
    expect(screen.queryByTestId('op-truncate-hint')).toBeNull()
  })

  it('命中 55 笔 → 上限提示 + 执行数截到 50', () => {
    const candidates = Array.from({ length: 55 }, (_, i) => tx({ id: `t${i}` }))
    render(<OpPreviewCard op={op({ candidates })} categories={cats} onConfirm={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.getByTestId('op-truncate-hint').textContent).toContain('仅执行最近 50 笔')
    expect(screen.getByTestId('op-confirm').textContent).toBe('执行 (50)')
  })

  it('执行 → 回调触发且按钮即刻禁用（防双击）；取消 → 独立回调', async () => {
    let resolveConfirm: () => void = () => {}
    const onConfirm = vi.fn(() => new Promise<void>(r => { resolveConfirm = r }))
    const onCancel = vi.fn()
    render(<OpPreviewCard op={op()} categories={cats} onConfirm={onConfirm} onCancel={onCancel} />)

    const btn = screen.getByTestId('op-confirm')
    fireEvent.click(btn)
    expect(onConfirm).toHaveBeenCalledTimes(1)
    // 在途再点无效（busy 守卫）
    fireEvent.click(btn)
    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect(btn).toBeDisabled()
    resolveConfirm()
    await waitFor(() => expect(btn).not.toBeDisabled())

    fireEvent.click(screen.getByTestId('op-cancel'))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })
})

describe('OpPreviewCard config 模式（T13-5 / 决策 #8）', () => {
  const rows = [
    { label: '操作', value: '新建账本' },
    { label: '名称', value: '旅行' },
    { label: '图标', value: '✈️' },
  ]

  it('title + rows 渲染；确认钮文案「确认」（无笔数）', () => {
    render(
      <OpPreviewCard variant="config" title="⚙️ 新建账本" rows={rows}
                     onConfirm={vi.fn()} onCancel={vi.fn()} />,
    )
    expect(screen.getByTestId('op-preview-card')).toBeInTheDocument()
    expect(screen.getByTestId('op-preview-title').textContent).toBe('⚙️ 新建账本')
    const rowEls = screen.getAllByTestId('op-config-row')
    expect(rowEls).toHaveLength(3)
    expect(rowEls[1].textContent).toContain('名称')
    expect(rowEls[1].textContent).toContain('旅行')
    expect(screen.getByTestId('op-confirm').textContent).toBe('确认')
    // 交易模式的截断提示不出现
    expect(screen.queryByTestId('op-truncate-hint')).toBeNull()
    expect(screen.queryByTestId('op-row')).toBeNull()
  })

  it('执行 → onConfirm 触发、执行中禁双触；完成恢复', async () => {
    let resolveConfirm: () => void = () => {}
    const onConfirm = vi.fn(() => new Promise<void>(r => { resolveConfirm = r }))
    render(
      <OpPreviewCard variant="config" title="⚙️ 新建账本" rows={rows}
                     onConfirm={onConfirm} onCancel={vi.fn()} />,
    )
    const btn = screen.getByTestId('op-confirm')
    fireEvent.click(btn)
    expect(onConfirm).toHaveBeenCalledTimes(1)
    fireEvent.click(btn)
    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect(btn).toBeDisabled()
    resolveConfirm()
    await waitFor(() => expect(btn).not.toBeDisabled())
  })

  it('取消 → onCancel 独立回调（config 执行/取消与交易模式互不共用）', () => {
    const onCancel = vi.fn()
    render(
      <OpPreviewCard variant="config" title="⚙️ 设置默认账本" rows={rows.slice(0, 2)}
                     onConfirm={vi.fn()} onCancel={onCancel} />,
    )
    fireEvent.click(screen.getByTestId('op-cancel'))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('缺省 variant（不传）仍走交易列表模式——存量调用点零改动兼容', () => {
    render(<OpPreviewCard op={op()} categories={cats} onConfirm={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.getByTestId('op-preview-title').textContent).toBe('🗑 将删除 2 笔记录')
    expect(screen.getByTestId('op-confirm').textContent).toBe('执行 (2)')
  })
})

// ─── 第十五轮 T15-7（REQ-56 ④ / 决策 #8）：OpRow 补账户/账本/收支/预算呈现 ───

describe('OpPreviewCard edit 全字段呈现（T15-7）', () => {
  const accounts = [
    { id: 'a-wx', name: '微信零钱', icon: '💬', version: 1 },
    { id: 'a-test', name: '测试', icon: '🧪', version: 1 },
  ]
  const books = [
    { id: 'book-1', ownerId: 'local', name: '日常', icon: '📒', version: 1 },
    { id: 'book-2', ownerId: 'local', name: '旅行', icon: '✈️', version: 1 },
  ]

  it('副行呈现当前账户/账本名（有值）', () => {
    render(
      <OpPreviewCard
        op={op({
          kind: 'edit', set: { amountCents: 500 },
          candidates: [tx({ id: 'a', accountId: 'a-wx' }), tx({ id: 'b', note: '午饭', amountCents: 4700, type: 'income' })],
        })}
        categories={cats} accounts={accounts} books={books}
        onConfirm={vi.fn()} onCancel={vi.fn()}
      />,
    )
    const rows = screen.getAllByTestId('op-row')
    const a = rows[0]
    expect(a.querySelector('[data-testid="op-row-subline"]')?.textContent)
      .toContain('账户·微信零钱')
    expect(a.querySelector('[data-testid="op-row-subline"]')?.textContent)
      .toContain('账本·日常')
    // 第二笔无 accountId → 不出现账户段；未标「不计入预算」
    expect(rows[1].textContent).not.toContain('账户·')
    expect(rows[1].textContent).not.toContain('不计入预算')
  })

  it('includeInBudget === false 的行标「不计入预算」', () => {
    render(
      <OpPreviewCard
        op={op({ kind: 'edit', set: null, candidates: [tx({ id: 'c', includeInBudget: false })] })}
        categories={cats} accounts={accounts} books={books}
        onConfirm={vi.fn()} onCancel={vi.fn()}
      />,
    )
    expect(screen.getAllByTestId('op-row')[0].textContent).toContain('不计入预算')
  })

  it('edit set 被改字段汇入「改为」diff 行（收支/账户/账本/预算/金额/备注清空）', () => {
    render(
      <OpPreviewCard
        op={op({
          kind: 'edit',
          set: {
            type: 'income', accountName: '测试', bookName: '旅行',
            includeInBudget: false, amountCents: 42000, note: '',
          },
        })}
        categories={cats} accounts={accounts} books={books}
        onConfirm={vi.fn()} onCancel={vi.fn()}
      />,
    )
    const diff = screen.getAllByTestId('op-row-diff')[0].textContent ?? ''
    expect(diff).toContain('收支·收入')
    expect(diff).toContain('账户·测试')
    expect(diff).toContain('账本·旅行')
    expect(diff).toContain('不计入预算')
    expect(diff).toContain('金额·¥420.00')
    expect(diff).toContain('备注·（清空）')
  })

  it('delete 不渲染 diff；未传 accounts/books 时旧渲染零变化（无副行）', () => {
    const { rerender } = render(
      <OpPreviewCard op={op()} categories={cats} onConfirm={vi.fn()} onCancel={vi.fn()} />,
    )
    expect(screen.queryByTestId('op-row-subline')).toBeNull()
    expect(screen.queryByTestId('op-row-diff')).toBeNull()
    rerender(
      <OpPreviewCard
        op={op({ kind: 'edit', set: { accountName: '测试' } })}
        categories={cats} accounts={accounts} books={books}
        onConfirm={vi.fn()} onCancel={vi.fn()}
      />,
    )
    expect(screen.getAllByTestId('op-row-diff').length).toBeGreaterThan(0)
  })
})
