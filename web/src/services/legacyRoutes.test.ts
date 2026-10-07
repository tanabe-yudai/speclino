import { describe, expect, it } from 'vitest'
import { knowledgeListRedirect, legacyRedirect } from './legacyRoutes'

describe('knowledgeListRedirect', () => {
  it('スキル・指示書はチーム共通、仕様と不明な区分はホームへ', () => {
    expect(knowledgeListRedirect('ws', 'skills')).toBe('/w/ws?tab=shared')
    expect(knowledgeListRedirect('ws', 'instructions')).toBe('/w/ws?tab=shared')
    expect(knowledgeListRedirect('ws', 'specs')).toBe('/w/ws')
    expect(knowledgeListRedirect('ws', undefined)).toBe('/w/ws')
    expect(knowledgeListRedirect('ws', 'x')).toBe('/w/ws')
  })
})

describe('legacyRedirect', () => {
  it('旧い MCP トークンとリポジトリの画面を設定のタブへ', () => {
    expect(legacyRedirect('/workspaces/ws/mcp-tokens')).toBe('/w/ws/settings?tab=integrations')
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
