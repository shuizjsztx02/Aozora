/**
 * CategoriesPage 组件测试（T9-3 / REQ-12）。
 *
 * 覆盖：
 * - CategoryEditor 表单字段顺序：分类名称、上级分类在上，图标区（预览/相册/emoji 宫格）在下
 * - CategoryIcon 照片渲染尺寸（1.25em 圆形，向 emoji 视觉看齐）
 * - T19-6（REQ-76②）：一级分类弹层内的二级分类段——可展开、可编辑、可新建
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { CategoriesPage } from '../../features/settings/CategoriesPage'
import { CategoryIcon } from '../../components/CategoryIcon'

const mockBridge = vi.hoisted(() => ({
  call: vi.fn(),
  listCategories: vi.fn(),
  insertCategory: vi.fn(),
  updateCategory: vi.fn(),
  pickIcon: vi.fn(),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

const SEED = [
  { id: 'c1', kind: 'expense' as const, name: '餐饮', parentId: null, icon: null, version: 1 },
  { id: 'c2', kind: 'expense' as const, name: '交通', parentId: null, icon: null, version: 1 },
]

/** T19-6 用：c1 带两个二级分类，c2 无子类。 */
const SEED_WITH_CHILDREN = [
  ...SEED,
  { id: 'c3', kind: 'expense' as const, name: '早餐', parentId: 'c1', icon: '🍳', version: 1 },
  { id: 'c4', kind: 'expense' as const, name: '午餐', parentId: 'c1', icon: null, version: 1 },
]

const renderPage = () =>
  render(
    <MemoryRouter>
      <CategoriesPage />
    </MemoryRouter>,
  )

describe('CategoriesPage · CategoryEditor（REQ-12）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.listCategories.mockResolvedValue(SEED)
    mockBridge.insertCategory.mockResolvedValue({ id: 'c-new' })
  })

  it('name input and parent-chip section come BEFORE the emoji picker grid', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByTestId('category-add-支出分类'))

    const editor = await screen.findByTestId('category-editor')
    const nameInput = screen.getByTestId('category-name')
    const emojiPicker = screen.getByTestId('emoji-picker')
    const photoBtn = screen.getByTestId('icon-pick-photo')

    // 名称在 emoji 宫格之前（T9-3 调序核心断言）
    expect(nameInput.compareDocumentPosition(emojiPicker) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // 上级分类在 emoji 宫格之前
    const parentNone = screen.getByTestId('parent-none')
    expect(parentNone.compareDocumentPosition(emojiPicker) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // 图标区（相册按钮）整体下移：上级分类在相册按钮之前
    expect(parentNone.compareDocumentPosition(photoBtn) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // 全部控件仍在编辑器内
    expect(editor).toContainElement(nameInput)
    expect(editor).toContainElement(emojiPicker)
  })

  it('CategoryIcon renders photo icons at 1.25em round (REQ-12B)', () => {
    const { container } = render(
      <CategoryIcon icon="data:image/png;base64,AAAA" name="多美" className="text-lg" />,
    )
    // REQ-32（T12-2）后 img 外层容器接管行中对齐（见 CategoryIcon.test.tsx），此处只锁内层视觉尺寸
    const img = container.querySelector('img')!
    expect(img.style.width).toBe('1.25em')
    expect(img.style.height).toBe('1.25em')
    expect(img.style.borderRadius).toBe('50%')
  })
})

describe('CategoriesPage · 二级分类可展开可配（T19-6 / REQ-76②）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.listCategories.mockResolvedValue(SEED_WITH_CHILDREN)
    mockBridge.insertCategory.mockResolvedValue({ id: 'c-new' })
    mockBridge.updateCategory.mockResolvedValue({ id: 'c3' })
  })

  it('点击带子类的一级分类：弹层下段列出其二级分类', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByTestId('category-cell-c1'))

    expect(await screen.findByTestId('category-children-section')).toBeInTheDocument()
    expect(screen.getByTestId('category-child-c3')).toHaveTextContent('早餐')
    expect(screen.getByTestId('category-child-c4')).toHaveTextContent('午餐')
    // 只列自己的子类
    expect(screen.queryByTestId('category-child-c2')).not.toBeInTheDocument()
  })

  it('二级分类可点进编辑并落库（字段换为该二级、parentId 保持）', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByTestId('category-cell-c1'))
    await user.click(await screen.findByTestId('category-child-c3'))

    // 切换编辑目标后表单被重置为该二级（而非停留在父级的「餐饮」）
    const nameInput = screen.getByTestId('category-name')
    expect(nameInput).toHaveValue('早餐')
    // 二级不显示子类段（不做三级）
    expect(screen.queryByTestId('category-children-section')).not.toBeInTheDocument()

    await user.clear(nameInput)
    await user.type(nameInput, '早点')
    await user.click(screen.getByTestId('category-save'))

    expect(mockBridge.updateCategory).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'c3', name: '早点', parentId: 'c1', kind: 'expense' }),
      expect.any(String),
    )
  })

  it('新建二级分类：parentId 预置为该一级，保存后回到列表', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByTestId('category-cell-c1'))
    await user.click(await screen.findByTestId('category-add-child'))

    const nameInput = screen.getByTestId('category-name')
    expect(nameInput).toHaveValue('')
    await user.type(nameInput, '晚餐')
    await user.click(screen.getByTestId('category-save'))

    expect(mockBridge.insertCategory).toHaveBeenCalledWith(
      expect.objectContaining({ name: '晚餐', parentId: 'c1', kind: 'expense' }),
      expect.any(String),
    )
  })

  it('无子类的一级分类：点击仍是直接编辑（原字段不变，子类段为空态）', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByTestId('category-cell-c2'))

    expect(await screen.findByTestId('category-editor')).toBeInTheDocument()
    expect(screen.getByTestId('category-name')).toHaveValue('交通')
    expect(screen.getByTestId('parent-none')).toBeInTheDocument()
    expect(screen.getByTestId('emoji-picker')).toBeInTheDocument()
    expect(screen.getByTestId('category-children-empty')).toBeInTheDocument()
  })

  it('一级格显示可点计数提示，计数含义不变、无子类的不显示', async () => {
    renderPage()
    expect(await screen.findByTestId('category-children-hint-c1')).toHaveTextContent('2 个子类')
    expect(screen.queryByTestId('category-children-hint-c2')).not.toBeInTheDocument()
  })
})
