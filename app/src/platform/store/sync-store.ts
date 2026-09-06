/**
 * 同步状态 Zustand store。
 *
 * 数据流：
 *   Kotlin → window.__aozoraSyncEvent__ → sync-store → UI
 */

import { create } from 'zustand'
import type { SyncStatusData, SyncEvent } from '../../platform/bridge/bridge-types'

const defaultStatus: SyncStatusData = {
  state: 'idle',
  pendingCount: 0,
  lastSyncedAt: 0,
  lastError: undefined,
  failedMutations: [],
}

interface SyncStore {
  status: SyncStatusData
  /** 设置同步状态（由 sync event listener 调用） */
  setStatus: (status: SyncStatusData) => void
  /** 初始化事件监听 */
  initListener: () => () => void
}

export const useSyncStore = create<SyncStore>((set) => ({
  status: defaultStatus,

  setStatus: (status) => set({ status }),

  initListener: () => {
    const handler = (event: Event) => {
      const syncEvent = (event as CustomEvent<SyncEvent>).detail
      if (syncEvent) {
        set({
          status: {
            state: syncEvent.state,
            pendingCount: syncEvent.pendingCount,
            lastSyncedAt: syncEvent.lastSyncedAt,
            lastError: syncEvent.lastError,
            failedMutations: syncEvent.failedMutations ?? [],
          },
        })
      }
    }

    // 监听自定义事件
    window.addEventListener('__aozoraSyncEvent__', handler as EventListener)

    // 也处理 Kotlin 直接设置 window 属性的方式
    const origDescriptor = Object.getOwnPropertyDescriptor(window, '__aozoraSyncEvent__')
    let descriptorCleaned = false
    if (!origDescriptor) {
      let _val: SyncEvent | undefined
      Object.defineProperty(window, '__aozoraSyncEvent__', {
        get: () => _val,
        set: (val: SyncEvent) => {
          _val = val
          if (val) {
            set({
              status: {
                state: val.state,
                pendingCount: val.pendingCount,
                lastSyncedAt: val.lastSyncedAt,
                lastError: val.lastError,
                failedMutations: val.failedMutations ?? [],
              },
            })
          }
        },
        configurable: true,
      })
      descriptorCleaned = true
    }

    return () => {
      window.removeEventListener('__aozoraSyncEvent__', handler as EventListener)
      // 清理 defineProperty 的 setter，避免闭包引用泄漏
      if (descriptorCleaned) {
        delete (window as any).__aozoraSyncEvent__
      }
    }
  },
}))

/** Selector hook：获取同步状态 */
export function useSyncStatus() {
  return useSyncStore(s => s.status)
}
