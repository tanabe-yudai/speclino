/**
 * 設定画面のタブ（メンバー / リポジトリ / 連携 / プロジェクト）。
 * 連携の中のエージェント接続トークンはオーナーだけの操作（API も owner 以外は 404）なので、
 * タブは全員に出し、その節だけを出し分ける。
 */

import type { SettingsTab } from '@/types/command'

export const SETTINGS_TABS: SettingsTab[] = ['members', 'repositories', 'integrations', 'project']

/** 旧 URL の ?tab= を新しいタブへ対応づける。 */
const LEGACY_TABS: Record<string, SettingsTab> = {
  sync: 'integrations',
  tokens: 'integrations',
  projects: 'project',
}

/** URL の ?tab= を、タブのどれかに正す（旧名は新名へ、不正・未指定ならメンバー）。 */
export function pickSettingsTab(requested: string | null): SettingsTab {
  if (requested === null) return SETTINGS_TABS[0]
  return SETTINGS_TABS.find((t) => t === requested) ?? LEGACY_TABS[requested] ?? SETTINGS_TABS[0]
}
