import { describe, expect, it } from 'vitest'
import type { ArtifactScope, ArtifactSummary } from '@/types/knowledge'
import type { KnowledgeFilter } from '@/types/navigation'
import type { ProjectSummary } from '@/types/project'
import {
  artifactStates,
  findFromProject,
  knowledgeCounts,
  knowledgeRows,
  parseKnowledgeFilter,
  pendingArtifacts,
  policyRowData,
  policyStates,
  scopeForProject,
  showPolicyRow,
} from './knowledgeNav'

function art(
  name: string,
  kind: ArtifactSummary['kind'],
  scope: ArtifactScope | null,
  revs: { active?: number | null; draft?: number | null } = {},
): ArtifactSummary {
  return {
    artifact_id: name,
    workspace: 'ws',
    kind,
    name,
    active_revision: revs.active === undefined ? 1 : revs.active,
    draft_revision: revs.draft ?? null,
    scope,
    created_at: '2026-10-01T00:00:00Z',
  }
}
const ws: ArtifactScope = { workspace: true, projects: [] }
const only = (...projects: string[]): ArtifactScope => ({ workspace: false, projects })
const project = { id: 'p1', repo_full_name: 'org/shop' } as ProjectSummary
const none: KnowledgeFilter = { kind: null, project: null, status: null }

describe('parseKnowledgeFilter', () => {
  it('知っている値だけを拾う', () => {
    expect(parseKnowledgeFilter(new URLSearchParams('kind=spec&project=org/shop&status=pending'))).toEqual({
      kind: 'spec',
      project: 'org/shop',
      status: 'pending',
    })
    expect(parseKnowledgeFilter(new URLSearchParams('kind=x&status=y'))).toEqual(none)
  })
})

describe('artifactStates / policyStates', () => {
  it('有効な改訂が無ければ未承認、下書きがあれば承認待ちも', () => {
    expect(artifactStates(art('a', 'spec', ws))).toEqual(['active'])
    expect(artifactStates(art('a', 'spec', ws, { active: null, draft: 1 }))).toEqual(['unapproved', 'pending'])
    expect(artifactStates(art('a', 'spec', ws, { active: 2, draft: 3 }))).toEqual(['active', 'pending'])
    expect(policyStates({ activeRevision: null, draftRevision: 1, rules: 2 })).toEqual(['unapproved', 'pending'])
  })
})

describe('knowledgeRows', () => {
  const all = [
    art('b-skill', 'skill', ws),
    art('a-skill', 'skill', only('p1', 'p2')),
    art('other', 'skill', only('p2')),
    art('orders', 'spec', only('p1'), { active: null, draft: 1 }),
    art('inst', 'instructions', only('p1')),
  ]
  it('絞り込みなしなら種類の順・名前の順', () => {
    expect(knowledgeRows(all, none, null).map((r) => r.artifact.name)).toEqual(['orders', 'a-skill', 'b-skill', 'other', 'inst'])
  })
  it('プロジェクトで絞るとそのプロジェクトのものとチーム共通', () => {
    const rows = knowledgeRows(all, { ...none, kind: 'skill', project: 'org/shop' }, [project])
    expect(rows.map((r) => [r.artifact.name, r.workspaceWide])).toEqual([
      ['a-skill', false],
      ['b-skill', true],
    ])
  })
  it('一覧に無いプロジェクトでは出さない', () => {
    expect(knowledgeRows(all, { ...none, project: 'org/none' }, [project])).toEqual([])
    expect(knowledgeRows(all, { ...none, project: 'org/shop' }, null)).toEqual([])
  })
  it('状態と規約の種類で絞る', () => {
    expect(knowledgeRows(all, { ...none, status: 'pending' }, null).map((r) => r.artifact.name)).toEqual(['orders'])
    expect(knowledgeRows(all, { ...none, kind: 'policy' }, null)).toEqual([])
  })
})

describe('showPolicyRow', () => {
  const row = { activeRevision: 2, draftRevision: null, rules: 3 }
  it('種類が規約か無指定で、状態が合うときだけ', () => {
    expect(showPolicyRow(row, none)).toBe(true)
    expect(showPolicyRow(row, { ...none, kind: 'spec' })).toBe(false)
    expect(showPolicyRow(row, { ...none, kind: 'policy', project: 'org/shop' })).toBe(true)
    expect(showPolicyRow(row, { ...none, status: 'pending' })).toBe(false)
  })
})

describe('pendingArtifacts / knowledgeCounts', () => {
  it('下書きのあるものと種類ごとの件数', () => {
    const all = [art('s', 'spec', only('p1'), { draft: 2 }), art('k', 'skill', ws)]
    expect(pendingArtifacts(all).map((a) => a.name)).toEqual(['s'])
    expect(knowledgeCounts(all, 4)).toEqual({ spec: 1, skill: 1, instructions: 0, policy: 4 })
  })
})

describe('scopeForProject / findFromProject', () => {
  it('ワークスペース全体にせず、そのプロジェクトだけ', () => {
    expect(scopeForProject('p1')).toEqual({ workspace: false, projects: ['p1'] })
  })
  it('from が一覧にあればそのプロジェクト、無ければ null', () => {
    expect(findFromProject([project], 'org/shop')).toBe(project)
    expect(findFromProject([project], 'org/none')).toBeNull()
    expect(findFromProject(null, 'org/shop')).toBeNull()
    expect(findFromProject([project], null)).toBeNull()
  })
})

describe('policyRowData', () => {
  it('有効な改訂のルール数、無ければ下書きの。どちらも無ければ null', () => {
    const rev = (revision: number, rules: number) => ({ revision, package: { rules: new Array(rules).fill({}) } })
    const view = (active: unknown, draft: unknown) => ({ active, draft }) as unknown as Parameters<typeof policyRowData>[0]
    expect(policyRowData(view(rev(2, 3), rev(3, 4)))).toEqual({ activeRevision: 2, draftRevision: 3, rules: 3 })
    expect(policyRowData(view(null, rev(1, 4)))).toEqual({ activeRevision: null, draftRevision: 1, rules: 4 })
    expect(policyRowData(view(null, null))).toBeNull()
  })
})
