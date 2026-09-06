/**
 * T-M4-04b: useVoice hook 单元测试。
 *
 * 验证：
 * - startRecording 调用 bridge.startVoiceRecording 并更新 isRecording
 * - stopRecording 调用 bridge.stopVoiceRecording 并返回最终文本
 * - onVoicePartial 事件更新 partialText
 * - onVoiceFinal 事件更新 finalText 并停止录音
 * - onVoiceError 事件设置 error 并停止录音
 * - reset 清空所有状态
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useVoice } from '../../../platform/hooks/use-voice'

// ─── Mocks ───

type Listener<T> = (data: T) => void

const mockBridge = vi.hoisted(() => {
  let partialListener: Listener<string> | null = null
  let finalListener: Listener<string> | null = null
  let errorListener: Listener<{ code: string; message: string }> | null = null

  return {
    startVoiceRecording: vi.fn(),
    stopVoiceRecording: vi.fn(),
    onVoicePartial: vi.fn((l: Listener<string>) => {
      partialListener = l
      return () => { partialListener = null }
    }),
    onVoiceFinal: vi.fn((l: Listener<string>) => {
      finalListener = l
      return () => { finalListener = null }
    }),
    onVoiceError: vi.fn((l: Listener<{ code: string; message: string }>) => {
      errorListener = l
      return () => { errorListener = null }
    }),
    // 供测试注入事件
    _emitPartial: (text: string) => partialListener?.(text),
    _emitFinal: (text: string) => finalListener?.(text),
    _emitError: (err: { code: string; message: string }) => errorListener?.(err),
  }
})

vi.mock('../../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

describe('useVoice (T-M4-04b)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.startVoiceRecording.mockResolvedValue({ started: true })
    mockBridge.stopVoiceRecording.mockResolvedValue({ stopped: true, text: '' })
  })

  it('初始状态为空闲', () => {
    const { result } = renderHook(() => useVoice())
    expect(result.current.isRecording).toBe(false)
    expect(result.current.partialText).toBe('')
    expect(result.current.finalText).toBe('')
    expect(result.current.error).toBeNull()
  })

  it('startRecording 调用 bridge 并设置 isRecording=true', async () => {
    const { result } = renderHook(() => useVoice())

    let started = false
    await act(async () => {
      started = await result.current.startRecording()
    })

    expect(started).toBe(true)
    expect(mockBridge.startVoiceRecording).toHaveBeenCalledTimes(1)
    expect(result.current.isRecording).toBe(true)
  })

  it('startRecording 失败时设置 error', async () => {
    mockBridge.startVoiceRecording.mockRejectedValue({
      code: 'permission_denied',
      message: '需要麦克风权限',
    })
    const { result } = renderHook(() => useVoice())

    let started = true
    await act(async () => {
      started = await result.current.startRecording()
    })

    expect(started).toBe(false)
    expect(result.current.isRecording).toBe(false)
    expect(result.current.error?.code).toBe('permission_denied')
  })

  it('收到 onVoicePartial 事件更新 partialText', async () => {
    const { result } = renderHook(() => useVoice())

    await act(async () => {
      await result.current.startRecording()
    })

    act(() => {
      mockBridge._emitPartial('午饭')
    })
    expect(result.current.partialText).toBe('午饭')

    act(() => {
      mockBridge._emitPartial('午饭十五')
    })
    expect(result.current.partialText).toBe('午饭十五')
  })

  it('收到 onVoiceFinal 事件更新 finalText 并停止录音', async () => {
    const { result } = renderHook(() => useVoice())

    await act(async () => {
      await result.current.startRecording()
    })
    expect(result.current.isRecording).toBe(true)

    act(() => {
      mockBridge._emitFinal('午饭 15 元')
    })

    expect(result.current.finalText).toBe('午饭 15 元')
    expect(result.current.isRecording).toBe(false)
  })

  it('收到 onVoiceError 事件设置 error 并停止录音', async () => {
    const { result } = renderHook(() => useVoice())

    await act(async () => {
      await result.current.startRecording()
    })

    act(() => {
      mockBridge._emitError({ code: 'model_not_loaded', message: '模型未加载' })
    })

    expect(result.current.error?.code).toBe('model_not_loaded')
    expect(result.current.error?.message).toBe('模型未加载')
    expect(result.current.isRecording).toBe(false)
  })

  it('stopRecording 调用 bridge 并返回最终文本', async () => {
    mockBridge.stopVoiceRecording.mockResolvedValue({
      stopped: true,
      text: '午饭十五块',
    })
    const { result } = renderHook(() => useVoice())

    await act(async () => {
      await result.current.startRecording()
    })

    let text = ''
    await act(async () => {
      text = await result.current.stopRecording()
    })

    expect(text).toBe('午饭十五块')
    expect(result.current.finalText).toBe('午饭十五块')
    expect(result.current.isRecording).toBe(false)
    expect(mockBridge.stopVoiceRecording).toHaveBeenCalledTimes(1)
  })

  it('stopRecording 失败时设置 error', async () => {
    mockBridge.stopVoiceRecording.mockRejectedValue({
      code: 'record_failed',
      message: '录音失败',
    })
    const { result } = renderHook(() => useVoice())

    await act(async () => {
      await result.current.startRecording()
    })

    let text = 'non-empty'
    await act(async () => {
      text = await result.current.stopRecording()
    })

    expect(text).toBe('')
    expect(result.current.isRecording).toBe(false)
    expect(result.current.error?.code).toBe('record_failed')
  })

  it('reset 清空所有状态', async () => {
    const { result } = renderHook(() => useVoice())

    await act(async () => {
      await result.current.startRecording()
    })
    act(() => {
      mockBridge._emitPartial('午饭')
    })
    act(() => {
      mockBridge._emitFinal('午饭 15')
    })

    expect(result.current.partialText).toBe('午饭')
    expect(result.current.finalText).toBe('午饭 15')

    act(() => {
      result.current.reset()
    })

    expect(result.current.partialText).toBe('')
    expect(result.current.finalText).toBe('')
    expect(result.current.isRecording).toBe(false)
    expect(result.current.error).toBeNull()
  })

  it('startRecording 前重置 partialText 和 finalText', async () => {
    const { result } = renderHook(() => useVoice())

    // 首次录音
    await act(async () => {
      await result.current.startRecording()
    })
    act(() => {
      mockBridge._emitPartial('旧文本')
    })
    act(() => {
      mockBridge._emitFinal('旧结果')
    })

    // 第二次录音应清空旧状态
    await act(async () => {
      await result.current.startRecording()
    })

    expect(result.current.partialText).toBe('')
    expect(result.current.finalText).toBe('')
    expect(result.current.error).toBeNull()
  })

  it('组件卸载时取消事件监听', async () => {
    const unsubPartial = vi.fn()
    const unsubFinal = vi.fn()
    const unsubError = vi.fn()
    mockBridge.onVoicePartial.mockReturnValue(unsubPartial)
    mockBridge.onVoiceFinal.mockReturnValue(unsubFinal)
    mockBridge.onVoiceError.mockReturnValue(unsubError)

    const { unmount } = renderHook(() => useVoice())
    unmount()

    expect(unsubPartial).toHaveBeenCalledTimes(1)
    expect(unsubFinal).toHaveBeenCalledTimes(1)
    expect(unsubError).toHaveBeenCalledTimes(1)
  })
})
