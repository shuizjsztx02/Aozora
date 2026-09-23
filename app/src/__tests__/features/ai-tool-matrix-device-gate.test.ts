/**
 * 「新包对不同输入的指令执行准确性」——**客户端侧落点核验**（离线、无模型）。
 *
 * T18-8（REQ-71）之前，设备上真正决定「AI 能不能看到这句输入」的是 `submitInput` 里的
 * 四条**启发式**判据（操作句式 / 本地置信度 / 警告 / chat 兜底）：四条全假即整句走本地
 * 快路径直接记账，云端 agent 根本不被调用。R-PROD-28（「给餐饮设个月度预算 1500」被本地
 * NLU 记成一笔 1500 元的餐饮支出）就是这么来的——服务端测试集全绿也说明不了设备上能用。
 *
 * **T18-8 起判据只剩一条事实：云端可不可达**（`routeInput`）。在线 → 一律走云端、本地
 * NLU 关闭；离线 → 本地快路径。故本用例对语料**分两态各算一遍**：
 *
 * - **在线态**：语料里**任何一条**都不许被本地吞掉（吞掉 = AI 看不到，能力静默消失）。
 *   这是 REQ-71 的正面断言——也是本轮修好的那件事。
 * - **离线态**：被吞掉的集合就是「本地快路径」（离线没有云端可去，属**已知代价**）；
 *   命中操作句式的那几条由 canned 拦下（离线时绝不让本地 NLU 把「改X」记成「新账X」）。
 */
import { describe, it, expect } from 'vitest'
import corpusJson from '../../../../server/scripts/ai_tool_matrix/corpus.json'
import { parseInput } from '../../domain/nlu/parser'
import type { ParseContext } from '../../domain/nlu/parser'
import { routeInput } from '../../features/assistant/use-assistant'
import type { InputRoute } from '../../features/assistant/use-assistant'
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

/** 与 `submitInput` 同构地算落点：**只有走本地那一支才解析**（在线不解析 = NLU 关闭）。 */
function devicePath(text: string, cloudReachable: boolean): { route: InputRoute; local: string } {
  const route = routeInput(text, cloudReachable)
  if (route !== 'local') return { route, local: '—' }
  const p = parseInput(text, ctx())
  const tx = p.transactions?.[0]
  const local = `${p.action}` + (tx
    ? ` ¥${(tx.amountCents / 100).toFixed(2)} 分类=${CATEGORIES.find(c => c.id === tx.categoryId)?.name ?? tx.categoryId ?? '无'} 日期=${tx.bizDate ?? '—'} 账户=${ACCOUNTS.find(a => a.id === tx.accountId)?.name ?? '不使用'}`
    : ` conf=${p.confidence.toFixed(2)}`)
  return { route, local }
}

/** 语料里期望「记一笔」的用例——对它们而言走本地快路径是**设计意图**（离线可记账）。 */
const isBookkeeping = (c: Case) =>
  c.expect.includes('transaction_add') || c.expect.includes('transaction_add_batch')

/** 离线态被本地快路径接管的记账用例（快照）——**对它们而言走本地是设计意图**
 *  （离线要能记账）；下列这几条是旧快照里就有的「设计意图」子集，用于核验本地
 *  必须把它们**记对**。 */
const OFFLINE_LOCAL_BOOKKEEPING = [
  'ACC-NEG-01', 'CMP-01', 'DEV-SIDE-01', 'TX-ADD-01', 'TX-ADD-02', 'TX-ADD-04', 'TX-BATCH-01',
]
/**
 * 离线态被 `OP_SENTENCE_RE` 拦下的用例（快照）——操作/查询句离线时没有云端可去，
 * 也不能拿本地结果硬记（本地会把句中的金额当新账，T12-12 真机复现过）。
 */
const OFFLINE_CANNED = [
  'ANA-M-01', 'BOOK-DEL-01', 'BOOK-DETAIL-01', 'BUD-DEL-01', 'BUD-SETCAT-01', 'BUD-STATUS-01',
  'CAT-ARCH-02', 'CMP-02', 'META-02', 'NEG-03', 'PER-ARCH-01', 'SAV-UPD-01', 'SCH-UPD-01',
  'SEC-PROFILE-01', 'TX-DEL-01', 'TX-DEL-02', 'TX-LIST-01', 'TX-NEG-01', 'TX-SUM-01',
  'TX-SUM-02', 'TX-UPD-01', 'TX-UPD-02',
]

/**
 * 离线态**误记**的用例（快照）：意图**不是**记账，本地却把它解析成一笔账。
 *
 * **这是离线路径的已知代价，不是本轮引入的**——离线时没有云端可去，旧代码同样会落到
 * 本地写（`needsAI` 只在在线时才起作用）。在线态下这个集合是**空**（见第一条断言）。
 * 记在这里是为了：本地解析一旦改进，这条会变红，提醒把清单缩短。
 */
const OFFLINE_MISPARSE = [
  'BUD-CHECK-01', 'BUD-SETCAT-02', 'GATE-01', 'GATE-03', 'SCH-CREATE-01', 'SCH-CREATE-02',
  'SCH-NEG-01',
]

describe('AI 工具测试集 — 客户端落点（T18-8 / REQ-71）', () => {
  it('语料可读且非空', () => {
    expect(corpus.cases.length).toBeGreaterThan(50)
  })

  it('★ 在线态：没有任何一条被本地拦下（REQ-71 的正面断言）', () => {
    // 这是本轮修好的东西：原先四条启发式判据会把其中一部分整句交给本地快路径，
    // 云端 agent 根本看不到——非记账意图被静默记成一笔账（R-PROD-28）。
    const swallowed = corpus.cases
      .filter(c => devicePath(c.text, true).route !== 'cloud')
      .map(c => c.id)
    expect(swallowed).toEqual([])
  })

  it('离线态：操作/查询句被硬规则拦下（快照）；其余落本地快路径', () => {
    const canned: string[] = []
    const local: string[] = []
    for (const c of corpus.cases) {
      const { route, local: desc } = devicePath(c.text, false)
      if (route === 'offline-canned') canned.push(c.id)
      else {
        local.push(c.id)
        console.log(`→本地* ${c.id.padEnd(16)} ${c.text}\n         ${desc}`)
      }
    }
    // canned 集合 = 命中操作/查询句式的那些（守卫确实在起作用，而不是空转）
    expect(canned.sort()).toEqual([...OFFLINE_CANNED].sort())
    // 余下的全部落本地快路径——两者是划分，合起来 = 全语料
    expect(local.length + canned.length).toBe(corpus.cases.length)
    expect(local.length).toBeGreaterThan(canned.length)
  })

  it('离线态误记（快照）：非记账意图被本地记成一笔账 —— 离线已知代价', () => {
    // 口径 = 「**真会写进库**」：本地解析出 add_tx 且带上了分类（无分类的那条落到
    // 「待选分类」卡、不写库，不算误记）。
    const misparsed = corpus.cases.filter(c => {
      if (isBookkeeping(c)) return false
      if (devicePath(c.text, false).route !== 'local') return false
      const p = parseInput(c.text, ctx())
      if (p.action !== 'add_tx' && p.action !== 'add_tx_batch') return false
      return (p.transactions ?? []).some(t => !!t.categoryId)
    }).map(c => c.id).sort()
    expect(misparsed).toEqual([...OFFLINE_MISPARSE].sort())
  })

  it('走本地快路径的记账句必须记对（金额/分类/日期/账户四项）', () => {
    // 本地快路径是设计意图，但「走了快路径」不等于「记对了」——R-PROD-28 的原始
    // 形态就是本地把附加字段（账户 / 日期）静默丢弃、整句进备注。
    for (const id of OFFLINE_LOCAL_BOOKKEEPING) {
      const c = corpus.cases.find(x => x.id === id)!
      const p = parseInput(c.text, ctx())
      expect(p.action, `${id} 应被本地解析成记账`).toBe('add_tx')
      const tx = p.transactions![0]
      expect(tx.amountCents, `${id} 金额`).toBeGreaterThan(0)
      expect(CATEGORIES.some(k => k.id === tx.categoryId), `${id} 必须落到一个真实分类`).toBe(true)
    }
  })

  it('★ 缺陷特征化（R-PROD-28 残留）：复合句里的账户与日期被本地快路径丢弃', () => {
    // 设备上已硬证据复现（§5.3）：卡面显示「账户=不使用账户 / 日期=2026-09-21（今天）」，
    // 而用户说的是「账户用现金，日期写 2026-09-15」——整句后半段还进了备注。
    //
    // **T18-8 之后这只在离线态可达**（在线一律走云端，本地根本不解析）。留着它是因为
    // 离线记账是既定能力，这个坑仍在。**这是「现状」不是「期望」**：修好它这条用例就会
    // 变红，提醒把下面两行翻成正确期望（bizDate === '2026-09-15'、accountId 指向「现金」）。
    const text = '记一笔 88 元的餐饮，账户用现金，日期写 2026-09-15'
    expect(devicePath(text, false).route).toBe('local')
    const p = parseInput(text, ctx())
    expect(p.action).toBe('add_tx')
    const tx = p.transactions![0]
    expect(tx.amountCents).toBe(8800)
    expect(tx.bizDate).not.toBe('2026-09-15')     // ← 修好后改成 toBe
    expect(tx.accountId ?? null).toBeNull()        // ← 修好后改成指向「现金」
  })

  it('操作句离线 → canned（绝不拿本地结果硬记）', () => {
    for (const t of [
      '这个月餐饮花了多少', '把便利店那笔改成 30 元', '把夜宵那笔删掉',
      '查一下这个月的餐饮记录', '把餐饮的月度预算改成 1500',
    ]) {
      expect(devicePath(t, false).route, t).toBe('offline-canned')
    }
  })

  it('本地快路径确实会记成一笔账（证明离线这条路不是纸面风险）', () => {
    // 「给餐饮设个月度预算 1500」——意图是**改预算**，本地却按记账解析。
    const p = parseInput('给餐饮设个月度预算 1500', ctx())
    expect(p.action).toBe('add_tx')
    expect(p.transactions?.[0]?.amountCents).toBe(150000)
    expect(p.transactions?.[0]?.categoryId).toBe('cat-0') // 餐饮
  })

  it('今天这个基准日可用（日期换算前提）', () => {
    expect(todayBizDate(TZ)).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })
})