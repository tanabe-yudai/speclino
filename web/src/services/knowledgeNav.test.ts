import { describe, expect, it } from 'vitest'
import type { ArtifactScope, ArtifactSummary } from '@/types/knowledge'
import type { ProjectSummary } from '@/types/project'
import {
  KNOWLEDGE_STATUS_FILTERS,
  artifactStates,
  findFromProject,
  isWorkspaceSpec,
  kindArtifacts,
  knowledgeCounts,
  parseKnowledgeListFilter,
  pendingArtifacts,
  policyRowData,
  policyStates,
  retiredArtifacts,
  scopeForProject,
  specGroups,
} from './knowledgeNav'

function art(
  name: string,
  kind: ArtifactSummary['kind'],
  scope: ArtifactScope | null,
  revs: { active?: number | null; draft?: number | null; retired?: number | null } = {},
): ArtifactSummary {
  return {
    artifact_id: name,
    workspace: 'ws',
    kind,
    name,
    active_revision: revs.active === undefined ? 1 : revs.active,
    draft_revision: revs.draft ?? null,
    retired_revision: revs.retired ?? null,
    scope,
    created_at: '2026-10-01T00:00:00Z',
  }
}
const ws: ArtifactScope = { workspace: true, projects: [] }
const only = (...projects: string[]): ArtifactScope => ({ workspace: false, projects })
const project = { id: 'p1', repo_full_name: 'org/shop' } as ProjectSummary
const p2 = { id: 'p2', repo_full_name: 'org/api' } as ProjectSummary
const p3 = { id: 'p3', repo_full_name: 'org/web' } as ProjectSummary

describe('parseKnowledgeListFilter', () => {
  it('知っている状態だけを拾い、取り下げ済みでは絞らない', () => {
    expect(parseKnowledgeListFilter(new URLSearchParams('project=org/shop&status=pending'))).toEqual({ project: 'org/shop', status: 'pending' })
    expect(parseKnowledgeListFilter(new URLSearchParams('status=retired'))).toEqual({ project: null, status: null })
    expect(parseKnowledgeListFilter(new URLSearchParams('status=y'))).toEqual({ project: null, status: null })
    expect(KNOWLEDGE_STATUS_FILTERS).toEqual(['active', 'pending', 'unapproved'])
  })
})

describe('artifactStates / policyStates', () => {
  it('有効な改訂が無ければ未承認、下書きがあれば承認待ちも', () => {
    expect(artifactStates(art('a', 'spec', ws))).toEqual(['active'])
    expect(artifactStates(art('a', 'spec', ws, { active: null, draft: 1 }))).toEqual(['unapproved', 'pending'])
    expect(artifactStates(art('a', 'spec', ws, { active: 2, draft: 3 }))).toEqual(['active', 'pending'])
    expect(artifactStates({ ...art('a', 'spec', ws, { active: null }), retired_revision: 2 })).toEqual(['retired'])
    expect(artifactStates({ ...art('a', 'spec', ws, { active: null, draft: 3 }), retired_revision: 2 })).toEqual(['retired', 'pending'])
    expect(policyStates({ activeRevision: null, draftRevision: 1, rules: 2 })).toEqual(['unapproved', 'pending'])
  })
})

describe('kindArtifacts', () => {
  const all = [
    art('b-skill', 'skill', ws),
    art('a-skill', 'skill', only('p1', 'p2')),
    art('gone', 'skill', ws, { active: null, retired: 2 }),
    art('orders', 'spec', only('p1'), { active: null, draft: 1 }),
    art('inst', 'instructions', only('p1')),
  ]
  it('その種類だけを名前の順に、取り下げ済みは出さない', () => {
    expect(kindArtifacts(all, 'skill', null).map((a) => a.name)).toEqual(['a-skill', 'b-skill'])
    expect(kindArtifacts(all, 'instructions', null).map((a) => a.name)).toEqual(['inst'])
  })
  it('状態で絞る', () => {
    expect(kindArtifacts(all, 'spec', 'pending').map((a) => a.name)).toEqual(['orders'])
    expect(kindArtifacts(all, 'spec', 'active')).toEqual([])
  })
})

describe('isWorkspaceSpec / specGroups', () => {
  const projects = [project, p2, p3]
  it('スコープがワークスペース全体の仕様だけがワークスペース全体（全プロジェクトの列挙は推測しない）', () => {
    expect(isWorkspaceSpec(ws)).toBe(true)
    expect(isWorkspaceSpec(only('p1', 'p2', 'p3'))).toBe(false)
    expect(isWorkspaceSpec(null)).toBe(false)
  })
  const specs = [
    art('req', 'spec', ws),
    art('all3', 'spec', only('p3', 'p2', 'p1')),
    art('orders', 'spec', only('p1')),
    art('auth', 'spec', only('p1', 'p2')),
    art('lost', 'spec', only('px')),
    art('noscope', 'spec', null),
  ]
  it('リポジトリ別はリポジトリ名の順、複数のリポジトリに当たる仕様はそれぞれに、見つからないものは最後', () => {
    const g = specGroups(specs, projects)
    expect(g.workspace.map((a) => a.name)).toEqual(['req'])
    expect(g.repositories.map((r) => [r.project?.repo_full_name ?? null, r.specs.map((a) => a.name)])).toEqual([
      ['org/api', ['all3', 'auth']],
      ['org/shop', ['all3', 'auth', 'orders']],
      ['org/web', ['all3']],
      [null, ['lost', 'noscope']],
    ])
  })
  it('リポジトリで絞るとリポジトリ別はそのリポジトリだけ（ワークスペース全体はそのまま）', () => {
    const g = specGroups(specs, projects, 'org/shop')
    expect(g.workspace.map((a) => a.name)).toEqual(['req'])
    expect(g.repositories.map((r) => r.project?.id)).toEqual(['p1'])
    expect(specGroups(specs, projects, 'org/none').repositories).toEqual([])
  })
})

describe('retiredArtifacts', () => {
  it('取り下げ済みだけを種類の順・名前の順に', () => {
    const all = [
      art('k', 'skill', ws, { active: null, retired: 1 }),
      art('s2', 'spec', only('p1'), { active: null, retired: 3, draft: 4 }),
      art('s1', 'spec', only('p1'), { active: null, retired: 1 }),
      art('live', 'spec', only('p1')),
      art('new', 'spec', only('p1'), { active: null, draft: 1 }),
    ]
    expect(retiredArtifacts(all).map((a) => a.name)).toEqual(['s1', 's2', 'k'])
  })
})

describe('pendingArtifacts / knowledgeCounts', () => {
  it('下書きのあるものと種類ごとの件数', () => {
    const all = [art('s', 'spec', only('p1'), { draft: 2 }), art('k', 'skill', ws), art('gone', 'skill', ws, { active: null, retired: 1 })]
    expect(pendingArtifacts(all).map((a) => a.name)).toEqual(['s'])
    expect(knowledgeCounts(all, 4)).toEqual({ spec: 1, skill: 1, instructions: 0, policy: 4 })
  })
  it('取り下げ済みは件数に数えないが、戻すための下書きは承認待ちに出す', () => {
    const restoring = art('back', 'skill', ws, { active: null, retired: 1, draft: 2 })
    expect(knowledgeCounts([restoring], null).skill).toBe(0)
    expect(pendingArtifacts([restoring]).map((a) => a.name)).toEqual(['back'])
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
