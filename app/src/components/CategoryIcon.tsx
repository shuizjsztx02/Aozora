/**
 * CategoryIcon — 分类图标统一渲染（第八轮 REQ-8）。
 *
 * 优先级：icon 以 data: 开头 → 照片（<img>）；icon 非空 → emoji 文本；
 * 否则回退按名映射 getCategoryEmoji(name)。全站所有分类图标渲染点统一走此组件，
 * 保证「同一分类在记账宫格 / 明细 / 首页 / 聊天卡」显示一致。
 */
import { getCategoryEmoji } from './category-emoji'

interface CategoryIconProps {
  icon?: string | null
  name: string
  /** 追加到外层容器的 class（尺寸/圆角等由调用方控制）。 */
  className?: string
  /** icon 为空时的回退（默认按名映射 getCategoryEmoji）；账本/账户等非常规分类传固定 emoji。 */
  fallback?: string
}

export function CategoryIcon({ icon, name, className, fallback }: CategoryIconProps) {
  if (icon && icon.startsWith('data:')) {
    return (
      <img
        src={icon}
        alt=""
        className={`object-cover ${className ?? ''}`}
        // T9-3（REQ-12B）：1.25em 向 emoji 视觉尺寸看齐；圆形 + 行中对齐与 emoji 观感统一
        style={{ width: '1.25em', height: '1.25em', borderRadius: '50%', display: 'inline-block', verticalAlign: 'middle' }}
        aria-hidden
      />
    )
  }
  return (
    <span className={className} aria-hidden>
      {icon || fallback || getCategoryEmoji(name)}
    </span>
  )
}
