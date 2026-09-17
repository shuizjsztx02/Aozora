/**
 * T16-5（REQ-61 / 决策 #6）：四主题自定义背景存储与应用测试。
 *
 * jsdom 无 IndexedDB —— 存储路径天然覆盖"不可用时静默降级"；
 * 应用路径断言 body 的 inline 覆盖（必须写 body 而非 html：body 背景
 * 经 canvas 传播绘制，给 html 加背景会遮住自定义图）。
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  applyCustomBackground,
  loadThemeBackground,
  saveThemeBackground,
  clearThemeBackground,
  applyThemeBackground,
} from '../../design/theme-backgrounds'

const URI = 'data:image/jpeg;base64,QUJD'

describe('T16-5（REQ-61）：自定义背景存储与应用', () => {
  beforeEach(() => {
    document.body.removeAttribute('style')
    document.documentElement.removeAttribute('style')
  })

  it('applyCustomBackground 首层自定义图 + 主题渐变兜底，且只写 body', () => {
    applyCustomBackground(URI)
    expect(document.body.style.backgroundImage).toContain(URI)
    expect(document.body.style.backgroundImage).toContain('linear-gradient')
    expect(document.documentElement.style.backgroundImage).toBe('')
  })

  it('applyCustomBackground(null) 移除 inline 覆盖 —— 回落类内默认图', () => {
    applyCustomBackground(URI)
    applyCustomBackground(null)
    expect(document.body.style.backgroundImage).toBe('')
  })

  it('IndexedDB 不可用时 save/clear 静默降级不抛', async () => {
    await expect(saveThemeBackground('day', URI)).resolves.toBeUndefined()
    await expect(clearThemeBackground('day')).resolves.toBeUndefined()
  })

  it('保存后 load 命中缓存返回 data URI；清除后返回 null', async () => {
    await saveThemeBackground('dawn', URI)
    expect(await loadThemeBackground('dawn')).toBe(URI)
    await clearThemeBackground('dawn')
    expect(await loadThemeBackground('dawn')).toBeNull()
  })

  it('applyThemeBackground 对无自定义的主题清掉 inline 覆盖', async () => {
    applyCustomBackground(URI)
    await applyThemeBackground('night')
    expect(document.body.style.backgroundImage).toBe('')
  })
})