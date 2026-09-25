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
import type { CategoryPagerProps, EntryChipRowProps } from '../../features/ledger/entry-controls'
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

// ─── CategoryPager：层级化（T19-5 / REQ-76①） ───
//
// 宿主仍传扁平全量（三处过滤口径不动），组件自己按 parentId 分组：一级进宫格、
// 二级进「选中/展开的一级」下方的内嵌面板。★ 不变量：一级仍是合法终值——
// 点一级格 onSelect 收到的就是一级 id（不下钻），展开与否只是可见性。

const subCat = (id: string, name: string, parentId: string): CategoryData => ({
  id, name, kind: 'expense', parentId, version: 1,
})

/** 餐饮 ← 午饭/晚饭；交通（无子类）；购物 ← 网购 */
const HIER: CategoryData[] = [
  cat('c-food', '餐饮'),
  subCat('c-lunch', '午饭', 'c-food'),
  subCat('c-dinner', '晚饭', 'c-food'),
  cat('c-traffic', '交通'),
  cat('c-shop', '购物'),
  subCat('c-online', '网购', 'c-shop'),
]

function renderPager(props: Partial<CategoryPagerProps> = {}) {
  const full: CategoryPagerProps = {
    categories: HIER,
    selectedId: '',
    onSelect: vi.fn(),
    page: 0,
    onPageChange: vi.fn(),
    ...props,
  }
  render(<CategoryPager {...full} />)
  return full
}

describe('CategoryPager — 层级化（T19-5）', () => {
  it('宫格只排一级；二级不进格；仅带子类的一级有可展开标记；占位仍按一级数补', () => {
    renderPager()
    const pager = screen.getByTestId('category-pager')
    for (const name of ['餐饮', '交通', '购物']) {
      expect(within(pager).getByText(name)).toBeTruthy()
    }
    for (const name of ['午饭', '晚饭', '网购']) {
      expect(within(pager).queryByText(name)).toBeNull()
    }
    // 3 个一级 → 7 个占位格（分页口径只看一级，二级不入页）
    expect(screen.getAllByTestId('cat-cell-placeholder')).toHaveLength(7)
    expect(screen.getAllByTestId('cat-expand-marker')).toHaveLength(2)
    const food = within(pager).getByText('餐饮').closest('button') as HTMLElement
    const traffic = within(pager).getByText('交通').closest('button') as HTMLElement
    expect(within(food).queryByTestId('cat-expand-marker')).toBeTruthy()
    expect(within(traffic).queryByTestId('cat-expand-marker')).toBeNull()
    // 默认不展开（选中的是空）
    expect(screen.queryByTestId('category-subpanel')).toBeNull()
  })

  it('点带子类的一级 = 选中该一级（★不下钻）+ 其二级面板展开', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    renderPager({ onSelect })
    await user.click(within(screen.getByTestId('category-pager')).getByText('餐饮'))
    expect(onSelect).toHaveBeenLastCalledWith('c-food') // 一级仍是合法终值
    const panel = screen.getByTestId('category-subpanel')
    expect(within(panel).getByText('午饭')).toBeTruthy()
    expect(within(panel).getByText('晚饭')).toBeTruthy()
    expect(within(panel).queryByText('网购')).toBeNull() // 只属于「餐饮」
  })

  it('已展开时再点该一级 → 收起面板（选中值不变，仍是一级）', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    renderPager({ onSelect })
    const food = () => within(screen.getByTestId('category-pager')).getByText('餐饮')
    await user.click(food())
    expect(screen.getByTestId('category-subpanel')).toBeTruthy()
    await user.click(food())
    expect(screen.queryByTestId('category-subpanel')).toBeNull()
    expect(onSelect).toHaveBeenLastCalledWith('c-food')
  })

  it('点二级格 → onSelect 收到二级 id，面板保持展开', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    renderPager({ onSelect })
    await user.click(within(screen.getByTestId('category-pager')).getByText('餐饮'))
    await user.click(within(screen.getByTestId('category-subpanel')).getByText('晚饭'))
    expect(onSelect).toHaveBeenLastCalledWith('c-dinner')
    expect(screen.getByTestId('category-subpanel')).toBeTruthy()
  })

  it('无子类的一级：点击只选中，不展开面板（零回归）', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    renderPager({ onSelect })
    await user.click(within(screen.getByTestId('category-pager')).getByText('交通'))
    expect(onSelect).toHaveBeenLastCalledWith('c-traffic')
    expect(screen.queryByTestId('category-subpanel')).toBeNull()
  })

  it('受控 selectedId 落在二级 → 其一级格 active 且面板默认展开（选的是谁的孩子可见）', () => {
    renderPager({ selectedId: 'c-lunch' })
    const pager = screen.getByTestId('category-pager')
    expect(within(pager).getByText('餐饮').closest('button')).toHaveClass('active')
    expect(within(pager).getByText('交通').closest('button')).not.toHaveClass('active')
    const panel = screen.getByTestId('category-subpanel')
    expect(within(panel).getByText('午饭').closest('button')).toHaveClass('active')
    expect(within(panel).getByText('晚饭').closest('button')).not.toHaveClass('active')
  })

  it('二级不参与分页：10 个一级 + 2 个二级仍是单页、无指示点', () => {
    const many = [...makeCats(10), subCat('s-1', '子一', 'cat-0'), subCat('s-2', '子二', 'cat-1')]
    renderPager({ categories: many })
    expect(screen.queryByTestId('category-page-dots')).toBeNull()
    expect(screen.queryAllByTestId('cat-cell-placeholder')).toHaveLength(0)
    expect(within(screen.getByTestId('category-pager')).queryByText('子一')).toBeNull()
  })

  it('父类不在列表内的孤儿按一级渲染——不凭空消失、仍可选中（改动前它们本就在格子里）', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    renderPager({ categories: [cat('c-a', '甲'), subCat('c-x', '孤儿', 'c-missing')], onSelect })
    await user.click(within(screen.getByTestId('category-pager')).getByText('孤儿'))
    expect(onSelect).toHaveBeenLastCalledWith('c-x')
    expect(screen.queryByTestId('category-subpanel')).toBeNull()
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
