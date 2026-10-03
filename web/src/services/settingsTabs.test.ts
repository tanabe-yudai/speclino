import { describe, expect, it } from 'vitest'
import { pickSettingsTab, visibleSettingsTabs } from './settingsTabs'

describe('visibleSettingsTabs', () => {
  it('owner はプロジェクト・リポジトリ・メンバー・MCP トークン', () => {
    expect(visibleSettingsTabs('owner')).toEqual(['projects', 'repositories', 'members', 'tokens'])
  })
  it('owner 以外・非メンバーは MCP トークンを出さない（owner の操作）', () => {
    expect(visibleSettingsTabs('approver')).toEqual(['projects', 'repositories', 'members'])
    expect(visibleSettingsTabs('member')).toEqual(['projects', 'repositories', 'members'])
    expect(visibleSettingsTabs(null)).toEqual(['projects', 'repositories', 'members'])
  })
})

describe('pickSettingsTab', () => {
  it('見えるタブならその値、見えない・不正なら先頭', () => {
    expect(pickSettingsTab('tokens', ['repositories', 'tokens'])).toBe('tokens')
    expect(pickSettingsTab('tokens', ['repositories'])).toBe('repositories')
    expect(pickSettingsTab('nope', ['repositories', 'tokens'])).toBe('repositories')
    expect(pickSettingsTab(null, ['repositories', 'tokens'])).toBe('repositories')
    expect(pickSettingsTab('members', ['projects', 'repositories', 'members'])).toBe('members')
  })
})
