import type { PaneView } from '@/types/paneView'

export const PANE_VIEWS: { value: PaneView; label: string }[] = [
  { value: 'edit', label: '本文' },
  { value: 'preview', label: 'プレビュー' },
]

/** 既存の知識を開いたときはプレビュー、新規（本文が空）は本文から始める。 */
export function initialPaneView(text: string): PaneView {
  return text.trim() ? 'preview' : 'edit'
}

/** 切り替えのボタンの文言。診断があるときはプレビュー側に件数を添える。 */
export function paneViewLabel(view: PaneView, diagnosticCount?: number | null): string {
  const label = PANE_VIEWS.find((v) => v.value === view)?.label ?? ''
  return view === 'preview' && diagnosticCount != null && diagnosticCount > 0 ? `${label}（診断 ${diagnosticCount}）` : label
}
