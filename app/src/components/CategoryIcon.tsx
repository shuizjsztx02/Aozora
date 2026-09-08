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
}

export function CategoryIcon({ icon, name, className }: CategoryIconProps) {
  if (icon && icon.startsWith('data:')) {
    return (
      <img
        src={icon}
        alt=""
        className={`object-cover ${className ?? ''}`}
        style={{ width: '1em', height: '1em' }}
        aria-hidden
      />
    )
  }
  return (
    <span className={className} aria-hidden>
      {icon || getCategoryEmoji(name)}
    </span>
  )
}
