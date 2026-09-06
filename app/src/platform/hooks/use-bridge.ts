/**
 * Bridge React hooks — 封装 NativeBridge 的调用。
 */

import { useEffect, useRef, useCallback } from 'react'
import { NativeBridge } from '../bridge/native-bridge'
import { useSyncStore } from '../store/sync-store'

/** 全局 bridge 单例 */
let _bridge: NativeBridge | null = null

function getBridge(): NativeBridge {
  if (!_bridge) {
    _bridge = new NativeBridge()
  }
  return _bridge
}

/** 获取 bridge 实例 */
export function useBridge() {
  const ref = useRef<NativeBridge>(null!)
  if (!ref.current) {
    ref.current = getBridge()
  }
  return ref.current
}

/** 初始化同步事件监听 */
export function useInitSync() {
  const initListener = useSyncStore(s => s.initListener)

  useEffect(() => {
    return initListener()
  }, [initListener])
}

/** 插入交易 hook */
export function useInsertTransaction() {
  const bridge = useBridge()

  return useCallback(async (input: Record<string, unknown>) => {
    const id = `tx_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
    await bridge.call('transaction.insert', { ...input, id, occurredAt: Date.now() })
    return id
  }, [bridge])
}

/** 软删除交易 hook */
export function useDeleteTransaction() {
  const bridge = useBridge()

  return useCallback(async (id: string) => {
    await bridge.call('transaction.softDelete', { id })
  }, [bridge])
}
