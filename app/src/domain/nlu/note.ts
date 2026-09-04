/**
 * 备注抽取器 — 从文本中剥离已识别的 token 后，剩余部分作为 note。
 *
 * 策略：
 * - 移除金额、日期、收支关键词、分类关键词占位
 * - 保留剩余文本（trim 后）
 * - 空 note 使用默认
 *
 * 约束：
 * - 纯函数，不修改原文本
 */

export interface NoteResult {
  note: string
  isEmpty: boolean
}

/**
 * 从原文中抽取 note。
 *
 * @param text 原始输入
 * @param rangesToStrip 需要剥离的字符范围 [start, end][]
 * @param defaultNote 无剩余内容时的默认 note
 */
export function extractNote(
  text: string,
  rangesToStrip: Array<[number, number]>,
  defaultNote: string = ''
): NoteResult {
  if (!text || typeof text !== 'string') {
    return { note: defaultNote, isEmpty: true }
  }

  // 将字符拆成数组，标记每个字符是否被覆盖
  const chars = Array.from(text)
  const mask = new Array<boolean>(chars.length).fill(false)
  for (const [start, end] of rangesToStrip) {
    for (let i = start; i < end && i < chars.length; i++) {
      if (i >= 0) mask[i] = true
    }
  }

  // 保留未覆盖的字符
  const remaining = chars.filter((_, i) => !mask[i]).join('')
  // 清理多余空白和连接词
  const cleaned = remaining
    .replace(/[，,、\s]+/g, ' ')
    .replace(/^[的了吗呢吧啊]+/, '')
    .replace(/[的了吗呢吧啊]+$/, '')
    .trim()

  if (!cleaned) {
    return { note: defaultNote, isEmpty: true }
  }

  return { note: cleaned, isEmpty: false }
}
