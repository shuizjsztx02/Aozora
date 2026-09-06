/**
 * T-M4-04d: 图片上传 React hook。
 *
 * 封装 image.pick / image.delete 桥命令，提供：
 * - 相机/相册选择 → 压缩 → 上传一体化
 * - 上传进度追踪
 * - 失败可恢复（返回 draft 状态供 UI 决策重试或删除）
 *
 * 用法：
 * ```tsx
 * const { pickImage, uploading, progress, attachment, error, retry, clear } = useImage()
 *
 * // 从相册选择并上传
 * await pickImage('gallery')
 *
 * // 上传失败后重试（重新打开选择器）
 * await retry()
 *
 * // 删除已上传附件（放弃草稿）
 * await clear()
 * ```
 */

import { useState, useCallback, useEffect, useRef } from 'react'
import { useBridge } from './use-bridge'
import type { ImagePickResult, ImageProgressEvent } from '../bridge/bridge-types'

export interface UseImageReturn {
  /** 是否正在上传（含等待系统选择器返回） */
  uploading: boolean
  /** 上传进度 0-1（-1 表示尚未开始或已完成） */
  progress: number
  /** 上传成功的附件结果 */
  attachment: ImagePickResult | null
  /** 最近一次错误（cancelled / compress_failed / network / timeout / auth_lost） */
  error: string | null
  /** 上一次选择的源（用于 retry） */
  lastSource: 'camera' | 'gallery' | null
  /** 打开系统选择器 → 压缩 → 上传。返回结果或抛错。 */
  pickImage: (source: 'camera' | 'gallery', quality?: number) => Promise<ImagePickResult>
  /** 重试上一次操作（重新打开选择器）。 */
  retry: () => Promise<ImagePickResult | null>
  /** 删除服务端附件（放弃草稿）。 */
  clear: () => Promise<void>
}

export function useImage(): UseImageReturn {
  const bridge = useBridge()
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(-1)
  const [attachment, setAttachment] = useState<ImagePickResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lastSource, setLastSource] = useState<'camera' | 'gallery' | null>(null)

  // 保存最近一次的参数供 retry 使用
  const lastArgs = useRef<{ source: 'camera' | 'gallery'; quality: number } | null>(null)

  // 监听上传进度
  useEffect(() => {
    return bridge.onImageProgress((event: ImageProgressEvent) => {
      if (event.total > 0) {
        setProgress(event.sent / event.total)
      }
    })
  }, [bridge])

  const pickImage = useCallback(
    async (source: 'camera' | 'gallery', quality = 80): Promise<ImagePickResult> => {
      setUploading(true)
      setProgress(-1)
      setError(null)
      setLastSource(source)
      lastArgs.current = { source, quality }

      try {
        const result = await bridge.pickImage(source, quality)
        setAttachment(result)
        setProgress(1)
        setUploading(false)
        return result
      } catch (e) {
        setUploading(false)
        setProgress(-1)
        const code = e instanceof Error && 'code' in e ? (e as { code: string }).code : 'unknown'
        setError(code)
        throw e
      }
    },
    [bridge],
  )

  const retry = useCallback(async (): Promise<ImagePickResult | null> => {
    const args = lastArgs.current
    if (!args) return null
    return pickImage(args.source, args.quality)
  }, [pickImage])

  const clear = useCallback(async () => {
    if (attachment?.attachmentId) {
      try {
        await bridge.deleteImage(attachment.attachmentId)
      } catch {
        // 删除失败不阻塞 UI（服务端 24h 后自动过期）
      }
    }
    setAttachment(null)
    setError(null)
    setProgress(-1)
  }, [attachment, bridge])

  return {
    uploading,
    progress,
    attachment,
    error,
    lastSource,
    pickImage,
    retry,
    clear,
  }
}
