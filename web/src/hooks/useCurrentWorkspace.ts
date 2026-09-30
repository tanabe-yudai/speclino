import { useParams } from 'react-router-dom'
import { pickWorkspace } from '@/services/workspaces'
import type { WorkspaceMembership } from '@/types/me'
import { useMe } from './useMe'

/** 今見ているワークスペース（services/workspaces.ts の pickWorkspace）。 */
export function useCurrentWorkspace(): WorkspaceMembership | null {
  const { slug } = useParams<{ slug?: string }>()
  const { me } = useMe()
  return pickWorkspace(me?.workspaces ?? [], slug)
}
