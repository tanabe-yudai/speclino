/** パンくず（components/shell/Breadcrumbs.tsx）の段。第 1 階層（知識・検査）から今の画面まで。 */

import type { ArtifactKind } from '@/types/knowledge'
import type { Crumb } from '@/types/navigation'
import type { ProjectSummary } from '@/types/project'
import { KIND_LABELS } from './knowledge'
import { SECTION_LABELS } from './labels'
import { paths } from './paths'

/**
 * 知識のエディタ・改訂履歴。プロジェクトから開いた（from がある）ときは、そのプロジェクトの
 * 知識のタブ（種類で絞ったもの）を経由する。revisionsOf を渡すと「改訂の履歴」の段を足す。
 */
export function knowledgeCrumbs(input: {
  slug: string
  kind: ArtifactKind
  name: string | null
  fromProject: ProjectSummary | null
  revisionsOf?: string
}): Crumb[] {
  const { slug, kind, name, fromProject, revisionsOf } = input
  const label = KIND_LABELS[kind]
  const base: Crumb[] = fromProject
    ? [
        { label: fromProject.repo_full_name.split('/')[1], to: paths.project(slug, fromProject.repo_full_name) },
        { label, to: paths.project(slug, fromProject.repo_full_name, 'knowledge', { kind }) },
      ]
    : [
        { label: SECTION_LABELS.knowledge, to: paths.knowledge(slug) },
        { label, to: paths.knowledge(slug, { kind }) },
      ]
  if (revisionsOf) {
    const from = fromProject?.repo_full_name
    return [...base, { label: name ?? '…', to: paths.knowledgeItem(slug, kind, revisionsOf, { from }) }, { label: '改訂の履歴' }]
  }
  return [...base, { label: name ?? `新しい${label}` }]
}

/** 規約の画面。 */
export function policiesCrumbs(slug: string): Crumb[] {
  return [{ label: SECTION_LABELS.knowledge, to: paths.knowledge(slug) }, { label: '規約' }]
}

/** 検査の詳細。ワークスペースが分からない run は一覧へ戻れないので今の画面だけ。 */
export function runCrumbs(slug: string | null, label: string): Crumb[] {
  return slug ? [{ label: SECTION_LABELS.runs, to: paths.runs(slug) }, { label }] : [{ label }]
}
