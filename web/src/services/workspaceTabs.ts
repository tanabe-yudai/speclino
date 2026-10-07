/** ワークスペース画面のタブ（`/w/:slug?tab=`）。 */

import type { WorkspaceTab } from '@/types/navigation'

export const WORKSPACE_TABS: WorkspaceTab[] = ['projects', 'shared', 'policies', 'usage']

export const WORKSPACE_TAB_LABELS: Record<WorkspaceTab, string> = {
  projects: 'プロジェクト',
  shared: 'チーム共通',
  policies: 'チーム規約',
  usage: '利用状況',
}

/** URL の ?tab= を正す（無い・不正ならプロジェクト）。 */
export function pickWorkspaceTab(raw: string | null): WorkspaceTab {
  return WORKSPACE_TABS.find((t) => t === raw) ?? 'projects'
}
