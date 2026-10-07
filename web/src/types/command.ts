/** ⌘K のコマンドと、画面のパスで使う型。 */

export type CommandGroup = 'ワークスペース' | 'プロジェクト' | '画面'

export interface Command {
  id: string
  label: string
  group: CommandGroup
  path: string
}

export type SettingsTab = 'members' | 'repositories' | 'integrations' | 'project'
