import { describe, expect, it } from 'vitest'
import { mcpEnvLines, mcpUrl, syncCommand } from './agentConnection'

describe('agentConnection', () => {
  it('MCP の接続先は同じオリジンの /mcp（末尾の / は外す）', () => {
    expect(mcpUrl('https://speclino.example.com/')).toBe('https://speclino.example.com/mcp')
    expect(mcpEnvLines('https://speclino.example.com')).toContain('SPECLINO_MCP_URL=https://speclino.example.com/mcp')
  })
  it('sync の手順はトークンを環境変数で渡し、URL を埋める', () => {
    const cmd = syncCommand('https://speclino.example.com')
    expect(cmd).toContain('export SPECLINO_SYNC_TOKEN=slsync_...')
    expect(cmd).toContain('speclino sync --url https://speclino.example.com --repo .')
  })
})
