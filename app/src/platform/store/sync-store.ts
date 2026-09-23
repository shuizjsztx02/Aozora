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
    const apply = (syncEvent: SyncEvent | null | undefined) => {
      if (!syncEvent) return
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

    // 监听自定义事件
    const handler = (event: Event) => {
      apply((event as CustomEvent<SyncEvent>).detail)
    }
    window.addEventListener('__aozoraSyncEvent__', handler as EventListener)

    /**
     * T18-2（REQ-67①）：原生侧推事件用的是**调用函数**的形式——
     * `MainActivity.dispatchSyncEvent` 注入的 JS 是
     * `if (typeof window.__aozoraSyncEvent__ === 'function') window.__aozoraSyncEvent__({…})`。
     * 旧实现挂的是 getter/setter（指望原生**赋值**一个对象），于是那个 `typeof`
     * 判据在真机上恒为假：**事件从未到达 store**——表现为 SyncBadge 恒显「已同步」、
     * AuthLostBanner 永不出现、页面也拿不到「同步完成」的信号（本卡 ③ 依赖它）。
     * 故此处改为挂**函数**（与 `Router` 的 `__aozoraNavigate__`、`native-bridge` 的
     * `onSyncEvent` 同一约定）；已有函数时串在其后，不打断既有订阅。
     */
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const prev = (window as any).__aozoraSyncEvent__
    const wrapped = (event: SyncEvent) => {
      apply(event)
      if (typeof prev === 'function') prev(event)
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(window as any).__aozoraSyncEvent__ = wrapped

    return () => {
      window.removeEventListener('__aozoraSyncEvent__', handler as EventListener)
      // 只清理自己挂上的那个，避免撤掉后来者的订阅
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if ((window as any).__aozoraSyncEvent__ === wrapped) {
        delete (window as any).__aozoraSyncEvent__
      }
    }
  },
}))

/** Selector hook：获取同步状态 */
export function useSyncStatus() {
  return useSyncStore(s => s.status)
}
