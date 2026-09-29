import { useParams } from 'react-router-dom'
import type { WorkspaceMembership } from '@/types/me'
import { useMe } from './useMe'

/** 今見ているワークスペース。URL の :slug を優先し、無ければ所属の先頭。所属が無ければ null。 */
export function useCurrentWorkspace(): WorkspaceMembership | null {
  const { slug } = useParams<{ slug?: string }>()
  const { me } = useMe()
  const workspaces = me?.workspaces ?? []
  return workspaces.find((w) => w.slug === slug) ?? workspaces[0] ?? null
}
