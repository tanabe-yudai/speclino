import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'
import { LoadingState } from './LoadingState'

/** 未ログインなら /login へ、認証状態を確認中はローディングを出す。 */
export function RequireAuth() {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return <LoadingState message="ログイン状態を確認しています…" />
  }
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }
  return <Outlet />
}
