/** 再利用コンポーネントの props 型。コンポーネント本体には型を書かず、ここに集約する。 */

import type { ReactNode } from 'react'
import type { Counterexample, Finding } from './finding'
import type { RunEvent } from './events'
import type { RerunResponse } from './run'

export type BadgeTone = 'success' | 'warning' | 'danger' | 'neutral' | 'info'

/** 判定の 4 値の色（design 3.1。意味と色の対応は固定）。 */
export type VerdictTone = 'ok' | 'bad' | 'unk' | 'rev'

export interface BadgeProps {
  tone: BadgeTone
  children: ReactNode
}

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

export interface RerunButtonProps {
  runId: string
  /** 再実行が作られた後に呼ぶ（新しい run へ移動するなど）。 */
  onStarted: (result: RerunResponse) => void
}
