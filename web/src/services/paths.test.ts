import { describe, expect, it } from 'vitest'
import { paths } from './paths'

describe('paths', () => {
  it('プロジェクトは owner/repo をパスの 2 段にし、概要以外はタブを足す', () => {
    expect(paths.project('ws', 'example-org/example-repo')).toBe('/w/ws/p/example-org/example-repo')
    expect(paths.project('ws', 'example-org/example-repo', 'overview')).toBe('/w/ws/p/example-org/example-repo')
    expect(paths.project('ws', 'example-org/example-repo', 'specs', { status: 'pending' })).toBe(
      '/w/ws/p/example-org/example-repo/specs?status=pending',
    )
    expect(paths.project('ws', 'o/r', 'runs', { gate: 'fail' })).toBe('/w/ws/p/o/r/runs?gate=fail')
  })
  it('設定のタブはパスで指定する', () => {
    expect(paths.settings('ws')).toBe('/w/ws/settings')
    expect(paths.settings('ws', 'agents')).toBe('/w/ws/settings/agents')
    expect(paths.settings('ws', 'retired')).toBe('/w/ws/settings/retired')
  })
  it('slug と名前はエンコードする', () => {
    expect(paths.policies('a b')).toBe('/w/a%20b/policies')
    expect(paths.workspace('acme team')).toBe('/w/acme%20team')
  })
  it('種類の画面と検査の一覧は絞り込みをクエリにする（リポジトリで絞れるのは仕様だけ、規約は絞らない）', () => {
    expect(paths.knowledgeList('ws', 'spec')).toBe('/w/ws/specs')
    expect(paths.knowledgeList('ws', 'spec', { project: 'o/r', status: 'pending' })).toBe('/w/ws/specs?project=o%2Fr&status=pending')
    expect(paths.knowledgeList('ws', 'skill', { project: 'o/r', status: 'active' })).toBe('/w/ws/skills?status=active')
    expect(paths.knowledgeList('ws', 'instructions')).toBe('/w/ws/instructions')
    expect(paths.knowledgeList('ws', 'policy', { status: 'pending' })).toBe('/w/ws/policies')
    expect(paths.runs('ws', { gate: 'fail', project: 'o/r' })).toBe('/w/ws/runs?gate=fail&project=o%2Fr')
  })
  it('検査の詳細はワークスペースの下。分からなければ旧いパス', () => {
    expect(paths.run('ws', 'r1')).toBe('/w/ws/runs/r1')
    expect(paths.run(null, 'r1')).toBe('/runs/r1')
  })
  it('エディタは種類ごとのパスにする', () => {
    expect(paths.knowledgeNew('ws', 'instructions')).toBe('/w/ws/knowledge/instructions/new')
    expect(paths.knowledgeItem('ws', 'spec', 'a/b')).toBe('/w/ws/knowledge/specs/a%2Fb')
    expect(paths.knowledgeRevisions('ws', 'spec', 'id1')).toBe('/w/ws/knowledge/specs/id1/revisions')
  })
  it('from（戻り先のプロジェクト）と chat をクエリで引き継ぐ', () => {
    expect(paths.knowledgeNew('ws', 'skill', 'o/r')).toBe('/w/ws/knowledge/skills/new?from=o%2Fr')
    expect(paths.knowledgeItem('ws', 'spec', 'id1', { chat: 'c1', from: 'o/r' })).toBe(
      '/w/ws/knowledge/specs/id1?chat=c1&from=o%2Fr',
    )
    expect(paths.knowledgeItem('ws', 'spec', 'id1', { from: null })).toBe('/w/ws/knowledge/specs/id1')
    expect(paths.knowledgeRevisions('ws', 'spec', 'id1', 'o/r')).toBe('/w/ws/knowledge/specs/id1/revisions?from=o%2Fr')
  })
})
