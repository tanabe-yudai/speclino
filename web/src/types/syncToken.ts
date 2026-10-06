/** `/api/workspaces/:slug/sync-tokens` の型（S68）。 */

export interface SyncTokenInfo {
  id: string
  project_id: string
  repo_full_name: string
  name: string
  created_by: string
  created_at: string
  last_used_at: string | null
  revoked_at: string | null
}

/** 発行直後の応答だけに含まれる（平文はこの 1 回しか見えない）。 */
export interface IssuedSyncToken extends SyncTokenInfo {
  token: string
}
