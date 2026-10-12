/** 画面の行き来（第 1 階層・プロジェクトのタブ・一覧の絞り込み・パンくず）の型。 */

import type { ArtifactKind, ArtifactSummary } from './knowledge'
import type { ProjectSummary } from './project'
import type { GateStatus } from './run'

/**
 * ワークスペースの下の第 1 階層。上部バー（PC）はすべて、画面下のタブ（スマホ）は一部を並べ、残りは「その他」に入れる。
 * 仕様・スキル・指示書・規約は種類ごとに独立した画面。
 */
export type SectionId = 'home' | 'specs' | 'skills' | 'instructions' | 'policies' | 'runs' | 'observability' | 'settings'

/** プロジェクト（絞り込みの画面）のタブ。 */
export type ProjectTab = 'overview' | 'specs' | 'runs'

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

/** 一覧の状態の絞り込み。取り下げ済みは一覧に出さない（設定の「取り下げ済みの知識」に分ける）。 */
export type KnowledgeStatusFilter = Exclude<KnowledgeState, 'retired'>

/** 種類ごとの一覧の絞り込み（`?project=&status=`）。project は owner/repo（仕様の画面だけが使う）。 */
export interface KnowledgeListFilter {
  project: string | null
  status: KnowledgeStatusFilter | null
}

/** リポジトリ別の仕様の 1 まとまり。project が null なのは、このワークスペースに見つからないプロジェクトを指すもの。 */
export interface RepositorySpecs {
  project: ProjectSummary | null
  specs: ArtifactSummary[]
}

/** 仕様の画面の 2 つの節（ワークスペース全体・リポジトリ別）。 */
export interface SpecGroups {
  workspace: ArtifactSummary[]
  repositories: RepositorySpecs[]
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
