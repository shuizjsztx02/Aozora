import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { setAuthToken, setUnauthorizedHandler } from '../api/client'
import type { AdminTokenResponse } from '../api/types'

/** 管理员会话：token 存内存 + sessionStorage（可容错，不落 localStorage）。 */
export interface AdminSession {
  accessToken: string
  adminId: string
  username: string
  /** epoch 毫秒；到点后视为失效，回登录页。 */
  expiresAt: number
}

const STORAGE_KEY = 'aozora.admin.session'

function readStoredSession(): AdminSession | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<AdminSession>
    if (!parsed.accessToken || !parsed.username || typeof parsed.expiresAt !== 'number') {
      return null
    }
    if (parsed.expiresAt <= Date.now()) {
      sessionStorage.removeItem(STORAGE_KEY)
      return null
    }
    return {
      accessToken: parsed.accessToken,
      adminId: parsed.adminId ?? '',
      username: parsed.username,
      expiresAt: parsed.expiresAt,
    }
  } catch {
    // 无痕模式 / 被禁用的存储：退化为纯内存会话。
    return null
  }
}

interface AuthContextValue {
  session: AdminSession | null
  signIn: (token: AdminTokenResponse) => void
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AdminSession | null>(() => {
    const stored = readStoredSession()
    // 查询可能在首帧之后立即发出，token 必须在渲染期就装好。
    setAuthToken(stored?.accessToken ?? null)
    return stored
  })

  const logout = useCallback(() => {
    setAuthToken(null)
    setSession(null)
    try {
      sessionStorage.removeItem(STORAGE_KEY)
    } catch {
      // 存储不可用：内存态已清空。
    }
  }, [])

  const signIn = useCallback((token: AdminTokenResponse) => {
    const next: AdminSession = {
      accessToken: token.access_token,
      adminId: token.admin_id,
      username: token.username,
      expiresAt: Date.now() + token.expires_in * 1000,
    }
    setAuthToken(next.accessToken)
    setSession(next)
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch {
      // 存储不可用：本次会话保持内存态。
    }
  }, [])

  // 任意读端点返回 401（token 过期 / 管理员被停用）→ 清会话回登录页。
  useEffect(() => {
    setUnauthorizedHandler(logout)
    return () => setUnauthorizedHandler(null)
  }, [logout])

  const value = useMemo<AuthContextValue>(
    () => ({ session, signIn, logout }),
    [session, signIn, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>')
  return value
}
