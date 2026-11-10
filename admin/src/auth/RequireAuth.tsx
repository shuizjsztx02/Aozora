import { Navigate, Outlet, useLocation } from 'react-router'
import { useAuth } from './AuthContext'

/** 未登录一律回登录页；登录后由登录页跳回 `/users`。 */
export function RequireAuth() {
  const { session } = useAuth()
  const location = useLocation()

  if (!session) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  return <Outlet />
}
