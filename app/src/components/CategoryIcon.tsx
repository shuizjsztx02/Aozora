/**
 * CategoryIcon — 分类图标统一渲染（第八轮 REQ-8）。
 *
 * 优先级：icon 以 data: 开头 → 照片（<img>）；icon 非空 → emoji 文本；
 * 否则回退按名映射 getCategoryEmoji(name)。全站所有分类图标渲染点统一走此组件，
 * 保证「同一分类在记账宫格 / 明细 / 首页 / 聊天卡」显示一致。
 *
 * REQ-68（T18-4）：历史兜底裸词（见 [PLACEHOLDER_ICONS]）视同空值 —— 它们是
 * 英文单词、不是图标，否则会被当 emoji 原样画进圆形框。
 */
import type { CSSProperties } from 'react'
import { getCategoryEmoji } from './category-emoji'

/**
 * 第十八轮 REQ-68：服务端 book/account 的 icon 列可空，旧客户端的兜底赋值
 * 曾把 `"default"`（Repository/SyncModels 四处）与 `"book"`/`"account"`
 * （AozoraBridge 两处）写进库；存量行仍带着这些词。
 *
 * 兜底值已改为 emoji，但存量行不会自己回填（本轮只允许新增一个 Room schema 版本，
 * 见第十八轮决策 #1）——故在渲染层把这类裸词视同「未设置」，走 fallback。
 */
const PLACEHOLDER_ICONS = new Set(['default', 'book', 'account'])

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
  // 存量兜底裸词视同「未设置」（REQ-68）
  const value = icon && !PLACEHOLDER_ICONS.has(icon) ? icon : null
  if (value && value.startsWith('data:')) {
    return (
      <span className={className} style={containerStyle} aria-hidden>
        {/* T9-3（REQ-12B）：1.25em 向 emoji 视觉尺寸看齐；圆形（行中对齐已上移至容器，REQ-32） */}
        <img
          src={value}
          alt=""
          className="object-cover"
          style={{ width: '1.25em', height: '1.25em', borderRadius: '50%' }}
        />
      </span>
    )
  }
  return (
    <span className={className} style={containerStyle} aria-hidden>
      {value || fallback || getCategoryEmoji(name)}
    </span>
  )
}
