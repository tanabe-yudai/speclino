/** ワークスペースを自分で作る。VITE_MOCK=1 のときは固定値を返す。 */

import { apiFetch } from './client'
import type { WorkspaceMembership } from '../types/me'

const USE_MOCK = import.meta.env.VITE_MOCK === '1'

export async function createWorkspace(slug: string, name: string | null): Promise<WorkspaceMembership> {
  if (USE_MOCK) return { slug, name, role: 'owner' }
  return apiFetch<WorkspaceMembership>('/api/workspaces', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ slug, name }),
  })
}
