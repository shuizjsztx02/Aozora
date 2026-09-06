/**
 * T-M4-04a: 权限 React hook。
 *
 * 封装 PermissionManager 桥命令，提供响应式权限状态。
 *
 * - 挂载时自动查询权限状态
 * - 监听异步权限结果推送（Android 权限对话框关闭后）
 * - 提供便捷判断：canRecord / canCamera / canGallery / shouldGuideSettings
 */

import { useState, useEffect, useCallback } from 'react'
import { useBridge } from './use-bridge'
import type { PermissionType, PermissionStatusData } from '../bridge/bridge-types'

const DEFAULT_STATUSES: PermissionStatusData = {
  permissions: {
    RECORD_AUDIO: 'not_requested',
    CAMERA: 'not_requested',
    READ_MEDIA_IMAGES: 'not_requested',
  },
  canRecordAudio: false,
  canUseCamera: false,
  canAccessImages: false,
}

export interface UsePermissionsReturn {
  /** 完整权限状态 */
  status: PermissionStatusData
  /** 是否正在加载 */
  loading: boolean
  /** 是否可以录音 */
  canRecord: boolean
  /** 是否可以使用相机 */
  canCamera: boolean
  /** 是否可以访问图片 */
  canGallery: boolean
  /** 是否有权限被永久拒绝（需引导到设置页） */
  shouldGuideSettings: boolean
  /** 请求单个权限 */
  requestPermission: (permission: PermissionType) => Promise<void>
  /** 打开应用设置页 */
  openSettings: () => Promise<void>
  /** 手动刷新权限状态 */
  refresh: () => Promise<void>
}

export function usePermissions(): UsePermissionsReturn {
  const bridge = useBridge()
  const [status, setStatus] = useState<PermissionStatusData>(DEFAULT_STATUSES)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    if (!bridge.isAvailable()) {
      setLoading(false)
      return
    }
    try {
      const data = await bridge.getPermissionStatus()
      setStatus(data)
    } catch {
      // 查询失败保持默认值
    } finally {
      setLoading(false)
    }
  }, [bridge])

  // 挂载时查询权限状态
  useEffect(() => {
    refresh()
  }, [refresh])

  // 监听异步权限结果
  useEffect(() => {
    return bridge.onPermissionResult(() => {
      // 权限结果推送后重新查询全量状态
      refresh()
    })
  }, [bridge, refresh])

  const requestPermission = useCallback(async (permission: PermissionType) => {
    if (!bridge.isAvailable()) return
    try {
      await bridge.requestPermission(permission)
      // 结果通过 onPermissionResult 异步推送
    } catch {
      // 请求失败
    }
  }, [bridge])

  const openSettings = useCallback(async () => {
    if (!bridge.isAvailable()) return
    try {
      await bridge.openPermissionSettings()
    } catch {
      // 打开设置失败
    }
  }, [bridge])

  const shouldGuideSettings = Object.values(status.permissions).some(
    s => s === 'permanently_denied'
  )

  return {
    status,
    loading,
    canRecord: status.canRecordAudio,
    canCamera: status.canUseCamera,
    canGallery: status.canAccessImages,
    shouldGuideSettings,
    requestPermission,
    openSettings,
    refresh,
  }
}
