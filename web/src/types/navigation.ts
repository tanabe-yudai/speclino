/** プロジェクト画面とワークスペース画面のタブ、編集画面の戻り先（S74）。 */

import type { ArtifactSummary } from './knowledge'

export type ProjectTab = 'overview' | 'specs' | 'skills' | 'instructions' | 'policies' | 'runs'

export type WorkspaceTab = 'projects' | 'shared' | 'policies' | 'usage'

export interface TabItem {
  id: string
  label: string
  to: string
}

/** 一覧の 1 行。workspaceWide は「チーム共通」（ワークスペース全体のスコープ）。 */
export interface ArtifactRowData {
  artifact: ArtifactSummary
  workspaceWide: boolean
}

/** 編集画面の「戻る」リンク。 */
export interface BackLink {
  to: string
  label: string
}

/** スマホ幅のメニューの 1 項目。depth=1 はプロジェクトのタブ（字下げ）。 */
export interface NavEntry {
  label: string
  to: string
  depth: 0 | 1
  project?: boolean
}
