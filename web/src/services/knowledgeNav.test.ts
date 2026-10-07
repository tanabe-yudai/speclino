import { describe, expect, it } from 'vitest'
import type { ArtifactScope, ArtifactSummary } from '@/types/knowledge'
import type { ProjectSummary } from '@/types/project'
import { backLink, findFromProject, projectArtifactRows, scopeForProject, sharedArtifactRows } from './knowledgeNav'

function art(name: string, kind: ArtifactSummary['kind'], scope: ArtifactScope | null): ArtifactSummary {
  return { artifact_id: name, workspace: 'ws', kind, name, active_revision: 1, draft_revision: null, scope, created_at: '2026-10-01T00:00:00Z' }
}
const ws: ArtifactScope = { workspace: true, projects: [] }
const only = (...projects: string[]): ArtifactScope => ({ workspace: false, projects })

const project = { id: 'p1', repo_full_name: 'org/shop' } as ProjectSummary

describe('projectArtifactRows', () => {
  const all = [
    art('b-skill', 'skill', ws),
    art('a-skill', 'skill', only('p1', 'p2')),
    art('other', 'skill', only('p2')),
    art('no-scope', 'skill', null),
    art('inst', 'instructions', only('p1')),
  ]
  it('種類で絞り、そのプロジェクトのものとチーム共通を名前順に並べる', () => {
    const rows = projectArtifactRows(all, 'skill', 'p1')
    expect(rows.map((r) => [r.artifact.name, r.workspaceWide])).toEqual([
      ['a-skill', false],
      ['b-skill', true],
    ])
  })
})

describe('sharedArtifactRows', () => {
  it('ワークスペース全体のスコープだけ', () => {
    const rows = sharedArtifactRows([art('a', 'skill', only('p1')), art('b', 'skill', ws), art('c', 'instructions', ws)], 'skill')
    expect(rows.map((r) => r.artifact.name)).toEqual(['b'])
    expect(rows[0].workspaceWide).toBe(true)
  })
})

describe('scopeForProject', () => {
  it('ワークスペース全体にせず、そのプロジェクトだけ', () => {
    expect(scopeForProject('p1')).toEqual({ workspace: false, projects: ['p1'] })
  })
})

describe('findFromProject / backLink', () => {
  it('from が一覧にあればそのプロジェクト、無ければ null', () => {
    expect(findFromProject([project], 'org/shop')).toBe(project)
    expect(findFromProject([project], 'org/none')).toBeNull()
    expect(findFromProject(null, 'org/shop')).toBeNull()
    expect(findFromProject([project], null)).toBeNull()
  })
  it('from のプロジェクトがあれば、その種類のタブへ戻る', () => {
    expect(backLink('ws', 'skill', project)).toEqual({ to: '/w/ws/p/org/shop/skills', label: '← shop のスキル' })
    expect(backLink('ws', 'spec', project)).toEqual({ to: '/w/ws/p/org/shop/specs', label: '← shop の仕様' })
  })
  it('無ければ、スキル・指示書はチーム共通、仕様はホームへ', () => {
    expect(backLink('ws', 'skill', null).to).toBe('/w/ws?tab=shared')
    expect(backLink('ws', 'instructions', null).to).toBe('/w/ws?tab=shared')
    expect(backLink('ws', 'spec', null).to).toBe('/w/ws')
  })
})
