import { useEffect, useRef, useCallback, useState } from 'react'
import { useBridge } from './use-bridge'

/**
 * T-M4-04b: 语音识别 hook
 *
 * 封装 voice.startRecording / voice.stopRecording 桥命令及实时转写事件。
 * 返回 { isRecording, partialText, finalText, startRecording, stopRecording, error, reset }
 *
 * 用法：
 *   const voice = useVoice()
 *   voice.startRecording() // 开始录音
 *   voice.partialText      // 实时部分转写
 *   voice.stopRecording()  // 停止录音
 *   voice.finalText        // 最终转写结果
 */

export interface UseVoiceReturn {
  /** 是否正在录音 */
  isRecording: boolean
  /** 实时部分转写文本（录音中持续更新） */
  partialText: string
  /** 最终转写文本（停止录音后设置） */
  finalText: string
  /** 错误信息 */
  error: { code: string; message: string } | null
  /** 开始录音 */
  startRecording: () => Promise<boolean>
  /** 停止录音 */
  stopRecording: () => Promise<string>
  /** 重置状态 */
  reset: () => void
}

export function useVoice(): UseVoiceReturn {
  const bridge = useBridge()
  const [isRecording, setIsRecording] = useState(false)
  const [partialText, setPartialText] = useState('')
  const [finalText, setFinalText] = useState('')
  const [error, setError] = useState<{ code: string; message: string } | null>(null)
  const unsubscribersRef = useRef<Array<() => void>>([])

  // 注册事件监听
  useEffect(() => {
    const unsubPartial = bridge.onVoicePartial((text: string) => {
      setPartialText(text)
    })
    const unsubFinal = bridge.onVoiceFinal((text: string) => {
      setFinalText(text)
      setIsRecording(false)
    })
    const unsubError = bridge.onVoiceError((err: { code: string; message: string }) => {
      setError(err)
      setIsRecording(false)
    })

    unsubscribersRef.current = [unsubPartial, unsubFinal, unsubError]

    return () => {
      unsubscribersRef.current.forEach(unsub => unsub())
      unsubscribersRef.current = []
    }
  }, [bridge])

  const startRecording = useCallback(async (): Promise<boolean> => {
    setError(null)
    setPartialText('')
    setFinalText('')

    try {
      const result = await bridge.startVoiceRecording()
      setIsRecording(result.started)
      return result.started
    } catch (err) {
      const bridgeError = err as { code: string; message: string }
      setError(bridgeError)
      setIsRecording(false)
      return false
    }
  }, [bridge])

  const stopRecording = useCallback(async (): Promise<string> => {
    try {
      const result = await bridge.stopVoiceRecording()
      setIsRecording(false)
      setFinalText(result.text)
      return result.text
    } catch (err) {
      const bridgeError = err as { code: string; message: string }
      setError(bridgeError)
      setIsRecording(false)
      return ''
    }
  }, [bridge])

  const reset = useCallback(() => {
    setPartialText('')
    setFinalText('')
    setError(null)
    setIsRecording(false)
  }, [])

  return {
    isRecording,
    partialText,
    finalText,
    error,
    startRecording,
    stopRecording,
    reset,
  }
}
