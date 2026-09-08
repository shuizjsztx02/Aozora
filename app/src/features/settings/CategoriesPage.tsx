/**
 * CategoriesPage — 分类管理页（第八轮 REQ-8：支持自定义）。
 *
 * 展示支出/收入分类（顶级 + 二级），支持新建、编辑（改名/图标/父级）。
 * 图标 = emoji 选择器 + 相册照片上传（icon.pick，base64 存本地）。
 * 删除暂不提供（REQ-8 只要求新建 / 二级 / 图标）。
 */

import { useState, useEffect, useCallback } from 'react'
import { PageHeader } from '../../components/Layout'
import { useBridge } from '../../platform/hooks/use-bridge'
import { BridgeError } from '../../platform/bridge/native-bridge'
import { newUuid } from '../../platform/uuid'
import { CategoryIcon } from '../../components/CategoryIcon'
import { EmojiPicker } from '../../components/EmojiPicker'
import type { CategoryData } from '../../platform/bridge/bridge-types'

export function CategoriesPage() {
  const bridge = useBridge()
  const [categories, setCategories] = useState<CategoryData[]>([])
  const [loading, setLoading] = useState(true)
  // editing = null 关闭抽屉；否则承载新建/编辑目标
  const [editing, setEditing] = useState<{ mode: 'new' | 'edit'; base: CategoryData } | null>(null)

  const load = useCallback(async () => {
    try {
      setCategories(await bridge.listCategories())
    } catch (err) {
      console.error('Failed to load categories:', err)
    } finally {
      setLoading(false)
    }
  }, [bridge])

  useEffect(() => { load() }, [load])

  const topLevel = (kind: 'expense' | 'income') =>
    categories.filter(c => c.kind === kind && !c.parentId)
  const childrenOf = (id: string) => categories.filter(c => c.parentId === id)

  const openNew = (kind: 'expense' | 'income') =>
    setEditing({ mode: 'new', base: { id: '', kind, name: '', parentId: null, icon: null, version: 1 } })
  const openEdit = (c: CategoryData) =>
    setEditing({ mode: 'edit', base: c })

  return (
    <div className="min-h-screen">
      <PageHeader back />

      <div className="px-4 py-4 space-y-4 pb-20">
        {loading ? (
          <div className="glass-card p-4 text-xs text-ink3">加载中…</div>
        ) : (
          <>
            <CategorySection
              title="支出分类"
              tops={topLevel('expense')}
              childrenOf={childrenOf}
              onEdit={openEdit}
              onNew={() => openNew('expense')}
            />
            <CategorySection
              title="收入分类"
              tops={topLevel('income')}
              childrenOf={childrenOf}
              onEdit={openEdit}
              onNew={() => openNew('income')}
            />
          </>
        )}
      </div>

      {editing && (
        <CategoryEditor
          editing={editing}
          siblings={topLevel(editing.base.kind)}
          bridge={bridge}
          onSaved={() => { setEditing(null); load() }}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  )
}

function CategorySection({
  title, tops, childrenOf, onEdit, onNew,
}: {
  title: string
  tops: CategoryData[]
  childrenOf: (id: string) => CategoryData[]
  onEdit: (c: CategoryData) => void
  onNew: () => void
}) {
  return (
    <div className="glass-card p-4">
      <div className="text-sm font-medium mb-3" style={{ color: 'var(--ink)' }}>
        {title} ({tops.length})
      </div>
      <div className="grid grid-cols-3 gap-2">
        {tops.map(cat => {
          const subs = childrenOf(cat.id)
          return (
            <button
              key={cat.id}
              onClick={() => onEdit(cat)}
              className="flex flex-col items-center gap-1 py-2 px-1 rounded-lg"
              style={{ background: 'var(--glass)' }}
              data-testid={`category-cell-${cat.id}`}
            >
              <CategoryIcon icon={cat.icon} name={cat.name} className="text-xl" />
              <span className="text-xs text-ink2 truncate w-full text-center">{cat.name}</span>
              {subs.length > 0 && (
                <span className="text-[10px]" style={{ color: 'var(--ink3)' }}>{subs.length} 个子类</span>
              )}
            </button>
          )
        })}
      </div>
      <button
        onClick={onNew}
        className="w-full mt-3 py-2 rounded-lg text-sm font-medium"
        style={{ background: 'var(--accent)', color: 'white' }}
        data-testid={`category-add-${title}`}
      >
        ＋ 新建{title.startsWith('支出') ? '支出' : '收入'}分类
      </button>
    </div>
  )
}

function CategoryEditor({
  editing, siblings, bridge, onSaved, onClose,
}: {
  editing: { mode: 'new' | 'edit'; base: CategoryData }
  siblings: CategoryData[]
  bridge: ReturnType<typeof useBridge>
  onSaved: () => void
  onClose: () => void
}) {
  const isEdit = editing.mode === 'edit'
  const [name, setName] = useState(editing.base.name)
  const [icon, setIcon] = useState<string | null>(editing.base.icon ?? null)
  const [parentId, setParentId] = useState<string | null>(editing.base.parentId ?? null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const pickPhoto = useCallback(async () => {
    setError(null)
    try {
      const r = await bridge.pickIcon('gallery')
      setIcon(r.dataUri)
    } catch (e) {
      if (e instanceof BridgeError && e.code === 'cancelled') return
      if (e instanceof BridgeError && e.code === 'unavailable') { setError('当前环境不支持选择照片'); return }
      setError('照片选择失败，请重试')
    }
  }, [bridge])

  const handleSave = useCallback(async () => {
    if (!name.trim()) { setError('请填写分类名称'); return }
    setSaving(true)
    setError(null)
    try {
      if (isEdit) {
        await bridge.updateCategory(
          {
            id: editing.base.id, kind: editing.base.kind, name: name.trim(),
            parentId, icon, version: editing.base.version + 1,
          },
          newUuid()
        )
      } else {
        await bridge.insertCategory(
          {
            id: newUuid(), ownerId: 'local', kind: editing.base.kind, name: name.trim(),
            parentId, icon, version: 1,
          },
          newUuid()
        )
      }
      onSaved()
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败，请重试')
    } finally {
      setSaving(false)
    }
  }, [isEdit, editing, name, parentId, icon, bridge, onSaved])

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col justify-end"
      style={{ background: 'rgba(0,0,0,0.3)' }}
      onClick={saving ? undefined : onClose}
      data-testid="category-editor-mask"
    >
      <div
        className="sheet-glass relative z-10 p-4 pb-6 space-y-4"
        onClick={e => e.stopPropagation()}
        data-testid="category-editor"
      >
        <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>
          {isEdit ? '编辑分类' : `新建${editing.base.kind === 'expense' ? '支出' : '收入'}分类`}
        </div>

        {/* 图标预览 + 选择 */}
        <div className="flex items-center gap-3">
          <div
            className="flex items-center justify-center w-14 h-14 rounded-full text-2xl shrink-0"
            style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
          >
            <CategoryIcon icon={icon} name={name || '?'} className="text-2xl" />
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
                onClick={() => setIcon(null)}
                className="text-xs px-3 py-1 rounded-lg"
                style={{ color: 'var(--ink3)' }}
                data-testid="icon-clear"
              >
                清除自定义图标
              </button>
            )}
          </div>
        </div>

        <EmojiPicker selected={icon && !icon.startsWith('data:') ? icon : ''} onPick={e => setIcon(e)} />

        {/* 名称 */}
        <input
          type="text"
          value={name}
          onChange={e => setName(e.target.value)}
          placeholder="分类名称"
          maxLength={64}
          className="w-full px-3 py-2 rounded-lg text-sm outline-none"
          style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
          data-testid="category-name"
        />

        {/* 父级（二级分类） */}
        <div>
          <div className="text-xs mb-1" style={{ color: 'var(--ink3)' }}>上级分类（可选，用于二级分类）</div>
          <div className="flex flex-wrap gap-1.5">
            <button
              type="button"
              onClick={() => setParentId(null)}
              className="text-xs px-2.5 py-1.5 rounded-lg"
              style={{
                background: parentId === null ? 'var(--accent)' : 'var(--glass)',
                color: parentId === null ? 'white' : 'var(--ink2)',
                border: '1px solid var(--glass-border)',
              }}
              data-testid="parent-none"
            >
              无（顶级）
            </button>
            {siblings
              .filter(s => !isEdit || s.id !== editing.base.id) // 排除自身
              .map(s => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => setParentId(s.id)}
                  className="text-xs px-2.5 py-1.5 rounded-lg"
                  style={{
                    background: parentId === s.id ? 'var(--accent)' : 'var(--glass)',
                    color: parentId === s.id ? 'white' : 'var(--ink2)',
                    border: '1px solid var(--glass-border)',
                  }}
                >
                  {s.name}
                </button>
              ))}
          </div>
        </div>

        {error && (
          <div className="text-xs px-3 py-2 rounded-lg" style={{ background: 'rgba(220,38,38,0.1)', color: '#dc2626' }}>
            {error}
          </div>
        )}

        <div className="flex gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="flex-1 py-2 rounded-lg text-sm"
            style={{ background: 'var(--glass)', color: 'var(--ink2)' }}
          >
            取消
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || !name.trim()}
            className="flex-1 py-2 rounded-lg text-sm font-medium text-white disabled:opacity-40"
            style={{ background: 'var(--accent)' }}
            data-testid="category-save"
          >
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}
