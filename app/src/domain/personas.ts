/**
 * 聊天角色解析（第十一轮 T11-7 / REQ-28 / 决策 #8）。
 *
 * 当前角色 = meta.active_persona_id；缺失/指向已删角色 → 回退「小贝」，
 * 连小贝也没有（用户删除） → 取列表第一条；空列表 → null。
 * 单一入口：AssistantPage 与 PersonasPage 都走此函数，禁止各自手写 find。
 */

/** 内置默认角色名（Repository.SEED_PERSONAS 首位）——回退锚点。 */
export const DEFAULT_PERSONA_NAME = '小贝'

export function resolveActivePersona<T extends { id: string; name: string }>(
  personas: T[],
  activeId: string | null | undefined
): T | null {
  if (activeId) {
    const hit = personas.find(p => p.id === activeId)
    if (hit) return hit
  }
  return personas.find(p => p.name === DEFAULT_PERSONA_NAME) ?? personas[0] ?? null
}
