/**
 * T18-2（REQ-67①）：数据页的「同步完成 / 重新可见」重取。
 *
 * 首页、明细、报表三处原先都是**挂载期单次读**（`useEffect(() => loadData(), [loadData])`）——
 * 页面上再没有第二条触发路径。于是这两种情况下页面长期停在旧值：
 *   ① 写入发生在**服务端**（AI 的 v2 执行），本地 Room 要靠 pull 才知道；
 *   ② 页面**已经挂载**，之后才有同步把新数据拉回来（含后台 WorkManager 那轮）。
 * 用户看到的就是「改好了，但各处显示都没变，只有切页/重启才更新」（REQ-67 症状）。
 *
 * 本 hook 补两条触发，都调同一个 `refresh`：
 *   · 同步轮**完成**：store 收到原生推的终态（`lastSyncedAt` 前进）→ 重取；
 *   · 页面**重新可见**：`visibilitychange` → visible（应用切后台再回来，期间可能有
 *     一次后台同步）→ 重取。
 *
 * **首帧只记基准不重取**：挂载那一次读已经由页面自己的 `useEffect` 负责，这里不重复读；
 * 基准取的是**挂载那一刻**的值。此后 `lastSyncedAt` **只要变了就重取**——包括
 * `0 → 非零`：冷启动时应用刚起来、首轮同步尚未完成，用户已经切到首页（store 里还是
 * 0），若把这次变化当作基准而非变化，首轮同步把权威行拉回来后页面不会重取，
 * 用户得再切一次页才看得见新数据（与 REQ-67 同一症状，只是触发在冷启动）。
 *
 * 调用方应传入一个**静默**的重取回调（不置 loading / 不闪骨架），否则每次后台同步
 * 都会让正在阅读的页面闪一下加载态。
 */

import { useEffect, useRef } from 'react'
import { useSyncStore } from '../store/sync-store'

export function useRefreshOnSync(refresh: () => void): void {
  const lastSyncedAt = useSyncStore(s => s.status.lastSyncedAt)
  /** null = 还没记过基准（首帧只记不取） */
  const seenRef = useRef<number | null>(null)

  useEffect(() => {
    if (seenRef.current === null) {
      seenRef.current = lastSyncedAt
      return
    }
    if (lastSyncedAt === seenRef.current) return
    seenRef.current = lastSyncedAt
    refresh()
  }, [lastSyncedAt, refresh])

  useEffect(() => {
    if (typeof document === 'undefined') return
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') refresh()
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => document.removeEventListener('visibilitychange', onVisibilityChange)
  }, [refresh])
}