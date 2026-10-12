import { describe, expect, it } from 'vitest'
import { legacyRedirect } from './legacyRoutes'

describe('legacyRedirect', () => {
  it('旧い MCP トークンとリポジトリの画面を設定のタブへ', () => {
    expect(legacyRedirect('/workspaces/ws/mcp-tokens')).toBe('/w/ws/settings/agents')
    expect(legacyRedirect('/workspaces/ws/repositories')).toBe('/w/ws/settings/repositories')
  })
  it('ホームの旧いタブ: チーム共通は知識、チーム規約は規約、利用状況は観測、プロジェクトはホーム', () => {
    expect(legacyRedirect('/w/ws', '?tab=shared')).toBe('/w/ws/knowledge')
    expect(legacyRedirect('/w/ws', '?tab=policies')).toBe('/w/ws/knowledge/policies')
    expect(legacyRedirect('/w/ws', '?tab=usage')).toBe('/w/ws/observability')
    expect(legacyRedirect('/w/ws', '?tab=projects')).toBe('/w/ws')
    expect(legacyRedirect('/w/ws', '?tab=x')).toBe('/w/ws')
    expect(legacyRedirect('/w/ws')).toBeNull()
  })
  it('旧い規約の画面と種類ごとの一覧', () => {
    expect(legacyRedirect('/w/ws/policies')).toBe('/w/ws/knowledge/policies')
    expect(legacyRedirect('/w/ws/knowledge/specs')).toBe('/w/ws/knowledge?kind=spec')
    expect(legacyRedirect('/w/ws/knowledge/skills')).toBe('/w/ws/knowledge?kind=skill')
    expect(legacyRedirect('/w/ws/knowledge/instructions')).toBe('/w/ws/knowledge?kind=instructions')
    expect(legacyRedirect('/w/ws/knowledge/x')).toBe('/w/ws/knowledge')
    expect(legacyRedirect('/w/ws/knowledge/policies')).toBeNull()
  })
  it('設定の旧い ?tab= をパスのタブへ', () => {
    expect(legacyRedirect('/w/ws/settings', '?tab=integrations')).toBe('/w/ws/settings/agents')
    expect(legacyRedirect('/w/ws/settings', '?tab=sync')).toBe('/w/ws/settings/agents')
    expect(legacyRedirect('/w/ws/settings', '?tab=tokens')).toBe('/w/ws/settings/agents')
    expect(legacyRedirect('/w/ws/settings', '?tab=project')).toBe('/w/ws/settings/projects')
    expect(legacyRedirect('/w/ws/settings', '?tab=repositories')).toBe('/w/ws/settings/repositories')
    expect(legacyRedirect('/w/ws/settings', '?tab=nope')).toBe('/w/ws/settings')
    expect(legacyRedirect('/w/ws/settings')).toBeNull()
    expect(legacyRedirect('/w/ws/settings/integrations')).toBe('/w/ws/settings/agents')
    expect(legacyRedirect('/w/ws/settings/agents')).toBeNull()
  })
  it('プロジェクトの旧いタブは知識のタブを種類で絞ったものへ、知らないタブは概要へ', () => {
    expect(legacyRedirect('/w/ws/p/org/shop/specs')).toBe('/w/ws/p/org/shop/knowledge?kind=spec')
    expect(legacyRedirect('/w/ws/p/org/shop/policies')).toBe('/w/ws/p/org/shop/knowledge?kind=policy')
    expect(legacyRedirect('/w/ws/p/org/shop/nope')).toBe('/w/ws/p/org/shop')
  })
  it('関係ないパスは null', () => {
    expect(legacyRedirect('/runs/abc')).toBeNull()
    expect(legacyRedirect('/workspaces/ws/other')).toBeNull()
  })
  it('壊れた % を含むパスは例外にせず null', () => {
    expect(legacyRedirect('/workspaces/%E0/repositories')).toBeNull()
  })
})

describe('legacyRedirect（新しいパスは転送しない）', () => {
  it.each(['/w/ws/p/org/shop/knowledge', '/w/ws/p/org/shop/runs', '/w/ws/settings/members', '/w/ws/settings/projects'])('%s', (p) => {
    expect(legacyRedirect(p)).toBeNull()
  })
})
