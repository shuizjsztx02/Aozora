/**
 * ScheduledPage 组件测试 — 第十一轮 T11-5（REQ-26 / 决策 #4/#9）。
 *
 * 覆盖（卡验收）：
 * - insert payload 全字段 + UUID + version 1（默认账本过滤 master / 默认账户 / 无限制）
 * - enabled 切换走 update 局部合并（只传 {id, enabled, version+1}）
 * - 账本下拉无 master
 * - 必填校验（名称为空保存禁用；分类未选给可读错误，不发起写入）
 * - 列表卡：周期文案/账本·分类副行/上次·下次执行小字
 * - 点击卡片进编辑（回填 + update version+1）；删除 confirm → archive
 * - T14-5（REQ-43）：新建默认执行时刻 8:00 进 payload / 编辑回填 + 步进进
 *   update payload / 列表周期文案含时刻
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { todayBizDate } from '../../domain/date'
import type { ScheduledRuleData } from '../../platform/bridge/bridge-types'

const mockBridge = vi.hoisted(() => ({
  listScheduledRules: vi.fn(),
  listBooks: vi.fn(),
  listCategories: vi.fn(),
  listAccounts: vi.fn(),
  getMeta: vi.fn(),
  insertScheduledRule: vi.fn(),
  updateScheduledRule: vi.fn(),
  archiveScheduledRule: vi.fn(),
}))

vi.mock('../../platform/hooks/use-bridge', () => ({
  useBridge: () => mockBridge,
}))

const mockNavigate = vi.hoisted(() => vi.fn())
vi.mock('react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

import { ScheduledPage } from '../../features/settings/ScheduledPage'

const TZ = 'Asia/Shanghai'
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

const BOOKS = [
  { id: 'book-master', ownerId: 'local', name: '总账本', icon: '📒', version: 1, isMaster: true },
  { id: 'book-1', ownerId: 'local', name: '日常账本', icon: '☕', version: 1 },
]
const CATS = [
  { id: 'cat-food', kind: 'expense' as const, name: '饮食', parentId: null, version: 1 },
  { id: 'cat-transport', kind: 'expense' as const, name: '交通', parentId: null, version: 1 },
  { id: 'cat-salary', kind: 'income' as const, name: '工资', parentId: null, version: 1 },
]
const ACCOUNTS = [{ id: 'acc-1', name: '招商银行', icon: '🏦', version: 1 }]

function rule(overrides: Partial<ScheduledRuleData> = {}): ScheduledRuleData {
  return {
    id: 'rule-1', ownerId: 'local', type: 'expense', name: '早餐', amountCents: 1500,
    bookId: 'book-1', categoryId: 'cat-food', accountId: 'acc-1', includeInBudget: true,
    note: '', repeat: 'daily', startDate: '2026-09-01', endDate: null, enabled: true,
    lastRunDate: null, runHour: 8, runMinute: 0, createdAt: 1, updatedAt: 1, version: 3,
    ...overrides,
  }
}

const renderLoaded = async (rules: ScheduledRuleData[]) => {
  mockBridge.listScheduledRules.mockResolvedValue(rules)
  render(<ScheduledPage />)
  await waitFor(() => expect(screen.queryByText('加载中…')).toBeNull())
}

describe('ScheduledPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockBridge.listScheduledRules.mockResolvedValue([])
    mockBridge.listBooks.mockResolvedValue(BOOKS)
    mockBridge.listCategories.mockResolvedValue(CATS)
    mockBridge.listAccounts.mockResolvedValue(ACCOUNTS)
    mockBridge.getMeta.mockResolvedValue({})
    mockBridge.insertScheduledRule.mockResolvedValue({ id: 'x' })
    mockBridge.updateScheduledRule.mockResolvedValue({ id: 'x' })
    mockBridge.archiveScheduledRule.mockResolvedValue({ success: true })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  // 打开新建表单并填到可提交（分类选饮食）
  const fillBasicForm = async () => {
    await userEvent.click(screen.getByTestId('scheduled-new-btn'))
    await userEvent.type(screen.getByTestId('scheduled-form-name'), '早餐')
    await userEvent.type(screen.getByTestId('scheduled-form-amount'), '15')
    await userEvent.click(screen.getByTestId('scheduled-form-category-cat-food'))
  }

  it('新建提交：insert payload 全字段 + UUID + 默认值（账本过滤 master / 默认账户 / 无限制 / 计入预算）', async () => {
    await renderLoaded([])
    await fillBasicForm()
    await userEvent.click(screen.getByTestId('scheduled-form-save'))

    await waitFor(() => expect(mockBridge.insertScheduledRule).toHaveBeenCalledTimes(1))
    const input = mockBridge.insertScheduledRule.mock.calls[0][0]
    expect(input.id).toMatch(UUID_RE)
    expect(input).toMatchObject({
      ownerId: 'local',
      type: 'expense',
      name: '早餐',
      amountCents: 1500,
      bookId: 'book-1',          // pickEntryBook 跳过 master
      categoryId: 'cat-food',
      accountId: 'acc-1',        // pickDefaultAccount 第一个账户
      includeInBudget: true,
      note: '',
      repeat: 'daily',
      startDate: todayBizDate(TZ),
      endDate: null,             // 无限制
      enabled: true,
      runHour: 8,               // REQ-43：新建默认执行时刻 8:00
      runMinute: 0,
      version: 1,
    })
    // 无 mutationId：规则仅本地
    expect(input).not.toHaveProperty('mutationId')
  })

  it('结束日期"按日期"：endDate 进 payload（等于开始日期为合法边界）', async () => {
    await renderLoaded([])
    await fillBasicForm()
    await userEvent.click(screen.getByTestId('scheduled-form-end-dated'))
    // DateField 默认值 = 开始日期（今天），先验证相等可通过，再手动改早
    await userEvent.click(screen.getByTestId('scheduled-form-save'))
    await waitFor(() => expect(mockBridge.insertScheduledRule).toHaveBeenCalledTimes(1))
    expect(mockBridge.insertScheduledRule.mock.calls[0][0].endDate).toBe(todayBizDate(TZ))
  })

  it('enabled 快捷开关：update 只传 {id, enabled, version+1}（局部合并）', async () => {
    await renderLoaded([rule({ enabled: true, version: 3 })])
    fireEvent.click(screen.getByTestId('scheduled-toggle-rule-1'))

    await waitFor(() => expect(mockBridge.updateScheduledRule).toHaveBeenCalledTimes(1))
    expect(mockBridge.updateScheduledRule).toHaveBeenCalledWith({
      id: 'rule-1', enabled: false, version: 4,
    })
    // 切换后重拉列表
    await waitFor(() => expect(mockBridge.listScheduledRules).toHaveBeenCalledTimes(2))
  })

  it('账本下拉无 master', async () => {
    await renderLoaded([])
    await userEvent.click(screen.getByTestId('scheduled-new-btn'))
    await userEvent.click(screen.getByTestId('scheduled-form-book-btn'))
    const list = await screen.findByTestId('scheduled-form-book-list')
    expect(within(list).getByTestId('scheduled-form-book-book-1')).toBeInTheDocument()
    expect(within(list).queryByTestId('scheduled-form-book-book-master')).toBeNull()
  })

  it('必填校验：名称/金额未填保存按钮禁用；分类未选给错误且不写入', async () => {
    await renderLoaded([])
    await userEvent.click(screen.getByTestId('scheduled-new-btn'))
    expect(screen.getByTestId('scheduled-form-save')).toBeDisabled()

    await userEvent.type(screen.getByTestId('scheduled-form-name'), '早餐')
    await userEvent.type(screen.getByTestId('scheduled-form-amount'), '15')
    await userEvent.click(screen.getByTestId('scheduled-form-save'))
    await waitFor(() => expect(screen.getByTestId('scheduled-error')).toHaveTextContent('请选择分类'))
    expect(mockBridge.insertScheduledRule).not.toHaveBeenCalled()
  })

  it('列表卡渲染：周期文案 + 账本·分类副行 + 上次/下次执行小字', async () => {
    const today = todayBizDate(TZ)
    await renderLoaded([
      // 2026-09-09 是周三
      rule({ id: 'r-weekly', repeat: 'weekly', startDate: '2026-09-09', lastRunDate: '2026-01-09' }),
      rule({ id: 'r-daily', repeat: 'daily', lastRunDate: today }),
      rule({ id: 'r-monthly', repeat: 'monthly', startDate: '2026-08-15' }),
    ])
    expect(screen.getByTestId('scheduled-repeat-r-weekly')).toHaveTextContent('每周三 08:00')
    expect(screen.getByTestId('scheduled-repeat-r-monthly')).toHaveTextContent('每月 15 日 08:00')
    expect(screen.getByTestId('scheduled-subline-r-weekly')).toHaveTextContent('日常账本')
    expect(screen.getByTestId('scheduled-subline-r-weekly')).toHaveTextContent('饮食')
    // 上次执行日期标签（固定日期，与运行日无关）；今天已跑过的每天规则下次=明天
    expect(screen.getByTestId('scheduled-runtime-r-weekly')).toHaveTextContent('1月9日')
    expect(screen.getByTestId('scheduled-runtime-r-daily')).toHaveTextContent('下次 明天')
    expect(screen.getByTestId('scheduled-runtime-r-monthly')).toHaveTextContent('上次 未执行')
  })

  it('关闭的规则下次执行显示"已暂停"；已到期显示"已结束"', async () => {
    const yesteryear = `${Number(todayBizDate(TZ).slice(0, 4)) - 1}-01-01`
    await renderLoaded([
      rule({ id: 'r-off', enabled: false }),
      rule({ id: 'r-over', repeat: 'yearly', startDate: '2020-03-15', endDate: yesteryear }),
    ])
    expect(screen.getByTestId('scheduled-runtime-r-off')).toHaveTextContent('下次 已暂停')
    expect(screen.getByTestId('scheduled-runtime-r-over')).toHaveTextContent('下次 已结束')
  })

  it('点击卡片进编辑：表单回填，保存走 update（version+1，全字段）', async () => {
    await renderLoaded([rule({ version: 3 })])
    await userEvent.click(screen.getByTestId('scheduled-card-rule-1'))
    expect(screen.getByText('编辑定时任务')).toBeInTheDocument()
    expect(screen.getByTestId('scheduled-form-name')).toHaveValue('早餐')
    expect(screen.getByTestId('scheduled-form-amount')).toHaveValue(15)

    await userEvent.clear(screen.getByTestId('scheduled-form-amount'))
    await userEvent.type(screen.getByTestId('scheduled-form-amount'), '20')
    await userEvent.click(screen.getByTestId('scheduled-form-save'))

    await waitFor(() => expect(mockBridge.updateScheduledRule).toHaveBeenCalledTimes(1))
    const input = mockBridge.updateScheduledRule.mock.calls[0][0]
    expect(input).toMatchObject({
      id: 'rule-1', amountCents: 2000, name: '早餐', repeat: 'daily', version: 4,
    })
    expect(mockBridge.insertScheduledRule).not.toHaveBeenCalled()
  })

  it('删除：确认后走 archive；取消不删', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await renderLoaded([rule()])
    await userEvent.click(screen.getByTestId('scheduled-archive-rule-1'))
    expect(mockBridge.archiveScheduledRule).not.toHaveBeenCalled()

    confirmSpy.mockReturnValue(true)
    await userEvent.click(screen.getByTestId('scheduled-archive-rule-1'))
    await waitFor(() => expect(mockBridge.archiveScheduledRule).toHaveBeenCalledWith('rule-1'))
  })

  it('类型切收入：分类宫格按 kind 过滤', async () => {
    await renderLoaded([])
    await userEvent.click(screen.getByTestId('scheduled-new-btn'))
    expect(screen.getByTestId('scheduled-form-category-cat-food')).toBeInTheDocument()
    await userEvent.click(screen.getByTestId('scheduled-form-type-income'))
    expect(screen.queryByTestId('scheduled-form-category-cat-food')).toBeNull()
    expect(screen.getByTestId('scheduled-form-category-cat-salary')).toBeInTheDocument()
  })

  // ─── T14-5（REQ-43）：执行时刻 ───

  it('编辑回填：规则 09:30 呈现于 TimeField，步进 1 分钟后 update payload 带时刻', async () => {
    await renderLoaded([rule({ version: 3, runHour: 9, runMinute: 30 })])
    await userEvent.click(screen.getByTestId('scheduled-card-rule-1'))
    expect(screen.getByTestId('scheduled-form-run-time-hour')).toHaveTextContent('09')
    expect(screen.getByTestId('scheduled-form-run-time-minute')).toHaveTextContent('30')

    await userEvent.click(screen.getByTestId('scheduled-form-run-time-minute-up'))
    expect(screen.getByTestId('scheduled-form-run-time-minute')).toHaveTextContent('31')
    await userEvent.click(screen.getByTestId('scheduled-form-save'))

    await waitFor(() => expect(mockBridge.updateScheduledRule).toHaveBeenCalledTimes(1))
    expect(mockBridge.updateScheduledRule.mock.calls[0][0]).toMatchObject({
      id: 'rule-1', runHour: 9, runMinute: 31, version: 4,
    })
  })

  it('新建改时刻：时 8→9 进 insert payload', async () => {
    await renderLoaded([])
    await fillBasicForm()
    await userEvent.click(screen.getByTestId('scheduled-form-run-time-hour-up'))
    await userEvent.click(screen.getByTestId('scheduled-form-save'))
    await waitFor(() => expect(mockBridge.insertScheduledRule).toHaveBeenCalledTimes(1))
    expect(mockBridge.insertScheduledRule.mock.calls[0][0]).toMatchObject({
      runHour: 9, runMinute: 0,
    })
  })

  it('列表周期文案：非默认时刻也随 chip 呈现（每周三 21:15）', async () => {
    await renderLoaded([
      rule({ id: 'r-w', repeat: 'weekly', startDate: '2026-09-09', runHour: 21, runMinute: 15 }),
    ])
    expect(screen.getByTestId('scheduled-repeat-r-w')).toHaveTextContent('每周三 21:15')
  })
})
