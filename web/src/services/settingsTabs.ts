/**
 * 設定画面のタブ。MCP トークンは owner だけの操作（API も owner 以外は 404）。
 * sync トークンの一覧はメンバー全員（発行・失効は owner / approver で、タブの中で出し分ける）。
 */

import type { SettingsTab } from '@/types/command'
import type { WorkspaceRole } from '@/types/me'

const COMMON_TABS: SettingsTab[] = ['projects', 'repositories', 'members']

export function visibleSettingsTabs(role: WorkspaceRole | null): SettingsTab[] {
  if (role === null) return COMMON_TABS
  return role === 'owner' ? [...COMMON_TABS, 'sync', 'tokens'] : [...COMMON_TABS, 'sync']
}

/** URL の ?tab= を、見えるタブのどれかに正す（見えない・不正なら先頭）。 */
export function pickSettingsTab(requested: string | null, visible: SettingsTab[]): SettingsTab {
  return visible.find((t) => t === requested) ?? visible[0]
}
