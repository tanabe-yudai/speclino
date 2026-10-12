/** ⌘K のコマンドと、画面のパスで使う型。 */

export type CommandGroup = 'ワークスペース' | 'プロジェクト' | '画面'

export interface Command {
  id: string
  label: string
  group: CommandGroup
  path: string
}

/** 設定のタブ（`/w/:slug/settings/:tab`）。retired は取り下げ済みの知識。 */
export type SettingsTab = 'members' | 'repositories' | 'agents' | 'projects' | 'retired'
