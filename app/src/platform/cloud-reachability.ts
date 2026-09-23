/**
 * 「云端是否可达」——T18-8（REQ-71）的在线判定。
 *
 * **为什么不是 `navigator.onLine`**：它只说「设备有没有网络接口」，不说「我们的服务端
 * 能不能答」。仓库里有实证——WorkManager 因网络缺 `VALIDATED`（captive portal）被卡死
 * 时 `navigator.onLine` 仍为 true。旧判据还混进了 `bridge.isAvailable()`（那是「WebView
 * 里注入了桥接口吗」，与网络无关），三者合起来会把「连不上服务端」判成在线。
 *
 * **判据分两层**（廉价负判据 + 真实正判据）：
 *   ① 操作系统说没有网络接口（`navigator.onLine === false`）→ 直接判不可达，
 *      **不做探测**（探测必然白等一轮超时；而 adb reverse / VPN 这类「无接口仍可达」
 *      的形态属于开发期特例，代价只是回落本地快路径，可接受）。
 *   ② 否则**真打一次服务端**：`auth.me`（`GET /me`）。它是既有命令里最轻的一次真实往返
 *      （`sync.getStatus` 只读本地 `engine.status`，不碰网络；`auth.triggerSync` 是整轮
 *      push+pull，重且会改服务端状态，不能当探测用）。故**不新增桥命令**——桥计数保持 85。
 *
 * **失败语义**：`auth_lost` / `server_error` 都算**可达**——服务端答了，只是这次请求不
 * 被接受；那属于 AI 链路的报错，不该被译成「离线」。只有 transport 层的问题
 * （`network_error` / `timeout` / 桥异常）才算不可达。
 *
 * **缓存**：探测结果带 TTL（`TTL_MS`），避免每次输入都多一次往返。三个失效时机——
 * ① TTL 到期；② 云端调用失败（`invalidate()`，见 use-assistant：一次失败就说明这次的
 * 「可达」判断过期了，下一次输入重新探）；③ 系统 online/offline 事件（用户插拔网络时
 * 立刻纠正，不必等 TTL）。
 */

import type { BridgeError } from './bridge/bridge-types'

/** 探测结果的有效期（ms）。够短以免误判滞留，够长以免每次输入都多一次往返。 */
const TTL_MS = 30_000

/** 探测超时（ms）。**必须短于 `DEFAULT_TIMEOUT`（10s）**——冷启动第一次输入要等它，
 *  服务端不可达时不能把首字拖到 10s。 */
const PROBE_TIMEOUT_MS = 4_000

/** 本模块需要的最小桥能力（便于测试注入 mock，不依赖 NativeBridge 整个类）。 */
export interface ReachabilityBridge {
  isAvailable(): boolean
  getMe(options?: { timeoutMs?: number }): Promise<unknown>
}

let cached: { at: number; value: boolean } | null = null
let attached = false

/**
 * 云端是否可达。命中缓存即返回；否则真打一次服务端并写回缓存。
 *
 * 调用方拿到 `false` 时**必须**走本地快路径（离线语义），拿到 `true` 才允许判「在线」——
 * 在线意味着本地 NLU 被关闭（REQ-71）。
 */
export async function isCloudReachable(bridge: ReachabilityBridge): Promise<boolean> {
  if (cached && Date.now() - cached.at < TTL_MS) return cached.value
  const value = await probe(bridge)
  cached = { at: Date.now(), value }
  return value
}

/** 让下一次 `isCloudReachable` 重新探测（云端调用失败 / 系统网络事件时调）。 */
export function invalidateCloudReachability(): void {
  cached = null
}

/** 测试用：清缓存 + 摘掉网络事件监听（模块级单例，逐用例复位防串）。 */
export function resetCloudReachabilityForTest(): void {
  cached = null
  if (attached && typeof window !== 'undefined') {
    window.removeEventListener('online', invalidateCloudReachability)
    window.removeEventListener('offline', invalidateCloudReachability)
  }
  attached = false
}

async function probe(bridge: ReachabilityBridge): Promise<boolean> {
  // **先挂监听再判**：两条早退路径（无网络接口 / 桥不可用）同样要吃到「网络回来了」这个
  // 事件——否则离线时缓存的 `false` 会在恢复后残留到 TTL 到期（最多 30s），而那正是用户
  // 重新开始输入的时刻。
  watchNetworkEvents()
  if (typeof navigator !== 'undefined' && navigator.onLine === false) return false
  // 桥不可用 = 环境不支持云端（纯浏览器开发态 / JS 与 APK 版本不同步），不是网络问题
  if (!bridge.isAvailable()) return false
  try {
    await bridge.getMe({ timeoutMs: PROBE_TIMEOUT_MS })
    return true
  } catch (err) {
    const code = (err as Partial<BridgeError> | null)?.code
    // 服务端答复了（只是这次请求不被接受）→ 网络是通的
    return code === 'auth_lost' || code === 'server_error'
  }
}

/** 系统网络状态一变就作废缓存：用户插拔网络时立刻纠正，不必等 TTL 到期。 */
function watchNetworkEvents(): void {
  if (attached || typeof window === 'undefined') return
  attached = true
  window.addEventListener('online', invalidateCloudReachability)
  window.addEventListener('offline', invalidateCloudReachability)
}