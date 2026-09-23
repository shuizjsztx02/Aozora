/**
 * EmojiPicker — 分类图标 emoji 选择器（第八轮 REQ-8）。
 * 网格展示常用 emoji，点击回调选中；受控（selected 高亮）。
 */

/** 导出供 IconPicker 判定「当前值是否为宫格 emoji」（非宫格值回填自定义文本框）。 */
export const EMOJI = [
  // 餐饮
  '🍽️', '🍜', '🍱', '🥐', '🍕', '🍔', '☕', '🍰',
  // 交通
  '🚇', '🚌', '🚕', '🚗', '🚲', '✈️', '🚄', '⛽',
  // 购物/生活
  '🛍️', '🛒', '👔', '🧴', '🧸', '💄', '📦', '🎁',
  // 居家
  '🏠', '🏢', '💡', '🛏️', '🧹', '🔧', '🚿', '🐶',
  // 娱乐/学习
  '🎮', '🎬', '🎲', '🎵', '📚', '✏️', '🎨', '💻',
  // 健康/其他
  '🏥', '💊', '💉', '🏃', '📱', '💬', '✈️', '📄',
  // 收入
  '💰', '💵', '💴', '🪙', '📈', '🧧', '💼', '🎯',
]

interface EmojiPickerProps {
  selected: string
  onPick: (emoji: string) => void
}

export function EmojiPicker({ selected, onPick }: EmojiPickerProps) {
  return (
    <div
      className="grid grid-cols-8 gap-1"
      role="listbox"
      aria-label="选择图标"
      data-testid="emoji-picker"
    >
      {EMOJI.map((e, i) => (
        <button
          key={`${e}-${i}`}
          type="button"
          role="option"
          aria-selected={selected === e}
          onClick={() => onPick(e)}
          className="aspect-square rounded-lg text-lg flex items-center justify-center transition-colors"
          style={{
            background: selected === e ? 'var(--accent)' : 'var(--glass)',
            border: selected === e ? '2px solid var(--accent)' : '1px solid var(--glass-border)',
          }}
        >
          {e}
        </button>
      ))}
    </div>
  )
}
