/**
 * 尾卡（R-PROD-31）：**v2 提案的通用确认卡**。
 *
 * 与 `ConfirmationCard` 的分工——那张卡是为 v1 的记账形状手写的（分类 / 金额 /
 * 账户 / 账本四行，外加编辑与账户选择），而 v2 有 **34 个 op、payload 形状各
 * 不相同**：逐 op 写卡等于把 34 种表单在客户端再实现一遍，且每加一个工具就要跟一次。
 * 这张卡因此是**通用**的：
 *
 *   · **`message` 作主行**——服务端每个工具都产出一句人类可读的中文
 *     （「把 2026-09 的整体预算设为 ¥3000」），`tools.py` 里 37 处 `"message"`
 *     就是为这一刻写的，所以通用卡不必按 op 各写一套文案；
 *   · **`warnings` 原样列出**——那是服务端的提醒，卡片不该把它吞掉；
 *   · **`payload` 键值对照**——给想核对的用户看，不美化也不隐藏。键名保持原样
 *     （`categoryId` 之类）：它是内部标识，翻译反而会让人以为那是能改的字段。
 *
 * **刻意不做编辑**：v2 的 payload 形状由工具注册表拥有，客户端无权解释它
 * （`validateProposalV2` 的 docstring：「结构通过不是执行许可」）。要改就拒绝后
 * 重说一句——这也是架构 §7.4 的取向：卡片是安全门与纠偏点，不是第二个表单。
 *
 * T18-3（REQ-67②③）：**只读态 + 回执诚实化**。三件事：
 *
 * 1. **`readonly`**：聊天历史里那张卡要能回看（v2 是默认车道，此前 v2 提案根本不
 *    进历史卡的渲染路径，历史里只剩 `commitV2Proposal` 写的那行文本）。只读态 = 主行
 *    + 提醒 + payload 对照 + 结果行，**没有确认/拒绝入口**——与 v1 的
 *    `ConfirmationCard` readonly 同口径（历史卡不是写入口）。
 * 2. **结果行只认凭据**（`executions`）：卡上说「已执行」的依据是**执行凭据**，
 *    不是 `status`。`status === 'committed'` 的含义是「不在途」，纯文本消息也带它，
 *    它当不了回执；而照 `status` 说话正是 REQ-67② 记的那个形态——用户看到「改好了」
 *    却说不清到底改没改。**没有凭据就不说「已完成」**：一律保守报「未执行 · 待确认」
 *    （与架构 §7.4「不确定时倾向拒绝」同一取向）。旧版本落盘的 v2 消息没有这个字段，
 *    在历史里因此显示为「未执行」——**这是如实**，不是为了兼容而假装成功。
 * 3. **`undone` / `error` 的文案**与 v1 历史卡对齐（已撤销灰化 / 执行失败红字）。
 *    注意 v2 的 `undone` **当前没有生产者**（撤销入口仍只有 v1 卡的内存态通道
 *    `activeTxIdsRef`，见 `undoExecution`）——这里先把渲染就位，等撤销链路覆盖 v2
 *    时不必再改这张卡。
 *
 * T20-2（REQ-77 上层）：**记账三 op 享业务卡**。用户要的「历史里出现过的那种记账卡」
 * 就是 v1 `ConfirmationCard` 的四行口径（分类 / 收支 / 金额 / 日期 / 备注 / 账本），
 * 这里直接复用它的 `SnapshotCard`（T16-3 写的那张只读快照卡）按 op 出三种形态：
 *
 *   · `transaction_add` → **记账卡**：payload 里那几笔的只读快照；
 *   · `transaction_update` → **修改卡**：旧 = 回执 `before`，新 = `before` 叠上
 *     payload 的**增量**，逐字段差异高亮（与 v1 对照区同一份 `diffMask` 口径）；
 *   · `transaction_delete` → **删除卡**：回执 `before` = 将被删的那笔。
 *
 * **其余 op 一律回落上面的通用卡**（决策 #3），通用卡那段一字未改。三条边界值得
 * 写下来，它们都是「卡上不许出现编造的信息」这条老规矩的实例：
 *
 * 1. **数据不全就不画业务卡**——`before` 只在**执行回执**里（T20-1 决策 #4），所以
 *    挂起态（用户还没点确认）的改 / 删卡拿不到「旧值」。那时手里只有一条**增量**
 *    payload，拿它当快照渲染会把「没提到的字段」显示成「未分类 / 不使用账户」。
 *    宁可不画：回落通用卡，与改前的行为一字不差。
 * 2. **只读态（历史卡）同样出业务卡**，依旧没有确认 / 拒绝入口——历史卡是留档不是
 *    安全门（T18-3 口径未变）。
 * 3. **预算位不渲染**：`before` / `payload` 都不带这一位，照默认值画等于编造
 *    （见 `SnapshotCard` 的 `hideBudget`）。
 */
import type { ActionProposalV2 } from '../../domain/action-proposal'
import type { AccountData, BookData, CategoryData } from '../../platform/bridge/bridge-types'
import { SnapshotCard, diffMask } from './ConfirmationCard'
import type { CommittedSnapshot, DiffMask } from './ConfirmationCard'
import type { CardStatus, ExecutionEvidence } from './use-assistant'

interface Props {
  proposal: ActionProposalV2
  status: CardStatus | 'undone'
  /** 流式中 / 执行中：按钮置灰（但**不隐藏**——用户要看得见发生了什么）。 */
  busy: boolean
  /** T18-3（REQ-67②）：这一轮**实际执行**的凭据（空 = 没有执行过的事实）。
   *  来源与形状见 `use-assistant` 的 `ExecutionEvidence`。T20-2：改 / 删业务卡的
   *  「旧值」也从这里取（`before`）。 */
  executions?: ExecutionEvidence[]
  /** T18-3（REQ-67③）：历史消息的只读卡——无确认/拒绝入口。 */
  readonly?: boolean
  /** 只读态不提供（历史卡不是写入口）。 */
  onConfirm?: () => void
  onReject?: () => void
  /** T20-2（REQ-77）：业务卡的四行口径要的分类 / 账户 / 账本名单（名 → 显示用）。
   *  通用卡不需要它们，所以缺省为空——那时业务卡显示「未分类」而不是崩掉。 */
  categories?: CategoryData[]
  accounts?: AccountData[]
  books?: BookData[]
  /** 业务卡上那笔所记的账本（v2 的执行落在 hook 层这个账本上，见 `commitV2Proposal`）。 */
  bookId?: string | null
  bookName?: string
}

/**
 * T20-2（REQ-77 决策 #3）：**享业务卡的记账三 op**——与 `use-assistant` 的
 * `LEDGER_OPS` 是同一条边界（那边判的是回执文案与撤销窗，这边判的是卡片形态）。
 * 其余 op 一律回落通用卡，**不得**借这一轮把通用卡改成逐 op 卡。
 */
const LEDGER_OPS: ReadonlySet<string> = new Set([
  'transaction_add', 'transaction_update', 'transaction_delete',
])

/** 卡片快照里的七个字段（决策 #4 的六项 + T20-1 补的 `accountId`）——键名与
 *  服务端 payload / `before` 逐字对齐，卡上的字段比对才成立。 */
const SNAPSHOT_KEYS = ['type', 'amountCents', 'categoryId', 'accountId', 'bizDate', 'note'] as const

/** 业务卡：三种形态各带自己那一份快照。 */
type LedgerCard =
  | { kind: 'add'; items: CommittedSnapshot[] }
  | { kind: 'update'; before: CommittedSnapshot; after: CommittedSnapshot; diff: DiffMask }
  | { kind: 'delete'; item: CommittedSnapshot }

/** 没有可比的两侧时用的空掩码（记账卡 / 删除卡：整卡都不高亮，字段照常显示）。 */
const NO_DIFF: DiffMask = {
  cat: false, type: false, amount: false, date: false, note: false,
  account: false, book: false, budget: false,
}

/**
 * 服务端下发的 payload / `before` → 卡片快照（`CommittedSnapshot`）。
 *
 * 缺字段一律退回**中性值**而不是猜：payload 是**增量**（只带模型改的那几项），
 * 想拿它直接当快照用就会把「没提到的字段」显示成「未分类 / 不使用账户」。
 * 调用方负责保证只在字段齐备时才走这条路（见 `ledgerCard`）。
 *
 * `bookId` 优先取快照自带的那一个（`before` 有），否则用 hook 层当前记账账本
 * （`transaction_add` 的 payload 不带账本——它记的就是请求的那个账本）。
 */
function snapshotOf(src: unknown, bookId: string): CommittedSnapshot {
  const o = (src && typeof src === 'object' ? src : {}) as Record<string, unknown>
  return {
    type: o.type === 'income' ? 'income' : 'expense',
    amountCents: typeof o.amountCents === 'number' ? o.amountCents : 0,
    categoryId: typeof o.categoryId === 'string' ? o.categoryId : '',
    accountId: typeof o.accountId === 'string' ? o.accountId : null,
    bizDate: typeof o.bizDate === 'string' ? o.bizDate : '',
    note: typeof o.note === 'string' ? o.note : '',
    bookId: typeof o.bookId === 'string' ? o.bookId : bookId,
    // 预算位两份数据里都没有——卡上按 `hideBudget` 隐藏，这里的值只是类型占位。
    includeInBudget: true,
  }
}

/** payload 里的改动 → 「新」快照的那几项覆盖。没给的字段表示**不变**（增量的语义）。 */
function changedFields(payload: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const k of SNAPSHOT_KEYS) if (k in payload) out[k] = payload[k]
  return out
}

/**
 * 记账三 op → 业务卡数据；**画不出来时返回 `null`**（调用方回落通用卡）。
 *
 * `transaction_update` / `transaction_delete` 的「旧值」只有一个来源：执行回执的
 * `before`（服务端在执行前抓，T20-1 决策 #4）。没有它就画不出新旧对照——挂起态
 * 正是这种情形，此时回落通用卡而不是编一张旧卡。
 */
function ledgerCard(
  action: string,
  payload: Record<string, unknown>,
  before: Record<string, unknown> | null,
  bookId: string,
): LedgerCard | null {
  if (!LEDGER_OPS.has(action)) return null

  if (action === 'transaction_add') {
    const items = payload.transactions
    if (!Array.isArray(items) || items.length === 0) return null
    return { kind: 'add', items: items.map(it => snapshotOf(it, bookId)) }
  }

  if (!before) return null
  const old = snapshotOf(before, bookId)
  if (action === 'transaction_delete') return { kind: 'delete', item: old }

  // 「新」= 旧值为底 + payload 里显式给出的那几项：`_build_tx_changes` 产出的就是
  // 一条增量（没提到的字段保持不变），直接拿 payload 当快照会缺一半字段。
  const after = snapshotOf({ ...before, ...changedFields(payload) }, bookId)
  return { kind: 'update', before: old, after, diff: diffMask(after, old) }
}

export function V2ConfirmationCard({
  proposal, status, busy, executions, readonly = false, onConfirm, onReject,
  categories = [], accounts = [], books = [], bookId = null, bookName = '',
}: Props) {
  const undone = status === 'undone'
  const failed = status === 'error'
  // 确认入口的两个条件：**待确认**态（服务端挂起、`done.proposals` 已核对）且非只读。
  const canDecide = !readonly && status === 'pending-confirm'
  const receipts = executions ?? []
  const affected = receipts.reduce((n, r) => n + r.entityIds.length, 0)
  // 设备执行的凭据没有服务端实体 id——单独一句话，不说「影响 0 项」（那读起来像什么都没发生）
  const onDevice = receipts.some(r => r.lane === 'device')

  const shell = 'rounded-xl p-3 mx-4 my-2'
  const cardStyle = {
    background: 'var(--glass)',
    border: '1px solid var(--glass-border)',
    // 与 v1 历史卡同口径：撤销过的卡整卡灰化（终态一眼可辨）
    opacity: undone ? 0.6 : 1,
  } as const
  const smallBtnStyle = {
    background: 'var(--glass)',
    color: 'var(--ink2)',
    border: '1px solid var(--glass-border)',
  } as const

  const payloadEntries = Object.entries(proposal.payload ?? {})
  // T20-2（REQ-77 上层）：记账三 op 走业务卡。凭据里的 `before` 是唯一的「旧值」
  // 来源（服务端执行前抓）；它缺席时 `ledgerCard` 返回 null，卡片回落通用卡。
  const before = receipts.find(r => r.before)?.before ?? null
  const ledger = ledgerCard(proposal.action, proposal.payload ?? {}, before, bookId ?? '')
  // 业务卡上的快照共用这几个下传项：名单（名→显示）+ 隐藏预算位（数据里没有它）。
  const snapProps = { categories, accounts, books, bookName, hideBudget: true } as const

  return (
    <div className={shell} style={cardStyle} data-testid="v2-confirmation-card">
      {/* 主行：服务端工具产出的人类可读文案 */}
      <div className="text-sm" style={{ color: 'var(--ink1)' }} data-testid="v2-card-message">
        {proposal.message}
      </div>

      {/* 服务端的提醒原样透出 */}
      {proposal.warnings.length > 0 && (
        <div className="mt-1.5 text-xs" style={{ color: 'var(--warn, #b45309)' }}
             data-testid="v2-card-warnings">
          ⚠️ {proposal.warnings.join('；')}
        </div>
      )}

      {/* T20-2（REQ-77）：记账三 op 的**业务卡**——v1 四行口径（共用 `SnapshotCard`），
          改 / 删另出新旧对照。其余 op 走下面的通用卡：payload 键值对照，技术细节，
          透明优先。两者**互斥**——业务卡自带四行快照，再列一遍 payload 是同一件事
          说两遍（T20-1 的交接项）。 */}
      {ledger ? (
        <div className="mt-2 flex flex-col gap-2" data-testid="v2-card-ledger">
          {ledger.kind === 'update' ? (
            <>
              <div className="text-[10px]" style={{ color: 'var(--ink3)' }}
                   data-testid="v2-card-compare-label">
                新旧对照
              </div>
              <SnapshotCard testId="v2-card-before" snapshot={ledger.before}
                            diff={ledger.diff} {...snapProps} />
              <div className="text-center text-[10px] -my-1" style={{ color: 'var(--ink3)' }}>↓</div>
              <SnapshotCard testId="v2-card-after" snapshot={ledger.after}
                            diff={ledger.diff} {...snapProps} />
            </>
          ) : ledger.kind === 'add' ? (
            // 一笔一次卡（`transaction_add_batch` 的 payload 是多笔，逐笔各出一张）
            ledger.items.map((item, i) => (
              <SnapshotCard key={i} testId={`v2-card-tx-${i}`} snapshot={item}
                            diff={NO_DIFF} {...snapProps} />
            ))
          ) : (
            <SnapshotCard testId="v2-card-delete" snapshot={ledger.item}
                          diff={NO_DIFF} {...snapProps} />
          )}
        </div>
      ) : payloadEntries.length > 0 && (
        <div className="mt-2 pt-2 flex flex-col gap-0.5"
             style={{ borderTop: '1px solid var(--glass-border)' }}
             data-testid="v2-card-payload">
          {payloadEntries.map(([k, v]) => (
            <div key={k} className="flex gap-2 text-xs" style={{ color: 'var(--ink3)' }}>
              <span className="shrink-0">{k}</span>
              <span className="truncate" style={{ color: 'var(--ink2)' }}>
                {typeof v === 'object' ? JSON.stringify(v) : String(v)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* T18-3（REQ-67②）：结果行。三个终态优先（撤销 / 失败），其次是「有凭据」，
          最后才是「没有凭据」——**唯一不出现的分支是「没凭据却说已完成」**。
          失败那句只说「重说一次即可重试」：原话尾部的「可以拒绝这张卡后重说一次」
          指向的拒绝入口**只在待确认态存在**（失败态根本没有按钮，那句话在指一个不存在
          的东西）；而改成「本轮没有写入」同样不行——桥调用失败可能是**超时**，服务端
          那时可能已经写了。所以这一行只陈述能确证的事实：这次调用没成功。 */}
      {undone ? (
        <div className="mt-2 text-xs" style={{ color: 'var(--ink3)' }} data-testid="v2-card-undone">
          已撤销
        </div>
      ) : failed ? (
        <div className="mt-2 text-xs" style={{ color: 'var(--danger, #b91c1c)' }}
             data-testid="v2-card-error">
          ❌ 执行失败，请重说一次
        </div>
      ) : receipts.length > 0 ? (
        <div className="mt-2 text-xs" style={{ color: 'var(--ink2)' }} data-testid="v2-card-done">
          {onDevice ? '✓ 已在本机执行' : `✓ 已执行${affected > 0 ? ` · 影响 ${affected} 项` : ''}`}
        </div>
      ) : (
        <div className="mt-2 text-xs" style={{ color: 'var(--ink3)' }}
             data-testid="v2-card-not-executed">
          未执行 · 待确认
        </div>
      )}

      {canDecide && (
        <div className="flex gap-2 mt-2">
          <button
            type="button"
            onClick={onReject}
            className="flex-1 py-1.5 rounded-lg text-xs font-medium"
            style={smallBtnStyle}
            data-testid="v2-card-reject"
          >
            拒绝
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className="flex-1 py-1.5 rounded-lg text-xs font-medium text-white"
            style={{ background: 'var(--accent)', opacity: busy ? 0.5 : 1 }}
            data-testid="v2-card-confirm"
          >
            确认
          </button>
        </div>
      )}
    </div>
  )
}