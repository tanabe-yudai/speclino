import { describe, expect, it } from 'vitest'
import { SETTINGS_TABS, parseSettingsTab } from './settingsTabs'

describe('settings tabs', () => {
  it('メンバー・リポジトリ・エージェント接続・プロジェクトの順', () => {
    expect(SETTINGS_TABS).toEqual(['members', 'repositories', 'agents', 'projects'])
  })
  it('無ければメンバー、知っているタブはそのまま、知らないタブは null', () => {
    expect(parseSettingsTab(undefined)).toBe('members')
    expect(parseSettingsTab('agents')).toBe('agents')
    expect(parseSettingsTab('projects')).toBe('projects')
    expect(parseSettingsTab('integrations')).toBeNull()
  })
})
