/**
 * T12-6: entry-controls 共享记账控件测试。
 *
 * 覆盖 QuickEntrySheet 抽出的两个组件（行为与抽取前逐字一致）：
 * - CategoryPager：每页 10 格分页、指示点跳页、touch 横滑翻页（±40px 阈值）、
 *   末页隐形占位格、选中态
 * - EntryChipRow：账本/账户/预算三 chip 开合与单开、选中回调 + 选后关面板、
 *   master 过滤、「不使用账户」、空态
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { CategoryPager, EntryChipRow } from '../../features/ledger/entry-controls'
import type { EntryChipRowProps } from '../../features/ledger/entry-controls'
import type { AccountData, BookData, CategoryData } from '../../platform/bridge/bridge-types'

const cat = (id: string, name: string): CategoryData => ({
  id, name, kind: 'expense', parentId: null, version: 1,
})

const makeCats = (n: number) =>
  Array.from({ length: n }, (_, i) => cat(`cat-${i}`, `分类${i + 1}`))

describe('CategoryPager', () => {
  it('single page: renders all cells, no dots; last page padded with 10-n placeholders (UI-18)', () => {
    render(
      <CategoryPager
        categories={makeCats(3)}
        selectedId=""
        onSelect={() => {}}
        page={0}
        onPageChange={() => {}}
      />,
    )
    expect(screen.getByTestId('category-pager')).toBeTruthy()
    expect(screen.queryByTestId('category-page-dots')).toBeNull()
    // 3 真实格 → 7 占位格，且与真实格同构（cat-cell class 撑行高）
    const placeholders = screen.getAllByTestId('cat-cell-placeholder')
    expect(placeholders).toHaveLength(7)
    for (const ph of placeholders) {
      expect(ph.classList.contains('cat-cell')).toBe(true)
      expect(ph.style.visibility).toBe('hidden')
    }
  })

  it('12 categories → 2 pages, dots rendered, page 2 padded with 8 placeholders', () => {
    render(
      <CategoryPager
        categories={makeCats(12)}
        selectedId=""
        onSelect={() => {}}
        page={1}
        onPageChange={() => {}}
      />,
    )
    const dots = screen.getByTestId('category-page-dots')
    expect(dots.querySelectorAll('button')).toHaveLength(2)
    expect(screen.getAllByTestId('cat-cell-placeholder')).toHaveLength(8)
  })

  it('page prop drives translateX; out-of-range page clamped to last page', () => {
    const { rerender } = render(
      <CategoryPager
        categories={makeCats(12)}
        selectedId=""
        onSelect={() => {}}
        page={1}
        onPageChange={() => {}}
      />,
    )
    const track = screen.getByTestId('category-pager').firstElementChild as HTMLElement
    expect(track.style.transform).toBe('translateX(-100%)')
    // 宿主页码越界（如切换后分类变少）→ clamp 到末页，与抽取前 safeCatPage 一致
    rerender(
      <CategoryPager
        categories={makeCats(12)}
        selectedId=""
        onSelect={() => {}}
        page={5}
        onPageChange={() => {}}
      />,
    )
    expect(track.style.transform).toBe('translateX(-100%)')
  })

  it('dot click jumps to that page', async () => {
    const onPageChange = vi.fn()
    const user = userEvent.setup()
    render(
      <CategoryPager
        categories={makeCats(12)}
        selectedId=""
        onSelect={() => {}}
        page={0}
        onPageChange={onPageChange}
      />,
    )
    await user.click(screen.getByLabelText('分类第 2 页'))
    expect(onPageChange).toHaveBeenCalledWith(1)
  })

  it('swipe past ±40px threshold pages forward/back; smaller swipe ignored', () => {
    const onPageChange = vi.fn()
    const swipe = (startX: number, endX: number) => {
      const pager = screen.getByTestId('category-pager')
      fireEvent.touchStart(pager, { touches: [{ clientX: startX }] })
      fireEvent.touchEnd(pager, { changedTouches: [{ clientX: endX }] })
    }

    const { rerender } = render(
      <CategoryPager
        categories={makeCats(12)}
        selectedId=""
        onSelect={() => {}}
        page={0}
        onPageChange={onPageChange}
      />,
    )
    swipe(200, 159) // dx=-41 → 下一页
    expect(onPageChange).toHaveBeenLastCalledWith(1)
    swipe(200, 161) // dx=-39 → 阈值内不动
    expect(onPageChange).toHaveBeenCalledTimes(1)

    rerender(
      <CategoryPager
        categories={makeCats(12)}
        selectedId=""
        onSelect={() => {}}
        page={1}
        onPageChange={onPageChange}
      />,
    )
    swipe(100, 140) // dx=+40 → 上一页（首页边界）
    expect(onPageChange).toHaveBeenLastCalledWith(0)
    // 末页左滑不再前进
    onPageChange.mockClear()
    swipe(200, 100)
    expect(onPageChange).not.toHaveBeenCalled()
  })

  it('cell click reports id; selected cell highlighted active', async () => {
    const onSelect = vi.fn()
    const user = userEvent.setup()
    render(
      <CategoryPager
        categories={makeCats(2)}
        selectedId="cat-0"
        onSelect={onSelect}
        page={0}
        onPageChange={() => {}}
      />,
    )
    const cells = screen.getAllByText(/分类/).filter(
      el => el.classList.contains('truncate'),
    )
    await user.click(screen.getByText('分类2'))
    expect(onSelect).toHaveBeenCalledWith('cat-1')
    expect(cells[0].closest('.cat-cell')).toHaveClass('active')
    expect(cells[1].closest('.cat-cell')).not.toHaveClass('active')
  })
})

// ─── EntryChipRow ───

const BOOKS = [
  { id: 'm', name: '总账本', isMaster: true },
  { id: 'b1', name: '默认账本' },
  { id: 'b2', name: '旅行' },
] as BookData[]
const ACCOUNTS = [
  { id: 'a1', name: '微信' },
  { id: 'a2', name: '信用卡' },
] as AccountData[]

function renderChers(props: Partial<EntryChipRowProps> = {}) {
  const full: EntryChipRowProps = {
    books: BOOKS,
    accounts: ACCOUNTS,
    selectedBookId: 'b1',
    selectedAccountId: 'a1',
    includeInBudget: false,
    onBookSelect: vi.fn(),
    onAccountSelect: vi.fn(),
    onBudgetSelect: vi.fn(),
    ...props,
  }
  render(<EntryChipRow {...full} />)
  return full
}

describe('EntryChipRow', () => {
  it('labels reflect selection', () => {
    renderChers()
    expect(screen.getByTestId('entry-book-picker').textContent).toContain('默认账本')
    expect(screen.getByTestId('entry-account-picker').textContent).toContain('微信')
    expect(screen.getByTestId('entry-budget-picker').textContent).toContain('不计入预算')
  })

  it('book chip with no selection falls back to pickEntryBook (first non-master)', () => {
    renderChers({ selectedBookId: null })
    expect(screen.getByTestId('entry-book-picker').textContent).toContain('默认账本')
  })

  it('account chip shows 不使用账户 when accounts exist but none selected', () => {
    renderChers({ selectedAccountId: null })
    expect(screen.getByTestId('entry-account-picker').textContent).toContain('不使用账户')
  })

  it('no accounts at all → 无账户', () => {
    renderChers({ accounts: [], selectedAccountId: null })
    expect(screen.getByTestId('entry-account-picker').textContent).toContain('无账户')
  })

  it('book chip opens panel excluding master; select reports id and closes panel', async () => {
    const user = userEvent.setup()
    const { onBookSelect } = renderChers()
    await user.click(screen.getByTestId('entry-book-picker'))
    const list = screen.getByTestId('entry-book-list')
    expect(list.textContent).toContain('旅行')
    expect(list.textContent).not.toContain('总账本')
    await user.click(within(list).getByText('旅行'))
    expect(onBookSelect).toHaveBeenCalledWith('b2')
    expect(screen.queryByTestId('entry-book-list')).toBeNull()
  })

  it('clicking the same chip again toggles panel closed', async () => {
    const user = userEvent.setup()
    renderChers()
    await user.click(screen.getByTestId('entry-book-picker'))
    expect(screen.getByTestId('entry-book-list')).toBeTruthy()
    await user.click(screen.getByTestId('entry-book-picker'))
    expect(screen.queryByTestId('entry-book-list')).toBeNull()
  })

  it('only one panel open at a time (单开态)', async () => {
    const user = userEvent.setup()
    renderChers()
    await user.click(screen.getByTestId('entry-book-picker'))
    await user.click(screen.getByTestId('entry-budget-picker'))
    expect(screen.queryByTestId('entry-book-list')).toBeNull()
    expect(screen.getByTestId('entry-budget-list')).toBeTruthy()
  })

  it('empty books → panel shows 暂无可用账本', async () => {
    const user = userEvent.setup()
    renderChers({ books: [], selectedBookId: null })
    await user.click(screen.getByTestId('entry-book-picker'))
    expect(screen.getByTestId('entry-book-list').textContent).toContain('暂无可用账本')
  })

  it('account panel offers 不使用账户 → onAccountSelect(null)', async () => {
    const user = userEvent.setup()
    const { onAccountSelect } = renderChers()
    await user.click(screen.getByTestId('entry-account-picker'))
    await user.click(within(screen.getByTestId('entry-account-list')).getByText('不使用账户'))
    expect(onAccountSelect).toHaveBeenCalledWith(null)
    expect(screen.queryByTestId('entry-account-list')).toBeNull()
  })

  it('budget panel two options; picking 计入 reports true', async () => {
    const user = userEvent.setup()
    const { onBudgetSelect } = renderChers()
    await user.click(screen.getByTestId('entry-budget-picker'))
    await user.click(screen.getByTestId('entry-budget-include'))
    expect(onBudgetSelect).toHaveBeenCalledWith(true)
    expect(screen.queryByTestId('entry-budget-list')).toBeNull()
  })
})
