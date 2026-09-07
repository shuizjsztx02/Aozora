/**
 * AssistantMessage — T-M4-02c 助手消息气泡。
 *
 * 区分用户消息（右侧）和助手回复（左侧）。
 * 助手消息可附带确认卡或状态指示。
 */

import type { AssistantMessage as MessageType } from './use-assistant'
import type { CategoryData } from '../../platform/bridge/bridge-types'
import { ConfirmationCard } from './ConfirmationCard'
import type { ActionProposal } from '../../domain/action-proposal'

interface AssistantMessageProps {
  message: MessageType
  categories: CategoryData[]
  isConfirming: boolean
  onConfirm: (edited: ActionProposal) => void
  onCancel: () => void
}

export function AssistantMessage({
  message,
  categories,
  isConfirming,
  onConfirm,
  onCancel,
}: AssistantMessageProps) {
  const isUser = message.role === 'user'

  return (
    <div
      className={'flex flex-col ' + (isUser ? 'items-end' : 'items-start') + ' mb-3'}
      data-testid="assistant-message"
    >
      <div
        className="max-w-[85%]"
        style={{
          background: isUser ? 'var(--accent)' : 'var(--glass)',
          color: isUser ? 'white' : 'var(--ink)',
          borderRadius: '16px',
          padding: '10px 14px',
          ...(isUser ? { borderBottomRightRadius: '4px' } : { borderBottomLeftRadius: '4px' }),
        }}
      >
        {!isUser && (
          <div className="flex items-center gap-1 mb-1">
            <span className="ai-badge text-[10px] px-1 py-0.5 rounded" style={{ background: 'rgba(255,255,255,0.4)', color: 'var(--accent)', border: '1px solid var(--glass-border)' }}>AI 生成</span>
          </div>
        )}
        <p className="text-sm leading-relaxed whitespace-pre-wrap">{message.text}</p>

        {message.proposalStatus === 'executing' && (
          <div className="flex items-center gap-1 mt-2 text-xs" style={{ color: isUser ? 'rgba(255,255,255,0.7)' : 'var(--ink3)' }}>
            <span className="inline-block w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin" />
            <span>正在记账…</span>
          </div>
        )}

        {message.proposalStatus === 'undone' && (
          <div className="text-xs mt-1" style={{ color: isUser ? 'rgba(255,255,255,0.7)' : 'var(--ink3)' }}>
            ↩️ 已撤销
          </div>
        )}

        {message.proposalStatus === 'error' && (
          <div className="text-xs mt-1" style={{ color: isUser ? '#fca5a5' : 'var(--expense)' }}>
            ⚠️ 记账失败，请稍后重试
          </div>
        )}
      </div>

      {!isUser && isConfirming && message.proposal && (
        <div className="mt-2 w-full max-w-[90%]">
          <ConfirmationCard
            proposal={message.proposal}
            categories={categories}
            onConfirm={onConfirm}
            onCancel={onCancel}
          />
        </div>
      )}
    </div>
  )
}
