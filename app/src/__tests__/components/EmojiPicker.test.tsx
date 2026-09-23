/**
 * EmojiPicker 宫格完整性（REQ-68 / T18-4 ④）。
 *
 * 宫格自第八轮 T8 起就有 4 个坏格子（索引 2/43/47 是空串、索引 42 是孤立的
 * 变体选择符 U+FE0F），渲染成空白按钮——本轮清理，本组钉住「无空白格子」。
 */
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { EmojiPicker, EMOJI } from '../../components/EmojiPicker'

describe('EmojiPicker 宫格无空白按钮（REQ-68）', () => {
  it('EMOJI 无空串、无孤立变体选择符，且按 8 列宫格对齐', () => {
    expect(EMOJI.length % 8).toBe(0)
    for (const e of EMOJI) {
      expect(e.trim(), `空格子：${JSON.stringify(e)}`).not.toBe('')
      // U+FE0F 单独出现时 trim 不掉，会渲染成空白按钮
      expect(e.replace(/️/g, '').trim(), `孤立变体选择符：${JSON.stringify(e)}`).not.toBe('')
    }
  })

  it('每个渲染出的格子都有可见文本', () => {
    const { getAllByRole } = render(<EmojiPicker selected="" onPick={() => {}} />)
    const cells = getAllByRole('option')
    expect(cells).toHaveLength(EMOJI.length)
    for (const cell of cells) {
      expect((cell.textContent ?? '').trim()).not.toBe('')
    }
  })
})