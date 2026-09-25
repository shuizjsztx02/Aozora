/**
 * T-M7-05f: 设备能力通道的**客户端执行器**——把 `device_request` 帧落成一次设备动作。
 *
 * 位置与职责
 * ----------
 * 服务端的 loop 产出设备指令（05e），帧经 `window.__aozoraAiStream__` 到达 WebView；
 * 本模块是它的落地侧：**能力名 → 桥调用**，然后把结局按协议回传（需结果类）。
 *
 * 它是一个**纯编排**模块（无 React、无业务状态），因此可以逐类断言。与本仓其它
 * 客户端模块同一条分工：投影与投影的消费在 `use-assistant-stream` / `use-assistant`，
 * 「一次设备动作怎么做」在这里。
 *
 * 两件事各自的位置（刻意的，不是遗漏）
 * ------------------------------------
 * ① **能力名 → 具体桥命令的分派与 status 归一化在原生侧**
 *    （`AozoraBridge.handleDeviceExecute`）。这一层只面对 `ok / denied /
 *    unavailable / failed` 四值——与服务端协议同构，不认识任何具体能力的错误码族。
 * ② **回传失败不重试**。服务端的挂起有 `ai_device_timeout_seconds` 自己的收敛路径
 *    （ADR-010 决策 4：超时是「没等到结果」的一条工具结果，不是故障），客户端在这
 *    里重试只会拖长用户已经看不到的等待。
 *
 * 与既有设备路径的关系：**只借不改**。`haptic.feedback` / `share.text` /
 * `widget.refresh` / `permissions.request` / `image.pick` 都是既有命令，本模块与
 * 原生分派表都只是调用它们，不触碰界面上那几条已有的直调路径。
 */

import type {
  DeviceExecuteResult,
  DeviceOutcomeStatus,
  DeviceRequestFrame,
  DeviceResultAck,
} from '../../platform/bridge/bridge-types'

/**
 * 执行器需要的桥能力，**全部可选**：旧 APK 的命令表里可能没有
 * `device.execute` / `device.result`（与 `AgentStreamBridge` 的能力探测同口径）。
 * 缺失时执行器安静地什么都不做——服务端会等满超时自行收敛，界面不会因此报错。
 */
export interface DeviceExecutorBridge {
  executeDevice?(
    capability: string, payload: Record<string, unknown>,
  ): Promise<DeviceExecuteResult>
  resultDevice?(
    requestId: string, status: DeviceOutcomeStatus, data?: Record<string, unknown>,
  ): Promise<DeviceResultAck>
}

/**
 * 一次设备指令的处理结局。
 *
 * - 四值之一（`ok` / `denied` / `unavailable` / `failed`）：本次执行的结果；
 * - `null`：桥没有设备通道能力（旧 APK），本次什么都没做。
 */
export type DeviceRunOutcome = DeviceOutcomeStatus | null

/**
 * 执行一条设备指令。需结果类在函数内**一并回传**——回传是这次执行的收尾，不是
 * 调用方还要记得去做的第二件事（漏掉它 = 服务端白等一个超时窗）。
 *
 * 帧的形态判据全在这里：`kind` 是不是需结果（决定要不要回传）。畸形帧（缺
 * `request_id` / `capability`）直接当没收到。
 */
export async function runDeviceRequest(
  bridge: DeviceExecutorBridge,
  frame: DeviceRequestFrame,
): Promise<DeviceRunOutcome> {
  if (!bridge.executeDevice) return null
  if (!frame.request_id || !frame.capability) return null

  let result: DeviceExecuteResult
  try {
    result = await bridge.executeDevice(frame.capability, frame.payload ?? {})
  } catch {
    // 桥层失败（命令未知 / 原生异常 / 旧 APK 无该 capability）：按失败结算。
    // **不静默吞掉**——需结果类要回传，否则服务端会白等满超时（用户侧只看到卡住）。
    result = { status: 'failed' }
  }
  if (frame.kind === 'result') {
    await deliverDeviceResult(bridge, frame.request_id, result.status, result.data)
  }
  return result.status
}

/**
 * 回传一次需结果类的结局。**任何失败都吞掉**（见文件头 ③）——包括服务端的 404
 * `device_request_not_found`，它由桥翻成 `{accepted: true}`，语义是「已经收敛过了」。
 *
 * `data` 只在 `ok` 时带上：服务端对非 ok 的结局不消费 data（`agent._device_outcome`
 * 走的是 `_failure_outcome`），带了也是白带。
 */
export async function deliverDeviceResult(
  bridge: DeviceExecutorBridge,
  requestId: string,
  status: DeviceOutcomeStatus,
  data?: Record<string, unknown>,
): Promise<boolean> {
  if (!bridge.resultDevice) return false
  try {
    await bridge.resultDevice(requestId, status, status === 'ok' ? data : undefined)
    return true
  } catch {
    return false
  }
}