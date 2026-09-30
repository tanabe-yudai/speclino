import type { WorkspaceMembership } from '@/types/me'

/** 今見ているワークスペース。URL の slug があればそれだけ（所属に無ければ null）、無ければ所属の先頭。 */
export function pickWorkspace(
  workspaces: WorkspaceMembership[],
  slug: string | undefined,
): WorkspaceMembership | null {
  if (slug !== undefined) return workspaces.find((w) => w.slug === slug) ?? null
  return workspaces[0] ?? null
}
