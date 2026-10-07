/** 仕様・スキル・指示書の一覧と編集画面の行き来（S74）。スコープで画面ごとに絞り、戻り先を決める。 */

import type { ArtifactKind, ArtifactScope, ArtifactSummary } from '@/types/knowledge'
import type { ArtifactRowData, BackLink } from '@/types/navigation'
import type { ProjectSummary } from '@/types/project'
import { KIND_LABELS } from './knowledge'
import { paths } from './paths'
import { kindTab } from './projectTabs'

const byName = (a: ArtifactRowData, b: ArtifactRowData) => a.artifact.name.localeCompare(b.artifact.name)

/** プロジェクトのタブの行: スコープにそのプロジェクトを含むもの＋ワークスペース全体のもの（チーム共通）。 */
export function projectArtifactRows(artifacts: ArtifactSummary[], kind: ArtifactKind, projectId: string): ArtifactRowData[] {
  return artifacts
    .filter((a) => a.kind === kind)
    .flatMap((artifact): ArtifactRowData[] => {
      if (artifact.scope?.workspace) return [{ artifact, workspaceWide: true }]
      if (artifact.scope?.projects.includes(projectId)) return [{ artifact, workspaceWide: false }]
      return []
    })
    .sort(byName)
}

/** チーム共通のタブの行: ワークスペース全体のスコープのもの。 */
export function sharedArtifactRows(artifacts: ArtifactSummary[], kind: ArtifactKind): ArtifactRowData[] {
  return artifacts
    .filter((a) => a.kind === kind && a.scope?.workspace)
    .map((artifact) => ({ artifact, workspaceWide: true }))
    .sort(byName)
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

/**
 * 編集画面から戻る先。from のプロジェクトがあればそのタブ、無ければ
 * スキル・指示書はチーム共通、仕様はワークスペースのホーム。
 */
export function backLink(slug: string, kind: ArtifactKind, fromProject: ProjectSummary | null): BackLink {
  if (fromProject) {
    const [, repo] = fromProject.repo_full_name.split('/')
    return { to: paths.project(slug, fromProject.repo_full_name, kindTab(kind)), label: `← ${repo} の${KIND_LABELS[kind]}` }
  }
  if (kind === 'spec') return { to: paths.workspace(slug), label: '← ワークスペースのホーム' }
  return { to: paths.workspace(slug, 'shared'), label: '← チーム共通' }
}
