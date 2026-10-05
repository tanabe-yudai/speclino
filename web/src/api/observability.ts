/** `/api/workspaces/:slug/observability`（S71）のエンドポイント関数。 */

import { apiFetch } from './client'
import type { WorkspaceHealth } from '../types/observability'

export function getObservability(slug: string): Promise<WorkspaceHealth> {
  return apiFetch<WorkspaceHealth>(`/api/workspaces/${encodeURIComponent(slug)}/observability`)
}
