/**
 * PersonasPage — 聊天角色管理（第十一轮 T11-7 / REQ-28 / 决策 #8）。
 *
 * 卡列表（头像+昵称+人设两行截断；使用中徽标+置前、非使用中行"使用"按钮）
 * + 新建/编辑表单（IconPicker 头像 + 昵称 ≤16 + 人设 textarea ≤2000 计数）。
 * 角色仅存本机不上行同步（镜像定时规则，见 Repository 说明）；当前角色指针
 * = meta.active_persona_id（切换即 meta.set；删除使用中角色由回退链自动切回，
 * 见 domain/personas.ts resolveActivePersona）。
 */

import { useState, useEffect, useCallback, useMemo } from 'react'
import { PageHeader } from '../../components/Layout'
import { useBridge } from '../../platform/hooks/use-bridge'
import { newUuid } from '../../platform/uuid'
import { CategoryIcon } from '../../components/CategoryIcon'
import { IconPicker } from '../../components/IconPicker'
import { resolveActivePersona, DEFAULT_PERSONA_NAME } from '../../domain/personas'
import type { PersonaData } from '../../platform/bridge/bridge-types'

const MAX_NAME_LEN = 16
const MAX_PERSONA_LEN = 2000

interface PersonaForm {
  name: string
  avatar: string | null
  persona: string
}

const EMPTY_FORM: PersonaForm = { name: '', avatar: null, persona: '' }

export function PersonasPage() {
  const bridge = useBridge()
  const [personas, setPersonas] = useState<PersonaData[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // 表单：editingId=null 表示新建，非 null 表示编辑该角色
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingVersion, setEditingVersion] = useState(0)
  const [form, setForm] = useState<PersonaForm>(EMPTY_FORM)

  const load = useCallback(async () => {
    try {
      const [list, meta] = await Promise.all([
        bridge.listPersonas(),
        bridge.getMeta(['active_persona_id']).catch(() => ({}) as Record<string, string | null>),
      ])
      setPersonas(list)
      setActiveId(meta['active_persona_id'] ?? null)
    } catch (err) {
      console.error('Failed to load personas:', err)
      setError('加载失败，请重试')
    } finally {
      setLoading(false)
    }
  }, [bridge])

  useEffect(() => { load() }, [load])

  // 使用中置前（决策 #8）：回退链解析出的当前角色排首位，其余保持 createdAt 序
  const active = useMemo(() => resolveActivePersona(personas, activeId), [personas, activeId])
  const ordered = useMemo(() => {
    if (!active) return personas
    return [active, ...personas.filter(p => p.id !== active.id)]
  }, [personas, active])

  const openNew = () => {
    setEditingId(null)
    setForm(EMPTY_FORM)
    setError(null)
    setShowForm(true)
  }

  const openEdit = (p: PersonaData) => {
    setEditingId(p.id)
    setEditingVersion(p.version)
    setForm({ name: p.name, avatar: p.avatar, persona: p.persona })
    setError(null)
    setShowForm(true)
  }

  const closeForm = () => {
    setShowForm(false)
    setEditingId(null)
    setError(null)
  }

  const handleSubmit = async () => {
    const name = form.name.trim()
    const personaText = form.persona.trim()
    if (!name) { setError('请填写角色昵称'); return }
    if (name.length > MAX_NAME_LEN) { setError(`昵称最多 ${MAX_NAME_LEN} 字`); return }
    if (!personaText) { setError('请填写人设内容'); return }
    if (personaText.length > MAX_PERSONA_LEN) { setError(`人设最多 ${MAX_PERSONA_LEN} 字`); return }

    setError(null)
    try {
      if (editingId) {
        await bridge.updatePersona({
          id: editingId,
          name,
          avatar: form.avatar,
          persona: personaText,
          version: editingVersion + 1,
        })
      } else {
        await bridge.insertPersona({
          id: newUuid(),
          name,
          avatar: form.avatar,
          persona: personaText,
          version: 1,
        })
      }
      closeForm()
      await load()
    } catch (err) {
      console.error('Failed to save persona:', err)
      setError('保存失败，请重试')
    }
  }

  /** 切换当前角色（决策 #8）：写 meta.active_persona_id；AssistantPage 下次读取生效。 */
  const handleUse = async (p: PersonaData) => {
    setError(null)
    try {
      await bridge.setMeta('active_persona_id', p.id)
      await load()
    } catch (err) {
      console.error('Failed to set active persona:', err)
      setError('切换失败，请重试')
    }
  }

  const handleArchive = async (p: PersonaData) => {
    const suffix = p.id === active?.id ? '（当前使用中，切换后自动回退默认角色）' : ''
    if (!window.confirm(`确定要删除角色「${p.name}」吗？${suffix}`)) return
    setError(null)
    try {
      await bridge.archivePersona(p.id)
      if (editingId === p.id) closeForm()
      await load()
    } catch (err) {
      console.error('Failed to archive persona:', err)
      setError('删除失败，请重试')
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen">
        <PageHeader back title="角色管理" />
        <div className="px-4 py-10 text-center text-sm" style={{ color: 'var(--ink3)' }}>加载中…</div>
      </div>
    )
  }

  if (showForm) {
    return (
      <div className="min-h-screen">
        <PageHeader back title={editingId ? '编辑角色' : '新建角色'} />
        <div className="px-4 py-4 space-y-4 pb-20">
          <div>
            <label className="block text-xs mb-1.5" style={{ color: 'var(--ink2)' }}>
              角色昵称（≤{MAX_NAME_LEN} 字）
            </label>
            <input
              type="text"
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value.slice(0, MAX_NAME_LEN) }))}
              maxLength={MAX_NAME_LEN}
              placeholder="如：小贝"
              data-testid="persona-name-input"
              className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
              style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
            />
          </div>

          <div>
            <label className="block text-xs mb-1.5" style={{ color: 'var(--ink2)' }}>
              人设（≤{MAX_PERSONA_LEN} 字，用于聊天语气与偏好）
            </label>
            <textarea
              value={form.persona}
              onChange={e => setForm(f => ({ ...f, persona: e.target.value.slice(0, MAX_PERSONA_LEN) }))}
              maxLength={MAX_PERSONA_LEN}
              rows={6}
              placeholder="描述角色的性格、说话方式、与你相处的风格…"
              data-testid="persona-text-input"
              className="w-full px-3 py-2.5 rounded-xl text-sm outline-none resize-none"
              style={{ background: 'var(--glass)', color: 'var(--ink)', border: '1px solid var(--glass-border)' }}
            />
            <div className="text-right text-[10px] mt-0.5" style={{ color: 'var(--ink3)' }}>
              {form.persona.trim().length}/{MAX_PERSONA_LEN}
            </div>
          </div>

          <div>
            <label className="block text-xs mb-1.5" style={{ color: 'var(--ink2)' }}>角色头像</label>
            <IconPicker
              icon={form.avatar}
              onChange={v => setForm(f => ({ ...f, avatar: v }))}
              name={form.name || DEFAULT_PERSONA_NAME}
              fallback="👤"
            />
          </div>

          {error && (
            <div
              className="text-xs px-3 py-2 rounded-lg"
              style={{ background: 'rgba(220,38,38,0.1)', color: '#dc2626' }}
              data-testid="persona-form-error"
            >
              {error}
            </div>
          )}

          <div className="flex gap-3">
            <button
              type="button"
              onClick={closeForm}
              className="flex-1 py-2.5 rounded-xl text-sm"
              style={{ background: 'var(--glass)', color: 'var(--ink2)', border: '1px solid var(--glass-border)' }}
            >
              取消
            </button>
            <button
              type="button"
              onClick={handleSubmit}
              className="flex-1 py-2.5 rounded-xl text-sm font-medium text-white"
              style={{ background: 'var(--accent)' }}
              data-testid="persona-save"
            >
              保存
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen">
      <PageHeader back title="角色管理" />

      <div className="px-4 py-4 space-y-4 pb-20">
        <div className="text-xs text-ink3 text-center">聊天角色仅保存在本机，不随账号同步</div>

        {error && (
          <div
            className="text-xs px-3 py-2 rounded-lg"
            style={{ background: 'rgba(220,38,38,0.1)', color: '#dc2626' }}
            data-testid="persona-error"
          >
            {error}
          </div>
        )}

        {personas.length === 0 ? (
          <div className="glass-card rounded-2xl p-6 text-center text-sm" style={{ color: 'var(--ink3)' }}>
            暂无角色
          </div>
        ) : (
          <div className="space-y-3">
            {ordered.map(p => {
              const isActive = p.id === active?.id
              return (
                <div
                  key={p.id}
                  className="glass-card rounded-2xl p-3 flex items-start gap-3"
                  data-testid={`persona-card-${p.id}`}
                >
                  <div
                    className="flex items-center justify-center w-11 h-11 rounded-full text-xl shrink-0"
                    style={{ background: 'var(--glass)', border: '1px solid var(--glass-border)' }}
                  >
                    <CategoryIcon icon={p.avatar} name={p.name} fallback="👤" className="text-xl" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold truncate" style={{ color: 'var(--ink)' }}>
                        {p.name}
                      </span>
                      {isActive && (
                        <span
                          className="text-[10px] px-1.5 py-0.5 rounded shrink-0"
                          style={{ background: 'var(--accent)', color: '#fff' }}
                          data-testid={`persona-active-badge-${p.id}`}
                        >
                          使用中
                        </span>
                      )}
                    </div>
                    <p
                      className="text-xs mt-1 leading-relaxed"
                      style={{
                        color: 'var(--ink3)',
                        display: '-webkit-box',
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: 'vertical',
                        overflow: 'hidden',
                      }}
                    >
                      {p.persona}
                    </p>
                    <div className="flex items-center justify-end gap-2 mt-2">
                      {!isActive && (
                        <button
                          type="button"
                          onClick={() => handleUse(p)}
                          className="text-xs px-3 py-1 rounded-lg"
                          style={{ color: 'var(--accent)', border: '1px solid var(--accent)' }}
                          data-testid={`persona-use-${p.id}`}
                        >
                          使用
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => openEdit(p)}
                        className="text-xs px-3 py-1 rounded-lg"
                        style={{ color: 'var(--ink2)', border: '1px solid var(--glass-border)' }}
                        data-testid={`persona-edit-${p.id}`}
                      >
                        编辑
                      </button>
                      <button
                        type="button"
                        onClick={() => handleArchive(p)}
                        className="text-xs px-3 py-1 rounded-lg"
                        style={{ color: '#dc2626', border: '1px solid rgba(220,38,38,0.3)' }}
                        data-testid={`persona-delete-${p.id}`}
                      >
                        删除
                      </button>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}

        <button
          type="button"
          onClick={openNew}
          className="w-full py-3 rounded-xl text-sm font-medium text-white"
          style={{ background: 'var(--accent)' }}
          data-testid="persona-new"
        >
          ＋ 新建角色
        </button>
      </div>
    </div>
  )
}
