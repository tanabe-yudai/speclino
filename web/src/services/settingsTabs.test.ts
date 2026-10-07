import { describe, expect, it } from 'vitest'
import { SETTINGS_TABS, pickSettingsTab } from './settingsTabs'

describe('SETTINGS_TABS', () => {
  it('メンバー・リポジトリ・連携・プロジェクトの順', () => {
    expect(SETTINGS_TABS).toEqual(['members', 'repositories', 'integrations', 'project'])
  })
})

describe('pickSettingsTab', () => {
  it('新しいタブ名はそのまま', () => {
    expect(pickSettingsTab('integrations')).toBe('integrations')
    expect(pickSettingsTab('project')).toBe('project')
    expect(pickSettingsTab('repositories')).toBe('repositories')
  })
  it('旧 URL を新しいタブへ対応づける', () => {
    expect(pickSettingsTab('sync')).toBe('integrations')
    expect(pickSettingsTab('tokens')).toBe('integrations')
    expect(pickSettingsTab('projects')).toBe('project')
    expect(pickSettingsTab('members')).toBe('members')
  })
  it('未指定・不正はメンバー', () => {
    expect(pickSettingsTab(null)).toBe('members')
    expect(pickSettingsTab('nope')).toBe('members')
  })
})
