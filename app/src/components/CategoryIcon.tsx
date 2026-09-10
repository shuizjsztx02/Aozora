/**
 * CategoryIcon — 分类图标统一渲染（第八轮 REQ-8）。
 *
 * 优先级：icon 以 data: 开头 → 照片（<img>）；icon 非空 → emoji 文本；
 * 否则回退按名映射 getCategoryEmoji(name)。全站所有分类图标渲染点统一走此组件，
 * 保证「同一分类在记账宫格 / 明细 / 首页 / 聊天卡」显示一致。
 */
import type { CSSProperties } from 'react'
import { getCategoryEmoji } from './category-emoji'

interface CategoryIconProps {
  icon?: string | null
  name: string
  /** 追加到外层容器的 class（尺寸/圆角等由调用方控制）。 */
  className?: string
  /** icon 为空时的回退（默认按名映射 getCategoryEmoji）；账本/账户等非常规分类传固定 emoji。 */
  fallback?: string
}

// REQ-32（T12-2）：两分支同构外层容器 —— font-size 由调用方 className 继承，
// 容器锁定 1em×1em，emoji 自然行高（≈1.5em）与照片 1.25em 均居中溢出盒子、
// 不再撑出不同图标行高，宫格名称行基线对齐；verticalAlign 沿用原 img 分支的
// 行中对齐（T9-3），保证文字行内两分支观感一致。
const containerStyle: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: '1em',
  height: '1em',
  verticalAlign: 'middle',
}

export function CategoryIcon({ icon, name, className, fallback }: CategoryIconProps) {
  if (icon && icon.startsWith('data:')) {
    return (
      <span className={className} style={containerStyle} aria-hidden>
        {/* T9-3（REQ-12B）：1.25em 向 emoji 视觉尺寸看齐；圆形（行中对齐已上移至容器，REQ-32） */}
        <img
          src={icon}
          alt=""
          className="object-cover"
          style={{ width: '1.25em', height: '1.25em', borderRadius: '50%' }}
        />
      </span>
    )
  }
  return (
    <span className={className} style={containerStyle} aria-hidden>
      {icon || fallback || getCategoryEmoji(name)}
    </span>
  )
}
