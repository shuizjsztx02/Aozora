/**
 * T17-3（REQ-65）：聊天页进入即定位尾部，取消「从第一条滚下来」。
 *
 * 三条判据（对应 AssistantPage 里那条 useLayoutEffect）：
 * - 挂载首帧 → `behavior: 'auto'`（布局阶段瞬移，不给浏览器画出「列表顶部」那一帧）
 * - 会话中追加一条消息（已在尾部）→ `behavior: 'smooth'`
 * - 非首帧但离底超过一屏（服务端会话正文异步补进来）→ `behavior: 'auto'`
 *
 * jsdom 不实现滚动：下面把 scrollHeight / clientHeight / scrollTop 三个几何量接到
 * 可控变量与 WeakMap 上，并让 scrollTo 真的把 scrollTop 推到底——否则「滚过去之后
 * 就到底了」这一步缺失，距离判据会永远停在「离底很远」，测不出中间那条真实路径。
 *
 * **本文件测不到的部分**：`useLayoutEffect` 与 `useEffect` 的差别是「有没有机会先画出
 * 顶部那一帧」——jsdom 没有绘制，RTL 的 act() 又同时冲掉布局与被动副作用，两种写法
 * 在这里表现一致。故「进入时无闪烁」只能由 T17-4 的真机走查判定（≥30 条会话点「＋」）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { AssistantPage } from '../../features/assistant/AssistantPage'
import { useAssistantChatStore } from '../../platform/store/assistant-chat-store'
import type { AssistantMessage } from '../../features/assistant/use-assistant'

const hookState = vi.hoisted(() => ({
  phase: 'idle' as 'idle' | 'processing' | 'executing',
}))

const mockBridge = vi.hoisted(() => ({
  listCategories: vi.fn().mockResolvedValue([]),
  listBooks: vi.fn().mockResolvedValue([{ id: 'book-1', name: '默认账本', isMaster: false }]),
  listAccounts: vi.fn().mockResolvedValue([]),
  getMeta: vi.fn().mockResolvedValue({}),
  listPersonas: vi.fn().mockResolvedValue([]),
  listSavingsGoals: vi.fn().mockResolvedValue([]),
  listBudgets: vi.fn().mockResolvedValue([]),
  triggerSync: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({ useBridge: () => mockBridge }))
vi.mock('../../platform/hooks/use-permissions', () => ({
  usePermissions: () => ({
    canRecord: false,
    status: { permissions: { RECORD_AUDIO: 'not_requested' } },
    requestPermission: vi.fn().mockResolvedValue(undefined),
    openSettings: vi.fn().mockResolvedValue(undefined),
    refresh: vi.fn().mockResolvedValue(undefined),
  }),
}))
vi.mock('../../features/assistant/use-assistant', async () => {
  const { useAssistantChatStore } = await import('../../platform/store/assistant-chat-store')
  return {
    useAssistant: () => ({
      messages: useAssistantChatStore(s => s.messages),
      phase: hookState.phase,
      activeProposal: null,
      cardStatus: null,
      isSlow: false,
      pendingOp: null,
      pendingConfig: null,
      streaming: null,
      submitInput: vi.fn(),
      editProposal: vi.fn(),
      undoExecution: vi.fn(),
      confirmOp: vi.fn(),
      cancelOp: vi.fn(),
      confirmConfig: vi.fn(),
      cancelConfig: vi.fn(),
      cancelStream: vi.fn(),
      rejectApproval: vi.fn(),
      interrupted: null,
      retryInterrupted: vi.fn(),
      appendAssistantMessage: vi.fn(),
    }),
  }
})

const userMsg = (id: string, text: string): AssistantMessage => ({ id, role: 'user', text })

// ── jsdom 滚动几何量 ──────────────────────────────────────────────
let rect = { scrollHeight: 0, clientHeight: 0 }
const scrollTops = new WeakMap<Element, number>()
const scrollTo = vi.fn()
const originalScrollTo = Element.prototype.scrollTo

const lastOpts = () => scrollTo.mock.calls.at(-1)?.[0] as { top: number; behavior: string } | undefined

const renderPage = () =>
  render(
    <MemoryRouter>
      <AssistantPage />
    </MemoryRouter>,
  )

describe('AssistantPage — 进入即定位尾部（T17-3 / REQ-65）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    rect = { scrollHeight: 0, clientHeight: 0 }
    useAssistantChatStore.setState({ messages: [] })
    hookState.phase = 'idle'

    vi.spyOn(Element.prototype, 'scrollHeight', 'get').mockImplementation(() => rect.scrollHeight)
    vi.spyOn(Element.prototype, 'clientHeight', 'get').mockImplementation(() => rect.clientHeight)
    vi.spyOn(Element.prototype, 'scrollTop', 'get').mockImplementation(function (this: Element) {
      return scrollTops.get(this) ?? 0
    })
    vi.spyOn(Element.prototype, 'scrollTop', 'set').mockImplementation(function (this: Element, v: number) {
      scrollTops.set(this, v)
    })
    Element.prototype.scrollTo = function (this: Element, opts: ScrollToOptions) {
      scrollTo(opts)
      scrollTops.set(this, opts.top ?? 0)
    } as unknown as typeof Element.prototype.scrollTo
  })

  afterEach(() => {
    vi.restoreAllMocks()
    Element.prototype.scrollTo = originalScrollTo
  })

  it('挂载首帧即瞬移到底部（历史很长也不演「从第一条滚下来」）', () => {
    rect = { scrollHeight: 1200, clientHeight: 400 }
    useAssistantChatStore.setState({ messages: [userMsg('u1', '第一句'), userMsg('u2', '第二句')] })

    renderPage()

    // 首帧判据先于距离判据：即便离底很远（1200 - 0 - 400 > 400），也是瞬移不是动画
    expect(lastOpts()).toEqual({ top: 1200, behavior: 'auto' })
  })

  it('会话中追加一条消息 → 平滑跟到尾部', async () => {
    rect = { scrollHeight: 1200, clientHeight: 400 }
    useAssistantChatStore.setState({ messages: [userMsg('u1', '第一句')] })
    renderPage()
    expect(lastOpts()?.behavior).toBe('auto') // 首帧瞬移，scrollTop 落到 1200

    scrollTo.mockClear()
    await act(async () => {
      useAssistantChatStore.setState({
        messages: [userMsg('u1', '第一句'), userMsg('u2', '第二句')],
      })
    })

    expect(lastOpts()).toEqual({ top: 1200, behavior: 'smooth' })
  })

  it('非首帧但离底超过一屏（正文异步补进来）→ 也是瞬移', async () => {
    rect = { scrollHeight: 300, clientHeight: 400 }
    useAssistantChatStore.setState({ messages: [userMsg('u1', '第一句')] })
    renderPage()

    // 正文补进来：内容从「一屏装得下」变成「离底两屏」
    rect = { scrollHeight: 2000, clientHeight: 400 }
    scrollTo.mockClear()
    await act(async () => {
      useAssistantChatStore.setState({
        messages: [userMsg('u1', '第一句'), userMsg('u2', '补进来的正文')],
      })
    })

    expect(lastOpts()).toEqual({ top: 2000, behavior: 'auto' })
  })

  it('键盘顶起（resize）仍走平滑，不受上面判据影响', () => {
    rect = { scrollHeight: 1200, clientHeight: 400 }
    useAssistantChatStore.setState({ messages: [userMsg('u1', '第一句')] })
    renderPage()

    scrollTo.mockClear()
    act(() => {
      window.dispatchEvent(new Event('resize'))
    })

    expect(lastOpts()).toEqual({ top: 1200, behavior: 'smooth' })
  })
})