/** 把后端错误码翻译成后台可读文案（码见 `server/src/common/errors.py` 与各模块）。 */

import { ApiError } from '../api/client'

export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case 'invalid_credentials':
        return '用户名或密码错误'
      case 'missing_admin_token':
      case 'invalid_admin_token':
        return '登录已失效，请重新登录'
      case 'network_error':
        return error.message
      case 'query_too_short':
        return '关键词至少 2 个字符'
      case 'invalid_cursor':
        return '分页游标无效，请重新查询'
      case 'validation_error':
        return '查询条件格式不正确'
      case 'user_not_found':
        return '用户不存在'
      case 'conversation_not_found':
        return '会话不存在'
      default:
        return error.requestId
          ? `${error.message}（request_id: ${error.requestId}）`
          : error.message
    }
  }
  if (error instanceof Error) return error.message
  return '请求失败'
}
