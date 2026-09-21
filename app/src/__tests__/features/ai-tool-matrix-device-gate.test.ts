/**
 * 「新包对不同输入的指令执行准确性」——**客户端侧前置闸门**核验（离线、无模型）。
 *
 * 为什么需要这一层：设备上真正决定「AI 能不能看到这句输入」的不是服务端工具注册表，
 * 而是 ``use-assistant.submitInput`` 里的四判据（操作句式 / 本地置信度 / 警告 / chat
 * 兜底）。判据为假时整句走**本地快路径**直接记账，云端 agent 根本不被调用——服务端
 * 测试集全绿也说明不了设备上能用（R-PROD-28 就是这么来的：「给餐饮设个月度预算 1500」
 * 被本地 NLU 记成了一笔 1500 元的餐饮支出）。
 *
 * 本用例对 ``server/scripts/ai_tool_matrix/corpus.json`` 的**每一条**用例算出它走哪条
 * 路，并把「被本地快路径吞掉」的集合钉成快照。这个集合一旦变化（变大＝AI 覆盖退化，
 * 变小＝修复），用例变红，必须有人来看一眼。
 */
import { describe, it, expect } from 'vitest'
import corpusJson from '../../../../server/scripts/ai_tool_matrix/corpus.json'
import { parseInput } from '../../domain/nlu/parser'
import type { ParseContext } from '../../domain/nlu/parser'
import { needsAiRouting } from '../../features/assistant/use-assistant'
import type { Category, Account } from '../../domain/types'
import { todayBizDate } from '../../domain/date'

interface Case {
  id: string
  domain: string
  layer: string
  text: string
  expect: string[]
  kind: string
}

interface Corpus {
  seed: {
    categories: Array<{ name: string; kind: string }>
    accounts: Array<{ name: string }>
  }
  cases: Case[]
}

/** **直接 import 语料**（`resolveJsonModule` 已开），不手工维护一份镜像：
 *  本地 NLU 的解析结果依赖「用户有哪些分类 / 账户」，镜像漂了会让本该走快路径的
 *  句子被误判成要走 agent（本轮实测踩过一次：语料加了「保险费」分类后，
 *  `SCH-CREATE-01` 的判定就变了）。 */
const corpus = corpusJson as Corpus
const TZ = 'Asia/Shanghai'
const CATEGORIES: Category[] = corpus.seed.categories.map((c, i) => ({
  id: `cat-${i}`, ownerId: 'u-1', bookId: 'b-1', parentId: null,
  kind: c.kind as Category['kind'], name: c.name, archivedAt: null, version: 1,
}))

const ACCOUNTS: Account[] = corpus.seed.accounts.map((a, i) => ({
  id: `acc-${i}`, ownerId: 'u-1', name: a.name, icon: 'wallet', archivedAt: null, version: 1,
}))

function ctx(): ParseContext {
  return {
    now: Date.UTC(2026, 8, 21, 4, 0, 0), // 2026-09-21 12:00 +08
    timezone: TZ,
    categories: CATEGORIES,
    accounts: ACCOUNTS,
  }
}

function devicePath(text: string): { route: 'agent' | 'local'; why: string; local: string } {
  const p = parseInput(text, ctx())
  const toAgent = needsAiRouting(text, p)
  const tx = p.transactions?.[0]
  const local = toAgent
    ? '—'
    : `${p.action}` + (tx
      ? ` ¥${(tx.amountCents / 100).toFixed(2)} 分类=${CATEGORIES.find(c => c.id === tx.categoryId)?.name ?? tx.categoryId ?? '无'} 日期=${tx.bizDate ?? '—'} 账户=${ACCOUNTS.find(a => a.id === tx.accountId)?.name ?? '不使用'}`
      : ` conf=${p.confidence.toFixed(2)}`)
  return { route: toAgent ? 'agent' : 'local', why: '', local }
}

/** 语料里期望「记一笔」的用例——对它们而言走本地快路径是**设计意图**（离线可记账）。 */
const isBookkeeping = (c: Case) =>
  c.expect.includes('transaction_add') || c.expect.includes('transaction_add_batch')

/**
 * 已知会被本地快路径吞掉的用例（快照）。**这不是「预期如此」，是「现状如此」**。
 * 分成两类，处置方式完全不同：
 *
 * - ``快路径``：意图就是记一笔 → 走本地是设计意图（快、离线可用）。
 * - ``缺口``：意图**不是**记账，本地却把它当记账解析 → AI 根本看不到这句输入。
 */
const SWALLOWED_BOOKKEEPING = ['ACC-NEG-01', 'CMP-01', 'DEV-SIDE-01', 'TX-ADD-01', 'TX-ADD-02', 'TX-ADD-04', 'TX-BATCH-01']
const SWALLOWED_GAP = ['BUD-CHECK-01', 'BUD-SETCAT-02', 'GATE-01', 'GATE-03', 'SCH-CREATE-01', 'SCH-CREATE-02']

describe('AI 工具测试集 — 客户端快路径闸门', () => {
  it('语料可读且非空', () => {
    expect(corpus.cases.length).toBeGreaterThan(50)
  })

  it('「被本地快路径吞掉」的用例集合与快照一致', () => {
    const swallowed = corpus.cases
      .filter(c => devicePath(c.text).route === 'local')
      .map(c => c.id)
      .sort()

    // 打印全表，便于人工比对（vitest 通过时默认不显示，加 -v 可见）
    const lines = corpus.cases.map(c => {
      const d = devicePath(c.text)
      const book = isBookkeeping(c)
      return `${d.route === 'agent' ? '→AI  ' : (book ? '→本地*' : '→本地!')} ${c.id.padEnd(16)} ${c.text}\n         ${d.local}`
    })
    console.log('\n' + lines.join('\n'))

    expect(swallowed).toEqual(
      [...SWALLOWED_BOOKKEEPING, ...SWALLOWED_GAP].sort(),
    )
  })

  it('非记账意图的句子不得被本地快路径吞掉（吞掉 = AI 看不到，能力静默消失）', () => {
    const gaps = corpus.cases
      .filter(c => !isBookkeeping(c))
      .filter(c => devicePath(c.text).route === 'local')
      .map(c => c.id)
      .sort()
    expect(gaps).toEqual([...SWALLOWED_GAP].sort())
  })

  it('走本地快路径的记账句必须记对（金额/分类/日期/账户四项）', () => {
    // 本地快路径是设计意图，但「走了快路径」不等于「记对了」——R-PROD-28 的原始
    // 形态就是本地把附加字段（账户 / 日期）静默丢弃、整句进备注。
    for (const id of SWALLOWED_BOOKKEEPING) {
      const c = corpus.cases.find(x => x.id === id)!
      const p = parseInput(c.text, ctx())
      expect(p.action, `${id} 应被本地解析成记账`).toBe('add_tx')
      const tx = p.transactions![0]
      expect(tx.amountCents, `${id} 金额`).toBeGreaterThan(0)
      expect(CATEGORIES.some(k => k.id === tx.categoryId), `${id} 必须落到一个真实分类`).toBe(true)
    }
  })

  it('★ 缺陷特征化（R-PROD-28）：复合句里的账户与日期被本地快路径丢弃', () => {
    // 设备上已硬证据复现（§5.3）：卡面显示「账户=不使用账户 / 日期=2026-09-21（今天）」，
    // 而用户说的是「账户用现金，日期写 2026-09-15」——整句后半段还进了备注。
    //
    // **这是「现状」不是「期望」**：把缺陷钉成断言，修好它这条用例就会变红，
    // 提醒把下面两行翻成正确期望（bizDate === '2026-09-15'、accountId 指向「现金」）。
    const p = parseInput('记一笔 88 元的餐饮，账户用现金，日期写 2026-09-15', ctx())
    expect(p.action).toBe('add_tx')
    const tx = p.transactions![0]
    expect(tx.amountCents).toBe(8800)
    expect(tx.bizDate).not.toBe('2026-09-15')     // ← 修好后改成 toBe
    expect(tx.accountId ?? null).toBeNull()        // ← 修好后改成指向「现金」
  })

  it('「多少 / 改成 / 删掉 / 查询」类说法稳定进 agent（回归锚点）', () => {
    for (const t of [
      '这个月餐饮花了多少', '把便利店那笔改成 30 元', '把夜宵那笔删掉',
      '查一下这个月的餐饮记录', '把餐饮的月度预算改成 1500', '我最近记了几笔',
    ]) {
      expect(devicePath(t).route, t).toBe('agent')
    }
  })

  it('本地快路径确实会记成一笔账（证明这道闸门不是纸面风险）', () => {
    // 「给餐饮设个月度预算 1500」——意图是**改预算**，本地却按记账解析。
    const p = parseInput('给餐饮设个月度预算 1500', ctx())
    expect(needsAiRouting('给餐饮设个月度预算 1500', p)).toBe(false)
    expect(p.action).toBe('add_tx')
    expect(p.transactions?.[0]?.amountCents).toBe(150000)
    expect(p.transactions?.[0]?.categoryId).toBe('cat-0') // 餐饮
  })

  it('今天这个基准日可用（日期换算前提）', () => {
    expect(todayBizDate(TZ)).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })
})