/**
 * T-M4-04a: 权限 React hook。
 *
 * 封装 PermissionManager 桥命令，提供响应式权限状态。
 *
 * - 挂载时自动查询权限状态
 * - 监听异步权限结果推送（Android 权限对话框关闭后）
 * - 提供便捷判断：canCamera / canGallery / shouldGuideSettings
 */

import { useState, useEffect, useCallback } from 'react'
import { useBridge } from './use-bridge'
import type { PermissionType, PermissionStatusData } from '../bridge/bridge-types'

const DEFAULT_STATUSES: PermissionStatusData = {
  permissions: {
    CAMERA: 'not_requested',
    READ_MEDIA_IMAGES: 'not_requested',
    POST_NOTIFICATIONS: 'not_requested',
  },
  canUseCamera: false,
  canAccessImages: false,
}

export interface UsePermissionsReturn {
  /** 完整权限状态 */
  status: PermissionStatusData
  /** 是否正在加载 */
  loading: boolean
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
  /** 手动刷新权限状态（T14-6：返回查询结果供调用方即时判定，失败回默认态） */
  refresh: () => Promise<PermissionStatusData>
}

export function usePermissions(): UsePermissionsReturn {
  const bridge = useBridge()
  const [status, setStatus] = useState<PermissionStatusData>(DEFAULT_STATUSES)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async (): Promise<PermissionStatusData> => {
    if (!bridge.isAvailable()) {
      setLoading(false)
      return DEFAULT_STATUSES
    }
    try {
      const data = await bridge.getPermissionStatus()
      setStatus(data)
      return data
    } catch (error) {
      // 查询失败保持默认值
      console.warn('[usePermissions] refresh failed:', error)
      return DEFAULT_STATUSES
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
      const { pending } = await bridge.requestPermission(permission)
      // P3-4 fix: Android 侧已授予时 Bridge 同步返回 {pending: false}，
      // 此时不走 onPermissionResult 推送，需主动刷新以更新 UI 状态。
      if (!pending) refresh()
    } catch (error) {
      console.warn('[usePermissions] requestPermission failed:', error)
    }
  }, [bridge, refresh])

  const openSettings = useCallback(async () => {
    if (!bridge.isAvailable()) return
    try {
      await bridge.openPermissionSettings()
    } catch (error) {
      console.warn('[usePermissions] openSettings failed:', error)
    }
  }, [bridge])

  const shouldGuideSettings = Object.values(status.permissions).some(
    s => s === 'permanently_denied'
  )

  return {
    status,
    loading,
    canCamera: status.canUseCamera,
    canGallery: status.canAccessImages,
    shouldGuideSettings,
    requestPermission,
    openSettings,
    refresh,
  }
}
