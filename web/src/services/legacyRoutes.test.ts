import { describe, expect, it } from 'vitest'
import { legacyRedirect } from './legacyRoutes'

describe('legacyRedirect', () => {
  it('旧い MCP トークンとリポジトリの画面を設定のタブへ', () => {
    expect(legacyRedirect('/workspaces/ws/mcp-tokens')).toBe('/w/ws/settings/agents')
    expect(legacyRedirect('/workspaces/ws/repositories')).toBe('/w/ws/settings/repositories')
  })
  it('ホームの旧いタブ: チーム共通はスキル、チーム規約は規約、利用状況は観測、プロジェクトはホーム', () => {
    expect(legacyRedirect('/w/ws', '?tab=shared')).toBe('/w/ws/skills')
    expect(legacyRedirect('/w/ws', '?tab=policies')).toBe('/w/ws/policies')
    expect(legacyRedirect('/w/ws', '?tab=usage')).toBe('/w/ws/observability')
    expect(legacyRedirect('/w/ws', '?tab=projects')).toBe('/w/ws')
    expect(legacyRedirect('/w/ws', '?tab=x')).toBe('/w/ws')
    expect(legacyRedirect('/w/ws')).toBeNull()
  })
  it('旧い統合一覧は種類の画面へ（種類が無ければ仕様、リポジトリで絞れるのは仕様だけ）', () => {
    expect(legacyRedirect('/w/ws/knowledge')).toBe('/w/ws/specs')
    expect(legacyRedirect('/w/ws/knowledge', '?kind=spec&project=o/r&status=pending')).toBe('/w/ws/specs?project=o%2Fr&status=pending')
    expect(legacyRedirect('/w/ws/knowledge', '?kind=skill&project=o/r')).toBe('/w/ws/skills')
    expect(legacyRedirect('/w/ws/knowledge', '?kind=instructions&status=active')).toBe('/w/ws/instructions?status=active')
    expect(legacyRedirect('/w/ws/knowledge', '?kind=policy&status=pending')).toBe('/w/ws/policies')
    expect(legacyRedirect('/w/ws/knowledge', '?status=pending')).toBe('/w/ws/specs?status=pending')
  })
  it('取り下げ済みで絞った旧い一覧は設定の「取り下げ済みの知識」へ', () => {
    expect(legacyRedirect('/w/ws/knowledge', '?kind=skill&status=retired')).toBe('/w/ws/settings/retired')
  })
  it('種類ごとの旧い一覧と旧い規約の画面は種類の画面へ', () => {
    expect(legacyRedirect('/w/ws/knowledge/specs')).toBe('/w/ws/specs')
    expect(legacyRedirect('/w/ws/knowledge/skills')).toBe('/w/ws/skills')
    expect(legacyRedirect('/w/ws/knowledge/instructions')).toBe('/w/ws/instructions')
    expect(legacyRedirect('/w/ws/knowledge/policies')).toBe('/w/ws/policies')
    expect(legacyRedirect('/w/ws/knowledge/x')).toBe('/w/ws/specs')
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
  it('プロジェクトの旧い知識のタブは仕様のタブへ、スキル・指示書・規約はワークスペースの種類の画面へ', () => {
    expect(legacyRedirect('/w/ws/p/org/shop/knowledge')).toBe('/w/ws/p/org/shop/specs')
    expect(legacyRedirect('/w/ws/p/org/shop/knowledge', '?kind=spec&status=pending')).toBe('/w/ws/p/org/shop/specs?status=pending')
    expect(legacyRedirect('/w/ws/p/org/shop/knowledge', '?kind=skill')).toBe('/w/ws/skills')
    expect(legacyRedirect('/w/ws/p/org/shop/knowledge', '?kind=policy')).toBe('/w/ws/policies')
    expect(legacyRedirect('/w/ws/p/org/shop/skills')).toBe('/w/ws/skills')
    expect(legacyRedirect('/w/ws/p/org/shop/instructions')).toBe('/w/ws/instructions')
    expect(legacyRedirect('/w/ws/p/org/shop/policies')).toBe('/w/ws/policies')
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
  it.each([
    '/w/ws/p/org/shop/specs',
    '/w/ws/p/org/shop/runs',
    '/w/ws/settings/members',
    '/w/ws/settings/projects',
    '/w/ws/settings/retired',
    '/w/ws/specs',
    '/w/ws/policies',
  ])('%s', (p) => {
    expect(legacyRedirect(p)).toBeNull()
  })
})
