import { describe, expect, it } from 'vitest'
import { legacyRedirect } from './legacyRoutes'

describe('legacyRedirect', () => {
  it('旧い MCP トークンとリポジトリの画面を設定のタブへ', () => {
    expect(legacyRedirect('/workspaces/ws/mcp-tokens')).toBe('/w/ws/settings?tab=tokens')
    expect(legacyRedirect('/workspaces/ws/repositories')).toBe('/w/ws/settings?tab=repositories')
  })
  it('関係ないパスは null', () => {
    expect(legacyRedirect('/runs/abc')).toBeNull()
    expect(legacyRedirect('/workspaces/ws/other')).toBeNull()
  })
  it('壊れた % を含むパスは例外にせず null', () => {
    expect(legacyRedirect('/workspaces/%E0/repositories')).toBeNull()
  })
})
