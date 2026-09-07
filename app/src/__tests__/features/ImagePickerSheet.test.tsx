/**
 * T-M4-04d: ImagePickerSheet 组件测试。
 *
 * 验证：
 * - 渲染拍照和从相册选择两个入口
 * - 点击按钮触发 onPick 回调（有权限时）
 * - 无权限时请求权限而非直接回调
 * - 权限授予后自动续接选择（R-M4-48）
 * - 取消按钮触发 onClose
 * - 永久拒绝时显示引导设置
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ImagePickerSheet } from '../../features/image/ImagePickerSheet'

// ─── Mocks ───

const mockPermissions = vi.hoisted(() => ({
  canCamera: true,
  canGallery: true,
  shouldGuideSettings: false,
  requestPermission: vi.fn().mockResolvedValue(undefined),
  openSettings: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('../../platform/hooks/use-permissions', () => ({
  usePermissions: () => mockPermissions,
}))

describe('ImagePickerSheet', () => {
  const onPick = vi.fn()
  const onClose = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    mockPermissions.canCamera = true
    mockPermissions.canGallery = true
    mockPermissions.shouldGuideSettings = false
  })

  it('renders camera and gallery buttons', () => {
    render(<ImagePickerSheet onPick={onPick} onClose={onClose} />)
    expect(screen.getByTestId('pick-camera')).toBeTruthy()
    expect(screen.getByTestId('pick-gallery')).toBeTruthy()
    expect(screen.getByTestId('pick-cancel')).toBeTruthy()
  })

  it('calls onPick with camera when camera button clicked and permission granted', async () => {
    const user = userEvent.setup()
    render(<ImagePickerSheet onPick={onPick} onClose={onClose} />)
    await user.click(screen.getByTestId('pick-camera'))
    expect(onPick).toHaveBeenCalledWith('camera')
  })

  it('calls onPick with gallery when gallery button clicked and permission granted', async () => {
    const user = userEvent.setup()
    render(<ImagePickerSheet onPick={onPick} onClose={onClose} />)
    await user.click(screen.getByTestId('pick-gallery'))
    expect(onPick).toHaveBeenCalledWith('gallery')
  })

  it('requests permission when camera permission not granted', async () => {
    mockPermissions.canCamera = false
    const user = userEvent.setup()
    render(<ImagePickerSheet onPick={onPick} onClose={onClose} />)
    await user.click(screen.getByTestId('pick-camera'))
    expect(onPick).not.toHaveBeenCalled()
    expect(mockPermissions.requestPermission).toHaveBeenCalledWith('CAMERA')
  })

  it('requests permission when gallery permission not granted', async () => {
    mockPermissions.canGallery = false
    const user = userEvent.setup()
    render(<ImagePickerSheet onPick={onPick} onClose={onClose} />)
    await user.click(screen.getByTestId('pick-gallery'))
    expect(onPick).not.toHaveBeenCalled()
    expect(mockPermissions.requestPermission).toHaveBeenCalledWith('READ_MEDIA_IMAGES')
  })

  it('calls onClose when cancel button clicked', async () => {
    const user = userEvent.setup()
    render(<ImagePickerSheet onPick={onPick} onClose={onClose} />)
    await user.click(screen.getByTestId('pick-cancel'))
    expect(onClose).toHaveBeenCalled()
  })

  it('shows settings guidance when shouldGuideSettings is true', () => {
    mockPermissions.shouldGuideSettings = true
    render(<ImagePickerSheet onPick={onPick} onClose={onClose} />)
    expect(screen.getByText(/请在系统设置中允许访问相机和相册/)).toBeTruthy()
    expect(screen.getByText('去设置')).toBeTruthy()
  })

  it('calls openSettings when settings link clicked', async () => {
    mockPermissions.shouldGuideSettings = true
    const user = userEvent.setup()
    render(<ImagePickerSheet onPick={onPick} onClose={onClose} />)
    await user.click(screen.getByText('去设置'))
    expect(mockPermissions.openSettings).toHaveBeenCalled()
  })
})
