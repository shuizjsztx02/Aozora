/**
 * PersonasPage 组件测试 — 第十一轮 T11-7（REQ-28 / 决策 #8）。
 *
 * 覆盖（卡验收）：
 * - 使用中徽标 + 置前；"使用"按钮 → setMeta('active_persona_id', id)
 * - 新建 payload：UUID id + name/avatar/persona + version 1
 * - 编辑回填 → updatePersona version+1（avatar 允许显式 null）
 * - 必填/长度校验（名称空、人设空 → 不发起写入）
 * - 删除 confirm → archivePersona；使用中角色文案含回退提示
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { PersonaData } from '../../platform/bridge/bridge-types'

const mockBridge = vi.hoisted(() => ({
  listPersonas: vi.fn(),
  getMeta: vi.fn(),
  setMeta: vi.fn(),
  insertPersona: vi.fn(),
  updatePersona: vi.fn(),
  archivePersona: vi.fn(),
  pickIcon: vi.fn(),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

const mockNavigate = vi.hoisted(() => vi.fn())
vi.mock('react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

import { PersonasPage } from '../../features/settings/PersonasPage'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function persona(id: string, name: string, overrides: Partial<PersonaData> = {}): PersonaData {
  return {
    id, name, avatar: '😊', persona: `${name}的人设文本`,
    createdAt: 1, updatedAt: 1, version: 1,
    ...overrides,
  }
}

const renderLoaded = async (personas: PersonaData[], activeId: string | null = null) => {
  mockBridge.listPersonas.mockResolvedValue(personas)
  mockBridge.getMeta.mockResolvedValue(activeId ? { active_persona_id: activeId } : {})
  render(<PersonasPage />)
  await waitFor(() => expect(screen.queryByText('加载中…')).toBeNull())
}

describe('PersonasPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.setMeta.mockResolvedValue({ success: true })
    mockBridge.insertPersona.mockResolvedValue({ id: 'x' })
    mockBridge.updatePersona.mockResolvedValue({ id: 'x' })
    mockBridge.archivePersona.mockResolvedValue({ success: true })
  })

  it('无 active 键 → 回退"小贝"为使用中并置前', async () => {
    await renderLoaded([persona('p-1', '老陈'), persona('p-2', '小贝')])
    const badge = await screen.findByTestId('persona-active-badge-p-2')
    expect(badge).toHaveTextContent('使用中')
    // 置前：小贝卡在老陈卡之前
    const cards = screen.getAllByTestId(/^persona-card-/)
    expect(cards[0]).toHaveTextContent('小贝')
    // 使用中行无"使用"按钮，非使用中行有
    expect(screen.queryByTestId('persona-use-p-2')).toBeNull()
    expect(screen.getByTestId('persona-use-p-1')).toBeInTheDocument()
  })

  it('点"使用" → setMeta(active_persona_id) 并重载', async () => {
    await renderLoaded([persona('p-1', '小贝'), persona('p-2', '饭团')], 'p-1')
    const user = userEvent.setup()
    await user.click(screen.getByTestId('persona-use-p-2'))
    expect(mockBridge.setMeta).toHaveBeenCalledWith('active_persona_id', 'p-2')
    await waitFor(() => expect(mockBridge.listPersonas).toHaveBeenCalledTimes(2))
  })

  it('新建：昵称+人设填写后 insertPersona 全字段 payload（UUID id，version 1）', async () => {
    await renderLoaded([])
    const user = userEvent.setup()
    await user.click(screen.getByTestId('persona-new'))
    await user.type(screen.getByTestId('persona-name-input'), '测试角色')
    await user.type(screen.getByTestId('persona-text-input'), '这是一段人设描述')
    await user.click(screen.getByTestId('persona-save'))

    expect(mockBridge.insertPersona).toHaveBeenCalledWith({
      id: expect.stringMatching(UUID_RE),
      name: '测试角色',
      avatar: null,
      persona: '这是一段人设描述',
      version: 1,
    })
  })

  it('编辑：回填后 updatePersona version+1，avatar 显式传当前值', async () => {
    await renderLoaded([persona('p-1', '小贝', { avatar: '🐱', version: 4 })])
    const user = userEvent.setup()
    await user.click(screen.getByTestId('persona-edit-p-1'))
    const nameInput = screen.getByTestId('persona-name-input')
    expect(nameInput).toHaveValue('小贝')
    await user.clear(nameInput)
    await user.type(nameInput, '小贝酱')
    await user.click(screen.getByTestId('persona-save'))

    expect(mockBridge.updatePersona).toHaveBeenCalledWith({
      id: 'p-1',
      name: '小贝酱',
      avatar: '🐱',
      persona: '小贝的人设文本',
      version: 5,
    })
  })

  it('必填校验：名称为空时保存报错，不发起写入', async () => {
    await renderLoaded([])
    const user = userEvent.setup()
    await user.click(screen.getByTestId('persona-new'))
    await user.type(screen.getByTestId('persona-text-input'), '只有人设没有名字')
    await user.click(screen.getByTestId('persona-save'))
    expect(await screen.findByTestId('persona-form-error')).toHaveTextContent('昵称')
    expect(mockBridge.insertPersona).not.toHaveBeenCalled()
  })

  it('删除：confirm 后 archivePersona；使用中角色确认文案含回退提示', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockImplementation(msg => {
      expect(msg).toContain('自动回退默认角色')
      return true
    })
    await renderLoaded([persona('p-1', '小贝')])
    const user = userEvent.setup()
    await user.click(screen.getByTestId('persona-delete-p-1'))
    await waitFor(() => expect(mockBridge.archivePersona).toHaveBeenCalledWith('p-1'))
    confirmSpy.mockRestore()
  })
})
