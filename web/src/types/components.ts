/** 再利用コンポーネントの props 型。コンポーネント本体には型を書かず、ここに集約する。 */

import type { ReactNode } from 'react'
import type { Counterexample, Finding } from './finding'
import type { RunEvent } from './events'

export type BadgeTone = 'success' | 'warning' | 'danger' | 'neutral' | 'info'

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
