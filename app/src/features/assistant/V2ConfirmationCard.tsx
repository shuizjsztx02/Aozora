/**
 * 尾卡（R-PROD-31）：**v2 提案的通用确认卡**。
 *
 * 与 `ConfirmationCard` 的分工——那张卡是为 v1 的记账形状手写的（分类 / 金额 /
 * 账户 / 账本四行，外加编辑与账户选择），而 v2 有 **34 个 op、payload 形状各不
 * 相同**：逐 op 写卡等于把 34 种表单在客户端再实现一遍，且每加一个工具就要跟一次。
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
 */
import type { ActionProposalV2 } from '../../domain/action-proposal'
import type { CardStatus } from './use-assistant'

interface Props {
  proposal: ActionProposalV2
  status: CardStatus
  /** 流式中 / 执行中：按钮置灰（但**不隐藏**——用户要看得见发生了什么）。 */
  busy: boolean
  onConfirm: () => void
  onReject: () => void
}

export function V2ConfirmationCard({ proposal, status, busy, onConfirm, onReject }: Props) {
  const done = status === 'committed'
  const failed = status === 'error'
  const pending = status === 'pending-confirm'

  const shell = 'rounded-xl p-3 mx-4 my-2'
  const cardStyle = {
    background: 'var(--glass)',
    border: '1px solid var(--glass-border)',
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

      {done && (
        <div className="mt-2 text-xs" style={{ color: 'var(--ink2)' }} data-testid="v2-card-done">
          ✓ 已完成
        </div>
      )}
      {failed && (
        <div className="mt-2 text-xs" style={{ color: 'var(--danger, #b91c1c)' }}
             data-testid="v2-card-error">
          ❌ 执行失败，可以拒绝这张卡后重说一次
        </div>
      )}

      {pending && (
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