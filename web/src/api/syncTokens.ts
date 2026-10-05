/** `/api/workspaces/:slug/sync-tokens`（S68）のエンドポイント関数。 */

import { apiFetch } from './client'
import type { IssuedSyncToken, SyncTokenInfo } from '../types/syncToken'

const base = (slug: string) => `/api/workspaces/${encodeURIComponent(slug)}/sync-tokens`

export function listSyncTokens(slug: string): Promise<SyncTokenInfo[]> {
  return apiFetch<SyncTokenInfo[]>(base(slug))
}

export function issueSyncToken(slug: string, name: string, projectId: string): Promise<IssuedSyncToken> {
  return apiFetch<IssuedSyncToken>(base(slug), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, project_id: projectId }),
  })
}

export async function revokeSyncToken(slug: string, tokenId: string): Promise<void> {
  await apiFetch<void>(`${base(slug)}/${encodeURIComponent(tokenId)}`, { method: 'DELETE' })
}
