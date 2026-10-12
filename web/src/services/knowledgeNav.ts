/**
 * 種類ごとの知識の画面（仕様・スキル・指示書・規約）の絞り込みと状態、仕様の節の振り分け、エディタの行き来。
 * 状態の判断（有効・承認待ち・未承認・取り下げ済み）は artifactStates / policyStates だけで行う。
 */

import type { ArtifactKind, ArtifactScope, ArtifactSummary } from '@/types/knowledge'
import type {
  KnowledgeKind,
  KnowledgeListFilter,
  KnowledgeState,
  KnowledgeStatusFilter,
  PolicyRowData,
  RepositorySpecs,
  SpecGroups,
} from '@/types/navigation'
import type { PoliciesView } from '@/types/policy'
import type { ProjectSummary } from '@/types/project'

export const KNOWLEDGE_KINDS: KnowledgeKind[] = ['spec', 'skill', 'instructions', 'policy']
/** 絞り込みに出す状態（取り下げ済みは一覧に出さない）。 */
export const KNOWLEDGE_STATUS_FILTERS: KnowledgeStatusFilter[] = ['active', 'pending', 'unapproved']

const ARTIFACT_KINDS: ArtifactKind[] = ['spec', 'skill', 'instructions']

/** 状態のバッジの色（無いものは灰色）。 */
export const KNOWLEDGE_STATE_TONE: Partial<Record<KnowledgeState, 'ok' | 'rev'>> = { active: 'ok', pending: 'rev' }

/** URL のクエリ（`?project=&status=`）を絞り込みに。知らない状態は絞らない。 */
export function parseKnowledgeListFilter(params: URLSearchParams): KnowledgeListFilter {
  const raw = params.get('status')
  return {
    project: params.get('project') || null,
    status: KNOWLEDGE_STATUS_FILTERS.find((s) => s === raw) ?? null,
  }
}

/** 取り下げ済み（有効な改訂が無く、取り下げた改訂がある）。一覧・件数から外し、設定の「取り下げ済みの知識」にだけ出す。 */
export function isRetired(a: ArtifactSummary): boolean {
  return a.active_revision === null && a.retired_revision != null
}

/**
 * 知識 1 件の状態（表示する順）。有効な改訂が無ければ、取り下げた改訂があれば取り下げ済み、
 * 無ければ未承認。下書きがあれば承認待ちも。
 */
export function artifactStates(a: ArtifactSummary): KnowledgeState[] {
  const states: KnowledgeState[] = [a.active_revision !== null ? 'active' : isRetired(a) ? 'retired' : 'unapproved']
  if (a.draft_revision !== null) states.push('pending')
  return states
}

/** 規約（チーム規約）の状態。 */
export function policyStates(row: PolicyRowData): KnowledgeState[] {
  const states: KnowledgeState[] = [row.activeRevision !== null ? 'active' : 'unapproved']
  if (row.draftRevision !== null) states.push('pending')
  return states
}

const byName = (x: ArtifactSummary, y: ArtifactSummary) => x.name.localeCompare(y.name)
const byKindAndName = (x: ArtifactSummary, y: ArtifactSummary) =>
  ARTIFACT_KINDS.indexOf(x.kind) - ARTIFACT_KINDS.indexOf(y.kind) || byName(x, y)

/** 種類ごとの画面の行（取り下げ済みを除き、状態で絞る）。名前の順。 */
export function kindArtifacts(artifacts: ArtifactSummary[], kind: ArtifactKind, status: KnowledgeStatusFilter | null): ArtifactSummary[] {
  return artifacts
    .filter((a) => a.kind === kind && !isRetired(a))
    .filter((a) => !status || artifactStates(a).includes(status))
    .sort(byName)
}

/** ワークスペース全体の仕様か（スコープがワークスペース全体）。 */
export function isWorkspaceSpec(scope: ArtifactScope | null | undefined): boolean {
  return !!scope?.workspace
}

/**
 * 仕様を「ワークスペース全体」と「リポジトリ別」に振り分ける。リポジトリ別はリポジトリ名の順に、
 * そのプロジェクトをスコープに含む仕様（複数のプロジェクトを挙げた仕様はそれぞれに出す）。仕様の無いリポジトリは出さない。
 * このワークスペースに無いプロジェクトだけを挙げた仕様・スコープの無い仕様は、最後の project が null のまとまり。
 * repo（owner/repo）を渡すと、リポジトリ別はそのリポジトリだけ（一覧に無ければ空）。
 */
export function specGroups(specs: ArtifactSummary[], projects: ProjectSummary[], repo: string | null = null): SpecGroups {
  const workspace: ArtifactSummary[] = []
  const perRepo: ArtifactSummary[] = []
  for (const s of specs) (isWorkspaceSpec(s.scope) ? workspace : perRepo).push(s)

  const ordered = [...projects].sort((a, b) => a.repo_full_name.localeCompare(b.repo_full_name))
  const targets = repo ? ordered.filter((p) => p.repo_full_name === repo) : ordered
  const repositories: RepositorySpecs[] = targets
    .map((project) => ({ project, specs: perRepo.filter((s) => !!s.scope?.projects.includes(project.id)).sort(byName) }))
    .filter((g) => g.specs.length > 0)
  if (!repo) {
    const known = new Set(projects.map((p) => p.id))
    const orphans = perRepo.filter((s) => !s.scope || !s.scope.projects.some((id) => known.has(id)))
    if (orphans.length > 0) repositories.push({ project: null, specs: orphans.sort(byName) })
  }
  return { workspace: workspace.sort(byName), repositories }
}

/** 取り下げ済みの知識（設定の「取り下げ済みの知識」）。種類の順、名前の順。 */
export function retiredArtifacts(artifacts: ArtifactSummary[]): ArtifactSummary[] {
  return artifacts.filter(isRetired).sort(byKindAndName)
}

/** 規約の画面の応答から行。規約が 1 つも無い（有効も下書きも無い）ワークスペースは null。 */
export function policyRowData(view: PoliciesView): PolicyRowData | null {
  if (!view.active && !view.draft) return null
  return {
    activeRevision: view.active?.revision ?? null,
    draftRevision: view.draft?.revision ?? null,
    rules: (view.active ?? view.draft)?.package.rules.length ?? 0,
  }
}

/**
 * 承認を待つ下書き（ホームの「やること」）。種類の順、名前の順。
 * 取り下げ済みでも下書きがあれば出す（承認すると戻るので、承認者の操作が要る）。
 */
export function pendingArtifacts(artifacts: ArtifactSummary[]): ArtifactSummary[] {
  return artifacts.filter((a) => a.draft_revision !== null).sort(byKindAndName)
}

/** 種類ごとの件数（ホーム）。取り下げ済みは数えない。規約はルールの数。 */
export function knowledgeCounts(artifacts: ArtifactSummary[], policyRules: number | null): Record<KnowledgeKind, number | null> {
  const count = (k: ArtifactKind) => artifacts.filter((a) => a.kind === k && !isRetired(a)).length
  return { spec: count('spec'), skill: count('skill'), instructions: count('instructions'), policy: policyRules }
}

/** 「このプロジェクトで作る」ときの初期スコープ。 */
export function scopeForProject(projectId: string): ArtifactScope {
  return { workspace: false, projects: [projectId] }
}

/** `?from=owner/repo` が今のプロジェクトの一覧にあるときだけ、そのプロジェクト。 */
export function findFromProject(projects: ProjectSummary[] | null, from: string | null): ProjectSummary | null {
  if (!from || !projects) return null
  return projects.find((p) => p.repo_full_name === from) ?? null
}
