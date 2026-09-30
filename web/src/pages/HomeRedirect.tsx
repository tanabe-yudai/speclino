import { Navigate } from 'react-router-dom'
import { LoadingState } from '@/components/LoadingState'
import { useMe } from '@/hooks/useMe'
import { paths } from '@/services/paths'
import { Home } from './Home'

/** `/app`: 所属の先頭のワークスペースのホームへ。所属が無ければその場で uid を出す（Home が出す）。 */
export function HomeRedirect() {
  const { me } = useMe()
  if (!me) return <LoadingState />
  const first = me.workspaces[0]
  return first ? <Navigate to={paths.workspace(first.slug)} replace /> : <Home />
}
