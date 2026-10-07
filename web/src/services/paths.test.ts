import { describe, expect, it } from 'vitest'
import { paths } from './paths'

describe('paths', () => {
  it('プロジェクトは owner/repo をパスの 2 段にし、概要以外はタブを足す', () => {
    expect(paths.project('ws', 'example-org/example-repo')).toBe('/w/ws/p/example-org/example-repo')
    expect(paths.project('ws', 'example-org/example-repo', 'overview')).toBe('/w/ws/p/example-org/example-repo')
    expect(paths.project('ws', 'example-org/example-repo', 'skills')).toBe('/w/ws/p/example-org/example-repo/skills')
  })
  it('設定のタブはクエリで指定する', () => {
    expect(paths.settings('ws')).toBe('/w/ws/settings')
    expect(paths.settings('ws', 'integrations')).toBe('/w/ws/settings?tab=integrations')
  })
  it('slug と名前はエンコードする', () => {
    expect(paths.policies('a b')).toBe('/w/a%20b/policies')
  })
  it('ワークスペースのホームはタブをクエリで指定する（プロジェクトは素のパス）', () => {
    expect(paths.workspace('acme team')).toBe('/w/acme%20team')
    expect(paths.workspace('ws', 'projects')).toBe('/w/ws')
    expect(paths.workspace('ws', 'shared')).toBe('/w/ws?tab=shared')
    expect(paths.workspace('ws', 'usage')).toBe('/w/ws?tab=usage')
  })
  it('エディタは種類ごとのパスにする', () => {
    expect(paths.knowledgeNew('ws', 'instructions')).toBe('/w/ws/knowledge/instructions/new')
    expect(paths.knowledgeItem('ws', 'spec', 'a/b')).toBe('/w/ws/knowledge/specs/a%2Fb')
    expect(paths.knowledgeRevisions('ws', 'spec', 'id1')).toBe('/w/ws/knowledge/specs/id1/revisions')
  })
  it('from（戻り先のプロジェクト）と chat をクエリで引き継ぐ', () => {
    expect(paths.knowledgeNew('ws', 'skill', 'o/r')).toBe('/w/ws/knowledge/skills/new?from=o%2Fr')
    expect(paths.knowledgeNewWithChat('ws', 'skill')).toBe('/w/ws/knowledge/skills/new?chat=new')
    expect(paths.knowledgeNewWithChat('ws', 'skill', 'o/r')).toBe('/w/ws/knowledge/skills/new?chat=new&from=o%2Fr')
    expect(paths.knowledgeItem('ws', 'spec', 'id1', { chat: 'c1', from: 'o/r' })).toBe(
      '/w/ws/knowledge/specs/id1?chat=c1&from=o%2Fr',
    )
    expect(paths.knowledgeItem('ws', 'spec', 'id1', { from: null })).toBe('/w/ws/knowledge/specs/id1')
    expect(paths.knowledgeRevisions('ws', 'spec', 'id1', 'o/r')).toBe('/w/ws/knowledge/specs/id1/revisions?from=o%2Fr')
  })
})
