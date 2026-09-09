/**
 * CategoriesPage 组件测试（T9-3 / REQ-12）。
 *
 * 覆盖：
 * - CategoryEditor 表单字段顺序：分类名称、上级分类在上，图标区（预览/相册/emoji 宫格）在下
 * - CategoryIcon 照片渲染尺寸（1.25em 圆形，向 emoji 视觉看齐）
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
    const img = container.querySelector('img')!
    expect(img.style.width).toBe('1.25em')
    expect(img.style.height).toBe('1.25em')
    expect(img.style.borderRadius).toBe('50%')
    expect(img.style.display).toBe('inline-block')
    expect(img.style.verticalAlign).toBe('middle')
  })
})
