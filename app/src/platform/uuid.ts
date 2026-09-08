/**
 * newUuid — RFC 4122 v4 UUID 生成。
 *
 * 服务端 sync push / ai.execute 要求 entity_id / mutation_id 为 UUID
 * （server/src/modules/sync/schemas.py），本地生成的所有实体 id 与
 * mutationId 必须与之一致，否则同步与聊天记账永久 422。
 *
 * WebView 安全上下文（appassets https origin）下 crypto.randomUUID 可用；
 * 保留 Math.random 回退以兼容 jsdom 等测试环境。
 */
export function newUuid(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}
