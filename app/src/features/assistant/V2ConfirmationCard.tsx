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
 */
import type { ActionProposalV2 } from '../../domain/action-proposal'
import type { CardStatus, ExecutionEvidence } from './use-assistant'

interface Props {
  proposal: ActionProposalV2
  status: CardStatus | 'undone'
  /** 流式中 / 执行中：按钮置灰（但**不隐藏**——用户要看得见发生了什么）。 */
  busy: boolean
  /** T18-3（REQ-67②）：这一轮**实际执行**的凭据（空 = 没有执行过的事实）。
   *  来源与形状见 `use-assistant` 的 `ExecutionEvidence`。 */
  executions?: ExecutionEvidence[]
  /** T18-3（REQ-67③）：历史消息的只读卡——无确认/拒绝入口。 */
  readonly?: boolean
  /** 只读态不提供（历史卡不是写入口）。 */
  onConfirm?: () => void
  onReject?: () => void
}

export function V2ConfirmationCard({
  proposal, status, busy, executions, readonly = false, onConfirm, onReject,
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

      {/* payload 键值对照：技术细节，透明优先 */}
      {payloadEntries.length > 0 && (
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