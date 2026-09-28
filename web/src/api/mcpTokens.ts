/** `/api/workspaces/:slug/mcp-tokens` のエンドポイント関数。VITE_MOCK=1 のときは fixture を返す。 */

import { apiFetch } from './client'
import type { IssuedMcpToken, McpTokenInfo } from '../types/mcpToken'

const USE_MOCK = import.meta.env.VITE_MOCK === '1'

// VITE_MOCK 用のインメモリ状態（画面確認だけが目的で、リロードすると消える）。
let mockTokens: McpTokenInfo[] = [
  {
    id: 'mock-token-1',
    name: 'エディタ（ローカル）',
    created_by: 'mock-user',
    created_at: '2026-09-20T09:00:00.000000Z',
    last_used_at: '2026-09-27T10:00:00.000000Z',
    revoked_at: null,
  },
]

export async function listMcpTokens(slug: string): Promise<McpTokenInfo[]> {
  if (USE_MOCK) return mockTokens
  return apiFetch<McpTokenInfo[]>(`/api/workspaces/${slug}/mcp-tokens`)
}

export async function issueMcpToken(slug: string, name: string): Promise<IssuedMcpToken> {
  if (USE_MOCK) {
    const issued: IssuedMcpToken = {
      id: `mock-token-${mockTokens.length + 1}`,
      name,
      created_by: 'mock-user',
      created_at: new Date().toISOString(),
      last_used_at: null,
      revoked_at: null,
      token: `slmcp_mock_${Math.random().toString(36).slice(2)}`,
    }
    mockTokens = [issued, ...mockTokens]
    return issued
  }
  return apiFetch<IssuedMcpToken>(`/api/workspaces/${slug}/mcp-tokens`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
}

export async function revokeMcpToken(slug: string, tokenId: string): Promise<void> {
  if (USE_MOCK) {
    mockTokens = mockTokens.map((t) =>
      t.id === tokenId ? { ...t, revoked_at: new Date().toISOString() } : t,
    )
    return
  }
  await apiFetch<void>(`/api/workspaces/${slug}/mcp-tokens/${tokenId}`, { method: 'DELETE' })
}
