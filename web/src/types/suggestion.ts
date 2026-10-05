/** AI の書き換え提案（S67、POST /specs/suggest）の型。サーバの `SpecSuggestions` に合わせる。 */

import type { SpecDiagnostic } from './knowledge'

export interface SuggestionOutcome {
  errors_before: number
  errors_after: number
  compiles: boolean
  diagnostics_after: SpecDiagnostic[]
}

/** サーバの提案。start / end は本文の code point の位置。 */
export interface SuggestionResult {
  start: number
  end: number
  replacement: string
  reason: string
  after: SuggestionOutcome
}

export interface SpecSuggestions {
  errors_before: number
  suggestions: SuggestionResult[]
}

/** 編集欄に当てるための提案。start / end は UTF-16 の位置（JS の文字列の添字）。 */
export interface PendingSuggestion {
  id: number
  start: number
  end: number
  replacement: string
  reason: string
  after: SuggestionOutcome
}

/** 提案の前後の数行（差分の表示用）。 */
export interface SuggestionExcerpt {
  before: string
  after: string
  /** before の先頭の行番号（1 始まり） */
  firstLine: number
}

export type SuggestionPanelState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | {
      kind: 'ready'
      /** 提案の位置が指している本文（求めた本文に、適用した提案を当てたもの） */
      base: string
      items: PendingSuggestion[]
      errorsBefore: number
      /** 先に適用した提案と重なって外した件数 */
      dropped: number
    }

export interface SuggestionPanelProps {
  state: SuggestionPanelState
  /** 提案を受け取った後に本文が手で変わった（残りの提案は適用できない） */
  stale: boolean
  /** 提案を求められない理由（保存中・プレビュー中・JSON 欄が読めない）。null なら求められる */
  blockedReason: string | null
  onRequest: () => void
  /** 編集欄だけを変える。保存はしない */
  onApply: (s: PendingSuggestion) => void
  onReject: (s: PendingSuggestion) => void
}
