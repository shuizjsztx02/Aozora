/**
 * T-M7-05f：设备能力通道**客户端执行器**的测试。
 *
 * 执行器是纯编排（无 React、无业务状态），所以可以对每条分派规则给出确定断言：
 * 单向不回传、需结果回传（含 data 的有无）、语音交给上层、桥缺席时安静退出、
 * 执行抛异常时按 failed 结算（**不能悬挂**——服务端在等这一次回传）。
 *
 * 桥本身的行为（能力名 → 具体命令、status 归一化）在原生侧，由 Kotlin 的
 * `AozoraBridgeDeviceTest` 覆盖；这里用假桥，只验证编排。
 */
import { describe, it, expect, vi } from 'vitest'
import {
  VOICE_CAPABILITY,
  deliverDeviceResult,
  runDeviceRequest,
  type DeviceExecutorBridge,
} from '../../features/assistant/device-executor'
import type { DeviceRequestFrame } from '../../platform/bridge/bridge-types'

function frame(over: Partial<DeviceRequestFrame> = {}): DeviceRequestFrame {
  return {
    request_id: 'dev-1',
    kind: 'one_way',
    capability: 'haptic.feedback',
    payload: { kind: 'success' },
    ...over,
  }
}

function fakeBridge(over: Partial<DeviceExecutorBridge> = {}): DeviceExecutorBridge & {
  executeDevice: ReturnType<typeof vi.fn>
  resultDevice: ReturnType<typeof vi.fn>
} {
  const executeDevice = vi.fn(async () => ({ status: 'ok' as const }))
  const resultDevice = vi.fn(async () => ({ accepted: true }))
  return { executeDevice, resultDevice, ...over } as never
}

describe('T-M7-05f 设备执行器 — 分派', () => {
  it('单向指令：执行，但**不回传**（回传凭据用不上）', async () => {
    const bridge = fakeBridge()
    const outcome = await runDeviceRequest(bridge, frame({ kind: 'one_way' }))

    expect(outcome).toBe('ok')
    expect(bridge.executeDevice).toHaveBeenCalledWith('haptic.feedback', { kind: 'success' })
    expect(bridge.resultDevice).not.toHaveBeenCalled()
  })

  it('需结果：执行后回传结局（ok 带 data）', async () => {
    const bridge = fakeBridge({
      executeDevice: vi.fn(async () => ({
        status: 'ok' as const,
        data: { attachmentId: 'att-1', mimeType: 'image/jpeg' },
      })),
    })
    const outcome = await runDeviceRequest(bridge, frame({
      kind: 'result', capability: 'image.pick', payload: { source: 'gallery' },
    }))

    expect(outcome).toBe('ok')
    expect(bridge.resultDevice).toHaveBeenCalledWith(
      'dev-1', 'ok', { attachmentId: 'att-1', mimeType: 'image/jpeg' },
    )
  })

  it('需结果 + 非 ok：回传**不带 data**（服务端对失败结局不消费 data）', async () => {
    const bridge = fakeBridge({
      executeDevice: vi.fn(async () => ({ status: 'denied' as const, data: { ignored: true } })),
    })
    const outcome = await runDeviceRequest(bridge, frame({
      kind: 'result', capability: 'permissions.request', payload: { permission: 'CAMERA' },
    }))

    expect(outcome).toBe('denied')
    expect(bridge.resultDevice).toHaveBeenCalledWith('dev-1', 'denied', undefined)
  })

  it('执行抛异常 → 按 failed 结算并回传（**不悬挂**，服务端在等这一发）', async () => {
    const bridge = fakeBridge({
      executeDevice: vi.fn(async () => { throw new Error('boom') }),
    })
    const outcome = await runDeviceRequest(bridge, frame({ kind: 'result' }))

    expect(outcome).toBe('failed')
    expect(bridge.resultDevice).toHaveBeenCalledWith('dev-1', 'failed', undefined)
  })

  it('语音转写交给上层（`deferred`）：**不自动执行**，也不回传', async () => {
    const bridge = fakeBridge()
    const outcome = await runDeviceRequest(bridge, frame({
      kind: 'result', capability: VOICE_CAPABILITY, payload: {},
    }))

    expect(outcome).toBe('deferred')
    expect(bridge.executeDevice).not.toHaveBeenCalled()
    expect(bridge.resultDevice).not.toHaveBeenCalled()
  })

  it('桥没有设备通道能力（旧 APK）→ null，什么都不做', async () => {
    const outcome = await runDeviceRequest({}, frame({ kind: 'result' }))
    expect(outcome).toBeNull()
  })

  it('畸形帧（缺 request_id / capability）当没收到', async () => {
    const bridge = fakeBridge()
    expect(await runDeviceRequest(bridge, frame({ request_id: '' }))).toBeNull()
    expect(await runDeviceRequest(bridge, frame({ capability: '' }))).toBeNull()
    expect(bridge.executeDevice).not.toHaveBeenCalled()
  })

  it('payload 缺省时按空对象下发', async () => {
    const bridge = fakeBridge()
    await runDeviceRequest(bridge, {
      request_id: 'dev-2', kind: 'one_way', capability: 'widget.refresh',
    } as DeviceRequestFrame)
    expect(bridge.executeDevice).toHaveBeenCalledWith('widget.refresh', {})
  })
})

describe('T-M7-05f 设备执行器 — 回传', () => {
  it('回传失败被吞掉（服务端有超时收敛路径，客户端不重试）', async () => {
    const bridge = fakeBridge({
      resultDevice: vi.fn(async () => { throw new Error('offline') }),
    })
    expect(await deliverDeviceResult(bridge, 'dev-3', 'ok')).toBe(false)
  })

  it('桥没有回传能力 → false，不抛', async () => {
    expect(await deliverDeviceResult({}, 'dev-4', 'denied')).toBe(false)
  })

  it('ok 之外的结局不带 data（与执行路径同一口径）', async () => {
    const bridge = fakeBridge()
    await deliverDeviceResult(bridge, 'dev-5', 'unavailable', { stray: true })
    expect(bridge.resultDevice).toHaveBeenCalledWith('dev-5', 'unavailable', undefined)
  })
})