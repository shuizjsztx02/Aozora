/**
 * TxListRow — 流水行共享展示组件（T14-1 / REQ-38，决策 #2）。
 *
 * 视觉逐值 = DetailPage 原流水行：40×40 玻璃方格 + CategoryIcon 19px、
 * 标题 15px semibold（备注优先策略由宿主计算后传入）、副行 12px、
 * 金额 17px bold（无 ¥，按 isExpense 前缀 +/-）、AI 徽标 9px 直角。
 * 分割线由宿主决定（Detail 末行不画、Home 每行画）；right 槽放金额右侧
 * 附加控件（如 DetailPage 的 ✕ 删除钮）。除首页/明细外不引新消费方。
 */
import type { ReactNode } from 'react'
import { CategoryIcon } from './CategoryIcon'
import * as Money from '../domain/money'

interface TxListRowProps {
  /** 分类 icon（emoji 或 data: URL），透传 CategoryIcon */
  icon?: string | null
  /** 分类名，供 CategoryIcon 无 icon 时按名回退 emoji */
  iconName: string
  title: string
  /** 副行文案（如 "HH:mm · 分类名"） */
  sub: string
  amountCents: number
  isExpense: boolean
  /** 显示 AI 徽标（tx.source === 'nlu_ai'） */
  aiBadge?: boolean
  onClick?: () => void
  /** 金额右侧插槽（如 ✕ 删除钮） */
  right?: ReactNode
  /** 是否画底部分割线 */
  borderBottom?: boolean
  /**
   * 金额展示文本覆盖（缺省按 amountCents 格式化）。T21-10：待确认卡在解析结果
   * 缺金额时显示「—」，但仍要与流水行共用同一套排版与字号。
   */
  amountLabel?: string
}

export function TxListRow({
  icon, iconName, title, sub, amountCents, isExpense,
  aiBadge, onClick, right, borderBottom, amountLabel,
}: TxListRowProps) {
  return (
    <div
      className="flex items-center gap-3 py-2.5 cursor-pointer active:opacity-70"
      style={borderBottom ? { borderBottom: '1px solid var(--glass-border)' } : undefined}
      onClick={onClick}
    >
      <div
        className="flex items-center justify-center shrink-0 rounded-xl"
        style={{ width: 40, height: 40, background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
      >
        <CategoryIcon icon={icon} name={iconName} className="text-[19px] leading-none" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-[15px] font-semibold truncate" style={{ color: 'var(--ink)' }}>
          {title}
          {aiBadge && (
            <span
              className="ml-1.5 align-middle text-[9px] px-1 py-px rounded"
              style={{ background: 'var(--glass)', color: 'var(--accent)', border: '1px solid var(--glass-border)' }}
            >
              AI
            </span>
          )}
        </div>
        <div className="text-xs truncate mt-0.5" style={{ color: 'var(--ink3)' }}>{sub}</div>
      </div>
      <span className="money-num text-[17px] font-bold shrink-0" style={{ color: 'var(--ink)' }}>
        {amountLabel ?? `${isExpense ? '-' : '+'}${Money.formatYuan(Money.cents(amountCents))}`}
      </span>
      {right}
    </div>
  )
}
