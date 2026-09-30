/** MCP トークンの状態表示ロジック（S54）。コンポーネントには埋め込まない。 */

import type { McpTokenInfo } from '../types/mcpToken'

export type McpTokenStatus = 'active' | 'revoked'

export function mcpTokenStatus(info: McpTokenInfo): McpTokenStatus {
  return info.revoked_at ? 'revoked' : 'active'
}

export const MCP_TOKEN_STATUS_LABELS: Record<McpTokenStatus, string> = {
  active: '有効',
  revoked: '失効済み',
}

