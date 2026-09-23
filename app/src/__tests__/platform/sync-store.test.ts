/**
 * T18-2（REQ-67①）：同步事件通道的真实契约。
 *
 * 原生侧 `MainActivity.dispatchSyncEvent` 注入的 JS 是**调用函数**的形式：
 *
 *   if (typeof window.__aozoraSyncEvent__ === 'function') {
 *     window.__aozoraSyncEvent__({state, pendingCount, lastSyncedAt, …})
 *   }
 *
 * 旧实现挂的是 `Object.defineProperty` 的 getter/setter（指望原生**赋值**一个对象），
 * 于是那个 `typeof … === 'function'` 判据在真机上恒为假——**事件从未到达 store**
 * （SyncBadge 恒显「已同步」、AuthLostBanner 永不出现、页面拿不到「同步完成」信号）。
 * 本用例把「原生调用形式」钉成回归：装载后该全局名必须是**函数**，且调用它能落进 store。
 */
import { describe, it, expect, afterEach } from 'vitest'
import { useSyncStore } from '../../platform/store/sync-store'
import type { SyncEvent } from '../../platform/bridge/bridge-types'

const event = (over: Partial<SyncEvent> = {}): SyncEvent => ({
  state: 'idle',
  pendingCount: 0,
  lastSyncedAt: 0,
  lastError: undefined,
  failedMutations: [],
  ...over,
})

afterEach(() => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  delete (window as any).__aozoraSyncEvent__
})

describe('sync-store 事件通道（T18-2 / REQ-67①）', () => {
  it('initListener 挂的是函数，原生调用形式能把事件写进 store', () => {
    const stop = useSyncStore.getState().initListener()
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const installed = (window as any).__aozoraSyncEvent__
    expect(typeof installed).toBe('function')

    installed(event({ state: 'pulling' }))
    expect(useSyncStore.getState().status.state).toBe('pulling')

    // 终态：lastSyncedAt 前进（页面侧的重取订阅就以它为信号）
    installed(event({ state: 'idle', lastSyncedAt: 1234 }))
    expect(useSyncStore.getState().status.lastSyncedAt).toBe(1234)
    expect(useSyncStore.getState().status.state).toBe('idle')

    stop()
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((window as any).__aozoraSyncEvent__).toBeUndefined()
  })

  it('已有订阅者时串在其后，不打断既有 handler', () => {
    const seen: SyncEvent[] = []
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(window as any).__aozoraSyncEvent__ = (e: SyncEvent) => { seen.push(e) }

    const stop = useSyncStore.getState().initListener()
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(window as any).__aozoraSyncEvent__(event({ lastSyncedAt: 77 }))

    expect(useSyncStore.getState().status.lastSyncedAt).toBe(77)
    expect(seen).toHaveLength(1)

    // cleanup 只摘掉自己挂的那个，不误删后来者的订阅
    const later = () => {}
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(window as any).__aozoraSyncEvent__ = later
    stop()
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((window as any).__aozoraSyncEvent__).toBe(later)
  })

  it('DOM CustomEvent 形式仍然被接收（既有约定不回退）', () => {
    const stop = useSyncStore.getState().initListener()
    window.dispatchEvent(new CustomEvent('__aozoraSyncEvent__', { detail: event({ lastSyncedAt: 9 }) }))
    expect(useSyncStore.getState().status.lastSyncedAt).toBe(9)
    stop()
  })
})