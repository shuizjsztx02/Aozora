/**
 * T-M4-04d: ImageUploadView 组件测试。
 *
 * 验证：
 * - 上传中显示进度条
 * - 等待选择时显示等待提示
 * - 上传成功显示附件信息和移除按钮
 * - 上传失败显示错误和重试/重新选择/取消按钮
 * - cancelled 错误不显示失败 UI（静默）
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ImageUploadView } from '../../features/image/ImageUploadView'

describe('ImageUploadView', () => {
  const defaultProps = {
    uploading: false,
    progress: -1,
    attachment: null,
    error: null,
    onRetry: vi.fn(),
    onClear: vi.fn(),
    onPickAgain: vi.fn(),
  }

  it('shows progress bar when uploading with progress', () => {
    render(<ImageUploadView {...defaultProps} uploading={true} progress={0.65} />)
    const el = screen.getByTestId('image-uploading')
    expect(el).toBeTruthy()
    expect(screen.getByText('65%')).toBeTruthy()
    expect(screen.getByText('上传中...')).toBeTruthy()
  })

  it('shows waiting message when uploading but no progress yet', () => {
    render(<ImageUploadView {...defaultProps} uploading={true} progress={-1} />)
    expect(screen.getByTestId('image-waiting')).toBeTruthy()
    expect(screen.getByText('等待选择图片...')).toBeTruthy()
  })

  it('shows success state with file size when attachment present', () => {
    const attachment = {
      attachmentId: 'att-1',
      mimeType: 'image/jpeg',
      fileSize: 1024 * 512,
    }
    render(<ImageUploadView {...defaultProps} attachment={attachment} />)
    const el = screen.getByTestId('image-success')
    expect(el).toBeTruthy()
    expect(screen.getByText(/已上传 512.0 KB/)).toBeTruthy()
    expect(screen.getByTestId('image-clear')).toBeTruthy()
  })

  it('formats large file sizes in MB', () => {
    const attachment = {
      attachmentId: 'att-2',
      mimeType: 'image/jpeg',
      fileSize: 2.5 * 1024 * 1024,
    }
    render(<ImageUploadView {...defaultProps} attachment={attachment} />)
    expect(screen.getByText(/已上传 2.5 MB/)).toBeTruthy()
  })

  it('formats small file sizes in bytes', () => {
    const attachment = {
      attachmentId: 'att-3',
      mimeType: 'image/jpeg',
      fileSize: 500,
    }
    render(<ImageUploadView {...defaultProps} attachment={attachment} />)
    expect(screen.getByText(/已上传 500 B/)).toBeTruthy()
  })

  it('calls onClear when clear button clicked in success state', async () => {
    const user = userEvent.setup()
    const attachment = {
      attachmentId: 'att-4',
      mimeType: 'image/jpeg',
      fileSize: 1024,
    }
    render(<ImageUploadView {...defaultProps} attachment={attachment} />)
    await user.click(screen.getByTestId('image-clear'))
    expect(defaultProps.onClear).toHaveBeenCalled()
  })

  it('shows error state with retry and pick-again buttons', () => {
    render(<ImageUploadView {...defaultProps} error="network" />)
    const el = screen.getByTestId('image-error')
    expect(el).toBeTruthy()
    expect(screen.getByText(/网络错误/)).toBeTruthy()
    expect(screen.getByTestId('image-retry')).toBeTruthy()
    expect(screen.getByTestId('image-pick-again')).toBeTruthy()
    expect(screen.getByTestId('image-dismiss')).toBeTruthy()
  })

  it('maps known error codes to Chinese messages', () => {
    const { unmount } = render(<ImageUploadView {...defaultProps} error="compress_failed" />)
    expect(screen.getByText(/压缩失败/)).toBeTruthy()
    unmount()

    render(<ImageUploadView {...defaultProps} error="file_too_large" />)
    expect(screen.getByText(/文件过大/)).toBeTruthy()
  })

  it('shows generic message for unknown error codes', () => {
    render(<ImageUploadView {...defaultProps} error="some_new_error" />)
    expect(screen.getByText(/未知错误/)).toBeTruthy()
  })

  it('calls onRetry when retry button clicked', async () => {
    const user = userEvent.setup()
    render(<ImageUploadView {...defaultProps} error="network" />)
    await user.click(screen.getByTestId('image-retry'))
    expect(defaultProps.onRetry).toHaveBeenCalled()
  })

  it('calls onPickAgain when pick-again button clicked', async () => {
    const user = userEvent.setup()
    render(<ImageUploadView {...defaultProps} error="network" />)
    await user.click(screen.getByTestId('image-pick-again'))
    expect(defaultProps.onPickAgain).toHaveBeenCalled()
  })

  it('calls onClear when dismiss button clicked', async () => {
    const user = userEvent.setup()
    render(<ImageUploadView {...defaultProps} error="network" />)
    await user.click(screen.getByTestId('image-dismiss'))
    expect(defaultProps.onClear).toHaveBeenCalled()
  })

  it('renders nothing when error is cancelled', () => {
    const { container } = render(<ImageUploadView {...defaultProps} error="cancelled" />)
    // cancelled should not render any error UI
    expect(container.innerHTML).toBe('')
  })

  it('renders nothing when no state is active', () => {
    const { container } = render(<ImageUploadView {...defaultProps} />)
    expect(container.innerHTML).toBe('')
  })
})
