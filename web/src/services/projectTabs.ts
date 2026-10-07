/** プロジェクト画面のタブ（`/w/:slug/p/:owner/:repo/:tab`）。 */

import type { ArtifactKind } from '@/types/knowledge'
import type { ProjectTab } from '@/types/navigation'

export const PROJECT_TABS: ProjectTab[] = ['overview', 'specs', 'skills', 'instructions', 'policies', 'runs']

export const PROJECT_TAB_LABELS: Record<ProjectTab, string> = {
  overview: '概要',
  specs: '仕様',
  skills: 'スキル',
  instructions: '指示書',
  policies: '規約',
  runs: '検査履歴',
}

/** URL のタブ。無ければ概要、知らないタブは null（画面側で概要へ寄せる）。 */
export function parseProjectTab(raw: string | undefined): ProjectTab | null {
  if (raw === undefined) return 'overview'
  return PROJECT_TABS.find((t) => t === raw) ?? null
}

const TAB_KIND: Partial<Record<ProjectTab, ArtifactKind>> = { specs: 'spec', skills: 'skill', instructions: 'instructions' }

/** 仕様・スキル・指示書のタブが扱う種類。それ以外のタブは null。 */
export function tabKind(tab: ProjectTab): ArtifactKind | null {
  return TAB_KIND[tab] ?? null
}

const KIND_TAB: Record<ArtifactKind, ProjectTab> = { spec: 'specs', skill: 'skills', instructions: 'instructions' }

export function kindTab(kind: ArtifactKind): ProjectTab {
  return KIND_TAB[kind]
}
