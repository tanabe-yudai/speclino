/** 設定画面のタブ。MCP トークンは owner だけの操作（API も owner 以外は 404）。 */

import type { SettingsTab } from '@/types/command'
import type { WorkspaceRole } from '@/types/me'

export function visibleSettingsTabs(role: WorkspaceRole | null): SettingsTab[] {
  return role === 'owner' ? ['repositories', 'tokens'] : ['repositories']
}

/** URL の ?tab= を、見えるタブのどれかに正す（見えない・不正なら先頭）。 */
export function pickSettingsTab(requested: string | null, visible: SettingsTab[]): SettingsTab {
  return visible.find((t) => t === requested) ?? visible[0]
}
