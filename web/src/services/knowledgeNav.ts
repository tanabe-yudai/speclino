/**
 * 知識の一覧（仕様・スキル・指示書・規約）の絞り込みと状態、エディタの行き来。
 * 状態の判断（有効・承認待ち・未承認・取り下げ済み）は artifactStates / policyStates だけで行う。
 */

import type { ArtifactKind, ArtifactScope, ArtifactSummary } from '@/types/knowledge'
import type { ArtifactRowData, KnowledgeFilter, KnowledgeKind, KnowledgeState, PolicyRowData } from '@/types/navigation'
import type { PoliciesView } from '@/types/policy'
import type { ProjectSummary } from '@/types/project'

export const KNOWLEDGE_KINDS: KnowledgeKind[] = ['spec', 'skill', 'instructions', 'policy']
/** 絞り込みに出す状態。 */
export const KNOWLEDGE_STATUS_FILTERS: KnowledgeState[] = ['active', 'pending', 'unapproved', 'retired']

const ARTIFACT_KINDS: ArtifactKind[] = ['spec', 'skill', 'instructions']

/** 状態のバッジの色（無いものは灰色）。 */
export const KNOWLEDGE_STATE_TONE: Partial<Record<KnowledgeState, 'ok' | 'rev'>> = { active: 'ok', pending: 'rev' }

function pick<T extends string>(all: readonly T[], raw: string | null): T | null {
  return all.find((x) => x === raw) ?? null
}

/** URL のクエリ（`?kind=&project=&status=`）を絞り込みに。知らない値は絞らない。 */
export function parseKnowledgeFilter(params: URLSearchParams): KnowledgeFilter {
  return {
    kind: pick(KNOWLEDGE_KINDS, params.get('kind')),
    project: params.get('project') || null,
    status: pick<KnowledgeState>(['active', 'pending', 'unapproved', 'retired'], params.get('status')),
  }
}

/**
 * 知識 1 件の状態（表示する順）。有効な改訂が無ければ、取り下げた改訂があれば取り下げ済み、
 * 無ければ未承認。下書きがあれば承認待ちも。
 */
export function artifactStates(a: ArtifactSummary): KnowledgeState[] {
  const states: KnowledgeState[] = [a.active_revision !== null ? 'active' : a.retired_revision != null ? 'retired' : 'unapproved']
  if (a.draft_revision !== null) states.push('pending')
  return states
}

/** 規約（チーム規約）の状態。 */
export function policyStates(row: PolicyRowData): KnowledgeState[] {
  const states: KnowledgeState[] = [row.activeRevision !== null ? 'active' : 'unapproved']
  if (row.draftRevision !== null) states.push('pending')
  return states
}

/**
 * 一覧の行。種類・状態で絞り、プロジェクトで絞るときはそのプロジェクトのスコープのものと
 * チーム共通（ワークスペース全体）のもの。並びは種類の順、同じ種類は名前の順。
 * プロジェクトが一覧に無い（まだ読み込み中・別のワークスペース）ときは行を出さない。
 */
export function knowledgeRows(
  artifacts: ArtifactSummary[],
  filter: KnowledgeFilter,
  projects: ProjectSummary[] | null,
): ArtifactRowData[] {
  if (filter.kind === 'policy') return []
  const projectId = filter.project ? (projects?.find((p) => p.repo_full_name === filter.project)?.id ?? null) : null
  if (filter.project && !projectId) return []
  return artifacts
    .filter((a) => !filter.kind || a.kind === filter.kind)
    .filter((a) => !projectId || !!a.scope?.workspace || !!a.scope?.projects.includes(projectId))
    .filter((a) => !filter.status || artifactStates(a).includes(filter.status))
    .map((artifact) => ({ artifact, workspaceWide: !!artifact.scope?.workspace }))
    .sort(
      (x, y) =>
        ARTIFACT_KINDS.indexOf(x.artifact.kind) - ARTIFACT_KINDS.indexOf(y.artifact.kind) ||
        x.artifact.name.localeCompare(y.artifact.name),
    )
}

/** 規約の画面の応答から一覧の行。規約が 1 つも無い（有効も下書きも無い）ワークスペースは null。 */
export function policyRowData(view: PoliciesView): PolicyRowData | null {
  if (!view.active && !view.draft) return null
  return {
    activeRevision: view.active?.revision ?? null,
    draftRevision: view.draft?.revision ?? null,
    rules: (view.active ?? view.draft)?.package.rules.length ?? 0,
  }
}

/** 規約の行を出すか（規約はワークスペース全体に効くので、プロジェクトでは絞らない）。 */
export function showPolicyRow(row: PolicyRowData, filter: KnowledgeFilter): boolean {
  if (filter.kind && filter.kind !== 'policy') return false
  return !filter.status || policyStates(row).includes(filter.status)
}

/** 承認を待つ下書き（ホームの「やること」）。種類の順、名前の順。 */
export function pendingArtifacts(artifacts: ArtifactSummary[]): ArtifactSummary[] {
  return knowledgeRows(artifacts, { kind: null, project: null, status: 'pending' }, null).map((r) => r.artifact)
}

/** 種類ごとの件数（ホーム）。規約はルールの数。 */
export function knowledgeCounts(artifacts: ArtifactSummary[], policyRules: number | null): Record<KnowledgeKind, number | null> {
  const count = (k: ArtifactKind) => artifacts.filter((a) => a.kind === k).length
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
