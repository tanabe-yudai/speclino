import { describe, expect, it } from 'vitest'
import { paths } from './paths'

describe('paths', () => {
  it('プロジェクトは owner/repo をパスの 2 段にする', () => {
    expect(paths.project('ws', 'example-org/example-repo')).toBe('/w/ws/p/example-org/example-repo')
  })
  it('設定のタブはクエリで指定する', () => {
    expect(paths.settings('ws')).toBe('/w/ws/settings')
    expect(paths.settings('ws', 'tokens')).toBe('/w/ws/settings?tab=tokens')
  })
  it('slug と名前はエンコードする', () => {
    expect(paths.policies('a b')).toBe('/w/a%20b/policies')
  })
  it('ワークスペースのホーム', () => {
    expect(paths.workspace('acme team')).toBe('/w/acme%20team')
  })
  it('知識は種類ごとのパスにする', () => {
    expect(paths.knowledgeRoot('ws')).toBe('/w/ws/knowledge')
    expect(paths.knowledge('ws')).toBe('/w/ws/knowledge/specs')
    expect(paths.knowledge('ws', 'skill')).toBe('/w/ws/knowledge/skills')
    expect(paths.knowledgeNew('ws', 'instructions')).toBe('/w/ws/knowledge/instructions/new')
    expect(paths.knowledgeItem('ws', 'spec', 'a/b')).toBe('/w/ws/knowledge/specs/a%2Fb')
    expect(paths.knowledgeRevisions('ws', 'spec', 'id1')).toBe('/w/ws/knowledge/specs/id1/revisions')
  })
})
