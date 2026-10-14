import { Navigate } from 'react-router-dom'
import { LoadingState } from '@/components/LoadingState'
import { useMe } from '@/hooks/useMe'
import { landingPath } from '@/services/workspaces'

/** `/app`: 所属が 1 つならそのホームへ、0 か 2 つ以上なら自分のワークスペースの画面へ。 */
export function HomeRedirect() {
  const { me } = useMe()
  if (!me) return <LoadingState />
  return <Navigate to={landingPath(me.workspaces)} replace />
}
