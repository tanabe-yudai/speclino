/** `/api/workspaces/:slug/mcp-tokens` の型（S54）。 */

export interface McpTokenInfo {
  id: string
  name: string
  created_by: string
  created_at: string
  last_used_at: string | null
  revoked_at: string | null
}

/** 発行直後の応答だけに含まれる（平文はこの1回しか見えない）。 */
export interface IssuedMcpToken extends McpTokenInfo {
  token: string
}
