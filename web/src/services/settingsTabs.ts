/**
 * 設定画面のタブ（`/w/:slug/settings/:tab`: メンバー / リポジトリ / エージェント接続 / プロジェクト / 取り下げ済みの知識）。
 * エージェント接続の中のトークンの発行はオーナー（MCP）・オーナーと承認者（sync）だけの操作なので、
 * タブは全員に出し、その節だけを出し分ける。旧い `?tab=` は legacyRoutes が転送する。
 */

import type { SettingsTab } from '@/types/command'

export const SETTINGS_TABS: SettingsTab[] = ['members', 'repositories', 'agents', 'projects', 'retired']

/** URL のタブ。無ければメンバー、知らないタブは null。 */
export function parseSettingsTab(raw: string | undefined): SettingsTab | null {
  if (raw === undefined) return SETTINGS_TABS[0]
  return SETTINGS_TABS.find((t) => t === raw) ?? null
}
