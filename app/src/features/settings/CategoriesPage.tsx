/**
 * CategoriesPage — 分类管理页。
 *
 * 展示当前所有收支分类（分类由系统预置，暂不支持新增）。
 */

import { useState, useEffect, useCallback } from 'react'
import { PageHeader } from '../../components/Layout'
import { useBridge } from '../../platform/hooks/use-bridge'
import { getCategoryEmoji } from '../../components/category-emoji'
import type { CategoryData } from '../../platform/bridge/bridge-types'

export function CategoriesPage() {
  const bridge = useBridge()
  const [categories, setCategories] = useState<CategoryData[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const list = await bridge.call('category.list', {}) as CategoryData[]
      setCategories(list)
    } catch (err) {
      console.error('Failed to load categories:', err)
    } finally {
      setLoading(false)
    }
  }, [bridge])

  useEffect(() => { load() }, [load])

  const expenseCats = categories.filter(c => c.kind === 'expense')
  const incomeCats = categories.filter(c => c.kind === 'income')

  return (
    <div className="min-h-screen">
      <PageHeader back />

      <div className="px-4 py-4 space-y-4 pb-20">
        {/* 支出分类 */}
        <div className="glass-card p-4">
          <div className="text-sm font-medium mb-3" style={{ color: 'var(--ink)' }}>
            支出分类 ({expenseCats.length})
          </div>
          {loading && expenseCats.length === 0 ? (
            <div className="text-xs text-ink3">加载中…</div>
          ) : (
            <div className="grid grid-cols-3 gap-2">
              {expenseCats.map(cat => (
                <div key={cat.id} className="flex flex-col items-center gap-1 py-2 px-1 rounded-lg" style={{ background: 'var(--glass)' }}>
                  <span className="text-xl">{getCategoryEmoji(cat.name)}</span>
                  <span className="text-xs text-ink2 truncate w-full text-center">{cat.name}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 收入分类 */}
        {incomeCats.length > 0 && (
          <div className="glass-card p-4">
            <div className="text-sm font-medium mb-3" style={{ color: 'var(--ink)' }}>
              收入分类 ({incomeCats.length})
            </div>
            <div className="grid grid-cols-3 gap-2">
              {incomeCats.map(cat => (
                <div key={cat.id} className="flex flex-col items-center gap-1 py-2 px-1 rounded-lg" style={{ background: 'var(--glass)' }}>
                  <span className="text-xl">{getCategoryEmoji(cat.name)}</span>
                  <span className="text-xs text-ink2 truncate w-full text-center">{cat.name}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="text-xs text-ink3 text-center py-2">
          分类由系统预置，暂不支持自定义
        </div>
      </div>
    </div>
  )
}
