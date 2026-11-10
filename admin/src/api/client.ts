/**
 * admin API 客户端：基址、鉴权头、错误归一化。
 *
 * 契约见 `docs/architecture/admin-console.md` §5；错误体为
 * `{ code, message, request_id }`（`server/src/common/errors.py`）。
 */

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId: string | null

  constructor(status: number, code: string, message: string, requestId: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.requestId = requestId
  }
}

type QueryValue = string | number | boolean | null | undefined

const DEV_API_BASE = 'http://127.0.0.1:8011/api/v1'
const PROD_API_BASE = '/aozora-api/api/v1'

/** 生产由 nginx 同源反代 `/aozora-api/`（infra/deploy/prod/nginx/DEPLOYED.md）。 */
export const API_BASE = (
  import.meta.env.VITE_API_BASE?.trim() ||
  (import.meta.env.DEV ? DEV_API_BASE : PROD_API_BASE)
).replace(/\/+$/, '')

let authToken: string | null = null
let unauthorizedHandler: (() => void) | null = null

/** 由 AuthProvider 维护——token 只存内存 + sessionStorage，绝不写进代码。 */
export function setAuthToken(token: string | null): void {
  authToken = token
}

/** 401 统一处理（清会话回登录页）。登录请求自身不触发。 */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  query?: Record<string, QueryValue>
  body?: unknown
  /** 登录接口用：401 表示凭据错误，不应触发全局登出。 */
  skipAuthRedirect?: boolean
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const url = new URL(API_BASE + path, window.location.origin)
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === null || value === undefined || value === '') continue
    url.searchParams.set(key, String(value))
  }
  return url.toString()
}

async function parseError(response: Response): Promise<ApiError> {
  let code = `http_${response.status}`
  let message = `请求失败（HTTP ${response.status}）`
  let requestId: string | null = null
  try {
    const payload = (await response.json()) as {
      code?: string
      message?: string
      request_id?: string | null
    }
    if (payload.code) code = payload.code
    if (payload.message) message = payload.message
    requestId = payload.request_id ?? null
  } catch {
    // 非 JSON 错误体（网关 502 页面等）——保留默认文案。
  }
  return new ApiError(response.status, code, message, requestId)
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(buildUrl(path, options.query), {
      method: options.method ?? 'GET',
      headers: {
        Accept: 'application/json',
        ...(options.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
      },
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    })
  } catch {
    throw new ApiError(0, 'network_error', '无法连接服务端，请检查网络或 API 地址')
  }

  if (!response.ok) {
    const error = await parseError(response)
    if (response.status === 401 && !options.skipAuthRedirect) {
      unauthorizedHandler?.()
    }
    throw error
  }

  return (await response.json()) as T
}
