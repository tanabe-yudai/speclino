/**
 * AI が書いている本文の途中経過と、反映の記録（S76）の型。
 * 途中経過は表示専用で、編集欄の状態は変えない。確定値は `done` の提案から取る。
 */

import type { ChatProposal } from './knowledgeChat'

/** 生成中の提案の本文。`before` は送った時点の本文、`text` は届いた全文（置き換えで更新する）。 */
export interface ChatDraft {
  field: string
  before: string
  text: string
}

/** 自動で反映しなかった理由。 */
export type SkipReason = 'invalid' | 'files' | 'edited'

export type ApplyDecision = { action: 'apply' } | { action: 'skip'; reason: SkipReason }

/** 提案 1 件の扱い（この画面で起きたことだけ。再読込の後は残らない）。 */
export type ProposalOutcome = { status: 'applied' } | { status: 'skipped'; reason: SkipReason }

/** 反映の記録: 「元に戻す」は、編集欄が反映直後の状態のままのときだけできる。 */
export interface AppliedEdit {
  /** 反映した発言の ID */
  messageId: string | null
  /** 反映前・反映後の本文（差分の表示と、元に戻すときの内容） */
  before: string
  after: string
  /** 反映直後の編集欄の本文（`editorText`）。今と違えば反映後に編集されている */
  afterEditorText: string
  /** スキルのとき: 反映前・反映直後の付属ファイル（元に戻すときに本文と一緒に戻す） */
  beforeFiles?: Record<string, string>
  afterFiles?: Record<string, string>
}

export interface DecideApplyInput {
  proposal: ChatProposal
  /** 送った時点の編集欄の本文（提案の土台） */
  sentEditorText: string
  /** 今の編集欄の本文 */
  currentEditorText: string
  /** 提案が付属ファイルを変えるか */
  touchesFiles: boolean
  /** 送った時点の本文のハッシュが提案の土台（`base_hash`）と一致するか（冪等の再送で古い提案を入れない） */
  baseMatches: boolean
}

export interface DraftDiffLine {
  op: 'same' | 'add' | 'del'
  text: string
  oldNo: number | null
  newNo: number | null
}

export interface DraftDiff {
  lines: DraftDiffLine[]
  /** 自動で追う行（最後に追加された行）。無ければ -1 */
  anchor: number
}

/** 一覧のスクロールの位置（末尾を追うかの判断に使う）。 */
export interface ScrollMetrics {
  scrollTop: number
  scrollHeight: number
  clientHeight: number
}

export interface DraftOverlayProps {
  draft: ChatDraft
  onStop: () => void
}

export interface AppliedDiffPanelProps {
  applied: AppliedEdit
  /** 編集欄が反映した直後のままか（違えば元に戻せない） */
  undoable: boolean
  onUndo: () => void
  onClose: () => void
}
