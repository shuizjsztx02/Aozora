/**
 * T18-8（REQ-71）：云端可达性探测。
 *
 * 这是「在线 / 离线」的唯一判据——它判错的两个方向都有代价：
 * 判成在线而云端其实不可达 → 输入走云端（本地 NLU 已关闭）却没有结果；
 * 判成离线而云端其实可达 → 用户拿不到云端能力。故逐条钉住语义。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  isCloudReachable, invalidateCloudReachability, resetCloudReachabilityForTest,
} from '../../platform/cloud-reachability'

type GetMe = (options?: { timeoutMs?: number }) => Promise<unknown>
type GetMeMock = ReturnType<typeof vi.fn<GetMe>>

function bridgeMock(overrides: { available?: boolean; getMe?: GetMeMock } = {}) {
  return {
    isAvailable: () => overrides.available ?? true,
    getMe: overrides.getMe ?? vi.fn<GetMe>().mockResolvedValue({ id: 'u-1' }),
  }
}

function setOnLine(value: boolean) {
  Object.defineProperty(navigator, 'onLine', { value, configurable: true })
}

beforeEach(() => { resetCloudReachabilityForTest() })
afterEach(() => { setOnLine(true); resetCloudReachabilityForTest() })

describe('cloud-reachability — 判据', () => {
  it('系统说没有网络接口 → 不可达，且**不做探测**（省掉必然白等的一轮超时）', async () => {
    setOnLine(false)
    const getMe = vi.fn<GetMe>().mockResolvedValue({})
    expect(await isCloudReachable(bridgeMock({ getMe }))).toBe(false)
    expect(getMe).not.toHaveBeenCalled()
  })

  it('桥不可用（纯浏览器态 / JS 与 APK 不同步）→ 不可达，不探测', async () => {
    const getMe = vi.fn<GetMe>().mockResolvedValue({})
    expect(await isCloudReachable(bridgeMock({ available: false, getMe }))).toBe(false)
    expect(getMe).not.toHaveBeenCalled()
  })

  it('服务端答了 → 可达（真打一次 GET /me，用短超时而非默认 10s）', async () => {
    const getMe = vi.fn<GetMe>().mockResolvedValue({ id: 'u-1' })
    expect(await isCloudReachable(bridgeMock({ getMe }))).toBe(true)
    expect(getMe).toHaveBeenCalledTimes(1)
    expect(getMe.mock.calls[0][0]).toMatchObject({ timeoutMs: 4000 })
  })

  it('auth_lost / server_error 都算可达——服务端答复了，只是这次请求不被接受', async () => {
    for (const code of ['auth_lost', 'server_error']) {
      resetCloudReachabilityForTest()
      const getMe = vi.fn<GetMe>().mockRejectedValue(Object.assign(new Error('x'), { code }))
      expect(await isCloudReachable(bridgeMock({ getMe })), code).toBe(true)
    }
  })

  it('transport 层失败 → 不可达（network_error / timeout / 无 code 的异常）', async () => {
    for (const err of [
      Object.assign(new Error('x'), { code: 'network_error' }),
      Object.assign(new Error('x'), { code: 'timeout' }),
      new Error('boom'),
    ]) {
      resetCloudReachabilityForTest()
      const getMe = vi.fn<GetMe>().mockRejectedValue(err)
      expect(await isCloudReachable(bridgeMock({ getMe }))).toBe(false)
    }
  })
})

describe('cloud-reachability — 缓存与失效', () => {
  it('TTL 内命中缓存，不重复探测', async () => {
    const getMe = vi.fn<GetMe>().mockResolvedValue({})
    const bridge = bridgeMock({ getMe })
    await isCloudReachable(bridge)
    await isCloudReachable(bridge)
    await isCloudReachable(bridge)
    expect(getMe).toHaveBeenCalledTimes(1)
  })

  it('invalidate 之后重新探测（云端调用失败 / 网络事件都会调它）', async () => {
    const getMe = vi.fn<GetMe>().mockResolvedValue({})
    const bridge = bridgeMock({ getMe })
    expect(await isCloudReachable(bridge)).toBe(true)
    invalidateCloudReachability()
    expect(await isCloudReachable(bridge)).toBe(true)
    expect(getMe).toHaveBeenCalledTimes(2)
  })

  it('系统 online/offline 事件使缓存作废（插拔网络后不必等 TTL）', async () => {
    const getMe = vi.fn<GetMe>().mockResolvedValue({})
    const bridge = bridgeMock({ getMe })
    expect(await isCloudReachable(bridge)).toBe(true) // 同时挂上事件监听
    window.dispatchEvent(new Event('offline'))
    expect(await isCloudReachable(bridge)).toBe(true)
    expect(getMe).toHaveBeenCalledTimes(2)
  })

  it('★ 无接口判负之后网络恢复：online 事件立刻翻案（否则残留 30s 误判离线）', async () => {
    setOnLine(false)
    const getMe = vi.fn<GetMe>().mockResolvedValue({})
    const bridge = bridgeMock({ getMe })
    expect(await isCloudReachable(bridge)).toBe(false)
    expect(getMe).not.toHaveBeenCalled() // 早退路径确实没探测

    setOnLine(true)
    window.dispatchEvent(new Event('online')) // 系统恢复网络 —— 早退路径也必须吃到这个事件
    expect(await isCloudReachable(bridge)).toBe(true)
    expect(getMe).toHaveBeenCalledTimes(1)
  })

  it('判错后立刻纠正：先是「可达」、真打失败后转「不可达」', async () => {
    const getMe = vi.fn<GetMe>().mockResolvedValue({})
    const bridge = bridgeMock({ getMe })
    expect(await isCloudReachable(bridge)).toBe(true)
    // 云端调用失败 → 调用方 invalidate → 下一次探测拿到真相
    getMe.mockRejectedValue(Object.assign(new Error('x'), { code: 'network_error' }))
    invalidateCloudReachability()
    expect(await isCloudReachable(bridge)).toBe(false)
  })
})