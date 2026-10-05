/** 再利用コンポーネントの props 型。コンポーネント本体には型を書かず、ここに集約する。 */

import type { AttestationTarget } from './attestation'
import type { Counterexample, Finding } from './finding'
import type { RunEvent } from './events'
import type { RerunResponse, ReviewRunDetail } from './run'
import type { AgentSupplement } from './supplement'

/** 判定の 4 値の色（design 3.1。意味と色の対応は固定）と、免除中（exempt）の色。 */
export type VerdictTone = 'ok' | 'bad' | 'unk' | 'rev' | 'exempt'

export interface LoadingStateProps {
  message?: string
}

export interface ErrorStateProps {
  message: string
  onRetry?: () => void
}

export interface CounterexamplePanelProps {
  counterexample: Counterexample
}

export interface FindingCardProps {
  finding: Finding
  /** GitHub リンク生成に使う。無ければリンクを出さずテキストのみ表示する。 */
  repoFullName: string | null
  headSha: string
}

export interface ScopeBlockProps {
  title: string
  /** 保証範囲（解析が対応した範囲の説明）。 */
  scopeItems: string[]
  /** 前提として置いた仮定。 */
  assumptions: string[]
  /** まだ確認できていない範囲（UNKNOWN / REVIEW の要求など）。 */
  unresolvedItems: string[]
}

export interface TimelineProps {
  events: RunEvent[]
}

export interface AttestButtonProps {
  slug: string
  runId: string
  target: AttestationTarget
  /** 確定の後に呼ぶ（再検査の run があればその ID）。 */
  onDone: (recheckRunId: string | null) => void
}

export interface FindingAttestProps {
  /** run のワークスペース（未登録なら null） */
  slug: string | null
  run: ReviewRunDetail
  finding: Finding
  /** owner / approver なら確定のボタンを出す */
  mayAttest: boolean
}

export interface RerunButtonProps {
  runId: string
  /** 再実行が作られた後に呼ぶ（新しい run へ移動するなど）。 */
  onStarted: (result: RerunResponse) => void
}

export interface SupplementListProps {
  supplements: AgentSupplement[]
}
