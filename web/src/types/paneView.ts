/** 狭い画面で本文とプレビューのどちらを出すか。 */
export type PaneView = 'edit' | 'preview'

export interface PaneViewSwitchProps {
  view: PaneView
  onChange: (view: PaneView) => void
  /** プレビュー側の診断の件数（仕様のみ。null は未取得で出さない） */
  diagnosticCount?: number | null
}
