/** 画面の行き来（第 1 階層・プロジェクトのタブ・一覧の絞り込み・パンくず）の型。 */

import type { ArtifactKind, ArtifactSummary } from './knowledge'
import type { GateStatus } from './run'

/** ワークスペースの下の第 1 階層。上部バー（PC）と画面下のタブ（スマホ）で同じ並び。 */
export type SectionId = 'home' | 'knowledge' | 'runs' | 'observability' | 'settings'

/** プロジェクト（絞り込みの画面）のタブ。 */
export type ProjectTab = 'overview' | 'knowledge' | 'runs'

export interface TabItem {
  id: string
  label: string
  to: string
}

/** 知識の種類。規約（チーム規約）は改訂の単位が別だが、一覧では同じ並びに置く。 */
export type KnowledgeKind = ArtifactKind | 'policy'

/**
 * 一覧の状態。有効=有効な改訂がある、承認待ち=承認を待つ下書きがある、
 * 未承認=有効な改訂がまだ無い、取り下げ済み=取り下げた知識。
 */
export type KnowledgeState = 'active' | 'pending' | 'unapproved' | 'retired'

/** 知識の一覧の絞り込み（`?kind=&project=&status=`）。project は owner/repo。 */
export interface KnowledgeFilter {
  kind: KnowledgeKind | null
  project: string | null
  status: KnowledgeState | null
}

/** 一覧の 1 行。workspaceWide は「チーム共通」（ワークスペース全体のスコープ）。 */
export interface ArtifactRowData {
  artifact: ArtifactSummary
  workspaceWide: boolean
}

/** 規約の行（チーム規約は 1 つの改訂の列）。 */
export interface PolicyRowData {
  activeRevision: number | null
  draftRevision: number | null
  rules: number
}

/** 検査の一覧の絞り込み（`?gate=&project=`）。running はゲートがまだ出ていない検査。 */
export type RunGateFilter = GateStatus | 'running'

export interface RunFilter {
  gate: RunGateFilter | null
  project: string | null
}

/** パンくずの 1 段。to が無いものは今の画面。 */
export interface Crumb {
  label: string
  to?: string
}
