/**
 * T16-5（REQ-61 / 决策 #6）：四主题自定义背景图存储与应用。
 *
 * 存储分层铁律：背景图 data URI 远超 meta 白名单 8192 字符上限、且有
 * localStorage 5MB quota 风险 —— 只落 IndexedDB，不上行、不进 meta。
 * 读写全 try/catch 静默降级（照搬 assistant-chat-store 的 safeStorage 模式：
 * IndexedDB 不可用时纯内存，不炸页面）。
 *
 * 应用口径：覆写 body 的 background-image（首层自定义图 + 主题渐变兜底；
 * 图解码失败时渐变仍在），清除时移除 inline 覆盖、回落 .t-xxx 类内默认图。
 * 注意必须写 body 而非 documentElement —— body 背景经 canvas 传播绘制，
 * 给 html 加背景会取消传播并把主题图顶到 body 层遮住自定义图。
 */

import type { ThemeName } from './theme'

const DB_NAME = 'aozora-theme-bg'
const STORE = 'backgrounds'

/** 内存缓存：避免每个 useTheme 实例重复读库；写/清同步更新 */
const cache = new Map<ThemeName, string | null>()

let dbPromise: Promise<IDBDatabase | null> | null = null

function getDb(): Promise<IDBDatabase | null> {
  if (!dbPromise) {
    dbPromise = new Promise(resolve => {
      try {
        if (typeof indexedDB === 'undefined') {
          resolve(null)
          return
        }
        const req = indexedDB.open(DB_NAME, 1)
        req.onupgradeneeded = () => {
          if (!req.result.objectStoreNames.contains(STORE)) {
            req.result.createObjectStore(STORE)
          }
        }
        req.onsuccess = () => resolve(req.result)
        req.onerror = () => resolve(null)
      } catch {
        resolve(null)
      }
    })
  }
  return dbPromise
}

async function readStore(theme: ThemeName): Promise<string | null> {
  const db = await getDb()
  if (!db) return null
  return new Promise(resolve => {
    try {
      const req = db.transaction(STORE, 'readonly').objectStore(STORE).get(theme)
      req.onsuccess = () => resolve(typeof req.result === 'string' ? req.result : null)
      req.onerror = () => resolve(null)
    } catch {
      resolve(null)
    }
  })
}

async function writeStore(theme: ThemeName, value: string | null): Promise<void> {
  const db = await getDb()
  if (!db) return
  await new Promise<void>(resolve => {
    try {
      const tx = db.transaction(STORE, 'readwrite')
      const store = tx.objectStore(STORE)
      if (value === null) store.delete(theme)
      else store.put(value, theme)
      tx.oncomplete = () => resolve()
      tx.onerror = () => resolve()
      tx.onabort = () => resolve()
    } catch {
      resolve()
    }
  })
}

/** 读取某主题的自定义背景（无/不可用 → null）。 */
export async function loadThemeBackground(theme: ThemeName): Promise<string | null> {
  if (cache.has(theme)) return cache.get(theme) ?? null
  const value = await readStore(theme)
  cache.set(theme, value)
  return value
}

/** 保存某主题的自定义背景（静默降级；不自动应用，由调用方 apply）。 */
export async function saveThemeBackground(theme: ThemeName, dataUri: string): Promise<void> {
  cache.set(theme, dataUri)
  await writeStore(theme, dataUri)
}

/** 清除某主题的自定义背景（静默降级；不自动应用，由调用方 apply）。 */
export async function clearThemeBackground(theme: ThemeName): Promise<void> {
  cache.set(theme, null)
  await writeStore(theme, null)
}

/** 立即应用（或清除）自定义背景到 body。 */
export function applyCustomBackground(dataUri: string | null): void {
  const body = document.body
  if (!body) return
  if (dataUri) {
    body.style.backgroundImage =
      `url("${dataUri}"), linear-gradient(180deg, var(--sky-from), var(--sky-to))`
  } else {
    body.style.removeProperty('background-image')
  }
}

/** 竞态守卫：快速切主题时只允许最后一次请求落地 */
let applySeq = 0

/** 按主题读取并应用自定义背景（useTheme 在主题变化时调用）。 */
export async function applyThemeBackground(theme: ThemeName): Promise<void> {
  const seq = ++applySeq
  const dataUri = await loadThemeBackground(theme)
  if (seq !== applySeq) return
  applyCustomBackground(dataUri)
}