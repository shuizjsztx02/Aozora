/**
 * IconPicker — 图标选择统一组件（第九轮 T9-6 / 决策 #5）。
 *
 * 预览（CategoryIcon）+「🖼️ 从相册选择照片」（bridge.pickIcon('gallery')）
 * + EmojiPicker 宫格 +（customText 时）≤4 字符自定义文本框。
 * 单一受控值 icon：emoji / 自定义文本 / 照片 data URI，三者互斥——
 * 后选覆写先选；照片在场时文本框与宫格选择态让位（决策 #5"非空优先"），
 * 但用户仍可直接点 emoji/输入文本覆写照片（与 CategoriesPage 原行为一致）。
 * 供 CategoryEditor、BooksPage、AccountsPage 三处共用。
 */
import { useState, useCallback } from 'react'
import { useBridge } from '../platform/hooks/use-bridge'
import { BridgeError } from '../platform/bridge/native-bridge'
import { CategoryIcon } from './CategoryIcon'
import { EmojiPicker, EMOJI } from './EmojiPicker'

interface IconPickerProps {
  /** 受控值：emoji、自定义文本或照片 data URI；null/'' = 未选 */
  icon: string | null
  onChange: (v: string | null) => void
  /** 显示"自定义图标"文本框（≤4 字符，账户表单用） */
  customText?: boolean
  /** 预览按名回退用的名称（icon 为空时 CategoryIcon 语义） */
  name?: string
  /** icon 为空时的固定回退 emoji（账本 📒 / 账户 💰） */
  fallback?: string
}

export function IconPicker({ icon, onChange, customText, name = '', fallback }: IconPickerProps) {
  const bridge = useBridge()
  const [photoError, setPhotoError] = useState<string | null>(null)

  const isPhoto = !!icon && icon.startsWith('data:')
  // "非预置回填文本框"：值非照片且不在 emoji 宫格内 → 视为自定义文本
  const customValue = !isPhoto && icon && !EMOJI.includes(icon) ? icon : ''

  const pickPhoto = useCallback(async () => {
    setPhotoError(null)
    try {
      const r = await bridge.pickIcon('gallery')
      onChange(r.dataUri)
    } catch (e) {
      if (e instanceof BridgeError && e.code === 'cancelled') return
      if (e instanceof BridgeError && e.code === 'unavailable') {
        setPhotoError('当前环境不支持选择照片')
        return
      }
      setPhotoError('照片选择失败，请重试')
    }
  }, [bridge, onChange])

  return (
    <div className="space-y-3">
      {/* 预览 + 照片选择 */}
      <div className="flex items-center gap-3">
        <div
          className="flex items-center justify-center w-14 h-14 rounded-full text-2xl shrink-0"
          style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
        >
          <CategoryIcon icon={icon} name={name || '?'} fallback={fallback} className="text-2xl" />
        </div>
        <div className="flex flex-col gap-2">
          <button
            type="button"
            onClick={pickPhoto}
            className="text-xs px-3 py-2 rounded-lg"
            style={{ background: 'var(--glass)', color: 'var(--ink2)', border: '1px solid var(--glass-border)' }}
            data-testid="icon-pick-photo"
          >
            🖼️ 从相册选择照片
          </button>
          {icon && (
            <button
              type="button"
              onClick={() => { setPhotoError(null); onChange(null) }}
              className="text-xs px-3 py-1 rounded-lg"
              style={{ color: 'var(--ink3)' }}
              data-testid="icon-clear"
            >
              清除自定义图标
            </button>
          )}
        </div>
      </div>

      {photoError && (
        <div className="text-xs px-3 py-2 rounded-lg" style={{ background: 'rgba(220,38,38,0.1)', color: '#dc2626' }}>
          {photoError}
        </div>
      )}

      <EmojiPicker selected={isPhoto ? '' : (customValue ? '' : (icon ?? ''))} onPick={e => onChange(e)} />

      {customText && (
        <div className="flex items-center gap-2">
          <span className="text-xs shrink-0" style={{ color: 'var(--ink3)' }}>自定义图标</span>
          <input
            type="text"
            value={customValue}
            onChange={e => onChange(e.target.value.slice(0, 4) || null)}
            maxLength={4}
            placeholder="≤4 字符"
            data-testid="custom-icon-input"
            className="w-20 px-3 py-1.5 rounded-lg text-sm outline-none"
            style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
          />
        </div>
      )}
    </div>
  )
}
